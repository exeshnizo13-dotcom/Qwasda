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
