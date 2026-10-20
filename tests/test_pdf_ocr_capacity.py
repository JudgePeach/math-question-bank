"""Independent, network-free checks for process-wide OCR capacity."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from mathbank.pdf_ocr_capacity import (
    DEFAULT_PDF_OCR_CONCURRENCY,
    MAX_PDF_OCR_CONCURRENCY,
    PDF_OCR_PAGE_INPUT_BUDGET,
    PdfOcrCapacity,
    configured_pdf_ocr_concurrency,
)
from mathbank.task_manager import TaskCancelled


@pytest.mark.parametrize("value, expected", [
    (None, 4), ("", 4), (" 12 ", 12), ("1", 1), (4, 4), (6, 6), (8, 8), (12, 12),
    ("9", 9), ("8.0", 4), ("-1", 4), ("0", 4), (13, 4), (True, 4),
    (False, 4), (1.0, 4), ("１２", 4), ("nan", 4), ("1" * 9000, 4),
])
def test_concurrency_config_has_safe_bounded_default(value, expected):
    assert configured_pdf_ocr_concurrency(value) == expected


def test_concurrency_config_reads_environment(monkeypatch):
    monkeypatch.delenv("PDF_OCR_CONCURRENCY", raising=False)
    assert configured_pdf_ocr_concurrency() == DEFAULT_PDF_OCR_CONCURRENCY
    monkeypatch.setenv("PDF_OCR_CONCURRENCY", "12")
    assert configured_pdf_ocr_concurrency() == MAX_PDF_OCR_CONCURRENCY
    monkeypatch.setenv("PDF_OCR_CONCURRENCY", "not an integer")
    assert configured_pdf_ocr_concurrency() == DEFAULT_PDF_OCR_CONCURRENCY


@pytest.mark.parametrize("value", [0, 13, True, 1.0, "8"])
def test_invalid_capacity_is_rejected(value):
    with pytest.raises(ValueError, match="1 至 12"):
        PdfOcrCapacity(value)


@pytest.mark.parametrize("value", [0, -1, True, 1.0])
def test_invalid_byte_budget_is_rejected(value):
    with pytest.raises(ValueError, match="正整数"):
        PdfOcrCapacity(page_input_budget=value)


def test_twelve_real_page_slots_can_be_active_together():
    capacity = PdfOcrCapacity(12)
    all_started = threading.Barrier(13)
    release = threading.Event()

    def request(page):
        with capacity.page_slot(1024, check_cancelled=lambda: None):
            all_started.wait(timeout=3)
            assert release.wait(3)
            return page

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(request, page) for page in range(12)]
        try:
            all_started.wait(timeout=3)
            report = capacity.snapshot()
            assert report["active"] == report["page_active"] == 12
            assert report["page_input_bytes"] == 12 * 1024
        finally:
            release.set()
        assert [future.result(timeout=3) for future in futures] == list(range(12))
    assert capacity.snapshot()["active"] == capacity.snapshot()["page_input_bytes"] == 0


def test_two_document_tasks_share_one_total_capacity():
    capacity = PdfOcrCapacity(8)
    eight_started = threading.Event()
    release = threading.Event()
    active_lock = threading.Lock()
    started = []

    def request(task, page):
        with capacity.page_slot(2048, check_cancelled=lambda: None):
            with active_lock:
                started.append((task, page))
                if len(started) == 8:
                    eight_started.set()
            assert release.wait(3)
            return task, page

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(request, task, page)
                   for task in ("first", "second") for page in range(8)]
        try:
            assert eight_started.wait(3)
            assert capacity.snapshot()["active"] == 8
            assert len(started) == 8
        finally:
            release.set()
        results = [future.result(timeout=3) for future in futures]
    assert len(results) == 16
    assert capacity.snapshot()["peak_active"] == 8
    assert capacity.snapshot()["active"] == 0


def test_auxiliary_stays_four_and_counts_against_total():
    capacity = PdfOcrCapacity(8)
    for _ in range(4):
        assert capacity.auxiliary.acquire(blocking=False)
    assert not capacity.auxiliary.acquire(blocking=False)
    assert not capacity.auxiliary.acquire(timeout=0)
    with capacity.page_slot(1, check_cancelled=lambda: None):
        assert capacity.snapshot()["active"] == 5
        assert capacity.snapshot()["auxiliary_active"] == 4
    for _ in range(4):
        capacity.auxiliary.release()
    with pytest.raises(ValueError, match="释放次数过多"):
        capacity.auxiliary.release()
    with pytest.raises(ValueError, match="non-blocking"):
        capacity.auxiliary.acquire(blocking=False, timeout=0)


def test_page_slots_cannot_bypass_existing_auxiliary_users():
    capacity = PdfOcrCapacity(2)
    assert capacity.auxiliary_limit == 2
    with capacity.auxiliary:
        with capacity.page_slot(1, check_cancelled=lambda: None):
            assert not capacity.auxiliary.acquire(timeout=0)
            assert capacity.snapshot()["active"] == 2
    assert capacity.snapshot()["active"] == 0


def test_page_waiter_uses_released_auxiliary_slot_without_exceeding_total():
    capacity = PdfOcrCapacity(2)
    checked, entered = threading.Event(), threading.Event()
    for _ in range(2):
        assert capacity.auxiliary.acquire()

    def request():
        with capacity.page_slot(10, check_cancelled=checked.set):
            assert capacity.snapshot()["active"] == 2
            entered.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request)
            assert checked.wait(2)
            assert not entered.wait(0.05)
            capacity.auxiliary.release()
            future.result(timeout=2)
    finally:
        capacity.auxiliary.release()
    assert entered.is_set() and capacity.snapshot()["peak_active"] == 2
    assert capacity.snapshot()["active"] == 0


def test_auxiliary_timeout_does_not_reserve_total_capacity():
    capacity = PdfOcrCapacity(1)
    with capacity.page_slot(1, check_cancelled=lambda: None):
        assert not capacity.auxiliary.acquire(timeout=0.01)
        assert capacity.snapshot()["auxiliary_waiting"] == 0
        assert capacity.snapshot()["auxiliary_active"] == 0
    assert capacity.auxiliary.acquire(timeout=0.01)
    capacity.auxiliary.release()


def test_png_input_budget_waits_without_acquiring_or_reading_more_inputs():
    capacity = PdfOcrCapacity(12, page_input_budget=10)
    waiting_checked, entered = threading.Event(), threading.Event()

    def request():
        with capacity.page_slot(5, check_cancelled=waiting_checked.set):
            entered.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with capacity.page_slot(6, check_cancelled=lambda: None):
            future = executor.submit(request)
            assert waiting_checked.wait(2)
            assert not entered.wait(0.05)
            assert capacity.snapshot()["page_input_bytes"] == 6
            with capacity.page_slot(4, check_cancelled=lambda: None):
                assert capacity.snapshot()["page_input_bytes"] == 10
                assert not entered.is_set()
        future.result(timeout=2)
    report = capacity.snapshot()
    assert entered.is_set() and report["peak_page_input_bytes"] == 10
    assert report["active"] == report["page_input_bytes"] == 0


def test_cancellation_while_waiting_for_capacity_does_not_leak_permits():
    capacity = PdfOcrCapacity(1)
    checked, cancelled = threading.Event(), threading.Event()

    def check():
        checked.set()
        if cancelled.is_set():
            raise TaskCancelled("cancelled while waiting")

    def request():
        with capacity.page_slot(1, check_cancelled=check):
            pytest.fail("cancelled worker acquired occupied slot")

    with capacity.auxiliary:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request)
            assert checked.wait(2)
            cancelled.set()
            with pytest.raises(TaskCancelled, match="waiting"):
                future.result(timeout=1)
        report = capacity.snapshot()
        assert report["active"] == 1 and report["page_waiting"] == 0
        assert report["page_input_bytes"] == 0
    assert capacity.snapshot()["active"] == 0


def test_cancellation_immediately_after_acquire_releases_both_resources():
    capacity = PdfOcrCapacity(1)
    checks = 0
    def check():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise TaskCancelled("cancelled immediately after acquire")
    with pytest.raises(TaskCancelled):
        with capacity.page_slot(10, check_cancelled=check):
            pytest.fail("cancelled slot yielded")
    assert capacity.snapshot()["active"] == capacity.snapshot()["page_input_bytes"] == 0
    assert capacity.auxiliary.acquire(blocking=False)
    capacity.auxiliary.release()


@pytest.mark.parametrize("lane", ["page", "auxiliary"])
def test_request_exception_releases_capacity(lane):
    capacity = PdfOcrCapacity(1)
    with pytest.raises(RuntimeError, match="request failed"):
        context = (capacity.page_slot(10, check_cancelled=lambda: None)
                   if lane == "page" else capacity.auxiliary)
        with context:
            raise RuntimeError("request failed")
    assert capacity.snapshot()["active"] == capacity.snapshot()["page_input_bytes"] == 0
    with capacity.page_slot(10, check_cancelled=lambda: None):
        assert capacity.snapshot()["active"] == 1


@pytest.mark.parametrize("input_bytes", [-1, True, 1.5, PDF_OCR_PAGE_INPUT_BUDGET + 1])
def test_bad_input_size_never_enters_request(input_bytes):
    capacity = PdfOcrCapacity()
    with pytest.raises(ValueError, match="输入文件大小"):
        with capacity.page_slot(input_bytes, check_cancelled=lambda: pytest.fail("checked bad input")):
            pytest.fail("entered bad input")
    assert capacity.snapshot()["active"] == 0
