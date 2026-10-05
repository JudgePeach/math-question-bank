"""Event-driven PDF layout concurrency checks; no real AI or database access."""

from concurrent.futures import ThreadPoolExecutor
import io
import threading
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from PIL import Image

from mathbank import pdf_figures
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("mathbank.pdf_vision_request.PDF_VISION_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **k: pytest.fail("real network forbidden"))


def page_fixture(tmp_path, count=3, *, plain_last=False):
    image = io.BytesIO()
    Image.new("RGB", (100, 100), "black").save(image, "PNG")
    images, infos, sources = [], {}, []
    with fitz.open() as document:
        for index in range(count):
            page = document.new_page(width=400, height=500)
            page.insert_text((30, 40), f"{index + 1}. Refer to the diagram.")
            if not (plain_last and index == count - 1):
                page.insert_image(fitz.Rect(50, 70, 150, 170), stream=image.getvalue())
            page.insert_text((30, 210), "Find the answer.")
            target = tmp_path / f"page{index}.png"
            page.get_pixmap().save(target)
            images.append(str(target))
            infos[index] = pdf_figures.inspect_pdf_page(page, index)
            sources.append(f"{index + 1}. Refer to the diagram.\nFind the answer.")
        data = document.tobytes()
    return data, images, infos, sources


def layout(info):
    return {"page_complete": True, "figures": [{
        "bbox": [125, 140, 375, 340],
        "candidate_ids": [candidate["id"] for candidate in info["candidates"]],
        "anchor_before": "Refer to the diagram.", "anchor_after": "Find the answer.",
        "review_required": False,
    }], "ignored_candidates": []}


def enrich(tmp_path, fixture, **kwargs):
    data, images, _, sources = fixture
    return pdf_figures.enrich_pdf_with_figures(
        data, list(range(len(images))), images,
        [f"/static/uploads/tmp/page{index}.png" for index in range(len(images))],
        sources, set(), output_dir=tmp_path, url_prefix="/static/uploads/tmp", task_id="parallel-test",
        check_cancelled=lambda: None, register_asset=lambda _path: None,
        report_progress=kwargs.pop("report_progress", lambda *_args: None), **kwargs,
    )


def jobs(count):
    return [(index, index, f"page{index}.png", f"source {index}", {"page_index": index})
            for index in range(count)]


def test_requests_overlap_but_pdf_inspection_validation_and_crops_stay_on_caller(tmp_path, monkeypatch):
    fixture = page_fixture(tmp_path)
    caller = threading.get_ident()
    barrier = threading.Barrier(3)
    finished = [threading.Event() for _ in range(3)]
    completion_order = []
    progress = []
    original_inspect, original_crop, original_validate = (
        pdf_figures.inspect_pdf_page, pdf_figures.crop_pdf_figure, pdf_figures._validate_layout)

    def caller_only(function):
        def wrapped(*args, **kwargs):
            assert threading.get_ident() == caller
            return function(*args, **kwargs)
        return wrapped

    def request(_image, _source, info, **kwargs):
        assert threading.get_ident() != caller
        assert not any(isinstance(value, (fitz.Document, fitz.Page)) for value in info.values())
        index = info["page_index"]
        kwargs["diagnostics"]["visual_calls"] += 1
        kwargs["report_attempt"]({"status": "running", "attempt": 1, "page_index": index})
        barrier.wait(timeout=3)
        if index < 2:
            assert finished[index + 1].wait(3)
        completion_order.append(index)
        finished[index].set()
        return layout(info)

    monkeypatch.setattr(pdf_figures, "inspect_pdf_page", caller_only(original_inspect))
    monkeypatch.setattr(pdf_figures, "crop_pdf_figure", caller_only(original_crop))
    monkeypatch.setattr(pdf_figures, "_validate_layout", caller_only(original_validate))
    monkeypatch.setattr(pdf_figures, "request_pdf_layout", request)
    result = enrich(tmp_path, fixture, report_progress=lambda index, _message: progress.append(index))
    assert completion_order == [2, 1, 0]
    assert [page["page_index"] for page in result["pages"]] == [0, 1, 2]
    assert [page["figures"][0]["id"] for page in result["pages"]] == ["p1-f1", "p2-f1", "p3-f1"]
    for index, source in enumerate(result["page_texts"]):
        assert f"pdf_figure_parallel-test_{index}_p{index + 1}-f1_" in source
        assert source.startswith(f"{index + 1}. ")
    assert progress == sorted(progress)
    assert result["diagnostics"]["visual_calls"] == 3


def test_precomputed_and_plain_pages_make_no_new_request_and_inspection_is_reused(tmp_path, monkeypatch):
    fixture = page_fixture(tmp_path, plain_last=True)
    _, _, infos, _ = fixture
    calls = []
    monkeypatch.setattr(pdf_figures, "inspect_pdf_page", lambda *a: pytest.fail("duplicate page inspection"))

    def request(_image, _source, info, **kwargs):
        calls.append(info["page_index"])
        kwargs["diagnostics"]["visual_calls"] += 1
        return layout(info)

    monkeypatch.setattr(pdf_figures, "request_pdf_layout", request)
    result = enrich(tmp_path, fixture, precomputed_layouts={0: layout(infos[0])}, precomputed_page_infos=infos)
    assert calls == [1]
    assert result["diagnostics"]["visual_calls"] == 1
    assert result["diagnostics"]["joint_visual_calls"] == 1
    assert result["diagnostics"]["skipped_pages"] == 1
    assert result["diagnostics"]["figures_attached"] == 2
    assert all("figure_slots" not in info for info in infos.values())


def test_all_precomputed_pages_need_no_http_permit(tmp_path, monkeypatch):
    fixture = page_fixture(tmp_path)
    infos = fixture[2]
    gate = threading.BoundedSemaphore(1)
    gate.acquire()
    monkeypatch.setattr(pdf_figures, "request_pdf_layout", lambda *a, **k: pytest.fail("duplicate request"))
    try:
        result = enrich(tmp_path, fixture, precomputed_layouts={index: layout(info) for index, info in infos.items()},
                        precomputed_page_infos=infos, vision_semaphore=gate)
        assert result["diagnostics"]["figures_attached"] == 3
    finally:
        gate.release()


def test_one_page_failure_preserves_other_pages_and_original_failed_page_text(tmp_path, monkeypatch):
    fixture = page_fixture(tmp_path)
    calls = []

    def request(_image, _source, info, **kwargs):
        calls.append(info["page_index"])
        kwargs["diagnostics"]["visual_calls"] += 1
        if info["page_index"] == 1:
            raise ValueError("图位无效")
        return layout(info)

    monkeypatch.setattr(pdf_figures, "request_pdf_layout", request)
    result = enrich(tmp_path, fixture)
    assert sorted(calls) == [0, 1, 2]
    assert [page["status"] for page in result["pages"]] == ["checked", "review", "checked"]
    assert result["page_texts"][1] == fixture[3][1]
    assert "page1.png" in result["issues"][0]["source_excerpt"]
    assert result["diagnostics"]["figures_attached"] == 2
    assert result["diagnostics"]["visual_calls"] == 3


def test_process_wide_semaphore_bounds_two_layout_tasks_together(monkeypatch):
    gate = threading.BoundedSemaphore(4)
    reached_four, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = maximum = calls = 0

    def request(*args, **kwargs):
        nonlocal active, maximum, calls
        with lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
            if active == 4:
                reached_four.set()
        assert release.wait(3)
        with lock:
            active -= 1
        kwargs["diagnostics"]["visual_calls"] += 1
        return {"figures": []}

    monkeypatch.setattr(pdf_figures, "request_pdf_layout", request)
    with ThreadPoolExecutor(max_workers=2) as callers:
        pending = [callers.submit(pdf_figures._request_independent_layouts, jobs(6),
                   check_cancelled=lambda: None, report_attempt=lambda _event: None,
                   report_progress=lambda *_args: None, vision_semaphore=gate) for _ in range(2)]
        try:
            assert reached_four.wait(3)
            assert maximum == 4
        finally:
            release.set()
        results = [future.result(timeout=3) for future in pending]
    assert maximum == 4 and calls == 12 and active == 0
    assert all(len(result[0]) == 6 and result[1] == {} for result in results)
    assert all(gate.acquire(blocking=False) for _ in range(4))
    for _ in range(4):
        gate.release()


@pytest.mark.parametrize("reason", ["取消", "任务已移除"])
def test_cancellation_while_waiting_for_http_permit_starts_no_request(monkeypatch, reason):
    gate = threading.BoundedSemaphore(1)
    gate.acquire()
    waiting, cancelled = threading.Event(), threading.Event()
    calls = []

    def check():
        if cancelled.is_set():
            raise TaskCancelled(reason)
        if threading.current_thread().name.startswith("mathbank-pdf-layout"):
            waiting.set()

    monkeypatch.setattr(pdf_figures, "request_pdf_layout", lambda *a, **k: calls.append(a))
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(pdf_figures._request_independent_layouts, jobs(8), check_cancelled=check,
                               report_attempt=lambda _event: None, report_progress=lambda *_args: None,
                               vision_semaphore=gate)
        assert waiting.wait(3)
        cancelled.set()
        with pytest.raises(TaskCancelled):
            future.result(timeout=3)
    gate.release()
    assert calls == []


def test_parallel_attempt_usage_and_retry_counts_remain_per_page(monkeypatch, tmp_path):
    from mathbank.ai_providers import resolve_ocr_provider
    import requests
    provider = resolve_ocr_provider("siliconflow", {"SILICONFLOW_API_KEY": "unused-test-key"})
    monkeypatch.setattr(pdf_figures, "resolve_ocr_provider", lambda _engine: provider)
    monkeypatch.setattr(pdf_figures, "build_pdf_layout_prompt", lambda _source, info: str(info["page_index"]))
    calls = {}
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def post(_provider, payload, **kwargs):
        index = int(payload["messages"][0]["content"][0]["text"])
        with lock:
            number = calls[index] = calls.get(index, 0) + 1
        assert kwargs["timeout"] == 600
        assert kwargs["retry_connection"] is False and kwargs["allow_redirects"] is False
        if number == 1:
            barrier.wait(timeout=3)
            raise requests.ReadTimeout("private")
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"figures":[],"page_complete":true}'}}],
            "usage": {"prompt_tokens": index + 10, "completion_tokens": 1, "total_tokens": index + 11},
        })

    monkeypatch.setattr(pdf_figures, "post_chat_completion", post)
    fixture = page_fixture(tmp_path, count=4)
    result = enrich(tmp_path, fixture)
    assert calls == {0: 2, 1: 2, 2: 2, 3: 2}
    report = result["diagnostics"]
    assert report["visual_calls"] == len(report["attempts"]) == 8
    assert report["usage"] == {"prompt_tokens": 46, "completion_tokens": 4, "total_tokens": 50}
    assert [(event["page_index"], event["attempt"], event["status"]) for event in report["attempts"]] == [
        (index, attempt, "failed" if attempt == 1 else "succeeded") for index in range(4) for attempt in (1, 2)]


