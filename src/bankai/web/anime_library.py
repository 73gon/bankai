"""Show-level Anime library presentation, by AniDB entry.

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
from contextlib import suppress
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


def _prefs_path() -> Path:
    return erai._state_path().with_name("anime_library_prefs.json")


def load_prefs() -> dict[str, dict]:
    try:
        value = json.loads(_prefs_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def folder_links(prefs: dict[str, dict]) -> dict[str, int]:
    """The AniDB entry the user linked each library folder to, by folder name."""
    return {
        name.split(":", 1)[1]: int(row["anidb_id"])
        for name, row in prefs.items()
        if name.startswith("folder:") and str((row or {}).get("anidb_id") or "").isdigit()
    }


def _write_prefs(prefs: dict[str, dict]) -> None:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_folder_link(folders: list[str], anidb_id: int) -> None:
    """Link library folders to one AniDB entry.

    For the files Shoko has not matched: those it has keep Shoko's answer,
    which comes from the file itself. The rest were matched by the folder's
    name alone, which can miss or pick the wrong entry.
    """
    prefs = load_prefs()
    for folder in folders:
        if folder.strip():
            prefs[f"folder:{folder}"] = {"anidb_id": int(anidb_id)}
    _write_prefs(prefs)


def merge_episodes_absolute(
    files: list[dict],
    roster: list,
    *,
    ended: bool,
    flat: bool = False,
    codecs: dict[tuple[int, int], str] | None = None,
    german_dubbed: set[tuple[int, int]] | None = None,
) -> dict:
    """merge_episodes for a show whose file numbers count through the whole show.

    Naruto, Bleach, One Piece: kept in season folders that are the user's own
    arcs, while each file's number is the absolute one -- "Season 02/Naruto -
    S02E20" is episode 20 overall. Reading that as season 2, episode 20 matched
    nothing, so the whole show looked missing. Files are matched to TVDB by
    absolute number; the tabs are the arc folders (or one list, ``flat``), and
    a missing episode goes to the arc whose range it falls in.
    """
    regular = sorted(
        (item for item in roster if item.season >= 1 and item.episode >= 1),
        key=lambda item: (item.season, item.episode),
    )
    by_absolute = {}
    for ordinal, item in enumerate(regular, start=1):
        # Some TVDB series omit absoluteNumber; the default order is still one.
        by_absolute.setdefault(item.absolute_number or ordinal, item)

    present: dict[int, dict] = {}
    others: list[dict] = []
    for row in files:
        number = row["episode"]
        if number is None:
            others.append(row)
        elif number not in present or present[number]["staged"]:
            present[number] = row

    arcs: dict[int, tuple[int, int]] = {}
    folder_names: dict[int, str] = {}
    for number, row in present.items():
        arc = row["season_number"] if row["season_number"] is not None else 1
        low, high = arcs.get(arc, (number, number))
        arcs[arc] = (min(low, number), max(high, number))
        folder_names.setdefault(arc, str(row.get("season") or f"Season {arc:02d}"))

    def arc_for(number: int) -> int:
        if flat or not arcs:
            return 1
        for arc, (low, high) in sorted(arcs.items()):
            if low <= number <= high:
                return arc
        following = [arc for arc, (low, _high) in sorted(arcs.items()) if low > number]
        return following[0] if following else max(arcs)

    today = date.today().isoformat()
    rows: dict[int, dict] = {}
    for number, item in by_absolute.items():
        future = bool(item.aired and item.aired[:10] > today)
        tba = future or (not item.aired and not ended)
        arc = arc_for(number)
        if number in present:
            row = present[number]
            rows[number] = {
                **row,
                "season_number": 1 if flat else (row["season_number"] if row["season_number"] is not None else 1),
                "episode_title": item.name,
                "aired": item.aired,
                "tba": False,
                "missing": False,
                # Probed codecs go by the file's own numbers, published ones by TVDB's.
                "codec": (codecs or {}).get((row["season_number"], number))
                or (codecs or {}).get((item.season, item.episode)),
                # By the file's own numbers, before any renumbering for display.
                "german_dub": bool(german_dubbed and (row["season_number"], number) in german_dubbed),
            }
        else:
            rows[number] = {
                "path": "",
                "rel_path": "",
                "name": item.name or "TBA",
                "episode_title": item.name,
                "series": "",
                "season": "All episodes" if flat else folder_names.get(arc, f"Season {arc:02d}"),
                "season_number": arc,
                "episode": number,
                "size": 0,
                "mtime": 0,
                "staged": False,
                "stage": "missing",
                "transfer_status": "idle",
                "aired": item.aired,
                "tba": tba,
                "missing": True,
                "codec": None,
                "german_dub": False,
            }
    for number, row in present.items():
        # Beyond what TVDB lists yet: still downloaded, still shown.
        if number not in rows:
            rows[number] = {
                **row,
                "season_number": 1 if flat else (row["season_number"] or 1),
                "missing": False,
                "tba": False,
                "codec": (codecs or {}).get((row["season_number"], number)),
                "german_dub": bool(german_dubbed and (row["season_number"], number) in german_dubbed),
            }
    if flat:
        for row in rows.values():
            row["season"] = "All episodes"
    counted = list(rows.values())
    downloaded = sum(not row.get("missing", False) and not row["staged"] for row in counted)
    outstanding = [row for row in counted if row.get("missing", False) or row["staged"]]
    future_only = outstanding and all(row.get("tba", False) for row in outstanding)
    state = (
        "empty" if downloaded == 0
        else "upcoming" if future_only
        else "partial" if outstanding
        else "complete"
    )
    if not roster and downloaded:
        state = "unknown"
    return {
        "episodes": sorted(
            [*rows.values(), *others],
            key=lambda row: (row["season_number"] or 0, row["episode"] or 0, row["name"]),
        ),
        "downloaded_count": downloaded,
        "total_count": len(counted),
        "completion_state": state,
        "finished": ended and state == "complete",
        "metadata_available": bool(roster),
    }


async def search_episode(anidb_id: int, episode: int, query: str | None = None) -> dict:
    """Releases of one AniDB episode: searched by the entry's names, each result checked.

    The names are AniDB's and the ones Erai has published this entry under,
    each searched as "Name" "- 05". Every result is identified as the
    automation would and kept only if it is this entry's episode.
    """
    import httpx

    from bankai.metadata import anidb as anidb_mod

    table = await anidb_mod.index()
    entry = table.anime.get(int(anidb_id)) if table is not None else None
    if entry is None:
        raise ValueError("This AniDB entry is not in AniDB's title list")
    state = await asyncio.to_thread(erai._load_state)
    mappings = await asyncio.to_thread(erai._load_mappings)
    terms = [query] if query else []
    if not terms:
        names = [entry.title, entry.english_title]
        prefix = f"anidb:{entry.aid}|"
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
        match = anime.anidb_match(release.title, table=table, mappings=mappings)
        if not match or match["anidb_id"] != entry.aid or match["episode"] != episode:
            continue
        items.append(anime.entry_to_dict(replace(release, anidb=match, episode=episode)))
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


# -- AniDB identity in the library ---------------------------------------------
#
# A card is one AniDB entry, or the several entries one folder holds (Bleach:
# Thousand-Year Blood War keeps its four cours as season folders). TVDB made
# Bleach and its sequel one series of 416 episodes; AniDB makes Bleach 366,
# and each cour its own entry. Which entry a file is comes from Shoko's link
# for it, else from its season folder's name ("Season 02 - Sennen Kessen Hen -
# Ketsubetsu Tan"), else from the show folder's name. Each entry's episode
# list comes from Shoko, else -- for an entry Shoko does not hold -- is its
# episode count from anime-offline-database, numbers without titles.

# "Season 02 - Sennen Kessen Hen - Ketsubetsu Tan" -> the entry's own title.
_SEASON_FOLDER = re.compile(r"^\s*Season\s*\d+\s*[-–:]\s*(?P<title>.+?)\s*$", re.IGNORECASE)


def _resolve_name(table: Any, names: list[str], memo: dict[str, int | None]) -> int | None:
    from bankai.metadata import anidb

    for name in names:
        name = name.strip()
        if not name:
            continue
        if name not in memo:
            resolution = anidb.resolve_in(table, name)
            memo[name] = resolution.anime.aid if resolution.anime else None
        if memo[name] is not None:
            return memo[name]
    return None


def assign_entries(
    files: list[dict], *, catalog: Any, table: Any, links: dict[str, int] | None = None
) -> None:
    """Set each file's ``anidb_id`` and, when Shoko linked it, ``anidb_episode``.

    Shoko's link first; then the entry the user linked the folder to; then
    the folder's name.
    """
    memo: dict[str, int | None] = {}
    for row in files:
        shoko = catalog.files.get(str(row.get("path"))) if catalog is not None else None
        if shoko:
            row["anidb_id"], row["anidb_episode"] = shoko[0]
            continue
        row["anidb_episode"] = None
        row["anidb_id"] = None
        folder = str(row.get("series") or "")
        if links and folder in links:
            row["anidb_id"] = links[folder]
            continue
        if table is None:
            continue
        season = _SEASON_FOLDER.match(str(row.get("season") or ""))
        if season:
            base = re.split(r"\s+-\s+|:\s*", folder, maxsplit=1)[0]
            # The season folder may carry only the part after the show's name.
            row["anidb_id"] = _resolve_name(
                table, [season["title"], f"{base} {season['title']}", f"{folder} {season['title']}"], memo
            )
        if row["anidb_id"] is None:
            row["anidb_id"] = _resolve_name(table, [folder], memo)


def anidb_episode_of(row: dict, anime: Any) -> int | None:
    """The file's episode number within its AniDB entry.

    Shoko's link says it outright. Otherwise the number in the file name is
    taken as the entry's own, unless it counts through a season the entry only
    starts partway into, as Anime-Lists records: TYBW's second cour is filed
    as S17E14-26, AniDB episodes 1-13.
    """
    if row.get("anidb_episode"):
        return int(row["anidb_episode"])
    season, episode = row.get("season_number"), row.get("episode")
    if episode is None:
        return None
    tvdb_season = getattr(anime, "tvdb_season", None) or ""
    offset = int(getattr(anime, "tvdb_offset", 0) or 0)
    if offset and tvdb_season.isdigit() and season == int(tvdb_season) and episode > offset:
        return episode - offset
    return episode


@dataclass(frozen=True)
class _Episode:
    """One episode as the merge functions read it; TVDB's episodes have the same shape."""

    season: int
    episode: int
    name: str | None = None
    aired: str | None = None
    absolute_number: int | None = None


