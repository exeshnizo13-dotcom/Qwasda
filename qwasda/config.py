"""
Configuration management for Qwasda.

Handles loading/saving config.json and learned.json with validation.
"""

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from .hotkeys import HotkeyBindings, default_hotkeys, parse_hotkeys, serialize_hotkeys


def _get_qwasda_learned_sets() -> tuple[set[str], set[str], set[str], set[str]]:
    """Get learned word sets from qwasda module (allows monkeypatching in tests)."""
    qwasda = sys.modules.get("qwasda")
    if qwasda:
        return (
            cast(set[str], qwasda.FORCE_EN),
            cast(set[str], qwasda.FORCE_UK),
            cast(set[str], qwasda.BLOCK_UK),
            cast(set[str], qwasda.BLOCK_EN),
        )
    # Fallback to module-level sets from learning
    from .learning import _block_en, _block_uk, _force_en, _force_uk

    return _force_en, _force_uk, _block_uk, _block_en


@dataclass
class Config:
    """Runtime configuration with validation."""

    enabled: bool = True
    auto_correct_enabled: bool = True
    learning_enabled: bool = True
    min_autocorrect_len: int = 2
    min_en_to_uk: int = 3
    double_tap_window: float = 0.4
    hotkeys: HotkeyBindings = field(default_factory=default_hotkeys)
    statistics_enabled: bool = False
    app_dir: str = ""

    def __post_init__(self) -> None:
        # Validate numeric fields
        self.min_autocorrect_len = max(1, int(self.min_autocorrect_len))
        self.min_en_to_uk = max(1, int(self.min_en_to_uk))
        self.double_tap_window = max(0.1, min(2.0, float(self.double_tap_window)))
        # Set default app_dir if not provided
        if not self.app_dir:
            self.app_dir = os.path.join(
                os.environ.get("APPDATA", os.path.expanduser("~")), "Qwasda"
            )


class ConfigManager:
    """Manages persistent configuration in %APPDATA%\\Qwasda\\config.json."""

    CONFIG_FILENAME = "config.json"
    LEARNED_FILENAME = "learned.json"

    def __init__(self, app_dir: str | None = None):
        if app_dir is None:
            app_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Qwasda")
        self.app_dir = app_dir
        self.config_path = os.path.join(app_dir, self.CONFIG_FILENAME)
        self.learned_path = os.path.join(app_dir, self.LEARNED_FILENAME)
        self._config = Config(app_dir=app_dir)

    @property
    def _learned(self) -> dict[str, set[str]]:
        """Get learned word sets from qwasda module (allows test monkeypatching)."""
        force_en, force_uk, block_uk, block_en = _get_qwasda_learned_sets()
        return {
            "force_en": force_en,
            "force_uk": force_uk,
            "block_uk": block_uk,
            "block_en": block_en,
        }

    @property
    def config(self) -> Config:
        return self._config

    @property
    def learned(self) -> dict[str, set[str]]:
        return self._learned

    def load(self) -> None:
        """Load config and learned words from disk."""
        self._load_config()
        self._load_learned()

    def _load_config(self) -> None:
        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Only update known fields with valid types
                if "enabled" in data and isinstance(data["enabled"], bool):
                    self._config.enabled = data["enabled"]
                if "auto_correct_enabled" in data and isinstance(
                    data["auto_correct_enabled"], bool
                ):
                    self._config.auto_correct_enabled = data["auto_correct_enabled"]
                if "learning_enabled" in data and isinstance(data["learning_enabled"], bool):
                    self._config.learning_enabled = data["learning_enabled"]
                if (
                    "min_autocorrect_len" in data
                    and isinstance(data["min_autocorrect_len"], int)
                    and data["min_autocorrect_len"] > 0
                ):
                    self._config.min_autocorrect_len = data["min_autocorrect_len"]
                if (
                    "min_en_to_uk" in data
                    and isinstance(data["min_en_to_uk"], int)
                    and data["min_en_to_uk"] > 0
                ):
                    self._config.min_en_to_uk = data["min_en_to_uk"]
                if (
                    "double_tap_window" in data
                    and isinstance(data["double_tap_window"], (int, float))
                    and data["double_tap_window"] > 0
                ):
                    self._config.double_tap_window = float(data["double_tap_window"])
                self._config.hotkeys = parse_hotkeys(data.get("hotkeys"))
                if "statistics_enabled" in data and isinstance(data["statistics_enabled"], bool):
                    self._config.statistics_enabled = data["statistics_enabled"]
        except (OSError, json.JSONDecodeError):
            pass  # Keep defaults

    def _load_learned(self) -> None:
        try:
            with open(self.learned_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Get the actual sets from qwasda module
                force_en, force_uk, block_uk, block_en = _get_qwasda_learned_sets()
                for key, target_set in [
                    ("force_en", force_en),
                    ("force_uk", force_uk),
                    ("block_uk", block_uk),
                    ("block_en", block_en),
                ]:
                    vals = data.get(key)
                    if isinstance(vals, list):
                        target_set.clear()
                        target_set.update(str(v).lower() for v in vals if isinstance(v, str))
        except (OSError, json.JSONDecodeError):
            pass

    def save_config(self) -> None:
        """Atomically save config to disk."""
        try:
            os.makedirs(self.app_dir, exist_ok=True)
            tmp = self.config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                data = asdict(self._config)
                data["hotkeys"] = serialize_hotkeys(self._config.hotkeys)
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.config_path)
        except OSError:
            pass  # Non-fatal

    def save_learned(self) -> None:
        """Atomically save learned words to disk."""
        try:
            os.makedirs(self.app_dir, exist_ok=True)
            tmp = self.learned_path + ".tmp"
            data = {k: sorted(v) for k, v in self._learned.items()}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.learned_path)
        except OSError:
            pass

    def forget_learned(self) -> None:
        """Clear all learned words."""
        for s in self._learned.values():
            s.clear()
        self.save_learned()

    def load_learned(self) -> None:
        """Load learned words from disk (public method for backward compatibility)."""
        self._load_learned()

    def update_config(self, **kwargs: Any) -> bool:
        """Update config fields and save. Returns True if changed."""
        changed = False
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                current = getattr(self._config, key)
                if current != value:
                    setattr(self._config, key, value)
                    changed = True
        if changed:
            self.save_config()
        return changed
