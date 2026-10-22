"""Real disposable storage probes and failure boundaries; never use project data."""

import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from mathbank import storage_access as storage


def _failure(*, number=errno.EACCES, winerror=None):
    cause = OSError(number, "private diagnostic text", "/private/user/secret.db")
    if winerror is not None:
        cause.winerror = winerror
    return cause


def _assert_failure(error, cause, *, path, stage, code="permission", label="测试图片目录"):
    assert error.path == path
    assert error.stage == stage and error.code == code
    assert error.errno == cause.errno
    assert error.__cause__ is cause
    assert label in error.safe_message
    assert "private diagnostic text" not in str(error)
    assert "/private/user/secret.db" not in str(error)


def _os_proxy(**overrides):
    return SimpleNamespace(**{**vars(os), **overrides})


def _clear_probes(directory):
    for path in directory.glob(".mathbank-write-check-*"):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()


def test_real_chinese_directory_probe_cleans_up_and_preserves_existing_files(tmp_path):
    directory = tmp_path / "题库图片 空格"
    directory.mkdir()
    existing = directory / "保留.bin"
    existing.write_bytes(b"original\x00bytes")
    before = {path: stat.S_IMODE(path.stat().st_mode) for path in (directory, existing)}
    storage.check_directory_access(directory, label="测试图片目录")
    assert list(directory.iterdir()) == [existing]
    assert existing.read_bytes() == b"original\x00bytes"
    assert {path: stat.S_IMODE(path.stat().st_mode) for path in before} == before
    nested = directory / "新建" / "二级"
    storage.check_directory_access(nested, label="新建图片目录")
    assert nested.is_dir() and not list(nested.iterdir())


@pytest.mark.parametrize("failed_operation", ["mkdir", "mkdtemp", "first_file", "second_file"])
def test_creation_denial_reports_create_and_preserves_original_data(tmp_path, monkeypatch, failed_operation):
    directory = tmp_path / "图片"
    directory.mkdir()
    existing = directory / "keep.bin"
    existing.write_bytes(b"untouched")
    cause = _failure()
    original_mkdir, original_mkstemp = Path.mkdir, storage.tempfile.mkstemp
    calls = []

    def mkdir(path, *args, **kwargs):
        if path == directory:
            raise cause
        return original_mkdir(path, *args, **kwargs)

    def mkstemp(*args, **kwargs):
        calls.append(True)
        if len(calls) == (1 if failed_operation == "first_file" else 2):
            raise cause
        return original_mkstemp(*args, **kwargs)

    with monkeypatch.context() as patch:
        if failed_operation == "mkdir":
            patch.setattr(Path, "mkdir", mkdir)
        elif failed_operation == "mkdtemp":
            patch.setattr(storage.tempfile, "mkdtemp", lambda **_: (_ for _ in ()).throw(cause))
        else:
            patch.setattr(storage.tempfile, "mkstemp", mkstemp)
        with pytest.raises(storage.StorageAccessError) as caught:
            storage.check_directory_access(directory, label="测试图片目录",
                                           require_subdirectory=failed_operation == "mkdtemp")
    _assert_failure(caught.value, cause, path=directory, stage="create")
    assert list(directory.iterdir()) == [existing]
    assert existing.read_bytes() == b"untouched"


def test_existing_file_output_directory_does_not_require_creating_subdirectories(tmp_path, monkeypatch):
    cause = _failure()
    monkeypatch.setattr(storage.tempfile, "mkdtemp", lambda **_: (_ for _ in ()).throw(cause))
    # Windows can grant creation of files while denying creation of folders.
    # ZIP/JSON/WAL/image outputs in an existing directory only need file rights.
    storage.check_directory_access(tmp_path, label="文件输出目录")
    assert not list(tmp_path.iterdir())
    with pytest.raises(storage.StorageAccessError) as caught:
        storage.check_directory_access(tmp_path, label="任务目录", require_subdirectory=True)
    assert caught.value.code == "permission" and caught.value.stage == "create"


