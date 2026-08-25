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
import traceback
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
    from .settings_ui import SettingsWindow
    from .single_instance import ShutdownSignal, SingleInstance
    from .tray import TrayIcon
    from .updater import UpdateSnapshot
    from .win32 import get_foreground_layout

from .admin import get_integrity_level, is_admin
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
from .hotkeys import (
    HotkeyAction,
    HotkeyBindings,
    HotkeyError,
    HotkeyManager,
    default_hotkeys,
)
from .learning import LearningManager
from .logging_config import get_logger, initialize_logging, shutdown_logging
from .settings_ui import SettingsWindow
from .single_instance import ShutdownSignal, SingleInstance
from .startup import migrate_legacy_startup
from .startup import set_enabled as set_startup_enabled
from .statistics import StatisticsManager
from .tray import TrayIcon
from .updater import UpdateChannel, UpdateManager, UpdateSnapshot
from .win32 import (
    MODIFIER_VKS,
    NAV_CLEAR_VKS,
    VK_BACK,
    VK_CAPITAL,
    VK_SHIFT,
    WM_HOTKEY,
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
        self.statistics = StatisticsManager(config.app_dir, enabled=config.statistics_enabled)
        self.single_instance = SingleInstance()
        self.shutdown_signal = ShutdownSignal()
        self._shutdown_thread: threading.Thread | None = None
        self._message_thread_id: int | None = None
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
        self.hotkeys = HotkeyManager()

        # Hooks
        self.kb_hook: KeyboardHook | None = None
        self.mouse_hook: MouseHook | None = None
        self.worker: CorrectionWorker | None = None

        # Tray
        self.tray: TrayIcon | None = None
        self.settings: SettingsWindow | None = None
        self.updater: UpdateManager | None = None

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
            "open_settings": self._open_settings,
            "exit": self._request_exit,
        }
        self.dict_loader.on_loaded = self._on_dictionaries_loaded

    def _get_version(self) -> str:
        from .version import __version__

        return __version__

    # =========================================================================
    # Public API
    # =========================================================================

    def run(self) -> int:
        """Main entry point. Returns exit code and always releases resources."""
        try:
            return self._run()
        except Exception:
            self._write_startup_failure(traceback.format_exc())
            logging.getLogger("qwasda.engine").exception("Qwasda startup or runtime failure")
            return 1
        finally:
            self._cleanup()

    def _write_startup_failure(self, details: str) -> None:
        """Best-effort fallback log when structured logging is not available yet."""
        try:
            log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Qwasda" / "Logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "startup.log").open("a", encoding="utf-8") as fh:
                fh.write(details)
                if not details.endswith("\n"):
                    fh.write("\n")
        except OSError:
            pass

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

        # Do not hard-require elevation on startup: keyboard hooks work for
        # normal apps without admin, and forcing UAC here can make the app
        # appear to "not start" if the prompt is declined or blocked.
        if not admin_status:
            logger.warning(
                "Running without administrator privileges; elevated windows may be unavailable"
            )

        # Single instance check
        if not self.single_instance.acquire():
            ctypes.windll.user32.MessageBoxW(None, "Qwasda вже запущено.", "Qwasda", 0x40)
            return 0

        migrate_legacy_startup()
        self.shutdown_signal.create()
        self._message_thread_id = kernel32.GetCurrentThreadId()
        self._shutdown_thread = threading.Thread(
            target=self._wait_for_shutdown, name="qwasda-shutdown", daemon=True
        )
        self._shutdown_thread.start()

        # Load config & learned words
        config_manager = ConfigManager(self.config.app_dir)
        config_manager.load()
        self.config_manager = config_manager
        self.config = config_manager.config
        self.statistics = StatisticsManager(
            self.config.app_dir, enabled=self.config.statistics_enabled
        )
        self.statistics.load()
        self.statistics.start()
        self.updater = UpdateManager(
            self.config.app_dir,
            enabled=self.config.update_checks_enabled,
            channel=self.config.update_channel,
            callback=self._on_update_snapshot,
        )
        try:
            self.hotkeys.apply(self.config.hotkeys)
        except HotkeyError:
            logger.exception("Configured hotkeys unavailable; restoring defaults")
            self.config.hotkeys = default_hotkeys()
            self.hotkeys.apply(self.config.hotkeys)
            config_manager.save_config()
        self.double_tap.set_trigger_vks(self.hotkeys.double_tap_vks())
        # LearningManager loads in __init__

        # Apply config to engine state
        self._enabled.set() if self.config.enabled else self._enabled.clear()

        # Initialize worker
        self.worker = CorrectionWorker(
            dict_loader=self.dict_loader,
            learning=self.learning,
            config=self.config,
            get_layout_func=self.get_layout,
            statistics=self.statistics,
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

        self.mouse_hook = MouseHook(
            self.phrase_buffer,
            self.worker,
            self.kb_hook,
            self.caret_guard,
        )
        self.mouse_hook.install(hinst)

        self.settings = SettingsWindow(
            config=self.config,
            dict_loader=self.dict_loader,
            on_dictionaries_changed=self._on_custom_dictionaries_changed,
            on_hotkeys_changed=self._apply_hotkeys,
            statistics=self.statistics,
            on_statistics_enabled=self._apply_statistics_enabled,
            on_statistics_cleared=self._clear_statistics,
            updater=self.updater,
            on_updates_enabled=self._apply_updates_enabled,
            on_update_channel=self._apply_update_channel,
            on_update_apply=self._apply_update,
        )

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
            on_open_settings=self._tray_callbacks["open_settings"],
            on_exit=self._tray_callbacks["exit"],
            version=self.version,
            statistics=self.statistics,
            on_check_updates=self._check_updates,
        )
        self.tray.run()
        # A cache hit is cheap; a cache miss is built in a low-priority
        # background thread while the tray and hooks remain responsive.
        self.dict_loader.load_cached_or_async()
        if self.updater and self.config.update_checks_enabled:
            self.updater.check(automatic=True)

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
            if msg.message == WM_HOTKEY:
                self._handle_hotkey(int(msg.wParam))
                continue
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

    def _on_dictionaries_loaded(self, loaded: bool) -> None:
        """React to dictionary loading finishing in the background."""
        logger = logging.getLogger("qwasda.engine")
        logger.info(
            "Dictionary loading finished",
            extra={
                "loaded": loaded,
                "en_words": len(self.dict_loader.dict_en),
                "uk_words": len(self.dict_loader.dict_uk),
            },
        )
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception:
                logger.exception("Failed to refresh tray after dictionary load")

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
            ("settings", self._stop_settings),
            ("updater", lambda: self.updater.stop() if self.updater else None),
            ("hotkeys", self._close_hotkeys),
            ("mouse hook", lambda: self.mouse_hook.uninstall() if self.mouse_hook else None),
            ("keyboard hook", lambda: self.kb_hook.uninstall() if self.kb_hook else None),
            ("worker", lambda: self.worker.shutdown() if self.worker else None),
            (
                "dictionary cache",
                lambda: self.dict_loader.close() if getattr(self, "dict_loader", None) else None,
            ),
            ("single instance", self.single_instance.release),
            ("shutdown signal", self._close_shutdown_signal),
            ("statistics", self._stop_statistics),
            ("logging", shutdown_logging),
            ("crash reporting", shutdown_crash_reporting),
        )
        for name, cleanup in cleanup_steps:
            started = time.perf_counter()
            try:
                cleanup()
                logging.getLogger("qwasda.engine").info(
                    "Cleanup step finished",
                    extra={"component": name, "seconds": round(time.perf_counter() - started, 3)},
                )
            except Exception:
                logging.getLogger("qwasda.engine").exception(
                    "Cleanup failed", extra={"component": name}
                )

    def _wait_for_shutdown(self) -> None:
        if self.shutdown_signal.wait():
            self._post_quit()

    def _post_quit(self) -> None:
        """Stop the main Win32 message loop from any callback thread."""
        self._running = False
        thread_id = self._message_thread_id
        if thread_id is not None:
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)

    def _close_shutdown_signal(self) -> None:
        self.shutdown_signal.close()

    def _check_updates(self) -> None:
        if self.updater:
            self.updater.check()
        self._open_settings("updates")

    def _on_update_snapshot(self, snapshot: UpdateSnapshot) -> None:
        if (
            snapshot.status == "available"
            and snapshot.available
            and self.tray
            and self.updater
            and self.updater.client.consume_notification(snapshot.available.version)
        ):
            self.tray.notify(
                f"Доступне оновлення Qwasda {snapshot.available.version}",
                "Qwasda",
            )
            self.tray.update_menu()

    def _signal_handler(self, signum: int, frame: object) -> None:
        self._post_quit()

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
            self.worker.clear_pending()
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
            self.worker.clear_pending()
            return

        if not self._enabled.is_set():
            return

        # Backspace
        if vk == VK_BACK:
            if self.typed_scans:
                self.typed_scans.pop()
            self.phrase_buffer.backspace()
            self.worker.clear_pending()
            return

        # Navigation keys
        if vk in NAV_CLEAR_VKS:
            self.typed_scans.clear()
            self.phrase_buffer.clear()
            self.worker.clear_pending()
            self.caret_guard.on_nav()
            return

        # Word break (Space, Enter, Tab)
        if vk in WORD_BREAK_VKS:
            suppressed = self.caret_guard.on_word_break()
            if self.config.auto_correct_enabled and not suppressed:
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
            self.worker.clear_pending()
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
        set_startup_enabled(not self._startup_enabled())

        if self.tray:
            self.tray.update_menu()

    def _open_settings(self, tab: str = "dictionaries") -> None:
        if self.settings:
            self.settings.show(tab)

    def _stop_settings(self) -> None:
        settings = getattr(self, "settings", None)
        if settings is not None:
            settings.stop()

    def _close_hotkeys(self) -> None:
        hotkeys = getattr(self, "hotkeys", None)
        if hotkeys is not None:
            hotkeys.close()

    def _stop_statistics(self) -> None:
        statistics = getattr(self, "statistics", None)
        if statistics is not None:
            statistics.stop()

    def _apply_statistics_enabled(self, enabled: bool) -> str | None:
        self.statistics.set_enabled(enabled)
        self.config.statistics_enabled = enabled
        if self.config_manager:
            self.config_manager.save_config()
        if self.tray:
            self.tray.update_menu()
        return None

    def _apply_updates_enabled(self, enabled: bool) -> str | None:
        if self.updater:
            self.updater.enabled = enabled
        self.config.update_checks_enabled = enabled
        if self.config_manager:
            self.config_manager.save_config()
        return None

    def _apply_update_channel(self, channel: UpdateChannel) -> str | None:
        if self.updater:
            self.updater.channel = channel
        self.config.update_channel = channel
        if self.config_manager:
            self.config_manager.save_config()
        return None

    def _apply_update(self) -> str | None:
        if self.updater is None:
            return "Updater не ініціалізований"
        try:
            message = self.updater.start_apply(Path(sys.executable))
        except Exception as exc:
            return str(exc)
        self._request_exit()
        return message

    @staticmethod
    def _startup_enabled() -> bool:
        from .startup import is_enabled

        return is_enabled()

    def _clear_statistics(self) -> str | None:
        self.statistics.clear()
        if self.tray:
            self.tray.update_menu()
        return None

    def _apply_hotkeys(self, bindings: HotkeyBindings) -> str | None:
        try:
            self.hotkeys.apply(bindings)
        except HotkeyError as exc:
            return str(exc)
        self.config.hotkeys = self.hotkeys.bindings
        self.double_tap.set_trigger_vks(self.hotkeys.double_tap_vks())
        if self.config_manager:
            self.config_manager.save_config()
        if self.tray:
            self.tray.update_menu()
        return None

    def _handle_hotkey(self, hotkey_id: int) -> None:
        if not self._running or self.worker is None or self.worker._correcting:
            return
        action = self.hotkeys.action_for_id(hotkey_id)
        if action == HotkeyAction.MANUAL_CONVERSION:
            if self._enabled.is_set():
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
        elif action == HotkeyAction.TOGGLE_ENABLED:
            self._toggle_enabled()
        elif action == HotkeyAction.TOGGLE_AUTOCORRECT:
            self._toggle_auto()

    def _on_custom_dictionaries_changed(self) -> None:
        if self.tray:
            self.tray.update_menu()

    def _request_exit(self) -> None:
        self._post_quit()
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
