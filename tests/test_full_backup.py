import hashlib
import json
import os
import sqlite3
import stat
import sys
import threading
import time
import types
import zipfile
from contextlib import closing, contextmanager
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


@contextmanager
def _sqlite_connection(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


def _create_database(path, question_count=1, image_paths=None, content=None, answer=None):
    with _sqlite_connection(path) as connection:
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


def _create_pre_v8_fingerprint_table(connection, indexes):
    connection.execute(
        """
        CREATE TABLE question_fingerprints (
            question_id INTEGER NOT NULL,
            fingerprint_version INTEGER NOT NULL,
            content_revision_hash VARCHAR(64) NOT NULL DEFAULT '',
            exact_hash VARCHAR(64) NOT NULL DEFAULT '',
            critical_math_hash VARCHAR(64) NOT NULL DEFAULT '',
            answer_hash VARCHAR(64) NOT NULL DEFAULT '',
            simhash_hex VARCHAR(32) NOT NULL DEFAULT '',
            token_count INTEGER NOT NULL DEFAULT 0,
            choice_count INTEGER NOT NULL DEFAULT 0,
            figure_count INTEGER NOT NULL DEFAULT 0,
            visible_image_hashes TEXT NOT NULL DEFAULT '[]',
            tikz_hashes TEXT NOT NULL DEFAULT '[]',
            band0 INTEGER NOT NULL DEFAULT 0,
            band1 INTEGER NOT NULL DEFAULT 0,
            band2 INTEGER NOT NULL DEFAULT 0,
            band3 INTEGER NOT NULL DEFAULT 0,
            band4 INTEGER NOT NULL DEFAULT 0,
            band5 INTEGER NOT NULL DEFAULT 0,
            band6 INTEGER NOT NULL DEFAULT 0,
            band7 INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (question_id, fingerprint_version),
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
        )
        """
    )
    for index_name, columns in indexes.items():
        connection.execute(
            f'CREATE INDEX "{index_name}" ON question_fingerprints '
            f"({', '.join(columns)})"
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
    with _sqlite_connection(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1
    assert (uploads / "figure.png").read_bytes() == b"original-image"
    assert json.loads(metadata.read_text(encoding="utf-8"))["curriculum"] == "original"


def _rewrite_backup_sources(path, *, remove=(), replacements=None, manifest_update=None):
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    for name in remove:
        entries.pop(name, None)
        manifest["files"].pop(name, None)
    for name, data in (replacements or {}).items():
        entries[name] = data
        manifest["files"][name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if manifest_update:
        manifest_update(manifest)
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def test_backup_has_one_verified_source_snapshot_and_restore_preserves_old_bytes(tmp_path, monkeypatch):
    from mathbank.content_sources import content_source_export_files, load_content_sources
    catalog = load_content_sources()
    catalog["title"] += "（历史快照）"
    historical = content_source_export_files(catalog)
    database = tmp_path / "bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    current_catalog = tmp_path / "program-resources" / "content_sources.json"
    current_catalog.parent.mkdir()
    current_catalog.write_bytes(b"current program catalog must remain unchanged")
    monkeypatch.setattr(backup_module, "CONTENT_SOURCES_PATH", current_catalog)
    monkeypatch.setattr(backup_module, "content_source_export_files", lambda: historical)
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "custom_metadata.json", retention=None)
    manifest = verify_full_backup(archive)
    assert manifest["content_sources_included"] is True
    with zipfile.ZipFile(archive) as zipped:
        for name, data in historical.items():
            assert zipped.namelist().count(name) == 1
            assert zipped.read(name) == data
            assert manifest["files"][name] == {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    # Safety backups use the current declaration; restored documentation must
    # retain the archive's older snapshot rather than regenerate it.
    monkeypatch.setattr(backup_module, "content_source_export_files", content_source_export_files)
    safety = restore_full_backup(archive, database_path=database, uploads_dir=uploads,
        metadata_path=tmp_path / "custom_metadata.json", safety_backup_dir=tmp_path / "safety",
        runtime_lock_path=tmp_path / "runtime.lock")
    saved = database.parent / "restored_content_sources" / safety.stem
    assert {x.name: x.read_bytes() for x in saved.iterdir()} == historical
    assert current_catalog.read_bytes() == b"current program catalog must remain unchanged"


@pytest.mark.parametrize("version", (1, 2))
def test_old_backup_without_source_snapshot_still_restores(tmp_path, version):
    database, uploads = tmp_path / "bank.db", tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "custom_metadata.json", retention=None)
    _rewrite_backup_sources(archive, remove=("content_sources.json", "CONTENT_SOURCES.txt"),
        manifest_update=lambda m: (m.pop("content_sources_included"), m.update(format_version=version)))
    assert verify_full_backup(archive)["format_version"] == version
    restore_full_backup(archive, database_path=database, uploads_dir=uploads,
        metadata_path=tmp_path / "custom_metadata.json", safety_backup_dir=tmp_path / "safety",
        runtime_lock_path=tmp_path / "runtime.lock")
    assert not (tmp_path / "restored_content_sources").exists()


@pytest.mark.parametrize("mutation", ("missing", "hash", "invalid_json", "invalid_text", "flag"))
def test_backup_rejects_corrupt_or_incomplete_source_snapshot(tmp_path, mutation):
    database, uploads = tmp_path / "bank.db", tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "custom_metadata.json", retention=None)
    if mutation == "missing":
        _rewrite_backup_sources(archive, remove=("CONTENT_SOURCES.txt",))
    elif mutation == "hash":
        _rewrite_backup_sources(archive, manifest_update=lambda m: m["files"]["content_sources.json"].update(sha256="0" * 64))
    elif mutation == "invalid_json":
        _rewrite_backup_sources(archive, replacements={"content_sources.json": b"[]"})
    elif mutation == "invalid_text":
        _rewrite_backup_sources(archive, replacements={"CONTENT_SOURCES.txt": b"\xff"})
    else:
        _rewrite_backup_sources(archive, manifest_update=lambda m: m.update(content_sources_included=False))
    with pytest.raises(RuntimeError):
        verify_full_backup(archive)


def test_restore_source_document_install_failure_rolls_back_data(tmp_path, monkeypatch):
    database, uploads = tmp_path / "bank.db", tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "custom_metadata.json", retention=None)
    database.unlink()
    _create_database(database, question_count=2)
    documents = tmp_path / "chosen-documents"
    documents.mkdir()
    (documents / "earlier.txt").write_bytes(b"keep existing documents")
    replace = backup_module.os.replace
    def fail_document_install(source, target):
        if Path(source).parent == documents and Path(source).name.startswith(".restore-new-"):
            raise OSError("injected source document install failure")
        return replace(source, target)
    monkeypatch.setattr(backup_module.os, "replace", fail_document_install)
    with pytest.raises(OSError, match="source document"):
        restore_full_backup(archive, database_path=database, uploads_dir=uploads,
            metadata_path=tmp_path / "custom_metadata.json", safety_backup_dir=tmp_path / "safety",
            runtime_lock_path=tmp_path / "runtime.lock", content_sources_dir=documents)
    with _sqlite_connection(database) as c:
        assert c.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 2
    assert {x.name: x.read_bytes() for x in documents.iterdir()} == {"earlier.txt": b"keep existing documents"}


def test_restore_rejects_program_resource_directory_for_historical_sources(tmp_path):
    with pytest.raises(RuntimeError, match="程序资源"):
        restore_full_backup(tmp_path / "missing.zip", database_path=tmp_path / "bank.db",
            uploads_dir=tmp_path / "uploads", metadata_path=tmp_path / "custom_metadata.json",
            safety_backup_dir=tmp_path / "safety", runtime_lock_path=tmp_path / "runtime.lock",
            content_sources_dir=backup_module.CONTENT_SOURCES_PATH.parent)


def test_backup_captures_native_hidden_and_rendered_references(tmp_path):
    database, uploads = tmp_path / "bank.db", tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    with _sqlite_connection(database) as c:
        c.executescript("ALTER TABLE questions ADD COLUMN content_tikz_assets TEXT;"
                       "ALTER TABLE questions ADD COLUMN answer_tikz_assets TEXT;"
                       "ALTER TABLE questions ADD COLUMN tikz_reference_image_path TEXT;")
        c.execute("UPDATE questions SET content_tikz_assets=?,answer_tikz_assets=?,tikz_reference_image_path=?", (
            json.dumps([{"image_path": "/static/uploads/native.png", "reference_image_path": "/static/uploads/hidden.png"}]),
            json.dumps([{"reference_image_path": "/static/uploads/answer-reference.jpg"}]),
            "/static/uploads/legacy-reference.webp"))
    names = ("native.png", "hidden.png", "answer-reference.jpg", "legacy-reference.webp")
    for name in names:
        (uploads / name).write_bytes(name.encode())
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "missing.json", retention=None)
    manifest = verify_full_backup(archive)
    assert manifest["database"]["structured_upload_references"] is True
    assert set(manifest["database"]["referenced_uploads"]) == {"uploads/" + name for name in names}
    (uploads / "hidden.png").unlink()
    with pytest.raises(RuntimeError, match="缺少数据库引用"):
        create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
            uploads_dir=uploads, metadata_path=tmp_path / "missing.json", retention=None)


