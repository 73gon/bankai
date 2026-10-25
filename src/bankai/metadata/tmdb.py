"""TMDB's artwork of a show or film: posters, backdrops, logos, season posters.

Read for the anime library (see web.anime_artwork), which AniList alone
leaves thin. Kept on disk per TMDB id for a month: artwork barely changes,
and the layout reads it on every pass without asking TMDB.

``metadata.tmdb_api_key`` takes either kind of TMDB credential: a v4 read
access token (a JWT, sent as a bearer token) or a v3 API key (a parameter).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

_API = "https://api.themoviedb.org/3"
_IMAGES = "https://image.tmdb.org/t/p/"
_TTL = 30 * 86400
# Kept per kind, best first: enough to choose from, not TMDB's hundreds.
_KEEP = 40
# Languages asked for: English, Japanese, German, and none (textless).
_LANGUAGES = "en,ja,de,null"

_LOADED: tuple[float, dict[str, dict]] | None = None
# Who could not be asked this pass, and what they said: the dashboard shows it.
last_error: dict[str, str] = {}


def _store() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "tmdb_images.json"


def configured() -> bool:
    return bool(get_settings().metadata.tmdb_api_key.strip())


def _auth() -> tuple[dict[str, str], dict[str, str]]:
    key = get_settings().metadata.tmdb_api_key.strip()
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}"}, {}
    return {}, {"api_key": key}


def url(path: str, size: str = "original") -> str:
    return f"{_IMAGES}{size}{path}"


def _load() -> dict[str, dict]:
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
        _LOADED = (mtime, raw if isinstance(raw, dict) else {})
    return _LOADED[1]


def _save(rows: dict[str, dict]) -> None:
    global _LOADED
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    _LOADED = None


def _trim(images: list[dict]) -> list[dict]:
    rows = [
        {
            "path": item["file_path"],
            "lang": item.get("iso_639_1"),
            "w": item.get("width"),
            "h": item.get("height"),
            "votes": round(float(item.get("vote_average") or 0.0), 2),
            "count": int(item.get("vote_count") or 0),
        }
        for item in images or []
        if item.get("file_path")
    ]
    rows.sort(key=lambda row: (row["votes"], row["count"], row["w"] or 0), reverse=True)
    return rows[:_KEEP]


def images(kind: str, tmdb_id: int) -> dict[str, Any]:
    """What is kept of a show ("tv") or film ("movie"): posters, backdrops, logos, seasons -> posters."""
    return _load().get(f"{kind}:{tmdb_id}") or {}


def due(kind: str, tmdb_id: int, seasons: set[int] = frozenset()) -> bool:  # type: ignore[assignment]
    row = images(kind, tmdb_id)
    if not row or time.time() - float(row.get("at") or 0) >= _TTL:
        return True
    kept = row.get("seasons") or {}
    return any(str(number) not in kept for number in seasons)


async def ensure(kind: str, tmdb_id: int, seasons: set[int] = frozenset(), *, force: bool = False) -> bool:  # type: ignore[assignment]
    """Fetch a show's or film's artwork (and these seasons' posters) unless kept and fresh. True if fetched."""
    if not configured() or (not force and not due(kind, tmdb_id, seasons)):
        return False
    headers, params = _auth()
    key = f"{kind}:{tmdb_id}"
    rows = dict(_load())
    row = dict(rows.get(key) or {})
    stale = force or not row or time.time() - float(row.get("at") or 0) >= _TTL
    try:
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if stale:
                response = await client.get(
                    f"{_API}/{kind}/{tmdb_id}/images", params={**params, "include_image_language": _LANGUAGES}
                )
                if response.status_code == 404:
                    row = {"posters": [], "backdrops": [], "logos": [], "seasons": {}}
                else:
                    response.raise_for_status()
                    data = response.json()
                    row = {
                        "posters": _trim(data.get("posters")),
                        "backdrops": _trim(data.get("backdrops")),
                        "logos": _trim(data.get("logos")),
                        "seasons": {} if force else dict(row.get("seasons") or {}),
                    }
                row["at"] = time.time()
            if kind == "tv":
                kept = dict(row.get("seasons") or {})
                for number in sorted(seasons):
                    if str(number) in kept and not stale:
                        continue
                    response = await client.get(
                        f"{_API}/tv/{tmdb_id}/season/{number}/images",
                        params={**params, "include_image_language": _LANGUAGES},
                    )
                    # A season TMDB does not have is remembered as none, not asked again.
                    kept[str(number)] = [] if response.status_code == 404 else _trim(
                        response.raise_for_status().json().get("posters")
                    )
                row["seasons"] = kept
    except (httpx.HTTPError, ValueError) as exc:
        last_error.update(source="TMDB", error=str(exc))
        log.warning("TMDB artwork of %s: %s", key, exc)
        return False
    last_error.clear()
    rows = dict(_load())
    rows[key] = row
    _save(rows)
    return True


__all__ = ["configured", "due", "ensure", "images", "url"]
