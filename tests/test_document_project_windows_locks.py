"""Win32 ABI contract mocks plus real Windows cross-process lease regression."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mathbank import document_projects as projects
from mathbank import windows_file_lock as locks


@pytest.mark.parametrize("shared,flags", [(True, 1), (False, 3)])
def test_win32_exact_byte_range_flags_and_pointer_width(monkeypatch, shared, flags):
    observed = []
    def acquire(handle, mode, reserved, low, high, overlap):
        value = overlap._obj
        observed.append((handle, mode, reserved, low, high, value.Offset,
                         value.OffsetHigh, value.Internal, value.InternalHigh, value.hEvent))
        return 1
    def release(handle, reserved, low, high, overlap):
        assert (handle, reserved, low, high, overlap._obj.Offset, overlap._obj.OffsetHigh) == (2**40, 0, 1, 0, 0, 0)
        return 1
    monkeypatch.setattr(locks, "_get_api", lambda: (
        SimpleNamespace(LockFileEx=acquire, UnlockFileEx=release), lambda fd: 2**40, lambda: 0))
    locks.lock(123, shared=shared)
    locks.unlock(123)
    assert observed == [(2**40, flags, 0, 1, 0, 0, 0, 0, 0, None)]
    assert ctypes.sizeof(locks._Overlapped) == (32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)


@pytest.mark.parametrize("code,busy", [(33, True), (5, False), (6, False), (997, False)])
def test_only_lock_violation_is_retryable(monkeypatch, code, busy):
    api = SimpleNamespace(LockFileEx=lambda *args: 0)
    monkeypatch.setattr(locks, "_get_api", lambda: (api, lambda fd: fd, lambda: code))
    with pytest.raises(OSError) as error:
        locks.lock(1, shared=True)
    assert isinstance(error.value, BlockingIOError) is busy
    assert error.value.winerror == code


def test_unlock_error_is_not_a_retryable_acquisition(monkeypatch):
    monkeypatch.setattr(locks, "_get_api", lambda: (
        SimpleNamespace(UnlockFileEx=lambda *args: 0), lambda fd: fd, lambda: 33))
    with pytest.raises(OSError) as error:
        locks.unlock(1)
    assert not isinstance(error.value, BlockingIOError)
    assert error.value.winerror == 33


def test_failed_lock_closes_fd_and_access_denial_is_not_conflict(tmp_path, monkeypatch):
    store = projects.DocumentProjectStore(tmp_path / 'projects')
    identifier = store.create(b'synthetic', 'test.pdf', {}, {})['project_id']
    handles = []
    def denied(fd, *, shared):
        handles.append(fd)
        raise PermissionError('denied')
    monkeypatch.setattr(projects, 'fcntl', None)
    monkeypatch.setattr(projects, '_windows_lock', SimpleNamespace(lock=denied))
    with pytest.raises(projects.ProjectUnsafePath):
        store._lock_fd(identifier, '.lease', 'shared', wait=False)
    assert len(handles) == 1
    with pytest.raises(OSError):
        os.fstat(handles[0])


def test_unlock_failure_still_closes_descriptor(tmp_path, monkeypatch):
    store = projects.DocumentProjectStore(tmp_path / 'projects')
    fd = os.open(tmp_path / 'lock', os.O_RDWR | os.O_CREAT, 0o600)
    monkeypatch.setattr(projects, 'fcntl', None)
    def denied(fd):
        raise OSError('unlock failed')
    monkeypatch.setattr(projects, '_windows_lock', SimpleNamespace(unlock=denied))
    with pytest.raises(OSError, match='unlock failed'):
        store._unlock_fd(fd)
    with pytest.raises(OSError):
        os.fstat(fd)


def test_failed_open_preserves_original_error_without_closing_another_fd(tmp_path, monkeypatch):
    store = projects.DocumentProjectStore(tmp_path / 'projects')
    identifier = store.create(b'synthetic', 'test.pdf', {}, {})['project_id']
    def denied(*args, **kwargs):
        raise PermissionError('open denied')
    monkeypatch.setattr(projects.os, 'open', denied)
    with pytest.raises(projects.ProjectUnsafePath) as error:
        store._lock_fd(identifier, '.lease', 'shared')
    assert isinstance(error.value.__cause__, PermissionError)


def test_unlock_failures_release_both_fds_and_thread_mutex(tmp_path, monkeypatch):
    store = projects.DocumentProjectStore(tmp_path / 'projects')
    identifier = store.create(b'synthetic', 'test.pdf', {}, {})['project_id']
    handles, released = [], []
    def acquire(fd, *, shared):
        handles.append(fd)
    def release(fd):
        released.append(fd)
        raise OSError('synthetic unlock failure')
    monkeypatch.setattr(projects, 'fcntl', None)
    monkeypatch.setattr(projects, '_windows_lock', SimpleNamespace(lock=acquire, unlock=release))
    with pytest.raises(OSError, match='synthetic unlock failure'):
        store.get(identifier)
    assert len(handles) == 2 and set(released) == set(handles)
    for fd in handles:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert store._local.leases == {}
    def acquire_thread_mutex():
        lock = store._locks[identifier]
        acquired = lock.acquire(timeout=1)
        if acquired:
            lock.release()
        return acquired
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(acquire_thread_mutex).result(timeout=5)


def _probe(path, shared):
    # Fresh process + fresh handle: a process-local mutex cannot pass this test.
    script = '''
import os, sys
from mathbank.windows_file_lock import lock, unlock
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
try:
    try:
        lock(fd, shared=sys.argv[2] == 'True')
    except BlockingIOError:
        print('busy')
    else:
        print('locked')
        unlock(fd)
finally:
    os.close(fd)
'''
    result = subprocess.run([sys.executable, '-c', script, str(path), str(shared)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(os.name != 'nt', reason='requires actual Win32 file locks')
def test_native_process_exit_releases_outstanding_lock(tmp_path):
    path = tmp_path / 'exit.lock'
    script = '''
import os, sys
from mathbank.windows_file_lock import lock
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
lock(fd, shared=False)
print('acquired')
# Deliberately exercise OS process-exit cleanup, without unlock/close.
'''
    result = subprocess.run([sys.executable, '-c', script, str(path)],
                            cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'acquired'
    assert _probe(path, False) == 'locked'


@pytest.mark.skipif(os.name != 'nt', reason='requires actual Win32 file locks')
@pytest.mark.parametrize('shared', [True, False])
def test_native_shared_and_exclusive_locks_across_processes(tmp_path, shared):
    path = tmp_path / '中文 lease.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        locks.lock(fd, shared=shared)
        assert _probe(path, True) == ('locked' if shared else 'busy')
        assert _probe(path, False) == 'busy'
        locks.unlock(fd)
        assert _probe(path, False) == 'locked'
    finally:
        os.close(fd)


@pytest.mark.skipif(os.name != 'nt', reason='requires actual Win32 file locks')
def test_native_cross_thread_read_and_exception_release(tmp_path):
    store = projects.DocumentProjectStore(tmp_path / 'projects')
    identifier = store.create(b'synthetic', 'test.pdf', {}, {})['project_id']
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with store.operation(identifier):
            with store.operation(identifier):  # same-thread reentrance
                with ThreadPoolExecutor(max_workers=1) as pool:
                    assert pool.submit(store.get, identifier).result(timeout=10)['project_id'] == identifier
            with pytest.raises(projects.ProjectConflict):
                store.delete(identifier)
            raise RuntimeError('synthetic failure')
    # All nested leases were released; an exclusive deletion must now succeed.
    store.delete(identifier)
    assert not (store.root / identifier).exists()
