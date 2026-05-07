"""Structured logging setup using Rich."""

from __future__ import annotations

import logging
from typing import Any

from rich.logging import RichHandler

_CONFIGURED = False


def configure_logging(level: int | str = logging.INFO, **handler_kwargs: Any) -> None:
    """Idempotent logging setup with a single RichHandler at the root."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        markup=True,
        **handler_kwargs,
    )
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
    )
    # Quiet noisy third-party loggers.
    for name in ("httpx", "httpcore", "asyncio", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
