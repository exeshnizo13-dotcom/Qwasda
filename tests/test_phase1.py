"""Regression tests for Phase 1 reliability components."""

from __future__ import annotations

import logging
import sys
import threading
import time
from unittest.mock import MagicMock


def test_request_elevation_preserves_arguments_and_adds_marker(monkeypatch):
    from qwasda import admin

    monkeypatch.setattr(sys, "argv", ["qwasda.exe", "--config", "custom.json"])
    called = {}

    def fake_run_as_admin(*, args):
        called["args"] = args
        return True, None

    monkeypatch.setattr(admin, "run_as_admin", fake_run_as_admin)
    assert admin.request_elevation() == (True, None)
    assert called["args"] == ["--config", "custom.json", admin.ELEVATED_ARG]


def test_request_elevation_does_not_duplicate_marker(monkeypatch):
    from qwasda import admin

    monkeypatch.setattr(sys, "argv", ["qwasda.exe", admin.ELEVATED_ARG])
    monkeypatch.setattr(admin, "run_as_admin", lambda **kwargs: (True, None))
    admin.request_elevation()
    assert sys.argv.count(admin.ELEVATED_ARG) == 1


def test_json_logging_writes_structured_record(tmp_path):
    from qwasda.logging_config import initialize_logging, shutdown_logging

    initialize_logging(log_dir=tmp_path, console=False)
    try:
        logging.getLogger("phase1.test").info("hello", extra={"operation": "test"})
        for handler in logging.getLogger().handlers:
            handler.flush()
        record = (tmp_path / "qwasda.json.log").read_text(encoding="utf-8").strip().splitlines()[-1]
        assert '"message": "hello"' in record
        assert '"operation": "test"' in record
    finally:
        shutdown_logging()


def test_crash_reporter_restores_exception_hook(tmp_path):
    from qwasda.crash_reporting import CrashReporter

    original = sys.excepthook
    original_thread = threading.excepthook
    reporter = CrashReporter()
    reporter.initialize(tmp_path)
    assert sys.excepthook != original
    assert threading.excepthook != original_thread
    reporter.shutdown()
    assert sys.excepthook is original
    assert threading.excepthook is original_thread


class _HealthyCheck:
    name = "fake"
    interval = 0.0

    def should_run(self):
        return True

    def run(self):
        from qwasda.health_monitor import ComponentHealth, HealthStatus

        return ComponentHealth(self.name, HealthStatus.HEALTHY, time.time())


def test_health_monitor_registers_and_runs_checks():
    from qwasda.health_monitor import HealthMonitor, HealthStatus

    monitor = HealthMonitor(check_interval=60, metrics_interval=60)
    monitor.register_check(_HealthyCheck())
    result = monitor.run_checks()
    assert result["fake"].status is HealthStatus.HEALTHY
    assert monitor.get_overall_status() is HealthStatus.HEALTHY


def test_watchdog_calls_callback_once_after_timeout():
    from qwasda.health_monitor import Watchdog

    called = threading.Event()
    watchdog = Watchdog(timeout=0.05, callback=called.set)
    watchdog.start()
    try:
        assert called.wait(1.0)
        time.sleep(0.06)
        assert called.is_set()
    finally:
        watchdog.stop()


def test_engine_cleanup_is_idempotent_and_cleans_in_reverse_order(monkeypatch):
    from qwasda import engine

    calls = []
    app = engine.QwasdaEngine.__new__(engine.QwasdaEngine)
    app._cleanup_done = False
    app._running = True
    app._health_monitor = MagicMock()
    app.tray = MagicMock()
    app.kb_hook = MagicMock()
    app.mouse_hook = MagicMock()
    app.worker = MagicMock()
    app.single_instance = MagicMock()

    for resource, method in (
        (app._health_monitor, "stop"),
        (app.tray, "stop"),
        (app.kb_hook, "uninstall"),
        (app.mouse_hook, "uninstall"),
        (app.worker, "shutdown"),
        (app.single_instance, "release"),
    ):
        getattr(resource, method).side_effect = lambda r=resource, m=method: calls.append(
            (id(r), m)
        )

    monkeypatch.setattr(engine, "shutdown_logging", lambda: calls.append(("global", "logging")))
    monkeypatch.setattr(
        engine, "shutdown_crash_reporting", lambda: calls.append(("global", "crash"))
    )
    app._cleanup()
    app._cleanup()

    assert [method for _, method in calls] == [
        "stop",
        "stop",
        "uninstall",
        "uninstall",
        "shutdown",
        "release",
        "logging",
        "crash",
    ]
    assert calls[:5] == [
        (id(app._health_monitor), "stop"),
        (id(app.tray), "stop"),
        (id(app.mouse_hook), "uninstall"),
        (id(app.kb_hook), "uninstall"),
        (id(app.worker), "shutdown"),
    ]
    assert app._cleanup_done


