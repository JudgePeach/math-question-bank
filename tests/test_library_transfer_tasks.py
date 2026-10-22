"""Durable intent and worker failure boundaries without a production database."""

import errno
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import uuid

import pytest

from mathbank import library_transfer_tasks as task_module
from mathbank.library_transfer_tasks import (
    EXPORT_RETENTION_SECONDS, LibraryTransferTasks, TransferTaskConflict,
    TransferTaskError, TransferTaskQueueFull, log_transfer_error,
    normalize_transfer_task_id, transfer_error,
)
from mathbank.storage_access import StorageAccessError


def _id():
    return str(uuid.uuid4())


def _archive(tmp_path, name="upload.zip", data=b"test package"):
    path = tmp_path / name
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def _wait(tasks, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = tasks.get(task_id)
        if result["status"] not in {"queued", "running", "committing"}:
            return result
        time.sleep(0.005)
    pytest.fail(f"Task did not finish: {tasks.get(task_id)}")


def _export(directory, payload, progress):
    progress("packaging", current=1, total=1)
    (directory / "library.zip").write_bytes(b"downloadable archive")
    return {"question_count": 2, "image_count": 0}


def test_concurrent_same_intent_runs_once_and_new_intent_still_appends(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(directory, progress):
        calls.append(directory.name)
        entered.set()
        assert release.wait(5)
        progress("committing", current=0, total=1)
        state = json.loads((directory / "state.json").read_text())
        assert state["status"] == "committing"
        return {"imported_count": 1, "image_count": 0}

    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=run, run_export=_export)
    task_id = _id()
    archive, digest = _archive(tmp_path)
    try:
        first, created = tasks.submit_import(task_id, archive, digest)
        assert created and first["status"] == "queued"
        assert entered.wait(5)
        # A second HTTP upload has its own private filename; it cannot replace
        # the accepted input or submit another database transaction.
        duplicate, _ = _archive(tmp_path, "retry.zip")
        second, created = tasks.submit_import(task_id, duplicate, digest)
        assert not created and second["task_id"] == first["task_id"]
        assert duplicate.exists()
        with pytest.raises(TransferTaskConflict):
            tasks.submit_import(task_id, duplicate, "0" * 64)
        with pytest.raises(TransferTaskConflict):
            tasks.submit_export(task_id, {})
        release.set()
        assert _wait(tasks, task_id)["status"] == "completed"
        explicit = _id()
        tasks.submit_import(explicit, duplicate, digest)
        assert _wait(tasks, explicit)["status"] == "completed"
        assert calls == [task_id, explicit]
        assert not (tasks.root / task_id / "input.zip").exists()
        assert "intent" not in tasks.get(task_id)
        assert "archive" not in json.dumps(tasks.get(task_id))
    finally:
        release.set()
        tasks.close()
    # The same UUID remains protected after process restart.
    restarted = LibraryTransferTasks(tasks.root, run_import=lambda *_: pytest.fail("replayed"), run_export=_export)
    retry, _ = _archive(tmp_path, "restart-retry.zip")
    try:
        result, created = restarted.submit_import(task_id, retry, digest)
        assert not created and result["status"] == "completed"
    finally:
        restarted.close()


def test_full_queue_rejects_without_creating_receipt_or_consuming_upload(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def run(directory, progress):
        entered.set()
        assert release.wait(5)
        return {"imported_count": 1}
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=run, run_export=_export, max_pending=1)
    try:
        archive, digest = _archive(tmp_path)
        tasks.submit_import(_id(), archive, digest)
        assert entered.wait(5)
        archive, pending_digest = _archive(tmp_path, "pending.zip", data=b"a different pending package")
        tasks.submit_import(_id(), archive, pending_digest)
        rejected = _id()
        archive, rejected_digest = _archive(tmp_path, "rejected.zip", data=b"another rejected package")
        with pytest.raises(TransferTaskQueueFull):
            tasks.submit_import(rejected, archive, rejected_digest)
        assert archive.exists() and not (tasks.root / rejected).exists()
    finally:
        release.set()
        tasks.close()


def test_same_active_archive_with_another_uuid_follows_original_job(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(directory, progress):
        calls.append(directory.name)
        entered.set()
        assert release.wait(5)
        return {"imported_count": 1}
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=run, run_export=_export)
    first_id, second_id = _id(), _id()
    archive, digest = _archive(tmp_path)
    try:
        tasks.submit_import(first_id, archive, digest)
        assert entered.wait(5)
        another, _ = _archive(tmp_path, "another-window.zip")
        with pytest.raises(TransferTaskConflict) as conflict:
            tasks.submit_import(second_id, another, digest)
        assert conflict.value.active_task_id == first_id
        assert not (tasks.root / second_id).exists() and another.exists()
        # Own-ID idempotency takes priority over the same-archive guard.
        _, created = tasks.submit_import(first_id, another, digest)
        assert not created
        release.set()
        assert _wait(tasks, first_id)["status"] == "completed"
        tasks.submit_import(second_id, another, digest)
        assert _wait(tasks, second_id)["status"] == "completed"
        assert calls == [first_id, second_id]
    finally:
        release.set()
        tasks.close()


def _saved_task(root, status, *, operation="import", changed_at=None):
    task_id = _id()
    directory = root / task_id
    directory.mkdir(parents=True)
    record = {"task_id": task_id, "operation": operation, "intent": operation + ":" + "a" * 64,
              "status": status, "stage": status, "current": 0, "total": 1,
              "stage_timings": {}, "created_at": 1, "updated_at": changed_at or time.time()}
    (directory / "state.json").write_text(json.dumps(record))
    return task_id


@pytest.mark.parametrize("previous,expected", [("queued", "interrupted"), ("running", "interrupted"), ("committing", "unknown")])
def test_restart_never_replays_pending_or_uncertain_append(tmp_path, previous, expected):
    root = tmp_path / "tasks"
    task_id = _saved_task(root, previous)
    tasks = LibraryTransferTasks(root, run_import=lambda *_: pytest.fail("replayed"), run_export=_export)
    archive, digest = _archive(tmp_path)
    try:
        result, created = tasks.submit_import(task_id, archive, "a" * 64)
        assert not created and result["status"] == expected
        assert result["error"]["code"] == ("unknown_after_commit" if expected == "unknown" else "interrupted")
        persisted = json.loads((root / task_id / "state.json").read_text())
        assert persisted["status"] == expected
        assert archive.exists()
    finally:
        tasks.close()


def test_damaged_receipt_directory_still_prevents_reuse(tmp_path):
    task_id = _saved_task(tmp_path / "tasks", "committing")
    (tmp_path / "tasks" / task_id / "state.json").unlink()
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: pytest.fail("replayed"), run_export=_export)
    try:
        assert tasks.get(task_id)["status"] == "unknown"
        archive, digest = _archive(tmp_path)
        with pytest.raises(TransferTaskConflict):
            tasks.submit_import(task_id, archive, digest)
    finally:
        tasks.close()


