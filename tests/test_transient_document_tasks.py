"""Transient imports keep runtime behavior without writing recovery artifacts."""
from copy import deepcopy
import datetime as dt
import threading

import pytest

from mathbank.document_task_store import DocumentTaskStore, DurableDocumentTasks
from mathbank.task_manager import TaskCancelled


def manager(store, **kwargs):
    return DurableDocumentTasks(store=store, max_workers=1, max_queue=0,
                                transient_document_types={"pdf"}, **kwargs)


def observe_writes(store, monkeypatch):
    writes = []
    original = store._write

    def write(path, content):
        writes.append(path.name)
        return original(path, content)

    monkeypatch.setattr(store, "_write", write)
    return writes


def test_pdf_progress_results_and_cancel_are_memory_only(tmp_path, monkeypatch):
    store = DocumentTaskStore(tmp_path)
    writes = observe_writes(store, monkeypatch)
    published = []
    tasks = manager(store, on_snapshot=lambda task_id, state: published.append((task_id, state)))
    try:
        tasks.create("pdf-progress", document_type="pdf", client_request_id="pdf-request")
        assert tasks.update("pdf-progress", status="ocr_extraction", progress=42,
                            pdf_resume_state={"pages": {"0": {"markdown": "source"}}})
        assert tasks.add_temp_asset("pdf-progress", "/temporary/page.png")
        tasks._persist("pdf-progress", force=True)
        tasks.cancel_by_request("pdf-request")
        snapshot = tasks.snapshot("pdf-progress")
        assert snapshot["progress"] == 0 and snapshot["status"] == "cancelled"
        assert snapshot["pdf_resume_state"]["pages"]["0"]["markdown"] == "source"
        assert snapshot["temp_assets"] == ["/temporary/page.png"]
        assert tasks.request_cancelled("pdf-request")
        with pytest.raises(TaskCancelled):
            tasks.check_cancelled("pdf-progress")
        assert tasks.update("pdf-progress", status="completed") is False
        tasks.create("pdf-complete", document_type="pdf")
        assert tasks.complete("pdf-complete", data=[{"content": "accepted", "answer_markdown": "answer"}])
        assert tasks.snapshot("pdf-complete")["data"][0]["content"] == "accepted"
        assert tasks.retain("pdf-complete")
        assert tasks.remove("pdf-progress")["status"] == "cancelled"
        assert writes == [] and published == [] and list(tmp_path.iterdir()) == []
    finally:
        tasks.shutdown()


def test_typed_cancel_before_pdf_upload_is_not_persisted(tmp_path, monkeypatch):
    store = DocumentTaskStore(tmp_path)
    writes = observe_writes(store, monkeypatch)
    tasks = manager(store)
    try:
        assert tasks.cancel_by_request("before-pdf", document_type="pdf") == []
        assert tasks.request_cancelled("before-pdf")
        tasks.create("cancelled-before-upload", document_type="pdf", client_request_id="before-pdf")
        assert tasks.cancel_by_request("before-pdf")[0]["status"] == "cancelled"
        assert writes == []
    finally:
        tasks.shutdown()
    recovered = manager(store)
    try:
        assert not recovered.request_cancelled("before-pdf")
        assert recovered.snapshot("cancelled-before-upload") is None
    finally:
        recovered.shutdown()


def test_startup_skips_old_pdf_without_deleting_its_snapshot_or_source(tmp_path, monkeypatch):
    store = DocumentTaskStore(tmp_path)
    old = DurableDocumentTasks(store=store, max_workers=1, max_queue=0)
    source = store.source("old-pdf", b"original PDF source")
    old.create("old-pdf", document_type="pdf", document_input=source)
    old.complete("old-pdf", data=[{"content": "old corrected question"}])
    old.shutdown()
    # Even expired old PDF files belong to the earlier policy, not cleanup here.
    state = dict(next(store.records())[1])
    state["created_at"] = state["updated_at"] = "2020-01-01T00:00:00Z"
    store.save("old-pdf", state)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    writes = observe_writes(store, monkeypatch)
    tasks = manager(store)
    try:
        assert tasks.snapshot("old-pdf") is None
        assert tasks.remove("old-pdf") is None
        # A newly allocated runtime task cannot delete old durable data either.
        tasks.create("old-pdf", document_type="pdf")
        tasks.complete("old-pdf", data=[{"content": "memory only"}])
        with tasks._lock:
            tasks._records["old-pdf"].updated_at = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        assert tasks.cleanup() == 1
        assert writes == []
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    finally:
        tasks.shutdown()


