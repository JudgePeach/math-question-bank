"""Private, atomic checkpoints for explicitly resumed document imports."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from mathbank.task_manager import TaskManager, TERMINAL_STATUSES, _TaskRecord


class DocumentTaskStore:
    MAX_STATE_BYTES = 64 * 1024 * 1024

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()

    def _path(self, task_id, suffix):
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", task_id):
            raise ValueError("无效的文档任务标识。")
        return self.root / (task_id + suffix)

    def _write(self, path, content):
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with open(temporary, "xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, task_id, snapshot):
        content = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(content) > self.MAX_STATE_BYTES:
            raise ValueError("文档恢复快照超过本地保存上限。")
        with self.lock:
            self._write(self._path(task_id, ".json"), content)

    def source(self, task_id, content=None):
        path = self._path(task_id, ".source")
        with self.lock:
            if content is not None:
                self._write(path, content)
                return {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
            if path.is_symlink() or path.stat().st_size > self.MAX_STATE_BYTES:
                raise ValueError("原文档恢复文件不可用。")
            return path.read_bytes()

    def has_source(self, task_id):
        try:
            path = self._path(task_id, ".source")
            return not path.is_symlink() and path.is_file()
        except (OSError, ValueError):
            return False

    def remove(self, task_id):
        with self.lock:
            for suffix in (".json", ".source"):
                try:
                    self._path(task_id, suffix).unlink(missing_ok=True)
                except OSError:
                    # Cleanup failure cannot prevent cancellation or startup.
                    # Expired records remain expired on the next startup too.
                    pass

    def records(self):
        candidates = []
        for path in self.root.glob("*.json"):
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue
        for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
            if path.name == "cancel-intents.json":
                continue
            try:
                self._path(path.stem, ".json")
                if path.stat().st_size > self.MAX_STATE_BYTES:
                    continue
                value = json.loads(path.read_text("utf-8"))
                if isinstance(value, dict) and value.get("task_id") == path.stem:
                    yield path.stem, value
            except (OSError, ValueError):
                continue

    def cancel_intent(self, request_id=None):
        path = self.root / "cancel-intents.json"
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        with self.lock:
            try:
                values = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                values = {}
            if not isinstance(values, dict):
                values = {}
            values = {k: v for k, v in values.items() if isinstance(v, (float, int)) and now - v < 86400}
            if request_id:
                values[request_id] = now
                self._write(path, json.dumps(values).encode())
            return values


class DurableDocumentTasks(TaskManager):
    """TaskManager with optional recovery, excluding explicitly transient types.

    Transient tasks keep the same in-process lifecycle but never publish a
    recovery snapshot. Source-file storage remains the upload caller's choice.
    """

    def __init__(self, *, store=None, on_snapshot=None, transient_document_types=(), **kwargs):
        if isinstance(transient_document_types, str):
            raise ValueError("临时文档类型须为类型名称集合。")
        self.transient_document_types = frozenset(transient_document_types)
        if any(not isinstance(value, str) or not value for value in self.transient_document_types):
            raise ValueError("临时文档类型名称无效。")
        self.store = store
        self.on_snapshot = on_snapshot
        self._publish_lock = threading.RLock()
        self._request_lock = threading.RLock()
        self._last_persist = {}
        self._cancel_intents = store.cancel_intent() if store else {}
        super().__init__(**kwargs)
        if store:
            now = dt.datetime.now(dt.timezone.utc)
            for task_id, state in store.records():
                # Leave earlier persisted files intact, including expired ones.
                # A new policy must not silently delete an old user's document.
                if self._is_transient(state):
                    continue
                if len(self._records) >= self._max_records:
                    break
                try:
                    updated = dt.datetime.fromisoformat(state.pop("updated_at").replace("Z", "+00:00"))
                    created = dt.datetime.fromisoformat(state.pop("created_at").replace("Z", "+00:00"))
                    retained_value = state.pop("retained_at", None)
                    retained = dt.datetime.fromisoformat(retained_value) if retained_value else None
                    retention = max(updated, retained) if state.get("status") == "completed" and retained else updated
                    if state.get("status") in TERMINAL_STATUSES and now - retention >= self._terminal_ttl:
                        store.remove(task_id)
                        continue
                    if state.get("status") not in TERMINAL_STATUSES:
                        state.update(status="interrupted", log="服务已重新启动，可继续处理已保留的文档。")
                    self._records[task_id] = _TaskRecord(state=state, created_at=created, updated_at=updated, retained_at=retained)
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue

    def _is_transient(self, state):
        return bool(state and state.get("document_type") in self.transient_document_types)

    def _persist(self, task_id, *, force=True):
        with self._lock:
            record = self._records.get(task_id)
            if record is None or self._is_transient(record.state):
                return
        published = None
        if self.store:
            with self._lock:
                now = time.monotonic()
                if not force and now - self._last_persist.get(task_id, float("-inf")) < 2:
                    return
                self._last_persist[task_id] = now
                snapshot = super().snapshot(task_id)
                record = self._records.get(task_id)
                if snapshot and record and record.retained_at:
                    snapshot["retained_at"] = record.retained_at.isoformat()
                if not snapshot:
                    return
                try:
                    self.store.save(task_id, snapshot)
                    published = snapshot
                except (OSError, ValueError, TypeError):
                    # A full disk must not turn accepted model output into a
                    # failed import. Surface recovery degradation separately.
                    with self._lock:
                        if task_id in self._records:
                            self._records[task_id].state["recovery_warning"] = "本地恢复快照未保存，请保留当前页面。"
        elif self.on_snapshot:
            published = super().snapshot(task_id)
        # Project IO must never hold the runtime task lock: draft saves and
        # task dispatch acquire these locks in the opposite temporal order.
        if published is not None and self.on_snapshot:
            with self._publish_lock:
                # Updates can arrive concurrently from page workers. Publish
                # the newest snapshot after serializing, so a slow progress
                # callback cannot overwrite a later completed result.
                current = super().snapshot(task_id)
                if current is None:
                    return
                try:
                    self.on_snapshot(task_id, current)
                except Exception as exc:
                    with self._lock:
                        if task_id in self._records:
                            self._records[task_id].state["recovery_warning"] = (
                                f"工程阶段暂存未完成（{type(exc).__name__}），已有结果仍保留。")

    def create(self, task_id, **state):
        super().create(task_id, **state)
        with self._lock:
            self._records[task_id].state["task_id"] = task_id
        self._persist(task_id)

    def update(self, task_id, **changes):
        with self._lock:
            record = self._records.get(task_id)
            previous_status = record.state.get("status") if record else None
        result = super().update(task_id, **changes)
        if result:
            force = (changes.get("status", previous_status) != previous_status
                     or bool(set(changes) & {"pdf_resume_state", "docx_resume_state", "docx_source_cache", "document_input", "data"}))
            self._persist(task_id, force=force)
        return result

    def cancel(self, task_id):
        result = super().cancel(task_id)
        self._persist(task_id)
        return result

    def add_temp_asset(self, task_id, path):
        result = super().add_temp_asset(task_id, path)
        if result:
            self._persist(task_id)
        return result

    def retain(self, task_id):
        result = super().retain(task_id)
        # Keep leases separate from evidence timestamps across restarts.
        if result and self.store:
            self._persist(task_id)
        return result

    def remove(self, task_id):
        result = super().remove(task_id)
        self._last_persist.pop(task_id, None)
        if self.store and not self._is_transient(result) and (result is not None or not self.transient_document_types):
            self.store.remove(task_id)
        return result

    def _cleanup_temp_assets_locked(self, record):
        super()._cleanup_temp_assets_locked(record)
        if self.store and record.state.get("task_id") and not self._is_transient(record.state):
            self.store.remove(record.state["task_id"])

    def _finalize_future(self, task_id, record, future):
        super()._finalize_future(task_id, record, future)
        self._persist(task_id)

    def restart(self, task_id):
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.future is not None or record.state.get("status") not in {"error", "interrupted"}:
                raise ValueError("该任务当前不能继续处理。")
            record.cancel_event.clear()
            record.submitted = False
            record.state.update(status="pending", log="已保留成功阶段，正在继续处理。")
            record.state.pop("error", None)
            record.updated_at = dt.datetime.now(dt.timezone.utc)
        self._persist(task_id)

    def request_cancelled(self, request_id):
        with self._request_lock:
            return bool(request_id and request_id in self._cancel_intents)

    def cancel_by_request(self, request_id, *, document_type=None):
        with self._request_lock:
            self._cancel_intents[request_id] = dt.datetime.now(dt.timezone.utc).timestamp()
            with self._lock:
                matches = [task_id for task_id, record in self._records.items()
                           if record.state.get("client_request_id") == request_id]
                transient = (document_type in self.transient_document_types
                    or bool(matches) and all(self._is_transient(self._records[task_id].state)
                                             for task_id in matches))
            # The caller supplies the type for cancellation before upload. Once
            # a task exists, its server-owned state also proves this policy.
            if self.store and not transient:
                try:
                    self.store.cancel_intent(request_id)
                except (OSError, ValueError):
                    # The in-process intent and known task cancellation still
                    # take effect if the disk cannot save a future lease.
                    pass
            return [self.cancel(task_id) for task_id in matches]