@pytest.mark.parametrize("stage,exception,code,status", [
    ("writing", sqlite3.OperationalError("database is locked; secret question"), "database_locked", "failed"),
    ("validating", ValueError("secret question"), "validation", "failed"),
    ("packaging", OSError(errno.ENOSPC, "secret file"), "disk", "failed"),
    ("committing", RuntimeError("secret SQL/parameters"), "unknown_after_commit", "unknown"),
])
def test_errors_are_specific_and_do_not_expose_sql_or_question_text(tmp_path, caplog, stage, exception, code, status):
    def run(directory, progress):
        progress("backup", result={"backup_path": "recovery.zip"})
        progress(stage)
        raise exception
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=run, run_export=_export)
    try:
        archive, digest = _archive(tmp_path)
        task_id = _id()
        tasks.submit_import(task_id, archive, digest)
        result = _wait(tasks, task_id)
        assert result["status"] == status and result["error"]["code"] == code
        assert result["result"]["backup_path"] == "recovery.zip"
        assert "secret" not in json.dumps(result) + caplog.text
        assert "traceback=" in caplog.text
    finally:
        tasks.close()


def test_completion_receipt_failure_reports_unknown_without_rerun(tmp_path, monkeypatch):
    def run(directory, progress):
        progress("committing")
        return {"imported_count": 1}
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=run, run_export=_export)
    original = tasks._save
    def failed_completion(record):
        if record["status"] == "completed":
            raise OSError(errno.ENOSPC, "injected disk full")
        original(record)
    monkeypatch.setattr(tasks, "_save", failed_completion)
    try:
        archive, digest = _archive(tmp_path)
        task_id = _id()
        tasks.submit_import(task_id, archive, digest)
        result = _wait(tasks, task_id)
        assert result["status"] == "unknown" and result["error"]["code"] == "unknown_after_commit"
        retry, _ = _archive(tmp_path, "retry.zip")
        _, created = tasks.submit_import(task_id, retry, digest)
        assert not created
    finally:
        tasks.close()


