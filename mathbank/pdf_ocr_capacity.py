"""Process-wide limits for page OCR and the existing auxiliary vision lane.

The byte budget counts page input PNG files (or a conservative regional crop
weight) while their requests are active.
It does not describe total Python/Pillow/base64 memory use, and never replaces
the individual request's existing input validation.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time
from typing import Callable, Iterator


DEFAULT_PDF_OCR_CONCURRENCY = 4
MAX_PDF_OCR_CONCURRENCY = 12
MAX_AUXILIARY_VISION_CONCURRENCY = 4
PDF_OCR_PAGE_INPUT_BUDGET = 96 * 1024 * 1024
_UNSET = object()


def configured_pdf_ocr_concurrency(value: object = _UNSET) -> int:
    """Read PDF_OCR_CONCURRENCY, falling back safely for invalid values."""
    if value is _UNSET:
        value = os.environ.get("PDF_OCR_CONCURRENCY")
    if type(value) is str:
        stripped = value.strip()
        if not stripped.isascii() or not stripped.isdecimal():
            return DEFAULT_PDF_OCR_CONCURRENCY
        # Refuse huge numeric strings without attempting an unbounded int().
        if len(stripped) > 2:
            return DEFAULT_PDF_OCR_CONCURRENCY
        value = int(stripped)
    if type(value) is int and 1 <= value <= MAX_PDF_OCR_CONCURRENCY:
        return value
    return DEFAULT_PDF_OCR_CONCURRENCY


class _AuxiliaryVisionLane:
    """Semaphore-compatible facade over the shared capacity object."""

    def __init__(self, capacity: "PdfOcrCapacity"):
        self._capacity = capacity

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        return self._capacity._acquire("auxiliary", 0, blocking=blocking, timeout=timeout)

    def release(self) -> None:
        self._capacity._release("auxiliary", 0)

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


class PdfOcrCapacity:
    """Share a total request cap without widening other OCR/verification flows.

    Create one instance per running process. All PDF page tasks must use its
    page_slot and all existing auxiliary callers must share its auxiliary lane.
    Waiting requests keep no image bytes/base64 payloads in this module.
    """

    def __init__(
        self,
        total: int = DEFAULT_PDF_OCR_CONCURRENCY,
        *,
        page_input_budget: int = PDF_OCR_PAGE_INPUT_BUDGET,
    ):
        if type(total) is not int or not 1 <= total <= MAX_PDF_OCR_CONCURRENCY:
            raise ValueError("PDF OCR 总并发仅支持 1 至 12。")
        if type(page_input_budget) is not int or page_input_budget <= 0:
            raise ValueError("PDF OCR 并发输入文件预算必须为正整数。")
        self.total = total
        self.auxiliary_limit = min(MAX_AUXILIARY_VISION_CONCURRENCY, total)
        self.page_input_budget = page_input_budget
        self._condition = threading.Condition()
        self._page_active = 0
        self._auxiliary_active = 0
        self._page_input_bytes = 0
        self._peak_active = 0
        self._peak_page_active = 0
        self._peak_auxiliary_active = 0
        self._peak_page_input_bytes = 0
        self._page_waiting = 0
        self._auxiliary_waiting = 0
        self.auxiliary = _AuxiliaryVisionLane(self)

    def _available(self, lane: str, input_bytes: int) -> bool:
        if self._page_active + self._auxiliary_active >= self.total:
            return False
        if lane == "auxiliary":
            return self._auxiliary_active < self.auxiliary_limit
        return self._page_input_bytes + input_bytes <= self.page_input_budget

    def _acquire(self, lane: str, input_bytes: int, *, blocking: bool = True,
                 timeout: float | None = None) -> bool:
        if not blocking and timeout is not None:
            raise ValueError("can't specify timeout for non-blocking acquire")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            waiting_attr = "_page_waiting" if lane == "page" else "_auxiliary_waiting"
            setattr(self, waiting_attr, getattr(self, waiting_attr) + 1)
            try:
                while not self._available(lane, input_bytes):
                    if not blocking:
                        return False
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        return False
                    self._condition.wait(remaining)
                if lane == "page":
                    self._page_active += 1
                    self._page_input_bytes += input_bytes
                else:
                    self._auxiliary_active += 1
                self._peak_active = max(self._peak_active, self._page_active + self._auxiliary_active)
                self._peak_page_active = max(self._peak_page_active, self._page_active)
                self._peak_auxiliary_active = max(self._peak_auxiliary_active, self._auxiliary_active)
                self._peak_page_input_bytes = max(self._peak_page_input_bytes, self._page_input_bytes)
                return True
            finally:
                setattr(self, waiting_attr, getattr(self, waiting_attr) - 1)

    def _release(self, lane: str, input_bytes: int) -> None:
        with self._condition:
            if lane == "page":
                if self._page_active <= 0 or input_bytes > self._page_input_bytes:
                    raise ValueError("PDF OCR 页面名额释放次数过多。")
                self._page_active -= 1
                self._page_input_bytes -= input_bytes
            else:
                if self._auxiliary_active <= 0:
                    raise ValueError("辅助识图名额释放次数过多。")
                self._auxiliary_active -= 1
            self._condition.notify_all()

    @contextmanager
    def page_slot(self, input_bytes: int, *, check_cancelled: Callable[[], None]) -> Iterator[None]:
        """Reserve capacity before loading/encoding PNGs; cancel every 250 ms."""
        if type(input_bytes) is not int or not 0 <= input_bytes <= self.page_input_budget:
            raise ValueError("PDF OCR 页面输入文件大小无效或超过并发预算。")
        if not callable(check_cancelled):
            raise ValueError("PDF OCR 取消检查必须为可调用对象。")
        acquired = False
        try:
            while not acquired:
                check_cancelled()
                acquired = self._acquire("page", input_bytes, timeout=0.25)
            check_cancelled()
            yield
        finally:
            if acquired:
                self._release("page", input_bytes)

    def snapshot(self) -> dict:
        """Return process-wide capacity facts, without model or source data."""
        with self._condition:
            return {
                "scope": "process_lifetime",
                "total_limit": self.total,
                "auxiliary_limit": self.auxiliary_limit,
                "page_input_budget_bytes": self.page_input_budget,
                "page_input_budget_kind": "png_files_or_conservative_crop_weight",
                "active": self._page_active + self._auxiliary_active,
                "page_active": self._page_active,
                "auxiliary_active": self._auxiliary_active,
                "page_input_bytes": self._page_input_bytes,
                "page_waiting": self._page_waiting,
                "auxiliary_waiting": self._auxiliary_waiting,
                "peak_active": self._peak_active,
                "peak_page_active": self._peak_page_active,
                "peak_auxiliary_active": self._peak_auxiliary_active,
                "peak_page_input_bytes": self._peak_page_input_bytes,
            }
