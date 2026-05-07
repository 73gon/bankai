"""Kinox (kinox.to) scraper.

Movies and series. Uses the same selector strategy as filmpalast, but
operates on a different markup. The HTML parsing here is a STUB: it
defines the registry entry and class shape so the rest of the system can
target it, but the actual selectors must be tuned against captured
fixture HTML before this works against the live site.

Capture a search results page and a series detail page into
``tests/fixtures/kinox/`` and then fill in :meth:`_parse_search` and
:meth:`_parse_episodes`.
"""

from __future__ import annotations

from typing import ClassVar

from bankai.queue.models import MediaKind
from bankai.scraper.base import EpisodeRef, ScraperError, SearchResult, StreamHandle
from bankai.scraper.http import make_client
from bankai.scraper.registry import register

_BASE = "https://www.kinox.to"


@register
class KinoxBackend:
    site_id: ClassVar[str] = "kinox"
    display_name: ClassVar[str] = "Kinox"
    supports_movies: ClassVar[bool] = True
    supports_series: ClassVar[bool] = True

    def __init__(self, base_url: str = _BASE) -> None:
        self._base = base_url.rstrip("/")
        self._client = make_client(base_url=self._base)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        *,
        kind: MediaKind | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        raise ScraperError("kinox backend not yet implemented â€” capture fixture HTML first")

    async def list_episodes(self, series_url: str) -> list[EpisodeRef]:
        raise ScraperError("kinox backend not yet implemented")

    async def resolve_stream(self, url: str) -> StreamHandle:
        # Most kinox hoster pages need Playwright to click through.
        return StreamHandle(site=self.site_id, url=url, hint="playwright")
