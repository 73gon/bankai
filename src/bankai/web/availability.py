"""Background availability checks for Discover items.

For each Discover title we check, lazily and in the background, whether
Filmpalast has the matching release *and* its detail page exposes at least one
hoster supported by our extractor. Results are cached in-memory so the Discover
endpoint stays fast: confirmed-unavailable titles get filtered out and
backfilled, while confirmed-available ones earn a checkmark in the UI.

This deliberately stops short of downloading the feature. The exact verified
wrapper URL is retained and handed to the queue, where it is checked again, so
the badge and the source eventually extracted cannot silently diverge.
"""

from __future__ import annotations

import asyncio
import re
import time

from bankai.logging import get_logger
from bankai.queue.models import MediaKind
from bankai.scraper.backends.filmpalast import FilmpalastBackend

log = get_logger(__name__)

# key -> {"status": "available"|"unavailable"|"unknown", "url": str|None, "ts": float}
_CACHE: dict[str, dict] = {}
_INFLIGHT: set[str] = set()
_TASKS: set[asyncio.Task[None]] = set()
_SEM = asyncio.Semaphore(4)
_TTL_OK = 12 * 3600.0  # cache confirmed results for 12h
_TTL_UNKNOWN = 5 * 60.0  # retry transient failures after 5 min

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "and", "der", "die", "das"}


def _tokens(s: str) -> set[str]:
    return {
        t
        for t in _TOKEN_RE.findall(s.casefold())
        if t not in _STOP and not (len(t) == 4 and t.isdigit())
    }


def _key(name: str, year: int | None = None) -> str:
    return f"{' '.join(sorted(_tokens(name)))}|{year or ''}"


def _matches(name: str, candidate_title: str) -> bool:
    want = _tokens(name)
    candidate = _tokens(candidate_title)
    if not want or not candidate:
        return False
    if want == candidate:
        return True
    # A one-word title such as "Up" must not certify "Step Up". Longer
    # titles may carry a short subtitle, but only within a narrow margin.
    if min(len(want), len(candidate)) == 1:
        return False
    smaller, larger = sorted((want, candidate), key=len)
    return smaller.issubset(larger) and len(larger) - len(smaller) <= 2


def get_status(name: str, *, year: int | None = None) -> dict | None:
    """Return the cached availability entry for a title, or None when it should
    be (re)checked."""
    entry = _CACHE.get(_key(name, year))
    if not entry:
        return None
    age = time.time() - entry["ts"]
    ttl = _TTL_UNKNOWN if entry["status"] == "unknown" else _TTL_OK
    if age < ttl:
        return entry
    return None


async def _check(name: str, *, year: int | None = None) -> None:
    key = _key(name, year)
    backend = FilmpalastBackend()
    try:
        async with _SEM:
            results = await backend.search(name, kind=MediaKind.MOVIE, limit=8)
            matches = [
                result
                for result in results
                if _matches(name, result.title)
                and (year is None or result.year is None or result.year == year)
            ]
            match = None
            mirror_count = 0
            for candidate in matches:
                mirrors = await backend.resolve_live_streams(candidate.url)
                if mirrors:
                    match = candidate
                    mirror_count = len(mirrors)
                    break
        _CACHE[key] = {
            "status": "available" if match else "unavailable",
            "url": match.url if match else None,
            "mirrors": mirror_count,
            "ts": time.time(),
        }
    except Exception as exc:
        log.debug("availability check failed for %r: %s", name, exc)
        _CACHE[key] = {"status": "unknown", "url": None, "mirrors": 0, "ts": time.time()}
    finally:
        await backend.aclose()
        _INFLIGHT.discard(key)


def schedule(name: str, *, year: int | None = None) -> None:
    """Kick off a background availability check unless one is cached/in-flight."""
    key = _key(name, year)
    if get_status(name, year=year) is not None or key in _INFLIGHT:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _INFLIGHT.add(key)
    task = loop.create_task(_check(name, year=year))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def shutdown() -> None:
    """Cancel unfinished checks and close their HTTP clients on app shutdown."""
    tasks = list(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _TASKS.clear()
    _INFLIGHT.clear()
