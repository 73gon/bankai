"""Show-level Anime library presentation: collections of AniList entries.

Also the TVDB metadata cache and episode lists of the Movies & Shows library
(mas_library), which keeps TVDB; nothing on the anime side asks TVDB.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata.tvdb import TVDBClient, TVDBEpisode
from bankai.processor.naming import sanitise
from bankai.torrent.matcher import parse_se
from bankai.web import anime, anime_collections, discover, erai

log = get_logger(__name__)

_VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".webm"}
_CACHE: dict[str, tuple[float, dict]] = {}
_PERSISTENT_CACHE: dict | None = None
_PERSISTENT_DIRTY = False
_PERSISTENT_LOCK = threading.RLock()
_PERSISTENT_TTL_SECONDS = 24 * 60 * 60
# How long "TVDB has no such title" is believed. Without it, every title that
# does not match -- most films on the Movies & Shows page -- was searched for
# again on every restart, one provider round trip each.
_MISS_TTL_SECONDS = 12 * 60 * 60
_REFRESHING: set[str] = set()
_BACKGROUND: set[asyncio.Task] = set()


def _persistent_path() -> Path:
    return erai._state_path().with_name("anime_tvdb_cache.json")


def _persistent_data() -> dict:
    global _PERSISTENT_CACHE
    with _PERSISTENT_LOCK:
        if _PERSISTENT_CACHE is None:
            try:
                value = json.loads(_persistent_path().read_text(encoding="utf-8"))
                _PERSISTENT_CACHE = value if isinstance(value, dict) else {}
            except (OSError, ValueError, TypeError):
                _PERSISTENT_CACHE = {}
        return _PERSISTENT_CACHE


def _persistent_entry(key: str) -> tuple[Any, float] | None:
    """A saved value and its age in seconds, however old; ``None`` if never saved."""
    hit = _persistent_data().get(key)
    if not isinstance(hit, dict) or "value" not in hit:
        return None
    return hit["value"], time.time() - float(hit.get("saved_at", 0))


def _persistent_get(key: str):
    entry = _persistent_entry(key)
    return entry[0] if entry is not None and entry[1] < _PERSISTENT_TTL_SECONDS else None


def _refresh_later(key: str, fetch: Callable[[], Awaitable[Any]]) -> None:
    """Fetch an expired entry again in the background, once at a time.

    The page is answered with what was saved. Blocking it instead meant that
    once a day every card in a library waited on the provider, and after a
    restart the Movies & Shows page took over two minutes.
    """
    if key in _REFRESHING:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _REFRESHING.add(key)

    async def run() -> None:
        try:
            await fetch()
            await asyncio.to_thread(flush_persistent_cache)
        except Exception as exc:
            log.debug("Background metadata refresh of %s failed: %s", key, exc)
        finally:
            _REFRESHING.discard(key)

    task = loop.create_task(run())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def _persistent_put(key: str, value) -> None:
    global _PERSISTENT_DIRTY
    with _PERSISTENT_LOCK:
        _persistent_data()[key] = {"saved_at": time.time(), "value": value}
        _PERSISTENT_DIRTY = True


def flush_persistent_cache() -> None:
    global _PERSISTENT_DIRTY
    with _PERSISTENT_LOCK:
        if not _PERSISTENT_DIRTY:
            return
        path = _persistent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(_persistent_data(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        _PERSISTENT_DIRTY = False


def _name(value: str) -> str:
    """Identity of a show for grouping, however its name was written down.

    One side of a comparison is a folder name and the other is a TVDB title,
    and the folder has been through :func:`sanitise`, which drops characters
    Windows forbids. Comparing the two raw meant "Jaadugar: A Witch in
    Mongolia" never matched its own folder "Jaadugar A Witch in Mongolia", and
    the show was listed twice -- once with its episodes, once empty.

    Repeated trailing years collapse too, so a folder left behind by the
    double-year bug still groups with the series it belongs to.
    """
    cleaned = sanitise(value, fallback=value)
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"\s*[\[(]\d{4}[\])]\s*$", "", cleaned).strip()
    return _MULTISPACE.sub(" ", cleaned).strip().casefold()


_MULTISPACE = re.compile(r"\s+")


# " S3", " Season 3", " 3rd Season" -- how Erai distinguishes a season, and
# not part of the show's name as TVDB knows it.
_SEASON_SUFFIX = re.compile(
    r"\s+(?:S\d{1,2}|Season\s+\d{1,2}|\d{1,2}(?:st|nd|rd|th)\s+Season)$",
    re.IGNORECASE,
)


_PUNCTUATION = re.compile(r"[^0-9a-z]+")


def _same_name(left: str, right: str) -> bool:
    """One name, for the purpose of not printing it under itself.

    Compared with punctuation dropped as well as case: _name only removes
    what Windows forbids in a folder, so an exclamation mark survives it and
    "Akame ga Kill!" did not match "Akame ga Kill".
    """
    return _PUNCTUATION.sub("", _name(left)) == _PUNCTUATION.sub("", _name(right))


def _search_title(title: str) -> str:
    """The show's own name, without the year or the season it happens to be."""
    stripped = re.sub(r"\s*\(\d{4}\)$", "", title).strip()
    return _SEASON_SUFFIX.sub("", stripped).strip() or stripped