def merge_episodes_anidb(
    rows: list[dict],
    entries: list[tuple[int, str, list | None, bool]],
    *,
    codecs: dict[tuple[int, int], str] | None = None,
    german_dubbed: set[tuple[int, int]] | None = None,
) -> dict:
    """Episodes of a card made of AniDB entries; ``rows`` are prepared by the caller.

    One entry: tabs are the season folders it is kept in (Bleach's arcs), as
    with absolute numbering. Several: one tab per entry, each counted
    against its own episode list. ``entries`` are (aid, tab label, episode
    list or None when unknown, finished), in display order.
    """
    today = date.today().isoformat()

    def ended(roster: list | None, finished: bool) -> bool:
        return finished or (bool(roster) and all(ep.aired and ep.aired[:10] <= today for ep in roster))

    if len(entries) == 1:
        _aid, _label, roster, finished = entries[0]
        items = [
            _Episode(season=1, episode=ep.number, absolute_number=ep.number, name=ep.title, aired=ep.aired)
            for ep in roster or []
        ]
        merged = merge_episodes_absolute(
            rows, items, ended=ended(roster, finished), codecs=codecs, german_dubbed=german_dubbed
        )
        merged["metadata_available"] = roster is not None and bool(roster)
        return merged
    items = [
        _Episode(season=index, episode=ep.number, name=ep.title, aired=ep.aired)
        for index, (_aid, _label, roster, _finished) in enumerate(entries, start=1)
        for ep in roster or []
    ]
    merged = merge_episodes(
        rows,
        items,
        ended=all(ended(roster, finished) for _aid, _label, roster, finished in entries),
        codecs=codecs,
        german_dubbed=german_dubbed,
    )
    labels = {index: label for index, (_aid, label, _roster, _finished) in enumerate(entries, start=1)}
    for row in merged["episodes"]:
        if row.get("season_number") in labels:
            row["season"] = labels[row["season_number"]]
    merged["metadata_available"] = any(roster for _aid, _label, roster, _finished in entries)
    return merged


