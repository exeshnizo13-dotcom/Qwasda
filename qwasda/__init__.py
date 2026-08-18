"""
Qwasda — автоматичне перемикання розкладки клавіатури (EN ↔ UK).

Автор: exeshnizo13
Ліцензія: MIT
"""

import json
import os
from contextlib import suppress

from .version import __version__

__author__ = "exeshnizo13"
__license__ = "MIT"
__description__ = "Універсальний перемикач розкладки для Windows з автокорекцією"

# Public API
from .config import Config, ConfigManager
from .conversion import (
    EN_SINGLE_WORDS,
    ENG_AT_POS,
    SCAN_ENG,
    SCAN_UKR,
    UK_SINGLE_WORDS,
    ScanBuffer,
    autocorrect_target,
    convert_phrase,
    is_word_terminator,
    is_word_text,
    manual_target,
    scans_to_eng,
    scans_to_ukr,
)
from .custom_dicts import (
    CustomDictionaryError,
    CustomDictionaryManager,
    CustomDictionaryRecord,
    CustomDictionarySnapshot,
    DictionaryLanguage,
)
from .dicts import DICT_EN, DICT_UK, DictionaryLoader, SortedWordIndex, dicts_loaded
from .engine import QwasdaEngine
from .hooks import (
    CaretGuard,
    CorrectionWorker,
    DoubleTapDetector,
    KeyboardHook,
    MouseHook,
    PhraseBuffer,
)
from .hotkeys import (
    HotkeyAction,
    HotkeyBinding,
    HotkeyError,
    HotkeyManager,
    binding_to_text,
    default_hotkeys,
)
from .learning import (
    LearningManager,
    _block_en,
    _block_uk,
    _force_en,
    _force_uk,
    block_en,
    block_uk,
    force_en,
    force_uk,
    forget_all,
    learn_block_word,
    learn_valid_word,
)
from .learning import _block_en as BLOCK_EN
from .learning import _block_uk as BLOCK_UK

# Module-level variables for backward compatibility with tests
# These are the canonical sets from learning.py - tests monkeypatch these directly
from .learning import _force_en as FORCE_EN
from .learning import _force_uk as FORCE_UK
from .settings_ui import SettingsWindow
from .single_instance import SingleInstance
from .statistics import StatisticsManager, UsageStatsSnapshot
from .tray import TrayIcon
from .win32 import (
    CTRL_VKS,
    LANG_ENGLISH,
    LANG_UKRAINIAN,
    MODIFIER_VKS,
    NAV_CLEAR_VKS,
    OEM_PUNCT_VKS,
    VK_BACK,
    VK_CAPITAL,
    VK_DELETE,
    VK_DOWN,
    VK_END,
    VK_ESCAPE,
    VK_HOME,
    VK_LEFT,
    VK_NEXT,
    VK_PRIOR,
    VK_RETURN,
    VK_RIGHT,
    VK_SHIFT,
    VK_SPACE,
    VK_TAB,
    VK_UP,
    WORD_BREAK_VKS,
    any_modifier_down,
    get_foreground_layout,
    send_backspaces,
    send_key,
    send_key_shifted,
    send_unicode_string,
    set_foreground_layout,
)

_config_manager: "ConfigManager | None" = None


def _get_config_manager() -> "ConfigManager":
    global _config_manager
    # Always create a new ConfigManager if test paths are set (to avoid cross-test contamination)
    # or if no manager exists yet.
    if _config_manager is None or APP_DIR or CONFIG_PATH or LEARNED_PATH:
        from .config import ConfigManager

        # Use test paths if set, otherwise let ConfigManager use defaults
        app_dir = APP_DIR if APP_DIR else None
        _config_manager = ConfigManager(app_dir=app_dir)
    # Allow tests to override paths via module-level variables
    if APP_DIR and _config_manager.app_dir != APP_DIR:
        _config_manager.app_dir = APP_DIR
        _config_manager.config_path = os.path.join(APP_DIR, "config.json")
        _config_manager.learned_path = os.path.join(APP_DIR, "learned.json")
    if CONFIG_PATH and _config_manager.config_path != CONFIG_PATH:
        _config_manager.config_path = CONFIG_PATH
    if LEARNED_PATH and _config_manager.learned_path != LEARNED_PATH:
        _config_manager.learned_path = LEARNED_PATH
    return _config_manager


