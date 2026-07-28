"""Opt-in, privacy-safe aggregate usage statistics."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class UsageStatsSnapshot:
    """Counters that intentionally contain no user-entered content."""

    day: str
    today_layout_switches: int = 0
    today_autocorrections: int = 0
    today_manual_conversions: int = 0
    lifetime_layout_switches: int = 0
    lifetime_autocorrections: int = 0
    lifetime_manual_conversions: int = 0


class StatisticsManager:
    """Thread-safe counters with periodic and shutdown atomic persistence."""

    SCHEMA_VERSION = 1
    FLUSH_INTERVAL_SECONDS = 30.0
    _COUNTER_NAMES = (
        "layout_switches",
        "autocorrections",
        "manual_conversions",
    )

    def __init__(self, app_dir: str | os.PathLike[str], enabled: bool = False):
        self.app_dir = Path(app_dir)
        self.path = self.app_dir / "statistics.json"
        self._enabled = bool(enabled)
        self._lock = threading.RLock()
        self._snapshot = UsageStatsSnapshot(day=date.today().isoformat())
        self._dirty = False
        self._last_flush = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger("qwasda.statistics")

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def snapshot(self) -> UsageStatsSnapshot:
        with self._lock:
            self._roll_day_locked()
            return self._snapshot

    def load(self) -> None:
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as handle:
                    data = json.load(handle)
                self._snapshot = self._parse(data)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                if self.path.exists():
                    self._logger.warning("Ignoring corrupted statistics file: %s", exc)
                self._snapshot = UsageStatsSnapshot(day=date.today().isoformat())
                self._dirty = False
            self._roll_day_locked()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._flush_worker, name="qwasda-stats", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread:
            thread.join(timeout=1.0)
        self.flush(force=True)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def record_layout_switch(self) -> None:
        self._record("layout_switches")

    def record_autocorrection(self, count: int = 1) -> None:
        self._record("autocorrections", count)

    def record_manual_conversion(self) -> None:
        self._record("manual_conversions")

    def clear(self) -> None:
        with self._lock:
            day = date.today().isoformat()
            self._snapshot = UsageStatsSnapshot(day=day)
            self._dirty = True
        self.flush(force=True)

    def flush(self, *, force: bool = False) -> None:
        with self._lock:
            self._roll_day_locked()
            if not self._dirty:
                return
            now = time.monotonic()
            if not force and now - self._last_flush < self.FLUSH_INTERVAL_SECONDS:
                return
            payload = self._serialize(self._snapshot)
            self.app_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                os.replace(tmp, self.path)
                self._dirty = False
                self._last_flush = now
            except OSError:
                with suppress(OSError):
                    tmp.unlink()
                if force:
                    self._logger.exception("Failed to flush statistics")

    def _record(self, counter: str, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            if not self._enabled:
                return
            self._roll_day_locked()
            current = self._snapshot
            today_name = f"today_{counter}"
            lifetime_name = f"lifetime_{counter}"
            self._snapshot = UsageStatsSnapshot(
                day=current.day,
                today_layout_switches=current.today_layout_switches
                + (count if today_name == "today_layout_switches" else 0),
                today_autocorrections=current.today_autocorrections
                + (count if today_name == "today_autocorrections" else 0),
                today_manual_conversions=current.today_manual_conversions
                + (count if today_name == "today_manual_conversions" else 0),
                lifetime_layout_switches=current.lifetime_layout_switches
                + (count if lifetime_name == "lifetime_layout_switches" else 0),
                lifetime_autocorrections=current.lifetime_autocorrections
                + (count if lifetime_name == "lifetime_autocorrections" else 0),
                lifetime_manual_conversions=current.lifetime_manual_conversions
                + (count if lifetime_name == "lifetime_manual_conversions" else 0),
            )
            self._dirty = True

    def _flush_worker(self) -> None:
        while not self._stop_event.wait(self.FLUSH_INTERVAL_SECONDS):
            self.flush()

    def _roll_day_locked(self) -> None:
        today = date.today().isoformat()
        if self._snapshot.day != today:
            self._snapshot = UsageStatsSnapshot(
                day=today,
                lifetime_layout_switches=self._snapshot.lifetime_layout_switches,
                lifetime_autocorrections=self._snapshot.lifetime_autocorrections,
                lifetime_manual_conversions=self._snapshot.lifetime_manual_conversions,
            )
            self._dirty = True

    @classmethod
    def _serialize(cls, snapshot: UsageStatsSnapshot) -> dict[str, object]:
        return {
            "version": cls.SCHEMA_VERSION,
            "day": snapshot.day,
            "today": {
                "layout_switches": snapshot.today_layout_switches,
                "autocorrections": snapshot.today_autocorrections,
                "manual_conversions": snapshot.today_manual_conversions,
            },
            "lifetime": {
                "layout_switches": snapshot.lifetime_layout_switches,
                "autocorrections": snapshot.lifetime_autocorrections,
                "manual_conversions": snapshot.lifetime_manual_conversions,
            },
        }

    @classmethod
    def _parse(cls, data: object) -> UsageStatsSnapshot:
        if not isinstance(data, dict) or data.get("version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported statistics schema")
        day = data.get("day")
        today = data.get("today")
        lifetime = data.get("lifetime")
        if (
            not isinstance(day, str)
            or not isinstance(today, dict)
            or not isinstance(lifetime, dict)
        ):
            raise ValueError("invalid statistics structure")

        def counter(section: dict[object, object], name: str) -> int:
            value = section.get(name, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("invalid statistics counter")
            return value

        return UsageStatsSnapshot(
            day=day,
            today_layout_switches=counter(today, "layout_switches"),
            today_autocorrections=counter(today, "autocorrections"),
            today_manual_conversions=counter(today, "manual_conversions"),
            lifetime_layout_switches=counter(lifetime, "layout_switches"),
            lifetime_autocorrections=counter(lifetime, "autocorrections"),
            lifetime_manual_conversions=counter(lifetime, "manual_conversions"),
        )
