"""Logging configuration utilities with structured and human-readable formats."""

import sys
from pathlib import Path
from typing import Any

from loguru import logger


def setup_logging(
    log_dir: Path = Path("logs"),
    log_level: str = "INFO",
    enable_rich: bool = True,
    enable_structured: bool = True,
    app_name: str = "app",
) -> None:
    """Configure logging with structured JSON and human-readable formats.

    This setup provides:
    - Structured JSON logs for AI parsing (*.jsonl files) using loguru's serialize=True
    - Human-readable logs for debugging (*.log files)
    - Rich console output with color coding
    - Separate error logs for quick issue identification
    - Thread-safe, rotating log files

    Args:
        log_dir: Directory for log files
        log_level: Logging level (TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL)
        enable_rich: Enable rich console formatting with colors
        enable_structured: Enable structured JSON logging
        app_name: Application name for log file naming

    Example:
        >>> from pathlib import Path
        >>> from loguru import logger
        >>> setup_logging(
        ...     log_dir=Path("logs"),
        ...     log_level="INFO",
        ...     enable_rich=True,
        ...     enable_structured=True,
        ...     app_name="my_app"
        ... )
        >>> logger.info("Application started")
        >>> logger.bind(user_id=123, action="login").info("User logged in")
    """
    # Remove default handler
    logger.remove()

    # Create logs directory
    log_dir.mkdir(exist_ok=True, parents=True)

    # ============================================================================
    # STRUCTURED JSON LOGS (for AI parsing)
    # ============================================================================
    if enable_structured:
        # All logs in structured JSON format
        # Using loguru's built-in serialize=True for reliable JSON output
        logger.add(
            log_dir / f"{app_name}.jsonl",
            rotation="10 MB",
            retention="7 days",
            level="DEBUG",
            format="{message}",
            serialize=True,  # Built-in JSON serialization
            backtrace=True,
            diagnose=True,
            enqueue=True,  # Thread-safe logging
        )

        # Errors only in structured JSON format
        logger.add(
            log_dir / f"{app_name}_errors.jsonl",
            rotation="5 MB",
            retention="14 days",
            level="ERROR",
            format="{message}",
            serialize=True,  # Built-in JSON serialization
            backtrace=True,
            diagnose=True,
            enqueue=True,
        )

    # ============================================================================
    # HUMAN-READABLE LOGS (for debugging)
    # ============================================================================
    # All logs in human-readable format
    logger.add(
        log_dir / f"{app_name}.log",
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        backtrace=True,
        diagnose=True,
        enqueue=True,
        colorize=False,  # No ANSI codes in file
    )

    # Errors only in human-readable format
    logger.add(
        log_dir / f"{app_name}_errors.log",
        rotation="5 MB",
        retention="14 days",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        backtrace=True,
        diagnose=True,
        enqueue=True,
        colorize=False,
    )

    # ============================================================================
    # CONSOLE OUTPUT (for interactive use)
    # ============================================================================
    if enable_rich:
        try:
            from rich.console import Console
            from rich.logging import RichHandler

            # Compact console for cleaner journalctl output
            console = Console(width=120, force_terminal=True)
            logger.add(
                RichHandler(
                    console=console,
                    rich_tracebacks=True,
                    markup=True,
                    show_time=False,  # journalctl already shows time
                    show_level=True,
                    show_path=True,
                    tracebacks_show_locals=False,  # Less verbose
                    omit_repeated_times=False,
                ),
                level=log_level,
                format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> | {message}",
            )
        except ImportError:
            # Fallback to colored console handler
            logger.add(
                sys.stderr,
                level=log_level,
                format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> | <level>{message}</level>",
                colorize=True,
            )
    else:
        # Simple console handler without colors
        logger.add(
            sys.stderr,
            level=log_level,
            format="{time:HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
            colorize=False,
        )

    logger.info(
        f"Logging initialized: level={log_level}, dir={log_dir}, structured={enable_structured}, app={app_name}"
    )


def log_with_context(
    level: str,
    message: str,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Log message with additional context for structured logging.

    Args:
        level: Log level (debug, info, warning, error, critical, success)
        message: Log message
        context: Additional context dictionary
        **kwargs: Additional key-value pairs to include in context

    Example:
        >>> log_with_context(
        ...     "info",
        ...     "User action completed",
        ...     context={"user_id": 123, "action": "login"},
        ...     duration_ms=45.2
        ... )
    """
    # Merge context and kwargs
    extra = {**(context or {}), **kwargs}

    # Get logger method by level
    log_method = getattr(logger, level.lower())

    # Log with context
    if extra:
        logger.bind(**extra)
        log_method(message)
    else:
        log_method(message)


# Convenience functions for common logging patterns
def log_debug(message: str, **context: Any) -> None:
    """Log debug message with optional context."""
    log_with_context("debug", message, **context)


def log_info(message: str, **context: Any) -> None:
    """Log info message with optional context."""
    log_with_context("info", message, **context)


def log_warning(message: str, **context: Any) -> None:
    """Log warning message with optional context."""
    log_with_context("warning", message, **context)


def log_error(message: str, **context: Any) -> None:
    """Log error message with optional context."""
    log_with_context("error", message, **context)


def log_critical(message: str, **context: Any) -> None:
    """Log critical message with optional context."""
    log_with_context("critical", message, **context)


def log_success(message: str, **context: Any) -> None:
    """Log success message with optional context."""
    log_with_context("success", message, **context)


# Example usage and documentation
if __name__ == "__main__":
    """
    Example usage of the logging setup:

    Basic setup:
        from src.utils.logging_setup import setup_logging
        from loguru import logger

        setup_logging(app_name="my_app")
        logger.info("Application started")

    With context:
        logger.bind(user_id=123, session="abc").info("User logged in")

    With convenience functions:
        from src.utils.logging_setup import log_info, log_error

        log_info("Processing request", request_id="req-123", method="POST")
        log_error("Failed to connect", host="api.example.com", timeout=30)

    Structured logs use loguru's built-in JSON serialization (*.jsonl):
        {
            "text": "User logged in",
            "record": {
                "elapsed": {"repr": "0:00:00.123456", "seconds": 0.123456},
                "exception": null,
                "extra": {"user_id": 123, "session": "abc"},
                "file": {"name": "app.py", "path": "/path/to/app.py"},
                "function": "main",
                "level": {"icon": "ℹ️", "name": "INFO", "no": 20},
                "line": 42,
                "message": "User logged in",
                "module": "app",
                "name": "__main__",
                "process": {"id": 12345, "name": "MainProcess"},
                "thread": {"id": 67890, "name": "MainThread"},
                "time": {"repr": "2026-01-01 18:37:00.123456+00:00", "timestamp": 1735754220.123456}
            }
        }

    To parse JSON logs, access record.extra for your context:
        import json
        with open("logs/my_app.jsonl") as f:
            for line in f:
                log = json.loads(line)
                message = log["record"]["message"]
                context = log["record"]["extra"]
                level = log["record"]["level"]["name"]
                timestamp = log["record"]["time"]["timestamp"]
    """
    setup_logging(app_name="example")

    # Basic logging
    logger.info("Application started")
    logger.debug("Debug information")
    logger.warning("Warning message")
    logger.error("Error occurred")

    # Logging with context
    logger.bind(user_id=123, action="login").info("User logged in")

    # Using convenience functions
    log_info("Processing request", request_id="req-123", method="POST", path="/api/users")
    log_error("Database connection failed", host="localhost", port=5432, retry_count=3)
    log_success("Task completed", task_id="task-456", duration_ms=123.45)
