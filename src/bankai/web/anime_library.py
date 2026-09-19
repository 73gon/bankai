"""Show-level Anime library presentation and cached TVDB artwork identity."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
import time
import unicodedata
from contextlib import suppress
from dataclasses import asdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from bankai.cli import bgjobs
from bankai.metadata import anime_mapping
from bankai.metadata.tvdb import TVDBEpisode
from bankai.processor.anime import _tvdb_episode_map
from bankai.processor.naming import sanitise
from bankai.torrent.matcher import parse_se
from bankai.web import anime, discover, erai

_VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".webm"}
_CACHE: dict[str, tuple[float, dict]] = {}
_PERSISTENT_CACHE: dict | None = None
_PERSISTENT_DIRTY = False
_PERSISTENT_LOCK = threading.RLock()
_PERSISTENT_TTL_SECONDS = 24 * 60 * 60


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


def _persistent_get(key: str):
    hit = _persistent_data().get(key)
    if not isinstance(hit, dict) or time.time() - float(hit.get("saved_at", 0)) >= _PERSISTENT_TTL_SECONDS:
        return None
    return hit.get("value")


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
        tmp = path.with_suffix(".tmp")
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

    Nothing is the right answer more often than it looks. "Akame ga Kill!" is
    already the romaji, so repeating it under itself says nothing; the page
    compared the two exactly, which would still have printed a second line
    over a stray exclamation mark.
    """
    candidate = erai or await romaji_name(tvdb_id)
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


async def show_metadata(title: str, tvdb_id: int | None = None) -> dict:
    key = f"id:{tvdb_id}" if tvdb_id else _name(title)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    # Versioned: entries cached before the season suffix was understood hold an
    # empty result that would otherwise be served for another day.
    persisted = _persistent_get(f"metadata:v2:{key}")
    if isinstance(persisted, dict):
        _CACHE[key] = (time.time(), persisted)
        return persisted
    metadata = {}
    if discover.is_configured():
        try:
            if tvdb_id:
                metadata = asdict(await anime.series_metadata(tvdb_id))
            else:
                query = _search_title(title)
                candidates = await anime.tvdb_candidates(query)
                exact = [
                    item
                    for item in candidates
                    if item.kind == "show"
                    and _name(query)
                    in {
                        _name(item.english_title),
                        _name(item.japanese_title or ""),
                        *(_name(alias) for alias in item.aliases),
                    }
                ]
                if len(exact) == 1:
                    metadata = asdict(exact[0])
        except Exception:
            pass  # Library browsing remains available during provider outages.
    _CACHE[key] = (time.time(), metadata)
    # A miss is not an answer. Persisting it would hold the show at its romaji
    # name with no cover for a day, including through a provider outage.
    if metadata:
        _persistent_put(f"metadata:v2:{key}", metadata)
    return metadata


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
    persisted = _persistent_get(key)
    if isinstance(persisted, list):
        rows = [TVDBEpisode(**row) for row in persisted if isinstance(row, dict)]
        _CACHE[key] = (time.time(), rows)
        return rows
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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(path)


