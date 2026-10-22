"""Permission failures stop sharing before backup or destination writes.

Run in a disposable source-only checkout: the imported app must never access a
user's real database, credentials, backups or uploads.
"""

import errno
import hashlib
from pathlib import Path
import stat
import tempfile
import uuid

import pytest

from mathbank import backup, sync_helper
from mathbank.storage_access import StorageAccessError
from test_library_transfer_api import transfer_api, _export, _seed, _question_count  # noqa: F401
from test_library_transfer_tasks_api import tasks_api, _start_import, _wait  # noqa: F401


@pytest.mark.parametrize("blocked", ["database_file", "database_directory", "images", "snapshots"])
def test_import_permission_failure_precedes_backup_copy_and_question_writes(tasks_api, monkeypatch, blocked):
    api = tasks_api
    _seed(api)
    archive = _export(api)
    database = Path(api.main.DATABASE_PATH)
    target = {"database_file": database, "database_directory": database.parent,
              "images": api.uploads, "snapshots": api.backups / "snapshots"}[blocked]
    original_open, original_mkstemp = Path.open, tempfile.mkstemp
    blocked_operations = []

    def no_file_write(path, mode="r", *args, **kwargs):
        if blocked == "database_file" and path == target and mode == "r+b":
            blocked_operations.append("file")
            raise PermissionError(errno.EACCES, "private source database filename")
        return original_open(path, mode, *args, **kwargs)

    def no_probe_directory(*args, **kwargs):
        if blocked != "database_file" and Path(kwargs.get("dir", "")) == target:
            blocked_operations.append("directory")
            raise PermissionError(errno.EACCES, "private account and path")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(Path, "open", no_file_write)
    monkeypatch.setattr(tempfile, "mkstemp", no_probe_directory)
    monkeypatch.setattr(backup, "_snapshot_database", lambda *_args, **_kwargs: pytest.fail("backup copied before permission check"))
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in api.uploads.iterdir()}
    accepted = _start_import(api, archive)
    assert accepted.status_code == 202, accepted.text
    result = _wait(api, accepted.json()["task_id"])
    assert result["status"] == "failed" and result["error"]["code"] == "permission"
    message = result["error"]["message"]
    assert "权限" in message and "private" not in message and str(target) not in message
    assert blocked_operations
    assert _question_count(api) == 1
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in api.uploads.iterdir()} == before
    assert not list((api.backups / "snapshots").glob("*.zip"))


def test_export_accepts_readonly_database_and_does_not_probe_input_images_for_write(tasks_api, monkeypatch):
    api = tasks_api
    _seed(api)
    database = Path(api.main.DATABASE_PATH)
    old_mode = stat.S_IMODE(database.stat().st_mode)
    monkeypatch.setattr(api.main, "check_database_write_access", lambda *_args, **_kwargs: pytest.fail("export required database write access"))
    original_check = api.main.check_directory_access

    def check(path, **kwargs):
        assert Path(path) != api.uploads, "export required input image directory write access"
        return original_check(path, **kwargs)

    monkeypatch.setattr(api.main, "check_directory_access", check)
    try:
        database.chmod(stat.S_IREAD)
        assert _export(api)
        task_id = str(uuid.uuid4())
        response = api.client.post("/api/library-transfer/tasks/export", headers=api.headers,
                                   json={"client_request_id": task_id, "source_keywords": []})
        assert response.status_code == 202, response.text
        result = _wait(api, task_id)
        assert result["status"] == "completed"
        assert api.client.get(result["result"]["download_url"]).status_code == 200
    finally:
        database.chmod(old_mode)


def test_text_only_import_does_not_require_image_directory_write_probe(tasks_api, monkeypatch):
    api = tasks_api
    _seed(api, with_image=False)
    archive = _export(api)
    original_check = api.main.check_directory_access

    def check(path, **kwargs):
        assert Path(path) != api.uploads, "text-only import probed the image directory"
        return original_check(path, **kwargs)

    monkeypatch.setattr(api.main, "check_directory_access", check)
    response = _start_import(api, archive)
    assert response.status_code == 202
    assert _wait(api, response.json()["task_id"])["status"] == "completed"
    assert _question_count(api) == 2


def test_derived_export_permission_failure_cannot_reverse_committed_import(tasks_api, monkeypatch):
    api = tasks_api
    _seed(api, with_image=False)
    archive = _export(api)

    def deny_derived(path, **kwargs):
        raise StorageAccessError(path, label=kwargs["label"], stage="create",
                                 cause=PermissionError(errno.EACCES, "private derived output path"))

    monkeypatch.setattr(sync_helper, "check_directory_access", deny_derived)
    task_id = str(uuid.uuid4())
    response = _start_import(api, archive, task_id)
    assert response.status_code == 202
    result = _wait(api, task_id)
    assert result["status"] == "completed" and result["result"]["imported_count"] == 1
    assert _question_count(api) == 2
    assert _start_import(api, archive, task_id).status_code == 200
    assert _question_count(api) == 2