def prepare_anidb_rows(files: list[dict], order: list[int], table: Any) -> list[dict]:
    """Copies of a card's files, numbered for merge_episodes_anidb.

    One entry: season_number is the season folder's place, episode the AniDB
    number. Several: season_number is the entry's place. A file whose entry is
    not among them lands in "Other files".
    """
    place = {aid: index for index, aid in enumerate(order, start=1)}
    anime = getattr(table, "anime", {}) if table is not None else {}
    single = len(order) == 1
    folders = sorted({str(row.get("season") or "") for row in files})
    folder_place = {name: index for index, name in enumerate(folders, start=1)}
    prepared = []
    for row in files:
        aid = row.get("anidb_id")
        number = anidb_episode_of(row, anime.get(aid)) if aid in place else None
        if number is None:
            prepared.append({**row, "season_number": None, "episode": None})
            continue
        prepared.append(
            {
                **row,
                "season_number": folder_place[str(row.get("season") or "")] if single else place[aid],
                "season": row.get("season") or "Episodes",
                "episode": number,
            }
        )
    return prepared


def _entry_name(aid: int, catalog: Any, known: dict) -> str:
    entry = catalog.entries.get(aid) if catalog is not None else None
    anime_entry = known.get(aid)
    return (entry.title if entry else None) or (
        (anime_entry.english_title or anime_entry.title) if anime_entry else f"AniDB {aid}"
    )


