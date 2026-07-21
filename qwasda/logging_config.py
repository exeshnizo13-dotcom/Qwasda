"""
Structured logging for Qwasda.

Provides:
- JSON-formatted logs for machine parsing
- Console output with colors
- File rotation
- Context-aware logging
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, ParamSpec, TextIO, TypeVar, cast

P = ParamSpec("P")
R = TypeVar("R")


class LogLevel(Enum):
    """Log levels matching standard logging."""

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "thread": record.thread,
            "thread_name": record.threadName,
            "process": record.process,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        if self.include_extra:
            for key, value in record.__dict__.items():
                if key not in {
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "message",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                }:
                    log_data[key] = value

        return json.dumps(log_data, ensure_ascii=False)


class ColoredConsoleFormatter(logging.Formatter):
    """Console formatter with ANSI colors."""

    COLORS = {
        logging.DEBUG: "\033[36m",  # Cyan
        logging.INFO: "\033[32m",  # Green
        logging.WARNING: "\033[33m",  # Yellow
        logging.ERROR: "\033[31m",  # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def __init__(self, use_colors: bool = True):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%H:%M:%S"
        )
        self.use_colors = use_colors and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        if self.use_colors:
            color = self.COLORS.get(record.levelno, "")
            record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


class QwasdaLogger:
    """
    Centralized logger for Qwasda.

    Features:
    - JSON file logging with rotation
    - Colored console output
    - Context-aware logging (component, operation)
    - Thread-safe
    """

    _instance: QwasdaLogger | None = None
    _lock = threading.Lock()
    _initialized: bool

    def __new__(cls) -> QwasdaLogger:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._loggers: dict[str, logging.Logger] = {}
        self._file_handler: logging.handlers.RotatingFileHandler | None = None
        self._console_handler: logging.StreamHandler[TextIO] | None = None
        self._log_dir: Path | None = None
        self._level = logging.INFO

    def initialize(
        self,
        log_dir: Path | None = None,
        level: int | str = logging.INFO,
        console: bool = True,
        json_file: bool = True,
        max_bytes: int = 10 * 1024 * 1024,  # 10 MB
        backup_count: int = 5,
    ) -> None:
        """
        Initialize the logging system.

        Args:
            log_dir: Directory for log files (default: %LOCALAPPDATA%/Qwasda/Logs)
            level: Log level
            console: Enable console output
            json_file: Enable JSON file logging
            max_bytes: Max size per log file
            backup_count: Number of backup files to keep
        """
        numeric_level = (
            cast(int, getattr(logging, level.upper())) if isinstance(level, str) else level
        )
        self._level = numeric_level

        # Set up log directory
        if log_dir is None:
            log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Qwasda" / "Logs"
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)

        # Clear existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Console handler
        if console:
            self._console_handler = logging.StreamHandler(sys.stdout)
            self._console_handler.setLevel(numeric_level)
            self._console_handler.setFormatter(ColoredConsoleFormatter())
            root_logger.addHandler(self._console_handler)

        # JSON file handler
        if json_file:
            log_file = self._log_dir / "qwasda.json.log"
            self._file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            self._file_handler.setLevel(numeric_level)
            self._file_handler.setFormatter(JsonFormatter())
            root_logger.addHandler(self._file_handler)

        # Also create a human-readable text log
        text_log_file = self._log_dir / "qwasda.log"
        text_handler = logging.handlers.RotatingFileHandler(
            text_log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        text_handler.setLevel(numeric_level)
        text_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root_logger.addHandler(text_handler)

        # Log initialization
        self.get_logger("qwasda.init").info(
            "Logging initialized",
            extra={"log_dir": str(self._log_dir), "level": logging.getLevelName(numeric_level)},
        )

    def get_logger(self, name: str) -> logging.Logger:
        """Get a logger instance for a component."""
        if name not in self._loggers:
            self._loggers[name] = logging.getLogger(name)
        return self._loggers[name]

    def set_level(self, level: int | str) -> None:
        """Change log level for all handlers."""
        numeric_level = (
            cast(int, getattr(logging, level.upper())) if isinstance(level, str) else level
        )
        self._level = numeric_level
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)
        for handler in root_logger.handlers:
            handler.setLevel(numeric_level)

    def add_context(
        self, logger: logging.Logger, **context: object
    ) -> logging.LoggerAdapter[logging.Logger]:
        """Create a logger adapter with extra context."""
        return logging.LoggerAdapter(logger, context)

    def shutdown(self) -> None:
        """Shutdown logging system."""
        logging.shutdown()
        self._initialized = False


# Global logger instance
_logger_instance: QwasdaLogger | None = None


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a component."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = QwasdaLogger()
    return _logger_instance.get_logger(name)


def initialize_logging(
    log_dir: Path | None = None,
    level: int | str = logging.INFO,
    console: bool = True,
    json_file: bool = True,
) -> QwasdaLogger:
    """Initialize the global logging system."""
    global _logger_instance
    _logger_instance = QwasdaLogger()
    _logger_instance.initialize(log_dir, level, console, json_file)
    return _logger_instance


def get_logging_instance() -> QwasdaLogger | None:
    """Get the global logger instance."""
    return _logger_instance


def shutdown_logging() -> None:
    """Shutdown the logging system."""
    global _logger_instance
    if _logger_instance:
        _logger_instance.shutdown()
        _logger_instance = None


# Convenience functions for common patterns
class LogContext:
    """Context manager for adding temporary context to logs."""

    def __init__(self, logger: logging.Logger, **context: object) -> None:
        self.logger = logger
        self.context = context
        self.adapter: logging.LoggerAdapter[logging.Logger] | None = None

    def __enter__(self) -> logging.LoggerAdapter[logging.Logger]:
        self.adapter = logging.LoggerAdapter(self.logger, self.context)
        return self.adapter

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        return None


def log_function_call(
    logger: logging.Logger, level: int = logging.DEBUG
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to log function entry/exit."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            logger.log(level, f"Entering {func.__name__}", extra={"function": func.__name__})
            try:
                result = func(*args, **kwargs)
                logger.log(level, f"Exiting {func.__name__}", extra={"function": func.__name__})
                return result
            except Exception as e:
                logger.exception(
                    f"Exception in {func.__name__}: {e}", extra={"function": func.__name__}
                )
                raise

        return wrapper

    return decorator


@contextmanager
def log_performance(logger: logging.Logger, operation: str) -> Iterator[None]:
    """Context manager to log operation duration."""
    import time

    start = time.perf_counter()
    try:
        yield
    except BaseException as exc:
        duration = time.perf_counter() - start
        logger.error(
            f"{operation} failed after {duration:.3f}s",
            extra={"operation": operation, "duration_ms": duration * 1000, "error": str(exc)},
        )
        raise
    else:
        duration = time.perf_counter() - start
        logger.info(
            f"{operation} completed in {duration:.3f}s",
            extra={"operation": operation, "duration_ms": duration * 1000},
        )