@pytest.mark.parametrize("failed_operation,stage", [("write", "write"), ("flush", "flush"), ("fsync", "flush")])
def test_write_and_flush_denial_report_real_stage_and_release_handles(tmp_path, monkeypatch, failed_operation, stage):
    directory = tmp_path / "题库"
    directory.mkdir()
    cause = _failure()
    handles = []

    class FaultyFile:
        def __init__(self, handle):
            self.handle = handle
            handles.append(handle)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return self.handle.__exit__(*exc)
        def write(self, data):
            if failed_operation == "write":
                raise cause
            return self.handle.write(data)
        def flush(self):
            if failed_operation == "flush":
                raise cause
            return self.handle.flush()
        def fileno(self):
            return self.handle.fileno()

    proxy = _os_proxy(fdopen=lambda *args: FaultyFile(os.fdopen(*args)))
    if failed_operation == "fsync":
        proxy.fsync = lambda _: (_ for _ in ()).throw(cause)
    monkeypatch.setattr(storage, "os", proxy)
    with pytest.raises(storage.StorageAccessError) as caught:
        storage.check_directory_access(directory, label="测试图片目录")
    _assert_failure(caught.value, cause, path=directory, stage=stage)
    assert handles and all(handle.closed for handle in handles)
    assert not list(directory.iterdir())


@pytest.mark.parametrize("failed_operation,stage", [("replace", "replace"), ("unlink", "delete")])
def test_replace_and_delete_denial_report_failure_without_touching_existing_file(tmp_path, monkeypatch, failed_operation, stage):
    directory = tmp_path / "题库"
    directory.mkdir()
    existing = directory / "keep.txt"
    existing.write_bytes(b"original")
    cause = _failure()
    original_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path.parent == directory and path.name.startswith(".mathbank-write-check-"):
            raise cause
        return original_unlink(path, *args, **kwargs)
    try:
        with monkeypatch.context() as patch:
            if failed_operation == "replace":
                patch.setattr(storage, "os", _os_proxy(replace=lambda *_: (_ for _ in ()).throw(cause)))
            else:
                patch.setattr(Path, "unlink", unlink)
            with pytest.raises(storage.StorageAccessError) as caught:
                storage.check_directory_access(directory, label="测试图片目录")
        _assert_failure(caught.value, cause, path=directory, stage=stage)
        assert existing.read_bytes() == b"original"
    finally:
        _clear_probes(directory)
    assert list(directory.iterdir()) == [existing]


