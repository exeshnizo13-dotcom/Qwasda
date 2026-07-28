"""Configurable global hotkeys with transactional Win32 registration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, cast

from .win32 import (
    VK_CONTROL,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_MENU,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    VK_SHIFT,
    user32,
)

HotkeyKind = Literal["double_tap", "chord"]

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
ALLOWED_MODIFIERS = MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_WIN

MODIFIER_KEYS = frozenset(
    {
        VK_CONTROL,
        VK_SHIFT,
        VK_MENU,
        VK_LCONTROL,
        VK_RCONTROL,
        VK_LSHIFT,
        VK_RSHIFT,
        VK_LMENU,
        VK_RMENU,
        VK_LWIN,
        VK_RWIN,
    }
)
DOUBLE_TAP_KEYS = frozenset({VK_CONTROL, VK_SHIFT, VK_MENU})


class HotkeyAction(StrEnum):
    MANUAL_CONVERSION = "manual_conversion"
    TOGGLE_ENABLED = "toggle_enabled"
    TOGGLE_AUTOCORRECT = "toggle_autocorrect"


class HotkeyError(ValueError):
    """Raised when bindings are invalid or cannot be registered."""


@dataclass(frozen=True)
class HotkeyBinding:
    kind: HotkeyKind
    key: int
    modifiers: int = 0

    def to_dict(self) -> dict[str, int | str]:
        return {"kind": self.kind, "key": self.key, "modifiers": self.modifiers}

    @classmethod
    def from_dict(cls, value: object) -> HotkeyBinding:
        if not isinstance(value, dict):
            raise HotkeyError("Некоректний формат hotkey")
        kind = value.get("kind")
        if kind not in ("double_tap", "chord"):
            raise HotkeyError("Невідомий тип hotkey")
        try:
            key = int(cast(Any, value.get("key")))
            modifiers = int(cast(Any, value.get("modifiers", 0)))
        except (TypeError, ValueError) as exc:
            raise HotkeyError("Некоректний код клавіші") from exc
        return cls(cast(HotkeyKind, kind), key, modifiers)


HotkeyBindings = dict[HotkeyAction, HotkeyBinding | None]


def default_hotkeys() -> HotkeyBindings:
    return {
        HotkeyAction.MANUAL_CONVERSION: HotkeyBinding("double_tap", VK_CONTROL),
        HotkeyAction.TOGGLE_ENABLED: None,
        HotkeyAction.TOGGLE_AUTOCORRECT: None,
    }


def parse_hotkeys(value: object) -> HotkeyBindings:
    """Load bindings while preserving defaults for missing or malformed fields."""
    defaults = default_hotkeys()
    if not isinstance(value, dict):
        return defaults
    parsed = defaults.copy()
    for action in HotkeyAction:
        if action.value not in value:
            continue
        raw = value[action.value]
        if raw is None:
            parsed[action] = None
            continue
        try:
            parsed[action] = HotkeyBinding.from_dict(raw)
        except HotkeyError:
            parsed[action] = defaults[action]
    try:
        validate_hotkeys(parsed)
    except HotkeyError:
        return defaults
    return parsed


def serialize_hotkeys(bindings: Mapping[HotkeyAction, HotkeyBinding | None]) -> dict[str, object]:
    return {
        action.value: binding.to_dict() if binding is not None else None
        for action, binding in bindings.items()
    }


def validate_hotkeys(bindings: Mapping[HotkeyAction, HotkeyBinding | None]) -> None:
    if set(bindings) != set(HotkeyAction):
        raise HotkeyError("Набір hotkeys неповний")
    seen: set[tuple[str, int, int]] = set()
    for action, binding in bindings.items():
        if binding is None:
            continue
        if binding.kind == "double_tap":
            if action != HotkeyAction.MANUAL_CONVERSION:
                raise HotkeyError("Double-tap доступний лише для ручної конвертації")
            if binding.key not in DOUBLE_TAP_KEYS or binding.modifiers:
                raise HotkeyError("Double-tap підтримує лише Ctrl, Shift або Alt")
        else:
            if binding.modifiers <= 0 or binding.modifiers & ~ALLOWED_MODIFIERS:
                raise HotkeyError("Комбінація має містити Ctrl, Alt, Shift або Win")
            if binding.key <= 0 or binding.key in MODIFIER_KEYS:
                raise HotkeyError("Комбінація має містити окрему основну клавішу")
        signature = (binding.kind, binding.key, binding.modifiers)
        if signature in seen:
            raise HotkeyError("Одна комбінація призначена кільком діям")
        seen.add(signature)


ACTION_IDS = {
    HotkeyAction.MANUAL_CONVERSION: 0x5101,
    HotkeyAction.TOGGLE_ENABLED: 0x5102,
    HotkeyAction.TOGGLE_AUTOCORRECT: 0x5103,
}


class HotkeyManager:
    """Register chord bindings and roll back completely when registration fails."""

    def __init__(
        self,
        register: Callable[[int, int, int], bool] | None = None,
        unregister: Callable[[int], bool] | None = None,
    ):
        self._register = register or self._register_win32
        self._unregister = unregister or self._unregister_win32
        self._bindings = default_hotkeys()
        self._registered: set[HotkeyAction] = set()

    @property
    def bindings(self) -> HotkeyBindings:
        return self._bindings.copy()

    def apply(self, bindings: Mapping[HotkeyAction, HotkeyBinding | None]) -> None:
        candidate = dict(bindings)
        validate_hotkeys(candidate)
        previous = self._bindings.copy()
        previous_registered = self._registered.copy()

        for action in previous_registered:
            self._unregister(ACTION_IDS[action])
        self._registered.clear()

        try:
            for action, binding in candidate.items():
                if binding is None or binding.kind != "chord":
                    continue
                if not self._register(
                    ACTION_IDS[action], binding.modifiers | MOD_NOREPEAT, binding.key
                ):
                    raise HotkeyError(f"Комбінація «{binding_to_text(binding)}» уже зайнята")
                self._registered.add(action)
        except Exception as exc:
            for action in tuple(self._registered):
                self._unregister(ACTION_IDS[action])
            self._registered.clear()
            rollback_failed = False
            for action in previous_registered:
                binding = previous[action]
                if binding is None or binding.kind != "chord":
                    continue
                if self._register(
                    ACTION_IDS[action], binding.modifiers | MOD_NOREPEAT, binding.key
                ):
                    self._registered.add(action)
                else:
                    rollback_failed = True
            self._bindings = previous
            if rollback_failed:
                raise HotkeyError("Не вдалося відновити попередні hotkeys") from exc
            if isinstance(exc, HotkeyError):
                raise
            raise HotkeyError(str(exc)) from exc

        self._bindings = candidate

    def action_for_id(self, hotkey_id: int) -> HotkeyAction | None:
        for action, action_id in ACTION_IDS.items():
            if action_id == hotkey_id and action in self._registered:
                return action
        return None

    def double_tap_vks(self) -> frozenset[int]:
        binding = self._bindings[HotkeyAction.MANUAL_CONVERSION]
        if binding is None or binding.kind != "double_tap":
            return frozenset()
        if binding.key == VK_CONTROL:
            return frozenset({VK_CONTROL, VK_LCONTROL, VK_RCONTROL})
        if binding.key == VK_SHIFT:
            return frozenset({VK_SHIFT, VK_LSHIFT, VK_RSHIFT})
        return frozenset({VK_MENU, VK_LMENU, VK_RMENU})

    def close(self) -> None:
        for action in tuple(self._registered):
            self._unregister(ACTION_IDS[action])
        self._registered.clear()

    @staticmethod
    def _register_win32(hotkey_id: int, modifiers: int, key: int) -> bool:
        return bool(user32.RegisterHotKey(None, hotkey_id, modifiers, key))

    @staticmethod
    def _unregister_win32(hotkey_id: int) -> bool:
        return bool(user32.UnregisterHotKey(None, hotkey_id))


def current_modifier_mask() -> int:
    mask = 0
    if user32.GetKeyState(VK_CONTROL) & 0x8000:
        mask |= MOD_CONTROL
    if user32.GetKeyState(VK_MENU) & 0x8000:
        mask |= MOD_ALT
    if user32.GetKeyState(VK_SHIFT) & 0x8000:
        mask |= MOD_SHIFT
    if user32.GetKeyState(VK_LWIN) & 0x8000 or user32.GetKeyState(VK_RWIN) & 0x8000:
        mask |= MOD_WIN
    return mask


def binding_to_text(binding: HotkeyBinding | None) -> str:
    if binding is None:
        return "Не призначено"
    if binding.kind == "double_tap":
        names = {VK_CONTROL: "Ctrl", VK_SHIFT: "Shift", VK_MENU: "Alt"}
        return f"Подвійний {names.get(binding.key, str(binding.key))}"
    parts = []
    for bit, name in (
        (MOD_CONTROL, "Ctrl"),
        (MOD_ALT, "Alt"),
        (MOD_SHIFT, "Shift"),
        (MOD_WIN, "Win"),
    ):
        if binding.modifiers & bit:
            parts.append(name)
    parts.append(_key_name(binding.key))
    return "+".join(parts)


def _key_name(key: int) -> str:
    if 0x30 <= key <= 0x39 or 0x41 <= key <= 0x5A:
        return chr(key)
    if 0x70 <= key <= 0x7B:
        return f"F{key - 0x6F}"
    names = {0x20: "Space", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc"}
    return names.get(key, f"VK_{key:02X}")
