"""bs.to (Burning Series) scraper — series only.

bs.to lists ~10k series at ``/andere-serien`` (one big page that we cache),
each linking to ``/serie/<Slug>``. A season's German episodes live at
``/serie/<Slug>/<season>/de`` inside ``<table class="episodes">``; every row
links to the episode page plus per-hoster variants
(``…/de/VOE``, ``…/de/Doodstream`` …).

The actual hoster URL is revealed only after a click on the JS player
(``<div class="hoster-player" data-lid=…>``), which bs.to gates behind a
security token / account. We therefore hand the hoster *page* to the
Playwright capture stage (``hint="playwright"``) which presses play and
intercepts the resulting stream — the same path that handles voe.
"""

from __future__ import annotations

import re
import time
from typing import ClassVar
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from bankai.logging import get_logger
from bankai.queue.models import MediaKind
from bankai.scraper.base import EpisodeRef, ScraperError, SearchResult, StreamHandle
from bankai.scraper.http import make_client
from bankai.scraper.registry import register

log = get_logger(__name__)

_BASE = "https://bs.to"
# Hosters ranked by how reliably our capture stage can pull a stream.
_HOSTER_PREFERENCE = ("VOE", "Vidoza", "Streamtape", "Doodstream", "Vidmoly", "Filemoon")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@register
class BsToBackend:
    site_id: ClassVar[str] = "bs.to"
    display_name: ClassVar[str] = "Burning Series"
    supports_movies: ClassVar[bool] = False
    supports_series: ClassVar[bool] = True

    # Cache the (large) series index across instances for a few minutes.
    _index_cache: ClassVar[tuple[float, list[tuple[str, str]]] | None] = None
    _INDEX_TTL: ClassVar[float] = 600.0

    def __init__(self, base_url: str = _BASE) -> None:
        self._base = base_url.rstrip("/")
        self._client = make_client(base_url=self._base)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- series index / search --------------------------------------------

    async def _series_index(self) -> list[tuple[str, str]]:
        """Return ``[(display_name, slug), …]`` for every bs.to series."""
        cached = type(self)._index_cache
        if cached is not None and time.time() - cached[0] < self._INDEX_TTL:
            return cached[1]
        resp = await self._client.get("/andere-serien")
        if resp.status_code != 200:
            raise ScraperError(f"bs.to index failed: HTTP {resp.status_code}")
        tree = HTMLParser(resp.text)
        pairs: list[tuple[str, str]] = []
        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            m = re.match(r"^serie/([^/]+)$", href)
            if m:
                name = (a.text() or "").strip()
                if name:
                    pairs.append((name, m.group(1)))
        type(self)._index_cache = (time.time(), pairs)
        return pairs

    async def search(
        self, query: str, *, kind: MediaKind | None = None, limit: int = 20
    ) -> list[SearchResult]:
        if kind == MediaKind.MOVIE:
            return []  # bs.to is series-only
        index = await self._series_index()
        q = _normalize(query)
        q_tokens = set(q.split())
        scored: list[tuple[int, str, str]] = []
        for name, slug in index:
            norm = _normalize(name)
            if q == norm:
                score = 1000
            elif norm.startswith(q):
                score = 500
            elif q in norm:
                score = 300
            else:
                overlap = len(q_tokens & set(norm.split()))
                if not overlap:
                    continue
                score = overlap * 50 - len(norm)
            scored.append((score, name, slug))
        scored.sort(key=lambda t: t[0], reverse=True)
        results: list[SearchResult] = []
        for _score, name, slug in scored[:limit]:
            results.append(
                SearchResult(
                    site=self.site_id,
                    title=name,
                    url=urljoin(self._base + "/", f"serie/{slug}"),
                    kind=MediaKind.EPISODE,
                    year=None,
                    poster_url=None,
                )
            )
        return results

    # ---- episodes ----------------------------------------------------------

    def _slug_from_url(self, url: str) -> str | None:
        m = re.search(r"/serie/([^/]+)", url)
        return m.group(1) if m else None

    async def list_season(self, show: str, season: int) -> list[EpisodeRef]:
        """Find the show in the index, then list one season's episodes."""
        slug = await self._resolve_slug(show)
        if not slug:
            return []
        return await self._episodes_for(slug, season)

    async def _resolve_slug(self, show: str) -> str | None:
        index = await self._series_index()
        q = _normalize(show)
        best: tuple[int, str] | None = None
        for name, slug in index:
            norm = _normalize(name)
            if q == norm:
                return slug
            score = 0
            if norm.startswith(q) or q in norm:
                score = 100 - abs(len(norm) - len(q))
            else:
                overlap = len(set(q.split()) & set(norm.split()))
                score = overlap * 10
            if score > 0 and (best is None or score > best[0]):
                best = (score, slug)
        return best[1] if best else None

    async def list_episodes(self, series_url: str) -> list[EpisodeRef]:
        slug = self._slug_from_url(series_url)
        if not slug:
            return []
        # Probe seasons 1..N until a season returns nothing.
        all_eps: list[EpisodeRef] = []
        for season in range(1, 12):
            eps = await self._episodes_for(slug, season)
            if not eps:
                break
            all_eps.extend(eps)
        return all_eps

    async def _episodes_for(self, slug: str, season: int) -> list[EpisodeRef]:
        url = f"/serie/{slug}/{season}/de"
        try:
            resp = await self._client.get(url)
        except Exception as exc:  # pragma: no cover - network guard
            log.debug("bs.to season fetch failed: %s", exc)
            return []
        if resp.status_code != 200:
            return []
        tree = HTMLParser(resp.text)
        table = tree.css_first("table.episodes")
        if table is None:
            return []
        eps: list[EpisodeRef] = []
        seen: set[int] = set()
        ep_re = re.compile(rf"^serie/{re.escape(slug)}/{season}/(\d+)-[^/]+/de$")
        for a in table.css("a"):
            href = a.attributes.get("href") or ""
            m = ep_re.match(href)
            if not m:
                continue
            ep_num = int(m.group(1))
            if ep_num in seen:
                continue
            seen.add(ep_num)
            title = (a.attributes.get("title") or a.text() or "").strip()
            eps.append(
                EpisodeRef(
                    site=self.site_id,
                    series_title=slug.replace("-", " "),
                    season=season,
                    episode=ep_num,
                    title=title,
                    url=urljoin(self._base + "/", href),
                )
            )
        eps.sort(key=lambda e: e.episode)
        return eps

    # ---- stream resolve ----------------------------------------------------

    async def resolve_stream(self, url: str) -> StreamHandle:
        """Pick the best hoster variant for an episode page.

        bs.to reveals the real hoster link only after a JS player click, so
        we hand the hoster *page* to the Playwright capture stage which
        presses play and intercepts the stream (handling voe et al.).
        """
        try:
            resp = await self._client.get(url)
        except Exception as exc:  # pragma: no cover - network guard
            log.debug("bs.to resolve fetch failed: %s", exc)
            return StreamHandle(site=self.site_id, url=url, hint="playwright")
        if resp.status_code != 200:
            return StreamHandle(site=self.site_id, url=url, hint="playwright")
        tree = HTMLParser(resp.text)
        available: dict[str, str] = {}
        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            m = re.search(r"serie/[^\s\"']+/de/([A-Za-z0-9]+)$", href)
            if m:
                available[m.group(1)] = urljoin(self._base + "/", href)
        for pref in _HOSTER_PREFERENCE:
            if pref in available:
                log.info("[bs.to] using hoster %s", pref)
                return StreamHandle(site=self.site_id, url=available[pref], hint="playwright")
        # Fall back to the episode page itself.
        return StreamHandle(site=self.site_id, url=url, hint="playwright")

