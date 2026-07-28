"""Qwasda entry point and packaging lifecycle commands."""

import sys

from .config import Config
from .engine import QwasdaEngine
from .single_instance import request_shutdown
from .version import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        sys.stdout.write(f"{__version__}\n")
        return 0
    if args == ["--shutdown"]:
        return 0 if request_shutdown() else 1
    if args == ["--smoke-test"]:
        return 0
    config = Config()
    engine = QwasdaEngine(config)
    return engine.run()


if __name__ == "__main__":
    sys.exit(main())
