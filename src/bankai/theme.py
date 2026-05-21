"""Centralised Rich theme for the bankai CLI.

All console output should use the semantic style names defined here rather
than hard-coded colour names; that way every command/screen stays
visually consistent and a future palette change is one edit.

Background jobs persist their log output to disk with ANSI escape codes
embedded (see ``cli.bgjobs``); the log viewer re-renders those codes via
``rich.text.Text.from_ansi`` so colours survive the round-trip.
"""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

# Semantic style names. Use these in `console.print("[success]...[/success]")`
# instead of raw colours so the palette stays consistent everywhere.
BANKAI_THEME = Theme(
    {
        # Status / outcomes
        "success": "bold green",
        "error": "bold red",
        "warn": "bold yellow",
        "info": "cyan",
        "muted": "dim",
        "accent": "bold magenta",
        # Banner / branding
        "brand": "bold magenta",
        "brand.dim": "magenta",
        # Job / queue states
        "status.queued": "cyan",
        "status.running": "yellow",
        "status.done": "green",
        "status.failed": "red",
        "status.cancelled": "magenta",
        "status.skipped": "blue",
        # Progress widget
        "progress.bar.filled": "green",
        "progress.bar.empty": "dim",
        "progress.percent": "cyan",
        "progress.pending": "dim",
        # Tables
        "table.header": "bold magenta",
        "table.separator": "dim",
        # Prompts / hints
        "kbd": "bold cyan",
        "hint": "dim italic",
    }
)


def make_console() -> Console:
    """Create a :class:`rich.console.Console` that uses the bankai theme."""
    return Console(theme=BANKAI_THEME)


__all__ = ["BANKAI_THEME", "make_console"]
