"""
Health monitoring and watchdog for Qwasda.

Provides:
- Component health checks
- Watchdog timer for detecting hangs
- Performance metrics collection
- Automatic recovery actions
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import psutil


class HealthStatus(Enum):
    """Health status levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    """Health status of a component."""

    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_check: float = 0.0
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "last_check": (
                datetime.fromtimestamp(self.last_check).isoformat() if self.last_check else None
            ),
            "message": self.message,
            "metadata": self.metadata,
        }


@dataclass
class SystemMetrics:
    """System resource metrics."""

    timestamp: float
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    thread_count: int
    handle_count: int
    disk_usage_mb: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.fromtimestamp(self.timestamp).isoformat(),
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "memory_percent": self.memory_percent,
            "thread_count": self.thread_count,
            "handle_count": self.handle_count,
            "disk_usage_mb": self.disk_usage_mb,
        }


class HealthCheck:
    """Base class for health checks."""

    def __init__(self, name: str, interval: float = 30.0):
        self.name = name
        self.interval = interval
        self._last_run = 0.0
        self._last_result: ComponentHealth | None = None

    def check(self) -> ComponentHealth:
        """Perform the health check. Override in subclasses."""
        raise NotImplementedError

    def should_run(self) -> bool:
        return time.time() - self._last_run >= self.interval

    def run(self) -> ComponentHealth:
        """Run the check and cache result."""
        self._last_run = time.time()
        try:
            self._last_result = self.check()
        except Exception as e:
            self._last_result = ComponentHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                last_check=self._last_run,
                message=f"Check failed: {e}",
            )
        return self._last_result

    def get_last_result(self) -> ComponentHealth | None:
        return self._last_result


