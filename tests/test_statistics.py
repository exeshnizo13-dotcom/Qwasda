from __future__ import annotations

import json

from qwasda.statistics import StatisticsManager, UsageStatsSnapshot


def test_statistics_are_opt_in_and_do_not_create_file(tmp_path):
    manager = StatisticsManager(tmp_path)
    manager.record_layout_switch()
    manager.record_autocorrection()
    manager.flush(force=True)
    assert manager.snapshot.today_layout_switches == 0
    assert not (tmp_path / "statistics.json").exists()


def test_statistics_round_trip_and_disable_preserves_history(tmp_path):
    manager = StatisticsManager(tmp_path, enabled=True)
    manager.record_layout_switch()
    manager.record_autocorrection(3)
    manager.record_manual_conversion()
    manager.flush(force=True)

    loaded = StatisticsManager(tmp_path, enabled=True)
    loaded.load()
    assert loaded.snapshot.today_layout_switches == 1
    assert loaded.snapshot.today_autocorrections == 3
    assert loaded.snapshot.lifetime_manual_conversions == 1

    loaded.set_enabled(False)
    loaded.record_manual_conversion()
    assert loaded.snapshot.lifetime_manual_conversions == 1


def test_statistics_day_rollover_keeps_lifetime(tmp_path):
    manager = StatisticsManager(tmp_path, enabled=True)
    manager._snapshot = UsageStatsSnapshot(
        day="2000-01-01",
        today_layout_switches=9,
        lifetime_layout_switches=12,
    )
    snapshot = manager.snapshot
    assert snapshot.today_layout_switches == 0
    assert snapshot.lifetime_layout_switches == 12


def test_statistics_clear_writes_aggregate_schema_only(tmp_path):
    manager = StatisticsManager(tmp_path, enabled=True)
    manager.record_layout_switch()
    manager.clear()
    payload = json.loads((tmp_path / "statistics.json").read_text(encoding="utf-8"))
    assert set(payload) == {"version", "day", "today", "lifetime"}
    assert payload["today"]["layout_switches"] == 0
    assert payload["lifetime"]["layout_switches"] == 0


def test_corrupted_statistics_are_isolated(tmp_path):
    path = tmp_path / "statistics.json"
    path.write_text("{not json", encoding="utf-8")
    manager = StatisticsManager(tmp_path, enabled=True)
    manager.load()
    assert manager.snapshot.lifetime_layout_switches == 0
