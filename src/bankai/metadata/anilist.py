"""AniList's names and covers for AniDB entries.

AniDB stays what anime is identified by -- Shoko hashes files against it,
releases are matched to it -- but AniList names a show the way people know
it, and has the better covers. Each AniDB entry is matched to its AniList one
through anime-offline-database (see anidb_art), and AniList's public API is
asked for their titles and covers fifty at a time, kept a week.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.metadata import anidb_art

log = get_logger(__name__)

_API = "https://graphql.anilist.co"
_KEEP = 7 * 86400
_BATCH = 50
# AniList allows ninety requests a minute and sometimes thirty; bankai asks
# for a handful, and a few more batches at most each refresh.
_BATCHES_PER_RUN = 6
_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id
      title { english romaji }
      coverImage { extraLarge large }
    }
  }
}
"""

_LOADED: tuple[float, dict[int, dict]] | None = None


def _store() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "anilist.json"


def _load() -> dict[int, dict]:
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
        _LOADED = (mtime, {int(key): row for key, row in raw.items() if str(key).isdigit()})
    return _LOADED[1]


def _save(rows: dict[int, dict]) -> None:
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({str(key): row for key, row in rows.items()}), encoding="utf-8")
    tmp.replace(path)


def _row(anidb_id: object) -> dict:
    anilist = anidb_art.facts(anidb_id).get("anilist")
    return _load().get(int(anilist), {}) if anilist else {}


def name(anidb_id: object) -> str | None:
    """AniList's name for an AniDB entry: English, else romaji; None if unknown."""
    row = _row(anidb_id)
    return row.get("english") or row.get("romaji") or None


def cover(anidb_id: object) -> str | None:
    """AniList's cover for an AniDB entry, the largest it has; None if unknown."""
    return _row(anidb_id).get("cover") or None


async def ensure(anidb_ids: set[int]) -> int:
    """Fetch what is not known yet, or a week old, of these entries; returns how many."""
    rows = dict(_load())
    wanted = []
    for aid in sorted(anidb_ids):
        anilist = anidb_art.facts(aid).get("anilist")
        if not anilist:
            continue
        hit = rows.get(int(anilist))
        if hit is None or time.time() - float(hit.get("fetched_at", 0)) >= _KEEP:
            wanted.append(int(anilist))
    wanted = list(dict.fromkeys(wanted))[: _BATCH * _BATCHES_PER_RUN]
    if not wanted:
        return 0
    fetched = 0
    async with httpx.AsyncClient(timeout=30, headers={"Content-Type": "application/json"}) as client:
        for start in range(0, len(wanted), _BATCH):
            ids = wanted[start : start + _BATCH]
            try:
                response = await client.post(_API, json={"query": _QUERY, "variables": {"ids": ids}})
                response.raise_for_status()
                media = (((response.json() or {}).get("data") or {}).get("Page") or {}).get("media") or []
            except Exception as exc:
                log.warning("AniList could not be asked for names: %s", exc)
                break
            now = time.time()
            seen = set()
            for item in media:
                if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                    continue
                title = item.get("title") or {}
                image = item.get("coverImage") or {}
                rows[item["id"]] = {
                    "english": title.get("english"),
                    "romaji": title.get("romaji"),
                    "cover": image.get("extraLarge") or image.get("large"),
                    "fetched_at": now,
                }
                seen.add(item["id"])
                fetched += 1
            # Not on AniList any more: remembered as such, not asked for daily.
            for missing in set(ids) - seen:
                rows[missing] = {**rows.get(missing, {}), "fetched_at": now}
    _save(rows)
    return fetched


__all__ = ["cover", "ensure", "name"]