def test_exit_callback_posts_quit_to_main_message_thread(monkeypatch):
    from qwasda import engine

    posted = []
    app = engine.QwasdaEngine.__new__(engine.QwasdaEngine)
    app._running = True
    app._message_thread_id = 4242
    app.tray = MagicMock()
    monkeypatch.setattr(
        engine.user32,
        "PostThreadMessageW",
        lambda thread_id, message, wparam, lparam: posted.append(
            (thread_id, message, wparam, lparam)
        ),
    )

    app._request_exit()

    assert not app._running
    assert posted == [(4242, engine.WM_QUIT, 0, 0)]
    app.tray.stop.assert_called_once_with()


def test_correction_worker_state_lock_is_reentrant():
    from qwasda.hooks import CorrectionWorker

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(),
        get_layout_func=MagicMock(return_value=0x0409),
    )
    try:
        assert worker._lock.acquire(blocking=False)
        try:
            assert worker._lock.acquire(blocking=False)
            worker._lock.release()
        finally:
            worker._lock.release()
    finally:
        worker.shutdown()


def test_replacement_switches_layout_before_separator(monkeypatch):
    from qwasda.hooks import CorrectionWorker
    from qwasda import LANG_UKRAINIAN

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(),
        get_layout_func=MagicMock(return_value=0x0409),
    )
    try:
        events = []
        monkeypatch.setattr("qwasda.hooks.send_backspaces", lambda count: None)
        monkeypatch.setattr(
            "qwasda.hooks.send_unicode_string",
            lambda text: events.append(("text", text)),
        )
        monkeypatch.setattr(
            "qwasda.hooks.set_foreground_layout",
            lambda layout: events.append(("layout", layout)),
        )
        monkeypatch.setattr(
            worker,
            "_send_sep",
            lambda *sep: events.append(("sep", sep)),
        )

        worker._replace_single(3, "дякую", LANG_UKRAINIAN, 0xBF, True)

        assert events == [("layout", LANG_UKRAINIAN), ("text", "дякую"), ("sep", (0xBF, True))]
    finally:
        worker.shutdown()


def test_pending_correction_keeps_short_middle_word_and_target_layout(monkeypatch):
    from qwasda import LANG_ENGLISH, LANG_UKRAINIAN
    from qwasda.conversion import SCAN_ENG
    from qwasda.hooks import CorrectionTask, CorrectionWorker

    scan_by_char = {character: scan for scan, character in SCAN_ENG.items()}

    def scans(text):
        return [(scan_by_char[character], False) for character in text]

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(min_autocorrect_len=2, min_en_to_uk=3),
        get_layout_func=MagicMock(return_value=LANG_ENGLISH),
    )
    replacements = iter(
        [
            ("\u0442\u0435\u043f\u0435\u0440", LANG_UKRAINIAN),
            (None, None),
            (None, None),
        ]
    )
    batch = MagicMock()
    monkeypatch.setattr(
        "qwasda.conversion.autocorrect_replacement",
        lambda *args: next(replacements),
    )
    monkeypatch.setattr(worker, "_replace_batch", batch)
    monkeypatch.setattr("qwasda.hooks.time.sleep", lambda _seconds: None)

    try:
        worker._input_seq = 2
        worker._do_auto(
            CorrectionTask(scans("ntgth"), LANG_ENGLISH, 0x20, False, seq=1)
        )
        worker._input_seq = 3
        worker._do_auto(CorrectionTask(scans("ws"), LANG_ENGLISH, 0xBC, False, seq=2))

        assert worker._pending_corrections == [
            (5, "\u0442\u0435\u043f\u0435\u0440", LANG_UKRAINIAN, 0x20, False),
            (2, "\u0446\u0456", LANG_UKRAINIAN, 0xBC, False),
        ]

        worker._do_auto(
            CorrectionTask(scans("next"), LANG_ENGLISH, 0x20, False, seq=3)
        )

        batch.assert_called_once_with(
            [
                (5, "\u0442\u0435\u043f\u0435\u0440", LANG_UKRAINIAN, 0x20, False),
                (2, "\u0446\u0456", LANG_UKRAINIAN, 0xBC, False),
            ],
            4,
            "\u0442\u0443\u0447\u0435",
            LANG_UKRAINIAN,
            0x20,
            False,
            True,
        )
    finally:
        worker.shutdown()


