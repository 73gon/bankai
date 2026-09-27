"""A cover image for every AniDB entry, from manami-project's anime-offline-database.

TVDB has one poster per show, so every season of one showed the same cover;
Shoko has AniDB's own images, but only for anime in its collection -- not for
the shows held for review or blacklisted before they were ever downloaded.
anime-offline-database maps each AniDB entry to its MyAnimeList / AniList
record, cover included: one image per entry, for every entry.

The release is a 60 MB JSON file, updated weekly. It is fetched weekly and
boiled down, in a child process so the web server never holds the whole
thing, to AniDB id -> cover URL: about a megabyte.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
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
_ANIDB_SOURCE = re.compile(r"https?://anidb\.net/anime/(\d+)")
# The database's placeholder for an anime without artwork.
_NO_PICTURE = ("no_pic", "noimage", "placeholder")

_LOADED: tuple[float, dict[int, str]] | None = None
_REFRESH_LOCK = asyncio.Lock()


def _store() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "anidb_covers.json"


def covers() -> dict[int, str]:
    """AniDB id -> cover URL, as last condensed; empty until the first refresh."""
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
        _LOADED = (mtime, {int(aid): url for aid, url in raw.items() if str(aid).isdigit()})
    return _LOADED[1]


def cover(anidb_id: object) -> str | None:
    try:
        return covers().get(int(anidb_id))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def condense(source: Path, target: Path) -> int:
    """The full database -> AniDB id -> cover URL. Returns how many were kept."""
    data = json.loads(source.read_text(encoding="utf-8")).get("data") or []
    found: dict[str, str] = {}
    for item in data:
        picture = str(item.get("picture") or "")
        if not picture.startswith("https://") or any(mark in picture for mark in _NO_PICTURE):
            continue
        for url in item.get("sources") or []:
            match = _ANIDB_SOURCE.match(str(url))
            if match:
                found.setdefault(match[1], picture)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    temporary.write_text(json.dumps(found, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)
    return len(found)


async def refresh(*, force: bool = False) -> bool:
    """Fetch and condense the database when the kept map is a week old. True if renewed."""
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
                sys.executable, "-m", "bankai.metadata.anidb_art", str(download), str(target)
            )
            if await process.wait() != 0:
                raise RuntimeError(f"condensing exited with {process.returncode}")
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            log.warning("could not refresh AniDB cover art: %s", exc)
            return False
        finally:
            download.unlink(missing_ok=True)
        log.info("AniDB cover art refreshed: %d entries", len(covers()))
        return True


if __name__ == "__main__":  # the child process of refresh()
    print(condense(Path(sys.argv[1]), Path(sys.argv[2])))
