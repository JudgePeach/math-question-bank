"""Runtime sources expire with their owning task, even without page images."""
from concurrent.futures import Future
import threading

import pytest

from mathbank.document_task_store import DurableDocumentTasks
from mathbank.task_manager import TaskCancelled, TaskManager, iter_completed_futures


@pytest.mark.parametrize("removal", ["ttl", "capacity", "explicit"])
def test_empty_asset_task_releases_source_on_every_removal_path(removal):
    sources, cleaned_files = {}, []
    manager = DurableDocumentTasks(
        transient_document_types={"pdf"}, max_workers=1, max_queue=0,
        max_records=1, terminal_ttl_seconds=0 if removal == "ttl" else 3600,
        temp_asset_cleanup=cleaned_files.append,
        record_cleanup=lambda task_id: sources.pop(task_id, None),
    )
    try:
        for index in range(3):
            task_id = f"failed-{index}"
            sources[task_id] = b"%PDF-invalid" + b"x" * (1024 * 1024)
            manager.create(task_id, document_type="pdf", temp_assets=[])
            manager.fail(task_id, "Invalid PDF before rendering")
            if removal == "ttl":
                assert manager.cleanup() == 1
            elif removal == "explicit":
                assert manager.remove(task_id)["status"] == "error"
            else:
                # Force capacity eviction without waiting for the one-hour TTL.
                manager.create("replacement", document_type="pdf", temp_assets=[])
                manager.remove("replacement")
            assert task_id not in sources
            assert not manager.exists(task_id)
        assert not sources
        assert not cleaned_files
    finally:
        manager.shutdown()


def test_actual_document_manager_callback_releases_registered_source(monkeypatch):
    import main

    monkeypatch.setattr(main, "DOCUMENT_TASK_INPUTS", {})
    monkeypatch.setattr(main, "DOCUMENT_TASK_STORE", None)
    manager = DurableDocumentTasks(max_workers=1, max_queue=0, terminal_ttl_seconds=0,
        transient_document_types={"pdf"}, record_cleanup=main.DOCUMENT_TASKS._record_cleanup)
    try:
        raw = b"%PDF-malformed"
        source = main._register_document_input("invalid-pdf", raw, "pdf", "invalid.pdf")
        manager.create("invalid-pdf", document_type="pdf", temp_assets=[], document_input=source)
        manager.fail("invalid-pdf", "No pages")
        assert main.DOCUMENT_TASK_INPUTS["invalid-pdf"] == raw
        assert manager.cleanup() == 1
        assert not main.DOCUMENT_TASK_INPUTS
    finally:
        manager.shutdown()


def test_cleanup_failure_cannot_prevent_eviction_or_reuse():
    def failing_cleanup(_task_id):
        raise OSError("isolated cleanup failure")

    manager = TaskManager(max_workers=1, max_queue=0, max_records=1,
                          record_cleanup=failing_cleanup)
    try:
        manager.create("old")
        manager.fail("old", "error")
        manager.create("new")
        assert manager.exists("new") and not manager.exists("old")
        assert manager.remove("new") is not None
    finally:
        manager.shutdown()


def test_cancel_observer_finishes_before_blocked_response_and_releases_import_slot():
    manager = TaskManager(max_workers=1, max_queue=0)
    entered, finalized, retired = threading.Event(), threading.Event(), threading.Event()
    response = Future()  # HTTP has not produced a response yet.

    def collect():
        entered.set()
        try:
            list(iter_completed_futures([response], lambda: manager.check_cancelled("cancel")))
        finally:
            finalized.set()

    try:
        manager.create("cancel")
        worker = manager.submit("cancel", collect)
        worker.add_done_callback(lambda _future: retired.set())
        assert entered.wait(1)
        manager.cancel("cancel")
        assert finalized.wait(1)
        with pytest.raises(TaskCancelled):
            worker.result(1)
        assert retired.wait(1)
        assert not response.done()  # No fake claim that the HTTP call was aborted.
        manager.create("next")
        assert manager.submit("next", lambda: "ready").result(1) == "ready"
    finally:
        response.cancel()
        manager.shutdown()


def test_completed_future_results_and_exceptions_are_preserved():
    good, bad = Future(), Future()
    good.set_result("complete response")
    bad.set_exception(ValueError("provider failure"))
    futures = set(iter_completed_futures([good, bad], lambda: None))
    assert futures == {good, bad}
    assert good.result() == "complete response"
    with pytest.raises(ValueError, match="provider failure"):
        bad.result()


def test_removed_pdf_collector_does_not_wait_for_inflight_ocr(monkeypatch, tmp_path):
    import main
    import pymupdf

    manager = TaskManager(max_workers=1, max_queue=0)
    monkeypatch.setattr(main, "DOCUMENT_TASKS", manager)
    monkeypatch.setattr(main, "TMP_UPLOAD_DIR", str(tmp_path))
    with pymupdf.open() as document:
        document.new_page(width=120, height=120)
        raw = document.tobytes()
    entered, release = threading.Event(), threading.Event()

    def blocked_ocr(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return "1. Late response"

    monkeypatch.setattr(main, "ocr_pdf_page_image", blocked_ocr)
    manager.create("removed-pdf", document_type="pdf", temp_assets=[])
    collector = threading.Thread(target=main.run_pdf_parsing_task,
        args=("removed-pdf", raw, "removed.pdf"), kwargs={"pdf_strategy": "force_ocr"})
    collector.start()
    try:
        assert entered.wait(2)
        manager.remove("removed-pdf")
        collector.join(1)
        assert not collector.is_alive()
        assert not manager.exists("removed-pdf")
    finally:
        release.set()
        collector.join(5)
        manager.shutdown()
