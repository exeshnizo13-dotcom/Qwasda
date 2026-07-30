"""Regression tests for pointer-sized Win32 hook declarations."""

from __future__ import annotations

import ctypes

from qwasda.win32 import HOOKPROC, LRESULT, user32


def test_hook_result_uses_pointer_sized_signed_type() -> None:
    assert ctypes.sizeof(LRESULT) == ctypes.sizeof(ctypes.c_void_p)
    assert HOOKPROC._restype_ is LRESULT
    assert user32.CallNextHookEx.restype is LRESULT
