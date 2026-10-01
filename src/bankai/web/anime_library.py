"""Show-level Anime library presentation and cached TVDB artwork identity."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.metadata import anime_mapping
from bankai.metadata.tvdb import TVDBEpisode
from bankai.processor.anime import _tvdb_episode_map
from bankai.processor.naming import sanitise
from bankai.torrent.matcher import parse_se
from bankai.web import anime, discover, erai

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


def _is_romaji(value: str) -> bool:
    """Every letter in it is a Latin one, so it can be typed and searched.

    Written as an allowlist after a blocklist of the scripts I thought of let
    Arabic straight through. Asking what each letter actually is cannot be
    outflanked by a script I did not consider. Accented Latin passes; kana,
    kanji, Cyrillic, Greek and Arabic do not.
    """
    letters = [character for character in value if character.isalpha()]
    if not letters:
        return False
    return all(
        "LATIN" in unicodedata.name(character, "") for character in letters
    )


_PUNCTUATION = re.compile(r"[^0-9a-z]+")


def _same_name(left: str, right: str) -> bool:
    """One name, for the purpose of not printing it under itself.

    Compared with punctuation dropped as well as case: _name only removes
    what Windows forbids in a folder, so an exclamation mark survives it and
    "Akame ga Kill!" did not match "Akame ga Kill".
    """
    return _PUNCTUATION.sub("", _name(left)) == _PUNCTUATION.sub("", _name(right))


async def second_name(tvdb_id: int | None, shown_as: str, erai: str = "") -> str:
    """The name to print under a show's title, or nothing.

    A card stands for a whole series, so its second name has to as well. The
    AniDB list is organised that way and its first entry is the series; an
    Erai release name is only ever the season it belongs to, which is why it
    is the fallback rather than the preference despite being firsthand.

    Nothing is the right answer more often than it looks. "Akame ga Kill!" is
    already the romaji, so repeating it under itself says nothing; the page
    compared the two exactly, which would still have printed a second line
    over a stray exclamation mark.
    """
    candidate = await romaji_name(tvdb_id) or erai
    return "" if _same_name(candidate, shown_as) else candidate


async def romaji_name(tvdb_id: int | None) -> str:
    """The romaji name a series is released under, from the AniDB list.

    Not from TVDB's aliases. That list is translations into every language it
    holds, in no useful order, so picking from it gave a show's Arabic name
    or the mouthful that is one specific season. The AniDB list is the names
    release groups actually publish under -- it is already what bankai
    searches Nyaa with -- so it is the same string the rest of the pipeline
    reasons about. Cached in memory for an hour by the mapping layer.
    """
    if not tvdb_id:
        return ""
    with suppress(Exception):
        for value in await anime_mapping.related_titles(int(tvdb_id)):
            if _is_romaji(value):
                return value.strip()
    return ""


def _search_title(title: str) -> str:
    """The show's own name, without the year or the season it happens to be."""
    stripped = re.sub(r"\s*\(\d{4}\)$", "", title).strip()
    return _SEASON_SUFFIX.sub("", stripped).strip() or stripped


async def show_metadata(
    title: str, tvdb_id: int | None = None, kind: str = "show", *, anime_only: bool = True
) -> dict:
    # The kind is part of the key: a film and a series can share a name, and
    # the movie library must not be handed the series' artwork. So is whether
    # the search was limited to anime.
    key = f"id:{tvdb_id}" if tvdb_id else f"{kind}:{_name(title)}"
    if not tvdb_id and not anime_only:
        key = "all:" + key
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    # Versioned: entries cached before the season suffix was understood hold an
    # empty result that would otherwise be served for another day.
    saved = _persistent_entry(f"metadata:v2:{key}")
    if saved is not None and isinstance(saved[0], dict):
        _CACHE[key] = (time.time(), saved[0])
        if saved[1] >= _PERSISTENT_TTL_SECONDS:
            _refresh_later(key, lambda: _fetch_metadata(key, title, tvdb_id, kind, anime_only))
        return saved[0]
    miss = _persistent_entry(f"miss:v1:{key}")
    if miss is not None and miss[1] < _MISS_TTL_SECONDS:
        _CACHE[key] = (time.time(), {})
        return {}
    return await _fetch_metadata(key, title, tvdb_id, kind, anime_only)


