"""Coalesce post-commit exports without dropping writes made during an export."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable


class DatabaseExportQueue:
    """One worker, one pending snapshot, and a bounded delay after a burst.

    Explicit exports remain synchronous. This queue is only for the derived
    JSON/Markdown files scheduled after successful database commits.
    """

    def __init__(
        self,
        export: Callable[[], object],
        *,
        debounce_seconds: float = 0.75,
        max_delay_seconds: float = 2.0,
        on_error: Callable[[Exception, str], None] | None = None,
    ) -> None:
        self._export = export
        self._debounce = max(0.0, debounce_seconds)
        self._max_delay = max(self._debounce, max_delay_seconds)
        self._on_error = on_error
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None
        self._pending = False
        self._closing = False
        self._first_request = 0.0
        self._last_request = 0.0
        self._operation = "update"

    @property
    def started(self) -> bool:
        with self._condition:
            return self._worker is not None

    def request(self, *, operation: str = "update") -> None:
        with self._condition:
            if self._closing:
                raise RuntimeError("Database export queue is shutting down")
            now = time.monotonic()
            if not self._pending:
                self._first_request = now
            self._last_request = now
            self._operation = operation
            self._pending = True
            if self._worker is None:
                worker = threading.Thread(
                    target=self._run, name="mathbank-database-export", daemon=True
                )
                self._worker = worker
                try:
                    worker.start()
                except Exception:
                    self._worker = None
                    raise
            self._condition.notify_all()

    def close(self, *, wait: bool = True) -> None:
        """Flush pending work during cooperative service shutdown."""
        with self._condition:
            self._closing = True
            worker = self._worker
            self._condition.notify_all()
        if wait and worker is not None and worker is not threading.current_thread():
            worker.join()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending:
                    if self._closing:
                        return
                    self._condition.wait()
                while not self._closing:
                    due = min(
                        self._last_request + self._debounce,
                        self._first_request + self._max_delay,
                    )
                    remaining = due - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
                operation = self._operation
                # A new request during the export sets this again, guaranteeing
                # another read snapshot after that commit.
                self._pending = False
            try:
                self._export()
            except Exception as exc:
                try:
                    if self._on_error is not None:
                        self._on_error(exc, operation)
                    else:
                        logging.getLogger(__name__).warning(
                            "Post-commit export failed for %s (%s)",
                            operation, type(exc).__name__,
                        )
                except Exception:
                    logging.getLogger(__name__).exception("Export error reporting failed")
