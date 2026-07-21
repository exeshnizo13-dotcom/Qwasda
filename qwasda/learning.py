"""
Learning manager for Qwasda - persists user corrections and exceptions.

Stores:
- FORCE_EN: Ukrainian-typed words that should auto-switch to English
- FORCE_UK: English-typed words that should auto-switch to Ukrainian
- BLOCK_UK: Ukrainian words that auto-correct should NOT touch
- BLOCK_EN: English words that auto-correct should NOT touch
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .config import Config


def _get_qwasda_sets() -> tuple[set[str], set[str], set[str], set[str]]:
    """Get the learned word sets from qwasda module (allows monkeypatching in tests)."""
    qwasda = sys.modules.get("qwasda")
    if qwasda:
        return (
            cast(set[str], qwasda.FORCE_EN),
            cast(set[str], qwasda.FORCE_UK),
            cast(set[str], qwasda.BLOCK_UK),
            cast(set[str], qwasda.BLOCK_EN),
        )
    # Fallback to module-level sets (for non-test usage)
    return _force_en, _force_uk, _block_uk, _block_en


# Module-level sets (used as fallback when qwasda module not available)
_force_en: set[str] = set()
_force_uk: set[str] = set()
_block_uk: set[str] = set()
_block_en: set[str] = set()


def _get_force_en(_instance: object | None = None) -> set[str]:
    """Get FORCE_EN set (from qwasda module if available, else module-level)."""
    return _get_qwasda_sets()[0]


def _get_force_uk(_instance: object | None = None) -> set[str]:
    """Get FORCE_UK set (from qwasda module if available, else module-level)."""
    return _get_qwasda_sets()[1]


def _get_block_uk(_instance: object | None = None) -> set[str]:
    """Get BLOCK_UK set (from qwasda module if available, else module-level)."""
    return _get_qwasda_sets()[2]


def _get_block_en(_instance: object | None = None) -> set[str]:
    """Get BLOCK_EN set (from qwasda module if available, else module-level)."""
    return _get_qwasda_sets()[3]


# Properties for backward compatibility - these delegate to qwasda module if available
force_en = property(_get_force_en)
force_uk = property(_get_force_uk)
block_uk = property(_get_block_uk)
block_en = property(_get_block_en)


def _get_qwasda_force_en() -> set[str]:
    """Get FORCE_EN from qwasda module (allows test monkeypatching)."""
    return _get_qwasda_sets()[0]


def _get_qwasda_force_uk() -> set[str]:
    """Get FORCE_UK from qwasda module (allows test monkeypatching)."""
    return _get_qwasda_sets()[1]


def _get_qwasda_block_uk() -> set[str]:
    """Get BLOCK_UK from qwasda module (allows test monkeypatching)."""
    return _get_qwasda_sets()[2]


def _get_qwasda_block_en() -> set[str]:
    """Get BLOCK_EN from qwasda module (allows test monkeypatching)."""
    return _get_qwasda_sets()[3]


def learn_valid_word(word: str, target_layout: int) -> bool:
    """
    Learn a word as valid for target layout (FORCE_*).
    Module-level function for backward compatibility.
    Returns True if set changed.
    """
    from .win32 import LANG_ENGLISH

    word = word.lower()
    if target_layout == LANG_ENGLISH:
        fe = _get_qwasda_force_en()
        if word in fe:
            return False
        fe.add(word)
    else:
        fu = _get_qwasda_force_uk()
        if word in fu:
            return False
        fu.add(word)
    _save_learned()
    return True


def learn_block_word(word: str, layout: int) -> bool:
    """
    Learn a word as exception for layout (BLOCK_*).
    Module-level function for backward compatibility.
    Returns True if set changed.
    """
    from .win32 import LANG_UKRAINIAN

    word = word.lower()
    if layout == LANG_UKRAINIAN:
        bu = _get_qwasda_block_uk()
        if word in bu:
            return False
        bu.add(word)
    else:
        be = _get_qwasda_block_en()
        if word in be:
            return False
        be.add(word)
    _save_learned()
    return True


def forget_all() -> None:
    """Clear all learned words."""
    _get_qwasda_force_en().clear()
    _get_qwasda_force_uk().clear()
    _get_qwasda_block_uk().clear()
    _get_qwasda_block_en().clear()
    _save_learned()


def load_learned(config: Config) -> None:
    """Load learned words from disk into module-level sets (public API)."""
    _load_learned(config)


def _learned_path(config: Config) -> Path:
    # Check if qwasda module has APP_DIR set (for tests)
    qwasda = sys.modules.get("qwasda")
    if qwasda and getattr(qwasda, "APP_DIR", None):
        return Path(qwasda.APP_DIR) / "learned.json"
    return Path(config.app_dir) / "learned.json"


def _load_learned(config: Config) -> None:
    """Load learned words from disk into module-level sets."""
    # Use qwasda module's APP_DIR if set (for tests), else use config
    qwasda = sys.modules.get("qwasda")
    if qwasda and getattr(qwasda, "APP_DIR", None):
        path = Path(qwasda.APP_DIR) / "learned.json"
    else:
        path = _learned_path(config)

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    if not isinstance(data, dict):
        return

    fe = _get_qwasda_force_en()
    fe.clear()
    fe.update(str(v).lower() for v in data.get("force_en", []) if isinstance(v, str) and v)
    fu = _get_qwasda_force_uk()
    fu.clear()
    fu.update(str(v).lower() for v in data.get("force_uk", []) if isinstance(v, str) and v)
    bu = _get_qwasda_block_uk()
    bu.clear()
    bu.update(str(v).lower() for v in data.get("block_uk", []) if isinstance(v, str) and v)
    be = _get_qwasda_block_en()
    be.clear()
    be.update(str(v).lower() for v in data.get("block_en", []) if isinstance(v, str) and v)


def _save_learned() -> None:
    """Atomically save module-level learned words to JSON."""
    # Use qwasda module's APP_DIR if set (for tests), else use ConfigManager
    qwasda = sys.modules.get("qwasda")
    if qwasda and getattr(qwasda, "APP_DIR", None):
        path = Path(qwasda.APP_DIR) / "learned.json"
    else:
        from .config import ConfigManager

        cm = ConfigManager()
        path = Path(cm.learned_path)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")

    # Get actual sets from properties
    fe = _get_qwasda_force_en()
    fu = _get_qwasda_force_uk()
    bu = _get_qwasda_block_uk()
    be = _get_qwasda_block_en()

    data = {
        "force_en": sorted(fe),
        "force_uk": sorted(fu),
        "block_uk": sorted(bu),
        "block_en": sorted(be),
    }

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


class LearningManager:
    """
    Manages learned words (FORCE_*/BLOCK_*) with atomic JSON persistence.
    Uses qwasda module sets for backward compatibility with tests.
    """

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        # Load from disk into qwasda module sets (allows test monkeypatching)
        _load_learned(config)

    @property
    def force_en(self) -> set[str]:
        return _get_qwasda_force_en()

    @property
    def force_uk(self) -> set[str]:
        return _get_qwasda_force_uk()

    @property
    def block_uk(self) -> set[str]:
        return _get_qwasda_block_uk()

    @property
    def block_en(self) -> set[str]:
        return _get_qwasda_block_en()

    def stats(self) -> dict[str, int]:
        """Get statistics about learned words."""
        return {
            "force_en": len(self.force_en),
            "force_uk": len(self.force_uk),
            "block_uk": len(self.block_uk),
            "block_en": len(self.block_en),
            "total": len(self.force_en)
            + len(self.force_uk)
            + len(self.block_uk)
            + len(self.block_en),
        }

    def learn_valid_word(self, word: str, target_layout: int) -> bool:
        """
        Learn a word as valid for target layout (FORCE_*).
        Returns True if set changed.
        """
        return learn_valid_word(word, target_layout)

    def learn_block_word(self, word: str, layout: int) -> bool:
        """
        Learn a word as exception for layout (BLOCK_*).
        Returns True if set changed.
        """
        return learn_block_word(word, layout)

    def forget_all(self) -> None:
        """Clear all learned words."""
        forget_all()

    def save(self) -> None:
        """Atomically save learned words to JSON."""
        _save_learned()

    def get_stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "force_en": len(self.force_en),
                "force_uk": len(self.force_uk),
                "block_uk": len(self.block_uk),
                "block_en": len(self.block_en),
            }
