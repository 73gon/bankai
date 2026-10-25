"""AniList entries' ids elsewhere -- TMDB, TheTVDB -- from Fribb's anime-lists.

AniList keeps a poster and a banner of each entry and nothing more: no
backdrop, no logo, no landscape thumb, and nothing for the "Specials" season
bankai makes of a show's side stories. TMDB keeps all of them; this tells
which TMDB show (and which of its seasons) an AniList entry is.

The list (github.com/Fribb/anime-lists, ~8 MB) is fetched weekly and kept
condensed to what bankai reads: AniList id -> {tmdb_tv | tmdb_movie,
tmdb_season, tvdb}.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
_TTL = 7 * 86400

_LOADED: tuple[float, dict[int, dict]] | None = None
_REFRESH_LOCK = asyncio.Lock()


def _store() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "anilist_external.json"


def condense(raw: list) -> dict[str, dict]:
    """The list as bankai keeps it: only entries AniList knows, only the ids it reads."""
    out: dict[str, dict] = {}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        aid = item.get("anilist_id")
        if not isinstance(aid, int):
            continue
        row: dict[str, int] = {}
        tmdb = item.get("themoviedb_id")
        # Older rows give a bare number; the type then says which it is.
        if isinstance(tmdb, int):
            tmdb = {"movie" if item.get("type") == "MOVIE" else "tv": tmdb}
        if isinstance(tmdb, dict):
            for kind in ("tv", "movie"):
                value = tmdb.get(kind)
                if isinstance(value, list):
                    value = value[0] if value else None
                if isinstance(value, int):
                    row[f"tmdb_{kind}"] = value
        season = (item.get("season") or {}).get("tmdb") if isinstance(item.get("season"), dict) else None
        if isinstance(season, int):
            row["tmdb_season"] = season
        if isinstance(item.get("tvdb_id"), int):
            row["tvdb"] = item["tvdb_id"]
        if row:
            out[str(aid)] = row
    return out


def ids_all() -> dict[int, dict]:
    global _LOADED
    path = _store()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _LOADED is None or _LOADED[0] != mtime:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        _LOADED = (mtime, {int(key): value for key, value in raw.items() if str(key).isdigit()})
    return _LOADED[1]


def ids(anilist_id: object) -> dict:
    """{tmdb_tv | tmdb_movie, tmdb_season, tvdb} of an AniList entry; empty when unknown."""
    try:
        return ids_all().get(int(anilist_id), {})  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}


async def refresh(*, force: bool = False) -> bool:
    """Fetch and condense the list when the kept copy is a week old. True if renewed."""
    async with _REFRESH_LOCK:
        target = _store()
        try:
            fresh = time.time() - target.stat().st_mtime < _TTL
        except OSError:
            fresh = False
        if fresh and not force:
            return False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=20), follow_redirects=True) as client:
                response = await client.get(_URL)
                response.raise_for_status()
            rows = await asyncio.to_thread(condense, response.json())
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("could not refresh the AniList-TMDB id list: %s", exc)
            return False
        if not rows:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        tmp.replace(target)
        log.info("AniList-TMDB id list refreshed: %d entries", len(rows))
        return True


__all__ = ["condense", "ids", "ids_all", "refresh"]