async def show_metadata(title: str, kind: str = "show") -> dict:
    """TVDB's film or series of this name, for the Movies & Shows library."""
    # The kind is part of the key: a film and a series can share a name, and
    # the movie library must not be handed the series' artwork.
    key = f"all:{kind}:{_name(title)}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    # Versioned: entries cached before the season suffix was understood hold an
    # empty result that would otherwise be served for another day.
    saved = _persistent_entry(f"metadata:v2:{key}")
    if saved is not None and isinstance(saved[0], dict):
        _CACHE[key] = (time.time(), saved[0])
        if saved[1] >= _PERSISTENT_TTL_SECONDS:
            _refresh_later(key, lambda: _fetch_metadata(key, title, kind))
        return saved[0]
    miss = _persistent_entry(f"miss:v1:{key}")
    if miss is not None and miss[1] < _MISS_TTL_SECONDS:
        _CACHE[key] = (time.time(), {})
        return {}
    return await _fetch_metadata(key, title, kind)


async def _fetch_metadata(key: str, title: str, kind: str) -> dict:
    """Ask the provider, and remember the answer -- but never an outage."""
    try:
        if not discover.is_configured():
            raise RuntimeError("TVDB is not configured")
        metadata = await _lookup_any_title(title, kind)
    except Exception:
        # Library browsing remains available during provider outages, on
        # whatever was saved before; a failure is held only in memory, and
        # briefly, so the next page load after the outage asks again.
        hit = _CACHE.get(key)
        if not (hit and hit[1]):
            _CACHE[key] = (time.time(), {})
        return hit[1] if hit and hit[1] else {}
    if not metadata:
        # A title that matched before and no longer does keeps its old match:
        # losing the cover to a provider's search ranking is worse than an
        # answer a day older.
        previous = _persistent_entry(f"metadata:v2:{key}")
        if previous is not None and isinstance(previous[0], dict) and previous[0]:
            metadata = previous[0]
    _CACHE[key] = (time.time(), metadata)
    if metadata:
        _persistent_put(f"metadata:v2:{key}", metadata)
    else:
        # The provider answered and has no such title. That is worth
        # remembering, for less long than a match.
        _persistent_put(f"miss:v1:{key}", True)
    return metadata


_FOLDER_YEAR = re.compile(r"\((\d{4})\)\s*$")


async def _lookup_any_title(title: str, kind: str) -> dict:
    """A film or series of any genre, for the Movies & Shows library.

    One TVDB search, the one the Discover page makes, which brings the cover
    with it. A folder's year tells remakes apart: "Passengers (2016)", not
    the 2008 film.
    """
    query = _search_title(title)
    results = await discover.search(query, kind=kind, limit=10)
    wanted = _name(query)
    exact = [item for item in results if item.kind == kind and _name(item.name) == wanted]
    year = _FOLDER_YEAR.search(title)
    if year and len(exact) != 1:
        # Several of that name, or none exactly ("RoboCop" for "Robocop"):
        # the year decides, among titles that at least contain the name.
        pool = exact or [item for item in results if item.kind == kind and wanted in _name(item.name)]
        dated = [item for item in pool if item.year == int(year[1])]
        exact = dated if len(dated) == 1 else exact
    if len(exact) != 1:
        return {}
    item = exact[0]
    return {
        "tvdb_id": item.tvdb_id,
        "english_title": item.name,
        "year": item.year,
        "poster_url": item.poster_url,
        "kind": kind,
        "status": item.status,
    }


