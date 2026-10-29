"""Nonblocking Windows shared/exclusive byte locks for document-project leases.

CRT LK_NBRLCK is not a shared lock. Use LockFileEx on the existing descriptor's
handle instead; keep ownership with the caller and never open or close it here.
"""
from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os


class _Overlapped(ctypes.Structure):
    # ULONG_PTR follows pointer width; DWORD is always 32 bits, even on Unix
    # hosts running mocked ABI tests. The two offsets occupy the native union.
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", ctypes.c_uint32), ("OffsetHigh", ctypes.c_uint32),
                ("hEvent", ctypes.c_void_p)]


@lru_cache(maxsize=1)
def _get_api():
    if os.name != "nt":
        raise OSError("Windows file locks are unavailable on this platform")
    import msvcrt
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LockFileEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                   ctypes.c_uint32, ctypes.c_uint32,
                                   ctypes.c_uint32, ctypes.POINTER(_Overlapped)]
    kernel32.LockFileEx.restype = ctypes.c_int
    kernel32.UnlockFileEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.POINTER(_Overlapped)]
    kernel32.UnlockFileEx.restype = ctypes.c_int
    return kernel32, msvcrt.get_osfhandle, ctypes.get_last_error


def _failure(code, *, acquiring):
    # Only an actual lock collision is retryable; bad handles and denied access
    # must not masquerade as an ordinary busy project or get retried for 5s.
    if acquiring and code == 33:  # ERROR_LOCK_VIOLATION
        error = BlockingIOError(errno.EAGAIN, "Windows file lock is busy")
    else:
        error = OSError(code, "Windows file lock operation failed")
    error.winerror = code
    return error


def lock(fd, *, shared):
    api, get_handle, last_error = _get_api()
    overlapped = _Overlapped()  # offset 0, no event, synchronous handle
    # FAIL_IMMEDIATELY avoids unbounded OS waits. No EXCLUSIVE flag means shared.
    flags = 1 | (0 if shared else 2)
    if not api.LockFileEx(get_handle(fd), flags, 0, 1, 0, ctypes.byref(overlapped)):
        raise _failure(last_error(), acquiring=True)


def unlock(fd):
    api, get_handle, last_error = _get_api()
    overlapped = _Overlapped()
    if not api.UnlockFileEx(get_handle(fd), 0, 1, 0, ctypes.byref(overlapped)):
        raise _failure(last_error(), acquiring=False)
