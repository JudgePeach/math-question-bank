import hashlib
import json
from pathlib import Path

import pytest

from scripts import release_overlay


def _write(root: Path, relative: str, content: bytes | str) -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    return path


def _manifest_bytes(root: Path, platform: str, paths: list[str], version: str) -> bytes:
    required = ["main.py", "scripts/release_overlay.py"]
    required.append("启动题库系统.command" if platform == "macos" else "启动题库系统.bat")
    if platform == "windows-x64":
        required.append("python/python.exe")
    paths = list(dict.fromkeys([*paths, *required]))
    for relative in required:
        path = root.joinpath(*relative.split("/"))
        if not path.exists():
            _write(root, relative, f"required: {relative}")
    entries = []
    for relative in sorted(paths):
        content = root.joinpath(*relative.split("/")).read_bytes()
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    managed_roots = sorted(
        {
            relative.split("/", 1)[0]
            if "/" in relative
            else relative
            for relative in paths
        }
        | {"main.py"}
    )
    return (
        json.dumps(
            {
                "app_version": version,
                "files": entries,
                "overlay": {
                    "managed_roots": managed_roots,
                    "protected_paths": [
                        ".env",
                        ".system_generated",
                        "data_backup",
                        "math_question_bank.db",
                        "math_question_bank.db-shm",
                        "math_question_bank.db-wal",
                        "static/uploads",
                        "venv",
                    ],
                    "schema_version": 1,
                },
                "platform": platform,
                "schema_version": 1,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _install_manifest(
    root: Path,
    platform: str,
    paths: list[str],
    version: str = "1.0.0",
) -> bytes:
    content = _manifest_bytes(root, platform, paths, version)
    (root / release_overlay.MANIFEST_NAME).write_bytes(content)
    return content


def test_overlay_preserves_persistent_data_and_unknown_custom_files(tmp_path):
    managed = _write(tmp_path, "mathbank/app.py", "old application")
    obsolete = _write(tmp_path, "mathbank/obsolete.py", "old release file")
    protected_contents = {
        ".env": "SECRET=kept",
        "math_question_bank.db": b"database",
        "math_question_bank.db-wal": b"wal",
        "math_question_bank.db-shm": b"shm",
        "data_backup/snapshots/backup.zip": b"backup",
        ".system_generated/user-setting": b"setting",
        "static/uploads/diagram.png": b"image",
        "venv/local-package.txt": b"environment",
    }
    for relative, content in protected_contents.items():
        _write(tmp_path, relative, content)
    custom_contents = {
        "scripts/my_local_tool.py": b"custom script",
        "templates/my-template/custom.sty": b"custom template",
    }
    for relative, content in custom_contents.items():
        _write(tmp_path, relative, content)

    old_paths = ["mathbank/app.py", "mathbank/obsolete.py"]
    _install_manifest(tmp_path, "macos", old_paths)
    release_overlay.apply_release_overlay(tmp_path, "macos")

    managed.write_text("new application", encoding="utf-8")
    _install_manifest(tmp_path, "macos", ["mathbank/app.py"], version="2.0.0")
    result = release_overlay.apply_release_overlay(tmp_path, "macos")

    assert result["status"] == "updated"
    assert result["deleted"] == ["mathbank/obsolete.py"]
    assert not obsolete.exists()
    for relative, expected in protected_contents.items():
        expected_bytes = expected.encode() if isinstance(expected, str) else expected
        assert tmp_path.joinpath(*relative.split("/")).read_bytes() == expected_bytes
    for relative, expected in custom_contents.items():
        assert tmp_path.joinpath(*relative.split("/")).read_bytes() == expected


def test_overlay_deletes_only_unchanged_files_from_previous_release(tmp_path):
    _write(tmp_path, "main.py", "old main")
    unchanged_obsolete = _write(tmp_path, "mathbank/old.py", "release-owned")
    modified_obsolete = _write(tmp_path, "scripts/old.py", "release-owned")
    _install_manifest(
        tmp_path,
        "macos",
        ["main.py", "mathbank/old.py", "scripts/old.py"],
    )
    release_overlay.apply_release_overlay(tmp_path, "macos")

    modified_obsolete.write_text("user changed this", encoding="utf-8")
    _write(tmp_path, "main.py", "new main")
    _install_manifest(tmp_path, "macos", ["main.py"], version="2.0.0")
    result = release_overlay.apply_release_overlay(tmp_path, "macos")

    assert "mathbank/old.py" in result["deleted"]
    assert not unchanged_obsolete.exists()
    assert modified_obsolete.read_text(encoding="utf-8") == "user changed this"


def test_overlay_preserves_non_regular_replacement_of_an_old_release_file(tmp_path):
    _write(tmp_path, "main.py", "version one")
    obsolete = _write(tmp_path, "scripts/old.py", "release-owned")
    _install_manifest(tmp_path, "macos", ["main.py", "scripts/old.py"])
    release_overlay.apply_release_overlay(tmp_path, "macos")

    obsolete.unlink()
    obsolete.symlink_to(tmp_path / "main.py")
    _write(tmp_path, "main.py", "version two")
    _install_manifest(tmp_path, "macos", ["main.py"], version="2.0.0")
    release_overlay.apply_release_overlay(tmp_path, "macos")

    assert obsolete.is_symlink()


def test_overlay_does_not_follow_a_replaced_parent_directory(tmp_path):
    _write(tmp_path, "main.py", "version one")
    old_file = _write(tmp_path, "mathbank/old.py", "release-owned")
    _install_manifest(tmp_path, "macos", ["main.py", "mathbank/old.py"])
    release_overlay.apply_release_overlay(tmp_path, "macos")

    old_file.unlink()
    old_file.parent.rmdir()
    outside = _write(tmp_path, "outside/old.py", "release-owned")
    (tmp_path / "mathbank").symlink_to(tmp_path / "outside", target_is_directory=True)
    _write(tmp_path, "main.py", "version two")
    _install_manifest(tmp_path, "macos", ["main.py"], version="2.0.0")

    release_overlay.apply_release_overlay(tmp_path, "macos")

    assert (tmp_path / "mathbank").is_symlink()
    assert outside.read_text(encoding="utf-8") == "release-owned"


def test_manifest_file_with_linked_parent_is_rejected(tmp_path):
    _install_manifest(tmp_path, "macos", ["main.py"])
    scripts = tmp_path / "scripts"
    redirected = tmp_path / "redirected-scripts"
    scripts.rename(redirected)
    scripts.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(release_overlay.ReleaseOverlayError, match="linked or unsafe parent"):
        release_overlay.apply_release_overlay(tmp_path, "macos")

    assert not (
        tmp_path
        / release_overlay.STATE_DIRECTORY
        / release_overlay.INSTALLED_MANIFEST_NAME
    ).exists()


def test_first_manifest_aware_upgrade_only_removes_reviewed_tombstones(tmp_path):
    _write(tmp_path, "main.py", "current")
    reviewed = _write(tmp_path, "database.py", "historical module")
    unknown = _write(tmp_path, "local-helper.py", "user file")
    custom_script = _write(tmp_path, "scripts/old-looking.py", "user script")
    _install_manifest(tmp_path, "macos", ["main.py"])

    result = release_overlay.apply_release_overlay(tmp_path, "macos")

    assert result["deleted"] == ["database.py"]
    assert not reviewed.exists()
    assert unknown.exists()
    assert custom_script.exists()


def test_windows_runtime_is_reconciled_exactly(tmp_path):
    python_exe = _write(tmp_path, "python/python.exe", b"runtime")
    current_dependency = _write(tmp_path, "python/site-packages/current.py", "current")
    obsolete_dependency = _write(tmp_path, "python/site-packages/old.py", "obsolete")
    obsolete_cache = _write(tmp_path, "python/site-packages/pkg/__pycache__/old.pyc", b"pyc")
    obsolete_empty = tmp_path / "python" / "empty-old"
    obsolete_empty.mkdir()
    outside_custom = _write(tmp_path, "scripts/windows-helper.py", "keep")
    _install_manifest(
        tmp_path,
        "windows-x64",
        ["python/python.exe", "python/site-packages/current.py"],
    )

    result = release_overlay.apply_release_overlay(tmp_path, "windows-x64")

    assert python_exe.exists()
    assert current_dependency.exists()
    assert not obsolete_dependency.exists()
    assert not obsolete_cache.exists()
    assert not obsolete_empty.exists()
    assert outside_custom.exists()
    assert result["deleted"] == [
        "python/site-packages/old.py",
        "python/site-packages/pkg/__pycache__/old.pyc",
    ]


def test_windows_runtime_unlinks_directory_symlink_without_following_it(tmp_path):
    outside = _write(tmp_path, "outside-runtime/user-data.txt", "keep")
    _write(tmp_path, "python/python.exe", "runtime")
    (tmp_path / "python" / "linked").symlink_to(
        outside.parent, target_is_directory=True
    )
    _install_manifest(tmp_path, "windows-x64", ["python/python.exe"])

    result = release_overlay.apply_release_overlay(tmp_path, "windows-x64")

    assert "python/linked" in result["deleted"]
    assert outside.read_text(encoding="utf-8") == "keep"


def test_windows_runtime_walk_error_fails_without_recording_success(tmp_path, monkeypatch):
    _install_manifest(tmp_path, "windows-x64", ["python/python.exe"])

    def inaccessible_walk(*_args, **kwargs):
        kwargs["onerror"](PermissionError("runtime directory is inaccessible"))
        return iter(())

    monkeypatch.setattr(release_overlay.os, "walk", inaccessible_walk)
    with pytest.raises(release_overlay.ReleaseOverlayError, match="cannot inspect"):
        release_overlay.apply_release_overlay(tmp_path, "windows-x64")

    assert not (
        tmp_path
        / release_overlay.STATE_DIRECTORY
        / release_overlay.INSTALLED_MANIFEST_NAME
    ).exists()


def test_windows_case_only_path_change_is_not_deleted_as_obsolete(tmp_path):
    old_case = _write(tmp_path, "mathbank/Legacy.py", "same application file")
    _install_manifest(tmp_path, "windows-x64", ["mathbank/Legacy.py"])
    release_overlay.apply_release_overlay(tmp_path, "windows-x64")

    _write(tmp_path, "mathbank/legacy.py", "same application file")
    _install_manifest(
        tmp_path, "windows-x64", ["mathbank/legacy.py"], version="2.0.0"
    )
    release_overlay.apply_release_overlay(tmp_path, "windows-x64")

    assert old_case.exists()


@pytest.mark.parametrize("failure", ["missing", "wrong-size", "wrong-hash"])
def test_broken_or_missing_release_file_is_rejected_before_cleanup(tmp_path, failure):
    current = _write(tmp_path, "main.py", "current")
    historical = _write(tmp_path, "database.py", "must remain on failure")
    manifest = json.loads(_manifest_bytes(tmp_path, "macos", ["main.py"], "1.0.0"))
    if failure == "missing":
        current.unlink()
    elif failure == "wrong-size":
        manifest["files"][0]["size"] += 1
    else:
        manifest["files"][0]["sha256"] = "0" * 64
    (tmp_path / release_overlay.MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(release_overlay.ReleaseOverlayError):
        release_overlay.apply_release_overlay(tmp_path, "macos")

    assert historical.exists()
    assert not (
        tmp_path
        / release_overlay.STATE_DIRECTORY
        / release_overlay.INSTALLED_MANIFEST_NAME
    ).exists()


@pytest.mark.parametrize(
    "manifest_content",
    [
        b"not-json",
        json.dumps(
            {
                "app_version": "1.0.0",
                "files": [
                    {"path": "../outside", "size": 0, "sha256": "0" * 64}
                ],
                "platform": "macos",
                "schema_version": 1,
            }
        ).encode(),
    ],
)
def test_broken_or_unsafe_manifest_is_rejected(tmp_path, manifest_content):
    (tmp_path / release_overlay.MANIFEST_NAME).write_bytes(manifest_content)

    with pytest.raises(release_overlay.ReleaseOverlayError):
        release_overlay.apply_release_overlay(tmp_path, "macos")


def test_missing_release_manifest_is_rejected(tmp_path):
    with pytest.raises(release_overlay.ReleaseOverlayError, match="is missing"):
        release_overlay.apply_release_overlay(tmp_path, "macos")


def test_installed_manifest_and_digest_are_saved_and_repeat_is_noop(tmp_path):
    _write(tmp_path, "main.py", "current")
    manifest_content = _install_manifest(tmp_path, "macos", ["main.py"])

    first = release_overlay.apply_release_overlay(tmp_path, "macos")
    second = release_overlay.apply_release_overlay(tmp_path, "macos")

    state = tmp_path / release_overlay.STATE_DIRECTORY
    expected_digest = hashlib.sha256(manifest_content).hexdigest()
    assert first["status"] == "updated"
    assert second == {
        "status": "unchanged",
        "deleted": [],
        "digest": expected_digest,
    }
    assert (state / release_overlay.INSTALLED_MANIFEST_NAME).read_bytes() == manifest_content
    assert (
        state / release_overlay.INSTALLED_DIGEST_NAME
    ).read_text(encoding="ascii") == expected_digest + "\n"


def test_same_manifest_fast_path_rechecks_only_required_application_files(tmp_path):
    main = _write(tmp_path, "main.py", "current")
    optional = _write(tmp_path, "static/index.html", "current page")
    _install_manifest(tmp_path, "macos", ["main.py", "static/index.html"])
    release_overlay.apply_release_overlay(tmp_path, "macos")

    optional.write_text("locally changed page", encoding="utf-8")
    assert release_overlay.apply_release_overlay(tmp_path, "macos")["status"] == "unchanged"

    main.unlink()
    with pytest.raises(release_overlay.ReleaseOverlayError, match="file is missing"):
        release_overlay.apply_release_overlay(tmp_path, "macos")


def test_release_state_directory_link_is_rejected(tmp_path):
    _install_manifest(tmp_path, "macos", ["main.py"])
    outside = tmp_path / "outside-state"
    outside.mkdir()
    (tmp_path / release_overlay.STATE_DIRECTORY).symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(release_overlay.ReleaseOverlayError, match="state path"):
        release_overlay.apply_release_overlay(tmp_path, "macos")
    assert list(outside.iterdir()) == []


def test_running_server_lock_blocks_overlay_before_cleanup_or_state_commit(tmp_path):
    from mathbank.backup import acquire_runtime_lock

    tombstone = _write(tmp_path, "database.py", "must stay")
    _install_manifest(tmp_path, "macos", ["main.py"])
    lock_path = (
        tmp_path / release_overlay.STATE_DIRECTORY / "runtime.lock"
    )
    held_lock = acquire_runtime_lock(lock_path)
    try:
        with pytest.raises(release_overlay.ReleaseOverlayError, match="服务仍在运行"):
            release_overlay.apply_release_overlay(tmp_path, "macos")
    finally:
        held_lock.close()

    assert tombstone.read_text(encoding="utf-8") == "must stay"
    assert not (
        tmp_path
        / release_overlay.STATE_DIRECTORY
        / release_overlay.INSTALLED_MANIFEST_NAME
    ).exists()


def test_runtime_lock_symlink_is_rejected_without_touching_target(tmp_path):
    _install_manifest(tmp_path, "macos", ["main.py"])
    outside = _write(tmp_path, "outside-lock", "do not touch")
    state = tmp_path / release_overlay.STATE_DIRECTORY
    state.mkdir()
    (state / "runtime.lock").symlink_to(outside)

    with pytest.raises(release_overlay.ReleaseOverlayError, match="not a regular file"):
        release_overlay.apply_release_overlay(tmp_path, "macos")

    assert outside.read_text(encoding="utf-8") == "do not touch"
    assert not (state / release_overlay.INSTALLED_MANIFEST_NAME).exists()


def test_split_state_after_interruption_is_repaired_on_next_run(tmp_path, monkeypatch):
    _write(tmp_path, "main.py", "current")
    manifest_content = _install_manifest(tmp_path, "macos", ["main.py"])
    expected_digest = hashlib.sha256(manifest_content).hexdigest()
    original_atomic_write = release_overlay._atomic_write

    def fail_digest_once(path, content):
        if path.name == release_overlay.INSTALLED_DIGEST_NAME:
            raise OSError("simulated interruption")
        return original_atomic_write(path, content)

    monkeypatch.setattr(release_overlay, "_atomic_write", fail_digest_once)
    with pytest.raises(release_overlay.ReleaseOverlayError, match="cannot save"):
        release_overlay.apply_release_overlay(tmp_path, "macos")

    monkeypatch.setattr(release_overlay, "_atomic_write", original_atomic_write)
    result = release_overlay.apply_release_overlay(tmp_path, "macos")

    assert result["status"] == "unchanged"
    assert (
        tmp_path
        / release_overlay.STATE_DIRECTORY
        / release_overlay.INSTALLED_DIGEST_NAME
    ).read_text(encoding="ascii") == expected_digest + "\n"


def test_tampered_installed_manifest_is_not_trusted_for_deletion(tmp_path):
    _write(tmp_path, "main.py", "version one")
    _write(tmp_path, "mathbank/old.py", "old application")
    _install_manifest(tmp_path, "macos", ["main.py", "mathbank/old.py"])
    release_overlay.apply_release_overlay(tmp_path, "macos")

    installed = (
        tmp_path
        / release_overlay.STATE_DIRECTORY
        / release_overlay.INSTALLED_MANIFEST_NAME
    )
    installed.write_bytes(installed.read_bytes() + b" ")
    _write(tmp_path, "main.py", "version two")
    _install_manifest(tmp_path, "macos", ["main.py"], version="2.0.0")

    with pytest.raises(release_overlay.ReleaseOverlayError, match="digest does not match"):
        release_overlay.apply_release_overlay(tmp_path, "macos")

    assert (tmp_path / "mathbank" / "old.py").exists()
