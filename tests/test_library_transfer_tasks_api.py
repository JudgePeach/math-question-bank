"""Background sharing HTTP contracts; run in a source-only disposable checkout."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
import zipfile

import pytest

from test_library_transfer_api import transfer_api, _export, _seed, _question_count  # noqa: F401
from mathbank.backup import verify_full_backup
from mathbank.database import Question


@pytest.fixture
def tasks_api(transfer_api):
    try:
        yield transfer_api
    finally:
        manager = transfer_api.main._LIBRARY_TRANSFER_TASKS
        if manager is not None:
            manager.close()


def _start_import(api, archive, task_id=None):
    return api.client.post(
        "/api/library-transfer/tasks/import", headers=api.headers,
        data={"client_request_id": task_id or str(uuid.uuid4())},
        files={"file": ("MathBank.zip", archive, "application/zip")},
    )


def _wait(api, task_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = api.client.get(f"/api/library-transfer/tasks/{task_id}", headers=api.headers)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] not in {"queued", "running", "committing"}:
            return result
        time.sleep(0.01)
    pytest.fail(f"Task did not finish: {result}")


def test_retry_after_response_loss_queries_same_append_and_new_uuid_can_repeat(tasks_api):
    api = tasks_api
    _seed(api)
    archive = _export(api)
    task_id = str(uuid.uuid4())
    first = _start_import(api, archive, task_id)
    assert first.status_code == 202, first.text
    # The original response can be lost; refresh/another tab retains the UUID
    # and discovers the accepted operation through this durable status URL.
    finished = _wait(api, task_id)
    assert finished["status"] == "completed", finished
    assert finished["result"]["imported_count"] == finished["result"]["image_count"] == 1
    assert Path(finished["result"]["backup_path"]).is_file()
    assert _question_count(api) == 2
    retry = _start_import(api, archive, task_id)
    assert retry.status_code == 200 and retry.json()["task_id"] == task_id
    assert _question_count(api) == 2
    assert not list((api.generated / "library_transfer_tasks" / "_uploads").iterdir())
    assert not list((api.generated / "library_transfer_tasks").rglob("input.zip"))
    assert set(finished["stage_timings"]) >= {"validating", "backup", "installing", "writing", "fingerprinting", "committing"}
    assert "intent" not in finished and "input.zip" not in json.dumps(finished)
    assert len(list((api.backups / "snapshots").glob("*.zip"))) == 1
    explicit = _start_import(api, archive)
    assert explicit.status_code == 202
    assert _wait(api, explicit.json()["task_id"])["status"] == "completed"
    assert _question_count(api) == 3


def test_upload_cleanup_failure_preserves_accepted_response_and_same_uuid_retry(tasks_api, monkeypatch):
    import tempfile

    api = tasks_api
    _seed(api, with_image=False)
    archive = _export(api)
    task_id = str(uuid.uuid4())
    original_cleanup = tempfile.TemporaryDirectory.cleanup
    failures = []

    def occupied_upload_directory(directory):
        if Path(directory.name).parent.name == "_uploads":
            failures.append(directory.name)
            original_cleanup(directory)
            failure = PermissionError(13, "simulated Windows directory sharing violation")
            failure.winerror = 32
            raise failure
        return original_cleanup(directory)

    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", occupied_upload_directory)
    accepted = _start_import(api, archive, task_id)
    assert accepted.status_code == 202, accepted.text
    assert _wait(api, task_id)["status"] == "completed"
    retry = _start_import(api, archive, task_id)
    assert retry.status_code == 200 and retry.json()["task_id"] == task_id
    assert len(failures) == 2
    assert _question_count(api) == 2
    assert len(list((api.backups / "snapshots").glob("*.zip"))) == 1


def test_same_uuid_different_package_or_operation_returns_conflict(tasks_api):
    api = tasks_api
    _seed(api, with_image=False)
    task_id = str(uuid.uuid4())
    archive = _export(api)
    accepted = _start_import(api, archive, task_id)
    assert accepted.status_code == 202
    assert _wait(api, task_id)["status"] == "completed"
    conflict = _start_import(api, archive + b"changed bytes", task_id)
    assert conflict.status_code == 409
    conflict = api.client.post(
        "/api/library-transfer/tasks/export", headers=api.headers,
        json={"client_request_id": task_id, "source_keywords": []},
    )
    assert conflict.status_code == 409
    assert _question_count(api) == 2
    assert not list((api.generated / "library_transfer_tasks" / "_uploads").iterdir())


def test_bad_archive_fails_before_backup_and_remains_queryable(tasks_api):
    api = tasks_api
    result = _start_import(api, b"invalid archive")
    assert result.status_code == 202
    task_id = result.json()["task_id"]
    final = _wait(api, task_id)
    assert final["status"] == "failed" and final["error"]["code"] == "validation"
    assert _question_count(api) == 0
    assert not list(api.backups.rglob("*.zip"))
    assert not list(api.uploads.glob("shared_*"))
    assert _start_import(api, b"invalid archive", task_id).status_code == 200


@pytest.mark.parametrize("background", [True, False])
def test_missing_old_images_are_recorded_in_backup_and_do_not_block_append(tasks_api, background):
    api = tasks_api
    original_id = _seed(api)
    archive = _export(api)
    with api.sessions() as session:
        original = session.get(Question, original_id)
        before = {column.name: getattr(original, column.name) for column in Question.__table__.columns}
    (api.uploads / "figure.png").unlink()

    if background:
        response = _start_import(api, archive)
        assert response.status_code == 202, response.text
        finished = _wait(api, response.json()["task_id"])
        assert finished["status"] == "completed", finished
        result = finished["result"]
        assert api.client.get(f"/api/library-transfer/tasks/{finished['task_id']}").json()["result"] == result
    else:
        response = api.client.post(
            "/api/library-transfer/import", headers=api.headers,
            files={"file": ("MathBank.zip", archive, "application/zip")},
        )
        assert response.status_code == 200, response.text
        result = response.json()
    assert result["imported_count"] == result["image_count"] == 1
    assert _question_count(api) == 2
    warnings = result["backup_warnings"]
    assert len(warnings) == 1 and warnings[0]["code"] == "missing_existing_images"
    assert warnings[0]["missing_count"] == 1 and "缺失" in warnings[0]["message"]
    assert str(api.uploads) not in json.dumps(warnings)
    manifest = verify_full_backup(Path(result["backup_path"]))
    assert manifest["database"]["row_counts"]["questions"] == 1
    with zipfile.ZipFile(result["backup_path"]) as backup:
        assert "uploads/figure.png" not in backup.namelist()
    with api.sessions() as session:
        original = session.get(Question, original_id)
        assert {column.name: getattr(original, column.name) for column in Question.__table__.columns} == before
        imported = session.query(Question).filter(Question.id != original_id).one()
        assert imported.image_paths and (api.uploads / Path(imported.image_paths[0]).name).is_file()
    assert not (api.uploads / "figure.png").exists(), "append must not silently repair old references"


def test_import_authentication_precedes_private_multipart_parsing(tasks_api, monkeypatch):
    from mathbank import library_transfer_upload

    def forbidden_parser(*args, **kwargs):
        pytest.fail("unauthenticated request reached multipart file staging")

    monkeypatch.setattr(library_transfer_upload._SharingMultipartParser, "on_headers_finished", forbidden_parser)
    for path in ("/api/library-transfer/tasks/import", "/api/library-transfer/import"):
        response = tasks_api.client.post(path, files={"file": ("MathBank.zip", b"bad", "application/zip")},
                                         data={"client_request_id": str(uuid.uuid4())})
        assert response.status_code == 403
    assert not (tasks_api.generated / "library_transfer_uploads").exists()


def test_export_finishes_before_repeatable_stream_download_and_missing_file_is_gone(tasks_api):
    api = tasks_api
    _seed(api)
    task_id = str(uuid.uuid4())
    response = api.client.post(
        "/api/library-transfer/tasks/export", headers=api.headers,
        json={"client_request_id": task_id, "source_keywords": []},
    )
    assert response.status_code == 202, response.text
    finished = _wait(api, task_id)
    assert finished["status"] == "completed", finished
    assert finished["result"]["question_count"] == 1
    url = finished["result"]["download_url"]
    first, second = api.client.get(url), api.client.get(url)
    assert first.status_code == second.status_code == 200
    assert hashlib.sha256(first.content).digest() == hashlib.sha256(second.content).digest()
    assert first.headers["content-type"].startswith("application/zip")
    assert first.headers["cache-control"] == "no-store"
    assert "library.zip" not in json.dumps(finished)
    (api.generated / "library_transfer_tasks" / task_id / "library.zip").unlink()
    assert api.client.get(url).status_code == 410
    assert api.client.get(f"/api/library-transfer/tasks/{task_id}").json()["status"] == "completed"


def test_validation_and_upload_do_not_hold_asset_or_textbook_write_locks(tasks_api, monkeypatch):
    api = tasks_api
    _seed(api)
    archive = _export(api)
    entered, release = threading.Event(), threading.Event()
    original = api.main.prepare_library_import
    @contextmanager
    def pause(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        with original(*args, **kwargs) as prepared:
            yield prepared
    monkeypatch.setattr(api.main, "prepare_library_import", pause)
    response = _start_import(api, archive)
    assert response.status_code == 202
    try:
        assert entered.wait(5)
        # The test thread differs from the worker, so RLock recursion cannot
        # make this pass if validation is inside the asset lifecycle lock.
        assert api.main.ASSET_LIFECYCLE_LOCK.acquire(timeout=0.5)
        api.main.ASSET_LIFECYCLE_LOCK.release()
        assert api.main._METADATA_SAVE_LOCK.acquire(timeout=0.5)
        api.main._METADATA_SAVE_LOCK.release()
        pending = api.client.get(f"/api/library-transfer/tasks/{response.json()['task_id']}/download")
        assert pending.status_code == 409
    finally:
        release.set()
    assert _wait(api, response.json()["task_id"])["status"] == "completed"


def test_two_windows_with_different_uuids_cannot_concurrently_append_the_same_archive(tasks_api, monkeypatch):
    api = tasks_api
    _seed(api)
    archive = _export(api)
    entered, release = threading.Event(), threading.Event()
    original = api.main.prepare_library_import
    @contextmanager
    def pause(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        with original(*args, **kwargs) as prepared:
            yield prepared
    monkeypatch.setattr(api.main, "prepare_library_import", pause)
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    response = _start_import(api, archive, first_id)
    assert response.status_code == 202
    try:
        assert entered.wait(5)
        conflict = _start_import(api, archive, second_id)
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "active_import"
        assert conflict.json()["active_task_id"] == first_id
        assert _start_import(api, archive, first_id).status_code == 200
        assert not (api.generated / "library_transfer_tasks" / second_id).exists()
        assert not list((api.generated / "library_transfer_tasks" / "_uploads").iterdir())
    finally:
        release.set()
    assert _wait(api, first_id)["status"] == "completed"
    assert _question_count(api) == 2
    # Once the first operation is complete, a fresh explicit intent remains
    # an append rather than an accidental persistent package deduplication.
    explicit = _start_import(api, archive, second_id)
    assert explicit.status_code == 202
    assert _wait(api, second_id)["status"] == "completed"
    assert _question_count(api) == 3


@pytest.mark.parametrize("endpoint", ["synchronous", "task"])
def test_real_commit_followed_by_exception_keeps_images_and_warns_against_retry(tasks_api, monkeypatch, endpoint):
    from mathbank.database import Question

    api = tasks_api
    _seed(api)
    archive = _export(api)
    original_image = (api.uploads / "figure.png").read_bytes()
    real_importer = api.main.import_library_zip
    def importer_with_late_commit_error(db, *args, **kwargs):
        real_commit = db.commit
        def commit_then_raise():
            real_commit()
            raise OSError("controlled exception after successful commit")
        db.commit = commit_then_raise
        try:
            return real_importer(db, *args, **kwargs)
        finally:
            db.commit = real_commit
    monkeypatch.setattr(api.main, "import_library_zip", importer_with_late_commit_error)
    if endpoint == "task":
        task_id = str(uuid.uuid4())
        response = _start_import(api, archive, task_id)
        assert response.status_code == 202
        result = _wait(api, task_id)
        assert result["status"] == "unknown" and result["error"]["code"] == "unknown_after_commit"
        assert "勿直接再次导入" in result["error"]["message"]
        assert Path(result["result"]["backup_path"]).is_file()
        retry = _start_import(api, archive, task_id)
        assert retry.status_code == 200 and retry.json()["status"] == "unknown"
    else:
        response = api.client.post(
            "/api/library-transfer/import", headers=api.headers,
            files={"file": ("MathBank.zip", archive, "application/zip")},
        )
        assert response.status_code == 500
        assert "勿直接再次导入" in response.json()["detail"]
        assert "已回滚" not in response.json()["detail"]
    assert _question_count(api) == 2
    with api.sessions() as db:
        newest = db.query(Question).order_by(Question.id.desc()).first()
        assert len(newest.image_paths) == 1
        assert (api.uploads / Path(newest.image_paths[0]).name).read_bytes() == original_image
    assert len(list(api.uploads.glob("shared_*"))) == 1


def test_oversize_and_invalid_request_id_cleanup_uploads(tasks_api, monkeypatch):
    api = tasks_api
    monkeypatch.setattr(api.main, "MAX_LIBRARY_ARCHIVE_BYTES", 10)
    oversized = _start_import(api, b"x" * 11)
    assert oversized.status_code == 413
    invalid = _start_import(api, b"x", "../unsafe")
    assert invalid.status_code == 400
    assert not list((api.generated / "library_transfer_tasks").rglob("*.zip"))
    assert _question_count(api) == 0


def test_task_submission_requires_existing_local_token(tasks_api):
    api = tasks_api
    imported = api.client.post(
        "/api/library-transfer/tasks/import", data={"client_request_id": str(uuid.uuid4())},
        files={"file": ("library.zip", b"bad", "application/zip")},
    )
    exported = api.client.post(
        "/api/library-transfer/tasks/export", json={"client_request_id": str(uuid.uuid4())},
    )
    assert imported.status_code == exported.status_code == 403
    assert not api.generated.exists()
