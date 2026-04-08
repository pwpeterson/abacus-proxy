"""Logging configuration utilities with structured and human-readable formats.

This is vendored into src/ so it is importable inside the container as `src.utils.logging_setup`.
"""

from __future__ import annotations

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
    rotation_size: str = "10 MB",
    retention_days: int = 7,
    error_retention_days: int = 14,
    redact_headers: list[str] | None = None,
) -> None:
    """Configure logging with structured JSON and human-readable formats.
    
    Args:
        log_dir: Directory for log files
        log_level: Minimum log level for console output
        enable_rich: Use rich console formatting
        enable_structured: Enable structured JSON logs
        app_name: Application name for log files
        rotation_size: Log file rotation size (e.g., '10 MB')
        retention_days: Days to retain regular logs
        error_retention_days: Days to retain error logs
        redact_headers: List of header names to redact from logs
    """
    # Remove default handler
    logger.remove()

    # Create logs directory
    log_dir.mkdir(exist_ok=True, parents=True)

    # ============================================================================
    # STRUCTURED JSON LOGS (for AI parsing)
    # ============================================================================
    if enable_structured:
        logger.add(
            log_dir / f"{app_name}.jsonl",
            rotation=rotation_size,
            retention=f"{retention_days} days",
            level="DEBUG",
            format="{message}",
            serialize=True,
            backtrace=True,
            diagnose=True,
            enqueue=True,
        )

        logger.add(
            log_dir / f"{app_name}_errors.jsonl",
            rotation=rotation_size,
            retention=f"{error_retention_days} days",
            level="ERROR",
            format="{message}",
            serialize=True,
            backtrace=True,
            diagnose=True,
            enqueue=True,
        )

    # ============================================================================
    # HUMAN-READABLE LOGS (for debugging)
    # ============================================================================
    human_log_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | "
        "{message}"
    )

    logger.add(
        log_dir / f"{app_name}.log",
        rotation=rotation_size,
        retention=f"{retention_days} days",
        level="DEBUG",
        format=human_log_format,
        backtrace=True,
        diagnose=True,
        enqueue=True,
        colorize=False,
    )

    logger.add(
        log_dir / f"{app_name}_errors.log",
        rotation=rotation_size,
        retention=f"{error_retention_days} days",
        level="ERROR",
        format=human_log_format,
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

            console = Console(width=120, force_terminal=True)
            rich_console_format = (
                "<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> | "
                "{message}"
            )
            logger.add(
                RichHandler(
                    console=console,
                    rich_tracebacks=True,
                    markup=True,
                    show_time=False,
                    show_level=True,
                    show_path=True,
                    tracebacks_show_locals=False,
                    omit_repeated_times=False,
                ),
                level=log_level,
                format=rich_console_format,
            )
        except ImportError:
            fallback_console_format = (
                "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
                "<cyan>{name}:{function}:{line}</cyan> | <level>{message}</level>"
            )
            logger.add(
                sys.stderr,
                level=log_level,
                format=fallback_console_format,
                colorize=True,
            )
    else:
        logger.add(
            sys.stderr,
            level=log_level,
            format="{time:HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
            colorize=False,
        )

    logger.info(
        f"Logging initialized: level={log_level}, dir={log_dir}, "
        f"structured={enable_structured}, app={app_name}"
    )


def log_with_context(
    level: str,
    message: str,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Log message with additional context for structured logging."""
    extra = {**(context or {}), **kwargs}
    log_method = getattr(logger, level.lower())

    if extra:
        logger.bind(**extra)
        log_method(message)
    else:
        log_method(message)


def log_debug(message: str, **context: Any) -> None:
    log_with_context("debug", message, **context)


def log_info(message: str, **context: Any) -> None:
    log_with_context("info", message, **context)


def log_warning(message: str, **context: Any) -> None:
    log_with_context("warning", message, **context)


def log_error(message: str, **context: Any) -> None:
    log_with_context("error", message, **context)


def log_critical(message: str, **context: Any) -> None:
    log_with_context("critical", message, **context)


def log_success(message: str, **context: Any) -> None:
    log_with_context("success", message, **context)