@pytest.mark.parametrize("version", (1, 2))
def test_old_backup_native_reference_scope_remains_backward_compatible(tmp_path, version):
    database, uploads = tmp_path / "bank.db", tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "missing.json", retention=None)
    with zipfile.ZipFile(archive) as zipped:
        entries = {name: zipped.read(name) for name in zipped.namelist()}
    manifest = json.loads(entries["manifest.json"])
    database_member = next(name for name in entries if name.startswith("database/"))
    historical_db = tmp_path / "historical.db"
    historical_db.write_bytes(entries[database_member])
    with _sqlite_connection(historical_db) as c:
        c.execute("ALTER TABLE questions ADD COLUMN tikz_reference_image_path TEXT")
        c.execute("UPDATE questions SET tikz_reference_image_path='/static/uploads/not-captured-by-old-writer.png'")
    entries[database_member] = historical_db.read_bytes()
    manifest["files"][database_member] = {"size": len(entries[database_member]), "sha256": hashlib.sha256(entries[database_member]).hexdigest()}
    manifest["database"].pop("structured_upload_references")
    manifest["format_version"] = version
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, data in entries.items():
            zipped.writestr(name, data)
    assert verify_full_backup(archive)["format_version"] == version


@pytest.mark.parametrize("version", ("N", "X"))
def test_full_backup_restores_new_curriculum_identity_and_old_mirrors(tmp_path, version):
    from mathbank.curriculums import build_default_metadata
    from mathbank.sub_knowledge import resolve_active_version

    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    metadata = tmp_path / "custom_metadata.json"
    uploads.mkdir()
    _create_database(database)
    with _sqlite_connection(database) as connection:
        connection.executemany("INSERT INTO question_curriculums(question_id, version_code) VALUES(1, ?)",
                               [(code,) for code in ("A", "B", "S", "H", "N", "X")])
    original = (json.dumps(build_default_metadata(version), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    metadata.write_bytes(original)
    assert resolve_active_version(metadata, strict=True) == version
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=metadata, retention=None)
    assert verify_full_backup(archive)["database"]["row_counts"]["question_curriculums"] == 6
    metadata.write_text('{"version_code":"A","curriculum":{}}', encoding="utf-8")
    with _sqlite_connection(database) as connection:
        connection.execute("DELETE FROM question_curriculums WHERE version_code IN ('B','S','N','X')")
    restore_full_backup(archive, database_path=database, uploads_dir=uploads, metadata_path=metadata,
        safety_backup_dir=tmp_path / "pre_restore", runtime_lock_path=tmp_path / "runtime.lock")
    assert metadata.read_bytes() == original
    assert resolve_active_version(metadata, strict=True) == version
    with _sqlite_connection(database) as connection:
        assert connection.execute("SELECT version_code FROM question_curriculums ORDER BY version_code").fetchall() == [
            (code,) for code in sorted(("A", "B", "S", "H", "N", "X"))
        ]


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


def test_schema_v6_backup_requires_and_counts_fingerprint_table(tmp_path):
    from mathbank.db_migrations import V6_QUESTION_FINGERPRINT_INDEXES

    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    with _sqlite_connection(database) as connection:
        connection.execute("PRAGMA user_version=6")
        _create_pre_v8_fingerprint_table(
            connection,
            V6_QUESTION_FINGERPRINT_INDEXES,
        )
        connection.execute(
            "INSERT INTO question_fingerprints (question_id, fingerprint_version) "
            "VALUES (1, 2)"
        )

    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )
    manifest = verify_full_backup(archive)

    assert manifest["database"]["row_counts"]["question_fingerprints"] == 1


