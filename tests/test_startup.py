from __future__ import annotations

from pathlib import Path

from qwasda import startup


def test_legacy_batch_detection_is_limited_to_qwasda(tmp_path):
    qwasda_batch = tmp_path / "qwasda.bat"
    qwasda_batch.write_text('start "" "C:\\Qwasda\\Qwasda.exe"', encoding="utf-8")
    unrelated_batch = tmp_path / "other.bat"
    unrelated_batch.write_text('start "" "C:\\Other\\other.exe"', encoding="utf-8")

    assert startup.legacy_references_qwasda(qwasda_batch)
    assert not startup.legacy_references_qwasda(unrelated_batch)


def test_legacy_batch_detection_handles_missing_file(tmp_path):
    assert not startup.legacy_references_qwasda(tmp_path / "missing.bat")


def test_remove_if_matches_does_not_touch_unrelated_run_entry(monkeypatch):
    monkeypatch.setattr(startup, "get_command", lambda: '"C:\\Other\\other.exe"')
    called = False

    def fake_set_enabled(enabled: bool, command: str | None = None) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(startup, "set_enabled", fake_set_enabled)
    startup.remove_if_matches(Path("C:\\Qwasda\\Qwasda.exe"))
    assert not called