def test_cleanup_failure_cannot_erase_commit_or_stop_the_worker(tmp_path, monkeypatch):
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: {"imported_count": 1}, run_export=_export)
    original = Path.unlink
    def fail_input_cleanup(path, *args, **kwargs):
        if path.name == "input.zip":
            raise PermissionError("controlled cleanup failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_input_cleanup)
    try:
        for index in range(2):
            archive, digest = _archive(tmp_path, f"{index}.zip")
            task_id = _id()
            tasks.submit_import(task_id, archive, digest)
            assert _wait(tasks, task_id)["status"] == "completed"
        assert tasks._worker.is_alive()
    finally:
        tasks.close()


def test_download_is_repeatable_and_startup_expiry_keeps_intent_receipt(tmp_path):
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: None, run_export=_export)
    task_id = _id()
    payload = {"question_ids": [1, 2]}
    try:
        tasks.submit_export(task_id, payload)
        assert _wait(tasks, task_id)["status"] == "completed"
        first, _ = tasks.download(task_id)
        assert first.read_bytes() == tasks.download(task_id)[0].read_bytes()
    finally:
        tasks.close()
    state_path = tasks.root / task_id / "state.json"
    state = json.loads(state_path.read_text())
    state["updated_at"] = time.time() - EXPORT_RETENTION_SECONDS - 1
    state_path.write_text(json.dumps(state))
    restarted = LibraryTransferTasks(tasks.root, run_import=lambda *_: None, run_export=_export)
    try:
        assert state_path.is_file()
        assert restarted.get(task_id)["status"] == "completed"
        _, created = restarted.submit_export(task_id, payload)
        assert not created
        with pytest.raises(FileNotFoundError):
            restarted.download(task_id)
    finally:
        restarted.close()


def test_upload_slots_are_bounded_and_cleanup_after_failure(tmp_path):
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: None, run_export=_export, max_pending=1)
    try:
        with tasks.upload_workspace() as first, tasks.upload_workspace() as second:
            (first / "part.zip").write_bytes(b"incomplete")
            with pytest.raises(TransferTaskQueueFull):
                with tasks.upload_workspace():
                    pytest.fail("unbounded upload")
            assert first != second
        assert not list((tasks.root / "_uploads").iterdir())
        with pytest.raises(ValueError):
            with tasks.upload_workspace() as failed:
                raise ValueError("interrupted upload")
        assert not failed.exists()
        with tasks.upload_workspace():
            pass
    finally:
        tasks.close()


