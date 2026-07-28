"""Per-user Windows startup registration and legacy migration."""

from __future__ import annotations

import os
import sys
from contextlib import suppress
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - only relevant outside Windows
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Qwasda"
LEGACY_STARTUP = Path(os.environ.get("APPDATA", "")) / (
    r"Microsoft\Windows\Start Menu\Programs\Startup\Qwasda.bat"
)


def current_command() -> str:
    """Return the quoted command used by the current source or packaged app."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{Path(__file__).resolve().parents[1] / "qwasda.py"}"'


def get_command() -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return value if isinstance(value, str) else None
    except (FileNotFoundError, OSError):
        return None


def is_enabled() -> bool:
    return get_command() is not None


def set_enabled(enabled: bool, command: str | None = None) -> None:
    if winreg is None:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command or current_command())
        else:
            with suppress(FileNotFoundError):
                winreg.DeleteValue(key, RUN_VALUE)


def legacy_references_qwasda(path: Path = LEGACY_STARTUP) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "qwasda.py" in text or "qwasda.exe" in text


def migrate_legacy_startup() -> bool:
    """Migrate our old startup batch and return whether startup was enabled."""
    if not LEGACY_STARTUP.exists() or not legacy_references_qwasda():
        return is_enabled()
    set_enabled(True)
    with suppress(OSError):
        LEGACY_STARTUP.unlink()
    return True


def remove_if_matches(command_path: str | os.PathLike[str]) -> None:
    """Remove our Run value only when it points at the supplied executable."""
    command = get_command()
    if command is None:
        return
    normalized = str(Path(command_path).resolve()).lower()
    if normalized in command.lower():
        set_enabled(False)
