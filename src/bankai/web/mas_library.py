"""The Movies & Shows library: what sits on the server, presented as cards.

The anime library already solved the hard parts of this -- collapsing a show
that exists under more than one folder name, merging what is on disk against
a TVDB roster so missing episodes are visible, and resolving artwork without
re-asking the provider for every card. Those helpers are not anime-specific
and are reused here rather than grown a second time.

What is deliberately *not* reused is everything downstream of the Erai
release state: encodes, German dubs and held releases mean nothing to a film
ripped by hand. This module walks the configured roots and nothing else.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path

from bankai.config import get_settings
from bankai.torrent.matcher import parse_se
from bankai.web import discover
from bankai.web.anime_library import (
    _display_title,
    _name,
    episode_roster,
    flush_persistent_cache,
    merge_episodes,
    show_metadata,
)

_VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".webm"}

# Shows and films are resolved concurrently but not without limit: each miss
# is a provider call, and a first scan of a large library is all misses.
_SLOTS = 6

# Walking every root is the expensive part, and the disk these libraries
# live on is the slowest thing in the system. Held for the configured TTL,
# the way the server library holds its own scan; the page's Rescan button
# asks for a fresh one.
_WALK_CACHE: dict[str, tuple[float, list[dict]]] = {}


def invalidate() -> None:
    """Forget the cached walk, so the next read touches the disk."""
    _WALK_CACHE.clear()


def _walk(roots: list[str | Path], *, use_cache: bool = True) -> list[dict]:
    """Every video file under ``roots``, tagged with the folder it sits in.

    A title is the first path segment when the file is nested and the file's
    own name when it is not, because a film is as likely to be a bare
    ``Title (Year).mkv`` as a folder containing one.
    """
    key = "|".join(str(root) for root in roots)
    if use_cache:
        hit = _WALK_CACHE.get(key)
        if hit and time.time() - hit[0] < get_settings().web.cache_ttl_seconds:
            return hit[1]
    entries: list[dict] = []
    for raw in roots:
        root = Path(raw)
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in _VIDEO_SUFFIXES:
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(root)
            except (OSError, ValueError):
                continue
            parts = relative.parts
            entries.append(
                {
                    "path": str(path),
                    "rel_path": str(relative),
                    "name": path.name,
                    "series": parts[0] if len(parts) > 1 else path.stem,
                    "season": parts[1] if len(parts) > 2 else None,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "root": str(root),
                    "staged": False,
                    "stage": "transferred",
                    "transfer_status": "done",
                }
            )
    _WALK_CACHE[key] = (time.time(), entries)
    return entries


def _bucket(entries: list[dict]) -> dict[str, dict]:
    """Group files by title identity, not by the folder name they arrived in.

    The same film or series routinely sits under two of the configured roots
    -- a local disk and a remote one -- and under spellings that differ only
    by a year or a colon. One card each.
    """
    buckets: dict[str, dict] = {}
    for entry in entries:
        bucket = buckets.setdefault(_name(entry["series"]), {"titles": [], "files": []})
        if entry["series"] not in bucket["titles"]:
            bucket["titles"].append(entry["series"])
        bucket["files"].append(entry)
    for bucket in buckets.values():
        bucket["titles"] = sorted(dict.fromkeys(bucket["titles"]))
    return buckets


async def _resolve(title: str, kind: str) -> dict:
    """Artwork and identity for one title, or an empty dict."""
    with suppress(Exception):
        return await show_metadata(title, None, kind=kind)
    return {}


async def movies(roots: list[str | Path], *, rescan: bool = False) -> list[dict]:
    """One card per film on the server."""
    buckets = _bucket(await asyncio.to_thread(_walk, roots, use_cache=not rescan))
    slots = asyncio.Semaphore(_SLOTS)

    async def build(bucket: dict) -> dict:
        title = _display_title(bucket["titles"])
        async with slots:
            metadata = await _resolve(title, "movie")
        files = bucket["files"]
        return {
            "key": title,
            "folders": bucket["titles"],
            "title": metadata.get("english_title") or title,
            "tvdb_id": metadata.get("tvdb_id"),
            "year": metadata.get("year"),
            "poster_url": metadata.get("poster_url"),
            "metadata_available": bool(metadata.get("tvdb_id")),
            "file_count": len(files),
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
    """One card per series, with what is on disk merged against TVDB."""
    walked = await asyncio.to_thread(_walk, roots, use_cache=not rescan)
    # Copied rather than annotated in place: the walk behind them is cached
    # and shared, and merge_episodes takes ownership of the rows it is given.
    entries = []
    for entry in walked:
        season, episode = parse_se(entry["name"]) or (None, None)
        entries.append({**entry, "season_number": season, "episode": episode})
    buckets = _bucket(entries)
    slots = asyncio.Semaphore(_SLOTS)

    async def build(bucket: dict) -> dict:
        title = _display_title(bucket["titles"])
        async with slots:
            metadata = await _resolve(title, "show")
        tvdb_id = metadata.get("tvdb_id")
        roster: list = []
        if tvdb_id and discover.is_configured():
            with suppress(Exception):
                roster = await episode_roster(int(tvdb_id))
        files = bucket["files"]
        merged = merge_episodes(
            files,
            roster,
            ended=str(metadata.get("status", "")).casefold() == "ended",
        )
        card = {
            "key": title,
            "folders": bucket["titles"],
            "title": metadata.get("english_title") or title,
            "tvdb_id": tvdb_id,
            "year": metadata.get("year"),
            "poster_url": metadata.get("poster_url"),
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
