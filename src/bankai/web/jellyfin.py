"""Telling Jellyfin what bankai changed in its libraries.

Jellyfin's monitor notices new and removed videos, and its scan new and
removed items. Neither reads a changed NFO file or a new ``-thumb.jpg`` of an
episode it already has: the new episode titles and the mid-episode pictures
waited for a refresh by hand. So whatever bankai writes beside an episode,
season or show is queued here by its path, and a scheduler pass asks Jellyfin
to refresh those items -- a normal refresh, which takes a new local picture
over one it grabbed itself.

A video bankai wrote anew (tracks taken out, encoded) is queued to be probed
again: a normal refresh keeps the tracks Jellyfin read before, which then
point at other tracks of the new file -- German audio became a subtitle and
played silent. Jellyfin also keeps the subtitles it extracted under each
track's number, whatever the file now holds there, so those are deleted first.

Jellyfin runs on Windows and sees the libraries as ``G:\\media\\movies`` and
the like; bankai sees ``/mnt/g/media/movies``. The anime library is matched
by what follows its folder and the anime root; the others by their drive.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web.state_lock import StateLock

log = get_logger(__name__)

# Refreshes asked for in one pass; the rest wait for the next.
_BATCH = 200
# Jellyfin's list of the library's items, by path, is fetched again after this.
_INDEX_SECONDS = 600.0

# Shared with the library worker's process (web.state_lock): its passes write these files too.
_LOCK = StateLock("jellyfin_queue")
_INDEX: tuple[float, dict[str, str]] | None = None
# The last time Jellyfin could not be asked and why, for the dashboard.
last_error: dict[str, object] = {}


def configured() -> bool:
    settings = get_settings().jellyfin
    return bool(settings.url and settings.api_key)


def _queue_path() -> Path:
    return bgjobs.jobs_root().parent / "jellyfin_refresh.json"


def _load() -> list[dict[str, Any]]:
    """The queue: ``{"path", "reprobe"}`` rows, the path as bankai sees it."""
    from bankai.web import anime_files

    try:
        rows = json.loads(_queue_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    loaded = []
    for row in rows:
        if isinstance(row, str):
            # An earlier queue's row: a path under the anime root.
            row = {"path": str(anime_files.anime_root() / row)}
        if isinstance(row, dict) and row.get("path"):
            loaded.append({"path": str(row["path"]), "reprobe": bool(row.get("reprobe"))})
    return loaded


def _save(rows: list[dict[str, Any]]) -> None:
    path = _queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def queue(paths: Iterable[Path], *, reprobe: bool = False) -> int:
    """Have Jellyfin refresh the items at these paths (a video, a season or show folder).

    ``reprobe`` for a video written anew: Jellyfin reads its tracks again.
    """
    if not configured():
        return 0
    wanted = [str(Path(path)) for path in paths if Path(path).is_absolute()]
    if not wanted:
        return 0
    with _LOCK:
        rows = _load()
        known = {row["path"]: row for row in rows}
        added = 0
        for path in dict.fromkeys(wanted):
            if path in known:
                known[path]["reprobe"] |= reprobe
                continue
            row = {"path": path, "reprobe": reprobe}
            rows.append(row)
            known[path] = row
            added += 1
        _save(rows)
    return added


def _client() -> httpx.AsyncClient:
    settings = get_settings().jellyfin
    return httpx.AsyncClient(
        base_url=settings.url.rstrip("/"),
        headers={"X-Emby-Token": settings.api_key, "Accept": "application/json"},
        timeout=60,
    )


def _jellyfin_relative(path: str, location: str) -> str | None:
    """An item's path after its library folder, written the way bankai writes it."""
    item, base = PureWindowsPath(path), PureWindowsPath(location)
    if "/" in path and "\\" not in path:
        item, base = PurePosixPath(path), PurePosixPath(location)  # type: ignore[assignment]
    try:
        return PurePosixPath(*item.relative_to(base).parts).as_posix()
    except ValueError:
        return None


