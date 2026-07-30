"""Qwasda entry point and packaging lifecycle commands."""

import ctypes
import ctypes.wintypes
import sys
from pathlib import Path

from .config import Config
from .engine import QwasdaEngine
from .single_instance import request_shutdown
from .updater import apply_update
from .version import __version__


def _write_windows_stdout(text: str) -> bool:
    """Write to an inherited stdout pipe in a PyInstaller windowed build."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.restype = ctypes.wintypes.HANDLE
    kernel32.GetStdHandle.argtypes = [ctypes.wintypes.DWORD]
    kernel32.WriteFile.restype = ctypes.wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.DWORD),
        ctypes.c_void_p,
    ]

    handle = kernel32.GetStdHandle(ctypes.wintypes.DWORD(-11))  # STD_OUTPUT_HANDLE
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        return False

    payload = text.encode("utf-8")
    buffer = ctypes.create_string_buffer(payload)
    written = ctypes.wintypes.DWORD()
    return bool(kernel32.WriteFile(handle, buffer, len(payload), ctypes.byref(written), None))


def _write_stdout(text: str) -> None:
    """Write CLI output without assuming a console stream exists."""
    if sys.stdout is not None:
        sys.stdout.write(text)
        return
    _write_windows_stdout(text)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        _write_stdout(f"{__version__}\n")
        return 0
    if args == ["--shutdown"]:
        return 0 if request_shutdown() else 1
    if args == ["--smoke-test"]:
        return 0
    if len(args) == 2 and args[0] == "--apply-update":
        return apply_update(Path(args[1]))
    config = Config()
    engine = QwasdaEngine(config)
    return engine.run()


if __name__ == "__main__":
    sys.exit(main())