def test_auto_correction_uses_layout_captured_at_word_boundary(monkeypatch):
    from qwasda import LANG_ENGLISH, LANG_UKRAINIAN
    from qwasda.hooks import CorrectionTask, CorrectionWorker

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(min_autocorrect_len=2, min_en_to_uk=2),
        get_layout_func=MagicMock(return_value=LANG_UKRAINIAN),
    )
    replacement = MagicMock(return_value=(None, None))
    monkeypatch.setattr("qwasda.conversion.autocorrect_replacement", replacement)
    monkeypatch.setattr("qwasda.hooks.time.sleep", lambda _seconds: None)

    try:
        worker._input_seq = 1
        worker._do_auto(
            CorrectionTask([(0x19, True)], LANG_ENGLISH, 0xBF, True, seq=1)
        )

        assert replacement.call_args.args[1] == LANG_ENGLISH
    finally:
        worker.shutdown()


def test_empty_space_flushes_pending_word_terminated_by_punctuation(monkeypatch):
    from qwasda import LANG_ENGLISH, LANG_UKRAINIAN
    from qwasda.hooks import CorrectionTask, CorrectionWorker

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(min_autocorrect_len=2, min_en_to_uk=2),
        get_layout_func=MagicMock(return_value=LANG_ENGLISH),
    )
    replacements = iter([("Здається", LANG_UKRAINIAN), (None, None)])
    batch = MagicMock()
    sleeper = MagicMock()
    monkeypatch.setattr(
        "qwasda.conversion.autocorrect_replacement",
        lambda *args: next(replacements),
    )
    monkeypatch.setattr(worker, "_replace_batch", batch)
    monkeypatch.setattr("qwasda.hooks.time.sleep", sleeper)

    try:
        word_scans = [(index, False) for index in range(9)]
        worker._input_seq = 2
        worker._do_auto(
            CorrectionTask(word_scans, LANG_ENGLISH, 0xBF, True, seq=1)
        )
        assert worker._pending_corrections == [
            (9, "Здається", LANG_UKRAINIAN, 0xBF, True)
        ]

        worker._do_auto(
            CorrectionTask([], LANG_ENGLISH, 0x20, False, seq=2)
        )

        batch.assert_called_once_with(
            [(9, "Здається", LANG_UKRAINIAN, 0xBF, True)],
            0,
            "",
            LANG_UKRAINIAN,
            0x20,
            False,
            True,
        )
        assert worker._pending_corrections == []
        sleeper.assert_called_once_with(0.03)
    finally:
        worker.shutdown()


