"""manami-project's anime-offline-database, kept per AniList entry.

One record per anime, merged across MyAnimeList, AniList, AniDB, Kitsu and
others: its title and synonyms from all of them, type, episode count, status,
season, cover, and the anime related to it. bankai keeps the entries that are
on AniList, keyed by AniList id -- what anime is identified by -- with the
titles a release could be named by (romanised ones; Erai never names a show
in kana), and, for the move away from AniDB, which AniList entry each AniDB
one is.

The release is a 60 MB JSON file, updated weekly. It is fetched weekly and
boiled down in a child process, so the web server never holds the whole thing.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

_URL = (
    "https://github.com/manami-project/anime-offline-database/releases/latest/download/"
    "anime-offline-database-minified.json"
)
_TTL = 7 * 86400
_ANILIST_SOURCE = re.compile(r"https?://anilist\.co/anime/(\d+)")
_ANIDB_SOURCE = re.compile(r"https?://anidb\.net/anime/(\d+)")
# The database's placeholder for an anime without artwork.
_NO_PICTURE = ("no_pic", "noimage", "placeholder")

_LOADED: tuple[float, dict[int, dict]] | None = None
_CROSS: tuple[float, dict[int, int]] | None = None
_REFRESH_LOCK = asyncio.Lock()


def _store() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "offline_anilist.json"


def _cross_store(store: Path | None = None) -> Path:
    return (store or _store()).with_name("anidb_to_anilist.json")


def _romanised(value: str) -> bool:
    """Written in Latin letters, as a release name would be."""
    letters = [character for character in value if character.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(character, "") for character in letters)


def entries() -> dict[int, dict]:
    """AniList id -> {title, synonyms, type, episodes, status, year, season, picture, anidb, related}."""
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


def entry(anilist_id: object) -> dict:
    try:
        return entries().get(int(anilist_id), {})  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}


def anilist_of_anidb(anidb_id: object) -> int | None:
    """The AniList entry an AniDB one is; for moving what was kept by AniDB id."""
    global _CROSS
    path = _cross_store()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _CROSS is None or _CROSS[0] != mtime:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        _CROSS = (mtime, {int(key): int(value) for key, value in raw.items() if str(key).isdigit()})
    try:
        return _CROSS[1].get(int(anidb_id))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def condense(source: Path, target: Path) -> int:
    """The full database -> what is kept per AniList entry, and the AniDB cross map.

    Returns how many AniList entries were kept.
    """
    data = json.loads(source.read_text(encoding="utf-8")).get("data") or []
    kept: dict[str, dict] = {}
    cross: dict[str, int] = {}
    for item in data:
        sources = [str(url) for url in item.get("sources") or []]
        anilist = next((int(m[1]) for url in sources if (m := _ANILIST_SOURCE.match(url))), None)
        if anilist is None:
            continue
        anidb = [int(m[1]) for url in sources if (m := _ANIDB_SOURCE.match(url))]
        related = [
            int(m[1]) for url in item.get("relatedAnime") or [] if (m := _ANILIST_SOURCE.match(str(url)))
        ]
        season = item.get("animeSeason") or {}
        picture = str(item.get("picture") or "")
        title = str(item.get("title") or "")
        synonyms = [
            name for name in dict.fromkeys(str(value) for value in item.get("synonyms") or [])
            if name and name != title and _romanised(name)
        ]
        kept[str(anilist)] = {
            "title": title,
            "synonyms": synonyms,
            "type": item.get("type"),
            "episodes": item.get("episodes") if isinstance(item.get("episodes"), int) else None,
            "status": item.get("status"),
            "year": season.get("year") if isinstance(season.get("year"), int) else None,
            "season": season.get("season"),
            "picture": picture if picture.startswith("https://") and not any(m in picture for m in _NO_PICTURE) else None,
            "anidb": anidb,
            "related": related,
        }
        for aid in anidb:
            cross.setdefault(str(aid), anilist)
    target.parent.mkdir(parents=True, exist_ok=True)
    for path, value in ((_cross_store(target), cross), (target, kept)):
        temporary = path.with_suffix(".part")
        temporary.write_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    return len(kept)


async def refresh(*, force: bool = False) -> bool:
    """Fetch and condense the database when the kept copy is a week old. True if renewed."""
    async with _REFRESH_LOCK:
        target = _store()
        try:
            fresh = time.time() - target.stat().st_mtime < _TTL
        except OSError:
            fresh = False
        if fresh and not force:
            return False
        download = target.with_name("anime-offline-database.json.part")
        try:
            download.parent.mkdir(parents=True, exist_ok=True)
            async with (
                httpx.AsyncClient(timeout=httpx.Timeout(120, connect=20), follow_redirects=True) as client,
                client.stream("GET", _URL) as response,
            ):
                response.raise_for_status()
                with download.open("wb") as handle:
                    async for chunk in response.aiter_bytes(1 << 20):
                        handle.write(chunk)
            # A child process parses it: the whole database is ~600 MB in
            # memory while parsed, which the web server should never carry.
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "bankai.metadata.offline", str(download), str(target)
            )
            if await process.wait() != 0:
                raise RuntimeError(f"condensing exited with {process.returncode}")
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            log.warning("could not refresh anime-offline-database: %s", exc)
            return False
        finally:
            download.unlink(missing_ok=True)
        log.info("anime-offline-database refreshed: %d AniList entries", len(entries()))
        return True


if __name__ == "__main__":  # the child process of refresh()
    print(condense(Path(sys.argv[1]), Path(sys.argv[2])))
