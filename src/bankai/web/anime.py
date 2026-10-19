"""Nyaa-only anime catalogue, each release identified on AniDB.

Nyaa exposes a stable RSS representation for browse/search results.  We use
that feed for the cheap list operation and only open individual detail pages
when uploader-description filtering is requested.
"""

from __future__ import annotations

import asyncio
import functools
import html
import re
import time
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import quote, urlparse
from xml.etree import ElementTree as ET

import httpx
from selectolax.parser import HTMLParser

from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

_NYAA_BASE = "https://nyaa.si"
_NYAA_NS = "https://nyaa.si/xmlns/nyaa"
_RSS_PAGE_SIZE = 75
_CACHE_TTL = 15 * 60
_DETAIL_CACHE: dict[str, tuple[float, tuple[str, str | None, str | None]]] = {}


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
    season: int | None = None
    episode: int | None = None
    description: str = ""
    # The AniDB entry and episode it is, when that can be told offline:
    # {anidb_id, title, english_title, episode, poster_url, year, type}.
    anidb: dict | None = None


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


# Pure functions of a release name, asked about the same tens of thousands
# of catalogue names every cycle.
@functools.lru_cache(maxsize=65536)
def deconstruct_release(title: str) -> tuple[str, int] | None:
    """Split the final ' - episode [tags]' suffix, retaining numeric title parts."""
    value = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", title).strip()
    value = re.sub(r"\.(?:mkv|mp4|avi|m4v|mov|ts|webm)$", "", value, flags=re.I)
    match = re.fullmatch(
        r"(?P<name>.+)\s+-\s+(?P<episode>\d{1,4})(?:v\d+)?\s*(?:\[[^]]*\]\s*)*",
        value,
    )
    if match:
        return match.group("name").strip(), int(match.group("episode"))
    return None


@functools.lru_cache(maxsize=65536)
def release_part(title: str) -> tuple[str, int | None]:
    """Keep a terminal Part N separate from the show's own title."""
    structured = deconstruct_release(title)
    name = structured[0] if structured else title
    match = re.fullmatch(r"(.+?)\s+Part\s+(\d+)\s*", name, re.I)
    return (match[1].strip(), int(match[2])) if match else (name, None)


def clean_release_title(title: str) -> str:
    """Reduce a scene-style release name to the anime's own title."""
    value = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", title).strip()
    value = re.sub(r"\.(?:mkv|mp4|avi|m4v|mov|ts|webm)$", "", value, flags=re.I)
    value = re.sub(r"\s*[|/]\s*.*$", "", value)
    structured = deconstruct_release(title)
    if structured:
        value = release_part(title)[0]
    else:
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


def release_episode_info(title: str) -> tuple[int | None, int | None]:
    """Extract explicit season and episode markers from a Nyaa release title.

    Anime releases commonly use either TV notation (``S02E01``), a written
    season plus an episode, or absolute numbering (``Title - 41``).  Absolute
    episode numbers intentionally keep the season unknown rather than guess it.
    """
    value = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", title).strip()
    match = re.search(r"\bS(?P<season>\d{1,2})\s*E(?P<episode>\d{1,4})\b", value, re.I)
    if match:
        return int(match.group("season")), int(match.group("episode"))

    season_match = (
        re.search(r"\b(?:season|staffel)\s*(\d{1,2})\b", value, re.I)
        or re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", value, re.I)
        or re.search(r"\bS(\d{1,2})\b", value, re.I)
    )
    structured = deconstruct_release(title)
    if structured:
        return int(season_match.group(1)) if season_match else None, structured[1]

    episode_match = (
        re.search(r"\b(?:episode|ep|e)\s*0*(\d{1,4})(?:v\d+)?\b", value, re.I)
        or re.search(r"\s+-\s+0*(\d{1,4})(?:v\d+)?(?:\b|\s)", value, re.I)
    )
    return (
        int(season_match.group(1)) if season_match else None,
        int(episode_match.group(1)) if episode_match else None,
    )


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
        season, episode = release_episode_info(title)
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
                season=season,
                episode=episode,
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


