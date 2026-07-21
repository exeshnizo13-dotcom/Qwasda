"""
Qwasda Engine - main orchestrator coordinating all components.

This is the central class that ties together:
- Config & Learning
- Dictionary loading
- Keyboard/Mouse hooks
- Correction worker
- Tray icon
- Startup management
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.wintypes
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .dicts import DictionaryLoader
    from .hooks import (
        CaretGuard,
        CorrectionWorker,
        DoubleTapDetector,
        KeyboardHook,
        MouseHook,
        PhraseBuffer,
    )
    from .learning import LearningManager
    from .single_instance import SingleInstance
    from .tray import TrayIcon
    from .win32 import get_foreground_layout

from .admin import ELEVATED_ARG, get_integrity_level, is_admin, request_elevation
from .config import Config, ConfigManager
from .conversion import (
    is_word_terminator,
)
from .crash_reporting import initialize_crash_reporting, shutdown_crash_reporting
from .dicts import DictionaryLoader
from .health_monitor import HealthMonitor, HealthStatus, initialize_health_monitoring
from .hooks import (
    CaretGuard,
    CorrectionTask,
    CorrectionWorker,
    DoubleTapDetector,
    KeyboardHook,
    MouseHook,
    PhraseBuffer,
)
from .learning import LearningManager
from .logging_config import get_logger, initialize_logging, shutdown_logging
from .single_instance import SingleInstance
from .tray import TrayIcon
from .win32 import (
    MODIFIER_VKS,
    NAV_CLEAR_VKS,
    VK_BACK,
    VK_CAPITAL,
    VK_SHIFT,
    WM_QUIT,
    WORD_BREAK_VKS,
    any_modifier_down,
    get_foreground_layout,
    kernel32,
    user32,
)


class QwasdaEngine:
    """
    Main application engine.

    Coordinates all subsystems and runs the message loop.
    """

    def __init__(self, config: Config):
        self.config = config
        self.version = self._get_version()

        # Core components
        self.dict_loader = DictionaryLoader(config.app_dir)
        self.learning = LearningManager(config)
        self.single_instance = SingleInstance()
        self.config_manager: ConfigManager | None = None

        # State
        self._running = False
        self._enabled = threading.Event()
        self._enabled.set()  # Start enabled

        # Buffers
        self.typed_scans: list[tuple[int, bool]] = []
        self.phrase_buffer = PhraseBuffer()
        self.caret_guard = CaretGuard()
        self.double_tap = DoubleTapDetector()

        # Hooks
        self.kb_hook: KeyboardHook | None = None
        self.mouse_hook: MouseHook | None = None
        self.worker: CorrectionWorker | None = None

        # Tray
        self.tray: TrayIcon | None = None

        # Layout cache
        self._cached_layout = 0x0409  # Default EN
        self._cached_layout_time = 0.0

        # Input sequence counter (for race detection)
        self._input_seq = 0
        self._seq_lock = threading.Lock()

        # Last window handle
        self._last_hwnd: int | None = None
        self._health_monitor: HealthMonitor | None = None
        self._cleanup_done = False

        # Callbacks for tray
        self._tray_callbacks = {
            "toggle_enabled": self._toggle_enabled,
            "toggle_auto": self._toggle_auto,
            "toggle_learning": self._toggle_learning,
            "forget_learned": self._forget_learned,
            "toggle_startup": self._toggle_startup,
            "exit": self._request_exit,
        }

    def _get_version(self) -> str:
        try:
            from importlib.metadata import version

            return version("qwasda")
        except Exception:
            return "1.3.4"

    # =========================================================================
    # Public API
    # =========================================================================

    def run(self) -> int:
        """Main entry point. Returns exit code and always releases resources."""
        try:
            return self._run()
        except Exception:
            logging.getLogger("qwasda.engine").exception("Qwasda startup or runtime failure")
            return 1
        finally:
            self._cleanup()

    def _run(self) -> int:
        """Initialize the application and run its message loop."""
        # Initialize crash reporting
        initialize_crash_reporting("Qwasda", self.version)

        # Initialize structured logging
        initialize_logging(
            level=self.config.log_level if hasattr(self.config, "log_level") else "INFO"
        )
        logger = get_logger("qwasda.engine")

        # Check admin status
        admin_status = is_admin()
        integrity = get_integrity_level()
        logger.info(
            "Starting Qwasda",
            extra={
                "version": self.version,
                "admin": admin_status,
                "integrity_level": integrity,
                "pid": os.getpid(),
            },
        )

        # A frozen build may already request elevation through its manifest.
        # Source/script launches use an explicit marker to avoid an elevation loop.
        if not admin_status and ELEVATED_ARG not in sys.argv:
            elevated, error = request_elevation()
            if elevated:
                return 0
            logger.warning("Could not obtain administrator privileges", extra={"error": error})

        # Single instance check
        if not self.single_instance.acquire():
            ctypes.windll.user32.MessageBoxW(None, "Qwasda вже запущено.", "Qwasda", 0x40)
            return 0

        # Load config & learned words
        config_manager = ConfigManager(self.config.app_dir)
        config_manager.load()
        self.config_manager = config_manager
        self.config = config_manager.config
        # LearningManager loads in __init__

        # Apply config to engine state
        self._enabled.set() if self.config.enabled else self._enabled.clear()

        # Start dictionary loading in background
        dict_thread = threading.Thread(target=self.dict_loader.load, daemon=True)
        dict_thread.start()

        # Initialize worker
        self.worker = CorrectionWorker(
            dict_loader=self.dict_loader,
            learning=self.learning,
            config=self.config,
            get_layout_func=self.get_layout,
        )

        # Initialize hooks
        hinst = ctypes.pythonapi._handle
        self.kb_hook = KeyboardHook(
            worker=self.worker,
            phrase_buffer=self.phrase_buffer,
            caret_guard=self.caret_guard,
            double_tap=self.double_tap,
            config=self.config,
            enabled_flag=self._enabled,
            get_layout_func=self.get_layout,
        )
        if not self.kb_hook.install(hinst):
            ctypes.windll.user32.MessageBoxW(
                None, "Не вдалося встановити клавіатурний хук.", "Qwasda", 0x10
            )
            return 1

        self.mouse_hook = MouseHook(self.phrase_buffer, self.worker)
        self.mouse_hook.install(hinst)

        # Start tray
        self.tray = TrayIcon(
            config=self.config,
            learning=self.learning,
            dict_loader=self.dict_loader,
            on_toggle_enabled=self._tray_callbacks["toggle_enabled"],
            on_toggle_auto=self._tray_callbacks["toggle_auto"],
            on_toggle_learning=self._tray_callbacks["toggle_learning"],
            on_forget_learned=self._tray_callbacks["forget_learned"],
            on_toggle_startup=self._tray_callbacks["toggle_startup"],
            on_exit=self._tray_callbacks["exit"],
            version=self.version,
        )
        self.tray.run()

        # Initialize health monitoring once, after all components are ready.
        self._health_monitor = initialize_health_monitoring(
            watchdog_callback=self._watchdog_triggered
        )

        # Register health checks
        from .health_monitor import (
            DictionaryHealthCheck,
            HookHealthCheck,
            TrayHealthCheck,
            WorkerHealthCheck,
        )

        self._health_monitor.register_check(DictionaryHealthCheck(self.dict_loader))
        self._health_monitor.register_check(HookHealthCheck(self.kb_hook, self.mouse_hook))
        self._health_monitor.register_check(WorkerHealthCheck(self.worker))
        self._health_monitor.register_check(TrayHealthCheck(self.tray))

        # Start health monitoring
        self._health_monitor.start()

        # Cleanup handlers
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Message loop
        self._running = True
        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

            # Heartbeat for watchdog
            if self._health_monitor:
                self._health_monitor.heartbeat()

        return 0

    def _watchdog_triggered(self) -> None:
        """Called when watchdog detects a hang."""
        if self._health_monitor is None:
            return
        # Log health status
        health = self._health_monitor.get_health()
        for name, comp in health.items():
            if comp and comp.status != HealthStatus.HEALTHY:
                logging.getLogger("qwasda.engine").error(
                    f"Watchdog: {name} is {comp.status.value}: {comp.message}"
                )

        # Could trigger crash dump here
        import faulthandler

        faulthandler.dump_traceback()

    def _cleanup(self) -> None:
        """Clean up all resources; safe to call repeatedly and partially."""
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._running = False

        cleanup_steps: tuple[tuple[str, Callable[[], object]], ...] = (
            (
                "health monitor",
                lambda: self._health_monitor.stop() if self._health_monitor else None,
            ),
            ("tray", lambda: self.tray.stop() if self.tray else None),
            ("mouse hook", lambda: self.mouse_hook.uninstall() if self.mouse_hook else None),
            ("keyboard hook", lambda: self.kb_hook.uninstall() if self.kb_hook else None),
            ("worker", lambda: self.worker.shutdown() if self.worker else None),
            ("single instance", self.single_instance.release),
            ("logging", shutdown_logging),
            ("crash reporting", shutdown_crash_reporting),
        )
        for name, cleanup in cleanup_steps:
            try:
                cleanup()
            except Exception:
                logging.getLogger("qwasda.engine").exception(
                    "Cleanup failed", extra={"component": name}
                )

    def _signal_handler(self, signum: int, frame: object) -> None:
        self._running = False
        user32.PostThreadMessageW(kernel32.GetCurrentThreadId(), WM_QUIT, 0, 0)

    # =========================================================================
    # Layout Management
    # =========================================================================

    def get_layout(self, force: bool = False) -> int:
        """Get cached foreground layout (refreshes every 250ms)."""
        now = time.time()
        if force or now - self._cached_layout_time > 0.25:
            self._cached_layout = get_foreground_layout()
            self._cached_layout_time = now
        return self._cached_layout

    # =========================================================================
    # Keyboard Event Processing (called from hook via worker)
    # =========================================================================

    def process_key_down(self, vk: int, sc: int, flags: int) -> None:
        """Process a key down event (called from hook thread)."""
        assert self.worker is not None
        # Injected events ignored at hook level

        # Update input sequence
        with self._seq_lock:
            self._input_seq += 1
            seq = self._input_seq

        # Window change check
        if self._foreground_changed():
            self.worker._pending_corrections.clear()
            self.worker.clear_autocorrect_undo()
            self.phrase_buffer.clear()
            self.caret_guard.on_focus_change()

        # Trigger key (Ctrl) down
        if self.double_tap.is_trigger(vk):
            self.double_tap.on_trigger_down()
            return

        # Any other key dirties tap
        self.double_tap.on_other_key()

        # Correction in progress - ignore
        if self.worker._correcting:
            return

        # Real typing clears undo
        self.worker.clear_autocorrect_undo()

        # Modifiers only
        if vk in MODIFIER_VKS:
            return

        # Other modifiers down = hotkey
        if any_modifier_down():
            self.typed_scans.clear()
            self.phrase_buffer.clear()
            self.worker._pending_corrections.clear()
            return

        if not self._enabled.is_set():
            return

        # Backspace
        if vk == VK_BACK:
            if self.typed_scans:
                self.typed_scans.pop()
            self.phrase_buffer.backspace()
            self.worker._pending_corrections.clear()
            return

        # Navigation keys
        if vk in NAV_CLEAR_VKS:
            self.typed_scans.clear()
            self.phrase_buffer.clear()
            self.worker._pending_corrections.clear()
            self.caret_guard.on_nav()
            return

        # Word break (Space, Enter, Tab)
        if vk in WORD_BREAK_VKS:
            if (
                self.config.auto_correct_enabled
                and self.typed_scans
                and not self.caret_guard.on_word_break()
            ):
                scans = list(self.typed_scans)
                layout = self.get_layout()
                self.worker.enqueue(
                    CorrectionTask(
                        scans=scans,
                        layout=layout,
                        sep_vk=vk,
                        sep_shifted=False,
                        seq=seq,
                        is_manual=False,
                    )
                )
            else:
                self.caret_guard.on_word_break()

            self.phrase_buffer.add_sep(vk)
            self.typed_scans.clear()
            return

        # Letter keys (by scan code)
        from .conversion import LETTER_SCANS

        if sc in LETTER_SCANS:
            shifted = bool(user32.GetKeyState(VK_SHIFT) & 0x8000) ^ bool(
                user32.GetKeyState(VK_CAPITAL) & 0x0001
            )
            self.typed_scans.append((sc, shifted))
            if len(self.typed_scans) > 100:
                del self.typed_scans[:-50]
            self.phrase_buffer.add_letter(sc, shifted)
            return

        # Punctuation / other - word boundary
        term_shifted = bool(user32.GetKeyState(VK_SHIFT) & 0x8000)
        if (
            self.config.auto_correct_enabled
            and self.typed_scans
            and is_word_terminator(vk, term_shifted)
            and not self.caret_guard.on_word_break()
        ):
            scans = list(self.typed_scans)
            layout = self.get_layout()
            self.worker.enqueue(
                CorrectionTask(
                    scans=scans,
                    layout=layout,
                    sep_vk=vk,
                    sep_shifted=term_shifted,
                    seq=seq,
                    is_manual=False,
                )
            )
        else:
            self.caret_guard.on_word_break()

        self.typed_scans.clear()
        self.phrase_buffer.clear()

    def process_key_up(self, vk: int) -> None:
        """Process key up event (for double-tap detection)."""
        assert self.worker is not None
        if (
            self.double_tap.is_trigger(vk)
            and self.double_tap.on_trigger_up(time.time(), self.config.double_tap_window)
            and self._enabled.is_set()
        ):
            self.worker.enqueue(
                CorrectionTask(
                    scans=[],
                    layout=0,
                    sep_vk=0,
                    sep_shifted=False,
                    seq=0,
                    is_manual=True,
                    phrase=self.phrase_buffer.copy(),
                )
            )

    def _foreground_changed(self) -> bool:
        hwnd = user32.GetForegroundWindow()
        if hwnd != self._last_hwnd:
            self._last_hwnd = hwnd
            return True
        return False

    # =========================================================================
    # Tray Callbacks
    # =========================================================================

    def _toggle_enabled(self) -> None:
        self.config.enabled = not self.config.enabled
        if self.config.enabled:
            self._enabled.set()
        else:
            self._enabled.clear()
            # Clear buffers when disabled
            self.typed_scans.clear()
            self.phrase_buffer.clear()
            assert self.worker is not None
            self.worker._pending_corrections.clear()
            self.worker.clear_autocorrect_undo()
        if self.config_manager:
            self.config_manager.save_config()
        if self.tray:
            self.tray.update_menu()

    def _toggle_auto(self) -> None:
        self.config.auto_correct_enabled = not self.config.auto_correct_enabled
        if self.config_manager:
            self.config_manager.save_config()
        if self.tray:
            self.tray.update_menu()

    def _toggle_learning(self) -> None:
        self.config.learning_enabled = not self.config.learning_enabled
        if not self.config.learning_enabled:
            assert self.worker is not None
            self.worker.clear_autocorrect_undo()
        if self.config_manager:
            self.config_manager.save_config()
        if self.tray:
            self.tray.update_menu()

    def _forget_learned(self) -> None:
        total = (
            len(self.learning.force_en)
            + len(self.learning.force_uk)
            + len(self.learning.block_uk)
            + len(self.learning.block_en)
        )
        if total == 0:
            return
        # Confirmation dialog
        resp = ctypes.windll.user32.MessageBoxW(
            None,
            f"Забути всі вивчені слова ({total})?\nЦю дію не можна скасувати.",
            "Qwasda — забути вивчене",
            0x04 | 0x20 | 0x100,
        )
        if resp != 6:  # IDYES
            return
        self.learning.forget_all()
        if self.tray:
            self.tray.notify("Пам'ять очищено — вивчені слова забуто.", "Qwasda")
            self.tray.update_menu()

    def _toggle_startup(self) -> None:
        startup_dir = (
            Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        )
        bat_path = startup_dir / "Qwasda.bat"

        if bat_path.exists():
            bat_path.unlink()
        else:
            startup_dir.mkdir(parents=True, exist_ok=True)
            if getattr(sys, "frozen", False):
                content = f'@echo off\nstart "" "{sys.executable}"\n'
            else:
                pw = sys.executable.replace("python.exe", "pythonw.exe")
                content = f'@echo off\nstart "" "{pw}" "{os.path.abspath(__file__)}"\n'
            bat_path.write_text(content, encoding="utf-8")

        if self.tray:
            self.tray.update_menu()

    def _request_exit(self) -> None:
        self._running = False
        if self.kb_hook:
            self.kb_hook.uninstall()
        if self.worker:
            self.worker.shutdown()
        user32.PostThreadMessageW(kernel32.GetCurrentThreadId(), WM_QUIT, 0, 0)
        if self.tray:
            self.tray.stop()


# =============================================================================
# Module-level entry point
# =============================================================================


def main() -> int:
    """Entry point for `python -m qwasda` or `qwasda.exe`."""
    config = Config()
    engine = QwasdaEngine(config)
    return engine.run()


if __name__ == "__main__":
    sys.exit(main())
