"""Bounded, cancellable in-process task management for document imports."""

from __future__ import annotations

import copy
import datetime as dt
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


TERMINAL_STATUSES = {"completed", "error", "cancelled"}


class TaskCancelled(RuntimeError):
    """Raised cooperatively when a task has been cancelled."""


class TaskQueueFull(RuntimeError):
    """Raised when the bounded worker queue has no capacity."""


@dataclass
class _TaskRecord:
    state: dict[str, Any]
    cancel_event: threading.Event = field(default_factory=threading.Event)
    created_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc)
    )
    updated_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc)
    )
    future: Future | None = None
    submitted: bool = False


class TaskManager:
    """Thread-safe task state with cooperative cancellation and TTL eviction."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_queue: int = 4,
        terminal_ttl_seconds: int = 3600,
        max_records: int = 1024,
        temp_asset_cleanup: Callable[[list[str]], Any] | None = None,
    ) -> None:
        if (
            max_workers < 1
            or max_queue < 0
            or terminal_ttl_seconds < 0
            or max_records < max_workers + max_queue
        ):
            raise ValueError("任务并发和队列容量必须为非负有效值")
        self._lock = threading.RLock()
        self._records: dict[str, _TaskRecord] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="mathbank-import",
        )
        self._capacity = threading.BoundedSemaphore(max_workers + max_queue)
        self._terminal_ttl = dt.timedelta(seconds=terminal_ttl_seconds)
        self._max_records = max_records
        self._temp_asset_cleanup = temp_asset_cleanup
        self._maintenance_stop = threading.Event()
        self._maintenance_thread: threading.Thread | None = None

    def create(self, task_id: str, **initial_state: Any) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with self._lock:
            self._cleanup_locked(now)
            if task_id in self._records:
                raise ValueError(f"任务已存在: {task_id}")
            self._make_record_capacity_locked()
            state = {
                "status": "queued",
                "progress": 0,
                **initial_state,
            }
            self._records[task_id] = _TaskRecord(
                state=state,
                created_at=now,
                updated_at=now,
            )

    def exists(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._records

    def remove(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.pop(task_id, None)
            if record is not None:
                record.cancel_event.set()
                if record.future and not record.future.running():
                    record.future.cancel()
            return copy.deepcopy(record.state) if record else None

    def update(self, task_id: str, **changes: Any) -> bool:
        """Update live state; cancelled/completed states cannot be overwritten."""

        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return False
            current_status = record.state.get("status")
            if current_status in TERMINAL_STATUSES:
                return False
            record.state.update(changes)
            record.updated_at = dt.datetime.now(dt.timezone.utc)
            return True

    def fail(self, task_id: str, error: str, **extra: Any) -> bool:
        return self.update(task_id, status="error", progress=0, error=error, **extra)

    def complete(self, task_id: str, **result: Any) -> bool:
        return self.update(task_id, status="completed", progress=100, **result)

    def cancel(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            if record.state.get("status") in TERMINAL_STATUSES:
                return copy.deepcopy(record.state)
            record.cancel_event.set()
            record.state.update(
                {
                    "status": "cancelled",
                    "progress": 0,
                    "log": "任务已由用户取消。",
                }
            )
            record.updated_at = dt.datetime.now(dt.timezone.utc)
            if record.future and not record.future.running():
                record.future.cancel()
            return copy.deepcopy(record.state)

    def is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            record = self._records.get(task_id)
            return bool(record and record.cancel_event.is_set())

    def check_cancelled(self, task_id: str) -> None:
        if self.is_cancelled(task_id):
            raise TaskCancelled(f"任务已取消: {task_id}")

    def add_temp_asset(self, task_id: str, path: str) -> bool:
        """Append one deduplicated cleanup asset, including after task completion."""

        if not isinstance(path, str) or not path:
            raise ValueError("临时资源路径不能为空")
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return False
            current = record.state.get("temp_assets")
            assets = list(current) if isinstance(current, list) else []
            if path in assets:
                return True
            assets.append(path)
            record.state["temp_assets"] = assets
            record.updated_at = dt.datetime.now(dt.timezone.utc)
            return True

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            result = copy.deepcopy(record.state)
            result["created_at"] = record.created_at.isoformat().replace("+00:00", "Z")
            result["updated_at"] = record.updated_at.isoformat().replace("+00:00", "Z")
            return result

    def submit(
        self,
        task_id: str,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Future:
        if not self._capacity.acquire(blocking=False):
            raise TaskQueueFull("后台任务繁忙，请稍后重试。")
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                self._capacity.release()
                raise KeyError(task_id)
            if record.submitted:
                self._capacity.release()
                raise ValueError(f"任务已提交: {task_id}")
            if record.state.get("status") in TERMINAL_STATUSES:
                self._capacity.release()
                raise ValueError(f"终态任务不能再次提交: {task_id}")
            record.submitted = True

        def runner():
            if record.cancel_event.is_set():
                raise TaskCancelled(f"任务已取消: {task_id}")
            return function(*args, **kwargs)

        try:
            future = self._executor.submit(runner)
        except Exception:
            with self._lock:
                if self._records.get(task_id) is record:
                    record.submitted = False
            self._capacity.release()
            raise
        with self._lock:
            if self._records.get(task_id) is record:
                record.future = future
        future.add_done_callback(
            lambda completed: self._finalize_future(task_id, record, completed)
        )
        return future

    def _finalize_future(
        self, task_id: str, record: _TaskRecord, future: Future
    ) -> None:
        """Release queue capacity and guarantee a terminal task state once."""

        try:
            with self._lock:
                if self._records.get(task_id) is not record:
                    return
                record.future = None
                if record.state.get("status") in TERMINAL_STATUSES:
                    return
                if future.cancelled() or record.cancel_event.is_set():
                    record.cancel_event.set()
                    record.state.update(
                        {"status": "cancelled", "progress": 0, "log": "任务已取消。"}
                    )
                else:
                    error = future.exception()
                    if error is None:
                        record.state.update({"status": "completed", "progress": 100})
                    else:
                        record.state.update(
                            {
                                "status": "error",
                                "progress": 0,
                                "error": f"后台任务执行失败: {type(error).__name__}",
                            }
                        )
                record.updated_at = dt.datetime.now(dt.timezone.utc)
        finally:
            self._capacity.release()

    def cleanup(self) -> int:
        with self._lock:
            return self._cleanup_locked(dt.datetime.now(dt.timezone.utc))

    def start_maintenance(self, *, interval_seconds: float = 60.0) -> None:
        """Periodically evict expired terminal records even when traffic is idle."""

        if interval_seconds <= 0:
            raise ValueError("任务维护间隔必须大于零")
        with self._lock:
            if self._maintenance_thread and self._maintenance_thread.is_alive():
                return
            self._maintenance_stop.clear()
            thread = threading.Thread(
                target=self._maintenance_loop,
                args=(interval_seconds,),
                name="mathbank-task-maintenance",
                daemon=True,
            )
            self._maintenance_thread = thread
            thread.start()

    def _maintenance_loop(self, interval_seconds: float) -> None:
        while not self._maintenance_stop.wait(interval_seconds):
            self.cleanup()

    def _cleanup_locked(self, now: dt.datetime) -> int:
        expired = [
            task_id
            for task_id, record in self._records.items()
            if record.state.get("status") in TERMINAL_STATUSES
            and record.future is None
            and now - record.updated_at >= self._terminal_ttl
        ]
        for task_id in expired:
            record = self._records.pop(task_id, None)
            if record is not None:
                self._cleanup_temp_assets_locked(record)
        return len(expired)

    def _make_record_capacity_locked(self) -> None:
        """Evict the oldest finished records before rejecting new work."""

        if len(self._records) < self._max_records:
            return
        terminal_records = sorted(
            (
                (task_id, record)
                for task_id, record in self._records.items()
                if record.state.get("status") in TERMINAL_STATUSES
                and record.future is None
            ),
            key=lambda item: item[1].updated_at,
        )
        for task_id, _record in terminal_records:
            record = self._records.pop(task_id, None)
            if record is not None:
                self._cleanup_temp_assets_locked(record)
            if len(self._records) < self._max_records:
                return
        raise TaskQueueFull("任务记录容量已满，请等待现有任务结束。")

    def _cleanup_temp_assets_locked(self, record: _TaskRecord) -> None:
        if self._temp_asset_cleanup is None:
            return
        values = record.state.get("temp_assets")
        if not isinstance(values, list):
            return
        paths = list(dict.fromkeys(path for path in values if isinstance(path, str)))
        if not paths:
            return
        try:
            self._temp_asset_cleanup(paths)
        except Exception:
            # Eviction must remain bounded even if a filesystem cleanup fails.
            pass

    def shutdown(self, *, wait: bool = True) -> None:
        self._maintenance_stop.set()
        with self._lock:
            maintenance_thread = self._maintenance_thread
            self._maintenance_thread = None
        if (
            wait
            and maintenance_thread is not None
            and maintenance_thread is not threading.current_thread()
        ):
            maintenance_thread.join()
        self._executor.shutdown(wait=wait, cancel_futures=True)
