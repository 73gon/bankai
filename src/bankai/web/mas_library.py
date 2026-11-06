"""The Movies & Shows library: what sits on the server, presented as cards.

The anime library already solved the hard parts of this -- collapsing a show
that exists under more than one folder name, merging what is on disk against
a TMDB roster so missing episodes are visible, and resolving artwork without
re-asking the provider for every card. Those helpers are not anime-specific
and are reused here rather than grown a second time.

What is deliberately *not* reused is everything downstream of the Erai
release state: encodes and held releases mean nothing to a film ripped by
hand. This module walks the configured roots, and reads each file's quality and
audio from the track inventory.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.metadata.tmdb_titles import Episode
from bankai.torrent.matcher import parse_se
from bankai.web import direct, discover, library_walk, track_cleanup
from bankai.web.anime_library import (
    _display_title,
    _name,
    flush_persistent_cache,
    merge_episodes,
    show_info,
    show_metadata,
)

# Shows and films are resolved concurrently but not without limit: each miss
# is a provider call, and a first scan of a large library is all misses.
_SLOTS = 6


def invalidate() -> None:
    """Have the next read check the disk first, rather than the held tree."""
    library_walk.mark_stale()


def _walk(roots: list[str | Path], *, use_cache: bool = True) -> list[dict]:
    """Every video file under ``roots``, tagged with the folder it sits in.

    A title is the first path segment when the file is nested and the file's
    own name when it is not, because a film is as likely to be a bare
    ``Title (Year).mkv`` as a folder containing one. The walk itself is the
    held tree shared with every other library view; the page's Rescan button
    checks it against the disk.
    """
    return [
        {**entry, "staged": False, "stage": "transferred", "transfer_status": "done"}
        for entry in library_walk.files(roots, rescan=not use_cache)
    ]


def quality(video: dict | None) -> str | None:
    """A picture's size as a release names it: 2160p, 1080p, 720p, else SD. By width
    first: a 2.39:1 film at 1920x800 is 1080p."""
    if not video:
        return None
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width >= 3200 or height >= 1800:
        return "2160p"
    if width >= 1700 or height >= 1000:
        return "1080p"
    if width >= 1200 or height >= 700:
        return "720p"
    return "SD" if width or height else None


def _tag(entries: list[dict]) -> None:
    """Each file's quality, whether it carries German audio and whether it has English
    subtitles, from the track inventory (never probed here: what it has not read yet is None).
    English subtitles in a file beside the video count, as in a followed show's drawer: the
    bar called Dexter S03-S05 "without English subtitles" for the ``.srt`` beside each episode."""
    from bankai.web.media import _is_german

    inventory = track_cleanup.load_inventory()
    listed = library_walk.lister(sorted({entry["root"] for entry in entries}))
    for entry in entries:
        found = inventory.get(entry["path"]) or {}
        tracks = None if found.get("unreadable") else found.get("tracks")
        entry["quality"] = quality(found.get("video"))
        audio = [track for track in tracks or [] if track.get("kind") == "audio"]
        entry["english_subs"] = None if tracks is None else (
            _english_subtitles(tracks) or direct.english_subtitle_file(Path(entry["path"]), listed) is not None
        )
        if tracks is None:
            entry["german"] = None
        elif any(_is_german(track.get("lang") or None, track.get("name") or None) for track in audio):
            entry["german"] = True
        elif audio and all(_untagged(track) for track in audio):
            # No track says its language ("und", no title): the folder it was filed into
            # does -- "Agent Ranjid rettet die Welt" was English for a track tagged und.
            entry["german"] = not str(entry.get("root") or "").rstrip("/").endswith("_en")
        else:
            entry["german"] = False


def _english_subtitles(tracks: list[dict]) -> bool:
    """Whether a file has English subtitles for all of it: a subtitle track in English that is
    not forced (a forced one carries only the lines spoken in another language)."""
    for track in tracks:
        if track.get("kind") != "subtitles" or track.get("forced"):
            continue
        words = set(re.findall(r"[a-z]+", str(track.get("name") or "").casefold()))
        if "forced" in words:
            continue
        code = track_cleanup.language(track.get("lang"))
        if code == "eng" or (code == "und" and words & {"english", "eng"}):
            return True
    return False


def episode_strip(rows: list[dict]) -> list[dict]:
    """Each season's episodes in order, a letter each, for the library's episode bar.

    The episodes the show's count reads (seasons from 1: a missing special is no gap), so
    the bar lights where the library has the show. ``g`` German dub, ``e`` English with
    English subtitles, ``n`` English without them, ``u`` on disk, its tracks not read yet,
    ``s`` staged, ``m`` missing, ``t`` not aired.
    """
    seasons: dict[int, str] = {}
    for row in rows:
        number = row.get("season_number")
        if number is None or number < 1 or row.get("episode") is None:
            continue
        if row.get("missing"):
            state = "t" if row.get("tba") else "m"
        elif row.get("staged"):
            state = "s"
        elif row.get("german_dub") or row.get("german") is True:
            state = "g"
        elif row.get("german") is None:
            state = "u"
        else:
            state = "e" if row.get("english_subs") else "n"
        seasons[number] = seasons.get(number, "") + state
    return [{"label": f"Season {number}", "states": seasons[number]} for number in sorted(seasons)]


def _untagged(track: dict) -> bool:
    """An audio track that names no language: "und", blank, and no title saying one."""
    return str(track.get("lang") or "").strip().casefold() in {"", "und", "unknown", "mis", "zxx"} and not (
        track.get("name") or ""
    ).strip()


def _bucket(entries: list[dict], kind: str | None = None) -> dict[str, dict]:
    """Group files by title identity, not by the folder name they arrived in.

    A folder's year is part of its identity: "How to Train Your Dragon (2010)"
    and "(2025)" are two films, not one stored twice. A folder without a year
    joins the one dated title of its name, if there is exactly one. A folder
    matched by hand (``kind`` given) is grouped by the TMDB title it was matched
    to, so matching one of a wrong pair splits it off.

    The same film or series routinely sits under two of the configured roots
    -- a local disk and a remote one -- and under spellings that differ only
    by a year or a colon. One card each.
    """
    chosen = matches() if kind else {}
    buckets: dict[tuple, dict] = {}
    for entry in entries:
        folder = entry["series"]
        hand = _chosen(chosen, folder, kind) if kind else None
        if hand:
            key: tuple = ("tmdb", hand["tmdb_id"])
        else:
            year = _YEAR.search(folder)
            key = ("name", _name(folder), year[1] if year else None)
        bucket = buckets.setdefault(key, {"titles": [], "files": []})
        if folder not in bucket["titles"]:
            bucket["titles"].append(folder)
        bucket["files"].append(entry)
    # A folder without a year belongs to the one dated title of its name, when there is one.
    for key in [key for key in buckets if key[0] == "name" and key[2] is None]:
        dated = [other for other in buckets if other[0] == "name" and other[1] == key[1] and other[2] is not None]
        if len(dated) == 1:
            undated = buckets.pop(key)
            buckets[dated[0]]["titles"] += undated["titles"]
            buckets[dated[0]]["files"] += undated["files"]
    out: dict[str, dict] = {}
    for key, bucket in buckets.items():
        bucket["titles"] = sorted(dict.fromkeys(bucket["titles"]))
        out["|".join(str(part) for part in key)] = bucket
    return out


_YEAR = re.compile(r"\((\d{4})\)\s*$")


# -- titles matched by hand ------------------------------------------------------
#
# A folder without a year whose name TMDB has more than once ("Avatar The Last
# Airbender": 2005 and 2024) is not guessed at; it stays without a cover until
# it is matched by hand. Kept per folder name, so it survives a rescan.

_MATCH_LOCK = threading.Lock()


def _matches_path() -> Path:
    return bgjobs.jobs_root().parent / "mas_matches.json"


def matches() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_matches_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _match_key(title: str, kind: str) -> str:
    """A folder's key: its name, and its year when it has one ("How to Train Your Dragon (2025)"
    is matched apart from "(2010)")."""
    year = _YEAR.search(title)
    return f"{kind}:{_name(title)}" + (f"|{year[1]}" if year else "")


def _chosen(chosen: dict[str, dict[str, Any]], title: str, kind: str | None) -> dict[str, Any] | None:
    """A folder's hand match: by its name and year, else by its name alone."""
    if not kind:
        return None
    exact = chosen.get(_match_key(title, kind))
    if exact:
        return exact
    loose = chosen.get(f"{kind}:{_name(title)}")
    year = _YEAR.search(title)
    # Matched by name alone (before years were part of the key): not a dated folder of another year.
    if loose and year and loose.get("year") and int(loose["year"]) != int(year[1]):
        return None
    return loose


