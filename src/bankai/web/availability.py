"""Background availability checks for Discover items.

For each Discover title we check, lazily and in the background, whether
filmpalast actually has a matching movie page — i.e. whether the German dub is
downloadable right now (the "mirror works"). Results are cached in-memory so the
Discover endpoint stays fast: confirmed-unavailable titles get filtered out and
backfilled, while confirmed-available ones earn a checkmark in the UI.

The check is intentionally cheap (a single filmpalast search + title match). A
search hit means the movie page exists and is downloadable; verifying that a
specific hoster resolves would require a full extraction and is far too costly
to run across ~100 discover cards.
"""

from __future__ import annotations

import asyncio
import re
import time

from bankai.backend import search_stream_sources
from bankai.logging import get_logger
from bankai.queue.models import MediaKind

log = get_logger(__name__)

# key -> {"status": "available"|"unavailable"|"unknown", "url": str|None, "ts": float}
_CACHE: dict[str, dict] = {}
_INFLIGHT: set[str] = set()
_SEM = asyncio.Semaphore(4)
_TTL_OK = 12 * 3600.0  # cache confirmed results for 12h
_TTL_UNKNOWN = 5 * 60.0  # retry transient failures after 5 min

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "and", "der", "die", "das"}


def _tokens(s: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(s.lower()) if t not in _STOP}


def _key(name: str) -> str:
    return " ".join(sorted(_tokens(name)))


def _matches(name: str, candidate_title: str) -> bool:
    want = _tokens(name)
    if not want:
        return False
    return want.issubset(_tokens(candidate_title))


def get_status(name: str) -> dict | None:
    """Return the cached availability entry for a title, or None when it should
    be (re)checked."""
    entry = _CACHE.get(_key(name))
    if not entry:
        return None
    age = time.time() - entry["ts"]
    ttl = _TTL_UNKNOWN if entry["status"] == "unknown" else _TTL_OK
    if age < ttl:
        return entry
    return None


async def _check(name: str) -> None:
    key = _key(name)
    try:
        async with _SEM:
            results = await search_stream_sources(name, site="filmpalast", limit=8, kind=MediaKind.MOVIE)
        match = next((r for r in results if _matches(name, r.title)), None)
        _CACHE[key] = {
            "status": "available" if match else "unavailable",
            "url": match.url if match else None,
            "ts": time.time(),
        }
    except Exception as exc:
        log.debug("availability check failed for %r: %s", name, exc)
        _CACHE[key] = {"status": "unknown", "url": None, "ts": time.time()}
    finally:
        _INFLIGHT.discard(key)


def schedule(name: str) -> None:
    """Kick off a background availability check unless one is cached/in-flight."""
    key = _key(name)
    if get_status(name) is not None or key in _INFLIGHT:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _INFLIGHT.add(key)
    loop.create_task(_check(name))