def _file_identity(path: Path) -> tuple[int, int] | None:
    """Size and mtime, so a replaced episode is probed again."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (int(stat.st_size), int(stat.st_mtime))


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
    for path in root.rglob("*"):
        if path.suffix.casefold() not in _VIDEO_SUFFIXES or not path.is_file():
            continue
        identity = _file_identity(path)
        if identity is None:
            continue
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
        if tvdb_id in titles:
            continue
        release = releases.get(str(row.get("info_hash") or ""))
        if not release:
            continue
        source = anime.clean_release_title(str(release.get("title") or ""))
        if source:
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


async def group_shows(
    entries: list[dict],
    root: Path,
    *,
    include_episodes: bool = True,
    only_key: str | None = None,
) -> list[dict]:
    """One card per show, however many folders and spellings it arrived under.

    Grouping used to key on the raw folder name, so a show sitting in two
    folders got two cards -- "Show" beside "Show (2024)", or a romaji folder
    beside its English one. Normalising the name only ever decided whether to
    add an empty card for a tracked title; it never merged two real folders.

    Identity is now decided twice. The normalised name catches the pairs that
    differ in punctuation or a trailing year, and the TVDB id catches the
    pairs that share no spelling at all.
    """
    buckets: dict[str, dict] = {}
    for entry in entries:
        season, episode = parse_se(entry["name"]) or (None, None)
        bucket = buckets.setdefault(_name(entry["series"]), {"titles": [], "files": []})
        if entry["series"] not in bucket["titles"]:
            bucket["titles"].append(entry["series"])
        bucket["files"].append({**entry, "season_number": season, "episode": episode})

    ids = await asyncio.to_thread(known_ids)
    # One read of the release state for the whole page, not one per show: it
    # is a fourteen megabyte file, and per show it cost twenty seconds.
    state = await asyncio.to_thread(erai._load_state)
    codecs_by_series = codec_index(state)
    source_titles = erai_source_titles(state)
    tracked = state.get("series", {})

    # A tracked show with nothing on disk yet still gets a card.
    for record in tracked.values():
        title = record.get("english_title")
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
    # series; the TVDB id is what says so. Without an id there is nothing
    # better than the name, so those stay separate rather than guess.
    merged: dict[object, dict] = {}
    for item in identified:
        identity = item["tvdb_id"] or f"name:{item['name']}"
        slot = merged.setdefault(
            identity,
            {"titles": [], "files": [], "tvdb_id": item["tvdb_id"], "metadata": {}},
        )
        slot["titles"].extend(item["titles"])
        slot["files"].extend(item["files"])
        if item["metadata"] and not slot["metadata"]:
            slot["metadata"] = item["metadata"]
    for slot in merged.values():
        slot["titles"] = sorted(dict.fromkeys(slot["titles"]))
        slot["key"] = _display_title(
            slot["titles"], str(slot["metadata"].get("english_title") or "")
        )

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
        codecs = {
            **codecs_by_series.get(str(tvdb_id), {}),
            **await asyncio.to_thread(probed_codecs, files),
        }
        dubbed = await asyncio.to_thread(german_dubbed_episodes, files)
        merged_episodes = merge_episodes(
            files,
            roster,
            ended=str(metadata.get("status", "")).casefold() == "ended",
            codecs=codecs,
            german_dubbed=dubbed,
        )
        result = {
            "key": title,
            # Every folder behind this one card, so the page can ask for the
            # right files back after two of them were merged.
            "folders": slot["titles"],
            "title": metadata.get("english_title") or title,
            "source_title": await second_name(
                tvdb_id,
                str(metadata.get("english_title") or title),
                source_titles.get(str(tvdb_id), ""),
            ),
            "avc_count": sum(
                1
                for row in merged_episodes["episodes"]
                # A German dub is irreplaceable, so it is not on offer.
                if row.get("codec") == "avc" and not row.get("german_dub")
            ),
            "hevc_count": sum(
                1 for row in merged_episodes["episodes"] if row.get("codec") == "hevc"
            ),
            # Counted separately from avc_count, which is what the upgrade can
            # actually offer to replace.
            "german_dub_count": sum(
                1 for row in merged_episodes["episodes"] if row.get("german_dub")
            ),
            "tvdb_id": tvdb_id,
            "year": metadata.get("year"),
            "poster_url": metadata.get("poster_url"),
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


async def enrich_review_rows(rows: list[dict]) -> list[dict]:
    """Attach canonical artwork without making the policy ledger provider-dependent."""
    mappings = await asyncio.to_thread(erai._load_mappings)
    slots = asyncio.Semaphore(6)

    async def enrich(row: dict) -> None:
        saved = mappings.get(row["key"], {})
        async with slots:
            metadata = await show_metadata(row["source_title"], saved.get("tvdb_id"))
        row["title"] = metadata.get("english_title") or row["source_title"]
        row["tvdb_id"] = metadata.get("tvdb_id") or saved.get("tvdb_id")
        row["year"] = metadata.get("year")
        row["poster_url"] = metadata.get("poster_url")

    await asyncio.gather(*(enrich(row) for row in rows))
    return rows
