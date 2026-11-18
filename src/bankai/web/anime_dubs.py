"""German dubs in the anime library: which collections have German audio, for tvshow.nfo's tag.

Jellyfin has read every episode's audio tracks, but cannot filter shows by an
audio language. So a collection with German audio on any of its episodes gets
the tag ``German Dub`` in its tvshow.nfo (anime_layout), and a client filters
by that tag (ZonTV's Shows page). How many of its episodes have it, the client
counts itself: a dub of only some seasons (Frieren, 10 of 38) still counts.

What Jellyfin reports is asked for every few hours in the maintenance pass
(all anime episodes with their tracks: about 27 MB for 6,000 episodes, so not
more often) and kept in anime_dubs.json by collection. A collection's tag
changing is part of its layout fingerprint (anime_maintenance), so only those
collections get their tvshow.nfo written anew, and Jellyfin is told.
"""

from __future__ import annotations

import json
import time
from pathlib import Path, PurePosixPath
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

TAG = "German Dub"
# The audio languages that are German, as Jellyfin and the files write them.
GERMAN = {"ger", "deu", "de", "german"}
# Asked of Jellyfin again after this.
INTERVAL_SECONDS = 6 * 3600.0
# Episodes per request: Jellyfin's answer stays a few MB.
_PAGE = 500

_CACHE: tuple[float, dict[str, Any]] | None = None


def _path() -> Path:
    return bgjobs.jobs_root().parent / "anime_dubs.json"


def load() -> dict[str, Any]:
    """``{"at": <epoch>, "folders": {<collection folder>: [german episodes, episodes]}}``."""
    global _CACHE
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {"at": 0.0, "folders": {}}
    if _CACHE is not None and _CACHE[0] == mtime:
        return _CACHE[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = {"at": float(data.get("at") or 0.0), "folders": dict(data.get("folders") or {})}
    _CACHE = (mtime, data)
    return data


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _german(item: dict[str, Any]) -> bool:
    return any(
        stream.get("Type") == "Audio" and str(stream.get("Language") or "").casefold() in GERMAN
        for stream in item.get("MediaStreams") or []
    )


def count(items: list[dict[str, Any]], locations: list[str]) -> dict[str, list[int]]:
    """German episodes and episodes by collection folder (the first part of the path under the
    library). Specials (season 0) are left out: a dubbed show's extras are often not."""
    from bankai.web.jellyfin import _jellyfin_relative

    folders: dict[str, list[int]] = {}
    for item in items:
        path = item.get("Path")
        if not path or item.get("ParentIndexNumber") == 0:
            continue
        for location in locations:
            rel = _jellyfin_relative(str(path), str(location))
            if rel and rel != ".":
                tally = folders.setdefault(PurePosixPath(rel).parts[0], [0, 0])
                tally[0] += _german(item)
                tally[1] += 1
                break
    return folders


async def refresh(*, force: bool = False) -> dict[str, Any] | None:
    """Ask Jellyfin again when the last answer is old (or ``force``); None when it was not asked."""
    from bankai.web import jellyfin

    if not force and time.time() - load()["at"] < INTERVAL_SECONDS:
        return None
    if not jellyfin.configured():
        return None
    async with jellyfin._client() as client:
        library = next((row for row in await jellyfin._libraries(client) if jellyfin._is_anime(row)), None)
        if library is None:
            raise RuntimeError("no anime library in Jellyfin")
        items: list[dict[str, Any]] = []
        while True:
            response = await client.get(
                "/Items",
                params={
                    "ParentId": library["ItemId"],
                    "Recursive": "true",
                    "IncludeItemTypes": "Episode",
                    "Fields": "Path,MediaStreams",
                    "EnableImages": "false",
                    "EnableUserData": "false",
                    "StartIndex": len(items),
                    "Limit": _PAGE,
                },
            )
            response.raise_for_status()
            page = response.json().get("Items") or []
            # Only what is counted is kept, not every track of 6,000 episodes.
            items += [
                {"Path": it.get("Path"), "ParentIndexNumber": it.get("ParentIndexNumber"),
                 "MediaStreams": [s for s in it.get("MediaStreams") or [] if s.get("Type") == "Audio"]}
                for it in page
            ]
            if len(page) < _PAGE:
                break
    data = {"at": time.time(), "folders": count(items, list(library.get("Locations") or []))}
    _save(data)
    dubbed = sum(1 for german, _total in data["folders"].values() if german)
    log.info("[anime dubs] %d episodes asked; %d of %d collections have German audio", len(items), dubbed, len(data["folders"]))
    return data


def tally(folder: str | None) -> tuple[int, int]:
    """German episodes and episodes of a collection's folder, as last reported."""
    german, total = load()["folders"].get(folder or "", [0, 0])
    return int(german), int(total)


def tags(folder: str | None) -> list[str]:
    """The tags a collection's tvshow.nfo carries for its audio: ``German Dub`` or none."""
    return [TAG] if tally(folder)[0] else []


__all__ = ["GERMAN", "TAG", "count", "load", "refresh", "tags", "tally"]