async def group_shows(
    entries: list[dict],
    root: Path,
    *,
    include_episodes: bool = True,
    only_key: str | None = None,
    catalog: Any = None,
) -> list[dict]:
    """One card per show, however many folders and spellings it arrived under.

    Grouping used to key on the raw folder name, so a show sitting in two
    folders got two cards -- "Show" beside "Show (2024)", or a romaji folder
    beside its English one. Identity is now decided twice: the normalised
    name catches the pairs that differ in punctuation or a trailing year, and
    the AniDB entry most of a folder's files are catches the pairs that share
    no spelling at all. Folders of different AniDB entries stay apart: Bleach
    and its sequel. ``catalog`` is Shoko's (see shoko_catalog).
    """
    from collections import Counter

    from bankai.metadata import anidb as anidb_mod
    from bankai.metadata import anidb_art
    from bankai.web.shoko_catalog import EntryEpisode

    table = None
    with suppress(Exception):
        table = await anidb_mod.index()
    known = getattr(table, "anime", {}) if table is not None else {}
    buckets: dict[str, dict] = {}
    for entry in entries:
        season, episode = parse_se(entry["name"]) or (None, None)
        bucket = buckets.setdefault(_name(entry["series"]), {"titles": [], "files": []})
        if entry["series"] not in bucket["titles"]:
            bucket["titles"].append(entry["series"])
        bucket["files"].append({**entry, "season_number": season, "episode": episode})
    prefs = await asyncio.to_thread(load_prefs)
    await asyncio.to_thread(
        assign_entries,
        [row for bucket in buckets.values() for row in bucket["files"]],
        catalog=catalog,
        table=table,
        links=folder_links(prefs),
    )

    merged: dict[str, dict] = {}
    for name, bucket in buckets.items():
        aids = Counter(row["anidb_id"] for row in bucket["files"] if row.get("anidb_id"))
        # The entry most of its files are; a tie goes to the older entry.
        primary = min(aids, key=lambda aid: (-aids[aid], aid)) if aids else None
        slot = merged.setdefault(
            f"anidb:{primary}" if primary else f"name:{name}",
            {"titles": [], "files": [], "aids": Counter()},
        )
        slot["titles"].extend(bucket["titles"])
        slot["files"].extend(bucket["files"])
        slot["aids"].update(aids)
    for slot in merged.values():
        slot["titles"] = sorted(dict.fromkeys(slot["titles"]))
        slot["folders"] = slot["titles"]
        slot["key"] = _display_title(slot["titles"])

    # One read of the release state for the whole page: it is a fourteen
    # megabyte file.
    state = await asyncio.to_thread(erai._load_state)
    blocked = erai._policy_anidb_ids(await asyncio.to_thread(erai._load_policies))

    # Collections: the cards of one Shoko group are one card, named after the
    # group, and the group's sequels and prequels not on disk are its missing
    # tabs. Without Shoko's groups, cards stay as their folders made them.
    collections = anime_collections.assign(
        catalog, await asyncio.to_thread(anime_collections.load_places)
    )
    titles = await asyncio.to_thread(anime_collections.load_titles)
    groups = getattr(catalog, "groups", None) or {}
    if collections.of:
        regrouped: dict[str, dict] = {}
        for identity, slot in merged.items():
            aids = slot["aids"]
            primary = min(aids, key=lambda aid: (-aids[aid], aid)) if aids else None
            group = collections.of.get(primary) if primary is not None else None
            if group is None:
                regrouped[identity] = slot
                continue
            target = regrouped.setdefault(
                f"group:{group}",
                {"titles": [], "folders": [], "files": [], "aids": Counter(), "collection_id": group},
            )
            target["titles"].extend(slot["titles"])
            target["folders"].extend(slot["folders"])
            target["files"].extend(slot["files"])
            target["aids"].update(slot["aids"])
        merged = regrouped
        on_disk = {aid for slot in merged.values() for aid in slot["aids"]}
        for aid, group in sorted(collections.of.items()):
            slot = merged.get(f"group:{group}")
            if slot is not None and aid not in on_disk and aid not in blocked:
                slot["aids"][aid] = 0
        keys: set[str] = set()
        for identity, slot in merged.items():
            group = groups.get(slot.get("collection_id"))
            if group is None:
                keys.add(_name(slot["key"]))
                continue
            slot["main_aid"] = anime_collections.title_entry(group, titles)
            # The group's name is the card's; its key must still be its own.
            key = group.name if _name(group.name) not in keys else f"{group.name} ({group.id})"
            slot["key"] = key
            slot["titles"] = sorted(dict.fromkeys([key, *slot["titles"]]))
            slot["folders"] = sorted(dict.fromkeys(slot["folders"]))
            keys.add(_name(key))
    else:
        keys = {_name(slot["key"]) for slot in merged.values()}

    # An AniDB entry the automation took with nothing on disk still gets a
    # card -- its collection's, if it has one -- unless it was blacklisted,
    # which is how one removed from the library leaves it.
    covered = {aid for slot in merged.values() for aid in slot["aids"]}
    for raw, record in (state.get("anime") or {}).items():
        if not str(raw).isdigit() or int(raw) in covered or int(raw) in blocked:
            continue
        aid = int(raw)
        home = merged.get(f"group:{collections.of.get(aid)}")
        if home is not None:
            home["aids"][aid] = 0
            covered.add(aid)
            continue
        anime_entry = known.get(aid)
        names = [
            (anime_entry.english_title or anime_entry.title) if anime_entry else None,
            anime_entry.title if anime_entry else None,
            str(record.get("english_title") or record.get("title") or "") or None,
        ]
        # Its key must not be another card's: the page asks for a card back by it.
        key = next((name for name in names if name and _name(name) not in keys), None)
        if key is None:
            key = f"{next((name for name in names if name), 'AniDB')} (AniDB {aid})"
        keys.add(_name(key))
        covered.add(aid)
        merged[f"anidb:{aid}"] = {
            "titles": [key], "folders": [], "files": [], "aids": Counter({aid: 0}), "key": key,
        }

    if only_key is not None:
        wanted = _name(only_key)
        merged = {
            identity: slot
            for identity, slot in merged.items()
            if wanted == _name(slot["key"]) or wanted in {_name(t) for t in slot["titles"]}
        }

    def anidb_card(slot: dict) -> dict:
        """The AniDB entries behind a card: display order, episode lists, tab labels."""
        aids = list(slot["aids"])
        rosters: dict[int, list | None] = {}
        finished: dict[int, bool] = {}
        for aid in aids:
            entry = catalog.entries.get(aid) if catalog is not None else None
            facts = anidb_art.facts(aid)
            finished[aid] = facts.get("status") == "FINISHED"
            if entry is not None and entry.episodes:
                rosters[aid] = list(entry.episodes)
            elif facts.get("episodes"):
                # Not in Shoko's collection: how many episodes it has, no more.
                rosters[aid] = [EntryEpisode(n, None, None) for n in range(1, int(facts["episodes"]) + 1)]
            else:
                rosters[aid] = None

        def first_aired(aid: int) -> str:
            dates = [ep.aired for ep in rosters.get(aid) or [] if ep.aired]
            year = anidb_art.facts(aid).get("year")
            return min(dates) if dates else str(year) if year else "9999"

        def folders_of(aid: int) -> set[str]:
            return {str(row.get("season") or "") for row in slot["files"] if row.get("anidb_id") == aid}

        # The sequel chain prequel to sequel, then side stories and specials;
        # by air date alone a 2013 OVA sat between seasons one and two.
        order = anime_collections.sequel_order(
            aids,
            getattr(catalog, "relations", None) or [],
            lambda aid: (first_aired(aid), min(folders_of(aid), default="")),
        )
        # Each tab is its entry, named without the collection's own name.
        # Named after season folders, two cours sharing one were two "Season 03".
        collection = slot["key"] if slot.get("collection_id") is not None else None
        labels = {
            aid: anime_collections.short_label(_entry_name(aid, catalog, known), collection)
            for aid in order
        }
        # Two tabs of one name -- a show and its OVA both "Attack on Titan" --
        # are told apart by what they are and when.
        counts = Counter(labels.values())
        for aid, label in labels.items():
            if counts[label] > 1:
                facts = anidb_art.facts(aid)
                kind = {"TV": "TV", "MOVIE": "Movie", "OVA": "OVA", "ONA": "ONA", "SPECIAL": "Special"}.get(
                    str(facts.get("type") or "").upper()
                )
                year = first_aired(aid)[:4] if first_aired(aid) != "9999" else None
                extra = ", ".join(part for part in (kind, year) if part)
                labels[aid] = f"{label} ({extra or aid})"
        return {"order": order, "rosters": rosters, "labels": labels, "finished": finished}

    def build(slot: dict) -> dict:
        files = slot["files"]
        title = slot["key"]
        card = anidb_card(slot) if slot["aids"] else None
        if card is not None:
            rows = prepare_anidb_rows(files, card["order"], table)
            # Keyed as the rows are numbered: the tab, then the AniDB episode.
            merged_episodes = merge_episodes_anidb(
                rows,
                [
                    (aid, card["labels"][aid], card["rosters"][aid], card["finished"][aid])
                    for aid in card["order"]
                ],
                codecs=probed_codecs(rows),
                german_dubbed=german_dubbed_episodes(rows),
            )
            numbering = "anidb"
        else:
            # No AniDB entry answers for these folders: their files, as named.
            merged_episodes = merge_episodes(
                files,
                [],
                ended=False,
                codecs=probed_codecs(files),
                german_dubbed=german_dubbed_episodes(files),
            )
            numbering = "files"
        display_title = title
        source_title = ""
        poster_url = None
        year = None
        anidb_id = None
        if card is not None:
            # The collection's title entry names the card and lends it its
            # cover, else the first entry: AniDB's own cover, from Shoko or
            # else anime-offline-database.
            main = slot.get("main_aid")
            anidb_id = main if main in card["order"] else card["order"][0]
            entry = catalog.entries.get(anidb_id) if catalog is not None else None
            anime_entry = known.get(anidb_id)
            if slot.get("collection_id") is not None:
                display_title = title  # the collection's own name
            elif entry is not None or anime_entry is not None:
                display_title = _entry_name(anidb_id, catalog, known)
            if anime_entry is not None and not _same_name(anime_entry.title, display_title):
                source_title = anime_entry.title
            poster_url = (entry.poster_url if entry else None) or anidb_art.cover(anidb_id)
            aired = [ep.aired for ep in card["rosters"].get(anidb_id) or [] if ep.aired]
            year = int(min(aired)[:4]) if aired else anidb_art.facts(anidb_id).get("year")
        result = {
            "key": title,
            # Every folder behind this one card, so the page can ask for the
            # right files back after two of them were merged.
            "folders": slot["folders"],
            "title": display_title,
            "source_title": source_title,
            "anidb_id": anidb_id,
            "anidb_ids": card["order"] if card is not None else [],
            # The Shoko group the card is, and its entries: the ones Shoko
            # holds can be picked to name and picture the collection.
            "collection_id": slot.get("collection_id"),
            "collection_entries": [
                {
                    "anidb_id": aid,
                    "title": _entry_name(aid, catalog, known),
                    "owned": catalog is not None and aid in catalog.entries,
                }
                for aid in (card["order"] if card is not None else [])
            ],
            "avc_count": sum(
                1
                for row in merged_episodes["episodes"]
                # A German dub is irreplaceable, so it is not on offer.
                if row.get("codec") == "avc" and not row.get("german_dub")
            ),
            "hevc_count": sum(
                1
                for row in merged_episodes["episodes"]
                if row.get("codec") == "hevc" and not row.get("german_dub")
            ),
            # The top tier, whatever its codec: each episode is counted in one
            # of the three, so together they never exceed the episodes on disk.
            "german_dub_count": sum(
                1 for row in merged_episodes["episodes"] if row.get("german_dub")
            ),
            "numbering": numbering,
            "year": year,
            "poster_url": poster_url,
            "episode_count": len(files),
            "season_count": len(
                {
                    row["season_number"]
                    for row in merged_episodes["episodes"]
                    if row["season_number"] is not None
                }
            ),
            "size": sum(row["size"] for row in files),
            "staged_count": sum(row["staged"] for row in files),
            **merged_episodes,
            "season_anidb": {},
        }
        if card is not None:
            # A card of AniDB entries: its tabs are the entries, or -- one
            # entry kept in season folders -- that entry's folders.
            def entry_link(aid: int) -> list[dict]:
                return [{"anidb_id": aid, "title": _entry_name(aid, catalog, known)}]

            tabs = {row["season_number"] for row in result["episodes"] if row["season_number"] is not None}
            result["season_anidb"] = (
                {str(tab): entry_link(card["order"][0]) for tab in tabs}
                if len(card["order"]) == 1
                else {str(index): entry_link(aid) for index, aid in enumerate(card["order"], start=1)}
            )
        if not include_episodes:
            result["episodes"] = []
        return result

    shows = await asyncio.to_thread(lambda: [build(slot) for slot in merged.values()])
    return sorted(shows, key=lambda row: row["title"].casefold())