@pytest.mark.parametrize("winerror", [32, 33])
def test_accepted_upload_cleanup_failure_preserves_receipt_and_idempotency(tmp_path, monkeypatch, caplog, winerror):
    calls, deferred = [], []
    original_cleanup = tempfile.TemporaryDirectory.cleanup

    def occupied_cleanup(temporary):
        if Path(temporary.name).name.startswith("upload-"):
            temporary._finalizer.detach()
            deferred.append(temporary)
            failure = PermissionError(errno.EACCES, "private upload directory")
            failure.winerror = winerror
            raise failure
        return original_cleanup(temporary)

    def run(directory, progress):
        calls.append(directory.name)
        return {"imported_count": 1}

    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=run, run_export=_export)
    task_id = _id()
    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", occupied_cleanup)
    try:
        with tasks.upload_workspace() as workspace:
            archive, digest = _archive(workspace)
            result, created = tasks.submit_import(task_id, archive, digest)
        assert created and result["task_id"] == task_id
        assert workspace.exists()  # Deferred removal cannot invalidate success.
        assert _wait(tasks, task_id)["status"] == "completed"
        persisted = json.loads((tasks.root / task_id / "state.json").read_text(encoding="utf-8"))
        assert persisted["status"] == "completed"
        with tasks.upload_workspace() as retry_workspace:
            retry, _ = _archive(retry_workspace)
            result, created = tasks.submit_import(task_id, retry, digest)
        assert not created and result["status"] == "completed"
        assert calls == [task_id]
        assert '"winerror": ' + str(winerror) in caplog.text
        assert "private upload directory" not in caplog.text
        tasks.close()
        restarted = LibraryTransferTasks(tasks.root, run_import=lambda *_: pytest.fail("replayed"), run_export=_export)
        try:
            assert restarted.get(task_id)["status"] == "completed"
            assert not workspace.exists() and not retry_workspace.exists()
        finally:
            restarted.close()
    finally:
        tasks.close()
        for temporary in deferred:
            original_cleanup(temporary)


@pytest.mark.parametrize("original_error", [
    ValueError("interrupted before acceptance"),
    PermissionError(errno.EACCES, "original upload write failure"),
])
def test_upload_cleanup_failure_preserves_body_error_and_releases_slots(tmp_path, monkeypatch, original_error):
    original_cleanup, deferred = tempfile.TemporaryDirectory.cleanup, []

    def occupied_cleanup(temporary):
        temporary._finalizer.detach()
        deferred.append(temporary)
        raise PermissionError(errno.EACCES, "controlled cleanup failure")

    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: None, run_export=_export, max_pending=1)
    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", occupied_cleanup)
    try:
        with pytest.raises(type(original_error)) as caught:
            with tasks.upload_workspace() as workspace:
                (workspace / "part.zip").write_bytes(b"incomplete")
                raise original_error
        assert caught.value is original_error
        assert not tasks._records
        # Both available slots can be reused even after the failed cleanup.
        with tasks.upload_workspace(), tasks.upload_workspace():
            with pytest.raises(TransferTaskQueueFull):
                with tasks.upload_workspace():
                    pytest.fail("unbounded upload")
    finally:
        tasks.close()
        for temporary in deferred:
            original_cleanup(temporary)


@pytest.mark.parametrize("value", [None, "../outside", "", uuid.uuid4().hex])
def test_task_id_never_becomes_an_arbitrary_path(value):
    with pytest.raises(ValueError):
        normalize_transfer_task_id(value)


def test_uncertain_import_exception_is_never_reported_as_safe_to_retry():
    from mathbank.library_transfer import LibraryImportCommitUncertain

    error = transfer_error(LibraryImportCommitUncertain("internal context"))
    assert error["code"] == "unknown_after_commit"
    assert "勿直接再次导入" in error["message"]


def test_failed_thread_start_does_not_poison_later_jobs_or_close(tmp_path, monkeypatch):
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: {"imported_count": 1}, run_export=_export)
    original_start, attempts = threading.Thread.start, []
    def first_start_fails(worker):
        attempts.append(worker)
        if len(attempts) == 1:
            raise RuntimeError("can't start new thread")
        return original_start(worker)
    monkeypatch.setattr(threading.Thread, "start", first_start_fails)
    first, second = _id(), _id()
    try:
        archive, digest = _archive(tmp_path)
        with pytest.raises(RuntimeError, match="can't start"):
            tasks.submit_import(first, archive, digest)
        assert not tasks.started and tasks._worker is None
        assert tasks.get(first)["status"] == "failed"
        archive, digest = _archive(tmp_path, "second.zip")
        tasks.submit_import(second, archive, digest)
        assert _wait(tasks, second)["status"] == "completed"
        assert tasks.started and tasks._worker.is_alive()
    finally:
        tasks.close()


