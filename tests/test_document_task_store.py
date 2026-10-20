"""Crash recovery and cancellation intent, without a server or user database."""
from pathlib import Path
from copy import deepcopy
import json
import threading

import pytest

from mathbank.document_task_store import DocumentTaskStore, DurableDocumentTasks


def test_restart_keeps_checkpoint_and_requires_explicit_resume(tmp_path):
    store = DocumentTaskStore(tmp_path)
    original = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    source = store.source("recovery-1", b"immutable-source")
    original.create("recovery-1", document_type="pdf", document_input=source)
    original.update("recovery-1", status="ocr_extraction", progress=55,
                    pdf_resume_state={"completed_pages": {"0": {"markdown": "first page"}}})
    original.shutdown()
    recovered = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    try:
        assert recovered.snapshot("recovery-1")["status"] == "interrupted"
        assert recovered.snapshot("recovery-1")["pdf_resume_state"]["completed_pages"]["0"]["markdown"] == "first page"
        assert store.source("recovery-1") == b"immutable-source"
        assert recovered._records["recovery-1"].future is None
        recovered.restart("recovery-1")
        assert recovered.submit("recovery-1", lambda: recovered.complete("recovery-1", data=[1])).result(2)
        assert recovered.snapshot("recovery-1")["status"] == "completed"
    finally:
        recovered.shutdown()


def test_cancel_before_upload_creation_survives_restart(tmp_path):
    store = DocumentTaskStore(tmp_path)
    manager = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    manager.cancel_by_request("request-before-upload")
    manager.shutdown()
    recovered = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    try:
        assert recovered.request_cancelled("request-before-upload")
        recovered.create("cancelled-upload", client_request_id="request-before-upload")
        assert recovered.cancel_by_request("request-before-upload")[0]["status"] == "cancelled"
        with pytest.raises(ValueError):
            recovered.restart("cancelled-upload")
    finally:
        recovered.shutdown()


def test_completed_task_cannot_be_accidentally_restarted_or_revived(tmp_path):
    store = DocumentTaskStore(tmp_path)
    manager = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    manager.create("completed-task", client_request_id="completed-request")
    manager.complete("completed-task", data=[{"content": "accepted"}])
    assert manager.cancel_by_request("completed-request")[0]["status"] == "completed"
    with pytest.raises(ValueError):
        manager.restart("completed-task")
    manager.shutdown()
    recovered = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    assert recovered.snapshot("completed-task")["data"] == [{"content": "accepted"}]
    recovered.remove("completed-task")
    recovered.shutdown()
    final = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    assert final.snapshot("completed-task") is None
    final.shutdown()


def test_bad_checkpoint_does_not_prevent_other_tasks_recovering(tmp_path):
    (tmp_path / "broken-task.json").write_text("{broken")
    store = DocumentTaskStore(tmp_path)
    with pytest.raises(ValueError):
        store.source("../outside", b"no")
    manager = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    manager.create("valid-task")
    manager.fail("valid-task", "retry later")
    manager.shutdown()
    recovered = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    assert recovered.snapshot("valid-task")["status"] == "error"
    recovered.shutdown()


@pytest.mark.parametrize("timestamp", [None, 123, [], {}])
def test_malformed_timestamp_is_ignored_without_blocking_startup(tmp_path, timestamp):
    store = DocumentTaskStore(tmp_path)
    store.save("bad-time", {"task_id": "bad-time", "status": "processing",
                            "created_at": "2026-10-17T00:00:00Z", "updated_at": timestamp})
    manager = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    try:
        assert manager.snapshot("bad-time") is None
        manager.create("new-task")
        assert manager.exists("new-task")
    finally:
        manager.shutdown()


@pytest.mark.parametrize("value", [[], None, 17, "valid JSON, wrong shape"])
def test_wrong_shape_cancel_intents_do_not_break_startup(tmp_path, value):
    (tmp_path / "cancel-intents.json").write_text(json.dumps(value))
    manager = DurableDocumentTasks(store=DocumentTaskStore(tmp_path), max_workers=1, max_queue=0)
    try:
        assert not manager.request_cancelled("unknown-request")
        manager.cancel_by_request("known-request")
        assert manager.request_cancelled("known-request")
    finally:
        manager.shutdown()


def test_disappearing_record_and_cleanup_errors_do_not_break_manager(tmp_path, monkeypatch):
    store = DocumentTaskStore(tmp_path)
    (tmp_path / "disappearing.json").symlink_to(tmp_path / "no-such-target")
    assert list(store.records()) == []
    source = store.source("cleanup-task", b"source")
    manager = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    manager.create("cleanup-task", document_input=source)
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path.name == "cleanup-task.json":
            raise PermissionError("injected cleanup failure")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    try:
        assert manager.remove("cleanup-task") is not None
        assert manager.snapshot("cleanup-task") is None
    finally:
        manager.shutdown()


def test_progress_writes_are_throttled_but_stage_results_persist_immediately(tmp_path, monkeypatch):
    store = DocumentTaskStore(tmp_path)
    manager = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    writes = []
    save = store.save
    monkeypatch.setattr(store, "save", lambda task_id, value: writes.append(deepcopy(value)) or save(task_id, value))
    try:
        manager.create("bounded-progress")
        for index in range(30):
            manager.update("bounded-progress", progress=index, diagnostics={"received": index})
        assert len(writes) == 1
        manager.update("bounded-progress", pdf_resume_state={"page_1": "accepted"})
        assert len(writes) == 2 and writes[-1]["pdf_resume_state"] == {"page_1": "accepted"}
        manager.complete("bounded-progress", data=[{"content": "complete"}])
        assert len(writes) == 3 and writes[-1]["status"] == "completed"
    finally:
        manager.shutdown()