def test_external_cancellation_returns_before_inflight_http_finishes_and_stops_queued_calls(monkeypatch):
    entered, release, cancelled, worker_done = (threading.Event() for _ in range(4))
    gate = threading.BoundedSemaphore(1)
    calls = []

    def check():
        if cancelled.is_set():
            raise TaskCancelled("cancelled")

    def request(*args, **kwargs):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        worker_done.set()
        # The worker's next cancellation check rejects this late result.
        return {"figures": []}

    monkeypatch.setattr(pdf_figures, "request_pdf_layout", request)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(pdf_figures._request_independent_layouts, jobs(8), check_cancelled=check,
                               report_attempt=lambda _event: None, report_progress=lambda *_args: None,
                               vision_semaphore=gate)
        try:
            assert entered.wait(3)
            cancelled.set()
            with pytest.raises(TaskCancelled):
                future.result(timeout=2)
            assert not worker_done.is_set()
        finally:
            release.set()
    assert worker_done.wait(3)
    assert len(calls) == 1


def test_worker_records_only_actual_semaphore_wait_without_inventing_first_token_timing(monkeypatch):
    clock = [20.0]
    events = []
    monkeypatch.setattr(pdf_figures.time, "monotonic", lambda: clock[0])

    class Gate:
        def acquire(self, *, timeout):
            assert timeout == 0.25
            clock[0] += 2.375
            return True

        def release(self):
            pass

    def request(*args, **kwargs):
        kwargs["report_attempt"]({"status": "running", "attempt": 1})
        kwargs["report_attempt"]({"status": "succeeded", "attempt": 1})
        return {"figures": []}

    monkeypatch.setattr(pdf_figures, "request_pdf_layout", request)
    pdf_figures._request_independent_layouts(jobs(1), check_cancelled=lambda: None,
        report_attempt=events.append, report_progress=lambda *_args: None, vision_semaphore=Gate())
    assert all(event["semaphore_wait_seconds"] == 2.375 for event in events)
    assert all("ttft_seconds" not in event for event in events)
    events.clear()
    pdf_figures._request_independent_layouts(jobs(1), check_cancelled=lambda: None,
        report_attempt=events.append, report_progress=lambda *_args: None)
    assert all("semaphore_wait_seconds" not in event for event in events)
