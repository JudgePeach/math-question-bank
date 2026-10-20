"""Private document projects, independent of short-lived import task records.

All timestamps are UTC epoch seconds. Listing/getting a project is read-only;
opening it explicitly calls touch(). Runtime task callbacks call sync_task().
The caller owns image release: deletion returns registered URLs and never
unlinks uploads. Only the caller can check formal database image references.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import datetime as dt
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time
import uuid

_WINDOWS = os.name == "nt"
_LOCK_WAIT_SECONDS = 5.0


class DocumentProjectError(ValueError):
    pass


class ProjectNotFound(DocumentProjectError):
    pass


class ProjectConflict(DocumentProjectError):
    def __init__(self, message, *, current_revision=None):
        super().__init__(message)
        self.current_revision = current_revision


class ProjectCorrupt(DocumentProjectError):
    pass


class ProjectUnsafePath(DocumentProjectError):
    pass


_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_FILE = re.compile(r"^(?:source\.bin|curriculum\.json|manifest\.json|(?:stage|draft|baseline|task)-[0-9a-f]{32}\.json|\.[0-9a-f]{32}\.tmp)$")
_ASSET = re.compile(r"/static/(?:uploads|test_uploads)/[^\s<>\"'(){}\\\[\]]+")
_CREDENTIALS = {"api_key", "apikey", "access_token", "refresh_token", "authorization",
                "password", "secret", "client_secret", "local_token", "credentials"}
_TASK_PARTS = {"pdf_resume_state", "docx_resume_state", "docx_source_cache", "pdf_source_cache",
               "pdf_source_pages", "data", "diagnostics", "document_input", "temp_assets",
               "page_images", "page_numbers"}
_TASK_SMALL = {"task_id", "status", "progress", "log", "error", "partial", "generate_answers",
               "document_type", "pdf_strategy", "recovery_warning", "project_id"}


class DocumentProjectStore:
    MAX_SOURCE_BYTES = 64 * 1024 * 1024
    MAX_JSON_BYTES = 64 * 1024 * 1024
    MAX_DRAFT_BYTES = 32 * 1024 * 1024
    MAX_MANIFEST_BYTES = 2 * 1024 * 1024
    MAX_FIELD_CHARACTERS = 500_000
    MAX_QUESTIONS = 10_000

    def __init__(self, root, retention_seconds=7 * 86400, clock=None):
        path = Path(root).expanduser().absolute()
        if path.is_symlink():
            raise ProjectUnsafePath("工程根目录不能是符号链接。")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir():
            raise ProjectUnsafePath("工程根目录无效。")
        self._safe_directory(path)
        self.root = path.resolve()
        os.chmod(self.root, 0o700)
        if not isinstance(retention_seconds, (int, float)) or not math.isfinite(retention_seconds) or retention_seconds <= 0:
            raise DocumentProjectError("工程保留时间无效。")
        self.retention_seconds = retention_seconds
        self.clock = clock or time.time
        self._locks = {}
        self._locks_guard = threading.Lock()
        self._local = threading.local()
        for name in (".locks", ".trash"):
            directory = self.root / name
            if directory.is_symlink():
                raise ProjectUnsafePath("工程内部目录不能是符号链接。")
            directory.mkdir(exist_ok=True, mode=0o700)
            self._safe_directory(directory)
            os.chmod(directory, 0o700)

    def _now(self):
        value = self.clock()
        value = value.timestamp() if isinstance(value, dt.datetime) else value
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise DocumentProjectError("工程时钟无效。")
        return float(value)

    def _id(self, project_id):
        if not isinstance(project_id, str) or not _ID.fullmatch(project_id):
            raise ProjectUnsafePath("工程标识无效。")
        return project_id

    def _safe_directory(self, directory):
        try:
            info = directory.lstat()
        except OSError as exc:
            raise ProjectNotFound("本地工程目录不存在。") from exc
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ProjectUnsafePath("工程目录不能是符号链接或重解析点。")

    def _assert_root(self):
        self._safe_directory(self.root)

    def _directory(self, project_id):
        self._assert_root()
        directory = self.root / self._id(project_id)
        if directory.is_symlink():
            raise ProjectUnsafePath("工程目录不能是符号链接。")
        if not directory.is_dir():
            raise ProjectNotFound("本地文档工程不存在。")
        self._safe_directory(directory)
        return directory

    def _lock_fd(self, project_id, suffix, mode, *, wait=True, deadline=None):
        self._assert_root()
        directory = self.root / ".locks"
        if directory.is_symlink() or not directory.is_dir():
            raise ProjectUnsafePath("工程锁目录无效。")
        path = directory / (self._id(project_id) + suffix)
        self._safe_directory(directory)
        if path.is_symlink():
            raise ProjectUnsafePath("工程锁文件不能是符号链接。")
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ProjectUnsafePath("工程锁文件无效。")
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            if deadline is None:
                deadline = time.monotonic() + (_LOCK_WAIT_SECONDS if wait else 0)
            while True:
                try:
                    if fcntl is not None:
                        fcntl.flock(fd, (fcntl.LOCK_SH if mode == "shared" else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                    elif msvcrt is not None:
                        if os.fstat(fd).st_size == 0:
                            os.write(fd, b"0")
                        os.lseek(fd, 0, os.SEEK_SET)
                        try:
                            msvcrt.locking(fd, msvcrt.LK_NBRLCK if mode == "shared" else msvcrt.LK_NBLCK, 1)
                        except OSError as exc:
                            raise BlockingIOError("project lock is busy") from exc
                    else:
                        raise DocumentProjectError("当前平台没有可用的工程文件锁。")
                    break
                except BlockingIOError:
                    if not wait or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.005)
            return fd
        except BlockingIOError as exc:
            os.close(fd)
            raise ProjectConflict("工程正在使用，请稍后重试。") from exc
        except OSError as exc:
            if "fd" in locals():
                os.close(fd)
            raise ProjectUnsafePath("工程锁文件不可安全访问。") from exc
        except Exception:
            if "fd" in locals():
                os.close(fd)
            raise

    @contextmanager
    def operation(self, project_id):
        """A cross-process shared lease; deletion requires its exclusive peer."""
        project_id = self._id(project_id)
        leases = getattr(self._local, "leases", None)
        if leases is None:
            leases = self._local.leases = {}
        if project_id in leases:
            leases[project_id][0] += 1
        else:
            fd = self._lock_fd(project_id, ".lease", "shared")
            leases[project_id] = [1, fd]
        try:
            self._directory(project_id)
            yield
        finally:
            leases[project_id][0] -= 1
            if leases[project_id][0] == 0:
                os.close(leases.pop(project_id)[1])

    @contextmanager
    def _locked(self, project_id, *, deleting=False, wait=False):
        project_id = self._id(project_id)
        # A caller cannot release its own shared lease while awaiting deletion.
        # Other threads/processes may finish short reads within the one budget.
        if deleting and project_id in getattr(self._local, "leases", {}):
            raise ProjectConflict("工程正在使用，请稍后重试。")
        delete_deadline = time.monotonic() + _LOCK_WAIT_SECONDS if deleting and wait else None
        with self._locks_guard:
            lock = self._locks.setdefault(project_id, threading.RLock())
        lease_fd = mutex_fd = None
        acquired = False
        if deleting:
            while True:
                # An outer operation may hold a shared lease while its next
                # touch/bind needs this RLock. Never wait on that lease while
                # retaining the RLock, or both requests would wait each other.
                acquired = lock.acquire(blocking=False)
                conflict = ProjectConflict("工程正在写入，请稍后重试。")
                if acquired:
                    try:
                        lease_fd = self._lock_fd(project_id, ".lease", "exclusive", wait=False)
                        break
                    except ProjectConflict as exc:
                        conflict = exc
                    except BaseException:
                        lock.release()
                        raise
                    lock.release()
                    acquired = False
                if not wait or time.monotonic() >= delete_deadline:
                    raise conflict
                time.sleep(min(0.005, max(0, delete_deadline - time.monotonic())))
        else:
            acquired = lock.acquire(timeout=_LOCK_WAIT_SECONDS)
            if not acquired:
                raise ProjectConflict("工程正在写入，请稍后重试。")
        try:
            if deleting:
                context = _null_context()
            else:
                context = self.operation(project_id)
            with context:
                mutex_fd = self._lock_fd(project_id, ".lock", "exclusive",
                                         deadline=delete_deadline)
                yield self._directory(project_id)
        finally:
            if mutex_fd is not None:
                os.close(mutex_fd)
            if lease_fd is not None:
                os.close(lease_fd)
            lock.release()

    def _encode(self, value, limit=None):
        def inspect(item, depth=0):
            if depth > 32:
                raise DocumentProjectError("工程 JSON 层级过深。")
            if isinstance(item, str):
                if len(item) > self.MAX_FIELD_CHARACTERS:
                    raise DocumentProjectError("工程文本字段超过保存上限。")
            elif isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str) or len(key) > 512:
                        raise DocumentProjectError("工程 JSON 字段名称无效。")
                    if key.lower().replace("-", "_") in _CREDENTIALS:
                        raise DocumentProjectError("工程不能保存凭据字段。")
                    inspect(child, depth + 1)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    inspect(child, depth + 1)
        inspect(value)
        try:
            content = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise DocumentProjectError("工程内容不是有效 JSON。") from exc
        if len(content) > (limit or self.MAX_JSON_BYTES):
            raise DocumentProjectError("工程 JSON 超过保存上限。")
        return content

    def _write(self, directory, filename, content):
        if not _FILE.fullmatch(filename):
            raise ProjectUnsafePath("工程文件名无效。")
        self._assert_root()
        self._safe_directory(directory)
        if _WINDOWS:
            temporary = directory / ("." + uuid.uuid4().hex + ".tmp")
            target = directory / filename
            try:
                if target.is_symlink() or (target.exists() and getattr(target.lstat(), "st_file_attributes", 0) & 0x400):
                    raise ProjectUnsafePath("工程文件不能是符号链接或重解析点。")
                with open(temporary, "xb") as handle:
                    os.chmod(temporary, 0o600)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._safe_directory(directory)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return
        parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        temporary = "." + uuid.uuid4().hex + ".tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Replacing a malicious destination link is safe, but never hide it.
            try:
                if stat.S_ISLNK(os.stat(filename, dir_fd=parent_fd, follow_symlinks=False).st_mode):
                    raise ProjectUnsafePath("工程文件不能是符号链接。")
            except FileNotFoundError:
                pass
            os.replace(temporary, filename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)

    def _read(self, directory, filename, limit):
        if not isinstance(filename, str) or not _FILE.fullmatch(filename):
            raise ProjectCorrupt("工程快照文件记录无效。")
        try:
            self._assert_root()
            self._safe_directory(directory)
            if _WINDOWS:
                path = directory / filename
                before = path.lstat()
                if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
                    raise ProjectCorrupt("工程文件不能是符号链接或重解析点。")
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
                after = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    os.close(fd)
                    raise ProjectCorrupt("工程文件在读取期间变化。")
            else:
                parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
                finally:
                    os.close(parent_fd)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    raise ProjectCorrupt("工程文件类型或大小无效。")
                content = handle.read(limit + 1)
                if len(content) > limit:
                    raise ProjectCorrupt("工程文件超过读取上限。")
                return content
        except OSError as exc:
            raise ProjectCorrupt("工程文件缺失或无法安全读取。") from exc

    def _manifest(self, directory):
        try:
            manifest = json.loads(self._read(directory, "manifest.json", self.MAX_MANIFEST_BYTES))
            if (not isinstance(manifest, dict) or manifest.get("schema") != 1
                    or manifest.get("project_id") != directory.name
                    or type(manifest.get("revision")) is not int or manifest["revision"] < 0
                    or not isinstance(manifest.get("stages"), dict)
                    or not isinstance(manifest.get("task_parts"), dict)
                    or not isinstance(manifest.get("temp_assets"), list)
                    or not isinstance(manifest.get("source"), dict) or manifest["source"].get("file") != "source.bin"
                    or not isinstance(manifest.get("curriculum"), dict) or manifest["curriculum"].get("file") != "curriculum.json"
                    or not isinstance(manifest.get("source_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", manifest["source_sha256"])
                    or type(manifest.get("source_bytes")) is not int or manifest["source_bytes"] <= 0
                    or not isinstance(manifest.get("title"), str) or not isinstance(manifest.get("filename"), str)
                    or any(type(manifest.get(key)) not in (int, float) or not math.isfinite(manifest[key])
                           for key in ("last_used", "expires_at", "created_at"))):
                raise ProjectCorrupt("工程清单结构无效。")
            for key in ("source", "curriculum", "baseline", "draft", "task"):
                receipt = manifest.get(key)
                if receipt is not None:
                    self._receipt_shape(receipt)
            for name, receipt in manifest["stages"].items():
                if not isinstance(name, str) or not _NAME.fullmatch(name):
                    raise ProjectCorrupt("工程阶段名称已损坏。")
                self._receipt_shape(receipt)
                if type(receipt.get("complete")) is not bool or not isinstance(receipt.get("input_fingerprint"), str):
                    raise ProjectCorrupt("工程阶段完成记录已损坏。")
            for name, receipt in manifest["task_parts"].items():
                if name not in _TASK_PARTS:
                    raise ProjectCorrupt("工程任务快照名称已损坏。")
                self._receipt_shape(receipt)
            if (manifest["source"]["sha256"] != manifest["source_sha256"]
                    or manifest["source"]["bytes"] != manifest["source_bytes"]
                    or any(not self._valid_asset(url) for url in manifest["temp_assets"])):
                raise ProjectCorrupt("工程来源或图片记录已损坏。")
            return manifest
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
            raise ProjectCorrupt("工程清单已损坏。") from exc

    def _receipt_shape(self, receipt):
        if (not isinstance(receipt, dict) or type(receipt.get("bytes")) is not int
                or receipt["bytes"] < 0 or not isinstance(receipt.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"])
                or not isinstance(receipt.get("file"), str) or not _FILE.fullmatch(receipt["file"])):
            raise ProjectCorrupt("工程快照校验记录无效。")

    def _blob(self, directory, receipt):
        self._receipt_shape(receipt)
        content = self._read(directory, receipt.get("file"), max(self.MAX_JSON_BYTES, self.MAX_SOURCE_BYTES))
        if len(content) != receipt["bytes"] or hashlib.sha256(content).hexdigest() != receipt["sha256"]:
            raise ProjectCorrupt("工程快照与校验记录不一致。")
        return content

    def _json_blob(self, directory, receipt):
        try:
            return json.loads(self._blob(directory, receipt))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectCorrupt("工程阶段快照已损坏。") from exc

    def _put_blob(self, directory, payload, prefix, previous=None, *, limit=None):
        content = self._encode(payload, limit)
        digest = hashlib.sha256(content).hexdigest()
        if previous and previous.get("sha256") == digest:
            self._blob(directory, previous)
            return previous
        filename = prefix + "-" + uuid.uuid4().hex + ".json"
        self._write(directory, filename, content)
        return {"file": filename, "sha256": digest, "bytes": len(content)}

    def _renew(self, manifest):
        now = max(self._now(), manifest.get("last_used", 0))
        manifest.update(last_used=now, expires_at=now + self.retention_seconds)

    def _save_manifest(self, directory, manifest):
        receipts = [manifest.get("source"), manifest.get("curriculum"), manifest.get("baseline"),
                    manifest.get("draft"), manifest.get("task")]
        receipts += list(manifest["stages"].values()) + list(manifest["task_parts"].values())
        manifest["total_bytes"] = sum(row.get("bytes", 0) for row in receipts if isinstance(row, dict))
        self._write(directory, "manifest.json", self._encode(manifest, self.MAX_MANIFEST_BYTES))

    def _metadata(self, manifest):
        keys = ("project_id", "filename", "title", "source_sha256", "source_bytes", "revision",
                "created_at", "last_used", "expires_at", "status", "task_id", "options",
                "curriculum_sha256", "question_count", "total_bytes")
        result = {key: deepcopy(manifest.get(key)) for key in keys}
        result.update(has_draft=bool(manifest.get("draft")), has_baseline=bool(manifest.get("baseline")),
                      stage_count=len(manifest["stages"]), last_used_at=manifest["last_used"])
        return result

    def _asset_urls(self, payload):
        urls = set()
        def visit(value):
            if isinstance(value, str):
                for candidate in _ASSET.findall(value):
                    if self._valid_asset(candidate):
                        urls.add(candidate)
            elif isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)
        visit(payload)
        return urls

    @staticmethod
    def _valid_asset(value):
        return (isinstance(value, str) and len(value) <= 4096 and bool(_ASSET.fullmatch(value))
                and not any(char in value for char in ("%", "?", "#", "\x00"))
                and all(part not in {"", ".", ".."} for part in value.split("/")[1:]))

    def _register(self, manifest, payload):
        manifest["temp_assets"] = sorted(set(manifest["temp_assets"]) | self._asset_urls(payload))

    def _retire(self, directory, before, after):
        """Only remove superseded owned blobs after the manifest commit succeeds."""
        live = set()
        def filenames(value):
            if isinstance(value, dict):
                if isinstance(value.get("file"), str):
                    yield value["file"]
                for child in value.values():
                    yield from filenames(child)
            elif isinstance(value, list):
                for child in value:
                    yield from filenames(child)
        live.update(filenames(after))
        for filename in set(filenames(before)) - live:
            if re.fullmatch(r"(?:stage|draft|task)-[0-9a-f]{32}\.json", filename):
                path = directory / filename
                try:
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
                except OSError:
                    pass

    def create(self, source_bytes, filename, options, curriculum_snapshot, task_id=None):
        self._assert_root()
        if not isinstance(source_bytes, bytes) or not source_bytes or len(source_bytes) > self.MAX_SOURCE_BYTES:
            raise DocumentProjectError("工程原文件为空或超过保存上限。")
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 512
                or any(char in filename for char in ("/", "\\", "\x00", "\n", "\r"))):
            raise DocumentProjectError("工程原文件名称无效。")
        options_bytes = self._encode(options, 32 * 1024)
        curriculum = self._encode(curriculum_snapshot)
        if task_id is not None:
            self._task_id(task_id)
        project_id = str(uuid.uuid4())
        now = self._now()
        directory = self.root / project_id
        directory.mkdir(mode=0o700)
        manifest = {"schema": 1, "project_id": project_id, "filename": filename,
                    "title": Path(filename).stem, "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "source_bytes": len(source_bytes), "revision": 0, "created_at": now,
                    "last_used": now, "expires_at": now + self.retention_seconds, "status": "created",
                    "task_id": None, "options": json.loads(options_bytes),
                    "curriculum_sha256": hashlib.sha256(curriculum).hexdigest(),
                    "question_count": 0, "stages": {}, "task_parts": {}, "temp_assets": []}
        # A failed creation remains visibly incomplete rather than risking the
        # deletion of files whose ownership cannot be proved after a failure.
        self._write(directory, "source.bin", source_bytes)
        self._write(directory, "curriculum.json", curriculum)
        manifest["source"] = {"file": "source.bin", "sha256": manifest["source_sha256"], "bytes": len(source_bytes)}
        manifest["curriculum"] = {"file": "curriculum.json", "sha256": manifest["curriculum_sha256"], "bytes": len(curriculum)}
        if task_id is not None:
            manifest["task_id"] = task_id
        self._save_manifest(directory, manifest)
        return self._metadata(manifest)

    def get(self, project_id):
        with self._locked(project_id) as directory:
            return self._metadata(self._manifest(directory))

    def list(self, limit=100, include_expired=False):
        self._assert_root()
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise DocumentProjectError("工程列表数量无效。")
        result = []
        now = self._now()
        for path in self.root.iterdir():
            if not _ID.fullmatch(path.name):
                continue
            try:
                metadata = self.get(path.name)
            except ProjectConflict:
                continue
            except (ProjectCorrupt, ProjectUnsafePath):
                metadata = {"project_id": path.name, "title": path.name, "status": "corrupt",
                            "read_only": True, "recovery_warning": "工程状态异常，已保留文件。", "last_used": 0}
            except ProjectNotFound:
                continue
            if include_expired or metadata.get("status") == "corrupt" or metadata["expires_at"] > now:
                result.append(metadata)
        return sorted(result, key=lambda row: row["last_used"], reverse=True)[:limit]

    def find_existing(self, source_sha256):
        if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise DocumentProjectError("原文件指纹无效。")
        return [row for row in self.list(limit=10_000) if row.get("source_sha256") == source_sha256]

    def load_source(self, project_id):
        with self._locked(project_id) as directory:
            return self._blob(directory, self._manifest(directory)["source"])

    def load_curriculum(self, project_id):
        with self._locked(project_id) as directory:
            return self._json_blob(directory, self._manifest(directory)["curriculum"])

    def touch(self, project_id):
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            self._renew(manifest)
            self._save_manifest(directory, manifest)
            return self._metadata(manifest)

    def _stage_name(self, name):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ProjectUnsafePath("工程阶段名称无效。")

    def _fingerprint(self, value):
        if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(ord(char) < 32 for char in value):
            raise DocumentProjectError("阶段输入指纹无效。")

    def save_stage(self, project_id, name, payload, input_fingerprint, complete=True):
        self._stage_name(name)
        self._fingerprint(input_fingerprint)
        if type(complete) is not bool:
            raise DocumentProjectError("阶段完成状态无效。")
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            before = deepcopy(manifest)
            receipt = self._put_blob(directory, payload, "stage", manifest["stages"].get(name))
            receipt = {**receipt, "input_fingerprint": input_fingerprint, "complete": complete}
            manifest["stages"][name] = receipt
            self._register(manifest, payload)
            self._renew(manifest)
            self._save_manifest(directory, manifest)
            self._retire(directory, before, manifest)
            return deepcopy(receipt)

    def load_stage(self, project_id, name, input_fingerprint):
        self._stage_name(name)
        self._fingerprint(input_fingerprint)
        with self._locked(project_id) as directory:
            receipt = self._manifest(directory)["stages"].get(name)
            if not receipt or receipt.get("complete") is not True or receipt.get("input_fingerprint") != input_fingerprint:
                return None
            return self._json_blob(directory, receipt)

    def _validate_draft(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
            raise DocumentProjectError("工程工作稿必须包含题目列表。")
        if len(payload["questions"]) > self.MAX_QUESTIONS or any(not isinstance(q, dict) for q in payload["questions"]):
            raise DocumentProjectError("工程题目列表无效或超过上限。")
        title = payload.get("paperTitle", "")
        if not isinstance(title, str) or len(title) > 1000:
            raise DocumentProjectError("工程试卷标题无效。")
        self._encode(payload, self.MAX_DRAFT_BYTES)

    def save_baseline(self, project_id, payload, input_fingerprint=None):
        self._validate_draft(payload)
        if input_fingerprint is not None:
            self._fingerprint(input_fingerprint)
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            if manifest.get("baseline"):
                existing = self._json_blob(directory, manifest["baseline"])
                if existing != payload:
                    raise ProjectConflict("工程原始拆题基线已保存，不能覆盖。")
                self._renew(manifest)
                self._save_manifest(directory, manifest)
                return deepcopy(existing)
            receipt = self._put_blob(directory, payload, "baseline", limit=self.MAX_DRAFT_BYTES)
            if input_fingerprint is not None:
                receipt["input_fingerprint"] = input_fingerprint
            manifest["baseline"] = receipt
            manifest["question_count"] = len(payload["questions"])
            self._register(manifest, payload)
            self._renew(manifest)
            self._save_manifest(directory, manifest)
            return deepcopy(payload)

    def get_baseline(self, project_id):
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            return self._json_blob(directory, manifest["baseline"]) if manifest.get("baseline") else None

    def get_draft(self, project_id):
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            receipt = manifest.get("draft") or manifest.get("baseline")
            payload = (self._json_blob(directory, receipt) if receipt else
                       {"questions": [], "paperTitle": manifest["title"], "pageImages": [], "pageNumbers": [], "diagnostics": {}})
            return {"revision": manifest["revision"], "payload": payload, "metadata": self._metadata(manifest)}

    def save_draft(self, project_id, payload, expected_revision):
        self._validate_draft(payload)
        if type(expected_revision) is not int or expected_revision < 0:
            raise DocumentProjectError("工作稿修订号无效。")
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            if manifest["revision"] != expected_revision:
                raise ProjectConflict("工程工作稿已被更新，请保留当前编辑并重新载入。", current_revision=manifest["revision"])
            before = deepcopy(manifest)
            manifest["draft"] = self._put_blob(directory, payload, "draft", manifest.get("draft"), limit=self.MAX_DRAFT_BYTES)
            manifest["revision"] += 1
            manifest["title"] = payload.get("paperTitle", manifest["title"])
            manifest["question_count"] = len(payload["questions"])
            self._register(manifest, payload)
            self._renew(manifest)
            self._save_manifest(directory, manifest)
            self._retire(directory, before, manifest)
            return {"revision": manifest["revision"], "payload": deepcopy(payload), "metadata": self._metadata(manifest)}

    def _task_id(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", task_id):
            raise DocumentProjectError("工程任务标识无效。")

    def bind_task(self, project_id, task_id):
        self._task_id(task_id)
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            before = deepcopy(manifest)
            if manifest.get("task_id") not in (None, task_id):
                manifest.pop("task", None)
                manifest["task_parts"] = {}
            manifest["task_id"] = task_id
            self._renew(manifest)
            self._save_manifest(directory, manifest)
            self._retire(directory, before, manifest)
            return self._metadata(manifest)

    def sync_task(self, project_id, task_snapshot):
        if not isinstance(task_snapshot, dict):
            raise DocumentProjectError("工程任务快照无效。")
        task_id = task_snapshot.get("task_id")
        if task_id is not None:
            self._task_id(task_id)
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            if task_id and manifest.get("task_id") not in (None, task_id):
                raise ProjectConflict("迟到任务与当前工程绑定不一致，未覆盖工程。")
            before = deepcopy(manifest)
            if task_id:
                manifest["task_id"] = task_id
            small = {key: value for key, value in task_snapshot.items() if key in _TASK_SMALL}
            manifest["task"] = self._put_blob(directory, small, "task", manifest.get("task"))
            for key in _TASK_PARTS & task_snapshot.keys():
                payload = task_snapshot[key]
                manifest["task_parts"][key] = self._put_blob(directory, payload, "stage", manifest["task_parts"].get(key))
                self._register(manifest, payload)
            status = task_snapshot.get("status")
            if isinstance(status, str) and len(status) <= 64:
                manifest["status"] = status
            if not manifest.get("draft") and isinstance(task_snapshot.get("data"), list):
                manifest["question_count"] = len(task_snapshot["data"])
            self._renew(manifest)
            self._save_manifest(directory, manifest)
            self._retire(directory, before, manifest)
            return self._metadata(manifest)

    def load_task(self, project_id):
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            result = self._json_blob(directory, manifest["task"]) if manifest.get("task") else {}
            for key, receipt in manifest["task_parts"].items():
                result[key] = self._json_blob(directory, receipt)
            return result

    def register_assets(self, project_id, assets):
        if not isinstance(assets, (list, tuple, set)) or any(not self._valid_asset(url) for url in assets):
            raise DocumentProjectError("工程登记图片路径无效。")
        with self._locked(project_id) as directory:
            manifest = self._manifest(directory)
            manifest["temp_assets"] = sorted(set(manifest["temp_assets"]) | set(assets))
            self._renew(manifest)
            self._save_manifest(directory, manifest)
            return self._metadata(manifest)

    def asset_urls(self, project_id):
        """Read only the manifest's asset registry, without loading checkpoints."""
        with self._locked(project_id) as directory:
            return set(self._manifest(directory)["temp_assets"])

    def _verify_all(self, directory, manifest):
        for path in directory.iterdir():
            if not _FILE.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                raise ProjectCorrupt("工程包含不明文件或符号链接，已保留。")
        for key in ("source", "curriculum", "baseline", "draft", "task"):
            if manifest.get(key):
                self._blob(directory, manifest[key])
        for receipt in [*manifest["stages"].values(), *manifest["task_parts"].values()]:
            self._blob(directory, receipt)
        if any(not self._valid_asset(url) for url in manifest["temp_assets"]):
            raise ProjectCorrupt("工程图片引用记录无效。")

    def referenced_assets(self, exclude_project_id=None):
        self._assert_root()
        trash = self.root / ".trash"
        self._safe_directory(trash)
        if any(trash.iterdir()):
            raise ProjectCorrupt("工程回收区有未完成清理的文件，不能安全释放图片。")
        if exclude_project_id is not None:
            self._id(exclude_project_id)
        assets = set()
        for path in self.root.iterdir():
            if not _ID.fullmatch(path.name) or path.name == exclude_project_id:
                continue
            with self._locked(path.name) as directory:
                manifest = self._manifest(directory)
                self._verify_all(directory, manifest)
                assets.update(manifest["temp_assets"])
        return assets

    def _delete(self, project_id, active_task_ids, *, expired_only, wait=False):
        with self._locked(project_id, deleting=True, wait=wait) as directory:
            manifest = self._manifest(directory)
            # A dispatch may bind a new task while explicit deletion waits for
            # its shared lease. Resolve the live set only under our exclusive
            # lease, so a pre-wait HTTP snapshot cannot authorize that deletion.
            active = active_task_ids() if callable(active_task_ids) else active_task_ids
            active = {value for value in active if isinstance(value, str)}
            if manifest.get("task_id") in active:
                raise ProjectConflict("工程仍有活跃解析任务，不能删除。")
            if expired_only and manifest["expires_at"] > self._now():
                return None
            self._verify_all(directory, manifest)
            trash = self.root / ".trash"
            if trash.is_symlink() or not trash.is_dir():
                raise ProjectUnsafePath("工程清理目录无效。")
            destination = trash / (project_id + "-" + uuid.uuid4().hex)
            os.replace(directory, destination)
            # A failure after rename keeps the private files in .trash; uploads
            # are still owned by the caller's independent reference checks.
            try:
                shutil.rmtree(destination)
            except OSError as exc:
                # Put even a partly removed project back under its original ID.
                # If this also fails, referenced_assets() fails closed on trash.
                try:
                    os.replace(destination, directory)
                except OSError:
                    pass
                raise ProjectCorrupt("工程清理未完成，已保留剩余文件与图片。") from exc
            return {"deleted_projects": [project_id], "temp_assets": list(manifest["temp_assets"]),
                    "retained_corrupt_projects": []}

    def delete(self, project_id, active_task_ids=(), *, wait=False):
        """Explicit HTTP callers may wait briefly; maintenance stays fail-fast.

        Pass a callable for active_task_ids when wait=True so the active-task
        check reflects dispatches that finished while acquiring the lease.
        """
        return self._delete(project_id, active_task_ids, expired_only=False, wait=wait)

    def cleanup(self, active_task_ids=()):
        self._assert_root()
        active = {value for value in active_task_ids if isinstance(value, str)}
        result = {"deleted_projects": [], "temp_assets": [], "retained_corrupt_projects": [], "busy_projects": []}
        for path in self.root.iterdir():
            if not _ID.fullmatch(path.name):
                continue
            try:
                receipt = self._delete(path.name, active, expired_only=True)
                if receipt:
                    result["deleted_projects"].extend(receipt["deleted_projects"])
                    result["temp_assets"].extend(receipt["temp_assets"])
            except ProjectConflict:
                result["busy_projects"].append(path.name)
            except (ProjectCorrupt, ProjectUnsafePath):
                result["retained_corrupt_projects"].append(path.name)
            except ProjectNotFound:
                continue
        result["temp_assets"] = sorted(set(result["temp_assets"]))
        return result


@contextmanager
def _null_context():
    yield