def test_keyboard_hook_enqueues_empty_space_after_punctuation(monkeypatch):
    import ctypes

    from qwasda import LANG_ENGLISH
    from qwasda.hooks import DoubleTapDetector, KeyboardHook
    from qwasda.win32 import KBDLLHOOKSTRUCT, WM_KEYDOWN

    worker = MagicMock()
    worker._correcting = False
    worker._input_seq = 7
    phrase_buffer = MagicMock()
    caret_guard = MagicMock()
    caret_guard.on_word_break.return_value = False
    enabled = threading.Event()
    enabled.set()
    hook = KeyboardHook(
        worker=worker,
        phrase_buffer=phrase_buffer,
        caret_guard=caret_guard,
        double_tap=DoubleTapDetector(),
        config=MagicMock(auto_correct_enabled=True, double_tap_window=0.4),
        enabled_flag=enabled,
        get_layout_func=MagicMock(return_value=LANG_ENGLISH),
    )
    monkeypatch.setattr(hook, "_foreground_changed", lambda: False)
    monkeypatch.setattr("qwasda.hooks.any_modifier_down", lambda: False)
    monkeypatch.setattr("qwasda.hooks._call_next_hook", lambda *args: 0)
    event = KBDLLHOOKSTRUCT(vkCode=0x20, scanCode=0x39, flags=0, time=0)

    hook._hook_proc(0, WM_KEYDOWN, ctypes.addressof(event))

    task = worker.enqueue.call_args.args[0]
    assert task.scans == []
    assert task.layout == LANG_ENGLISH
    assert task.sep_vk == 0x20
    assert task.seq == 7
    assert task.input_reserved is False
    worker.reserve_input.assert_not_called()


def test_keyboard_hook_suppresses_enter_until_word_is_checked(monkeypatch):
    import ctypes

    from qwasda import LANG_ENGLISH
    from qwasda.hooks import DoubleTapDetector, KeyboardHook
    from qwasda.win32 import KBDLLHOOKSTRUCT, VK_RETURN, WM_KEYDOWN

    worker = MagicMock()
    worker._correcting = False
    worker._input_seq = 9
    caret_guard = MagicMock()
    caret_guard.on_word_break.return_value = False
    enabled = threading.Event()
    enabled.set()
    hook = KeyboardHook(
        worker=worker,
        phrase_buffer=MagicMock(),
        caret_guard=caret_guard,
        double_tap=DoubleTapDetector(),
        config=MagicMock(auto_correct_enabled=True, double_tap_window=0.4),
        enabled_flag=enabled,
        get_layout_func=MagicMock(return_value=LANG_ENGLISH),
    )
    hook._typed_scans = [(0x19, True), (0x26, False), (0x21, False)]
    monkeypatch.setattr(hook, "_foreground_changed", lambda: False)
    monkeypatch.setattr("qwasda.hooks.any_modifier_down", lambda: False)
    monkeypatch.setattr("qwasda.hooks._call_next_hook", lambda *args: 0)
    event = KBDLLHOOKSTRUCT(vkCode=VK_RETURN, scanCode=0x1C, flags=0, time=0)

    result = hook._hook_proc(0, WM_KEYDOWN, ctypes.addressof(event))

    assert result == 1
    task = worker.enqueue.call_args.args[0]
    assert task.sep_vk == VK_RETURN
    assert task.separator_suppressed is True
    assert task.input_reserved is True


def test_suppressed_enter_is_replayed_once_after_correction(monkeypatch):
    from qwasda import LANG_ENGLISH, LANG_UKRAINIAN
    from qwasda.hooks import CorrectionTask, CorrectionWorker
    from qwasda.win32 import VK_RETURN

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(min_autocorrect_len=2, min_en_to_uk=2, learning_enabled=False),
        get_layout_func=MagicMock(return_value=LANG_ENGLISH),
    )
    replacement = MagicMock(return_value=("Здається", LANG_UKRAINIAN))
    replace_single = MagicMock()
    sleeper = MagicMock()
    monkeypatch.setattr("qwasda.conversion.autocorrect_replacement", replacement)
    monkeypatch.setattr(worker, "_replace_single", replace_single)
    monkeypatch.setattr("qwasda.hooks.time.sleep", sleeper)
    scans = [(index, False) for index in range(9)]

    try:
        worker._input_seq = 4
        worker._do_auto(
            CorrectionTask(
                scans,
                LANG_ENGLISH,
                VK_RETURN,
                False,
                seq=4,
                separator_suppressed=True,
            )
        )

        replace_single.assert_called_once_with(
            9, "Здається", LANG_UKRAINIAN, VK_RETURN, False
        )
        sleeper.assert_not_called()
    finally:
        worker.shutdown()


