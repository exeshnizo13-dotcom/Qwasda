"""PyInstaller-friendly launcher for Qwasda."""

from __future__ import annotations

import os
import multiprocessing
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _startup_log_path() -> Path:
    base_dir = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    log_dir = base_dir / "Qwasda" / "Logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "startup.log"


def _write_startup_log(message: str) -> None:
    try:
        with _startup_log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now().isoformat()}] {message}\n")
    except OSError:
        pass


def _run() -> int:
    _write_startup_log("Launcher started")
    from qwasda.__main__ import main
    _write_startup_log("Imported qwasda.__main__.main")

    result = main()
    _write_startup_log(f"main() returned {result}")
    return result


if __name__ == "__main__":
    try:
        multiprocessing.freeze_support()
        sys.exit(_run())
    except Exception:
        _write_startup_log(traceback.format_exc())
        raise
