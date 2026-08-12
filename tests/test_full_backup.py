import hashlib
import json
import os
import sqlite3
import stat
import sys
import types
import zipfile
from pathlib import Path

import pytest

import mathbank.backup as backup_module
from mathbank.backup import (
    acquire_runtime_lock,
    create_full_backup,
    create_full_backup_if_due,
    restore_full_backup,
    verify_full_backup,
)


def _create_database(path, question_count=1, image_paths=None, content=None, answer=None):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            PRAGMA user_version=1;
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                answer_markdown TEXT,
                image_paths TEXT
            );
            CREATE TABLE question_curriculums (
                id INTEGER PRIMARY KEY,
                question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                version_code TEXT NOT NULL,
                UNIQUE(question_id, version_code)
            );
            CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE paper_questions (
                id INTEGER PRIMARY KEY,
                paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                order_index INTEGER NOT NULL,
                UNIQUE(paper_id, order_index)
            );
            """
        )
        for question_id in range(1, question_count + 1):
            connection.execute(
                """INSERT INTO questions
                   (id, content, answer_markdown, image_paths)
                   VALUES (?, ?, ?, ?)""",
                (
                    question_id,
                    content if question_id == 1 and content is not None else f"question-{question_id}",
                    answer if question_id == 1 else None,
                    json.dumps(image_paths if question_id == 1 and image_paths else []),
                ),
            )


def _create_real_wal_sidecar_bytes(path):
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE evidence (payload TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('preserve-sidecars')")
        connection.commit()
        wal_bytes = path.with_name(f"{path.name}-wal").read_bytes()
        shm_bytes = path.with_name(f"{path.name}-shm").read_bytes()
    finally:
        connection.close()
    return wal_bytes, shm_bytes


def test_full_backup_round_trip_restores_database_uploads_and_metadata(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    metadata = tmp_path / "custom_metadata.json"
    snapshots = tmp_path / "snapshots"
    safety = tmp_path / "pre_restore"
    uploads.mkdir()
    (uploads / "figure.png").write_bytes(b"original-image")
    metadata.write_text('{"curriculum": "original"}', encoding="utf-8")
    _create_database(
        database,
        question_count=1,
        image_paths=["/static/uploads/figure.png"],
    )

    archive = create_full_backup(
        output_dir=snapshots,
        database_path=database,
        uploads_dir=uploads,
        metadata_path=metadata,
        retention=None,
    )
    manifest = verify_full_backup(archive)
    assert manifest["database"]["row_counts"]["questions"] == 1
    assert manifest["upload_file_count"] == 1
    assert manifest["secrets_included"] is False

    database.unlink()
    _create_database(database, question_count=2)
    (uploads / "figure.png").write_bytes(b"changed-image")
    metadata.write_text('{"curriculum": "changed"}', encoding="utf-8")

    safety_backup = restore_full_backup(
        archive,
        database_path=database,
        uploads_dir=uploads,
        metadata_path=metadata,
        safety_backup_dir=safety,
        runtime_lock_path=tmp_path / "runtime.lock",
    )

    assert safety_backup.is_file()
    assert not list(safety.glob("mathbank-raw-pre-restore-*.zip"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1
    assert (uploads / "figure.png").read_bytes() == b"original-image"
    assert json.loads(metadata.read_text(encoding="utf-8"))["curriculum"] == "original"


def test_backup_verifier_rejects_modified_payload(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "figure.png").write_bytes(b"original-image")
    _create_database(database, image_paths=["/static/uploads/figure.png"])
    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "uploads/figure.png":
                data = b"tampered"
            target.writestr(info, data)

    with pytest.raises(RuntimeError, match="大小不匹配|校验失败"):
        verify_full_backup(tampered)


def test_automatic_backup_skips_when_recent_snapshot_exists(tmp_path, monkeypatch):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    recent = snapshots / "mathbank-backup-recent.zip"
    recent.write_bytes(b"placeholder")
    called = []

    monkeypatch.setattr(
        "mathbank.backup.create_full_backup",
        lambda **kwargs: called.append(kwargs),
    )
    monkeypatch.setattr("mathbank.backup.verify_full_backup", lambda _path: {})
    result = create_full_backup_if_due(
        output_dir=snapshots,
        minimum_interval_seconds=3600,
    )

    assert result is None
    assert called == []


def test_invalid_recent_backup_does_not_suppress_automatic_backup(
    tmp_path, monkeypatch
):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    invalid = snapshots / "mathbank-backup-invalid.zip"
    invalid.write_bytes(b"not-a-zip")
    replacement = snapshots / "mathbank-backup-replacement.zip"
    calls = []

    def create_replacement(**kwargs):
        calls.append(kwargs)
        return replacement

    monkeypatch.setattr("mathbank.backup.create_full_backup", create_replacement)
    result = create_full_backup_if_due(
        output_dir=snapshots,
        minimum_interval_seconds=3600,
    )

    assert result == replacement
    assert len(calls) == 1
    assert invalid.is_file()


@pytest.mark.parametrize("unsafe_name", ["../escape", "uploads/..\\escape", "C:\\escape"])
def test_backup_verifier_rejects_cross_platform_traversal(tmp_path, unsafe_name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(unsafe_name, b"unsafe")

    with pytest.raises(RuntimeError, match="不安全路径"):
        verify_full_backup(archive)


def test_backup_verifier_rejects_zip_symlinks(tmp_path):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("uploads/link.png")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(link, b"../../outside")

    with pytest.raises(RuntimeError, match="特殊文件"):
        verify_full_backup(archive)


def test_restore_rolls_back_all_resources_when_upload_swap_fails(tmp_path, monkeypatch):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    metadata = tmp_path / "custom_metadata.json"
    uploads.mkdir()
    (uploads / "figure.png").write_bytes(b"backup-image")
    metadata.write_text('{"state": "backup"}', encoding="utf-8")
    _create_database(
        database,
        question_count=1,
        image_paths=["/static/uploads/figure.png"],
    )
    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=metadata,
        retention=None,
    )

    database.unlink()
    _create_database(database, question_count=2)
    (uploads / "figure.png").write_bytes(b"current-image")
    metadata.write_text('{"state": "current"}', encoding="utf-8")

    real_replace = os.replace
    injected = {"raised": False}

    def fail_upload_install(source, destination):
        source_path = backup_module.Path(source)
        destination_path = backup_module.Path(destination)
        if (
            not injected["raised"]
            and ".uploads.restore-new-" in source_path.name
            and destination_path == uploads
        ):
            injected["raised"] = True
            raise OSError("injected upload swap failure")
        return real_replace(source, destination)

    monkeypatch.setattr(backup_module.os, "replace", fail_upload_install)
    with pytest.raises(OSError, match="injected upload swap failure"):
        restore_full_backup(
            archive,
            database_path=database,
            uploads_dir=uploads,
            metadata_path=metadata,
            safety_backup_dir=tmp_path / "pre_restore",
            runtime_lock_path=tmp_path / "runtime.lock",
        )

    assert injected["raised"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 2
    assert (uploads / "figure.png").read_bytes() == b"current-image"
    assert json.loads(metadata.read_text(encoding="utf-8"))["state"] == "current"
    assert not list(tmp_path.glob(".*.restore-old-*"))


def test_restore_supports_backup_with_empty_upload_directory(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )
    (uploads / "stale.png").write_bytes(b"stale")

    restore_full_backup(
        archive,
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        safety_backup_dir=tmp_path / "pre_restore",
        runtime_lock_path=tmp_path / "runtime.lock",
    )

    assert uploads.is_dir()
    assert list(uploads.iterdir()) == []


def test_restore_quarantines_corrupt_current_database_before_recovery(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    metadata = tmp_path / "custom_metadata.json"
    uploads.mkdir()
    (uploads / "figure.png").write_bytes(b"recoverable-upload")
    metadata.write_text('{"state": "recoverable"}', encoding="utf-8")
    _create_database(database)
    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=metadata,
        retention=None,
    )

    wal_bytes, shm_bytes = _create_real_wal_sidecar_bytes(
        tmp_path / "sidecar-source.db"
    )
    corrupt_bytes = b"not a sqlite database but still recoverable evidence"
    database.write_bytes(corrupt_bytes)
    Path(f"{database}-wal").write_bytes(wal_bytes)
    Path(f"{database}-shm").write_bytes(shm_bytes)
    safety = restore_full_backup(
        archive,
        database_path=database,
        uploads_dir=uploads,
        metadata_path=metadata,
        safety_backup_dir=tmp_path / "pre_restore",
        runtime_lock_path=tmp_path / "runtime.lock",
    )

    assert safety.name.startswith("mathbank-raw-pre-restore-")
    with zipfile.ZipFile(safety, "r") as recovery:
        assert recovery.read("database/math_question_bank.db") == corrupt_bytes
        assert recovery.read("database/math_question_bank.db-wal") == wal_bytes
        assert recovery.read("database/math_question_bank.db-shm") == shm_bytes
        assert recovery.read("uploads/figure.png") == b"recoverable-upload"
        recovery_manifest = json.loads(recovery.read("recovery.json"))
        assert recovery_manifest["format"] == "mathbank-raw-pre-restore"
        assert (
            recovery_manifest["capture_stage"]
            == "before-current-database-sqlite-probe"
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1


def test_online_backup_includes_committed_wal_rows(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)

    with sqlite3.connect(database) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute(
            "INSERT INTO questions (id, content) VALUES (?, ?)",
            (2, "committed-in-wal"),
        )
        writer.commit()
        archive = create_full_backup(
            output_dir=tmp_path / "snapshots",
            database_path=database,
            uploads_dir=uploads,
            metadata_path=tmp_path / "missing.json",
            retention=None,
        )

    manifest = verify_full_backup(archive)
    assert manifest["database"]["row_counts"]["questions"] == 2
    with zipfile.ZipFile(archive, "r") as backup_zip:
        database_members = [
            name for name in backup_zip.namelist() if name.startswith("database/")
        ]
    assert database_members == ["database/math_question_bank.db"]


def test_backup_fails_if_database_references_a_missing_upload(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(
        database,
        image_paths=["/static/uploads/deleted-during-backup.png"],
    )

    snapshots = tmp_path / "snapshots"
    with pytest.raises(RuntimeError, match="缺少数据库引用的上传图片"):
        create_full_backup(
            output_dir=snapshots,
            database_path=database,
            uploads_dir=uploads,
            metadata_path=tmp_path / "missing.json",
            retention=None,
        )

    assert list(snapshots.glob("mathbank-backup-*.zip")) == []


def test_standard_backup_archives_only_referenced_production_rasters(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for name in (
        "structured.png",
        "legacy.jpg",
        "answer.webp",
        "unreferenced.png",
        ".gitkeep",
        ".env",
        "page.html",
    ):
        (uploads / name).write_bytes(name.encode())
    (uploads / "tmp").mkdir()
    (uploads / "tmp" / "crop.png").write_bytes(b"temporary")
    _create_database(
        database,
        image_paths=["/static/uploads/structured.png"],
        content="legacy ![](/static/uploads/legacy.jpg)",
        answer="answer ![](/static/uploads/answer.webp)",
    )

    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )

    with zipfile.ZipFile(archive) as backup_zip:
        upload_names = sorted(
            name for name in backup_zip.namelist() if name.startswith("uploads/")
        )
    assert upload_names == [
        "uploads/answer.webp",
        "uploads/legacy.jpg",
        "uploads/structured.png",
    ]


@pytest.mark.parametrize(
    "reference",
    [
        "/static/test_uploads/figure.png",
        "/static/uploads/tmp/figure.png",
        "/static/uploads/answer.html",
        "/static/uploads/.env",
    ],
)
def test_standard_backup_rejects_nonproduction_or_unsafe_structured_reference(
    tmp_path, reference
):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database, image_paths=[reference])

    with pytest.raises(RuntimeError, match="无效插图引用"):
        create_full_backup(
            output_dir=tmp_path / "snapshots",
            database_path=database,
            uploads_dir=uploads,
            metadata_path=tmp_path / "missing.json",
            retention=None,
        )


def test_test_upload_prefix_requires_explicit_parser_parameter(tmp_path):
    database = tmp_path / "math_question_bank.db"
    _create_database(
        database,
        image_paths=["/static/test_uploads/fixture.png"],
    )
    with sqlite3.connect(database) as connection:
        assert backup_module._database_upload_references(
            connection,
            url_prefix="/static/test_uploads",
        ) == {"uploads/fixture.png"}


def test_standard_backup_refuses_arbitrary_metadata_file(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=must-not-ship", encoding="utf-8")

    with pytest.raises(RuntimeError, match="custom_metadata.json"):
        create_full_backup(
            output_dir=tmp_path / "snapshots",
            database_path=database,
            uploads_dir=uploads,
            metadata_path=secret,
            retention=None,
        )


def test_verifier_rejects_secret_disguised_as_metadata(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    current = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )
    tampered = tmp_path / "secret-metadata.zip"
    with zipfile.ZipFile(current) as source:
        payloads = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if info.filename != "manifest.json"
        }
        manifest = json.loads(source.read("manifest.json"))
    secret = b"API_KEY=must-not-restore"
    payloads["metadata/.env"] = secret
    manifest["metadata_included"] = True
    manifest["files"]["metadata/.env"] = {
        "size": len(secret),
        "sha256": hashlib.sha256(secret).hexdigest(),
    }
    with zipfile.ZipFile(tampered, "w") as target:
        target.writestr("manifest.json", json.dumps(manifest).encode())
        for name, data in payloads.items():
            target.writestr(name, data)

    with pytest.raises(RuntimeError, match="非法元数据"):
        verify_full_backup(tampered)


@pytest.mark.parametrize("legacy_format_version", [1, 2])
def test_legacy_upload_manifest_allows_safe_extras_but_does_not_restore_them(
    tmp_path, legacy_format_version
):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "figure.png").write_bytes(b"referenced")
    _create_database(database, image_paths=["/static/uploads/figure.png"])
    current = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )

    legacy = tmp_path / f"legacy-v{legacy_format_version}.zip"
    with zipfile.ZipFile(current) as source:
        payloads = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if info.filename != "manifest.json"
        }
        manifest = json.loads(source.read("manifest.json"))
    payloads["uploads/unreferenced.png"] = b"legacy-extra"
    payloads["uploads/.gitkeep"] = b""
    manifest["format_version"] = legacy_format_version
    manifest["database"].pop("referenced_uploads", None)
    manifest["upload_file_count"] = 3
    for name in ("uploads/unreferenced.png", "uploads/.gitkeep"):
        data = payloads[name]
        manifest["files"][name] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    with zipfile.ZipFile(legacy, "w", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("manifest.json", json.dumps(manifest).encode())
        for name, data in payloads.items():
            target.writestr(name, data)

    assert verify_full_backup(legacy)["format_version"] == legacy_format_version
    (uploads / "stale.png").write_bytes(b"stale")
    restore_full_backup(
        legacy,
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        safety_backup_dir=tmp_path / "pre_restore",
        runtime_lock_path=tmp_path / "runtime.lock",
    )
    assert sorted(path.name for path in uploads.iterdir()) == ["figure.png"]


@pytest.mark.parametrize("legacy_format_version", [1, 2])
@pytest.mark.parametrize("unsafe_name", ["uploads/page.html", "uploads/tmp/crop.png"])
def test_legacy_upload_manifest_rejects_unsafe_extra(
    tmp_path, unsafe_name, legacy_format_version
):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    current = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )
    legacy = tmp_path / f"unsafe-v{legacy_format_version}.zip"
    with zipfile.ZipFile(current) as source:
        payloads = {
            info.filename: source.read(info.filename)
            for info in source.infolist()
            if info.filename != "manifest.json"
        }
        manifest = json.loads(source.read("manifest.json"))
    manifest["format_version"] = legacy_format_version
    manifest["database"].pop("referenced_uploads", None)
    payloads[unsafe_name] = b"unsafe"
    manifest["upload_file_count"] = 1
    manifest["files"][unsafe_name] = {
        "size": 6,
        "sha256": hashlib.sha256(b"unsafe").hexdigest(),
    }
    with zipfile.ZipFile(legacy, "w") as target:
        target.writestr("manifest.json", json.dumps(manifest).encode())
        for name, data in payloads.items():
            target.writestr(name, data)

    with pytest.raises(RuntimeError, match="非法上传资源"):
        verify_full_backup(legacy)


def test_runtime_lock_blocks_restore_probe_without_signalling_process(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    held = acquire_runtime_lock(lock_path)
    try:
        with pytest.raises(RuntimeError, match="题库服务仍在运行"):
            acquire_runtime_lock(lock_path)
    finally:
        held.close()
    with acquire_runtime_lock(lock_path):
        pass
    restore_source = (Path(__file__).parents[1] / "scripts" / "restore.py").read_text(
        encoding="utf-8"
    )
    assert "os.kill" not in restore_source


def test_public_restore_cannot_bypass_runtime_lock(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )
    lock_path = tmp_path / "runtime.lock"
    with acquire_runtime_lock(lock_path):
        with pytest.raises(RuntimeError, match="题库服务仍在运行"):
            restore_full_backup(
                archive,
                database_path=database,
                uploads_dir=uploads,
                metadata_path=tmp_path / "missing.json",
                safety_backup_dir=tmp_path / "pre_restore",
                runtime_lock_path=lock_path,
            )
    assert not (tmp_path / "pre_restore").exists()


def test_windows_runtime_lock_uses_one_byte_nonblocking_lock(tmp_path, monkeypatch):
    calls = []
    fake_msvcrt = types.SimpleNamespace(
        LK_NBLCK=10,
        LK_UNLCK=20,
        locking=lambda fd, mode, size: calls.append((fd, mode, size)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    with acquire_runtime_lock(tmp_path / "runtime.lock", platform_name="nt"):
        assert calls[-1][1:] == (fake_msvcrt.LK_NBLCK, 1)
    assert calls[-1][1:] == (fake_msvcrt.LK_UNLCK, 1)
