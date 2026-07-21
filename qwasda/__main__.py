"""
Qwasda entry point.

Usage: python -m qwasda
"""

from .config import Config
from .engine import QwasdaEngine


def main() -> int:
    config = Config()
    engine = QwasdaEngine(config)
    return engine.run()


if __name__ == "__main__":
    import sys

    sys.exit(main())