def test_close_tolerates_an_unstarted_worker(tmp_path):
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: None, run_export=_export)
    tasks._worker = threading.Thread(target=lambda: None)
    tasks.close()


def test_recovery_receipt_write_failure_keeps_uuid_bound_and_server_available(tmp_path, monkeypatch, caplog):
    root = tmp_path / "tasks"
    failed, healthy = _saved_task(root, "running"), _saved_task(root, "running")
    original_save = LibraryTransferTasks._save
    def cannot_save_one(self, record):
        if record["task_id"] == failed:
            failure = PermissionError(errno.EACCES, "private path /Users/person/private")
            failure.winerror = 32
            raise failure
        return original_save(self, record)
    monkeypatch.setattr(LibraryTransferTasks, "_save", cannot_save_one)
    tasks = LibraryTransferTasks(root, run_import=lambda *_: pytest.fail("replayed"), run_export=_export)
    try:
        assert tasks.get(failed)["status"] == "unknown"
        assert tasks.get(failed)["error"]["code"] == "receipt_unavailable"
        assert tasks.get(healthy)["status"] == "interrupted"
        archive, _ = _archive(tmp_path)
        same, created = tasks.submit_import(failed, archive, "a" * 64)
        assert not created and same["status"] == "unknown" and archive.exists()
        assert json.loads((root / failed / "state.json").read_text())["status"] == "running"
        assert '"winerror": 32' in caplog.text and "private path" not in caplog.text
    finally:
        tasks.close()


@pytest.mark.parametrize("sqlite_code,expected", [
    (5, "database_locked"), (6, "database_locked"), (8, "database_readonly"),
    (1032, "database_readonly"), (10, "database_io"), (778, "database_io"),
    (14, "database_io"), (13, "disk"), (7, "memory"),
])
def test_sqlite_result_codes_classify_wrapped_errors_without_private_text(sqlite_code, expected):
    from sqlalchemy.exc import OperationalError
    original = sqlite3.OperationalError("private SQL values /Users/person/private")
    original.sqlite_errorcode = sqlite_code
    error = OperationalError("INSERT private SQL", {"private": "question text"}, original)
    result = transfer_error(error)
    assert result["code"] == expected
    assert "private" not in json.dumps(result) and "question text" not in json.dumps(result)


@pytest.mark.parametrize("message,expected", [
    ("database is locked", "database_locked"),
    ("database table is locked: questions", "database_locked"),
    ("attempt to write a readonly database", "database_readonly"),
    ("database or disk is full", "disk"),
    ("disk I/O error", "database_io"),
    ("unable to open database file", "database_io"),
    ("out of memory", "memory"),
])
def test_python310_sqlite_error_messages_without_result_codes(message, expected):
    original = sqlite3.OperationalError(message)
    assert not hasattr(original, "sqlite_errorcode")
    assert transfer_error(original)["code"] == expected


@pytest.mark.parametrize("winerror,expected", [
    (5, "permission"), (32, "file_in_use"), (33, "file_in_use"), (112, "disk"), (39, "disk"),
])
def test_windows_storage_error_codes_are_specific_and_safe(winerror, expected):
    original = PermissionError(errno.EACCES, "private SQL and filename", "/Users/person/private")
    original.winerror = winerror
    assert transfer_error(original)["code"] == expected
    assert "private" not in json.dumps(transfer_error(original))


def test_wrapped_backup_busy_and_memory_errors_keep_specific_safe_guidance():
    original = RuntimeError("private SQLite operation")
    original.backup_busy = True
    wrapper = TransferTaskError("backup", "备份失败")
    wrapper.__cause__ = original
    assert transfer_error(wrapper)["code"] == "backup_busy"
    wrapper.__cause__ = MemoryError("private large question")
    assert transfer_error(wrapper)["code"] == "memory"
    assert transfer_error(wrapper, committing=True)["code"] == "unknown_after_commit"
    assert transfer_error(RuntimeError("database is locked private text"))["code"] == "internal"