async def _libraries(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/Library/VirtualFolders")
    response.raise_for_status()
    return response.json() or []


def _is_anime(row: dict[str, Any]) -> bool:
    """The anime library: the one named in the settings, else the one on the anime root's folder."""
    from bankai.web import anime_files

    wanted = get_settings().jellyfin.library
    if wanted:
        return str(row.get("Name")) == wanted
    folder = anime_files.anime_root().name.casefold()
    return any(PureWindowsPath(str(loc)).name.casefold() == folder
               or PurePosixPath(str(loc)).name.casefold() == folder
               for loc in row.get("Locations") or [])


async def catalog() -> list[dict[str, Any]]:
    """Every movie and series of the movie and show libraries but the anime one, with what
    their genres are made of (and their age rating): for genre_sync and age_ratings. BoxSets take theirs from their children and are
    left out, as are items in no library (a removed plugin's, until Jellyfin's next scan)."""
    rows: list[dict[str, Any]] = []
    async with _client() as client:
        for library in await _libraries(client):
            kind = library.get("CollectionType")
            if kind not in ("movies", "tvshows") or _is_anime(library):
                continue
            response = await client.get(
                "/Items",
                params={
                    "ParentId": library["ItemId"],
                    "Recursive": "true",
                    "IncludeItemTypes": "Movie" if kind == "movies" else "Series",
                    "Fields": "Genres,Tags,ProviderIds,Path,Settings,ProductionYear,OfficialRating",
                    "EnableImages": "false",
                    "EnableUserData": "false",
                },
            )
            response.raise_for_status()
            for item in response.json().get("Items") or []:
                rows.append({
                    "item_id": str(item["Id"]),
                    "type": str(item.get("Type") or ""),
                    "name": str(item.get("Name") or ""),
                    "year": item.get("ProductionYear"),
                    "library": str(library.get("Name") or ""),
                    "genres": list(item.get("Genres") or []),
                    "tags": list(item.get("Tags") or []),
                    "provider_ids": dict(item.get("ProviderIds") or {}),
                    "rating": item.get("OfficialRating"),
                    "locked_fields": list(item.get("LockedFields") or []),
                    "path": item.get("Path"),
                })
    return rows


def _local(location: str) -> Path | None:
    """A Jellyfin path as bankai sees it: ``G:\\media\\movies`` is ``/mnt/g/media/movies``."""
    windows = PureWindowsPath(location)
    if len(windows.drive) == 2:
        return Path("/mnt", windows.drive[0].lower(), *windows.parts[1:])
    return Path(location) if location.startswith("/") else None


async def _index(client: httpx.AsyncClient) -> dict[str, str]:
    """The movies, shows, seasons and episodes in Jellyfin, by their path as bankai sees it."""
    from bankai.web import anime_files

    global _INDEX
    if _INDEX is not None and time.monotonic() - _INDEX[0] < _INDEX_SECONDS:
        return _INDEX[1]
    wanted = get_settings().jellyfin.library
    libraries = await _libraries(client)
    if not any(_is_anime(row) for row in libraries):
        raise RuntimeError(f"no Jellyfin library {'named ' + wanted if wanted else 'on the anime folder'}")
    index: dict[str, str] = {}
    for library in libraries:
        anime = _is_anime(library)
        if not anime and library.get("CollectionType") not in ("movies", "tvshows"):
            continue
        response = await client.get(
            "/Items",
            params={
                "ParentId": library["ItemId"],
                "Recursive": "true",
                "IncludeItemTypes": "Series,Season,Episode" if anime else "Movie,Series,Season,Episode",
                "Fields": "Path",
                "EnableImages": "false",
                "EnableUserData": "false",
            },
        )
        response.raise_for_status()
        for item in response.json().get("Items") or []:
            path = item.get("Path")
            if not path:
                continue
            for location in library.get("Locations") or []:
                rel = _jellyfin_relative(str(path), str(location))
                root = anime_files.anime_root() if anime else _local(str(location))
                if rel and rel != "." and root is not None:
                    index[str(root / rel)] = str(item["Id"])
                    break
    _INDEX = (time.monotonic(), index)
    return index


async def _subtitle_cache(client: httpx.AsyncClient) -> Path | None:
    """Where Jellyfin keeps the subtitles it extracted, one folder per video; None out of reach."""
    response = await client.get("/System/Info")
    response.raise_for_status()
    data = _local(str(response.json().get("ProgramDataPath") or ""))
    folder = data / "data" / "subtitles" if data else None
    return folder if folder and folder.is_dir() else None


def _forget_subtitles(cache: Path | None, item: str) -> None:
    """Delete what Jellyfin extracted from a video's earlier file; it extracts the new one's again."""
    if cache is None:
        return
    try:
        name = str(uuid.UUID(item))
    except ValueError:
        return
    shutil.rmtree(cache / name[:2] / name, ignore_errors=True)


async def _known_parent(client: httpx.AsyncClient, path: Path, index: dict[str, str]) -> str | None:
    """The nearest folder above ``path`` Jellyfin has an item for: a show or season, else the library."""
    from bankai.web import anime_files

    for folder in path.parents:
        if str(folder) in index:
            return index[str(folder)]
    for library in await _libraries(client):
        for location in library.get("Locations") or []:
            root = anime_files.anime_root() if _is_anime(library) else _local(str(location))
            if root is not None and (path == root or root in path.parents):
                return str(library["ItemId"])
    return None


async def flush(*, limit: int = _BATCH) -> dict[str, int]:
    """Ask Jellyfin to refresh up to ``limit`` queued items."""
    global _INDEX
    with _LOCK:
        pending = _load()
    if not pending or not configured():
        return {"refreshed": 0, "remaining": len(pending)}
    taken, unknown, refreshed = pending[:limit], 0, 0
    try:
        async with _client() as client:
            index = await _index(client)
            if any(row["path"] not in index for row in taken):
                # Published since the list was fetched: look again once.
                _INDEX = None
                index = await _index(client)
            cache = await _subtitle_cache(client) if any(row["reprobe"] for row in taken) else None
            folders: dict[str, str] = {}
            for row in taken:
                item = index.get(row["path"])
                if item is None:
                    # Not in Jellyfin yet: the nearest folder it knows (the show, else the library)
                    # is refreshed recursively, which finds it -- its watcher misses files written
                    # over /mnt/g, and the next scan may be hours away.
                    unknown += 1
                    parent = await _known_parent(client, Path(row["path"]), index)
                    if parent is not None:
                        folders.setdefault(parent, row["path"])
                    continue
                if row["reprobe"]:
                    await asyncio.to_thread(_forget_subtitles, cache, item)
                response = await client.post(
                    f"/Items/{item}/Refresh",
                    params={
                        # A full refresh probes the file again; it still replaces no metadata.
                        "MetadataRefreshMode": "FullRefresh" if row["reprobe"] else "Default",
                        "ImageRefreshMode": "Default",
                        "ReplaceAllMetadata": "false",
                        "ReplaceAllImages": "false",
                    },
                )
                response.raise_for_status()
                refreshed += 1
            for parent in folders:
                response = await client.post(
                    f"/Items/{parent}/Refresh",
                    params={"Recursive": "true", "MetadataRefreshMode": "Default", "ImageRefreshMode": "Default",
                            "ReplaceAllMetadata": "false", "ReplaceAllImages": "false"},
                )
                response.raise_for_status()
                refreshed += 1
    except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
        message = str(exc) or type(exc).__name__
        log.warning("Jellyfin could not be told what changed: %s", message)
        since = last_error.get("since") if last_error else None
        last_error.update(message=message, at=time.time(), since=since or time.time())
        return {"refreshed": 0, "remaining": len(pending), "failed": 1}
    last_error.clear()
    with _LOCK:
        done = {row["path"] for row in taken}
        rest = [row for row in _load() if row["path"] not in done]
        _save(rest)
    return {"refreshed": refreshed, "not_in_jellyfin": unknown, "remaining": len(rest)}


__all__ = ["configured", "flush", "queue"]