def _reset_config_manager() -> None:
    """Reset config manager for testing (internal use)."""
    global _config_manager
    _config_manager = None


# Module-level variables for backward compatibility with tests
# These are simple variables that tests can monkeypatch
enabled = True
auto_correct_enabled = True
learning_enabled = True
MIN_AUTOCORRECT_LEN = 2
MIN_EN_TO_UK = 2
DOUBLE_TAP_WINDOW = 0.4

# App directory paths
APP_DIR = ""
CONFIG_PATH = ""
LEARNED_PATH = ""


def load_config() -> None:
    """Load config from disk (backward compatibility)."""
    cm = _get_config_manager()
    # Check if config file exists before loading
    if os.path.exists(cm.config_path):
        with suppress(OSError, json.JSONDecodeError):
            cm.load()
    # If file doesn't exist, keep current module-level values (don't reset to defaults)

    # Update module-level variables from loaded config
    global enabled, auto_correct_enabled, learning_enabled, MIN_AUTOCORRECT_LEN, MIN_EN_TO_UK, DOUBLE_TAP_WINDOW
    enabled = cm.config.enabled
    auto_correct_enabled = cm.config.auto_correct_enabled
    learning_enabled = cm.config.learning_enabled
    MIN_AUTOCORRECT_LEN = cm.config.min_autocorrect_len
    MIN_EN_TO_UK = cm.config.min_en_to_uk
    DOUBLE_TAP_WINDOW = cm.config.double_tap_window


def save_config() -> None:
    """Save config to disk (backward compatibility)."""
    cm = _get_config_manager()
    cm.config.enabled = enabled
    cm.config.auto_correct_enabled = auto_correct_enabled
    cm.config.learning_enabled = learning_enabled
    cm.config.min_autocorrect_len = MIN_AUTOCORRECT_LEN
    cm.config.min_en_to_uk = MIN_EN_TO_UK
    cm.config.double_tap_window = DOUBLE_TAP_WINDOW
    cm.save_config()


def save_learned() -> None:
    """Save learned words to disk (backward compatibility)."""
    _get_config_manager().save_learned()


def forget_learned() -> None:
    """Clear all learned words (backward compatibility)."""
    _get_config_manager().forget_learned()


def load_learned() -> None:
    """Load learned words from disk (backward compatibility)."""
    _get_config_manager().load_learned()


__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "__description__",
    "Config",
    "LearningManager",
    "DictionaryLoader",
    "CustomDictionaryError",
    "CustomDictionaryManager",
    "CustomDictionaryRecord",
    "CustomDictionarySnapshot",
    "DictionaryLanguage",
    "SortedWordIndex",
    "scans_to_eng",
    "scans_to_ukr",
    "manual_target",
    "autocorrect_target",
    "convert_phrase",
    "is_word_text",
    "is_word_terminator",
    "ScanBuffer",
    "get_foreground_layout",
    "set_foreground_layout",
    "any_modifier_down",
    "send_backspaces",
    "send_key",
    "send_key_shifted",
    "send_unicode_string",
    "KeyboardHook",
    "MouseHook",
    "CorrectionWorker",
    "PhraseBuffer",
    "CaretGuard",
    "DoubleTapDetector",
    "HotkeyAction",
    "HotkeyBinding",
    "HotkeyError",
    "HotkeyManager",
    "binding_to_text",
    "default_hotkeys",
    "TrayIcon",
    "SingleInstance",
    "SettingsWindow",
    "StatisticsManager",
    "UsageStatsSnapshot",
    "QwasdaEngine",
    "learn_valid_word",
    "learn_block_word",
    "load_learned",
    "forget_all",
    "LANG_UKRAINIAN",
    "LANG_ENGLISH",
    "VK_SPACE",
    "VK_RETURN",
    "VK_TAB",
    "VK_BACK",
    "VK_SHIFT",
    "VK_CAPITAL",
    "VK_ESCAPE",
    "VK_LEFT",
    "VK_UP",
    "VK_RIGHT",
    "VK_DOWN",
    "VK_HOME",
    "VK_END",
    "VK_PRIOR",
    "VK_NEXT",
    "VK_DELETE",
    "CTRL_VKS",
    "MODIFIER_VKS",
    "WORD_BREAK_VKS",
    "NAV_CLEAR_VKS",
    "OEM_PUNCT_VKS",
    "SCAN_ENG",
    "SCAN_UKR",
    "ENG_AT_POS",
]
