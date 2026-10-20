"""Deletion races use private temporary stores, never the real library or AI."""
from concurrent.futures import Future, ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

import mathbank.document_projects as project_module
from mathbank.document_projects import DocumentProjectStore, ProjectConflict
from mathbank.document_project_service import DocumentProjectService


def _project(tmp_path):
    store = DocumentProjectStore(tmp_path / "projects")
    project_id = store.create(b"%PDF-private-concurrency-test", "test.pdf", {}, {})["project_id"]
    store.sync_task(project_id, {"task_id": "finished", "status": "completed"})
    return store, project_id


def _hold_process_lease(root, project_id, entered, release):
    store = DocumentProjectStore(Path(root))
    with store.operation(project_id):
        entered.set()
        release.wait(10)


@pytest.mark.parametrize("hold_write", [False, True])
def test_explicit_delete_waits_for_short_read_or_write(tmp_path, hold_write):
    store, project_id = _project(tmp_path)
    entered, release, deleting = threading.Event(), threading.Event(), threading.Event()

    def reader():
        with (store._locked(project_id) if hold_write else store.operation(project_id)):
            entered.set()
            assert release.wait(3)

    def delete():
        deleting.set()
        return store.delete(project_id, wait=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(reader)
        assert entered.wait(3)
        deletion = pool.submit(delete)
        try:
            assert deleting.wait(3)
            assert not deletion.done()
            assert (store.root / project_id).is_dir()
        finally:
            release.set()
        reading.result(3)
        assert deletion.result(3)["deleted_projects"] == [project_id]
    assert not (store.root / project_id).exists()


def test_delete_rechecks_active_binding_after_the_lease_wait(tmp_path):
    store, project_id = _project(tmp_path)
    entered, release = threading.Event(), threading.Event()
    active = set()
    observations = []

    def dispatch():
        with store.operation(project_id):
            entered.set()
            assert release.wait(3)
            store.bind_task(project_id, "newly-submitted")
            active.add("newly-submitted")

    def current_active():
        observations.append(set(active))
        return set(active)

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatching = pool.submit(dispatch)
        assert entered.wait(3)
        deletion = pool.submit(store.delete, project_id, active_task_ids=current_active, wait=True)
        try:
            assert not deletion.done()
        finally:
            release.set()
        dispatching.result(3)
        with pytest.raises(ProjectConflict, match="活跃解析任务"):
            deletion.result(3)
    assert observations == [{"newly-submitted"}]
    assert store.load_source(project_id) == b"%PDF-private-concurrency-test"
    assert store.get(project_id)["task_id"] == "newly-submitted"


def test_explicit_wait_is_bounded_and_cross_process_lease_is_preserved(tmp_path, monkeypatch):
    store, project_id = _project(tmp_path)
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    process = context.Process(target=_hold_process_lease,
                              args=(str(store.root), project_id, entered, release))
    process.start()
    try:
        assert entered.wait(5)
        monkeypatch.setattr(project_module, "_LOCK_WAIT_SECONDS", 0.06)
        began = time.monotonic()
        with pytest.raises(ProjectConflict, match="工程正在使用"):
            store.delete(project_id, wait=True)
        assert 0.04 <= time.monotonic() - began < 1.0
        assert (store.root / project_id).is_dir()
        assert store.cleanup()["deleted_projects"] == []
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert store.delete(project_id, wait=True)["deleted_projects"] == [project_id]


def test_default_delete_and_cleanup_remain_nonblocking_for_busy_project(tmp_path):
    store, project_id = _project(tmp_path)
    other = DocumentProjectStore(store.root)
    with store.operation(project_id):
        with pytest.raises(ProjectConflict, match="工程正在使用"):
            other.delete(project_id)
        result = other.cleanup()
        assert result["deleted_projects"] == []
        assert result["busy_projects"] == [project_id]
    assert store.load_source(project_id) == b"%PDF-private-concurrency-test"


def test_waiting_delete_does_not_wait_for_its_own_shared_lease(tmp_path, monkeypatch):
    store, project_id = _project(tmp_path)
    def unexpected_wait(*args, **kwargs):
        raise AssertionError("cannot wait on this thread's own shared lease")
    with store.operation(project_id):
        monkeypatch.setattr(store, "_lock_fd", unexpected_wait)
        with pytest.raises(ProjectConflict, match="工程正在使用"):
            store.delete(project_id, wait=True)


@pytest.mark.parametrize("status", ["running", "completed", "error", "cancelled", "interrupted"])
def test_unfinished_future_remains_active_including_terminal_flush(status, tmp_path):
    store, _ = _project(tmp_path)
    future = Future()
    tasks = SimpleNamespace(_lock=threading.RLock(), _records={
        "flushing": SimpleNamespace(state={"status": status}, future=future),
        "sentinel": SimpleNamespace(state={"status": status}, future=object()),
        "released": SimpleNamespace(state={"status": status}, future=None),
    })
    service = DocumentProjectService(store, tasks)
    assert service.active_task_ids() == {"flushing", "sentinel"}
    future.set_result(None)
    assert service.active_task_ids() == {"sentinel"}
