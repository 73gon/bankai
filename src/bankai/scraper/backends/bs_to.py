"""bs.to (Burning Series) scraper â€” series-only.

Same shape as the other backends; HTML parsing is stubbed pending real
fixture HTML. The site exposes per-season episode lists at predictable
URLs (``/serie/<slug>/<season>``) which is friendly for batch processing.
"""

from __future__ import annotations

from typing import ClassVar

from bankai.queue.models import MediaKind
from bankai.scraper.base import EpisodeRef, ScraperError, SearchResult, StreamHandle
from bankai.scraper.http import make_client
from bankai.scraper.registry import register

_BASE = "https://burningseries.co"


@register
class BsToBackend:
    site_id: ClassVar[str] = "bs.to"
    display_name: ClassVar[str] = "Burning Series"
    supports_movies: ClassVar[bool] = False
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
        raise ScraperError("bs.to backend not yet implemented â€” capture fixture HTML first")

    async def list_episodes(self, series_url: str) -> list[EpisodeRef]:
        raise ScraperError("bs.to backend not yet implemented")

    async def resolve_stream(self, url: str) -> StreamHandle:
        return StreamHandle(site=self.site_id, url=url, hint="playwright")
