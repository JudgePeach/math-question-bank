"""Caller-thread PDF preparation and bounded HTTP overlap; no model or DB."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from mathbank.pdf_request_pipeline import iter_pdf_page_requests
from mathbank.pdf_ocr_capacity import DEFAULT_PDF_OCR_CONCURRENCY
from mathbank.task_manager import TaskCancelled


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request",
                        lambda *a, **k: pytest.fail("real network forbidden"))


def test_first_request_starts_before_remaining_pages_are_prepared_on_caller():
    caller = threading.get_ident()
    first_started = threading.Event()
    prepared = []
    producer_closed = []

    def jobs():
        try:
            for page in range(9):
                assert threading.get_ident() == caller
                if page == 1:
                    assert first_started.wait(2), "preparation retained the whole-document barrier"
                prepared.append(page)
                yield {"page": page, "path": f"detached-page-{page}.png"}
        finally:
            producer_closed.append(threading.get_ident())

    def request(job, check):
        assert threading.get_ident() != caller
        check()
        if job["page"] == 0:
            first_started.set()
        return job["page"]

    diagnostics = {}
    results = [future.result() for future in iter_pdf_page_requests(
        jobs(), request, check_cancelled=lambda: None, diagnostics=diagnostics)]
    assert sorted(results) == prepared == list(range(9))
    assert producer_closed == [caller]
    assert diagnostics["status"] == "completed"
    assert diagnostics["submitted"] == diagnostics["completed"] == 9
    assert 1 <= diagnostics["peak_in_flight"] <= DEFAULT_PDF_OCR_CONCURRENCY


def test_no_fifth_page_is_prepared_while_four_requests_are_blocked():
    prepared, active, peak = [], 0, 0
    lock = threading.Lock()
    four_running, release = threading.Event(), threading.Event()

    def jobs():
        for page in range(20):
            prepared.append(page)
            yield page

    def request(page, check):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 4:
                four_running.set()
        try:
            assert release.wait(2)
            check()
            return page
        finally:
            with lock:
                active -= 1

    def collect():
        return [future.result() for future in iter_pdf_page_requests(
            jobs(), request, check_cancelled=lambda: None, max_workers=4)]

    with ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(collect)
        try:
            assert four_running.wait(2)
            assert prepared == [0, 1, 2, 3]
        finally:
            release.set()
        assert sorted(result.result(timeout=3)) == list(range(20))
    assert peak <= 4 and active == 0


def test_cancellation_closes_producer_promptly_and_discards_late_results():
    running, release, cancelled, worker_done = (threading.Event() for _ in range(4))
    prepared, closed = [], []

    def check():
        if cancelled.is_set():
            raise TaskCancelled("test cancelled")

    def jobs():
        try:
            for page in range(20):
                prepared.append(page)
                yield page
        finally:
            closed.append(threading.get_ident())

    def request(page, check):
        running.set()
        try:
            assert release.wait(3)
            return page
        finally:
            worker_done.set()

    diagnostics, observed = {}, []
    def collect():
        caller = threading.get_ident()
        try:
            for future in iter_pdf_page_requests(jobs(), request, check_cancelled=check,
                                                max_workers=1, diagnostics=diagnostics):
                observed.append(future.result())
        finally:
            assert closed == [caller]

    with ThreadPoolExecutor(max_workers=1) as caller:
        result = caller.submit(collect)
        assert running.wait(2)
        cancelled.set()
        try:
            with pytest.raises(TaskCancelled):
                result.result(timeout=1)
            assert observed == [] and prepared == [0]
        finally:
            release.set()
            assert worker_done.wait(2)
    assert diagnostics["status"] == "cancelled"


def test_producer_failure_closes_its_pdf_context_without_waiting_for_http():
    running, release = threading.Event(), threading.Event()
    closed = []

    def jobs():
        try:
            yield 1
            assert running.wait(2)
            raise ValueError("local render failed")
        finally:
            closed.append(threading.get_ident())

    def request(job, check):
        running.set()
        assert release.wait(3)
        return job

    caller = threading.get_ident()
    diagnostics = {}
    try:
        with pytest.raises(ValueError, match="local render failed"):
            list(iter_pdf_page_requests(jobs(), request, check_cancelled=lambda: None,
                                       diagnostics=diagnostics))
        assert closed == [caller]
        assert diagnostics["status"] == "failed"
    finally:
        release.set()


def test_early_close_stops_production_and_request_exception_is_observed():
    closed, requested = [], []
    caller = threading.get_ident()

    def jobs():
        try:
            for page in range(9):
                yield page
        finally:
            closed.append(threading.get_ident())

    def request(job, check):
        requested.append(job)
        raise ValueError("request failed")

    report = {}
    pipeline = iter_pdf_page_requests(jobs(), request, check_cancelled=lambda: None,
                                      max_workers=1, diagnostics=report)
    try:
        future = next(pipeline)
        with pytest.raises(ValueError, match="request failed"):
            future.result()
    finally:
        pipeline.close()
    assert requested == [0] and closed == [caller]
    assert report["status"] == "closed" and report["submitted"] == 1


def test_empty_producer_has_no_request():
    report = {}
    assert list(iter_pdf_page_requests([], lambda *a: pytest.fail("empty request"),
                                       check_cancelled=lambda: None, diagnostics=report)) == []
    assert report["status"] == "completed" and report["submitted"] == 0


@pytest.mark.parametrize("workers", [0, 13, True, 1.5])
def test_invalid_concurrency_never_advances_producer(workers):
    def jobs():
        pytest.fail("producer advanced")
        yield None

    with pytest.raises(ValueError, match="1 至 12"):
        list(iter_pdf_page_requests(jobs(), lambda *a: None,
                                   check_cancelled=lambda: None, max_workers=workers))


@pytest.mark.parametrize("page_count", [9, 12])
def test_twelve_workers_can_start_all_nine_or_twelve_pages_in_one_wave(page_count):
    all_started = threading.Barrier(page_count + 1)
    release = threading.Event()
    prepared = []

    def jobs():
        for page in range(page_count):
            prepared.append(page)
            yield page

    def request(page, check):
        all_started.wait(timeout=3)
        assert release.wait(3)
        check()
        return page

    diagnostics = {}
    def collect():
        return [future.result() for future in iter_pdf_page_requests(
            jobs(), request, check_cancelled=lambda: None, max_workers=12,
            diagnostics=diagnostics)]

    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(collect)
        try:
            all_started.wait(timeout=3)
            assert prepared == list(range(page_count))
        finally:
            release.set()
        assert sorted(future.result(timeout=3)) == list(range(page_count))
    assert diagnostics["peak_in_flight"] == page_count
    assert diagnostics["status"] == "completed"