async def _fetch_metadata(
    key: str, title: str, tvdb_id: int | None, kind: str, anime_only: bool = True
) -> dict:
    """Ask the provider, and remember the answer -- but never an outage."""
    try:
        metadata = await _lookup_metadata(title, tvdb_id, kind, anime_only)
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


async def _lookup_metadata(title: str, tvdb_id: int | None, kind: str, anime_only: bool = True) -> dict:
    """The provider's answer; ``{}`` when it has no match, raising when it failed."""
    if not discover.is_configured():
        raise RuntimeError("TVDB is not configured")
    if tvdb_id:
        return asdict(await anime.series_metadata(tvdb_id))
    if not anime_only:
        return await _lookup_any_title(title, kind)
    query = _search_title(title)
    candidates = await anime.tvdb_candidates(query, raise_errors=True, anime_only=anime_only)
    exact = [
        item
        for item in candidates
        if item.kind == kind
        and _name(query)
        in {
            _name(item.english_title),
            _name(item.japanese_title or ""),
            *(_name(alias) for alias in item.aliases),
        }
    ]
    return asdict(exact[0]) if len(exact) == 1 else {}


_FOLDER_YEAR = re.compile(r"\((\d{4})\)\s*$")


async def _lookup_any_title(title: str, kind: str) -> dict:
    """A film or series of any genre, for the Movies & Shows library.

    One TVDB search, the one the Discover page makes, which brings the cover
    with it. The anime search this library used before filtered on TVDB's
    anime genre, so almost none of its films was found. A folder's year
    tells remakes apart: "Passengers (2016)", not the 2008 film.
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


def folder_tvdb_ids(folders: set[str] | list[str], root: Path) -> dict[str, int]:
    """Each show folder's TVDB id, as far as it is already known; no provider calls.

    From the ids bankai recorded itself, a tvshow.nfo, or the metadata the
    library page cached under the folder's name. The cache is read from disk
    each time: the automation worker is a process of its own, and the page,
    in the web process, is what fills it.
    """
    try:
        saved = json.loads(_persistent_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    ids = known_ids()
    found: dict[str, int] = {}
    for folder in folders:
        name = _name(folder)
        tvdb_id = ids.get(name) or _nfo_id(root / folder)
        if not tvdb_id:
            hit = saved.get(f"metadata:v2:show:{name}") if isinstance(saved, dict) else None
            value = hit.get("value") if isinstance(hit, dict) else None
            if isinstance(value, dict) and str(value.get("tvdb_id") or "").isdigit():
                tvdb_id = int(value["tvdb_id"])
        if tvdb_id:
            found[folder] = int(tvdb_id)
    return found


def known_ids() -> dict[str, int]:
    state = erai._load_state()
    ids = {}
    for record in [*state.get("series", {}).values(), *erai._load_mappings().values()]:
        if record.get("english_title") and record.get("tvdb_id"):
            ids[_name(record["english_title"])] = int(record["tvdb_id"])
    for job in bgjobs.list_jobs():
        if not job.args or job.args[0] != "anime-download":
            continue
        title = bgjobs.argument_value(job.args, "--english-title")
        tvdb_id = bgjobs.argument_value(job.args, "--tvdb-id")
        if title and tvdb_id and tvdb_id.isdigit():
            ids[_name(title)] = int(tvdb_id)
    return ids


def _nfo_id(folder: Path) -> int | None:
    path = folder / "tvshow.nfo"
    try:
        tree = ET.fromstring(path.read_bytes())
        raw = tree.findtext("tvdbid")
        if not raw:
            raw = next(
                (node.text for node in tree.findall("uniqueid") if node.get("type") == "tvdb"),
                None,
            )
        return int(raw) if raw and raw.isdigit() else None
    except (OSError, ET.ParseError):
        return None


async def episode_roster(tvdb_id: int) -> list:
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
    rows = await _tvdb_episode_map(tvdb_id)
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


def codec_index(state: dict | None = None) -> dict[str, dict[tuple[int, int], str]]:
    """Every series' published encodes, from a single read of the release state.

    Built for the whole library at once. Asking per series meant re-reading and
    re-parsing a fourteen megabyte state file once for each of them, which was
    the entire reason the library page took twenty seconds to answer.
    """
    state = erai._load_state() if state is None else state
    releases = state.get("releases", {})
    index: dict[str, dict[tuple[int, int], str]] = {}
    for canonical, row in state.get("canonical", {}).items():
        parts = str(canonical).split("|")
        if len(parts) != 3:
            continue
        release = releases.get(str(row.get("info_hash") or ""))
        if not release:
            continue
        try:
            key = (int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        codec = "hevc" if erai._is_hevc_title(str(release.get("title") or "")) else "avc"
        index.setdefault(parts[0], {})[key] = codec
    return index


def erai_source_titles(state: dict | None = None) -> dict[str, str]:
    """The name Erai-raws publishes each series under, keyed by TVDB id.

    The library calls a show by its English title, but every release, every
    held review card and every log line names it the way Erai does. Holding
    both is what makes a mismatch traceable, so the drawer carries the source
    name as well. Built from the same single read as :func:`codec_index`.
    """
    state = erai._load_state() if state is None else state
    releases = state.get("releases", {})
    titles: dict[str, str] = {}
    for canonical, row in state.get("canonical", {}).items():
        tvdb_id = str(canonical).split("|")[0]
        if tvdb_id.startswith("anidb:"):
            continue  # keyed by AniDB, not a TVDB series
        release = releases.get(str(row.get("info_hash") or ""))
        if not release:
            continue
        source = anime.clean_release_title(str(release.get("title") or ""))
        if not source:
            continue
        # The shortest, not the first. Erai publishes each season under its
        # own name -- "Bleach", then "Bleach: Sennen Kessen Hen - Kashin Tan"
        # -- and taking whichever came first out of a dict labelled the Bleach
        # card with one late arc. The shortest is the series, not an arc.
        current = titles.get(tvdb_id)
        if current is None or (len(source), source) < (len(current), current):
            titles[tvdb_id] = source
    return titles


def episode_codecs(tvdb_id: int | None) -> dict[tuple[int, int], str]:
    """Codecs for one series. Prefer :func:`codec_index` for a whole page."""
    if not tvdb_id:
        return {}
    return codec_index().get(str(tvdb_id), {})


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


NUMBERING_MODES = ("season", "absolute", "absolute_flat")


def _prefs_path() -> Path:
    return erai._state_path().with_name("anime_library_prefs.json")


def load_prefs() -> dict[str, dict]:
    try:
        value = json.loads(_prefs_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def numbering_for(prefs: dict[str, dict], *, tvdb_id: object, key: str) -> str:
    """The episode numbering chosen for a show: by its TVDB id, else its card key."""
    row = prefs.get(f"tvdb:{tvdb_id}") if tvdb_id else None
    row = row or prefs.get(f"key:{key}") or {}
    mode = row.get("numbering")
    return mode if mode in NUMBERING_MODES else "season"


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


def save_numbering(*, key: str, tvdb_id: object, mode: str) -> None:
    if mode not in NUMBERING_MODES:
        raise ValueError(f"numbering must be one of {', '.join(NUMBERING_MODES)}")
    prefs = load_prefs()
    for name in ([f"tvdb:{tvdb_id}"] if tvdb_id else []) + [f"key:{key}"]:
        if mode == "season":
            prefs.pop(name, None)  # the default needs no entry
        else:
            prefs[name] = {"numbering": mode}
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


async def search_episode(tvdb_id: int, season: int, episode: int, query: str | None = None) -> dict:
    """Search scene names in reverse, then verify every result's TVDB target."""
    from dataclasses import replace

    import httpx

    from bankai.metadata import anime_mapping

    match = await anime.series_metadata(tvdb_id)
    roster = await episode_roster(tvdb_id)
    if not any((row.season, row.episode) == (season, episode) for row in roster):
        raise ValueError("Episode does not exist in TVDB ordering")
    terms = [query] if query else []
    if not terms:
        for title in await anime_mapping.related_titles(tvdb_id):
            parts = [
                part for part in await anime_mapping.anidb_parts(title) if part.tvdb_id == tvdb_id
            ]
            for part in parts:
                for number in range(1, len(roster) + 1):
                    if anime_mapping.map_part(part, number, roster) == (season, episode):
                        terms.append(f'"{title}" "- {number:02d}"')
                        break
        target = next(row for row in roster if (row.season, row.episode) == (season, episode))
        number = target.absolute_number or episode
        terms.extend(
            f'"{title}" "- {number:02d}"'
            for title in [match.english_title, match.japanese_title]
            if title
        )
    terms = list(dict.fromkeys(terms))[:6]
    state = await asyncio.to_thread(erai._load_state)
    indexed = [
        erai._entry_from_dict(row)
        for index in [state["backfill"], *state["series_catalogs"].values()]
        for field in ["catalog_1080", "catalog_720"]
        for row in index.get(field, {}).values()
    ]
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": anime.get_settings().scraper.user_agent},
    ) as client:
        batches = await asyncio.gather(
            *(anime._fetch_rss(client, term, "1_2", 0) for term in terms), return_exceptions=True
        )
        by_hash = {row.info_hash: row for row in indexed}
        for batch in batches:
            if isinstance(batch, list):
                by_hash.update({row.info_hash: row for row in batch})
        items = []
        # Avoid TVDB lookups for unrelated catalogue titles.
        aliases = {
            anime_mapping.normalise(value)
            for value in [
                match.english_title,
                match.japanese_title or "",
                *match.aliases,
                *(await anime_mapping.related_titles(tvdb_id)),
            ]
        }
        for entry in by_hash.values():
            clean = anime.clean_release_title(entry.title)
            if anime_mapping.normalise(clean) not in aliases and not query:
                continue
            _, number = anime.release_episode_info(entry.title)
            if number is None:
                continue
            resolved, identity, _ = await erai._resolve(entry, episodes=roster)
            if (
                not resolved
                or resolved.tvdb_id != tvdb_id
                or not identity
                or (identity.season, identity.episode) != (season, episode)
            ):
                continue
            items.append(
                anime.entry_to_dict(replace(entry, tvdb=match, season=season, episode=episode))
            )
    return {
        "items": sorted(
            items, key=lambda row: (-erai._resolution(erai._entry_from_dict(row)), -row["seeders"])
        ),
        "queries": terms,
        "match": asdict(match),
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


def _folder_tvdb_id(root: Path, titles: list[str]) -> int | None:
    """The first tvshow.nfo id among the folders a show is sitting in."""
    for title in titles:
        found = _nfo_id(root / title)
        if found:
            return found
    return None


# -- AniDB identity in the library ---------------------------------------------
#
# A card is one AniDB entry, or the several entries one folder holds (Bleach:
# Thousand-Year Blood War keeps its four cours as season folders). TVDB made
# Bleach and its sequel one series of 416 episodes; AniDB makes Bleach 366,
# and each cour its own entry. Which entry a file is comes from Shoko's link
# for it, else from its season folder's name ("Season 02 - Sennen Kessen Hen -
# Ketsubetsu Tan"), else from the show folder's name. Each entry's episode
# list comes from Shoko, else from the slice of TVDB that Anime-Lists says is
# that entry.

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


def _season_entries(table: Any, tvdb_id: Any) -> dict[str, list[dict]]:
    """Each TVDB season's AniDB entries, in order; a season split in cours has several."""
    if table is None or not str(tvdb_id or "").isdigit():
        return {}
    seasons: dict[str, list[dict]] = {}
    family = sorted(
        (
            anime
            for anime in table.by_tvdb.get(int(tvdb_id), [])
            if str(anime.tvdb_season or "").isdigit() and anime.tvdb_season != "0"
        ),
        key=lambda anime: (int(anime.tvdb_season), anime.tvdb_offset, anime.aid),
    )
    for anime in family:
        seasons.setdefault(str(int(anime.tvdb_season)), []).append(
            {"anidb_id": anime.aid, "title": anime.english_title or anime.title}
        )
    return seasons


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
    taken as the entry's own, unless it counts through a TVDB season the
    entry only starts partway into: TYBW's second cour is TVDB S17E14-26,
    AniDB episodes 1-13.
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


def tvdb_slice(anime: Any, roster: list, family: list) -> list:
    """The TVDB episodes that are one AniDB entry, numbered as AniDB numbers them."""
    from bankai.web.shoko_catalog import EntryEpisode

    regular = sorted(
        (item for item in roster if item.season >= 1 and item.episode >= 1),
        key=lambda item: (item.season, item.episode),
    )
    season = str(anime.tvdb_season or "")
    if season.isdigit() and int(season) >= 1:
        number, start = int(season), int(anime.tvdb_offset or 0)
        # Up to where the next entry on the same TVDB season begins.
        later = sorted(
            int(other.tvdb_offset or 0)
            for other in family
            if str(other.tvdb_season or "") == season and int(other.tvdb_offset or 0) > start
        )
        end = later[0] if later else None
        return [
            EntryEpisode(item.episode - start, item.name, item.aired)
            for item in regular
            if item.season == number and item.episode > start and (end is None or item.episode <= end)
        ]
    if season == "a":
        # Numbered through the whole show, less the seasons that are other entries.
        claimed = {
            int(other.tvdb_season)
            for other in family
            if other.aid != anime.aid and str(other.tvdb_season or "").isdigit() and int(other.tvdb_season) >= 1
        }
        kept = [item for item in regular if item.season not in claimed]
        return [EntryEpisode(n, item.name, item.aired) for n, item in enumerate(kept, start=1)]
    return []


def merge_episodes_anidb(
    rows: list[dict],
    entries: list[tuple[int, str, list | None]],
    *,
    codecs: dict[tuple[int, int], str] | None = None,
    german_dubbed: set[tuple[int, int]] | None = None,
) -> dict:
    """Episodes of a card made of AniDB entries; ``rows`` are prepared by the caller.

    One entry: tabs are the season folders it is kept in (Bleach's arcs), as
    with absolute numbering. Several: one tab per entry, each counted
    against its own episode list. ``entries`` are (aid, tab label, episode
    list or None when unknown), in display order.
    """
    today = date.today().isoformat()

    def aired_out(roster: list | None) -> bool:
        return bool(roster) and all(ep.aired and ep.aired[:10] <= today for ep in roster)

    if len(entries) == 1:
        _aid, _label, roster = entries[0]
        items = [
            TVDBEpisode(season=1, episode=ep.number, absolute_number=ep.number, name=ep.title, aired=ep.aired)
            for ep in roster or []
        ]
        merged = merge_episodes_absolute(
            rows, items, ended=aired_out(roster), codecs=codecs, german_dubbed=german_dubbed
        )
        merged["metadata_available"] = roster is not None and bool(roster)
        return merged
    items = [
        TVDBEpisode(season=index, episode=ep.number, name=ep.title, aired=ep.aired)
        for index, (_aid, _label, roster) in enumerate(entries, start=1)
        for ep in roster or []
    ]
    merged = merge_episodes(
        rows,
        items,
        ended=all(aired_out(roster) for _aid, _label, roster in entries),
        codecs=codecs,
        german_dubbed=german_dubbed,
    )
    labels = {index: label for index, (_aid, label, _roster) in enumerate(entries, start=1)}
    for row in merged["episodes"]:
        if row.get("season_number") in labels:
            row["season"] = labels[row["season_number"]]
    merged["metadata_available"] = any(roster for _aid, _label, roster in entries)
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
    beside its English one. Normalising the name only ever decided whether to
    add an empty card for a tracked title; it never merged two real folders.

    Identity is now decided twice. The normalised name catches the pairs that
    differ in punctuation or a trailing year, and the AniDB entry (else the
    TVDB id) catches the pairs that share no spelling at all. Folders of
    different AniDB entries stay apart even when TVDB calls them one series:
    Bleach and its sequel. ``catalog`` is Shoko's (see shoko_catalog).
    """
    from collections import Counter

    from bankai.metadata import anidb as anidb_mod
    from bankai.metadata import anidb_art

    table = None
    with suppress(Exception):
        table = await anidb_mod.index()
    buckets: dict[str, dict] = {}
    for entry in entries:
        season, episode = parse_se(entry["name"]) or (None, None)
        bucket = buckets.setdefault(_name(entry["series"]), {"titles": [], "files": []})
        if entry["series"] not in bucket["titles"]:
            bucket["titles"].append(entry["series"])
        bucket["files"].append({**entry, "season_number": season, "episode": episode})
    prefs = await asyncio.to_thread(load_prefs)
    links = folder_links(prefs)
    await asyncio.to_thread(
        assign_entries,
        [row for bucket in buckets.values() for row in bucket["files"]],
        catalog=catalog,
        table=table,
        links=links,
    )

    ids = await asyncio.to_thread(known_ids)
    # One read of the release state for the whole page, not one per show: it
    # is a fourteen megabyte file, and per show it cost twenty seconds.
    state = await asyncio.to_thread(erai._load_state)
    codecs_by_series = codec_index(state)
    source_titles = erai_source_titles(state)
    tracked = state.get("series", {})

    # A tracked show with nothing on disk yet still gets a card -- unless it
    # was blacklisted, which is how a show removed from the library leaves it.
    # Per AniDB season now, so the show is gone once all its seasons are.
    policies = await asyncio.to_thread(erai._load_policies)
    for tvdb_key, record in tracked.items():
        title = record.get("english_title")
        if erai.tvdb_show_blacklisted(record.get("tvdb_id") or tvdb_key, policies, table=table):
            continue
        if title and _name(title) not in buckets:
            buckets[_name(title)] = {"titles": [title], "files": []}

    slots = asyncio.Semaphore(6)

    async def identify(name: str, bucket: dict) -> dict:
        tvdb_id = ids.get(name) or await asyncio.to_thread(
            _folder_tvdb_id, root, bucket["titles"]
        )
        async with slots:
            metadata = await show_metadata(_display_title(bucket["titles"]), tvdb_id)
        return {
            "name": name,
            "titles": bucket["titles"],
            "files": bucket["files"],
            "tvdb_id": metadata.get("tvdb_id") or tvdb_id,
            "metadata": metadata,
        }

    identified = await asyncio.gather(*(identify(n, b) for n, b in buckets.items()))

    # Second pass. Two folders can normalise differently and still be one
    # show; their AniDB entry is what says so, or failing that the TVDB id.
    # Without either there is nothing better than the name, so those stay
    # separate rather than guess.
    merged: dict[object, dict] = {}
    placeholders = []
    for item in identified:
        if not item["files"]:
            placeholders.append(item)  # a tracked show with nothing on disk yet
            continue
        aids = Counter(row["anidb_id"] for row in item["files"] if row.get("anidb_id"))
        # The entry most of its files are; a tie goes to the older entry.
        primary = min(aids, key=lambda aid: (-aids[aid], aid)) if aids else None
        identity = f"anidb:{primary}" if primary else item["tvdb_id"] or f"name:{item['name']}"
        slot = merged.setdefault(
            identity,
            {"titles": [], "files": [], "tvdb_id": item["tvdb_id"], "metadata": {}, "aids": Counter()},
        )
        slot["titles"].extend(item["titles"])
        slot["files"].extend(item["files"])
        slot["aids"].update(aids)
        if item["metadata"] and not slot["metadata"]:
            slot["metadata"] = item["metadata"]
    covered = {slot["tvdb_id"] for slot in merged.values() if slot["tvdb_id"]}
    for item in placeholders:
        if item["tvdb_id"] and item["tvdb_id"] in covered:
            continue
        merged.setdefault(
            item["tvdb_id"] or f"name:{item['name']}",
            {
                "titles": item["titles"],
                "files": [],
                "tvdb_id": item["tvdb_id"],
                "metadata": item["metadata"],
                "aids": Counter(),
            },
        )
    for slot in merged.values():
        slot["titles"] = sorted(dict.fromkeys(slot["titles"]))
        # Not TVDB's name for an AniDB card: TVDB calls Bleach and its sequel
        # both "Bleach", and the key must tell the two cards apart.
        slot["key"] = _display_title(
            slot["titles"],
            "" if slot["aids"] else str(slot["metadata"].get("english_title") or ""),
        )
    known = getattr(table, "anime", {}) if table is not None else {}

    async def anidb_card(slot: dict, show_roster: list) -> dict:
        """The AniDB entries behind a card: display order, episode lists, tab labels."""
        aids = list(slot["aids"])
        rosters: dict[int, list | None] = {}
        for aid in aids:
            entry = catalog.entries.get(aid) if catalog is not None else None
            if entry is not None and entry.episodes:
                rosters[aid] = list(entry.episodes)
                continue
            anime = known.get(aid)
            if anime is None or not anime.tvdb_id:
                rosters[aid] = None
                continue
            roster = show_roster if anime.tvdb_id == slot["tvdb_id"] else []
            if not roster and discover.is_configured():
                with suppress(Exception):
                    roster = await episode_roster(anime.tvdb_id)
            family = table.by_tvdb.get(anime.tvdb_id, []) if table is not None else []
            rosters[aid] = tvdb_slice(anime, roster or [], family) or None

        def first_aired(aid: int) -> str:
            dates = [ep.aired for ep in rosters.get(aid) or [] if ep.aired]
            return min(dates) if dates else "9999"

        def folders_of(aid: int) -> set[str]:
            return {str(row.get("season") or "") for row in slot["files"] if row.get("anidb_id") == aid}

        order = sorted(aids, key=lambda aid: (first_aired(aid), min(folders_of(aid), default=""), aid))
        labels = {}
        for aid in order:
            folders = folders_of(aid) - {""}
            entry = catalog.entries.get(aid) if catalog is not None else None
            anime = known.get(aid)
            name = (entry.title if entry else None) or (
                (anime.english_title or anime.title) if anime else f"AniDB {aid}"
            )
            # A cour kept in its own season folder is labelled by that folder.
            labels[aid] = next(iter(folders)) if len(order) > 1 and len(folders) == 1 else name
        return {"order": order, "rosters": rosters, "labels": labels}

    if only_key is not None:
        wanted = _name(only_key)
        merged = {
            identity: slot
            for identity, slot in merged.items()
            if wanted == _name(slot["key"]) or wanted in {_name(t) for t in slot["titles"]}
        }

    async def build(slot: dict) -> dict:
        metadata = slot["metadata"]
        tvdb_id = slot["tvdb_id"]
        files = slot["files"]
        title = slot["key"]
        roster = []
        if tvdb_id and discover.is_configured():
            with suppress(Exception):
                roster = await episode_roster(tvdb_id)
        card = await anidb_card(slot, roster) if slot["aids"] else None
        if card is not None:
            rows = prepare_anidb_rows(files, card["order"], table)
            # Keyed as the rows are numbered: the tab, then the AniDB episode.
            codecs = await asyncio.to_thread(probed_codecs, rows)
            dubbed = await asyncio.to_thread(german_dubbed_episodes, rows)
            merged_episodes = merge_episodes_anidb(
                rows,
                [(aid, card["labels"][aid], card["rosters"][aid]) for aid in card["order"]],
                codecs=codecs,
                german_dubbed=dubbed,
            )
            numbering = "anidb"
        else:
            codecs = {
                **codecs_by_series.get(str(tvdb_id), {}),
                **await asyncio.to_thread(probed_codecs, files),
            }
            dubbed = await asyncio.to_thread(german_dubbed_episodes, files)
            numbering = numbering_for(prefs, tvdb_id=tvdb_id, key=title)
            ended = str(metadata.get("status", "")).casefold() == "ended"
            if numbering == "season":
                merged_episodes = merge_episodes(
                    files, roster, ended=ended, codecs=codecs, german_dubbed=dubbed
                )
            else:
                merged_episodes = merge_episodes_absolute(
                    files,
                    roster,
                    ended=ended,
                    flat=numbering == "absolute_flat",
                    codecs=codecs,
                    german_dubbed=dubbed,
                )
        display_title = str(metadata.get("english_title") or title)
        source_title = await second_name(tvdb_id, display_title, source_titles.get(str(tvdb_id), ""))
        poster_url = metadata.get("poster_url")
        year = metadata.get("year")
        anidb_id = None
        if card is not None:
            # The first entry names the card and lends it its cover: AniDB's
            # own, from Shoko or else anime-offline-database, before TVDB's,
            # which is the same for every season of a show.
            anidb_id = card["order"][0]
            entry = catalog.entries.get(anidb_id) if catalog is not None else None
            anime = known.get(anidb_id)
            display_title = (
                (entry.title if entry else None)
                or ((anime.english_title or anime.title) if anime else None)
                or display_title
            )
            if anime is not None and anime.title != display_title:
                source_title = anime.title
            poster_url = (entry.poster_url if entry else None) or anidb_art.cover(anidb_id) or poster_url
            aired = [ep.aired for ep in card["rosters"].get(anidb_id) or [] if ep.aired]
            if aired:
                year = int(min(aired)[:4])
        # TVDB counts seasons, AniDB has an entry for each (or for each cour):
        # Anime-Lists says which entry each season is.
        season_anidb = _season_entries(table, tvdb_id)
        auto_ids: list[int] = []
        if card is None:
            # Nothing on disk to tell: the entry the user linked its folder to,
            # else the show's seasons as Anime-Lists maps them.
            anidb_id = next((links[name] for name in slot["titles"] if name in links), None)
            if anidb_id is None and season_anidb:
                auto_ids = [row["anidb_id"] for rows in season_anidb.values() for row in rows]
                anidb_id = auto_ids[0]
        result = {
            "key": title,
            # Every folder behind this one card, so the page can ask for the
            # right files back after two of them were merged.
            "folders": slot["titles"],
            "title": display_title,
            "source_title": source_title,
            "anidb_id": anidb_id,
            "anidb_ids": card["order"] if card is not None else auto_ids,
            # Taken from Anime-Lists rather than from files or the user.
            "anidb_auto": bool(auto_ids),
            "season_anidb": season_anidb,
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
            "tvdb_id": tvdb_id,
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
        }
        if card is not None:
            # A card of AniDB entries: its tabs are the entries, or -- one
            # entry kept in season folders -- that entry's folders.
            def entry_link(aid: int) -> list[dict]:
                entry = catalog.entries.get(aid) if catalog is not None else None
                anime = known.get(aid)
                name = (entry.title if entry else None) or (
                    (anime.english_title or anime.title) if anime else f"AniDB {aid}"
                )
                return [{"anidb_id": aid, "title": name}]

            tabs = {row["season_number"] for row in result["episodes"] if row["season_number"] is not None}
            result["season_anidb"] = (
                {str(tab): entry_link(card["order"][0]) for tab in tabs}
                if len(card["order"]) == 1
                else {str(index): entry_link(aid) for index, aid in enumerate(card["order"], start=1)}
            )
        elif numbering != "season":
            # Arc folders of an absolute count are not TVDB's seasons.
            result["season_anidb"] = {}
        if not include_episodes:
            result["episodes"] = []
        return result

    shows = await asyncio.gather(*(build(slot) for slot in merged.values()))
    await asyncio.to_thread(flush_persistent_cache)
    return sorted(shows, key=lambda row: row["title"].casefold())


async def queue_covers(rows: list[dict]) -> list[dict]:
    slots = asyncio.Semaphore(6)

    async def metadata_for(raw: str, title: str) -> tuple[str, dict]:
        async with slots:
            metadata = await show_metadata(title, int(raw))
        return raw, metadata

    # Hundreds of episode jobs commonly share one TVDB series. Fetch each
    # series once, then fan the result out to its rows instead of racing the
    # same provider/cache key many times.
    examples: dict[str, str] = {}
    for row in rows:
        raw = str(row.get("tvdb_id") or "")
        if raw.isdigit():
            examples.setdefault(raw, row["title"])
    metadata = dict(
        await asyncio.gather(*(metadata_for(raw, title) for raw, title in examples.items()))
    )
    for row in rows:
        item = metadata.get(str(row.get("tvdb_id") or ""))
        if item is None:
            continue
        row["poster_url"] = item.get("poster_url")
        row["series_title"] = item.get("english_title")
    return rows


async def enrich_review_rows(rows: list[dict], catalog: Any = None) -> list[dict]:
    """Attach canonical artwork without making the policy ledger provider-dependent.

    A card that is one AniDB entry shows that entry's cover -- from Shoko, else
    anime-offline-database -- since TVDB has one poster for every season of a
    show. ``catalog`` is Shoko's (see shoko_catalog), when it can be read.
    """
    from bankai.metadata import anidb_art

    mappings = await asyncio.to_thread(erai._load_mappings)
    slots = asyncio.Semaphore(6)

    async def enrich(row: dict) -> None:
        # A card is a whole show, and a TVDB choice is saved per season: any
        # season's choice identifies the show.
        saved = next(
            (
                mappings[key]
                for key in (row["key"], *row.get("keys", ()))
                if mappings.get(key, {}).get("tvdb_id")
            ),
            mappings.get(row["key"], {}),
        )
        async with slots:
            metadata = await show_metadata(
                row["source_title"], saved.get("tvdb_id") or row.get("tvdb_id")
            )
        if row.get("anidb_id"):
            # One AniDB entry: its own name. TVDB names the whole show, the
            # same for every season, so it only lends the cover here.
            row["title"] = row.get("english_title") or row.get("anidb_title") or row["source_title"]
        else:
            row["title"] = metadata.get("english_title") or row["source_title"]
            row["year"] = metadata.get("year")
        row["tvdb_id"] = metadata.get("tvdb_id") or saved.get("tvdb_id") or row.get("tvdb_id")
        anidb_cover = None
        if str(row.get("anidb_id") or "").isdigit():
            entry = catalog.entries.get(int(row["anidb_id"])) if catalog is not None else None
            anidb_cover = (entry.poster_url if entry else None) or anidb_art.cover(row["anidb_id"])
        row["poster_url"] = anidb_cover or metadata.get("poster_url") or row.get("anidb_poster_url")

    await asyncio.gather(*(enrich(row) for row in rows))
    return rows
