"""Nyaa-only anime catalogue, each release identified on AniList.

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
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlparse
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
    # The AniList entry and episode it is, when that can be told offline:
    # {anilist_id, title (romaji), name, episode, cover, format, year}.
    anilist: dict | None = None


@dataclass(frozen=True, slots=True)
class AnimeSearchPage:
    items: list[NyaaEntry]
    page: int
    has_next: bool
    aliases: list[str]
    # The requests asked of Nyaa for it, shown under the filters.
    sent: tuple[str, ...] = ()


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


def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


def publisher_matches(publisher: str, entry: NyaaEntry) -> bool:
    """Is the release by this group: its [tag] or its uploader, without spaces or punctuation
    ("PHT Mini" is "[PHTMini]")?"""
    wanted = _compact(publisher)
    return bool(wanted) and any(wanted in _compact(found) for found in (entry.publisher or "", _publisher(entry.title) or ""))


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
        magnet = magnet_uri(info_hash, title)
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


def parse_listing(page_html: str) -> list[NyaaEntry]:
    """Nyaa's web listing as entries, as parse_rss reads the feed: the feed ignores the page."""
    from email.utils import formatdate

    entries: list[NyaaEntry] = []
    for row in HTMLParser(page_html).css("table.torrent-list tbody tr"):
        cells = row.css("td")
        if len(cells) < 8:
            continue
        name = next((link for link in cells[1].css('a[href^="/view/"]') if "comments" not in (link.attributes.get("class") or "")), None)
        magnet_link = cells[2].css_first('a[href^="magnet:"]')
        download = cells[2].css_first('a[href$=".torrent"]')
        found = re.search(r"/view/(\d+)", (name.attributes.get("href") or "") if name else "")
        magnet = html.unescape(magnet_link.attributes.get("href") or "") if magnet_link else ""
        info_hash = re.search(r"btih:([0-9a-fA-F]{40})", magnet)
        if not (name and found and info_hash):
            continue
        title = html.unescape(name.attributes.get("title") or name.text(strip=True))
        category = cells[0].css_first("a")
        size = cells[3].text(strip=True)
        stamp = cells[4].attributes.get("data-timestamp")
        season, episode = release_episode_info(title)
        classes = row.attributes.get("class") or ""

        def number(cell) -> int:
            text = cell.text(strip=True)
            return int(text) if text.isdigit() else 0

        entries.append(
            NyaaEntry(
                id=int(found.group(1)),
                title=title,
                download_url=urljoin(_NYAA_BASE, download.attributes.get("href") or "") if download else "",
                detail_url=urljoin(_NYAA_BASE, f"/view/{found.group(1)}"),
                magnet_uri=magnet,
                info_hash=info_hash.group(1).lower(),
                category_id=((category.attributes.get("href") or "").rsplit("=", 1)[-1]) if category else "",
                category=(category.attributes.get("title") or "") if category else "",
                size=size,
                size_bytes=_size_bytes(size),
                seeders=number(cells[5]),
                leechers=number(cells[6]),
                downloads=number(cells[7]),
                comments=0,
                trusted="success" in classes,
                remake="danger" in classes,
                published_at=formatdate(int(stamp), usegmt=True) if stamp and stamp.isdigit() else None,
                publisher=_publisher(title),
                quality=_quality(title),
                season=season,
                episode=episode,
                description="",
            )
        )
    return entries


def _feed_params(query: str, category: str, page: int, user: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"page": "rss", "q": query, "c": category, "f": "0", "p": page + 1, "s": "seeders", "o": "desc"}
    if user:
        params["u"] = user  # one uploader's own torrents
    if page > 0:
        # The feed ignores the page (and the sort): page 2 was page 1 again. The web listing,
        # newest first like the feed, goes on where the first page ended.
        params = {**params, "s": "id"}
        params.pop("page")
    return params


def feed_url(query: str, category: str, page: int, user: str | None = None) -> str:
    """The address one search asks Nyaa for, as Discover shows it."""
    return f"{_NYAA_BASE}/?{urlencode(_feed_params(query, category, page, user))}"


async def _fetch_rss(
    client: httpx.AsyncClient, query: str, category: str, page: int, *, user: str | None = None
) -> list[NyaaEntry]:
    response = await client.get("/", params=_feed_params(query, category, page, user))
    response.raise_for_status()
    return parse_listing(response.text) if page > 0 else parse_rss(response.text)


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


def season_pack_name(title: str) -> str | None:
    """A scene-style season pack's show and season: "My Hero Academia S02 1080p NF WEB-DL ...
    (Boku no Hero Academia 4, ...)" -> "My Hero Academia Season 2".

    The cleaner keeps such a name whole, and its bracket can name another season
    altogether: picked by hand from that, VARYG's season 2 was filed as season 4 (Oct 2026).
    """
    value = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", title).strip()
    match = re.match(r"^(?P<name>.+?)[\s._]+S(?P<season>\d{1,2})(?=[\s._]|$)", value, re.I)
    if not match:
        return None
    name = re.sub(r"[._]+", " ", match["name"]).strip(" -")
    return f"{name} Season {int(match['season'])}" if name else None