def test_error_log_records_numeric_codes_without_sql_parameters_or_paths(caplog):
    from sqlalchemy.exc import OperationalError
    original = sqlite3.OperationalError("private question and filename /Users/person/private")
    original.sqlite_errorcode = 13
    wrapped = OperationalError("INSERT INTO questions private SQL", {"content": "private question"}, original)
    log_transfer_error(_id(), "import", "writing", wrapped)
    assert '"sqlite_errorcode": 13' in caplog.text
    assert "private" not in caplog.text and "INSERT" not in caplog.text


def _directory_permission_error(tmp_path, label):
    cause = PermissionError(errno.EACCES, "private operating system error", "/Users/private-user/secret-path")
    cause.winerror = 5
    return StorageAccessError(tmp_path / "private-user" / "secret-path", label=label,
                              stage="replace", cause=cause)


@pytest.mark.parametrize("operation", ["import", "export"])
def test_new_job_directory_permission_failure_is_not_accepted_or_consumed(tmp_path, monkeypatch, operation):
    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=lambda *_: {}, run_export=_export)
    archive, digest = _archive(tmp_path)
    task_id = _id()
    label = "分享任务回执目录（.system_generated/library_transfer_tasks）"
    failure = _directory_permission_error(tmp_path, label)
    probes = []

    def deny(path, **options):
        probes.append((Path(path), options))
        raise failure

    monkeypatch.setattr(task_module, "check_directory_access", deny)
    try:
        with pytest.raises(StorageAccessError) as raised:
            if operation == "import":
                tasks.submit_import(task_id, archive, digest)
            else:
                tasks.submit_export(task_id, {})
        assert raised.value is failure
        assert probes == [(tasks.root, {"label": label, "require_subdirectory": True})]
        assert tasks.get(task_id) is None and not tasks.started and tasks._queue.empty()
        assert not (tasks.root / task_id).exists() and not list(tasks.root.iterdir())
        assert archive.read_bytes() == b"test package"
        assert transfer_error(failure) == {"code": failure.code, "message": failure.safe_message}
        assert label in failure.safe_message and "private-user" not in failure.safe_message
    finally:
        tasks.close()


@pytest.mark.parametrize("operation", ["import", "export"])
def test_completed_uuid_replay_skips_new_directory_write_probe(tmp_path, monkeypatch, operation):
    calls = []

    def run_import(directory, progress):
        calls.append(directory.name)
        progress("committing")
        return {"imported_count": 1, "image_count": 0}

    tasks = LibraryTransferTasks(tmp_path / "tasks", run_import=run_import, run_export=_export)
    task_id = _id()
    archive, digest = _archive(tmp_path)
    try:
        if operation == "import":
            tasks.submit_import(task_id, archive, digest)
        else:
            tasks.submit_export(task_id, {})
        completed = _wait(tasks, task_id)
        assert completed["status"] == "completed"
        before = (tasks.root / task_id / "state.json").read_bytes()

        def forbidden(*args, **kwargs):
            pytest.fail("completed UUID replay attempted a fresh directory write probe")

        monkeypatch.setattr(task_module, "check_directory_access", forbidden)
        if operation == "import":
            retry, _ = _archive(tmp_path, "retry.zip")
            replayed, created = tasks.submit_import(task_id, retry, digest)
            assert retry.read_bytes() == b"test package" and calls == [task_id]
        else:
            replayed, created = tasks.submit_export(task_id, {})
        assert not created and replayed == completed
        assert (tasks.root / task_id / "state.json").read_bytes() == before
    finally:
        tasks.close()


def test_directory_probe_errors_keep_safe_label_and_commit_uncertainty_priority(tmp_path, caplog):
    label = "题库数据目录"
    failure = _directory_permission_error(tmp_path, label)
    wrapped = TransferTaskError("backup", "备份失败")
    wrapped.__cause__ = failure
    assert transfer_error(wrapped) == {"code": failure.code, "message": failure.safe_message}
    assert label in transfer_error(wrapped)["message"]
    assert transfer_error(wrapped, committing=True)["code"] == "unknown_after_commit"
    log_transfer_error(_id(), "import", "checking_storage", wrapped)
    assert '"winerror": 5' in caplog.text
    assert "private-user" not in caplog.text and "secret-path" not in caplog.text
    assert "private operating system error" not in caplog.text
