"""
Crash reporting for Qwasda.

Provides:
- faulthandler for Python tracebacks
- Windows Error Reporting (WER) integration
- Minidump generation for native crashes
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import faulthandler
import os
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

from .version import __version__

# Windows Error Reporting constants
WER_SUBMIT_HONOR_RECOVERY = 0x1
WER_SUBMIT_HONOR_RESTART = 0x2
WER_SUBMIT_QUEUE = 0x4
WER_SUBMIT_SHOW_DEBUG = 0x8
WER_SUBMIT_ADD_REGISTERED_DATA = 0x10
WER_SUBMIT_OUTOFPROCESS = 0x20
WER_SUBMIT_OUTOFPROCESS_ASYNC = 0x40
WER_SUBMIT_BYPASS_DATA_THROTTLING = 0x80
WER_SUBMIT_ARCHIVE_PARAMETERS_ONLY = 0x100
WER_SUBMIT_REPORT_MACHINE_ID = 0x200
WER_SUBMIT_BYPASS_APP_RESTORE = 0x400
WER_SUBMIT_NO_CLOSE_UI = 0x800
WER_SUBMIT_NO_QUEUE = 0x1000
WER_SUBMIT_NO_ARCHIVE = 0x2000
WER_SUBMIT_START_MINIMIZED = 0x4000
WER_SUBMIT_OUTOFPROCESS_NO_DUMP = 0x8000

WER_FAULT_REPORTING_ALWAYS_SHOW_UI = 0x1
WER_FAULT_REPORTING_DISABLE_SNAPSHOT = 0x2
WER_FAULT_REPORTING_DISABLE_QUEUE = 0x4
WER_FAULT_REPORTING_DISABLE_EVENT_LOG = 0x8
WER_FAULT_REPORTING_NO_UI = 0x10

WER_MAX_PATH = 260

# WER Report types
WerReportNonCritical = 0
WerReportCritical = 1
WerReportApplicationCrash = 2
WerReportApplicationHang = 3
WerReportKernel = 4
WerReportInvalid = 5

# WER Dump types
WerDumpTypeMicroDump = 1
WerDumpTypeMiniDump = 2
WerDumpTypeHeapDump = 3
WerDumpTypeTriageDump = 4
WerDumpTypeMax = 5


class CrashReporter:
    """
    Handles crash reporting for Qwasda.

    Features:
    - faulthandler for Python tracebacks
    - Windows Error Reporting integration
    - Custom exception handler
    - Minidump generation
    """

    def __init__(self, app_name: str = "Qwasda", app_version: str = __version__):
        self.app_name = app_name
        self.app_version = app_version
        self._original_excepthook: Callable[..., object] | None = None
        self._original_threading_excepthook: Callable[..., object] | None = None
        self._crash_log_path: Path | None = None
        self._fault_log_file: TextIO | None = None
        self._wer_dll: ctypes.WinDLL | None = None
        self._initialized = False

    def initialize(self, log_dir: Path | None = None) -> None:
        """Initialize crash reporting."""
        if self._initialized:
            return

        # Set up crash log directory
        if log_dir is None:
            log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Qwasda" / "CrashReports"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._crash_log_path = log_dir / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        # Enable faulthandler for Python crashes
        crash_log_file = open(  # noqa: SIM115 - held open for faulthandler lifetime
            self._crash_log_path, "w", encoding="utf-8"
        )
        self._fault_log_file = crash_log_file
        faulthandler.enable(crash_log_file)
        # Note: faulthandler.register() is not available on Windows
        # It's only available on Unix for signal handlers

        # Set custom exception hook
        self._original_excepthook = sys.excepthook
        sys.excepthook = self._handle_exception

        # Set thread exception hook
        self._original_threading_excepthook = threading.excepthook
        threading.excepthook = self._handle_thread_exception

        # Load WER DLL for Windows Error Reporting
        try:
            self._wer_dll = ctypes.WinDLL("wer.dll")
            self._setup_wer_functions()
        except (OSError, AttributeError):
            # WER not available (older Windows)
            pass

        self._initialized = True

    def _setup_wer_functions(self) -> None:
        """Set up Windows Error Reporting function signatures."""
        if not self._wer_dll:
            return

        # WerReportCreate
        self._wer_dll.WerReportCreate.argtypes = [
            ctypes.c_int,  # WER_REPORT_TYPE
            ctypes.c_wchar_p,  # pwzEventType
            ctypes.c_int,  # WER_REPORT_INFORMATION
            ctypes.POINTER(ctypes.c_void_p),  # phReportHandle
        ]
        self._wer_dll.WerReportCreate.restype = ctypes.c_long

        # WerReportAddDump
        self._wer_dll.WerReportAddDump.argtypes = [
            ctypes.c_void_p,  # hReportHandle
            ctypes.c_void_p,  # hProcess
            ctypes.c_void_p,  # hThread
            ctypes.c_int,  # WER_DUMP_TYPE
            ctypes.c_void_p,  # pExceptionParam
            ctypes.c_void_p,  # pDumpCustomOptions
            ctypes.c_ulong,  # dwFlags
        ]
        self._wer_dll.WerReportAddDump.restype = ctypes.c_long

        # WerReportSubmit
        self._wer_dll.WerReportSubmit.argtypes = [
            ctypes.c_void_p,  # hReportHandle
            ctypes.c_int,  # WER_SUBMIT_FLAGS
            ctypes.c_void_p,  # pSubmitOptions
            ctypes.POINTER(ctypes.c_int),  # pResult
        ]
        self._wer_dll.WerReportSubmit.restype = ctypes.c_long

        # WerReportCloseHandle
        self._wer_dll.WerReportCloseHandle.argtypes = [ctypes.c_void_p]
        self._wer_dll.WerReportCloseHandle.restype = ctypes.c_long

    def _handle_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        """Handle uncaught exceptions."""
        if self._crash_log_path is None:
            return
        # Write to crash log
        with open(self._crash_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"UNCAUGHT EXCEPTION: {datetime.now().isoformat()}\n")
            f.write(f"App: {self.app_name} v{self.app_version}\n")
            f.write(f"{'='*60}\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)

        # Try to submit to WER
        self._submit_to_wer(exc_type, exc_value, exc_traceback)

        # Call original handler
        if self._original_excepthook:
            self._original_excepthook(exc_type, exc_value, exc_traceback)

    def _handle_thread_exception(self, args: threading.ExceptHookArgs) -> None:
        """Handle uncaught thread exceptions."""
        if args.exc_value is not None:
            self._handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

    def _submit_to_wer(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        """Submit crash to Windows Error Reporting."""
        if not self._wer_dll:
            return

        try:
            # Create report
            report_handle = ctypes.c_void_p()
            event_type = f"{self.app_name}_Crash_v{self.app_version}"

            hr = self._wer_dll.WerReportCreate(
                WerReportApplicationCrash,
                event_type,
                0,  # WER_REPORT_INFORMATION
                ctypes.byref(report_handle),
            )

            if hr != 0:
                return

            # Add minidump
            self._wer_dll.WerReportAddDump(
                report_handle,
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.windll.kernel32.GetCurrentThread(),
                WerDumpTypeMiniDump,
                None,
                None,
                0,
            )

            # Submit
            result = ctypes.c_int()
            self._wer_dll.WerReportSubmit(
                report_handle,
                WER_SUBMIT_QUEUE | WER_SUBMIT_OUTOFPROCESS,
                None,
                ctypes.byref(result),
            )

            # Close handle
            self._wer_dll.WerReportCloseHandle(report_handle)

        except Exception:
            # Silently ignore WER failures
            pass

    def shutdown(self) -> None:
        """Clean up crash reporting."""
        if self._original_excepthook:
            sys.excepthook = self._original_excepthook
        if self._original_threading_excepthook:
            threading.excepthook = self._original_threading_excepthook
        faulthandler.disable()
        if self._fault_log_file:
            self._fault_log_file.close()
            self._fault_log_file = None
        self._original_excepthook = None
        self._original_threading_excepthook = None
        self._initialized = False


# Global instance
_crash_reporter: CrashReporter | None = None


def initialize_crash_reporting(
    app_name: str = "Qwasda", app_version: str = __version__, log_dir: Path | None = None
) -> CrashReporter:
    """Initialize global crash reporter."""
    global _crash_reporter
    _crash_reporter = CrashReporter(app_name, app_version)
    _crash_reporter.initialize(log_dir)
    return _crash_reporter


def get_crash_reporter() -> CrashReporter | None:
    """Get global crash reporter instance."""
    return _crash_reporter


def shutdown_crash_reporting() -> None:
    """Shutdown global crash reporter."""
    global _crash_reporter
    if _crash_reporter:
        _crash_reporter.shutdown()
        _crash_reporter = None
