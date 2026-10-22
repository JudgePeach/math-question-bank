"""Small, standard-library-only write checks for portable local storage.

Probe only private disposable files. Existing databases and lock/log files are
opened without truncation or writes; permissions and data locations never change.
Do not run these disk operations on every heartbeat or after a successful commit.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import tempfile
import time

from mathbank.paths import PROJECT_ROOT


class StorageAccessError(OSError):
    def __init__(self, path: Path, *, label: str, stage: str, cause: OSError):
        self.path, self.label, self.stage = Path(path), label, stage
        winerror = getattr(cause, "winerror", None)
        if winerror in {112, 39} or cause.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
            self.code, reason = "disk", "可用存储空间不足"
            advice = "请清理该磁盘空间后重试。"
        elif winerror in {32, 33}:
            self.code, reason = "file_in_use", "文件或目录正被其他程序占用"
            advice = "请等待文件释放后重试；若持续发生，检查同步或安全软件的占用记录。"
        elif winerror == 206 or cause.errno == errno.ENAMETOOLONG:
            self.code, reason = "path_too_long", "目录路径过长"
            advice = "请将整个 MathBank 文件夹移到较短的可写位置，保留数据库、图片和备份。"
        elif winerror == 5 or cause.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
            self.code, reason = "permission", "当前账户没有所需写入权限，或该位置受到保护"
            advice = "请检查该目录的写入权限，或将整个 MathBank 文件夹移到当前账户可写的位置，保留数据库、图片和备份。"
        else:
            self.code, reason = "storage", "存储位置无法完成所需文件操作"
            advice = "请检查目录、存储设备和读写权限后重试。"
        actions = {"create": "创建文件或子目录", "write": "写入文件", "flush": "保存文件",
                   "replace": "替换文件", "read": "回读文件", "delete": "删除临时文件",
                   "check_file": "以可写方式打开现有文件"}
        self.safe_message = f"{label}无法{actions.get(stage, '写入')}：{reason}。{advice}"
        super().__init__(cause.errno or errno.EIO, self.safe_message)
        if winerror is not None:
            self.winerror = winerror


def _retry_file_operation(operation):
    # Antivirus/indexers can briefly hold newly created probe files on Windows.
    # Keep the same short, bounded sharing-violation budget as normal writes.
    delays = (0.05, 0.1, 0.2, 0.4)
    for attempt in range(len(delays) + 1):
        try:
            return operation()
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in {32, 33} or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def check_directory_access(path: Path, *, label: str, create: bool = True,
                           require_subdirectory: bool = False) -> None:
    """Actually test create/write/fsync/replace/read/delete with random files.

    Windows directory read-only flags and os.access cannot establish ACL write
    permission. No existing file is replaced, no chmod/ACL changes are attempted.
    """
    directory, owned, stage = Path(path), [], "create"
    try:
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        if not stat.S_ISDIR(directory.stat().st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "Storage directory is unavailable")
        child = None
        if require_subdirectory:
            child = Path(tempfile.mkdtemp(prefix=".mathbank-write-check-", dir=directory))
            owned.append(child)
        descriptor, name = tempfile.mkstemp(prefix=".mathbank-write-check-", suffix=".tmp", dir=directory)
        source = Path(name)
        owned.append(source)
        marker = b"MathBank disposable write check\n"
        with os.fdopen(descriptor, "w+b") as handle:
            stage = "write"
            handle.write(marker)
            stage = "flush"
            handle.flush()
            os.fsync(handle.fileno())
        stage = "create"
        descriptor, name = tempfile.mkstemp(prefix=".mathbank-write-check-", suffix=".tmp", dir=directory)
        destination = Path(name)
        owned.append(destination)
        os.close(descriptor)
        stage = "replace"
        _retry_file_operation(lambda: os.replace(source, destination))
        stage = "read"
        if destination.read_bytes() != marker:
            raise OSError(errno.EIO, "Storage write check readback differs")
        stage = "delete"
        _retry_file_operation(destination.unlink)
        if child is not None:
            _retry_file_operation(child.rmdir)
    except OSError as exc:
        raise StorageAccessError(directory, label=label, stage=stage, cause=exc) from exc
    finally:
        # A primary failure must retain its real operation and error code.
        for temporary in reversed(owned):
            try:
                if temporary.is_dir():
                    temporary.rmdir()
                else:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass


def check_file_write_access(path: Path, *, label: str) -> None:
    """Check an existing file's independent ACL/read-only flag without writes."""
    target = Path(path)
    try:
        try:
            target.stat()
        except FileNotFoundError:
            return
        def open_existing():
            with target.open("r+b"):
                pass
        _retry_file_operation(open_existing)
    except OSError as exc:
        raise StorageAccessError(target, label=label, stage="check_file", cause=exc) from exc


def check_database_write_access(path: Path, *, label: str = "题库数据库") -> None:
    target = Path(path)
    check_directory_access(target.parent, label=label + "所在目录")
    check_file_write_access(target, label=label + "文件")
    for suffix in ("-wal", "-shm"):
        check_file_write_access(Path(str(target) + suffix), label=label + suffix + "日志文件")


def check_startup_storage(root: Path = PROJECT_ROOT) -> None:
    """Preflight before locks, environment preparation and database startup."""
    root = Path(root)
    check_database_write_access(root / "math_question_bank.db", label=f"题库数据库（{root / 'math_question_bank.db'}）")
    for relative, purpose in ((".system_generated", "程序运行目录"),
                              ("static/uploads", "题目图片目录"),
                              ("data_backup", "备份与同步导出目录"),
                              ("data_backup/snapshots", "完整备份目录")):
        check_directory_access(root / relative, label=f"{purpose}（{root / relative}）",
                               require_subdirectory=relative == "data_backup/snapshots")
    for name in ("launcher.lock", "runtime.lock", "server.log"):
        check_file_write_access(root / ".system_generated" / name, label=f"程序运行文件（.system_generated/{name}）")