class Watchdog:
    """
    Watchdog timer for detecting application hangs.

    The watchdog runs in a separate thread and expects periodic
    "heartbeat" calls. If no heartbeat is received within the timeout,
    it triggers a callback (e.g., to log, dump state, or restart).
    """

    def __init__(
        self,
        timeout: float = 30.0,
        callback: Callable[[], None] | None = None,
        name: str = "Watchdog",
    ):
        self.timeout = timeout
        self.callback = callback
        self.name = name
        self._last_heartbeat = time.time()
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._triggered = False

    def start(self) -> None:
        """Start the watchdog."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._last_heartbeat = time.time()
            self._triggered = False
            self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
            self._thread.start()

    def stop(self) -> None:
        """Stop the watchdog."""
        with self._lock:
            self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)

    def heartbeat(self) -> None:
        """Record a heartbeat (call periodically from main thread)."""
        with self._lock:
            self._last_heartbeat = time.time()
            self._triggered = False

    def _run(self) -> None:
        """Watchdog loop."""
        while True:
            with self._lock:
                if not self._running:
                    break
                elapsed = time.time() - self._last_heartbeat
                if elapsed >= self.timeout and not self._triggered:
                    self._triggered = True
                    callback = self.callback
                else:
                    callback = None

            if callback:
                with contextlib.suppress(Exception):
                    callback()

            time.sleep(min(1.0, self.timeout / 10))

    def is_alive(self) -> bool:
        """Check if watchdog thread is running."""
        return self._thread is not None and self._thread.is_alive()


class HealthMonitor:
    """
    Central health monitoring for Qwasda.

    Manages:
    - Component health checks
    - System metrics collection
    - Watchdog for hang detection
    - Alerting on degradation
    """

    def __init__(
        self,
        check_interval: float = 30.0,
        metrics_interval: float = 60.0,
        watchdog_timeout: float = 30.0,
        watchdog_callback: Callable[[], None] | None = None,
    ):
        self.check_interval = check_interval
        self.metrics_interval = metrics_interval
        self.logger = logging.getLogger("qwasda.health")

        # Components
        self._checks: dict[str, HealthCheck] = {}
        self._health: dict[str, ComponentHealth] = {}
        self._metrics_history: list[SystemMetrics] = []
        self._max_metrics_history = 1000

        # Watchdog
        self.watchdog = Watchdog(watchdog_timeout, watchdog_callback, "HealthWatchdog")

        # Threads
        self._running = False
        self._check_thread: threading.Thread | None = None
        self._metrics_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Process for metrics
        self._process = psutil.Process(os.getpid())

    def register_check(self, check: HealthCheck) -> None:
        """Register a health check."""
        with self._lock:
            self._checks[check.name] = check

    def unregister_check(self, name: str) -> None:
        """Unregister a health check."""
        with self._lock:
            self._checks.pop(name, None)
            self._health.pop(name, None)

    def start(self) -> None:
        """Start monitoring."""
        if self._running:
            return
        self._running = True
        self.watchdog.start()

        self._check_thread = threading.Thread(
            target=self._check_loop, daemon=True, name="HealthChecks"
        )
        self._check_thread.start()

        self._metrics_thread = threading.Thread(
            target=self._metrics_loop, daemon=True, name="HealthMetrics"
        )
        self._metrics_thread.start()

        self.logger.info("Health monitor started")

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        self.watchdog.stop()

        if self._check_thread:
            self._check_thread.join(timeout=5.0)
        if self._metrics_thread:
            self._metrics_thread.join(timeout=5.0)

        self.logger.info("Health monitor stopped")

    def heartbeat(self) -> None:
        """Record a heartbeat for the watchdog."""
        self.watchdog.heartbeat()

    def run_checks(self) -> dict[str, ComponentHealth]:
        """Run all health checks immediately."""
        results = {}
        with self._lock:
            checks = list(self._checks.values())

        for check in checks:
            if check.should_run():
                result = check.run()
                with self._lock:
                    self._health[check.name] = result
                results[check.name] = result

        return results

    def get_health(self, name: str | None = None) -> dict[str, ComponentHealth]:
        """Get current health status."""
        with self._lock:
            if name:
                return {name: self._health[name]} if name in self._health else {}
            return dict(self._health)

    def get_overall_status(self) -> HealthStatus:
        """Get overall system health."""
        with self._lock:
            if not self._health:
                return HealthStatus.UNKNOWN

            statuses = [h.status for h in self._health.values()]
            if HealthStatus.UNHEALTHY in statuses:
                return HealthStatus.UNHEALTHY
            if HealthStatus.DEGRADED in statuses:
                return HealthStatus.DEGRADED
            if all(s == HealthStatus.HEALTHY for s in statuses):
                return HealthStatus.HEALTHY
            return HealthStatus.UNKNOWN

    def get_metrics(self, limit: int = 100) -> list[SystemMetrics]:
        """Get recent system metrics."""
        with self._lock:
            return self._metrics_history[-limit:]

    def get_latest_metrics(self) -> SystemMetrics | None:
        """Get the most recent metrics."""
        with self._lock:
            return self._metrics_history[-1] if self._metrics_history else None

    def _check_loop(self) -> None:
        """Background loop for health checks."""
        while self._running:
            try:
                self.run_checks()
            except Exception as e:
                self.logger.error(f"Health check error: {e}")
            time.sleep(self.check_interval)

    def _metrics_loop(self) -> None:
        """Background loop for metrics collection."""
        while self._running:
            try:
                self._collect_metrics()
            except Exception as e:
                self.logger.error(f"Metrics collection error: {e}")
            time.sleep(self.metrics_interval)

    def _collect_metrics(self) -> None:
        """Collect system metrics."""
        try:
            with self._process.oneshot():
                cpu = self._process.cpu_percent()
                mem = self._process.memory_info()
                threads = self._process.num_threads()
                try:
                    handles = self._process.num_handles()
                except (AttributeError, psutil.AccessDenied):
                    handles = 0

            # Disk usage for app directory
            try:
                app_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Qwasda"
                if app_dir.exists():
                    usage = psutil.disk_usage(str(app_dir))
                    disk_mb = usage.used / (1024 * 1024)
                else:
                    disk_mb = 0
            except Exception:
                disk_mb = 0

            metrics = SystemMetrics(
                timestamp=time.time(),
                cpu_percent=cpu,
                memory_mb=mem.rss / (1024 * 1024),
                memory_percent=self._process.memory_percent(),
                thread_count=threads,
                handle_count=handles,
                disk_usage_mb=disk_mb,
            )

            with self._lock:
                self._metrics_history.append(metrics)
                if len(self._metrics_history) > self._max_metrics_history:
                    self._metrics_history = self._metrics_history[-self._max_metrics_history :]

        except Exception as e:
            self.logger.debug(f"Failed to collect metrics: {e}")


# Built-in health checks
class DictionaryHealthCheck(HealthCheck):
    """Check dictionary loading status."""

    def __init__(self, dict_loader: Any, interval: float = 60.0):
        super().__init__("dictionaries", interval)
        self.dict_loader = dict_loader

    def check(self) -> ComponentHealth:
        if not self.dict_loader.dicts_loaded:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,
                last_check=time.time(),
                message="Dictionaries not loaded yet",
            )

        en_count = len(self.dict_loader.dict_en) if self.dict_loader.dict_en else 0
        uk_count = len(self.dict_loader.dict_uk) if self.dict_loader.dict_uk else 0

        if en_count == 0 or uk_count == 0:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                last_check=time.time(),
                message=f"Empty dictionaries: EN={en_count}, UK={uk_count}",
            )

        return ComponentHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            last_check=time.time(),
            message=f"Dictionaries loaded: EN={en_count}, UK={uk_count}",
            metadata={"en_words": en_count, "uk_words": uk_count},
        )


class HookHealthCheck(HealthCheck):
    """Check keyboard/mouse hooks are active."""

    def __init__(self, kb_hook: Any, mouse_hook: Any, interval: float = 10.0):
        super().__init__("hooks", interval)
        self.kb_hook = kb_hook
        self.mouse_hook = mouse_hook

    def check(self) -> ComponentHealth:
        # Handle case where hooks aren't fully initialized yet
        kb_ok = False
        mouse_ok = False

        if self.kb_hook is not None:
            try:
                kb_ok = self.kb_hook.is_installed()
            except AttributeError:
                kb_ok = False

        if self.mouse_hook is not None:
            try:
                mouse_ok = self.mouse_hook.is_installed()
            except AttributeError:
                mouse_ok = False

        if kb_ok and mouse_ok:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.HEALTHY,
                last_check=time.time(),
                message="Keyboard and mouse hooks active",
            )
        elif kb_ok or mouse_ok:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,
                last_check=time.time(),
                message=f"Partial hooks: KB={kb_ok}, Mouse={mouse_ok}",
            )
        else:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,  # Not unhealthy yet - might be initializing
                last_check=time.time(),
                message="Hooks not installed yet",
            )


class TrayHealthCheck(HealthCheck):
    """Check system tray icon is running."""

    def __init__(self, tray_icon: Any, interval: float = 30.0):
        super().__init__("tray", interval)
        self.tray_icon = tray_icon

    def check(self) -> ComponentHealth:
        if self.tray_icon is not None:
            try:
                if self.tray_icon.is_running():
                    return ComponentHealth(
                        name=self.name,
                        status=HealthStatus.HEALTHY,
                        last_check=time.time(),
                        message="Tray icon running",
                    )
            except AttributeError:
                pass
        return ComponentHealth(
            name=self.name,
            status=HealthStatus.DEGRADED,  # Not unhealthy yet - might be initializing
            last_check=time.time(),
            message="Tray icon not running yet",
        )


class WorkerHealthCheck(HealthCheck):
    """Check correction worker is responsive."""

    def __init__(self, worker: Any, interval: float = 10.0):
        super().__init__("worker", interval)
        self.worker = worker

    def check(self) -> ComponentHealth:
        if not self.worker:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                last_check=time.time(),
                message="Worker not initialized",
            )

        # Check if worker thread is alive
        if (
            hasattr(self.worker, "_thread")
            and self.worker._thread
            and self.worker._thread.is_alive()
        ):
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.HEALTHY,
                last_check=time.time(),
                message="Worker thread alive",
            )

        return ComponentHealth(
            name=self.name,
            status=HealthStatus.UNHEALTHY,
            last_check=time.time(),
            message="Worker thread dead",
        )


# Global instance
_health_monitor: HealthMonitor | None = None


def get_health_monitor() -> HealthMonitor | None:
    """Get global health monitor instance."""
    return _health_monitor


def initialize_health_monitoring(
    check_interval: float = 30.0,
    metrics_interval: float = 60.0,
    watchdog_timeout: float = 30.0,
    watchdog_callback: Callable[[], None] | None = None,
) -> HealthMonitor:
    """Initialize global health monitoring."""
    global _health_monitor
    _health_monitor = HealthMonitor(
        check_interval=check_interval,
        metrics_interval=metrics_interval,
        watchdog_timeout=watchdog_timeout,
        watchdog_callback=watchdog_callback,
    )
    return _health_monitor


def shutdown_health_monitoring() -> None:
    """Shutdown global health monitoring."""
    global _health_monitor
    if _health_monitor:
        _health_monitor.stop()
        _health_monitor = None