def test_docx_recovery_and_cancellation_remain_durable(tmp_path, monkeypatch):
    store = DocumentTaskStore(tmp_path)
    writes = observe_writes(store, monkeypatch)
    published = []
    tasks = manager(store, on_snapshot=lambda task_id, state: published.append((task_id, deepcopy(state))))
    # Source bytes are deliberately managed by the upload caller, not tasks.
    source = store.source("docx-recover", b"original DOCX")
    try:
        tasks.create("docx-recover", document_type="docx", document_input=source)
        tasks.update("docx-recover", status="extracting", docx_resume_state={"accepted": "source"})
        tasks.cancel_by_request("docx-before-upload", document_type="docx")
        tasks.create("docx-done", document_type="docx")
        tasks.complete("docx-done", data=[{"content": "saved"}])
        assert "docx-recover.json" in writes and "docx-done.json" in writes
        assert "cancel-intents.json" in writes and "docx-recover.source" in writes
        assert any(task_id == "docx-recover" for task_id, _state in published)
    finally:
        tasks.shutdown()
    recovered = manager(store)
    try:
        assert recovered.snapshot("docx-recover")["status"] == "interrupted"
        assert recovered.snapshot("docx-recover")["docx_resume_state"] == {"accepted": "source"}
        assert recovered.snapshot("docx-done")["data"] == [{"content": "saved"}]
        assert recovered.request_cancelled("docx-before-upload")
        assert store.source("docx-recover") == b"original DOCX"
        recovered.remove("docx-done")
        assert not (tmp_path / "docx-done.json").exists()
    finally:
        recovered.shutdown()


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_pdf_worker_lifecycle_and_finalize_do_not_publish(tmp_path, monkeypatch, outcome):
    store = DocumentTaskStore(tmp_path)
    writes = observe_writes(store, monkeypatch)
    tasks = manager(store)
    started, finish, finalized = threading.Event(), threading.Event(), threading.Event()
    original = tasks._finalize_future

    def finalize(*args):
        try:
            original(*args)
        finally:
            finalized.set()

    monkeypatch.setattr(tasks, "_finalize_future", finalize)

    def worker():
        tasks.update("pdf-worker", status="working", progress=63)
        started.set()
        assert finish.wait(2)
        tasks.check_cancelled("pdf-worker")
        if outcome == "error":
            raise RuntimeError("isolated test failure")
        tasks.complete("pdf-worker", data=[{"content": "runtime result"}])

    try:
        tasks.create("pdf-worker", document_type="pdf")
        future = tasks.submit("pdf-worker", worker)
        assert started.wait(2)
        assert tasks.snapshot("pdf-worker")["progress"] == 63
        if outcome == "cancel":
            tasks.cancel("pdf-worker")
        finish.set()
        if outcome == "success":
            future.result(2)
        else:
            with pytest.raises(TaskCancelled if outcome == "cancel" else RuntimeError):
                future.result(2)
        assert finalized.wait(2)
        assert tasks.snapshot("pdf-worker")["status"] == {
            "success": "completed", "error": "error", "cancel": "cancelled"}[outcome]
        assert writes == [] and list(tmp_path.iterdir()) == []
    finally:
        finish.set()
        tasks.shutdown()


def test_transient_policy_is_generic_and_does_not_require_a_store():
    published = []
    tasks = DurableDocumentTasks(max_workers=1, max_queue=0, transient_document_types={"image"},
                                 on_snapshot=lambda task_id, _state: published.append(task_id))
    try:
        tasks.create("image", document_type="image")
        tasks.complete("image", data=[1])
        tasks.create("pdf", document_type="pdf")
        assert published == ["pdf"]
        assert tasks.snapshot("image")["data"] == [1]
    finally:
        tasks.shutdown()