def set_match(title: str, kind: str, item: dict[str, Any] | None) -> None:
    """Match a folder to this TMDB title, or (``None``) go back to the automatic match."""
    if kind not in {"movie", "show"}:
        raise ValueError("kind must be movie or show")
    with _MATCH_LOCK:
        data = matches()
        if item is None:
            data.pop(_match_key(title, kind), None)
        else:
            data[_match_key(title, kind)] = {
                "tmdb_id": int(item["tmdb_id"]),
                "english_title": str(item.get("english_title") or title),
                "year": item.get("year"),
                "poster_url": item.get("poster_url"),
                "kind": kind,
                "status": item.get("status"),
                "by_hand": True,
            }
        path = _matches_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


async def _resolve(title: str, kind: str) -> dict:
    """Artwork and identity for one title, or an empty dict."""
    chosen = _chosen(matches(), title, kind)
    if chosen:
        return chosen
    with suppress(Exception):
        # Any title, not only anime: this is the Movies & Shows library.
        return await show_metadata(title, kind=kind)
    return {}


async def movies(roots: list[str | Path], *, rescan: bool = False) -> list[dict]:
    """One card per film on the server."""
    entries = await asyncio.to_thread(_walk, roots, use_cache=not rescan)
    await asyncio.to_thread(_tag, entries)
    buckets = _bucket(entries, "movie")
    slots = asyncio.Semaphore(_SLOTS)

    async def build(bucket: dict) -> dict:
        title = _display_title(bucket["titles"])
        async with slots:
            metadata = await _resolve(title, "movie")
        files = bucket["files"]
        film = max(files, key=lambda row: row["size"])
        known = [row["german"] for row in files if row["german"] is not None]
        return {
            "key": title,
            "folders": bucket["titles"],
            "title": metadata.get("english_title") or title,
            "tmdb_id": metadata.get("tmdb_id"),
            "year": metadata.get("year"),
            "poster_url": metadata.get("poster_url"),
            "metadata_available": bool(metadata.get("tmdb_id")),
            "matched_by_hand": bool(metadata.get("by_hand")),
            "file_count": len(files),
            # The film's own file, the largest: extras and samples say nothing of it.
            "quality": film["quality"],
            "german": any(known) if known else None,
            "size": sum(row["size"] for row in files),
            # The roots it was found under; two means it is stored twice.
            "roots": sorted({row["root"] for row in files}),
            "files": sorted(files, key=lambda row: row["rel_path"]),
        }

    cards = await asyncio.gather(*(build(bucket) for bucket in buckets.values()))
    return sorted(cards, key=lambda row: row["title"].casefold())


