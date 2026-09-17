"""Structured logging setup using Rich."""

from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

from bankai.theme import make_console

_CONFIGURED = False
# Background jobs redirect this stream to a file that bgjobs parses back for
# BANKAI_STAGE / BANKAI_PROGRESS markers. Rich falls back to 80 columns when
# the stream is not a terminal and wraps mid-marker, which no marker regex can
# match -- the queue then shows every running job as "Starting" at 100%.
_MACHINE_READABLE_WIDTH = 400


def _log_console() -> Console:
    console = make_console()
    if console.is_terminal:
        return console
    return make_console(width=_MACHINE_READABLE_WIDTH)


def configure_logging(level: int | str = logging.INFO, **handler_kwargs: Any) -> None:
    """Idempotent logging setup with a single RichHandler at the root."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = RichHandler(
        console=_log_console(),
        rich_tracebacks=False,
        show_time=True,
        show_path=False,
        markup=False,
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
