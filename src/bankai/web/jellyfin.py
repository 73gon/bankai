"""Telling Jellyfin what bankai changed in the anime library.

Jellyfin's monitor notices new and removed videos, and its scan new and
removed items. Neither reads a changed NFO file or a new ``-thumb.jpg`` of an
episode it already has: the new episode titles and the mid-episode pictures
waited for a refresh by hand. So whatever bankai writes beside an episode,
season or show is queued here by its path under the anime root, and a
scheduler pass asks Jellyfin to refresh those items -- a normal refresh, which
takes a new local picture over one it grabbed itself.

Jellyfin runs on Windows and sees the library as ``G:\\media\\shows_anime``;
bankai sees ``/mnt/g/media/shows_anime``. What follows the library's folder
is the same, so items are matched by that.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

# Refreshes asked for in one pass; the rest wait for the next.
_BATCH = 200
# Jellyfin's list of the library's items, by path, is fetched again after this.
_INDEX_SECONDS = 600.0

_LOCK = threading.Lock()
_INDEX: tuple[float, dict[str, str]] | None = None
# The last time Jellyfin could not be asked and why, for the dashboard.
last_error: dict[str, object] = {}


def configured() -> bool:
    settings = get_settings().jellyfin
    return bool(settings.url and settings.api_key)


def _queue_path() -> Path:
    return bgjobs.jobs_root().parent / "jellyfin_refresh.json"


def _load() -> list[str]:
    try:
        rows = json.loads(_queue_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(row) for row in rows] if isinstance(rows, list) else []


def _save(rows: list[str]) -> None:
    path = _queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _relative(path: Path) -> str | None:
    from bankai.web import anime_files

    try:
        return PurePosixPath(*path.relative_to(anime_files.anime_root()).parts).as_posix()
    except ValueError:
        return None


def queue(paths: Iterable[Path]) -> int:
    """Have Jellyfin refresh the items at these paths (an episode's video, a season or show folder)."""
    if not configured():
        return 0
    wanted = [rel for path in paths if (rel := _relative(Path(path))) and rel != "."]
    if not wanted:
        return 0
    with _LOCK:
        rows = _load()
        known = set(rows)
        added = [rel for rel in dict.fromkeys(wanted) if rel not in known]
        if added:
            _save(rows + added)
    return len(added)


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


async def _index(client: httpx.AsyncClient) -> dict[str, str]:
    """The anime library's shows, seasons and episodes in Jellyfin, by path under the library folder."""
    global _INDEX
    if _INDEX is not None and time.monotonic() - _INDEX[0] < _INDEX_SECONDS:
        return _INDEX[1]
    from bankai.web import anime_files

    wanted = get_settings().jellyfin.library
    folder = anime_files.anime_root().name.casefold()
    response = await client.get("/Library/VirtualFolders")
    response.raise_for_status()
    libraries: list[dict[str, Any]] = response.json() or []

    def ours(row: dict[str, Any]) -> bool:
        if wanted:
            return str(row.get("Name")) == wanted
        return any(PureWindowsPath(str(loc)).name.casefold() == folder
                   or PurePosixPath(str(loc)).name.casefold() == folder
                   for loc in row.get("Locations") or [])

    library = next((row for row in libraries if ours(row)), None)
    if library is None:
        raise RuntimeError(f"no Jellyfin library {'named ' + wanted if wanted else 'on the anime folder'}")
    response = await client.get(
        "/Items",
        params={
            "ParentId": library["ItemId"],
            "Recursive": "true",
            "IncludeItemTypes": "Series,Season,Episode",
            "Fields": "Path",
            "EnableImages": "false",
            "EnableUserData": "false",
        },
    )
    response.raise_for_status()
    index: dict[str, str] = {}
    for item in response.json().get("Items") or []:
        path = item.get("Path")
        if not path:
            continue
        for location in library.get("Locations") or []:
            rel = _jellyfin_relative(str(path), str(location))
            if rel and rel != ".":
                index[rel] = str(item["Id"])
                break
    _INDEX = (time.monotonic(), index)
    return index


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
            if any(rel not in index for rel in taken):
                # Published since the list was fetched: look again once.
                _INDEX = None
                index = await _index(client)
            for rel in taken:
                item = index.get(rel)
                if item is None:
                    # Not in Jellyfin (yet): its first scan reads the NFO and picture anyway.
                    unknown += 1
                    continue
                response = await client.post(
                    f"/Items/{item}/Refresh",
                    params={
                        "MetadataRefreshMode": "Default",
                        "ImageRefreshMode": "Default",
                        "ReplaceAllMetadata": "false",
                        "ReplaceAllImages": "false",
                    },
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
        done = set(taken)
        rest = [rel for rel in _load() if rel not in done]
        _save(rest)
    return {"refreshed": refreshed, "not_in_jellyfin": unknown, "remaining": len(rest)}


__all__ = ["configured", "flush", "queue"]