def test_cleanup_failure_does_not_replace_primary_disk_error(tmp_path, monkeypatch):
    directory = tmp_path / "题库"
    directory.mkdir()
    primary, cleanup = _failure(number=errno.ENOSPC), _failure()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(storage, "os", _os_proxy(replace=lambda *_: (_ for _ in ()).throw(primary)))
            patch.setattr(Path, "unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(cleanup))
            with pytest.raises(storage.StorageAccessError) as caught:
                storage.check_directory_access(directory, label="测试图片目录")
        _assert_failure(caught.value, primary, path=directory, stage="replace", code="disk")
    finally:
        _clear_probes(directory)


def test_existing_file_probe_only_opens_writable_without_mutating_bytes_or_permissions(tmp_path, monkeypatch):
    target = tmp_path / "已有数据库.db"
    target.write_bytes(b"unchanged database placeholder\x00")
    before, mode = target.read_bytes(), stat.S_IMODE(target.stat().st_mode)
    original_open, modes = Path.open, []
    def opened(path, requested_mode="r", *args, **kwargs):
        if path == target:
            modes.append(requested_mode)
        return original_open(path, requested_mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", opened)
        storage.check_file_write_access(target, label="已有数据库")
    assert modes == ["r+b"]
    assert target.read_bytes() == before
    assert stat.S_IMODE(target.stat().st_mode) == mode
    missing = tmp_path / "missing.db"
    storage.check_file_write_access(missing, label="尚未创建的数据库")
    assert not missing.exists()


def test_native_readonly_file_is_reported_and_never_made_writable(tmp_path):
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses POSIX file permission checks")
    target = tmp_path / "只读.db"
    target.write_bytes(b"keep readonly data")
    mode = stat.S_IMODE(target.stat().st_mode)
    try:
        target.chmod(stat.S_IREAD if os.name == "nt" else 0o444)
        readonly_mode = stat.S_IMODE(target.stat().st_mode)
        with pytest.raises(storage.StorageAccessError) as caught:
            storage.check_file_write_access(target, label="只读数据库")
        assert caught.value.stage == "check_file" and caught.value.code == "permission"
        assert target.read_bytes() == b"keep readonly data"
        assert stat.S_IMODE(target.stat().st_mode) == readonly_mode
    finally:
        target.chmod(mode)


@pytest.mark.parametrize("winerror", [32, 33])
def test_windows_sharing_violation_retry_is_bounded_and_can_recover(monkeypatch, winerror):
    monkeypatch.setattr(storage, "os", SimpleNamespace(name="nt"))
    sleeps, attempts = [], []
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    cause = _failure(winerror=winerror)
    def always_busy():
        attempts.append(True)
        raise cause
    with pytest.raises(OSError) as caught:
        storage._retry_file_operation(always_busy)
    assert caught.value is cause and len(attempts) == 5
    assert sleeps == [0.05, 0.1, 0.2, 0.4]
    attempts.clear()
    sleeps.clear()
    def briefly_busy():
        attempts.append(True)
        if len(attempts) < 3:
            raise cause
        return "saved"
    assert storage._retry_file_operation(briefly_busy) == "saved"
    assert len(attempts) == 3 and sleeps == [0.05, 0.1]


def test_windows_permission_denial_is_not_retried(monkeypatch):
    monkeypatch.setattr(storage, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(storage.time, "sleep", lambda _: pytest.fail("permanent permission denial retried"))
    cause, attempts = _failure(winerror=5), []
    def denied():
        attempts.append(True)
        raise cause
    with pytest.raises(OSError) as caught:
        storage._retry_file_operation(denied)
    assert caught.value is cause and len(attempts) == 1


def test_startup_checks_disposable_directories_without_creating_database_or_runtime_files(tmp_path):
    root = tmp_path / "中文安装目录"
    storage.check_startup_storage(root)
    for relative in (".system_generated", "static/uploads", "data_backup", "data_backup/snapshots"):
        assert (root / relative).is_dir()
    assert not list(root.rglob(".mathbank-write-check-*"))
    assert not (root / "math_question_bank.db").exists()
    assert not list((root / ".system_generated").iterdir())


@pytest.mark.parametrize("relative", ["", ".system_generated", "static/uploads", "data_backup", "data_backup/snapshots"])
def test_startup_refuses_each_unwritable_directory(tmp_path, monkeypatch, relative):
    root = tmp_path / "题库"
    root.mkdir()
    denied, cause, original_mkdir = root / relative, _failure(), Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path == denied:
            raise cause
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(storage.StorageAccessError) as caught:
        storage.check_startup_storage(root)
    assert caught.value.path == denied
    assert caught.value.stage == "create" and caught.value.code == "permission"
    assert not (root / "math_question_bank.db").exists()


@pytest.mark.parametrize("relative", [
    "math_question_bank.db", "math_question_bank.db-wal", "math_question_bank.db-shm",
    ".system_generated/launcher.lock", ".system_generated/runtime.lock", ".system_generated/server.log",
])
def test_startup_checks_file_permissions_independently_of_writable_directory(tmp_path, monkeypatch, relative):
    root = tmp_path / "题库"
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing original")
    cause, original_open = _failure(), Path.open
    def opened(path, mode="r", *args, **kwargs):
        if path == target and mode == "r+b":
            raise cause
        return original_open(path, mode, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", opened)
        with pytest.raises(storage.StorageAccessError) as caught:
            storage.check_startup_storage(root)
    assert caught.value.path == target
    assert caught.value.stage == "check_file" and caught.value.code == "permission"
    assert caught.value.__cause__ is cause
    assert target.read_bytes() == b"existing original"
    assert "/private/user/secret.db" not in caught.value.safe_message
    assert not list(root.rglob(".mathbank-write-check-*"))
