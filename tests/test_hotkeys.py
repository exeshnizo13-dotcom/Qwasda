"""Tests for configurable hotkey validation and registration."""

from __future__ import annotations

from threading import Event
from unittest.mock import MagicMock

import pytest

from qwasda.config import Config, ConfigManager
from qwasda.engine import QwasdaEngine
from qwasda.hotkeys import (
    MOD_CONTROL,
    HotkeyAction,
    HotkeyBinding,
    HotkeyError,
    HotkeyManager,
    binding_to_text,
    default_hotkeys,
    parse_hotkeys,
    serialize_hotkeys,
    validate_hotkeys,
)
from qwasda.win32 import VK_MENU, VK_SHIFT


def test_defaults_and_config_round_trip(tmp_path) -> None:
    config = Config(app_dir=str(tmp_path))
    config.hotkeys[HotkeyAction.TOGGLE_ENABLED] = HotkeyBinding("chord", 0x45, MOD_CONTROL)
    manager = ConfigManager(str(tmp_path))
    manager._config = config
    manager.save_config()

    restored = ConfigManager(str(tmp_path))
    restored.load()

    assert restored.config.hotkeys == config.hotkeys
    assert parse_hotkeys({}) == default_hotkeys()


def test_malformed_hotkeys_migrate_to_defaults() -> None:
    value = {
        "manual_conversion": {"kind": "chord", "key": 0x41, "modifiers": 0},
        "toggle_enabled": {"kind": "double_tap", "key": VK_SHIFT},
        "toggle_autocorrect": None,
    }

    assert parse_hotkeys(value) == default_hotkeys()


def test_validation_rejects_duplicate_and_invalid_bindings() -> None:
    bindings = default_hotkeys()
    bindings[HotkeyAction.TOGGLE_ENABLED] = HotkeyBinding("chord", 0x41, MOD_CONTROL)
    bindings[HotkeyAction.TOGGLE_AUTOCORRECT] = HotkeyBinding("chord", 0x41, MOD_CONTROL)
    with pytest.raises(HotkeyError, match="кільком"):
        validate_hotkeys(bindings)

    bindings[HotkeyAction.TOGGLE_AUTOCORRECT] = HotkeyBinding("double_tap", VK_MENU)
    with pytest.raises(HotkeyError, match="лише"):
        validate_hotkeys(bindings)


def test_transactional_registration_rolls_back_previous_bindings() -> None:
    registered: dict[int, tuple[int, int]] = {}

    def register(hotkey_id: int, modifiers: int, key: int) -> bool:
        if key == 0x42:
            return False
        registered[hotkey_id] = (modifiers, key)
        return True

    def unregister(hotkey_id: int) -> bool:
        registered.pop(hotkey_id, None)
        return True

    manager = HotkeyManager(register, unregister)
    previous = default_hotkeys()
    previous[HotkeyAction.TOGGLE_ENABLED] = HotkeyBinding("chord", 0x41, MOD_CONTROL)
    manager.apply(previous)

    candidate = previous.copy()
    candidate[HotkeyAction.TOGGLE_AUTOCORRECT] = HotkeyBinding("chord", 0x42, MOD_CONTROL)
    with pytest.raises(HotkeyError, match="зайнята"):
        manager.apply(candidate)

    assert manager.bindings == previous
    assert len(registered) == 1


def test_double_tap_key_mapping_and_display() -> None:
    manager = HotkeyManager(lambda *_: True, lambda *_: True)
    bindings = default_hotkeys()
    bindings[HotkeyAction.MANUAL_CONVERSION] = HotkeyBinding("double_tap", VK_SHIFT)
    manager.apply(bindings)

    assert manager.double_tap_vks() == frozenset({VK_SHIFT, 0xA0, 0xA1})
    assert binding_to_text(bindings[HotkeyAction.MANUAL_CONVERSION]) == "Подвійний Shift"
    assert serialize_hotkeys(bindings)["toggle_enabled"] is None


def test_engine_dispatches_registered_manual_hotkey() -> None:
    manager = HotkeyManager(lambda *_: True, lambda *_: True)
    bindings = default_hotkeys()
    bindings[HotkeyAction.MANUAL_CONVERSION] = HotkeyBinding("chord", 0x4D, MOD_CONTROL)
    manager.apply(bindings)

    app = QwasdaEngine.__new__(QwasdaEngine)
    app._running = True
    app._enabled = Event()
    app._enabled.set()
    app.worker = MagicMock()
    app.worker._correcting = False
    app.phrase_buffer = MagicMock()
    app.phrase_buffer.copy.return_value = [("w", [(30, False)])]
    app.hotkeys = manager

    app._handle_hotkey(0x5101)

    task = app.worker.enqueue.call_args.args[0]
    assert task.is_manual is True
    assert task.phrase == [("w", [(30, False)])]


def test_close_unregisters_all_chords() -> None:
    unregistered: list[int] = []
    manager = HotkeyManager(
        lambda *_: True, lambda hotkey_id: unregistered.append(hotkey_id) or True
    )
    bindings = default_hotkeys()
    bindings[HotkeyAction.TOGGLE_ENABLED] = HotkeyBinding("chord", 0x45, MOD_CONTROL)
    manager.apply(bindings)
    manager.close()

    assert unregistered == [0x5102]
