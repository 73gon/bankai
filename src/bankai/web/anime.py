"""Nyaa-only anime catalogue with TVDB enrichment.

Nyaa exposes a stable RSS representation for browse/search results.  We use
that feed for the cheap list operation and only open individual detail pages
when uploader-description filtering is requested.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from urllib.parse import quote, urlparse
from xml.etree import ElementTree as ET

import httpx
from selectolax.parser import HTMLParser

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata.tvdb import TitleAlias, TVDBClient
from bankai.queue.models import MediaKind
from bankai.web import discover

log = get_logger(__name__)

_NYAA_BASE = "https://nyaa.si"
_NYAA_NS = "https://nyaa.si/xmlns/nyaa"
_RSS_PAGE_SIZE = 75
_CACHE_TTL = 15 * 60
_TVDB_CACHE: dict[str, tuple[float, list[AnimeTVDBMatch]]] = {}
_DETAIL_CACHE: dict[str, tuple[float, tuple[str, str | None, str | None]]] = {}


@dataclass(frozen=True, slots=True)
class AnimeTVDBMatch:
    tvdb_id: int
    kind: str  # show | movie
    english_title: str
    japanese_title: str | None = None
    year: int | None = None
    poster_url: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NyaaEntry:
    id: int
    title: str
    download_url: str
    detail_url: str
    magnet_uri: str
    info_hash: str
    category_id: str
    category: str
    size: str
    size_bytes: int
    seeders: int
    leechers: int
    downloads: int
    comments: int
    trusted: bool
    remake: bool
    published_at: str | None
    publisher: str | None
    quality: str | None
    description: str = ""
    tvdb: AnimeTVDBMatch | None = None


@dataclass(frozen=True, slots=True)
class AnimeSearchPage:
    items: list[NyaaEntry]
    page: int
    has_next: bool
    aliases: list[str]


def is_nyaa_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and (host == "nyaa.si" or host.endswith(".nyaa.si"))


def split_filter_terms(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [term.strip().casefold() for term in re.split(r"[,;\n]+", raw) if term.strip()]


def clean_release_title(title: str) -> str:
    """Reduce a scene-style release name to a TVDB-searchable anime title."""
    value = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", title).strip()
    value = re.sub(r"\.(?:mkv|mp4|avi|m4v|mov|ts|webm)$", "", value, flags=re.I)
    value = re.sub(r"\s*[|/]\s*.*$", "", value)
    value = re.sub(r"\b(?:season\s*)?S\d{1,2}\s*[-_. ]+\s*\d{1,4}\b.*$", "", value, flags=re.I)
    value = re.sub(r"\bS\d{1,2}E\d{1,4}\b.*$", "", value, flags=re.I)
    value = re.sub(r"\s+-\s+\d{1,4}(?:v\d+)?\b.*$", "", value, flags=re.I)
    value = re.sub(r"\s*\(\s*\d{1,4}\s*[-~]\s*\d{1,4}\s*\).*$", "", value)
    value = re.sub(r"\s+(?:season\s+)?\d+\s+(?:complete|batch)\b.*$", "", value, flags=re.I)
    value = re.sub(
        r"\s+(?:(?:\d+(?:st|nd|rd|th)|final)\s+season|season\s+\d+)\s*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?:\s*[\[(](?:2160p|1080p|720p|480p|4k|uhd|batch|complete|web[- .]?dl|"
        r"webrip|bluray|bdrip|x26[45]|h\.?26[45]|hevc|av1|aac|flac|dual audio)[^\])]*[\])])+$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"[._]+", " ", value)
    return re.sub(r"\s{2,}", " ", value).strip(" -_()[]") or title.strip()


def _publisher(title: str) -> str | None:
    match = re.match(r"\s*\[([^]]+)]", title)
    return match.group(1).strip() if match else None


def _quality(title: str) -> str | None:
    match = re.search(r"(?<!\d)(2160|1080|720|480)p\b", title, flags=re.I)
    return f"{match.group(1)}p" if match else None


def _size_bytes(raw: str) -> int:
    match = re.search(r"([\d.]+)\s*([KMGT]i?B)", raw, flags=re.I)
    if not match:
        return 0
    value = float(match.group(1))
    power = {"KB": 1, "KIB": 1, "MB": 2, "MIB": 2, "GB": 3, "GIB": 3, "TB": 4, "TIB": 4}
    return int(value * (1024 ** power[match.group(2).upper()]))


def parse_rss(xml: str) -> list[NyaaEntry]:
    root = ET.fromstring(xml)
    items: list[NyaaEntry] = []
    for node in root.findall("./channel/item"):

        def text(name: str, default: str = "", parent: ET.Element = node) -> str:
            child = parent.find(name)
            return (child.text or default).strip() if child is not None else default

        def nyaa(name: str, default: str = "") -> str:
            return text(f"{{{_NYAA_NS}}}{name}", default)

        detail_url = text("guid")
        match = re.search(r"/view/(\d+)", detail_url)
        info_hash = nyaa("infoHash").lower()
        title = html.unescape(text("title"))
        if not match or not info_hash:
            continue
        magnet = (
            f"magnet:?xt=urn:btih:{info_hash}&dn={quote(title)}"
            "&tr=http%3A%2F%2Fnyaa.tracker.wf%3A7777%2Fannounce"
            "&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce"
            "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
        )
        size = nyaa("size")
        rss_description = HTMLParser(text("description")).text(separator=" ", strip=True)
        items.append(
            NyaaEntry(
                id=int(match.group(1)),
                title=title,
                download_url=text("link"),
                detail_url=detail_url,
                magnet_uri=magnet,
                info_hash=info_hash,
                category_id=nyaa("categoryId"),
                category=nyaa("category"),
                size=size,
                size_bytes=_size_bytes(size),
                seeders=int(nyaa("seeders", "0") or 0),
                leechers=int(nyaa("leechers", "0") or 0),
                downloads=int(nyaa("downloads", "0") or 0),
                comments=int(nyaa("comments", "0") or 0),
                trusted=nyaa("trusted").casefold() == "yes",
                remake=nyaa("remake").casefold() == "yes",
                published_at=text("pubDate") or None,
                publisher=_publisher(title),
                quality=_quality(title),
                description=rss_description,
            )
        )
    return items


async def _fetch_rss(
    client: httpx.AsyncClient, query: str, category: str, page: int
) -> list[NyaaEntry]:
    response = await client.get(
        "/",
        params={
            "page": "rss",
            "q": query,
            "c": category,
            "f": "0",
            "p": page + 1,
            "s": "seeders",
            "o": "desc",
        },
    )
    response.raise_for_status()
    return parse_rss(response.text)


async def _detail(
    client: httpx.AsyncClient, entry: NyaaEntry
) -> tuple[str, str | None, str | None]:
    hit = _DETAIL_CACHE.get(entry.detail_url)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    response = await client.get(entry.detail_url)
    response.raise_for_status()
    tree = HTMLParser(response.text)
    description_node = tree.css_first("#torrent-description")
    description = description_node.text(separator=" ", strip=True) if description_node else ""
    magnet_node = tree.css_first('a[href^="magnet:"]')
    magnet = html.unescape(magnet_node.attributes.get("href", "")) if magnet_node else None
    uploader_node = tree.css_first('a[href^="/user/"]')
    uploader = uploader_node.text(strip=True) if uploader_node else None
    result = (description, magnet or None, uploader or None)
    _DETAIL_CACHE[entry.detail_url] = (time.time(), result)
    return result


def _normalise(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _match_score(query: str, candidate: AnimeTVDBMatch) -> float:
    clean = _normalise(query)
    names = [candidate.english_title, candidate.japanese_title or "", *candidate.aliases]

    def score(name: str) -> float:
        normalized = _normalise(name)
        if clean == normalized:
            return 1.0
        query_words = clean.split()
        name_words = normalized.split()
        if query_words and name_words[: len(query_words)] == query_words:
            return 0.96
        return SequenceMatcher(None, clean, normalized).ratio() * 0.72

    return max(score(name) for name in names if name)


async def tvdb_candidates(query: str, *, limit: int = 8) -> list[AnimeTVDBMatch]:
    clean = query.strip()
    if not clean or not discover.is_configured():
        return []
    cache_key = _normalise(clean)
    hit = _TVDB_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1][:limit]
    metadata = get_settings().metadata
    client = TVDBClient(
        api_key=metadata.tvdb_api_key,
        pin=metadata.tvdb_pin,
        languages=["eng", "jpn"],
    )
    try:
        series_eng, series_jpn, movies_eng, movies_jpn = await asyncio.gather(
            client.search_aliases(
                clean,
                kind=MediaKind.EPISODE,
                limit=3,
                search_language="eng",
            ),
            client.search_aliases(
                clean,
                kind=MediaKind.EPISODE,
                limit=3,
                search_language="jpn",
            ),
            client.search_aliases(
                clean,
                kind=MediaKind.MOVIE,
                limit=3,
                search_language="eng",
            ),
            client.search_aliases(
                clean,
                kind=MediaKind.MOVIE,
                limit=3,
                search_language="jpn",
            ),
        )
        series = [*series_eng, *series_jpn]
        movies = [*movies_eng, *movies_jpn]
    except Exception as exc:
        log.warning("TVDB anime lookup failed for %r: %s", clean, exc)
        return []
    finally:
        await client.aclose()

    async def convert(alias: TitleAlias, kind: str) -> AnimeTVDBMatch | None:
        if alias.tvdb_id is None:
            return None
        english = alias.english_title or alias.name or alias.japanese_title
        if not english:
            return None
        poster: str | None = None
        try:
            results = await discover.search(english, kind=kind, limit=5)
            matched = next((item for item in results if item.tvdb_id == alias.tvdb_id), None)
            poster = matched.poster_url if matched else None
        except Exception:
            pass
        return AnimeTVDBMatch(
            tvdb_id=alias.tvdb_id,
            kind=kind,
            english_title=english,
            japanese_title=alias.japanese_title or (alias.name if alias.name != english else None),
            year=alias.year,
            poster_url=poster,
            aliases=tuple(
                dict.fromkeys(
                    value
                    for value in (alias.name, *alias.aliases)
                    if value and value not in {english, alias.japanese_title}
                )
            ),
        )

    converted = await asyncio.gather(
        *(convert(alias, "show") for alias in series),
        *(convert(alias, "movie") for alias in movies),
    )
    unique: list[AnimeTVDBMatch] = []
    seen: set[tuple[str, int]] = set()
    for item in converted:
        if item is None or (item.kind, item.tvdb_id) in seen:
            continue
        seen.add((item.kind, item.tvdb_id))
        unique.append(item)
    unique.sort(key=lambda item: _match_score(clean, item), reverse=True)
    _TVDB_CACHE[cache_key] = (time.time(), unique)
    return unique[:limit]


async def _quick_tvdb_candidates(query: str) -> list[AnimeTVDBMatch]:
    """Resolve a release title with English/Japanese TVDB translations."""
    return await tvdb_candidates(query, limit=4)


async def _enrich_tvdb(
    entries: list[NyaaEntry], query_matches: list[AnimeTVDBMatch]
) -> list[NyaaEntry]:
    semaphore = asyncio.Semaphore(4)
    release_lookups: dict[str, asyncio.Task[list[AnimeTVDBMatch]]] = {}

    async def release_candidates(release_query: str) -> list[AnimeTVDBMatch]:
        async with semaphore:
            return await _quick_tvdb_candidates(release_query)

    async def enrich(entry: NyaaEntry) -> NyaaEntry:
        release_query = clean_release_title(entry.title)
        candidates = query_matches
        if not candidates:
            lookup_key = _normalise(release_query)
            task = release_lookups.get(lookup_key)
            if task is None:
                task = asyncio.create_task(release_candidates(release_query))
                release_lookups[lookup_key] = task
            candidates = await task
        match = max(candidates, key=lambda item: _match_score(release_query, item), default=None)
        if match is not None and _match_score(release_query, match) < 0.28:
            match = None
        return replace(entry, tvdb=match)

    return list(await asyncio.gather(*(enrich(entry) for entry in entries)))


def _matches_filters(
    entry: NyaaEntry,
    *,
    quality: str | None,
    publisher: str | None,
    title_terms: list[str],
    description_terms: list[str],
    min_seeders: int,
) -> bool:
    if (
        quality
        and quality.casefold() != "all"
        and (entry.quality or "").casefold() != quality.casefold()
    ):
        return False
    if publisher and publisher.casefold() not in (entry.publisher or "").casefold():
        return False
    if entry.seeders < min_seeders:
        return False
    if title_terms and not any(term in entry.title.casefold() for term in title_terms):
        return False
    return not description_terms or any(
        term in entry.description.casefold() for term in description_terms
    )


async def search(
    query: str = "",
    *,
    category: str = "1_0",
    page: int = 0,
    quality: str | None = None,
    publisher: str | None = None,
    title_filters: str | None = None,
    description_filters: str | None = None,
    min_seeders: int = 0,
) -> AnimeSearchPage:
    if category not in {"1_0", "1_1", "1_2", "1_3", "1_4"}:
        category = "1_0"
    query_candidates = await tvdb_candidates(query, limit=8) if query.strip() else []
    query_matches = [
        candidate for candidate in query_candidates[:1] if _match_score(query, candidate) >= 0.75
    ]
    aliases: list[str] = []
    for value in [
        query.strip(),
        *(item.english_title for item in query_matches),
        *(item.japanese_title or "" for item in query_matches),
    ]:
        if value and value.casefold() not in {item.casefold() for item in aliases}:
            aliases.append(value)
    queries = aliases or [""]
    headers = {"User-Agent": get_settings().scraper.user_agent}
    async with httpx.AsyncClient(
        base_url=_NYAA_BASE, headers=headers, timeout=30, follow_redirects=True
    ) as client:
        fetched = await asyncio.gather(
            *(_fetch_rss(client, term, category, page) for term in queries),
            return_exceptions=True,
        )
        batches = [batch for batch in fetched if isinstance(batch, list)]
        if not batches:
            failure = next((item for item in fetched if isinstance(item, Exception)), None)
            raise RuntimeError(f"Nyaa did not return a usable feed: {failure}")
        by_hash: dict[str, NyaaEntry] = {}
        for batch in batches:
            for entry in batch:
                by_hash.setdefault(entry.info_hash, entry)
        items = list(by_hash.values())
        description_terms = split_filter_terms(description_filters)
        if description_terms or publisher:
            detail_slots = asyncio.Semaphore(6)

            async def load_detail(
                entry: NyaaEntry,
            ) -> tuple[str, str | None, str | None]:
                try:
                    async with detail_slots:
                        return await _detail(client, entry)
                except Exception as exc:
                    log.debug("Nyaa description lookup failed for %s: %s", entry.id, exc)
                    return entry.description, None, entry.publisher

            details = await asyncio.gather(*(load_detail(entry) for entry in items))
            items = [
                replace(
                    entry,
                    description=description or entry.description,
                    magnet_uri=magnet or entry.magnet_uri,
                    publisher=uploader or entry.publisher,
                )
                for entry, (description, magnet, uploader) in zip(items, details, strict=True)
            ]

    title_terms = split_filter_terms(title_filters)
    items = [
        entry
        for entry in items
        if _matches_filters(
            entry,
            quality=quality,
            publisher=publisher,
            title_terms=title_terms,
            description_terms=description_terms,
            min_seeders=min_seeders,
        )
    ]
    items.sort(key=lambda entry: (entry.seeders, entry.downloads), reverse=True)
    # Keep every Nyaa row so pagination never skips releases. Blank browsing
    # gets fast automatic TVDB matches for the leading rows; all other rows
    # remain downloadable through the explicit TVDB picker.
    enrich_count = len(items) if query_matches else min(20, len(items))
    items = [
        *(await _enrich_tvdb(items[:enrich_count], query_matches)),
        *items[enrich_count:],
    ]
    has_next = any(len(batch) >= _RSS_PAGE_SIZE for batch in batches)
    return AnimeSearchPage(items=items, page=page, has_next=has_next, aliases=aliases)


def entry_to_dict(entry: NyaaEntry) -> dict:
    return asdict(entry)


def tvdb_to_dict(item: AnimeTVDBMatch) -> dict:
    return asdict(item)


__all__ = [
    "AnimeSearchPage",
    "AnimeTVDBMatch",
    "NyaaEntry",
    "clean_release_title",
    "entry_to_dict",
    "is_nyaa_url",
    "parse_rss",
    "search",
    "split_filter_terms",
    "tvdb_candidates",
    "tvdb_to_dict",
]
