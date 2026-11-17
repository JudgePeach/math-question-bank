"""Opt-in external backing for new immutable assets, with a local read cache.

Logical paths and security boundaries stay inside this checkout. Only this
module translates its private catalog to content-addressed external objects.
The business database, task metadata, locks and latest backups stay local.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import stat
import subprocess
import threading
import time
import uuid

from .paths import (PROJECT_ROOT, UPLOADS_DIR, DOCUMENT_PROJECTS_DIR,
                    DOCUMENT_TASKS_DIR, TIERED_STORAGE_DIR, TIERED_STORAGE_CONFIG,
                    RECEIVED_DOCUMENTS_DIR)


class TieredStorageError(ValueError):
    pass


def mounted_volume(volume_uuid):
    """Resolve a macOS volume by identity, including after a volume rename."""
    if os.name != "posix" or not Path("/usr/sbin/diskutil").is_file():
        return None
    try:
        result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", volume_uuid],
                                capture_output=True, timeout=5, check=True)
        info = plistlib.loads(result.stdout)
        mount = Path(info.get("MountPoint", ""))
        if (str(info.get("VolumeUUID", "")).lower() == volume_uuid.lower()
                and mount.is_absolute() and os.path.ismount(mount)):
            return mount
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _identity(path):
    value = path.lstat()
    return f"{value.st_dev}:{value.st_ino}"


def _regular(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
        raise TieredStorageError("资料文件不是安全的普通文件。")
    return value


def _fingerprint(path):
    value = _regular(path)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _sync(directory):
    if os.name == "posix":
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _safe_path(anchor, relative):
    relative = Path(relative)
    if (relative.is_absolute() or not relative.parts
            or any(p in {"", ".", ".."} for p in relative.parts)):
        raise TieredStorageError("资料存储记录中的路径无效。")
    current = Path(anchor)
    if current.is_symlink():
        raise TieredStorageError("资料存储路径不能经过符号链接。")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise TieredStorageError("资料存储路径不能经过符号链接。")
    return current


def _mkdir_on_volume(mount, directory, expected_identity):
    """Use an open directory anchor; never recreate an unmounted volume label."""
    parts = Path(directory).relative_to(mount).parts
    descriptor = os.open(mount, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                         | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        current = Path(mount).lstat()
        identity = (info.st_dev, info.st_ino)
        if identity != expected_identity or identity != (current.st_dev, current.st_ino):
            raise TieredStorageError("数据盘挂载状态已变化，已保留内盘原件。")
        for part in parts:
            if part in {"", ".", ".."}:
                raise TieredStorageError("资料目录无效。")
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


class TieredStorage:
    def __init__(self, *, root=PROJECT_ROOT, state_dir=TIERED_STORAGE_DIR,
                 config=None, volume_provider=mounted_volume, clock=time.time):
        self.root = Path(root).absolute()
        self.state_dir = Path(state_dir).absolute()
        self.config = dict(config or {})
        volume_uuid = self.config.get("volume_uuid", "")
        if not isinstance(volume_uuid, str) or not re.fullmatch(r"[A-Fa-f0-9-]{36}", volume_uuid):
            raise TieredStorageError("外置资料配置中的磁盘标识无效。")
        self.volume_provider = volume_provider
        self.clock = clock
        self.grace = max(60, int(self.config.get("cache_seconds", 3600)))
        self.stage_seconds = max(0, int(self.config.get("stage_seconds", 120)))
        self.external_relative = self.config.get("directory", "业务资料/MathBank")
        _safe_path(Path("/"), self.external_relative)
        self.lock = threading.RLock()
        self.activity_lock = threading.RLock()
        self.key_locks = {}
        self.stop = threading.Event()
        self.thread = None
        self.active_requests = 0
        self.busy = lambda: False
        self._mount_snapshot = None
        self.last_error = None
        if self.state_dir.is_symlink():
            raise TieredStorageError("资料索引目录不能是符号链接。")
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.index = self.state_dir / "catalog.sqlite"
        if self.index.is_symlink():
            raise TieredStorageError("资料索引不能是符号链接。")
        # Never silently recreate a corrupt/missing initialized catalog. Its
        # baseline determines which historical files must remain untouched.
        if not self.index.is_file():
            raise TieredStorageError("资料索引缺失；请恢复索引，未自动迁出任何文件。")
        with self._db() as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise TieredStorageError("资料索引完整性校验失败，已停止自动迁出。")
            if db.execute("SELECT value FROM meta WHERE key='schema'").fetchone() != ("1",):
                raise TieredStorageError("资料索引版本无效。")

    @contextmanager
    def _db(self):
        with self.lock:
            if self.index.is_symlink():
                raise TieredStorageError("资料索引不能是符号链接。")
            db = sqlite3.connect(self.index.as_uri() + "?mode=rw", uri=True, timeout=10)
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def eligible(self, path):
        try:
            relative = Path(path).absolute().relative_to(self.root)
        except ValueError:
            return None
        parts = relative.parts
        image = (len(parts) == 3 and parts[:2] == ("static", "uploads")
                 and Path(parts[-1]).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"})
        project = (len(parts) == 4 and parts[:2] == (".system_generated", "document_projects")
                   and parts[-1] == "source.bin" and re.fullmatch(r"[0-9a-f-]{36}", parts[2]))
        task = (len(parts) == 3 and parts[:2] == (".system_generated", "document_tasks")
                and re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.source", parts[-1]))
        received = (len(parts) == 3 and parts[:2] == (".system_generated", "received_documents")
                    and re.fullmatch(r"[a-f0-9]{64}\.(?:pdf|docx|tex)", parts[-1]))
        return relative.as_posix() if image or project or task or received else None

    def _candidates(self):
        for pattern in ("static/uploads/*", ".system_generated/document_projects/*/source.bin",
                        ".system_generated/document_tasks/*.source", ".system_generated/received_documents/*"):
            for path in self.root.glob(pattern):
                key = self.eligible(path)
                if key:
                    yield key, _safe_path(self.root, key)

    def _external(self):
        mount = self.volume_provider(self.config["volume_uuid"])
        if mount is None:
            return None
        mount = Path(mount)
        # No mkdir at an absent mount point; never identify a volume by name.
        if not mount.is_dir() or mount.is_symlink():
            return None
        info = mount.lstat()
        self._mount_snapshot = (mount, (info.st_dev, info.st_ino))
        return _safe_path(mount, self.external_relative)

    def _mkdir_external(self, directory):
        snapshot = self._mount_snapshot
        if snapshot is None:
            raise TieredStorageError("资料盘未连接。")
        _mkdir_on_volume(snapshot[0], directory, snapshot[1])

    def _object(self, external, checksum):
        if not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise TieredStorageError("资料索引中的校验值无效。")
        return _safe_path(external, f"objects/{checksum[:2]}/{checksum}")

    def _audit(self, action, key, checksum=None):
        path = self.state_dir / "audit.jsonl"
        if path.is_symlink():
            raise TieredStorageError("资料审计记录不能是符号链接。")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": self.clock(), "action": action,
                                     "path": key, "sha256": checksum}, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _key_lock(self, key):
        with self.lock:
            return self.key_locks.setdefault(key, threading.RLock())

    def hydrate(self, path):
        key = self.eligible(path)
        if key is None:
            return False
        path = _safe_path(self.root, key)
        if path.exists():
            _regular(path)
            try:
                with self._db() as db:
                    db.execute("UPDATE entries SET touched=? WHERE path=? AND state='backed'", (self.clock(), key))
            except (sqlite3.Error, OSError, TieredStorageError) as exc:
                self.last_error = type(exc).__name__
            return False  # Local readable files never depend on catalog health.
        with self._key_lock(key):
            with self._db() as db:
                row = db.execute("SELECT parent,sha256,size,state FROM entries WHERE path=?", (key,)).fetchone()
                if not row or row[3] not in {"backed", "retained"}:
                    return False
                if not path.parent.is_dir() or _identity(path.parent) != row[0]:
                    return False
                db.execute("UPDATE entries SET touched=? WHERE path=?", (self.clock(), key))
            if path.exists():
                _regular(path)
                return False
            external = self._external()
            if external is None:
                raise TieredStorageError("此资料已保存到外接数据盘，请连接数据盘后重试；其他题目和新资料仍可使用。")
            source = self._object(external, row[1])
            if not source.is_file() or _regular(source).st_size != row[2] or _hash(source) != row[1]:
                raise TieredStorageError("外盘资料缺失或 SHA-256 校验失败，未生成替代文件。")
            temporary = path.with_name(".tiered-" + uuid.uuid4().hex + ".tmp")
            try:
                self._copy_verified(source, temporary, row[1])
                with self._db() as db:
                    current = db.execute("SELECT parent,sha256,state FROM entries WHERE path=?", (key,)).fetchone()
                    if current != (row[0], row[1], row[3]) or _identity(path.parent) != row[0]:
                        raise TieredStorageError("资料记录已变化，未恢复旧文件。")
                    try:
                        os.link(temporary, path)
                        _sync(path.parent)
                    except FileExistsError:
                        pass
                    db.execute("UPDATE entries SET touched=?,state='backed' WHERE path=?", (self.clock(), key))
                self._audit("cache_restored", key, row[1])
            finally:
                temporary.unlink(missing_ok=True)
            return True

    def known(self, path):
        key = self.eligible(path)
        if not key:
            return False
        with self._db() as db:
            row = db.execute("SELECT parent,state FROM entries WHERE path=?", (key,)).fetchone()
            return bool(row and row[1] == "backed" and Path(path).parent.is_dir()
                        and _identity(Path(path).parent) == row[0])

    def readable_path(self, path):
        """Read-only view; never restore retired assets or create local files."""
        key = self.eligible(path)
        if not key:
            return Path(path)
        path = _safe_path(self.root, key)
        if path.is_file():
            _regular(path)
            return path
        with sqlite3.connect(self.index.as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT parent,sha256,size,state FROM entries WHERE path=?", (key,)).fetchone()
        if (not row or row[3] != "backed" or not path.parent.is_dir()
                or _identity(path.parent) != row[0]):
            return path
        external = self._external()
        if external is None:
            if path.is_file():
                _regular(path)
                return path
            raise TieredStorageError("此图片保存在外接数据盘，请连接数据盘后重试。")
        source = self._object(external, row[1])
        if not source.is_file() or _regular(source).st_size != row[2] or _hash(source) != row[1]:
            raise TieredStorageError("外盘图片缺失或 SHA-256 校验失败。")
        return source

    def forget(self, path):
        key = self.eligible(path)
        if key:
            with self._db() as db:
                db.execute("UPDATE entries SET state='retired' WHERE path=?", (key,))
                token = uuid.uuid4().hex
                db.execute("INSERT INTO pending_receipts VALUES(?,?)", (token, json.dumps({
                    "schema": 1, "action": "retired", "path": key, "time": self.clock()})))
                self._audit("retired", key)

    def retain_external(self, path):
        """Retire a cold image without needing its disk; true reads may restore it."""
        key = self.eligible(path)
        if not key or Path(path).exists():
            return False
        with self._db() as db:
            row = db.execute("SELECT parent,state FROM entries WHERE path=?", (key,)).fetchone()
            if (not row or row[1] != "backed" or not Path(path).parent.is_dir()
                    or _identity(Path(path).parent) != row[0]):
                return False
            db.execute("UPDATE entries SET state='retained' WHERE path=?", (key,))
            db.execute("INSERT INTO pending_receipts VALUES(?,?)", (uuid.uuid4().hex,
                json.dumps({"schema": 1, "action": "retained", "path": key, "time": self.clock()})))
        self._audit("retained_external", key)
        return True

    def _receipt(self, external, token, payload):
        directory = _safe_path(external, "receipts")
        self._mkdir_external(directory)
        target = directory / (token + ".json")
        if target.exists():
            return
        temporary = directory / (".receipt-" + uuid.uuid4().hex)
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
            _sync(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def hydrate_all(self, directory):
        directory = Path(directory).absolute()
        with self._db() as db:
            keys = [r[0] for r in db.execute("SELECT path FROM entries WHERE state IN ('backed','retained')")]
        for key in keys:
            path = self.root / key
            if path.is_relative_to(directory):
                self.hydrate(path)

    def restore_local(self):
        """Drain external-only files before code/config rollback, service stopped."""
        with self._db() as db:
            rows = list(db.execute("SELECT path,parent,sha256 FROM entries WHERE state IN ('backed','retained')"))
        restored = 0
        for key, parent, checksum in rows:
            path = _safe_path(self.root, key)
            if not path.parent.is_dir() or _identity(path.parent) != parent:
                continue
            self.hydrate(path)
            _regular(path)
            if _hash(path) != checksum:
                raise TieredStorageError("本地资料与外置索引内容不同，未停用外置存储；请先核对更新。")
            restored += 1
        return restored

    def save_received(self, content, filename, document_type):
        extension = {"pdf": ".pdf", "docx": ".docx", "tex": ".tex"}.get(document_type)
        if not extension:
            return
        checksum = hashlib.sha256(content).hexdigest()
        directory = _safe_path(self.root, ".system_generated/received_documents")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / (checksum + extension)
        with self._key_lock(str(path)):
            # Every new upload is durable locally even if an identical older
            # object is currently external-only and that disk is disconnected.
            if path.exists():
                _regular(path)
                if _hash(path) != checksum:
                    raise TieredStorageError("已保存的原文档校验失败，未覆盖。")
            else:
                temporary = directory / (".received-" + uuid.uuid4().hex + ".tmp")
                try:
                    with temporary.open("xb") as stream:
                        os.chmod(temporary, 0o600)
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, path)
                    _sync(directory)
                finally:
                    temporary.unlink(missing_ok=True)
            # Metadata stays internal, separate from transient OCR task status.
            metadata = directory / "catalog.jsonl"
            if metadata.is_symlink():
                raise TieredStorageError("原文档记录不能是符号链接。")
            with metadata.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": self.clock(), "filename": str(filename),
                    "file": path.name, "sha256": checksum, "bytes": len(content)}, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def received_documents(self):
        directory = self.root / ".system_generated/received_documents"
        return directory

    def _copy_verified(self, source, target, checksum):
        before = _fingerprint(source)
        with source.open("rb") as inp, target.open("xb") as out:
            os.chmod(target, 0o600)
            for block in iter(lambda: inp.read(1024 * 1024), b""):
                if self.stop.is_set() and threading.current_thread() is self.thread:
                    raise InterruptedError("资料迁出随服务正常停止")
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if before != _fingerprint(source) or _hash(target) != checksum:
            raise TieredStorageError("资料在复制期间变化或校验失败，已保留内盘原件。")

    def cycle(self, *, allow_evict=True):
        """Copy/hash outside global locks; finish each eviction under short locks."""
        external = self._external()
        if external is None:
            return
        try:
            self._mkdir_external(external)
        except (OSError, TieredStorageError) as exc:
            self.last_error = type(exc).__name__
            return
        self.last_error = None
        with self._db() as db:
            pending = list(db.execute("SELECT id,payload FROM pending_receipts"))
        for token, payload in pending:
            self._receipt(external, token, json.loads(payload))
            with self._db() as db:
                db.execute("DELETE FROM pending_receipts WHERE id=?", (token,))
        now = self.clock()
        with self._db() as db:
            rows = {r[0]: r[1:] for r in db.execute("SELECT path,parent,sha256,state,touched,fingerprint FROM entries")}
        for key, path in self._candidates():
            if self.stop.is_set():
                break
            try:
                row = rows.get(key)
                if row and row[2] == "excluded":
                    continue
                if not path.is_file():
                    continue
                before = _fingerprint(path)
                parent = _identity(path.parent)
                if row and row[0] != parent:
                    with self._db() as db:
                        db.execute("UPDATE entries SET parent=?,state='excluded' WHERE path=?", (parent, key))
                    continue
                if row and row[2] == "excluded":
                    continue
                if now - path.stat().st_mtime < self.stage_seconds:
                    continue
                fingerprint = json.dumps(before)
                if row and row[4] == fingerprint and now - row[3] < self.grace:
                    continue
                checksum = _hash(path)
                if before != _fingerprint(path):
                    continue
                target = self._object(external, checksum)
                self._mkdir_external(target.parent)
                if target.exists():
                    if _regular(target).st_size != before[2] or _hash(target) != checksum:
                        raise TieredStorageError("外盘对象校验失败，已保留内盘原件。")
                else:
                    temporary = target.with_name(".copy-" + uuid.uuid4().hex + ".tmp")
                    try:
                        self._copy_verified(path, temporary, checksum)
                        try:
                            os.link(temporary, target)
                        except FileExistsError:
                            if _hash(target) != checksum:
                                raise TieredStorageError("外盘对象发生冲突，已保留内盘原件。")
                        _sync(target.parent)
                    finally:
                        temporary.unlink(missing_ok=True)
                # diskutil/copy/SHA are outside the DB/activity/lifecycle locks.
                if self._external() != external or before != _fingerprint(path):
                    continue
                touched = row[3] if row and row[1] == checksum and row[2] == "backed" else now
                self._receipt(external, uuid.uuid4().hex, {"schema": 1,
                    "action": "backed", "time": now, "path": key,
                    "sha256": checksum, "bytes": before[2]})
                with self._db() as db:
                    latest = db.execute("SELECT touched FROM entries WHERE path=?", (key,)).fetchone()
                    if latest and row:
                        touched = max(touched, latest[0])
                    db.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?,?,?)",
                               (key, parent, checksum, before[2], "backed", touched, now, fingerprint))
                self._audit("external_verified", key, checksum)
                if allow_evict and now - touched >= self.grace:
                    from .asset_lifecycle import ASSET_LIFECYCLE_LOCK
                    with ASSET_LIFECYCLE_LOCK:
                        key_lock = self._key_lock(key)
                        if not key_lock.acquire(blocking=False):
                            continue  # A same-file hydration can be slow; skip it.
                        try:
                            with self.activity_lock:
                                if self.active_requests or self.busy():
                                    continue
                                with self._db() as db:
                                    latest = db.execute("SELECT touched FROM entries WHERE path=?", (key,)).fetchone()
                                    if (latest and now-latest[0] >= self.grace
                                            and before == _fingerprint(path)):
                                        path.unlink()
                                        _sync(path.parent)
                                self._audit("cache_evicted", key, checksum)
                        finally:
                            key_lock.release()
            except (OSError, sqlite3.Error, TieredStorageError) as exc:
                self.last_error = type(exc).__name__

    def start(self, busy=lambda: False, interval=60):
        if self.thread is not None:
            return
        self.stop.clear()
        self.busy = busy
        def run():
            while not self.stop.wait(interval):
                try:
                    # Do not evict while requests or document workers may hold
                    # paths. Existing lifecycle synchronization remains intact.
                    self.cycle(allow_evict=not busy())
                except Exception as exc:
                    self.last_error = type(exc).__name__
        self.thread = threading.Thread(target=run, name="mathbank-tiered-storage", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=15)

    def status(self):
        try:
            with self._db() as db:
                counts = dict(db.execute("SELECT state,COUNT(*) FROM entries GROUP BY state"))
        except (sqlite3.Error, OSError, TieredStorageError) as exc:
            return {"enabled": True, "error": type(exc).__name__, "external_connected": False}
        return {"enabled": True, "external_connected": self._external() is not None,
                "catalog_counts": counts, "last_error": self.last_error,
                "cache_seconds": self.grace}


_STORAGE = None
_CONFIG_CHECKED = False
_STORAGE_ERROR = None


def get_storage():
    global _STORAGE, _CONFIG_CHECKED, _STORAGE_ERROR
    if not _CONFIG_CHECKED:
        _CONFIG_CHECKED = True
        if TIERED_STORAGE_CONFIG.is_file() and not TIERED_STORAGE_CONFIG.is_symlink():
            try:
                config = json.loads(TIERED_STORAGE_CONFIG.read_text(encoding="utf-8"))
                if config.get("enabled"):
                    _STORAGE = TieredStorage(config=config)
            except Exception as exc:
                _STORAGE_ERROR = type(exc).__name__
                print(f"[Storage] 外置资料索引未启用 ({type(exc).__name__})；自动迁出已停止。", flush=True)
    return _STORAGE


def storage_status():
    storage = get_storage()
    if storage:
        return storage.status()
    return {"enabled": False, "configured": TIERED_STORAGE_CONFIG.exists(),
            "error": _STORAGE_ERROR}


def hydrate(path):
    storage = get_storage()
    return storage.hydrate(path) if storage else False


def forget(path):
    storage = get_storage()
    if storage:
        storage.forget(path)


def readable_path(path):
    storage = get_storage()
    return storage.readable_path(path) if storage else Path(path)


def document_workers_busy(records):
    """Historical interrupted records do not pin the local cache forever."""
    futures = tuple(record.future for record in tuple(records.values()))
    return any(future is not None and not future.done() for future in futures)


class StorageActivityMiddleware:
    """Keep evictions outside complete HTTP response bodies, including streams."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        storage = get_storage()
        if not storage or scope["type"] != "http":
            return await self.app(scope, receive, send)
        with storage.activity_lock:
            storage.active_requests += 1
        try:
            return await self.app(scope, receive, send)
        finally:
            with storage.activity_lock:
                storage.active_requests -= 1