def test_schema_v7_backup_uses_its_three_column_band_indexes(tmp_path):
    from mathbank.db_migrations import V7_QUESTION_FINGERPRINT_INDEXES

    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    with _sqlite_connection(database) as connection:
        connection.execute("PRAGMA user_version=7")
        _create_pre_v8_fingerprint_table(
            connection,
            V7_QUESTION_FINGERPRINT_INDEXES,
        )
        connection.execute(
            "INSERT INTO question_fingerprints (question_id, fingerprint_version) "
            "VALUES (1, 2)"
        )

    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )

    assert verify_full_backup(archive)["database"]["schema_version"] == 7


def test_schema_v6_backup_rejects_missing_fingerprint_table(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    with _sqlite_connection(database) as connection:
        connection.execute("PRAGMA user_version=6")

    with pytest.raises(RuntimeError, match="question_fingerprints"):
        create_full_backup(
            output_dir=tmp_path / "snapshots",
            database_path=database,
            uploads_dir=uploads,
            metadata_path=tmp_path / "missing.json",
            retention=None,
        )


def test_schema_v6_backup_rejects_malformed_fingerprint_table(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    with _sqlite_connection(database) as connection:
        connection.execute("PRAGMA user_version=6")
        connection.execute(
            "CREATE TABLE question_fingerprints ("
            "question_id INTEGER NOT NULL, fingerprint_version INTEGER NOT NULL, "
            "PRIMARY KEY(question_id, fingerprint_version))"
        )
    with pytest.raises(RuntimeError, match="缺少核心字段"):
        create_full_backup(
            output_dir=tmp_path / "snapshots",
            database_path=database,
            uploads_dir=uploads,
            metadata_path=tmp_path / "missing.json",
            retention=None,
        )


def test_restore_rejects_future_schema_before_replacing_current_database(tmp_path):
    from mathbank.db_migrations import LATEST_SCHEMA_VERSION

    future_database = tmp_path / "future.db"
    current_database = tmp_path / "current.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(future_database, content="future")
    with _sqlite_connection(future_database) as connection:
        connection.execute(f"PRAGMA user_version={LATEST_SCHEMA_VERSION + 1}")
        connection.execute(
            "CREATE TABLE question_fingerprints ("
            "question_id INTEGER NOT NULL, fingerprint_version INTEGER NOT NULL, "
            "PRIMARY KEY(question_id, fingerprint_version))"
        )
        connection.execute(
            "CREATE TABLE question_sub_knowledge ("
            "id INTEGER PRIMARY KEY, curriculum_id INTEGER NOT NULL "
            "REFERENCES question_curriculums(id) ON DELETE CASCADE, "
            "compulsory TEXT NOT NULL, chapter TEXT NOT NULL, knowledge TEXT NOT NULL, "
            "position INTEGER NOT NULL)"
        )
        # Unknown future schemas still include the supported v14 data tables;
        # restore rejects their newer version before replacing current data.
        for table in ("knowledge_points", "knowledge_point_curriculums", "question_knowledge_points"):
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        for table in ("question_source_labels", "question_references"):
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.execute(
            "CREATE TABLE document_import_receipts ("
            "id INTEGER PRIMARY KEY, project_id VARCHAR(36) NOT NULL, card_id VARCHAR(160) NOT NULL, "
            "payload_sha256 VARCHAR(64) NOT NULL, draft_revision INTEGER NOT NULL, "
            "question_id INTEGER REFERENCES questions(id) ON DELETE SET NULL, created_at DATETIME NOT NULL, "
            "UNIQUE(project_id,card_id))"
        )
    archive = create_full_backup(
        output_dir=tmp_path / "snapshots",
        database_path=future_database,
        uploads_dir=uploads,
        metadata_path=tmp_path / "missing.json",
        retention=None,
    )
    _create_database(current_database, content="current")

    with pytest.raises(RuntimeError, match="高于当前程序支持"):
        restore_full_backup(
            archive,
            database_path=current_database,
            uploads_dir=uploads,
            metadata_path=tmp_path / "missing.json",
            safety_backup_dir=tmp_path / "pre_restore",
            runtime_lock_path=tmp_path / "runtime.lock",
        )

    with _sqlite_connection(current_database) as connection:
        assert connection.execute("SELECT content FROM questions").fetchone()[0] == "current"


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
    with _sqlite_connection(database) as connection:
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
    with _sqlite_connection(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1


def test_online_backup_includes_committed_wal_rows(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)

    with _sqlite_connection(database) as writer:
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


def test_locked_backup_times_out_without_mutating_database_or_publishing_archive(tmp_path, monkeypatch):
    database = tmp_path / "math_question_bank.db"
    snapshots = tmp_path / "snapshots"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    original = database.read_bytes()
    monkeypatch.setattr(backup_module, "BACKUP_BUSY_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(backup_module, "BACKUP_BUSY_RETRY_SECONDS", 0.01)
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("UPDATE questions SET content='uncommitted' WHERE id=1")
        started = time.monotonic()
        try:
            with pytest.raises(backup_module.BackupBusyError, match="持续被其他写入操作占用") as failure:
                create_full_backup(output_dir=snapshots, database_path=database, uploads_dir=uploads,
                                   metadata_path=tmp_path / "missing.json", retention=None)
            assert failure.value.backup_busy is True
            assert 0.08 <= time.monotonic() - started < 2
        finally:
            writer.rollback()
    assert database.read_bytes() == original
    assert list(snapshots.iterdir()) == []
    with _sqlite_connection(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT content FROM questions").fetchone()[0] == "question-1"


def test_locked_restore_probe_supports_python310_without_named_error_constants(tmp_path, monkeypatch):
    database = tmp_path / "math_question_bank.db"
    _create_database(database)
    monkeypatch.delattr(sqlite3, "SQLITE_BUSY", raising=False)
    monkeypatch.delattr(sqlite3, "SQLITE_LOCKED", raising=False)
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(RuntimeError, match="数据库当前正被占用"):
                backup_module._check_database_can_be_replaced(database)
        finally:
            writer.rollback()


def test_backup_finishes_when_exclusive_lock_is_released_before_budget(tmp_path, monkeypatch):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    observed_busy = threading.Event()
    connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        def backup(self, target, *, pages, progress, sleep):
            def observed(status, remaining, total):
                if status in backup_module._SQLITE_BUSY_CODES:
                    observed_busy.set()
                progress(status, remaining, total)
            return super().backup(target, pages=pages, progress=observed, sleep=sleep)

    def observed_connect(path, *args, **kwargs):
        if Path(path) == database:
            kwargs["factory"] = ObservedConnection
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(backup_module.sqlite3, "connect", observed_connect)
    monkeypatch.setattr(backup_module, "BACKUP_BUSY_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(backup_module, "BACKUP_BUSY_RETRY_SECONDS", 0.01)
    result = {}

    def run_backup():
        try:
            result["archive"] = create_full_backup(
                output_dir=tmp_path / "snapshots", database_path=database, uploads_dir=uploads,
                metadata_path=tmp_path / "missing.json", retention=None)
        except BaseException as exc:
            result["error"] = exc

    with closing(connect(database)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("UPDATE questions SET content='uncommitted' WHERE id=1")
        worker = threading.Thread(target=run_backup)
        worker.start()
        try:
            assert observed_busy.wait(timeout=2), "backup did not observe the real SQLite lock"
        finally:
            writer.rollback()
            worker.join(timeout=5)
    assert not worker.is_alive()
    assert "error" not in result
    assert verify_full_backup(result["archive"])["database"]["row_counts"]["questions"] == 1
    with _sqlite_connection(database) as connection:
        assert connection.execute("SELECT content FROM questions").fetchone()[0] == "question-1"


def test_progressing_page_copy_has_no_total_duration_limit(tmp_path, monkeypatch):
    database = tmp_path / "math_question_bank.db"
    _create_database(database, content="x" * 200_000)
    connect = sqlite3.connect
    copied_pages = []

    class SlowCopyConnection(sqlite3.Connection):
        def backup(self, target, *, pages, progress, sleep):
            assert pages == 1
            def slow_progress(status, remaining, total):
                if status == sqlite3.SQLITE_OK:
                    copied_pages.append(remaining)
                    time.sleep(0.001)
                progress(status, remaining, total)
            return super().backup(target, pages=pages, progress=slow_progress, sleep=sleep)

    def slow_connect(path, *args, **kwargs):
        if Path(path) == database:
            kwargs["factory"] = SlowCopyConnection
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(backup_module.sqlite3, "connect", slow_connect)
    monkeypatch.setattr(backup_module, "BACKUP_PAGES_PER_STEP", 1)
    monkeypatch.setattr(backup_module, "BACKUP_BUSY_TIMEOUT_SECONDS", 0.01)
    started = time.monotonic()
    result = backup_module._snapshot_database(database, tmp_path / "snapshot.db")
    assert len(copied_pages) > 20
    assert time.monotonic() - started > backup_module.BACKUP_BUSY_TIMEOUT_SECONDS
    assert result["row_counts"]["questions"] == 1
    with _sqlite_connection(tmp_path / "snapshot.db") as snapshot:
        assert snapshot.execute("SELECT length(content) FROM questions").fetchone()[0] == 200_000


def test_backup_and_restore_fsync_reopened_files_with_write_access(tmp_path, monkeypatch):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _create_database(database)
    open_path = Path.open
    reopened = []

    def windows_sync_open(path, mode="r", *args, **kwargs):
        stream = open_path(path, mode, *args, **kwargs)
        if mode in {"rb", "r+b"}:
            class SyncCheckedStream:
                def __enter__(self):
                    stream.__enter__()
                    return self
                def __exit__(self, *exc):
                    return stream.__exit__(*exc)
                def __getattr__(self, name):
                    return getattr(stream, name)
                def fileno(self):
                    # Windows FlushFileBuffers requires GENERIC_WRITE.
                    if mode == "rb":
                        raise OSError(9, "read-only descriptor cannot be synced on Windows")
                    reopened.append(Path(path).name)
                    return stream.fileno()
            return SyncCheckedStream()
        return stream

    monkeypatch.setattr(Path, "open", windows_sync_open)
    archive = create_full_backup(output_dir=tmp_path / "snapshots", database_path=database,
                               uploads_dir=uploads, metadata_path=tmp_path / "missing.json", retention=None)
    with _sqlite_connection(database) as connection:
        connection.execute("INSERT INTO questions(id, content) VALUES(2, 'later')")
    restore_full_backup(archive, database_path=database, uploads_dir=uploads,
                        metadata_path=tmp_path / "missing.json", safety_backup_dir=tmp_path / "safety",
                        runtime_lock_path=tmp_path / "runtime.lock")
    assert any(name.endswith(".zip.tmp") for name in reopened)
    assert "content_sources.json" in reopened and "CONTENT_SOURCES.txt" in reopened
    with _sqlite_connection(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1


def test_zip_cleanup_failure_does_not_replace_original_sync_error(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "data.txt").write_text("preserve")
    output = tmp_path / "backup.zip"
    sentinel = OSError("original sync failure")
    unlink = Path.unlink

    def fail_sync(_path):
        raise sentinel
    def fail_temporary_cleanup(path, *args, **kwargs):
        if path == output.with_suffix(".zip.tmp"):
            raise PermissionError("scanner still holds temporary file")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(backup_module, "fsync_writable_file", fail_sync)
    monkeypatch.setattr(Path, "unlink", fail_temporary_cleanup)
    with pytest.raises(OSError) as failure:
        backup_module._write_zip_atomic(payload, "manifest.json", {}, output)
    assert failure.value is sentinel
    assert not output.exists()


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


def test_backup_preserves_legacy_review_image_and_ignores_literal_example(tmp_path):
    database = tmp_path / "math_question_bank.db"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    image = uploads / "review.png"
    image.write_bytes(b"commentary diagram")
    _create_database(database)
    with _sqlite_connection(database) as connection:
        connection.execute("ALTER TABLE questions ADD COLUMN review TEXT DEFAULT ''")
        connection.execute(
            "UPDATE questions SET review=?",
            ("错误示范 ![](/static/uploads/review.png)\n"
             "代码例子 `![](/static/uploads/missing.png)`",),
        )
    archive = create_full_backup(
        output_dir=tmp_path / "snapshots", database_path=database,
        uploads_dir=uploads, metadata_path=tmp_path / "missing.json", retention=None,
    )
    verified = verify_full_backup(archive)
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("uploads/review.png") == image.read_bytes()
        assert "uploads/missing.png" not in zipped.namelist()
    assert verified["upload_file_count"] == 1


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
    with _sqlite_connection(database) as connection:
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
