"""Discord webhook notifications.

Posts a small embed at the end of a pipeline run. Disabled when
``notifications.webhook_url`` is empty.
"""

from __future__ import annotations

from typing import Any

import httpx

from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)


async def notify_success(*, query: str, final_path: str, size_bytes: int | None = None) -> None:
    cfg = get_settings().notifications
    if not cfg.webhook_url or not cfg.on_success:
        return
    size = f"  ({size_bytes / 1024 / 1024 / 1024:.2f} GB)" if size_bytes else ""
    await _post(
        title="\u2705 bankai \u2014 done",
        description=f"**{query}**\n`{final_path}`{size}",
        color=0x57F287,
    )


async def notify_failure(*, query: str, error: str) -> None:
    cfg = get_settings().notifications
    if not cfg.webhook_url or not cfg.on_failure:
        return
    await _post(
        title="\u274c bankai \u2014 failed",
        description=f"**{query}**\n```\n{error[:1500]}\n```",
        color=0xED4245,
    )


async def notify_skipped(*, query: str, final_path: str) -> None:
    """Inform the user that we skipped a target because it already exists."""
    cfg = get_settings().notifications
    if not cfg.webhook_url or not cfg.on_success:
        return
    await _post(
        title="\u23ed\ufe0f bankai \u2014 skipped (already present)",
        description=f"**{query}**\n`{final_path}`",
        color=0x5865F2,
    )


async def notify_transfer_summary(*, summary: str, ok: bool) -> None:
    cfg = get_settings().notifications
    if not cfg.webhook_url:
        return
    if ok and not cfg.on_success:
        return
    if not ok and not cfg.on_failure:
        return
    await _post(
        title="\u2705 bankai transfer \u2014 done"
        if ok
        else "\u274c bankai transfer \u2014 issues",
        description=summary[:3500],
        color=0x57F287 if ok else 0xED4245,
    )


async def _post(*, title: str, description: str, color: int) -> None:
    cfg = get_settings().notifications
    url = str(cfg.webhook_url)
    payload: dict[str, Any] = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        log.warning("[notify] webhook post failed: %s", exc)
