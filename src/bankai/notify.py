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
