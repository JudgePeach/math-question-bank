"""Bounded sharing jobs with durable, non-replaying import intent receipts.

Only small status records survive restart. Inputs and worker closures are never
reconstructed: a process interruption must not silently append a package twice.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
import traceback
import uuid

from mathbank.asset_security import harden_private_path, replace_file_atomic, write_private_text_atomic
from mathbank.storage_access import StorageAccessError, check_directory_access


LOGGER = logging.getLogger(__name__)
_ACTIVE = {"queued", "running", "committing"}
EXPORT_RETENTION_SECONDS = 7 * 24 * 60 * 60
_PUBLIC = {"task_id", "operation", "status", "stage", "current", "total",
           "stage_timings", "created_at", "updated_at", "result", "error"}


class TransferTaskConflict(ValueError):
    def __init__(self, message, *, active_task_id=None):
        self.active_task_id = active_task_id
        super().__init__(message)


class TransferTaskQueueFull(RuntimeError):
    pass


class TransferTaskError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code, self.safe_message = code, message
        super().__init__(message)


def normalize_transfer_task_id(value: str) -> str:
    try:
        normalized = str(uuid.UUID(value))
        if normalized != value.lower():
            raise ValueError
        return normalized
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("导入导出请求编号须为标准 UUID") from exc


def _error_chain(exc):
    """Inspect wrapped storage errors without serializing their private text."""
    pending, seen = [exc], set()
    while pending and len(seen) < 16:
        cause = pending.pop(0)
        if not isinstance(cause, BaseException) or id(cause) in seen:
            continue
        seen.add(id(cause))
        yield cause
        pending.extend((getattr(cause, "orig", None), cause.__cause__, cause.__context__))


def _sqlite_failure(exc):
    if not isinstance(exc, sqlite3.Error):
        return None
    code = getattr(exc, "sqlite_errorcode", None)
    if type(code) is int:
        return code & 0xff
    # Python 3.10 does not attach SQLite result codes.  Only inspect SQLite's
    # fixed diagnostic prefixes, never arbitrary SQLAlchemy/exception text.
    message = str(exc).strip().lower()
    for prefixes, result in (
        (("database is locked", "database table is locked", "database schema is locked", "database is busy"), 5),
        (("attempt to write a readonly database",), 8),
        (("disk i/o error",), 10),
        (("database or disk is full",), 13),
        (("unable to open database file",), 14),
        (("out of memory",), 7),
    ):
        if message.startswith(prefixes):
            return result
    return None


def transfer_error(exc: BaseException, *, stage: str = "", committing: bool = False) -> dict:
    """Never expose SQL parameters, archive contents or exception text."""
    chain = tuple(_error_chain(exc))
    if committing or any(getattr(cause, "commit_outcome_unknown", False) for cause in chain):
        return {"code": "unknown_after_commit", "message": "提交期间未能确认结果，请先核实题库和备份，勿直接再次导入。"}
    for original in chain:
        if isinstance(original, StorageAccessError):
            return {"code": original.code, "message": original.safe_message}
    for original in chain:
        if getattr(original, "backup_busy", False):
            return {"code": "backup_busy", "message": "题库正被其他写入操作占用，导入前备份未完成，请稍后重试。"}
        sqlite_code = _sqlite_failure(original)
        if isinstance(original, MemoryError) or sqlite_code == 7:
            return {"code": "memory", "message": "本机可用内存不足，本次任务未完成，请关闭其他程序或将题库分批导入导出。"}
        if sqlite_code in {5, 6}:
            return {"code": "database_locked", "message": "题库正被其他写入操作占用，本次导入未完成，请稍后重试。"}
        if sqlite_code == 8:
            return {"code": "database_readonly", "message": "题库数据库或所在目录只读，本次导入未完成，请检查目录写入权限。"}
        if sqlite_code in {10, 14}:
            return {"code": "database_io", "message": "题库数据库读写失败，请检查存储设备、文件占用和目录权限。"}
        if sqlite_code == 13:
            return {"code": "disk", "message": "本地存储空间不足，本次任务未完成，请清理空间后重试。"}
        if isinstance(original, OSError):
            winerror = getattr(original, "winerror", None)
            if winerror in {112, 39} or original.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
                return {"code": "disk", "message": "本地存储空间不足，本次任务未完成，请清理空间后重试。"}
            if winerror in {32, 33}:
                return {"code": "file_in_use", "message": "本地文件正被其他程序占用，本次任务未完成，请待文件释放后重试。"}
            if winerror == 5 or original.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
                return {"code": "permission", "message": "本地目录无法写入，本次任务未完成，请检查目录权限或安全软件拦截。"}
            if original.errno == errno.EIO:
                return {"code": "disk", "message": "本地存储读写失败，请检查磁盘状态。"}
    if isinstance(exc, TransferTaskError):
        return {"code": exc.code, "message": exc.safe_message}
    if isinstance(exc, (ValueError, TypeError)):
        return {"code": "validation", "message": "分享包或导出参数未通过校验，请确认文件完整且由本题库导出。"}
    if stage == "backup":
        return {"code": "backup", "message": "导入前自动备份失败，尚未导入题目，请检查备份目录及现有图片。"}
    return {"code": "internal", "message": "题库分享任务未完成，请查看本地诊断日志后重试。"}


def log_transfer_error(task_id: str, operation: str, stage: str, exc: BaseException) -> None:
    # format_exception() would include SQL text and bound question parameters.
    # Stack frames retain the traceback's code locations without those values.
    frames = []
    for cause in _error_chain(exc):
        codes = {key: value for key in ("errno", "winerror", "sqlite_errorcode")
                 if type(value := getattr(cause, key, None)) is int}
        frames.append({"type": type(cause).__name__, **codes, "frames": [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(cause.__traceback__)]})
    LOGGER.error("Library transfer failed task=%s operation=%s stage=%s type=%s traceback=%s",
                 task_id, operation, stage, type(exc).__name__, json.dumps(frames))


class LibraryTransferTasks:
    def __init__(self, root: Path, *, run_import, run_export, max_pending: int = 2):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("题库分享任务目录不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True)
        harden_private_path(self.root, directory=True)
        self._run_import, self._run_export = run_import, run_export
        self._lock = threading.RLock()
        self._queue = queue.Queue(maxsize=max(1, max_pending))
        self._upload_slots = threading.BoundedSemaphore(max(1, max_pending) + 1)
        self._records = {}
        self._timers = {}
        self._last_saved = {}
        self._worker = None
        self._closing = False
        self._recover()

    @property
    def started(self):
        with self._lock:
            return self._worker is not None

    def _directory(self, task_id):
        return self.root / normalize_transfer_task_id(task_id)

    @staticmethod
    def _sync_directory(path):
        if os.name == "posix" and hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _save(self, record):
        directory = self._directory(record["task_id"])
        write_private_text_atomic(directory / "state.json", json.dumps(record, ensure_ascii=False, allow_nan=False))
        self._sync_directory(directory)
        self._last_saved[record["task_id"]] = time.monotonic()

    @staticmethod
    def _remove_artifact(path):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            # Deferred cleanup cannot change a committed result or stop the
            # worker. Do not log the private artifact's absolute path.
            log_transfer_error(path.parent.name, "cleanup", "cleanup", exc)

    def _recover(self):
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                task_id = normalize_transfer_task_id(directory.name)
            except ValueError:
                continue
            try:
                path = directory / "state.json"
                if path.stat().st_size > 64 * 1024 or path.is_symlink():
                    raise ValueError("Invalid receipt")
                record = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(record, dict) or record.get("task_id") != task_id
                        or record.get("operation") not in {"import", "export"}):
                    raise ValueError("Invalid receipt")
            except (OSError, ValueError, TypeError):
                # The directory itself is a durable tombstone. A damaged
                # receipt can never authorize reuse of this intent UUID.
                record = {"task_id": task_id, "operation": "unknown", "status": "unknown",
                          "stage": "unknown", "current": 0, "total": 0, "stage_timings": {},
                          "created_at": time.time(), "updated_at": time.time(),
                          "error": {"code": "receipt_unavailable", "message": "任务回执无法读取，请先核实题库，勿重复导入。"}}
            if record.get("status") in _ACTIVE:
                uncertain = record["operation"] == "import" and record.get("status") == "committing"
                record.update(status="unknown" if uncertain else "interrupted", updated_at=time.time())
                record["error"] = ({"code": "unknown_after_commit", "message": "服务在提交期间中断，请先核实题库和备份，勿直接再次导入。"}
                                   if uncertain else {"code": "interrupted", "message": "服务重启中断了任务，未自动重跑；请核实结果后再发起新任务。"})
                try:
                    self._save(record)
                except Exception as exc:
                    # Recovery must not prevent the server starting.  Keep the
                    # UUID bound and conservatively forbid any implicit replay.
                    record.update(status="unknown", updated_at=time.time(), error={
                        "code": "receipt_unavailable", "message": "任务恢复回执无法保存，请先核实题库和备份，勿重复导入。",
                    })
                    log_transfer_error(task_id, record["operation"], "recovery_receipt", exc)
            self._records[task_id] = record
            # Inputs cannot be resumed after restart. Keep receipts forever.
            self._remove_artifact(directory / "input.zip")
            shutil.rmtree(directory / "prepared", ignore_errors=True)
            if (record.get("operation") == "export" and record.get("status") == "completed"
                    and time.time() - record.get("updated_at", time.time()) > EXPORT_RETENTION_SECONDS):
                # Only startup expires archives, so an active FileResponse is
                # never raced by ordinary status polling or new submissions.
                # The intent receipt is retained, and download returns 410.
                self._remove_artifact(directory / "library.zip")
        shutil.rmtree(self.root / "_uploads", ignore_errors=True)

    @contextmanager
    def upload_workspace(self):
        """Bound concurrent uploads independently of the single DB worker."""
        if not self._upload_slots.acquire(blocking=False):
            raise TransferTaskQueueFull("题库分享上传队列已满，请稍后重试")
        try:
            with self._lock:
                if self._closing:
                    raise TransferTaskQueueFull("服务正在关闭，请稍后重试")
                root = self.root / "_uploads"
                root.mkdir(exist_ok=True)
                harden_private_path(root, directory=True)
            temporary = tempfile.TemporaryDirectory(prefix="upload-", dir=root)
            try:
                yield Path(temporary.name)
            finally:
                try:
                    temporary.cleanup()
                except OSError as exc:
                    # An accepted import has already moved its ZIP and saved
                    # its receipt. A Windows sharing violation while removing
                    # the now-unused workspace must not hide that response or
                    # replace the original upload error. Startup retries any
                    # deferred directory cleanup.
                    log_transfer_error("upload", "import", "cleanup", exc)
        finally:
            self._upload_slots.release()

    def submit_import(self, task_id, archive: Path, archive_sha256: str):
        if not isinstance(archive_sha256, str) or len(archive_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in archive_sha256):
            raise ValueError("分享包摘要无效")
        return self._submit(task_id, "import", archive_sha256, archive=Path(archive))

    def submit_export(self, task_id, payload: dict):
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        return self._submit(task_id, "export", hashlib.sha256(encoded).hexdigest(), payload=copy.deepcopy(payload))

    def _submit(self, task_id, operation, digest, *, archive=None, payload=None):
        task_id = normalize_transfer_task_id(task_id)
        intent = operation + ":" + digest
        with self._lock:
            existing = self._records.get(task_id)
            if existing is not None:
                if existing.get("intent") != intent:
                    raise TransferTaskConflict("请求编号已绑定其他导入导出意图，请使用新的请求编号")
                return self._public(existing), False
            if operation == "import":
                for active in self._records.values():
                    if (active.get("intent") == intent and active.get("status") in _ACTIVE):
                        # Two windows can generate different intent UUIDs.
                        # Concurrent copies of the same archive follow the
                        # accepted job; explicit sequential appends still work.
                        raise TransferTaskConflict(
                            "同一分享包正在导入，请查看已有任务进度，勿重复提交。",
                            active_task_id=active["task_id"],
                        )
            if self._closing or self._queue.full():
                raise TransferTaskQueueFull("题库分享任务队列已满，请稍后重试")
            directory = self._directory(task_id)
            if directory.exists():
                raise TransferTaskConflict("请求编号已有持久记录，请先查询原任务")
            # Replaying a durable UUID above must still work if directory
            # permissions changed since completion. Only new jobs need this
            # write/replace/delete probe, before accepting or moving input.
            check_directory_access(
                self.root, label="分享任务回执目录（.system_generated/library_transfer_tasks）",
                require_subdirectory=True,
            )
            directory.mkdir(mode=0o700)
            self._sync_directory(self.root)
            record = {"version": 1, "task_id": task_id, "operation": operation, "intent": intent,
                      "status": "queued", "stage": "queued", "current": 0, "total": 0,
                      "stage_timings": {}, "created_at": time.time(), "updated_at": time.time()}
            self._records[task_id] = record
            try:
                self._save(record)
                if archive is not None:
                    replace_file_atomic(archive, directory / "input.zip")
                    harden_private_path(directory / "input.zip")
                if self._worker is None:
                    worker = threading.Thread(target=self._run, name="mathbank-library-transfer", daemon=True)
                    try:
                        worker.start()
                    except BaseException:
                        self._worker = None
                        raise
                    self._worker = worker
                self._queue.put_nowait((task_id, payload))
            except BaseException as exc:
                record.update(status="failed", error=transfer_error(exc), updated_at=time.time())
                self._remove_artifact(directory / "input.zip")
                try:
                    self._save(record)
                except OSError:
                    pass
                raise
            return self._public(record), True

    def _public(self, record):
        result = copy.deepcopy({key: value for key, value in record.items() if key in _PUBLIC})
        timer = self._timers.get(record["task_id"])
        if timer is not None:
            stage, started = timer
            result["stage_timings"][stage] = result["stage_timings"].get(stage, 0) + time.monotonic() - started
        return result

    def get(self, task_id):
        with self._lock:
            record = self._records.get(normalize_transfer_task_id(task_id))
            return self._public(record) if record else None

    def download(self, task_id):
        task_id = normalize_transfer_task_id(task_id)
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise KeyError(task_id)
            if record.get("operation") != "export" or record.get("status") != "completed":
                raise TransferTaskConflict("导出任务尚未完成")
            archive = self._directory(task_id) / "library.zip"
            if not archive.is_file() or archive.is_symlink():
                raise FileNotFoundError("导出文件已不存在，请重新导出")
            return archive, copy.deepcopy(record.get("result", {}))

    def _progress(self, task_id, stage, *, current=0, total=0, result=None):
        with self._lock:
            record = self._records[task_id]
            now = time.monotonic()
            timer = self._timers.get(task_id)
            changed = timer is None or timer[0] != stage
            if changed:
                if timer is not None:
                    previous, started = timer
                    record["stage_timings"][previous] = record["stage_timings"].get(previous, 0) + now - started
                self._timers[task_id] = stage, now
            record.update(stage=stage, current=max(0, int(current)), total=max(0, int(total)), updated_at=time.time())
            if result is not None:
                # Only the trusted runner supplies this small pre-commit
                # recovery hint; no imported records or input paths are kept.
                record["result"] = {key: value for key, value in result.items()
                                    if key in {"backup_path", "active_version_code", "backup_warnings"}}
            if stage == "committing" and record["operation"] == "import":
                record["status"] = "committing"
            if changed or result is not None or now - self._last_saved.get(task_id, 0) >= 0.5:
                self._save(record)

    def _finish_timer(self, task_id):
        timer = self._timers.pop(task_id, None)
        if timer is not None:
            stage, started = timer
            record = self._records[task_id]
            record["stage_timings"][stage] = record["stage_timings"].get(stage, 0) + time.monotonic() - started

    def _execute(self, task_id, payload):
        directory = self._directory(task_id)
        record = self._records[task_id]
        try:
            with self._lock:
                record.update(status="running", updated_at=time.time())
                self._save(record)
            progress = lambda stage, **kwargs: self._progress(task_id, stage, **kwargs)
            runner = self._run_import if record["operation"] == "import" else self._run_export
            result = runner(directory, progress) if record["operation"] == "import" else runner(directory, payload, progress)
            with self._lock:
                self._finish_timer(task_id)
                # Persist the completed receipt before publishing success.
                completed = dict(record, status="completed", stage="completed", result=result, updated_at=time.time())
                self._save(completed)
                self._records[task_id] = completed
        except BaseException as exc:
            with self._lock:
                self._finish_timer(task_id)
                uncertain = record.get("status") == "committing"
                error = transfer_error(exc, stage=record.get("stage", ""), committing=uncertain)
                log_transfer_error(task_id, record["operation"], record.get("stage", ""), exc)
                record.update(status="unknown" if uncertain else "failed", error=error, updated_at=time.time())
                try:
                    self._save(record)
                except BaseException as save_exc:
                    log_transfer_error(task_id, record["operation"], "receipt", save_exc)
        finally:
            self._remove_artifact(directory / "input.zip")
            shutil.rmtree(directory / "prepared", ignore_errors=True)
            if self._records[task_id].get("status") != "completed":
                self._remove_artifact(directory / "library.zip")

    def _run(self):
        while True:
            try:
                task_id, payload = self._queue.get(timeout=0.2)
            except queue.Empty:
                with self._lock:
                    if self._closing:
                        return
                continue
            try:
                self._execute(task_id, payload)
            finally:
                self._queue.task_done()

    def close(self, *, wait=True):
        """Reject new requests, then drain accepted jobs during normal shutdown."""
        with self._lock:
            self._closing = True
            worker = self._worker
        if wait and worker is not None and worker.ident is not None and worker is not threading.current_thread():
            worker.join()
