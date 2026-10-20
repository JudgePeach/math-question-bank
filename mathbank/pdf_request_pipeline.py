"""Overlap serial PDF preparation with bounded requests over detached data.

The producer is advanced only on the caller's thread. A PyMuPDF document can
therefore stay in the producer while request workers receive only prepared
paths and ordinary dictionaries. This module never opens documents or assets.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import threading
import time
from typing import Callable, Iterable, Iterator

from mathbank.task_manager import TaskCancelled
from mathbank.pdf_ocr_capacity import (
    DEFAULT_PDF_OCR_CONCURRENCY,
    MAX_PDF_OCR_CONCURRENCY,
)


MAX_PDF_PAGE_REQUESTS = MAX_PDF_OCR_CONCURRENCY


def iter_pdf_page_requests(
    prepared_jobs: Iterable,
    request: Callable,
    *,
    check_cancelled: Callable[[], None],
    max_workers: int = DEFAULT_PDF_OCR_CONCURRENCY,
    diagnostics: dict | None = None,
) -> Iterator[Future]:
    """Yield completed futures, preparing at most max_workers jobs ahead.

    ``request(job, check_request_cancelled)`` executes on a request thread. It
    must use the supplied cancellation check around waits and network calls,
    and retain the existing shared OCR permit; this pool adds no HTTP budget.
    Advance and explicitly close this iterator on the original caller thread.
    Producer failures and early close stop queued work, close the producer and
    observe in-flight exceptions without waiting for an uninterruptible HTTP
    read. The worker's post-request check discards late results.
    """
    if type(max_workers) is not int or not 1 <= max_workers <= MAX_PDF_PAGE_REQUESTS:
        raise ValueError("PDF 页面流水线仅支持 1 至 12 个请求线程。")
    if not callable(request) or not callable(check_cancelled):
        raise ValueError("PDF 页面请求与取消检查必须为可调用对象。")
    if diagnostics is not None and not isinstance(diagnostics, dict):
        raise ValueError("PDF 页面流水线诊断必须为字典。")
    report = diagnostics if diagnostics is not None else {}
    report.update(workers=max_workers, max_in_flight=max_workers, prepared=0,
                  submitted=0, completed=0, peak_in_flight=0,
                  preparation_seconds=0.0, status="running")
    started = time.monotonic()
    producer = iter(prepared_jobs)
    stop = threading.Event()
    pending: set[Future] = set()
    all_futures: list[Future] = []
    exhausted = False
    completed_normally = False

    def check_request_cancelled():
        if stop.is_set():
            raise TaskCancelled("PDF 页面请求流水线已停止。")
        check_cancelled()

    def worker(job):
        check_request_cancelled()
        result = request(job, check_request_cancelled)
        check_request_cancelled()
        return result

    def observe(future):
        if not future.cancelled():
            future.exception()

    executor = ThreadPoolExecutor(max_workers=max_workers,
                                  thread_name_prefix="mathbank-pdf-ocr")
    try:
        while not exhausted or pending:
            check_request_cancelled()
            while not exhausted and len(pending) < max_workers:
                check_request_cancelled()
                preparing = time.monotonic()
                try:
                    job = next(producer)
                except StopIteration:
                    exhausted = True
                    break
                finally:
                    report["preparation_seconds"] += time.monotonic() - preparing
                report["prepared"] += 1
                check_request_cancelled()
                future = executor.submit(worker, job)
                future.add_done_callback(observe)
                all_futures.append(future)
                pending.add(future)
                report["submitted"] += 1
                report["peak_in_flight"] = max(report["peak_in_flight"], len(pending))
            if not pending:
                break
            ready, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            check_request_cancelled()
            for future in ready:
                pending.remove(future)
                report["completed"] += 1
                yield future
                check_request_cancelled()
        completed_normally = True
        report["status"] = "completed"
    except TaskCancelled:
        report["status"] = "cancelled"
        raise
    except GeneratorExit:
        report["status"] = "closed"
        raise
    except BaseException:
        report["status"] = "failed"
        raise
    finally:
        stop.set()
        for future in all_futures:
            future.cancel()
        # Generator.close runs its finally block on the caller, including any
        # producer-owned fitz.Document context. Never close it on a worker.
        try:
            close = getattr(producer, "close", None)
            if callable(close):
                close()
        finally:
            executor.shutdown(wait=completed_normally, cancel_futures=True)
            report["preparation_seconds"] = round(report["preparation_seconds"], 6)
            report["elapsed_seconds"] = round(time.monotonic() - started, 6)
