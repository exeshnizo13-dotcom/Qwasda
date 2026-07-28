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
SHUTDOWN_EVENT_NAME = "Local\\Qwasda_Shutdown_v1"
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0

kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]

kernel32.ReleaseMutex.restype = wintypes.BOOL
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]

kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

kernel32.GetLastError.restype = wintypes.DWORD
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def request_shutdown() -> bool:
    """Signal the running per-user instance to perform a normal cleanup."""
    handle = kernel32.OpenEventW(0x0002, False, SHUTDOWN_EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


class ShutdownSignal:
    """Owns the named event used to stop Qwasda during installer upgrades."""

    def __init__(self) -> None:
        self.handle: wintypes.HANDLE | None = None

    def create(self) -> None:
        self.handle = kernel32.CreateEventW(None, True, False, SHUTDOWN_EVENT_NAME)
        if not self.handle:
            raise OSError("CreateEventW failed")

    def wait(self, timeout: int = INFINITE) -> bool:
        return bool(
            self.handle and kernel32.WaitForSingleObject(self.handle, timeout) == WAIT_OBJECT_0
        )

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


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