async def _tvdb_series_episodes(tvdb_id: int) -> list[TVDBEpisode]:
    settings = get_settings().metadata
    if not settings.tvdb_enabled or not settings.tvdb_api_key:
        return []
    client = TVDBClient(api_key=settings.tvdb_api_key, pin=settings.tvdb_pin, languages=["eng", "jpn"])
    try:
        return await client.series_episodes(tvdb_id)
    finally:
        await client.aclose()


async def episode_roster(tvdb_id: int) -> list:
    """A TVDB series' episodes, for the Movies & Shows library."""
    key = f"episodes:{tvdb_id}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    saved = _persistent_entry(key)
    if saved is not None and isinstance(saved[0], list):
        rows = [TVDBEpisode(**row) for row in saved[0] if isinstance(row, dict)]
        _CACHE[key] = (time.time(), rows)
        if saved[1] >= _PERSISTENT_TTL_SECONDS:
            _refresh_later(key, lambda: _fetch_roster(tvdb_id))
        return rows
    return await _fetch_roster(tvdb_id)


async def _fetch_roster(tvdb_id: int) -> list:
    key = f"episodes:{tvdb_id}"
    rows = await _tvdb_series_episodes(tvdb_id)
    _CACHE[key] = (time.time(), rows)
    _persistent_put(key, [asdict(row) for row in rows])
    return rows


# Probing is cheap per file -- ffprobe reads headers, not the stream -- but the
# library holds thousands of episodes on a slow disk, so results are cached by
# identity and the sweep is bounded per pass.
_CODEC_CACHE_LOCK = threading.Lock()
_CODEC_CACHE: dict[str, dict] | None = None
_HEVC_NAMES = {"hevc", "h265", "x265"}
_AVC_NAMES = {"h264", "avc", "x264"}
_GERMAN_AUDIO = {"ger", "deu", "de", "german", "deutsch"}


def _codec_cache_path() -> Path:
    return erai._state_path().with_name("anime_codecs.json")


