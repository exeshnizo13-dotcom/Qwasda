"""
Single instance enforcement for Qwasda using named mutex.

Prevents multiple instances from running simultaneously (which would
create conflicting keyboard hooks).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from ctypes import wintypes
from types import TracebackType

kernel32 = ctypes.windll.kernel32

ERROR_ALREADY_EXISTS = 183

kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]

kernel32.ReleaseMutex.restype = wintypes.BOOL
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]

kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

kernel32.GetLastError.restype = wintypes.DWORD


class SingleInstance:
    """
    Named mutex to ensure only one Qwasda instance runs.

    Usage:
        si = SingleInstance("Qwasda_SingleInstance_Mutex")
        if not si.acquire():
            sys.exit(0)  # Another instance running
        try:
            main()
        finally:
            si.release()
    """

    def __init__(self, name: str = "Qwasda_SingleInstance_Mutex"):
        self.name = name
        self._mutex: wintypes.HANDLE | None = None
        self._acquired = False

    def acquire(self) -> bool:
        """Try to acquire mutex. Returns True if this is the first instance."""
        self._mutex = kernel32.CreateMutexW(None, False, self.name)
        if not self._mutex:
            return False
        self._acquired = kernel32.GetLastError() != ERROR_ALREADY_EXISTS
        return self._acquired

    def release(self) -> None:
        """Release mutex (call on exit)."""
        if self._mutex:
            if self._acquired:
                kernel32.ReleaseMutex(self._mutex)
            kernel32.CloseHandle(self._mutex)
            self._mutex = None
            self._acquired = False

    def __enter__(self) -> SingleInstance:
        if not self.acquire():
            raise RuntimeError("Another instance is already running")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()

    @property
    def is_acquired(self) -> bool:
        return self._acquired