def test_suppressed_enter_is_replayed_once_for_valid_word(monkeypatch):
    from qwasda import LANG_ENGLISH
    from qwasda.hooks import CorrectionTask, CorrectionWorker
    from qwasda.win32 import VK_RETURN

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(min_autocorrect_len=2, min_en_to_uk=2),
        get_layout_func=MagicMock(return_value=LANG_ENGLISH),
    )
    send_sep = MagicMock()
    monkeypatch.setattr(
        "qwasda.conversion.autocorrect_replacement",
        lambda *args: (None, None),
    )
    monkeypatch.setattr(worker, "_send_sep", send_sep)
    monkeypatch.setattr("qwasda.hooks.time.sleep", lambda _seconds: None)

    try:
        worker._input_seq = 5
        worker._do_auto(
            CorrectionTask(
                [(0x23, False), (0x12, False)],
                LANG_ENGLISH,
                VK_RETURN,
                False,
                seq=5,
                separator_suppressed=True,
            )
        )

        send_sep.assert_called_once_with(VK_RETURN, False)
    finally:
        worker.shutdown()


def test_keyboard_hook_reserves_input_at_first_word_boundary():
    from qwasda import LANG_ENGLISH
    from qwasda.hooks import DoubleTapDetector, KeyboardHook

    worker = MagicMock()
    worker._input_seq = 11
    hook = KeyboardHook(
        worker=worker,
        phrase_buffer=MagicMock(),
        caret_guard=MagicMock(),
        double_tap=DoubleTapDetector(),
        config=MagicMock(),
        enabled_flag=threading.Event(),
        get_layout_func=MagicMock(),
    )
    scans = [(0x19, True), (0x26, False), (0x21, False)]

    hook._enqueue_auto(scans, LANG_ENGLISH, 0xBF, True)

    worker.reserve_input.assert_called_once_with()
    task = worker.enqueue.call_args.args[0]
    assert task.scans == scans
    assert task.seq == 11
    assert task.input_reserved is True


def test_worker_releases_input_reservation_after_boundary_task(monkeypatch):
    from qwasda import LANG_ENGLISH
    from qwasda.hooks import CorrectionTask, CorrectionWorker

    worker = CorrectionWorker(
        dict_loader=MagicMock(),
        learning=MagicMock(),
        config=MagicMock(min_autocorrect_len=2, min_en_to_uk=2),
        get_layout_func=MagicMock(return_value=LANG_ENGLISH),
    )
    monkeypatch.setattr(
        "qwasda.conversion.autocorrect_replacement",
        lambda *args: (None, None),
    )
    monkeypatch.setattr("qwasda.hooks.time.sleep", lambda _seconds: None)

    try:
        worker.reserve_input()
        worker.enqueue(
            CorrectionTask(
                [(0x19, True)],
                LANG_ENGLISH,
                0x20,
                False,
                seq=0,
                input_reserved=True,
            )
        )
        worker._queue.join()

        assert worker._input_gate.acquire(blocking=False)
        worker._input_gate.release()
    finally:
        if worker._input_gate.locked():
            worker._input_gate.release()
        worker.shutdown()


def test_mouse_click_resets_keyboard_context_and_suppresses_fragment_correction():
    from qwasda.hooks import MouseHook

    phrase_buffer = MagicMock()
    worker = MagicMock()
    keyboard_hook = MagicMock()
    caret_guard = MagicMock()
    mouse_hook = MouseHook(phrase_buffer, worker, keyboard_hook, caret_guard)

    mouse_hook._reset_input_context()

    worker.increment_seq.assert_called_once_with()
    keyboard_hook.clear_typed_scans.assert_called_once_with()
    phrase_buffer.clear.assert_called_once_with()
    worker._pending_corrections.clear.assert_called_once_with()
    worker.clear_autocorrect_undo.assert_called_once_with()
    caret_guard.on_nav.assert_called_once_with()