def _load_codec_cache() -> dict[str, dict]:
    global _CODEC_CACHE
    with _CODEC_CACHE_LOCK:
        if _CODEC_CACHE is None:
            try:
                _CODEC_CACHE = json.loads(_codec_cache_path().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                _CODEC_CACHE = {}
        return _CODEC_CACHE


def _save_codec_cache(cache: dict[str, dict]) -> None:
    path = _codec_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(path)


def probe_streams(path: Path) -> dict | None:
    """Video encode and audio languages of one file, in a single probe.

    The audio languages matter as much as the codec: an episode carrying a
    German dub is irreplaceable, because Erai-raws only ever ships Japanese
    audio. Reading both here means the upgrade never has to guess.
    """
    from bankai.web.media import ffprobe_bin

    binary = ffprobe_bin()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [
                binary, "-v", "error",
                "-show_entries", "stream=codec_type,codec_name:stream_tags=language,title",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except json.JSONDecodeError:
        return None
    codec = None
    audio: list[str] = []
    for stream in streams:
        kind = str(stream.get("codec_type") or "").casefold()
        tags = stream.get("tags") or {}
        if kind == "video" and codec is None:
            name = str(stream.get("codec_name") or "").casefold()
            codec = "hevc" if name in _HEVC_NAMES else "avc" if name in _AVC_NAMES else name or None
        elif kind == "audio":
            language = str(tags.get("language") or "").casefold()
            title = str(tags.get("title") or "").casefold()
            if language:
                audio.append(language)
            # Not every muxer sets the language tag; a named track still counts.
            if not language and title:
                audio.append(title)
    if codec is None and not audio:
        return None
    return {"codec": codec, "audio": audio}


def has_german_audio(entry: dict | None) -> bool:
    """Does this file carry a German audio track?"""
    for name in (entry or {}).get("audio") or []:
        words = set(re.findall(r"[a-z]+", str(name).casefold()))
        if words & _GERMAN_AUDIO:
            return True
    return False


def sweep_codecs(root: Path, *, limit: int) -> dict[str, int]:
    """Probe up to ``limit`` library files that have not been identified yet.

    Bounded so the sweep never competes for long with publishing, and cached by
    size and mtime so an episode is only ever probed once -- unless it is
    replaced, which is exactly when the answer changes.
    """
    if limit <= 0 or not root.exists():
        return {"probed": 0, "remaining": 0}
    cache = dict(_load_codec_cache())
    probed = 0
    remaining = 0
    from bankai.web import library_walk

    # The held tree rather than a walk of its own: finding out that nothing
    # is left to probe used to mean crossing the whole library over 9p.
    for row in library_walk.files([root]):
        if Path(row["name"]).suffix.casefold() not in _VIDEO_SUFFIXES:
            continue
        path = Path(row["path"])
        identity = (int(row["size"]), int(row["mtime"]))
        key = str(path)
        cached = cache.get(key)
        if cached and cached.get("size") == identity[0] and cached.get("mtime") == identity[1]:
            continue
        if probed >= limit:
            remaining += 1
            continue
        streams = probe_streams(path) or {}
        cache[key] = {
            "size": identity[0],
            "mtime": identity[1],
            "codec": streams.get("codec"),
            "audio": streams.get("audio") or [],
        }
        probed += 1
    if probed:
        with _CODEC_CACHE_LOCK:
            global _CODEC_CACHE
            _CODEC_CACHE = cache
        _save_codec_cache(cache)
    return {"probed": probed, "remaining": remaining}


def probed_codecs(files: list[dict]) -> dict[tuple[int, int], str]:
    """Codecs read from the files themselves, for episodes already probed."""
    cache = _load_codec_cache()
    found: dict[tuple[int, int], str] = {}
    for row in files:
        season, episode = row.get("season_number"), row.get("episode")
        if season is None or episode is None:
            continue
        entry = cache.get(str(row.get("path") or ""))
        codec = (entry or {}).get("codec")
        if codec:
            found[(season, episode)] = codec
    return found


def german_dubbed_episodes(files: list[dict]) -> set[tuple[int, int]]:
    """Episodes whose file carries a German dub, which must never be replaced."""
    cache = _load_codec_cache()
    found: set[tuple[int, int]] = set()
    for row in files:
        season, episode = row.get("season_number"), row.get("episode")
        if season is None or episode is None:
            continue
        if has_german_audio(cache.get(str(row.get("path") or ""))):
            found.add((season, episode))
    return found


def merge_episodes(
    files: list[dict],
    roster: list,
    *,
    ended: bool,
    codecs: dict[tuple[int, int], str] | None = None,
    german_dubbed: set[tuple[int, int]] | None = None,
) -> dict:
    """Count unique regular episodes, using final files as downloaded evidence."""
    by_number = {}
    others = []
    for row in files:
        key = (row["season_number"], row["episode"])
        if None in key:
            others.append(row)
        elif key not in by_number or by_number[key]["staged"]:
            by_number[key] = row
    today = date.today().isoformat()
    for item in roster:
        key = (item.season, item.episode)
        if item.season < 1 or item.episode < 1:
            continue
        future = bool(item.aired and item.aired[:10] > today)
        tba = future or (not item.aired and not ended)
        if key in by_number:
            by_number[key] = {
                **by_number[key],
                "episode_title": item.name,
                "aired": item.aired,
                "tba": False,
                "missing": False,
            }
        else:
            by_number[key] = {
                "path": "",
                "rel_path": "",
                "name": item.name or "TBA",
                "episode_title": item.name,
                "series": "",
                "season": f"Season {item.season:02d}",
                "season_number": item.season,
                "episode": item.episode,
                "size": 0,
                "mtime": 0,
                "staged": False,
                "stage": "missing",
                "transfer_status": "idle",
                "aired": item.aired,
                "tba": tba,
                "missing": True,
            }
    for key, row in by_number.items():
        row["codec"] = (codecs or {}).get(key) if not row.get("missing") else None
        row["german_dub"] = bool(german_dubbed and key in german_dubbed)
    regular = [row for (season, _), row in by_number.items() if season > 0]
    downloaded = sum(not row.get("missing", False) and not row["staged"] for row in regular)
    outstanding = [row for row in regular if row.get("missing", False) or row["staged"]]
    future_only = outstanding and all(row.get("tba", False) for row in outstanding)
    state = (
        "empty"
        if downloaded == 0
        else "upcoming"
        if future_only
        else "partial"
        if outstanding
        else "complete"
    )
    # Do not imply full TVDB completeness when metadata is unavailable.
    if not roster and downloaded:
        state = "unknown"
    return {
        "episodes": sorted(
            [*by_number.values(), *others],
            key=lambda row: (row["season_number"] or 0, row["episode"] or 0, row["name"]),
        ),
        "downloaded_count": downloaded,
        "total_count": len(regular),
        "completion_state": state,
        "finished": ended and state == "complete",
        "metadata_available": bool(roster),
    }




async def search_episode(anilist_id: int, episode: int, query: str | None = None) -> dict:
    """Releases of one AniList episode: searched by the entry's names, each result checked.

    The names are AniList's and the ones Erai has published this entry under,
    each searched as "Name" "- 05". Every result is identified as the
    automation would and kept only if it is this entry's episode.
    """
    import httpx

    from bankai.metadata import anilist

    table = await anilist.index()
    entry = table.anime.get(int(anilist_id)) if table is not None else None
    if entry is None:
        raise ValueError("This AniList entry is not in the title index")
    state = await asyncio.to_thread(erai._load_state)
    mappings = await asyncio.to_thread(erai._load_mappings)
    terms = [query] if query else []
    if not terms:
        names = [entry.title, entry.english]
        prefix = f"anilist:{entry.id}|"
        for release in (state.get("releases") or {}).values():
            if str(release.get("canonical") or "").startswith(prefix):
                parsed = erai._erai_name_episode(str(release.get("title") or ""))
                if parsed:
                    names.append(parsed[0])
        terms = [f'"{name}" "- {episode:02d}"' for name in dict.fromkeys(n for n in names if n)]
    terms = list(dict.fromkeys(terms))[:6]
    indexed = [
        erai._entry_from_dict(row)
        for index in [state["backfill"], *state["series_catalogs"].values()]
        for field in ["catalog_1080", "catalog_720"]
        for row in index.get(field, {}).values()
    ]
    # Relative to Nyaa: _fetch_rss asks for "/".
    async with httpx.AsyncClient(
        base_url="https://nyaa.si",
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": get_settings().scraper.user_agent},
    ) as client:
        batches = await asyncio.gather(
            *(anime._fetch_rss(client, term, "1_2", 0) for term in terms), return_exceptions=True
        )
    by_hash = {row.info_hash: row for row in indexed}
    for batch in batches:
        if isinstance(batch, list):
            by_hash.update({row.info_hash: row for row in batch})
    items = []
    for release in by_hash.values():
        match = anime.anilist_match(release.title, table=table, mappings=mappings)
        if not match or match["anilist_id"] != entry.id or match["episode"] != episode:
            continue
        items.append(anime.entry_to_dict(replace(release, anilist=match, episode=episode)))
    return {
        "items": sorted(
            items, key=lambda row: (-erai._resolution(erai._entry_from_dict(row)), -row["seeders"])
        ),
        "queries": terms,
        "anime": {**entry.as_dict(), "episode": episode},
    }


def _display_title(titles: list[str], english: str = "") -> str:
    """One name for a show that is on disk under more than one.

    Deterministic, because it becomes the card's key and the page asks for a
    single show back by it. The longest folder name wins: it is the one
    carrying the year or the fuller punctuation.
    """
    if english:
        return english
    if not titles:
        return ""
    return sorted(titles, key=lambda value: (-len(value), value))[0]


# -- The anime library: collections of AniList entries ---------------------------
#
# A card is a collection (see anime_collections): one show, however many
# AniList entries -- seasons, cours, movies -- it holds, kept in one folder.
# Which entry and episode each file is comes from the file index
# (anime_files), written when the file was published, moved into place or
# matched by hand; never guessed from a folder's name. A file the index does
# not know sits on an "Unmatched" card of its folder until it is matched on
# AniList. Each entry is one tab, its episodes AniList's.


@dataclass(frozen=True)
class _Episode:
    """One episode as merge_episodes reads it; TVDB's episodes have the same shape."""

    season: int
    episode: int
    name: str | None = None
    aired: str | None = None
    absolute_number: int | None = None


@dataclass(frozen=True)
class EntryEpisode:
    number: int
    title: str | None = None
    aired: str | None = None


def roster(aid: int) -> list[EntryEpisode] | None:
    """An entry's episodes as AniList lists them; None when it does not say how many.

    AniList's air dates reach back only so far. An episode of a finished
    entry without one has aired all the same: it is dated by the entry's start.
    """
    from bankai.metadata import anilist

    rows = anilist.episodes(aid)
    if not rows:
        return None
    started = anilist.media(aid).get("start") if anilist.finished(aid) else None
    return [EntryEpisode(n, title, aired or started) for n, title, aired in rows]


def merge_entry_episodes(
    rows: list[dict],
    entries: list[tuple[int, str, list | None, bool]],
    *,
    codecs: dict[tuple[int, int], str] | None = None,
    german_dubbed: set[tuple[int, int]] | None = None,
    optional: set[int] | None = None,
) -> dict:
    """Episodes of a card made of AniList entries, one tab per entry.

    ``rows`` are the card's files, ``season_number`` the tab of their entry
    and ``episode`` its own number. ``entries`` are (AniList id, tab label,
    episode list or None when unknown, finished), in tab order. The missing
    episodes of ``optional`` entries -- the specials -- are listed but not
    counted: a show is complete without its recap movie.
    """
    today = date.today().isoformat()

    def ended(listed: list | None, finished: bool) -> bool:
        return finished or (bool(listed) and all(ep.aired and ep.aired[:10] <= today for ep in listed))

    items = [
        _Episode(season=index, episode=ep.number, name=ep.title, aired=ep.aired)
        for index, (_aid, _label, listed, _finished) in enumerate(entries, start=1)
        for ep in listed or []
    ]
    merged = merge_episodes(
        rows,
        items,
        ended=all(ended(listed, finished) for _aid, _label, listed, finished in entries),
        codecs=codecs,
        german_dubbed=german_dubbed,
    )
    labels = {index: label for index, (_aid, label, _listed, _finished) in enumerate(entries, start=1)}
    for row in merged["episodes"]:
        if row.get("season_number") in labels:
            row["season"] = labels[row["season_number"]]
    merged["metadata_available"] = any(listed for _aid, _label, listed, _finished in entries)
    skipped = {index for index, (aid, *_rest) in enumerate(entries, start=1) if aid in (optional or set())}
    if skipped:
        counted = [
            row for row in merged["episodes"]
            if row.get("season_number") is not None
            and not (row.get("missing") and row["season_number"] in skipped)
        ]
        downloaded = sum(not row.get("missing", False) and not row["staged"] for row in counted)
        outstanding = [row for row in counted if row.get("missing", False) or row["staged"]]
        state = (
            "empty" if downloaded == 0
            else "upcoming" if outstanding and all(row.get("tba", False) for row in outstanding)
            else "partial" if outstanding
            else "complete"
        )
        if merged["completion_state"] == "unknown" and downloaded:
            state = "unknown"
        merged.update(
            downloaded_count=downloaded,
            total_count=len(counted),
            completion_state=state,
            finished=state == "complete"
            and all(ended(listed, done) for aid, _label, listed, done in entries if aid not in (optional or set())),
        )
    return merged


_KINDS = {"TV": "TV", "MOVIE": "Movie", "OVA": "OVA", "ONA": "ONA", "SPECIAL": "Special", "MUSIC": "Music"}


def tab_labels(order: list[int], collection: str | None) -> dict[int, str]:
    """Each entry's tab name, without the collection's own: "Attack on Titan Season 2" -> "Season 2".

    Two tabs of one name -- a show and its OVA both "Attack on Titan" -- are
    told apart by what they are and when.
    """
    from collections import Counter

    from bankai.metadata import anilist

    labels = {
        aid: anime_collections.short_label(anime_collections.entry_name(aid), collection) for aid in order
    }
    counts = Counter(labels.values())
    for aid, label in labels.items():
        if counts[label] > 1:
            extra = ", ".join(str(part) for part in (_KINDS.get(anilist.kind(aid) or ""), anilist.year(aid)) if part)
            labels[aid] = f"{label} ({extra or aid})"
    return labels


def _unique(key: str, taken: set[str]) -> str:
    """A card key no other card has: the page asks for a card back by it."""
    candidate, number = key, 2
    while _name(candidate) in taken:
        candidate = f"{key} ({number})"
        number += 1
    taken.add(_name(candidate))
    return candidate


async def group_shows(
    entries: list[dict],
    root: Path,
    *,
    include_episodes: bool = True,
    only_key: str | None = None,
) -> list[dict]:
    """One card per collection, and one per folder of files no AniList entry is known for.

    A collection with nothing on disk still has a card while the automation
    follows one of its entries, unless that entry was blacklisted, which is
    how one removed from the library leaves it.
    """
    from bankai.metadata import anilist
    from bankai.web import anime_files, anime_layout

    index = await asyncio.to_thread(anime_files.load)
    store = await asyncio.to_thread(anime_collections.load)
    # One read of the release state for the whole page: it is a large file.
    state = await asyncio.to_thread(erai._load_state)
    blocked = erai._policy_anilist_ids(await asyncio.to_thread(erai._load_policies))
    folder_of = anime_layout.folder_names(store)
    of = store.of()

    slots: dict[str, dict] = {}

    def slot_for(aid: int) -> dict:
        cid = of.get(aid)
        identity = f"collection:{cid}" if cid is not None else f"entry:{aid}"
        return slots.setdefault(
            identity, {"cid": cid, "aids": [] if cid is not None else [aid], "files": [], "folders": set()}
        )

    for entry in entries:
        rel = anime_files.key(entry["path"])
        pairs = index.get(rel) if rel else None
        if pairs:
            aid, number = pairs[0]
            slot = slot_for(aid)
            slot["files"].append({**entry, "anilist_id": aid, "anilist_episode": number})
        else:
            slot = slots.setdefault(
                f"unmatched:{entry['series']}",
                {"cid": None, "aids": [], "files": [], "folders": set(), "unmatched": True},
            )
            season, episode = parse_se(entry["name"]) or (None, None)
            slot["files"].append({**entry, "season_number": season, "episode": episode})
        slot["folders"].add(entry["series"])
    for raw in state.get("anime") or {}:
        if str(raw).isdigit() and int(raw) not in blocked:
            slot_for(int(raw))

    taken: set[str] = set()
    for identity in sorted(slots, key=lambda value: (not value.startswith("collection:"), value)):
        slot = slots[identity]
        if slot["cid"] is not None:
            row = store.collections[slot["cid"]]
            slot["title"] = anime_collections.name(row)
            slot["key"] = _unique(folder_of.get(slot["cid"]) or slot["title"], taken)
        elif slot.get("unmatched"):
            folder = identity.split(":", 1)[1]
            slot["title"] = folder
            # Its folder's name -- unless a collection's folder is called so,
            # holding a few files the index does not know beside its own.
            slot["key"] = _unique(folder if _name(folder) not in taken else f"{folder} (unmatched)", taken)
        else:
            slot["title"] = anime_collections.entry_name(slot["aids"][0])
            slot["key"] = _unique(slot["title"], taken)

    if only_key is not None:
        wanted = _name(only_key)
        slots = {identity: slot for identity, slot in slots.items() if _name(slot["key"]) == wanted}

    def counts(rows: list[dict]) -> dict:
        episodes = rows
        return {
            "avc_count": sum(
                1 for row in episodes
                # A German dub is irreplaceable, so it is not on offer.
                if row.get("codec") == "avc" and not row.get("german_dub")
            ),
            "hevc_count": sum(
                1 for row in episodes if row.get("codec") == "hevc" and not row.get("german_dub")
            ),
            # The top tier, whatever its codec: each episode is counted in one
            # of the three, so together they never exceed the episodes on disk.
            "german_dub_count": sum(1 for row in episodes if row.get("german_dub")),
        }

    def build(slot: dict) -> dict:
        files = slot["files"]
        base = {
            "key": slot["key"],
            "folders": sorted(slot["folders"]),
            "title": slot["title"],
            "episode_count": len(files),
            "size": sum(row["size"] for row in files),
            "staged_count": sum(row["staged"] for row in files),
        }
        if slot.get("unmatched"):
            merged = merge_episodes(
                files, [], ended=False, codecs=probed_codecs(files), german_dubbed=german_dubbed_episodes(files)
            )
            result = {
                **base,
                "source_title": "",
                "unmatched": True,
                "anilist_id": None,
                "anilist_ids": [],
                "collection_id": None,
                "collection_name_custom": False,
                "collection_entries": [],
                "numbering": "files",
                "year": None,
                "poster_url": None,
                "season_count": len({row["season_number"] for row in merged["episodes"] if row["season_number"] is not None}),
                **counts(merged["episodes"]),
                **merged,
                "season_anilist": {},
            }
        else:
            row = store.collections.get(slot["cid"]) if slot["cid"] is not None else None
            on_disk: dict[int, int] = {}
            for file in files:
                on_disk[file["anilist_id"]] = max(on_disk.get(file["anilist_id"], 0), file["anilist_episode"])
            if row is not None:
                placing = anime_collections.plan(row, episodes_on_disk=lambda aid: on_disk.get(aid, 0), store=store)
                order = placing.order()
                main = row.title if row.title in order else order[0]
                seasons = {
                    aid: (f"Season {number}" if number else None) for aid, number in placing.seasons.items()
                }
                seasons.update({aid: "Specials" for aid in placing.specials})
            else:
                order = list(slot["aids"])
                main = order[0]
                seasons = {}
            labels = tab_labels(order, slot["title"] if row is not None else None)
            place = {aid: number for number, aid in enumerate(order, start=1)}
            rows = []
            for file in files:
                aid = file["anilist_id"]
                if aid not in place:
                    rows.append({**file, "season_number": None, "episode": None})
                    continue
                rows.append({**file, "season_number": place[aid], "season": labels[aid], "episode": file["anilist_episode"]})
            merged = merge_entry_episodes(
                rows,
                [(aid, labels[aid], roster(aid), anilist.finished(aid)) for aid in order],
                codecs=probed_codecs(rows),
                german_dubbed=german_dubbed_episodes(rows),
                optional=set(placing.specials) if row is not None else None,
            )
            romaji = anilist.media(main).get("romaji")
            result = {
                **base,
                "source_title": romaji if romaji and not _same_name(romaji, slot["title"]) else "",
                "unmatched": False,
                "anilist_id": main,
                "anilist_ids": order,
                "collection_id": slot["cid"],
                "collection_name_custom": bool(row is not None and row.name),
                "collection_entries": [
                    {
                        "anilist_id": aid,
                        "title": anime_collections.entry_name(aid),
                        "owned": aid in on_disk,
                        "season": seasons.get(aid),
                    }
                    for aid in order
                ],
                "numbering": "anilist",
                "year": anilist.year(main),
                "poster_url": anilist.cover(main),
                "season_count": len({r["season_number"] for r in merged["episodes"] if r["season_number"] is not None}),
                **counts(merged["episodes"]),
                **merged,
                "season_anilist": {
                    str(number): [{"anilist_id": aid, "title": anime_collections.entry_name(aid)}]
                    for aid, number in place.items()
                },
            }
        if not include_episodes:
            result["episodes"] = []
        return result

    shows = await asyncio.to_thread(lambda: [build(slot) for slot in slots.values()])
    return sorted(shows, key=lambda row: row["title"].casefold())


async def queue_covers(rows: list[dict]) -> list[dict]:
    """Each queue row's cover and name, by the AniList entry its job files under."""
    from bankai.metadata import anilist

    for row in rows:
        raw = str(row.get("anilist_id") or "")
        if not raw.isdigit():
            continue
        aid = int(raw)
        row["poster_url"] = anilist.cover(aid)
        if anilist.name(aid):
            row["series_title"] = anilist.name(aid)
    return rows


async def enrich_review_rows(rows: list[dict]) -> list[dict]:
    """Each review or blacklist card's name, year and cover, from its AniList entry.

    A card that is one AniList entry shows that entry's name and cover. One
    that no entry answers for yet is shown by its Erai name until one is
    chosen for it.
    """
    from bankai.metadata import anilist

    for row in rows:
        row.pop("tvdb_id", None)
        if not str(row.get("anilist_id") or "").isdigit():
            row["title"] = row["source_title"]
            row["poster_url"] = row.get("anilist_poster_url")
            continue
        aid = int(row["anilist_id"])
        row["title"] = anilist.name(aid) or row.get("english_title") or row.get("anilist_title") or row["source_title"]
        row["year"] = anilist.year(aid)
        row["poster_url"] = anilist.cover(aid) or row.get("anilist_poster_url")
    return rows


# -- Matching by hand ------------------------------------------------------------


def match_files(paths: list[Path], anilist_id: int) -> dict:
    """Files no entry was known for become one entry's episodes, by the number in each name.

    Returns the files matched and the ones whose name carries no episode number.
    """
    from bankai.processor.anime import episode_number
    from bankai.web import anime_files

    matched, skipped = [], []
    with anime_files.editing() as rows:
        for path in paths:
            rel = anime_files.key(path)
            numbered = parse_se(path.name)
            number = numbered[1] if numbered else episode_number(path.name)
            if rel is None or not number:
                skipped.append(str(path))
                continue
            rows[rel] = [(int(anilist_id), int(number))]
            matched.append(str(path))
    return {"matched": matched, "skipped": skipped}


def rematch_entry(from_id: int, to_id: int) -> int:
    """Every file of one entry becomes another's, episode for episode. Returns how many."""
    from bankai.web import anime_files

    moved = 0
    with anime_files.editing() as rows:
        for rel, pairs in rows.items():
            if any(aid == int(from_id) for aid, _episode in pairs):
                rows[rel] = [(int(to_id) if aid == int(from_id) else aid, episode) for aid, episode in pairs]
                moved += 1
    return moved
