"""Filmpalast (filmpalast.to) scraper.

Scrapes the public search and movie-detail pages, then hands the resolved
URL to yt-dlp for stream extraction.

The HTML selectors here are best-effort and based on the public site
structure as of 2024. The site changes shape periodically; if parsing
breaks, update the selectors at the marked points and add a fixture in
``tests/fixtures/filmpalast/`` that captures the new layout.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace
from typing import ClassVar
from urllib.parse import quote, urljoin, urlparse

from selectolax.parser import HTMLParser

from bankai.logging import get_logger
from bankai.queue.models import MediaKind
from bankai.scraper.base import EpisodeRef, ScraperError, SearchResult, StreamHandle
from bankai.scraper.http import detect_cloudflare, make_client
from bankai.scraper.registry import register

log = get_logger(__name__)

_BASE = "https://filmpalast.to"
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_EPISODE_RE = re.compile(r"-s(?P<season>\d{1,2})-?e(?P<episode>\d{1,3})(?:-|$)", re.I)
_RELEASE_RE = re.compile(
    r"\bRelease\s*:\s*(?P<release>.+?)(?=\s+(?:Jahr|Spielzeit|Views|Votes|Imdb|IMDb)\s*:|$)",
    re.I,
)
_RUNTIME_RE = re.compile(r"\bSpielzeit\s*:\s*(?P<minutes>\d+)\s*(?:min|Min\.)?", re.I)
_DETAIL_YEAR_RE = re.compile(r"(?:Ver.{0,3}ffentlicht|Jahr)\s*:\s*(?P<year>(?:19|20)\d{2})", re.I)


def _detail_release_metadata(tree: HTMLParser) -> tuple[str | None, int | None]:
    release_element = tree.css_first("#release_text, .sliderReleaseTitle")
    release_name = None
    if release_element is not None:
        release_name = " ".join((release_element.text(separator=" ") or "").split()) or None
    detail_text = " ".join((tree.text(separator=" ") or "").split())
    year_match = _DETAIL_YEAR_RE.search(detail_text)
    year = int(year_match.group("year")) if year_match else None
    return release_name, year


@dataclass(frozen=True, slots=True)
class FilmpalastTitleDetails:
    title: str
    url: str
    kind: MediaKind
    year: int | None
    poster_url: str | None
    release_name: str | None
    runtime_minutes: int | None
    mirrors: tuple[StreamHandle, ...]
    episodes: tuple[EpisodeRef, ...]


@register
class FilmpalastBackend:
    site_id: ClassVar[str] = "filmpalast"
    display_name: ClassVar[str] = "Filmpalast"
    supports_movies: ClassVar[bool] = True
    supports_series: ClassVar[bool] = True

    def __init__(self, base_url: str = _BASE) -> None:
        self._base = base_url.rstrip("/")
        self._client = make_client(base_url=self._base)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- search ------------------------------------------------------------

    async def search(self, query: str, *, kind: MediaKind | None = None, limit: int = 20) -> list[SearchResult]:
        results = await self._raw_search(query, limit=limit)
        if results:
            return results
        # Filmpalast's search engine frequently returns nothing for long or
        # punctuated German titles (e.g. "Green Book - Eine besondere
        # Freundschaft"). Retry with trimmed/shortened variants and finally
        # probe the direct /stream/<slug> page before giving up.
        return await self._search_fallback(query, kind=kind, limit=limit)

    async def _raw_search(self, query: str, *, limit: int = 20) -> list[SearchResult]:
        # Filmpalast's /search/title/<term> expects a URL-encoded term (spaces
        # as %20, e.g. .../search/title/Die%20Sch%C3%B6ne%20und%20das%20Biest).
        # Using "+" for spaces sends a literal plus in the path and returns the
        # wrong (default) result set, so encode properly with quote().
        resp = await self._client.get("/search/title/" + quote(query.strip()))
        detect_cloudflare(resp)
        if resp.status_code != 200:
            raise ScraperError(f"filmpalast search failed: HTTP {resp.status_code}")
        return self._parse_search(resp.text, limit=limit)

    async def recent(
        self,
        page: int,
        *,
        pages_per_page: int = 3,
        feed: str = "new",
    ) -> tuple[list[SearchResult], bool, int, int]:
        """Return a logical recent-release page assembled from site pages.

        Filmpalast publishes relatively short pages. Bankai intentionally groups
        three of them into one denser page while preserving the site's ordering.
        """
        logical_page = max(0, int(page))
        group_size = max(1, int(pages_per_page))
        feed_path = {
            "new": "",
            "movies": "/movies/new",
            "shows": "/serien/view",
            "top": "/movies/top",
        }.get(feed.strip().casefold())
        if feed_path is None:
            raise ValueError("feed must be new, movies, shows, or top")
        source_start = logical_page * group_size + 1
        source_end = source_start + group_size - 1
        results: list[SearchResult] = []
        seen: set[str] = set()
        has_next = False

        for source_page in range(source_start, source_end + 1):
            path = feed_path or "/"
            if source_page > 1:
                path = f"{feed_path}/page/{source_page}" if feed_path else f"/page/{source_page}"
            resp = await self._client.get(path)
            detect_cloudflare(resp)
            if resp.status_code != 200:
                raise ScraperError(
                    f"filmpalast recent page {source_page} failed: HTTP {resp.status_code}"
                )
            for item in self._parse_search(resp.text, limit=1_000):
                if item.url in seen:
                    continue
                seen.add(item.url)
                results.append(item)
            if source_page == source_end:
                has_next = _has_page_link(resp.text, source_page + 1)

        return results, has_next, source_start, source_end

    async def _search_fallback(self, query: str, *, kind: MediaKind | None, limit: int) -> list[SearchResult]:
        for variant in _query_variants(query):
            try:
                hits = await self._raw_search(variant, limit=limit)
            except ScraperError:
                continue
            if hits:
                return hits
        # Last resort: probe the direct movie page by slug.
        probed = await self._probe_slug(query)
        return [probed] if probed else []

    async def _probe_slug(self, query: str) -> SearchResult | None:
        slug = _slugify(query)
        if not slug:
            return None
        url = urljoin(self._base, f"/stream/{slug}")
        try:
            resp = await self._client.get(url)
        except Exception as exc:  # pragma: no cover - network guard
            log.debug("filmpalast slug probe failed: %s", exc)
            return None
        if resp.status_code != 200:
            return None
        text = resp.text
        if "iconPlay" not in text and not self._HOSTER_RE.search(text):
            return None
        tree = HTMLParser(text)
        title_el = tree.css_first("h1, h2, .name")
        title = (title_el.text() or "").strip() if title_el is not None else ""
        if not title:
            title = slug.replace("-", " ").title()
        year_match = _YEAR_RE.search(title)
        release_name, detail_year = _detail_release_metadata(tree)
        return SearchResult(
            site=self.site_id,
            title=title,
            url=url,
            kind=MediaKind.EPISODE if _EPISODE_RE.search(slug) else MediaKind.MOVIE,
            year=int(year_match.group(0)) if year_match else detail_year,
            poster_url=None,
            release_name=release_name,
        )

    async def enrich_search_results(self, results: list[SearchResult]) -> list[SearchResult]:
        """Fill filename/year from detail pages when search cards omit them.

        Filmpalast's search listing currently contains neither field, while the
        linked detail page exposes both in ``#release_text`` and Shortinfos.
        Fetch in a small bounded pool so opening the picker remains responsive.
        """

        gate = asyncio.Semaphore(4)

        async def enrich(result: SearchResult) -> SearchResult:
            if result.release_name and result.year:
                return result
            async with gate:
                try:
                    response = await self._client.get(result.url)
                except Exception as exc:  # pragma: no cover - network guard
                    log.debug("filmpalast detail metadata failed: %s", exc)
                    return result
            if response.status_code != 200:
                return result
            try:
                tree = HTMLParser(response.text)
                release_name, year = _detail_release_metadata(tree)
            except Exception as exc:  # pragma: no cover - malformed upstream HTML
                log.debug("filmpalast detail metadata parse failed: %s", exc)
                return result
            return replace(
                result,
                release_name=result.release_name or release_name,
                year=result.year or year,
            )

        return list(await asyncio.gather(*(enrich(result) for result in results)))

    async def title_details(self, url: str) -> FilmpalastTitleDetails:
        """Load a Filmpalast title page, its release filename, and mirrors."""
        try:
            response = await self._client.get(url)
        except Exception as exc:
            raise ScraperError(f"filmpalast detail fetch failed: {exc}") from exc
        detect_cloudflare(response)
        if response.status_code != 200:
            raise ScraperError(f"filmpalast detail fetch failed: HTTP {response.status_code}")

        tree = HTMLParser(response.text)
        slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        title_element = tree.css_first("h1, h2, .name")
        title = (title_element.text() or "").strip() if title_element is not None else ""
        if not title:
            title = slug.replace("-", " ").title()
        release_name, detail_year = _detail_release_metadata(tree)
        year_match = _YEAR_RE.search(title)

        poster_url = None
        poster_element = tree.css_first(
            "meta[property='og:image'], meta[name='twitter:image'], .detail img, img"
        )
        if poster_element is not None:
            poster_url = next(
                (
                    poster_element.attributes.get(name)
                    for name in ("content", "data-src", "data-original", "src")
                    if poster_element.attributes.get(name)
                ),
                None,
            )
        if poster_url:
            poster_url = urljoin(self._base, poster_url)

        detail_text = " ".join((tree.text(separator=" ") or "").split())
        runtime_match = _RUNTIME_RE.search(detail_text)
        episode_match = _EPISODE_RE.search(slug)
        episodes: list[EpisodeRef] = []
        seen_episodes: set[str] = set()
        for anchor in tree.css("a[href*='/stream/']"):
            href = anchor.attributes.get("href") or ""
            match = _EPISODE_RE.search(href)
            episode_url = urljoin(self._base, href)
            if not match or episode_url in seen_episodes:
                continue
            seen_episodes.add(episode_url)
            episodes.append(
                EpisodeRef(
                    site=self.site_id,
                    series_title=title,
                    season=int(match.group("season")),
                    episode=int(match.group("episode")),
                    title=(anchor.text() or "").strip(),
                    url=episode_url,
                )
            )
        episodes.sort(key=lambda episode: (episode.season, episode.episode))

        mirrors = tuple(
            StreamHandle(site=self.site_id, url=hoster, hint=_hoster_hint(hoster))
            for hoster in sorted(self._extract_hosters(response.text), key=_hoster_rank)
        )
        return FilmpalastTitleDetails(
            title=title,
            url=url,
            kind=MediaKind.EPISODE if episode_match or episodes else MediaKind.MOVIE,
            year=int(year_match.group(0)) if year_match else detail_year,
            poster_url=poster_url,
            release_name=release_name,
            runtime_minutes=int(runtime_match.group("minutes")) if runtime_match else None,
            mirrors=mirrors,
            episodes=tuple(episodes),
        )

    def _parse_search(self, html: str, *, limit: int) -> list[SearchResult]:
        tree = HTMLParser(html)
        results: list[SearchResult] = []
        # Each hit is an <article class="liste rb"> with <a class="rb"> for the title.
        for article in tree.css("article.liste"):
            anchor = article.css_first("a.rb, a")
            title_el = article.css_first("h2, .name, a")
            if anchor is None or title_el is None:
                continue
            href = anchor.attributes.get("href") or ""
            if not href:
                continue
            title = (title_el.text() or "").strip()
            if not title:
                continue
            poster_el = article.css_first("img")
            poster = None
            if poster_el is not None:
                poster = next(
                    (
                        poster_el.attributes.get(name)
                        for name in ("data-src", "data-original", "src")
                        if poster_el.attributes.get(name)
                    ),
                    None,
                )
            if poster:
                poster = urljoin(self._base, poster)
            article_text = " ".join((article.text(separator=" ") or "").split())
            release_match = _RELEASE_RE.search(article_text)
            runtime_match = _RUNTIME_RE.search(article_text)
            release_name = release_match.group("release").strip().rstrip("\u200b") if release_match else None
            year_match = _YEAR_RE.search(title)
            if year_match is None and release_name:
                year_match = _YEAR_RE.search(release_name)
            year = int(year_match.group(0)) if year_match else None
            kind = MediaKind.EPISODE if _EPISODE_RE.search(href) or _EPISODE_RE.search(title) else MediaKind.MOVIE
            results.append(
                SearchResult(
                    site=self.site_id,
                    title=title,
                    url=urljoin(self._base, href),
                    kind=kind,
                    year=year,
                    poster_url=poster,
                    release_name=release_name,
                    raw={"runtime_minutes": runtime_match.group("minutes")} if runtime_match else {},
                )
            )
            if len(results) >= limit:
                break
        return results

    # ---- episodes ----------------------------------------------------------

    async def list_episodes(self, series_url: str) -> list[EpisodeRef]:
        """Scrape a show's main page for ``-sNN-eNN`` episode links."""
        resp = await self._client.get(series_url)
        if resp.status_code != 200:
            raise ScraperError(f"filmpalast episodes failed: HTTP {resp.status_code}")
        tree = HTMLParser(resp.text)
        eps: list[EpisodeRef] = []
        seen: set[str] = set()
        for a in tree.css("a[href*='/stream/']"):
            href = a.attributes.get("href") or ""
            m = _EPISODE_RE.search(href)
            if not m or href in seen:
                continue
            seen.add(href)
            eps.append(
                EpisodeRef(
                    site=self.site_id,
                    series_title=series_url.rsplit("/", 1)[-1],
                    season=int(m.group("season")),
                    episode=int(m.group("episode")),
                    title=(a.text() or "").strip(),
                    url=urljoin(self._base, href),
                )
            )
        eps.sort(key=lambda e: (e.season, e.episode))
        return eps

    async def list_season(self, show: str, season: int) -> list[EpisodeRef]:
        """Find a show by name and return episode refs for a single season.

        Strategy: try likely direct ``/stream/<slug>`` show pages first, then
        search for ``"<show> staffel <season>"`` and ``"<show>"``.
        """
        found: list[EpisodeRef] = []
        for url in self._series_url_candidates(show, season):
            found.extend(await self._episodes_for_season(url, season))
        if found:
            return _dedupe_episodes(found)
        for q in (
            f"{show} s{season:02d}e01",
            f"{show} s{season:02d}",
            f"{show} staffel {season}",
            show,
        ):
            hits = await self.search(q, limit=5)
            for hit in hits:
                eps = await self._episodes_for_season(hit.url, season)
                if eps:
                    found.extend(eps)
                    continue
                ep = _episode_from_url(hit.url, hit.title)
                if ep and ep.season == season:
                    found.append(ep)
            if found:
                return _dedupe_episodes(found)
        return _dedupe_episodes(found)

    async def _episodes_for_season(self, series_url: str, season: int) -> list[EpisodeRef]:
        try:
            eps = await self.list_episodes(series_url)
        except ScraperError:
            return []
        return [e for e in eps if e.season == season]

    def _series_url_candidates(self, show: str, season: int) -> list[str]:
        slug = _slugify(show)
        if not slug:
            return []
        candidates = [
            slug,
            f"{slug}-s{season:02d}e01",
            f"{slug}-staffel-{season}",
            f"{slug}-season-{season}",
            f"{slug}-s{season:02d}",
        ]
        seen: set[str] = set()
        urls: list[str] = []
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            urls.append(urljoin(self._base, f"/stream/{candidate}"))
        return urls

    # ---- stream resolve ----------------------------------------------------

    # Any <a ...> tag that carries the iconPlay class, in either attribute order.
    _HOSTER_ANCHOR_RE = re.compile(r"<a\b[^>]*iconPlay[^>]*>", re.I)
    _URL_ATTR_RE = re.compile(
        r"""(?:data-player-url|data-url|data-src|href)=["']([^"']+)["']""",
        re.I,
    )

    async def resolve_stream(self, url: str) -> StreamHandle:
        """Scrape the detail page for the hoster URL (Voe / Streamtape / \u2026).

        Filmpalast itself is just a wrapper; the actual playable URL lives in
        ``<a class="button iconPlay" href="https://voe.sx/\u2026">`` tags. A page
        often lists several mirrors (e.g. voe.sx, veev.to, vinovo.to); we pick
        the one most likely to extract cleanly, since hosters like veev.to are
        not supported by yt-dlp and defeat the playwright fallback too.
        """
        handles = await self.resolve_all_streams(url)
        if not handles:
            return StreamHandle(site=self.site_id, url=url, hint="playwright")
        log.info(
            "[filmpalast] resolved hoster: %s (from %d mirror(s))",
            handles[0].url,
            len(handles),
        )
        return handles[0]

    async def resolve_all_streams(
        self,
        url: str,
        *,
        strict: bool = False,
    ) -> list[StreamHandle]:
        """Return every direct hoster mirror, ordered by extractor reliability."""
        try:
            resp = await self._client.get(url)
        except Exception as exc:
            log.debug("filmpalast resolve_stream fetch failed: %s", exc)
            if strict:
                raise ScraperError(f"filmpalast detail fetch failed: {exc}") from exc
            return []
        if resp.status_code != 200:
            if strict:
                raise ScraperError(f"filmpalast detail fetch failed: HTTP {resp.status_code}")
            return []
        hosters = sorted(self._extract_hosters(resp.text), key=_hoster_rank)
        return [
            StreamHandle(site=self.site_id, url=hoster, hint=_hoster_hint(hoster))
            for hoster in hosters
        ]

    async def resolve_supported_streams(
        self,
        url: str,
        *,
        strict: bool = False,
    ) -> list[StreamHandle]:
        """Return mirrors backed by hosters the extractor is known to handle.

        ``resolve_all_streams`` intentionally retains unknown and known-bad
        mirrors as last-ditch pipeline fallbacks. Availability checks need a
        stricter answer: a Filmpalast detail page alone is not evidence that a
        downloadable German stream currently exists.
        """
        return [
            handle
            for handle in await self.resolve_all_streams(url, strict=strict)
            if is_supported_hoster(handle.url)
        ]

    async def resolve_live_streams(self, url: str) -> list[StreamHandle]:
        """Return supported mirrors whose player page is currently reachable.

        This is still a lightweight metadata probe, not a media download. It
        catches expired VOE links (normally 404/410) and dead-page bodies. A
        host such as Vinovo that frequently exposes only an advert placeholder
        is intentionally not eligible for availability certification.
        """
        supported = await self.resolve_supported_streams(url, strict=True)

        async def probe(handle: StreamHandle) -> tuple[StreamHandle, bool | None]:
            try:
                resp = await self._client.get(handle.url)
            except Exception as exc:
                log.debug("filmpalast hoster probe failed for %s: %s", handle.url, exc)
                return handle, None
            if resp.status_code >= 400:
                return handle, False
            content_type = resp.headers.get("content-type", "").casefold()
            body = resp.text[:100_000].casefold() if "text" in content_type or not content_type else ""
            if any(marker in body for marker in _DEAD_HOSTER_MARKERS):
                return handle, False
            return handle, True

        probes = await asyncio.gather(*(probe(handle) for handle in supported))
        completed_probes = sum(result is not None for _, result in probes)
        if supported and completed_probes == 0:
            raise ScraperError("all Filmpalast hoster probes failed")
        return [handle for handle, is_live in probes if is_live]

    def _extract_hosters(self, html: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []

        def add(href: str | None) -> None:
            value = (href or "").strip()
            if value.startswith("http") and value not in seen:
                seen.add(value)
                out.append(value)

        # Newer Filmpalast pages put FireStream and similar mirrors in a
        # data-player-url attribute while leaving href empty or javascript-y.
        # Parse the tag properly first, then retain the regex fallback for
        # malformed fragments returned by the site from time to time.
        for node in HTMLParser(html).css("a.iconPlay"):
            attrs = node.attributes
            for name in ("data-player-url", "data-url", "data-src", "href"):
                value = attrs.get(name)
                if value:
                    add(value)
                    break
        for tag in self._HOSTER_ANCHOR_RE.findall(html):
            match = self._URL_ATTR_RE.search(tag)
            if match:
                add(match.group(1))
        return out


# Hosters ranked by how reliably the extractor (yt-dlp, then playwright)
# can pull a media URL. Lower score = preferred. Anything unlisted sits in
# the middle; known-bad hosts (veev.to defeats both yt-dlp and playwright)
# are pushed to the back.
_HOSTER_PREFERRED = (
    "vidsonic.net",
    "firestream.to",
    "vidmatrixa.com",
    "flyfile.app",
    "voe.sx",
    "voe",
    "streamtape",
    "dood",
    "mixdrop",
    "vidoza",
    "upstream",
    "filemoon",
    "supervideo",
    "vinovo",
)
_HOSTER_AVOID = ("veev.to", "veev")
_AVAILABILITY_AVOID = (*_HOSTER_AVOID, "vinovo")
_DEAD_HOSTER_MARKERS = (
    "not recognized",
    "404 - not found",
    "file not found",
    "video has been deleted",
    "video was deleted",
)


def is_supported_hoster(url: str) -> bool:
    """Whether ``url`` belongs to a hoster with a working extraction path."""
    host = urlparse(url).netloc.casefold()
    if any(bad in host for bad in _AVAILABILITY_AVOID):
        return False
    return any(name in host for name in _HOSTER_PREFERRED)


def _hoster_rank(url: str) -> int:
    host = urlparse(url).netloc.lower()
    for i, key in enumerate(_HOSTER_PREFERRED):
        if key in host:
            return i
    if any(bad in host for bad in _HOSTER_AVOID):
        return 900
    return 500


def _hoster_hint(url: str) -> str:
    """Use the browser directly for hosts that expose HLS at runtime."""
    host = urlparse(url).netloc.lower()
    browser_hosts = (
        "vidsonic.net",
        "firestream.to",
        "vidmatrixa.com",
        "flyfile.app",
        "voe.sx",
        "voe",
        "vinovo",
        "filemoon",
    )
    return "playwright" if any(name in host for name in browser_hosts) else "ytdlp"


def _query_variants(query: str) -> list[str]:
    """Generate progressively looser search queries for a stubborn title.

    Filmpalast's search chokes on long, punctuated titles. We try the title
    without its subtitle, without a trailing year, without punctuation, and
    finally progressively shorter word-prefixes (down to a single word).
    """
    variants: list[str] = []

    def add(value: str) -> None:
        cleaned = value.strip()
        if cleaned and cleaned.casefold() not in {v.casefold() for v in variants}:
            variants.append(cleaned)

    q = query.strip()
    for sep in (" - ", " \u2013 ", ": ", " | ", " / "):
        if sep in q:
            add(q.split(sep, 1)[0])
    no_year = re.sub(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$", "", q).strip()
    add(no_year)
    no_punct = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", no_year)).strip()
    add(no_punct)
    words = no_punct.split()
    for n in range(len(words) - 1, 0, -1):
        add(" ".join(words[:n]))
    # Drop the original query (it already failed before fallback ran).
    return [v for v in variants if v.casefold() != q.casefold()]


def _has_page_link(html: str, page: int) -> bool:
    """Return whether the listing links to the requested source page."""
    tree = HTMLParser(html)
    expected = f"/page/{page}"
    return any(
        (anchor.attributes.get("href") or "").rstrip("/").endswith(expected)
        for anchor in tree.css("a[href]")
    )


def _slugify(value: str) -> str:
    clean = value.casefold()
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for source, target in replacements.items():
        clean = clean.replace(source, target)
    clean = re.sub(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$", "", clean).strip()
    clean = re.sub(r"[^a-z0-9]+", "-", clean).strip("-")
    return clean


def _episode_from_url(url: str, title: str) -> EpisodeRef | None:
    match = _EPISODE_RE.search(url)
    if not match:
        return None
    season = int(match.group("season"))
    episode = int(match.group("episode"))
    clean_title = re.sub(r"\s*[Ss]\d{1,2}[Ee]\d{1,3}.*$", "", title).strip(" -_")
    return EpisodeRef(
        site=FilmpalastBackend.site_id,
        series_title=clean_title or url.rsplit("/", 1)[-1],
        season=season,
        episode=episode,
        title=title,
        url=url,
    )


def _dedupe_episodes(episodes: list[EpisodeRef]) -> list[EpisodeRef]:
    seen: set[tuple[int, int, str]] = set()
    out: list[EpisodeRef] = []
    for ep in sorted(episodes, key=lambda e: (e.season, e.episode, e.url)):
        key = (ep.season, ep.episode, ep.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(ep)
    return out