async def queue_covers(rows: list[dict], catalog: Any = None) -> list[dict]:
    """Each queue row's AniDB cover and name, by the entry its job files under."""
    from bankai.metadata import anidb as anidb_mod
    from bankai.metadata import anidb_art

    table = await anidb_mod.index()
    known = getattr(table, "anime", {}) if table is not None else {}
    for row in rows:
        raw = str(row.get("anidb_id") or "")
        if not raw.isdigit():
            continue
        aid = int(raw)
        entry = catalog.entries.get(aid) if catalog is not None else None
        row["poster_url"] = (entry.poster_url if entry else None) or anidb_art.cover(aid)
        if entry is not None or aid in known:
            row["series_title"] = _entry_name(aid, catalog, known)
    return rows


async def enrich_review_rows(rows: list[dict], catalog: Any = None) -> list[dict]:
    """Each review or blacklist card's name, year and cover, from its AniDB entry.

    A card that is one AniDB entry shows that entry's name and cover -- from
    Shoko, else anime-offline-database. One that no entry answers for yet is
    shown by its Erai name until one is chosen for it.
    """
    from bankai.metadata import anidb_art

    for row in rows:
        row.pop("tvdb_id", None)
        if not str(row.get("anidb_id") or "").isdigit():
            row["title"] = row["source_title"]
            row["poster_url"] = row.get("anidb_poster_url")
            continue
        aid = int(row["anidb_id"])
        entry = catalog.entries.get(aid) if catalog is not None else None
        row["title"] = row.get("english_title") or row.get("anidb_title") or row["source_title"]
        row["year"] = anidb_art.facts(aid).get("year")
        row["poster_url"] = (
            (entry.poster_url if entry else None) or anidb_art.cover(aid) or row.get("anidb_poster_url")
        )
    return rows