async def shows(
    roots: list[str | Path], *, include_episodes: bool = True, rescan: bool = False
) -> list[dict]:
    """One card per series, with what is on disk merged against TMDB."""
    walked = await asyncio.to_thread(_walk, roots, use_cache=not rescan)
    # Copied rather than annotated in place: the walk behind them is cached
    # and shared, and merge_episodes takes ownership of the rows it is given.
    entries = []
    for entry in walked:
        season, episode = parse_se(entry["name"]) or (None, None)
        # One file holding several episodes ("S03E18-E21"): all of them are on disk.
        numbers = direct.file_episode(entry["name"])
        last = numbers[2] if numbers and (numbers[0], numbers[1]) == (season, episode) else None
        entries.append({**entry, "season_number": season, "episode": episode, "episode_last": last})
    await asyncio.to_thread(_tag, entries)
    buckets = _bucket(entries, "show")
    slots = asyncio.Semaphore(_SLOTS)

    async def build(bucket: dict) -> dict:
        title = _display_title(bucket["titles"])
        async with slots:
            metadata = await _resolve(title, "show")
        tmdb_id = metadata.get("tmdb_id")
        info: dict = {}
        if tmdb_id and discover.is_configured():
            with suppress(Exception):
                info = await show_info(int(tmdb_id))
        roster = [Episode(**row) for row in info.get("episodes") or []]
        files = bucket["files"]
        merged = merge_episodes(
            files,
            roster,
            ended=str(info.get("status") or "").casefold() in {"ended", "canceled"},
            german_dubbed={
                (row["season_number"], number)
                for row in files if row["german"] and row["episode"] is not None
                for number in range(row["episode"], (row.get("episode_last") or row["episode"]) + 1)
            },
        )
        held = [row for row in merged["episodes"] if not row.get("missing") and row.get("path")]
        card = {
            "key": title,
            "folders": bucket["titles"],
            "title": metadata.get("english_title") or title,
            "tmdb_id": tmdb_id,
            "year": metadata.get("year"),
            "poster_url": metadata.get("poster_url"),
            "matched_by_hand": bool(metadata.get("by_hand")),
            "episode_count": len(files),
            "season_count": len(
                {
                    row["season_number"]
                    for row in merged["episodes"]
                    if row["season_number"] is not None
                }
            ),
            "size": sum(row["size"] for row in files),
            "roots": sorted({row["root"] for row in files}),
            **merged,
            # Episodes on disk by their audio: German, English only, not read yet.
            "german_count": sum(row.get("german") is True for row in held),
            "english_count": sum(row.get("german") is False for row in held),
            # Where the library has the show, and in which language: the episode bar.
            "strip": episode_strip(merged["episodes"]),
        }
        if not include_episodes:
            card["episodes"] = []
        return card

    cards = await asyncio.gather(*(build(bucket) for bucket in buckets.values()))
    return sorted(cards, key=lambda row: row["title"].casefold())


async def library(
    movie_roots: list[str | Path],
    show_roots: list[str | Path],
    *,
    include_episodes: bool = False,
    rescan: bool = False,
) -> dict:
    """Both halves of the Movies & Shows library."""
    films, series = await asyncio.gather(
        movies(movie_roots, rescan=rescan),
        shows(show_roots, include_episodes=include_episodes, rescan=rescan),
    )
    await asyncio.to_thread(flush_persistent_cache)
    return {"movies": films, "shows": series}