def initialize(*, root=PROJECT_ROOT, state_dir=TIERED_STORAGE_DIR, config_path=TIERED_STORAGE_CONFIG,
               volume_uuid, directory="业务资料/MathBank", cache_seconds=3600):
    """Explicit one-time baseline; existing data are not selected for migration."""
    root, state_dir, config_path = Path(root).absolute(), Path(state_dir).absolute(), Path(config_path).absolute()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    index = state_dir / "catalog.sqlite"
    if index.exists() or index.is_symlink() or config_path.exists():
        raise TieredStorageError("资料配置或索引已存在；未覆盖，请先检查现有配置。")
    config = {"enabled": True, "volume_uuid": volume_uuid, "directory": directory,
              "cache_seconds": cache_seconds, "stage_seconds": 120}
    with sqlite3.connect(index) as db:
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO meta VALUES('schema','1')")
        db.execute("CREATE TABLE entries(path TEXT PRIMARY KEY,parent TEXT NOT NULL,sha256 TEXT,size INTEGER,state TEXT NOT NULL,touched REAL,verified REAL,fingerprint TEXT)")
        db.execute("CREATE TABLE pending_receipts(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    storage = TieredStorage(root=root, state_dir=state_dir, config=config)
    with storage._db() as db:
        for key, path in storage._candidates():
            if path.is_file():
                _regular(path)
                db.execute("INSERT INTO entries VALUES(?,?,NULL,NULL,'excluded',0,0,NULL)", (key, _identity(path.parent)))
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with config_path.open("x", encoding="utf-8") as stream:
        os.chmod(config_path, 0o600)
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(config_path.parent)
    return storage