def anilist_match(title: str, *, table: Any, mappings: dict[str, Any]) -> dict | None:
    """The AniList entry and episode a release is, from the title index; no network.

    The user's choice for its name first, as the automation does; else the
    name as AniList has it, its episode settled into the right part of a
    split season. None when no single entry answers for the name.
    """
    from bankai.metadata import anilist
    from bankai.web import erai

    if table is None:
        return None
    # A batch's name too ("2nd Season - 01 ~ 13"): read by the cleaner alone it lost its season,
    # and Re:Zero's second season was taken for the first and replaced it (Nov 2026).
    parsed = erai._erai_identity_name(title)
    if parsed:
        name, number = parsed
    else:
        name = clean_release_title(title)
        season, number = release_episode_info(title)
        if season and season > 1 and not erai._SHOW_SEASON_SUFFIX.search(name):
            name = f"{name} Season {season}"
    saved = mappings.get(erai._mapping_key(title)) or {}
    if str(saved.get("anilist_id") or "").isdigit():
        entry = table.anime.get(int(saved["anilist_id"]))
        if number is not None:
            number += int(saved.get("anilist_episode_offset") or 0)
    else:
        entry = anilist.resolve_in(table, name).anime
        if entry is None:
            pack = season_pack_name(title)
            entry = anilist.resolve_in(table, pack).anime if pack else None
        if entry is not None and number is not None:
            entry, number = anilist.settle_in(entry, number)
    if entry is None:
        return None
    return {
        "anilist_id": entry.id,
        "title": entry.title,
        # How it is shown: English, else romaji.
        "name": entry.name,
        "episode": number,
        "cover": anilist.cover(entry.id),
        # TV, MOVIE, OVA...: the download dialog files a MOVIE as a movie.
        "format": anilist.kind(entry.id),
        "year": anilist.year(entry.id),
    }


_SUBTITLE_EXTS = {".ass", ".ssa", ".srt", ".sub", ".vtt"}
# What a release can bring the episodes the library has: German, preferred above all.
GAIN_LANGUAGES = ("ger",)


def _episode_languages(paths: list[Path], listed: dict[Path, set[str]], inventory: dict[str, dict]) -> tuple[set[str], set[str]] | None:
    """An episode's audio and subtitle languages over its library copies; None when none was read yet.

    Its tracks as the Tracks page read them, and the subtitle files beside it
    ("<stem>.de.ass": the German Erai-raws uploads later).
    """
    from bankai.web import track_cleanup

    audio: set[str] = set()
    subtitles: set[str] = set()
    read = False
    for path in paths:
        entry = inventory.get(str(path))
        if entry:
            read = True
            for track in entry.get("tracks") or []:
                code = track_cleanup.language(track.get("lang"))
                if code == "und":
                    continue
                if track.get("kind") == "audio":
                    audio.add(code)
                elif track.get("kind") == "subtitles":
                    subtitles.add(code)
        for name in listed.get(path.parent, ()):
            if not name.startswith(path.stem + ".") or Path(name).suffix.casefold() not in _SUBTITLE_EXTS:
                continue
            # "<stem>.de.ass", "<stem>.de.forced.ass": the first part that is a language.
            codes = [track_cleanup.language(part) for part in name[len(path.stem) + 1 :].split(".")[:-1]]
            code = next((code for code in codes if code != "und"), None)
            if code:
                subtitles.add(code)
    return (audio, subtitles) if read else None


def _counts(sets: list[set[str]]) -> dict[str, int]:
    counted = Counter(code for found in sets for code in found)
    return dict(sorted(counted.items(), key=lambda item: (-item[1], item[0])))