async def _detail_url(
    client: httpx.AsyncClient, detail_url: str
) -> tuple[str, str | None, str | None]:
    hit = _DETAIL_CACHE.get(detail_url)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    response = await client.get(detail_url)
    response.raise_for_status()
    tree = HTMLParser(response.text)
    description_node = tree.css_first("#torrent-description")
    # Nyaa descriptions often contain literal Markdown. Preserve block and
    # source line breaks so headings, links, and GFM tables remain renderable
    # instead of collapsing into one long paragraph.
    description = description_node.text(separator="\n", strip=True) if description_node else ""
    magnet_node = tree.css_first('a[href^="magnet:"]')
    magnet = html.unescape(magnet_node.attributes.get("href", "")) if magnet_node else None
    uploader_node = tree.css_first('a[href^="/user/"]')
    uploader = uploader_node.text(strip=True) if uploader_node else None
    result = (description, magnet or None, uploader or None)
    _DETAIL_CACHE[detail_url] = (time.time(), result)
    return result


async def detail(detail_url: str) -> tuple[str, str | None, str | None]:
    """Load one Nyaa description lazily for the result-card disclosure."""
    if not is_nyaa_url(detail_url):
        raise ValueError("description URL must point to nyaa.si")
    headers = {"User-Agent": get_settings().scraper.user_agent}
    async with httpx.AsyncClient(
        base_url=_NYAA_BASE, headers=headers, timeout=30, follow_redirects=True
    ) as client:
        return await _detail_url(client, detail_url)


def _normalise(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def anidb_match(title: str, *, table: Any, mappings: dict[str, Any]) -> dict | None:
    """The AniDB entry and episode a release is, from the title index; no network.

    The user's choice for its name first, as the automation does; else the
    name as AniDB has it, its episode settled into the right part of a split
    season. None when no single entry answers for the name.
    """
    from bankai.metadata import anidb as anidb_mod
    from bankai.metadata import anidb_art
    from bankai.web import erai

    if table is None:
        return None
    parsed = erai._erai_name_episode(title)
    name = parsed[0] if parsed else clean_release_title(title)
    number = parsed[1] if parsed else release_episode_info(title)[1]
    saved = mappings.get(erai._mapping_key(title)) or {}
    if str(saved.get("anidb_id") or "").isdigit():
        entry = table.anime.get(int(saved["anidb_id"]))
        if number is not None:
            number += int(saved.get("anidb_episode_offset") or 0)
    else:
        entry = anidb_mod.resolve_in(table, name).anime
        if entry is not None and number is not None:
            entry, number = anidb_mod.settle_episode(table, entry, number)
    if entry is None:
        return None
    facts = anidb_art.facts(entry.aid)
    return {
        "anidb_id": entry.aid,
        "title": entry.title,
        "english_title": entry.english_title,
        "episode": number,
        "poster_url": anidb_art.cover(entry.aid),
        "year": facts.get("year"),
        "type": facts.get("type"),
    }


async def _enrich_anidb(entries: list[NyaaEntry]) -> list[NyaaEntry]:
    from bankai.metadata import anidb as anidb_mod
    from bankai.web import erai

    table = await anidb_mod.index()
    mappings = await asyncio.to_thread(erai._load_mappings)
    return [
        replace(entry, anidb=anidb_match(entry.title, table=table, mappings=mappings))
        for entry in entries
    ]


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
    aliases: list[str] = [query.strip()] if query.strip() else []
    if query.strip():
        from bankai.metadata import anidb as anidb_mod

        # The anime's other names on AniDB widen the search: Erai publishes
        # under the romaji title, people type the English one.
        table = await anidb_mod.index()
        found = anidb_mod.resolve_in(table, query.strip()).anime if table is not None else None
        for value in (found.title, found.english_title) if found is not None else ():
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
                        return await _detail_url(client, entry.detail_url)
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
    # Every row is identified: AniDB's title index is on disk, so it costs
    # no requests. A row it cannot place is still downloadable once an AniDB
    # entry is picked for it.
    items = await _enrich_anidb(items)
    has_next = any(len(batch) >= _RSS_PAGE_SIZE for batch in batches)
    return AnimeSearchPage(items=items, page=page, has_next=has_next, aliases=aliases)


def entry_to_dict(entry: NyaaEntry) -> dict:
    return asdict(entry)


__all__ = [
    "AnimeSearchPage",
    "NyaaEntry",
    "anidb_match",
    "clean_release_title",
    "detail",
    "entry_to_dict",
    "is_nyaa_url",
    "parse_rss",
    "release_episode_info",
    "search",
    "split_filter_terms",
]
