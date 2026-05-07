"""Shared async HTTP client for scraper backends."""

from __future__ import annotations

from typing import Any

import httpx

from bankai.config import get_settings
from bankai.scraper.base import CloudflareBlocked


def make_client(**overrides: Any) -> httpx.AsyncClient:
    """Construct an :class:`httpx.AsyncClient` with our defaults."""
    settings = get_settings().scraper
    headers = {
        "User-Agent": settings.user_agent,
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    headers.update(overrides.pop("headers", {}))
    kwargs: dict[str, Any] = {
        "timeout": settings.request_timeout_seconds,
        "follow_redirects": True,
        "headers": headers,
        "http2": False,
    }
    kwargs.update(overrides)
    return httpx.AsyncClient(**kwargs)


_CF_MARKERS = (
    "cf-browser-verification",
    "cf_chl_opt",
    "Just a moment...",
    "challenge-platform",
)


def detect_cloudflare(response: httpx.Response) -> None:
    """Raise :class:`CloudflareBlocked` if the response looks like a CF challenge."""
    server = response.headers.get("server", "").lower()
    if response.status_code in (403, 503) and "cloudflare" in server:
        raise CloudflareBlocked(
            f"Cloudflare challenge from {response.url} ({response.status_code})"
        )
    body = (
        response.text[:8000] if response.headers.get("content-type", "").startswith("text") else ""
    )
    if body and any(marker in body for marker in _CF_MARKERS):
        raise CloudflareBlocked(f"Cloudflare interstitial from {response.url}")