def mark_owned(rows: list[dict]) -> list[dict]:
    """Each matched row's entry with what the library has of it.

    ``owned`` its episodes in the library, ``episodes`` how many it has,
    ``languages`` the audio and subtitle languages of the episodes the row is
    about (its one episode, or a batch's entry's) with the number of episodes
    having each, and ``gains`` what the release would bring them: German audio
    or subtitles they lack, told from the languages a nekobt row lists.
    """
    from bankai.metadata import anilist
    from bankai.web import anime_files, track_cleanup

    matches = [(row, row["anilist"]) for row in rows if row.get("anilist")]
    found, listed = anime_files.library_episodes(match["anilist_id"] for _row, match in matches)
    inventory = track_cleanup.load_inventory()
    languages: dict[int, dict[int, tuple[set[str], set[str]] | None]] = {
        aid: {episode: _episode_languages(paths, listed, inventory) for episode, paths in episodes.items()}
        for aid, episodes in found.items()
    }
    for row, match in matches:
        aid = match["anilist_id"]
        have = languages.get(aid, {})
        match["owned"] = sorted(have)
        match["episodes"] = anilist.episode_count(aid)
        episode = match.get("episode")
        about = list(have) if episode is None else [episode] if episode in have else []
        read = [found for number in about if (found := have[number]) is not None]
        match["languages"] = {
            "read": len(read),
            "audio": _counts([audio for audio, _subtitles in read]),
            "subtitles": _counts([subtitles for _audio, subtitles in read]),
        }
        offered = {
            "audio": {track_cleanup.language(code) for code in row.get("audio_langs") or []},
            "subtitles": {
                track_cleanup.language(code) for code in [*(row.get("sub_langs") or []), *(row.get("fansub_langs") or [])]
            },
        }
        gains = []
        for code in GAIN_LANGUAGES:
            for kind, index in (("audio", 0), ("subtitles", 1)):
                lacking = sum(1 for pair in read if code not in pair[index])
                if code in offered[kind] and lacking:
                    gains.append({"kind": kind, "language": code, "episodes": lacking, "of": len(read)})
        match["gains"] = gains
    return rows


async def _enrich_anilist(entries: list[NyaaEntry]) -> list[NyaaEntry]:
    from bankai.metadata import anilist
    from bankai.web import erai

    table = await anilist.index()
    mappings = await asyncio.to_thread(erai._load_mappings)
    return [
        replace(entry, anilist=anilist_match(entry.title, table=table, mappings=mappings))
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
    if publisher and not publisher_matches(publisher, entry):
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
        from bankai.metadata import anilist

        # The anime's other names on AniList widen the search: Erai publishes
        # under the romaji title, people type the English one.
        table = await anilist.index()
        found = anilist.resolve_in(table, query.strip()).anime if table is not None else None
        for value in (found.title, found.english) if found is not None else ():
            if value and value.casefold() not in {item.casefold() for item in aliases}:
                aliases.append(value)
    queries = aliases or [""]
    feeds = [(term, None) for term in queries]
    if publisher and publisher.strip():
        # Asked of Nyaa, not only filtered from what came back: the newest uploads hold a small
        # group's releases rarely (PHT Mini). Its name with the title, as typed and without spaces
        # (the tag is "[PHTMini]"), and the uploader's own torrents when it is an account.
        group = publisher.strip()
        names = list(dict.fromkeys([group, re.sub(r"\s+", "", group)]))
        feeds = [(f"{name} {term}".strip(), None) for name in names for term in queries]
        feeds += [(term, group) for term in queries]
    headers = {"User-Agent": get_settings().scraper.user_agent}
    async with httpx.AsyncClient(
        base_url=_NYAA_BASE, headers=headers, timeout=30, follow_redirects=True
    ) as client:
        fetched = await asyncio.gather(
            *(_fetch_rss(client, term, category, page, user=user) for term, user in feeds),
            return_exceptions=True,
        )
        batches = [batch for batch in fetched if isinstance(batch, list)]
        if not batches:
            failure = next((item for item in fetched if isinstance(item, Exception)), None)
            raise RuntimeError(f"Nyaa did not return a usable feed: {failure}")
        by_hash: dict[str, NyaaEntry] = {}
        for (_term, user), batch in zip(feeds, fetched, strict=True):
            if not isinstance(batch, list):
                continue
            for entry in batch:
                # From the uploader's own feed: its release, whatever the tag says.
                by_hash.setdefault(entry.info_hash, replace(entry, publisher=user) if user else entry)
        items = list(by_hash.values())
        description_terms = split_filter_terms(description_filters)
        # Only for a description filter: the group is in the release's [tag] or found by its feed.
        if description_terms:
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
    # Every row is identified: AniList's title index is on disk, so it costs
    # no requests. A row it cannot place is still downloadable once an
    # AniList entry is picked for it.
    items = await _enrich_anilist(items)
    has_next = any(len(batch) >= _RSS_PAGE_SIZE for batch in batches)
    sent = tuple(feed_url(term, category, page, user) for term, user in feeds)
    return AnimeSearchPage(items=items, page=page, has_next=has_next, aliases=aliases, sent=sent)


def entry_to_dict(entry: NyaaEntry) -> dict:
    return {**asdict(entry), "source": "nyaa"}


__all__ = [
    "AnimeSearchPage",
    "NyaaEntry",
    "anilist_match",
    "clean_release_title",
    "detail",
    "entry_to_dict",
    "is_nyaa_url",
    "parse_rss",
    "release_episode_info",
    "search",
    "split_filter_terms",
]


def magnet_uri(info_hash: str, title: str) -> str:
    """A Nyaa torrent's magnet link: its hash, its name, Nyaa's trackers."""
    return (
        f"magnet:?xt=urn:btih:{info_hash}&dn={quote(title)}"
        "&tr=http%3A%2F%2Fnyaa.tracker.wf%3A7777%2Fannounce"
        "&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce"
        "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
    )
