"""Best-effort poster-URL cache for library/queue titles (TVDB-backed).

Poster lookups hit TVDB, which is slow, so we never block the ``/api/titles``
response on them: we return whatever is already cached and kick off a
background thread to resolve any misses. TVDB artwork URLs are public CDN
links the browser can load directly, so we only cache the URL (a ``null`` is
cached too, to avoid re-querying titles with no artwork).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from bankai.logging import get_logger
from bankai.web import discover
from bankai.web.review import _state_root

log = get_logger(__name__)

_LOCK = threading.Lock()
_INFLIGHT: set[str] = set()


def _store() -> Path:
    return _state_root() / "posters.json"


def _load() -> dict:
    p = _store()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    p = _store()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(p)


def cached(key: str) -> str | None:
    entry = _load().get(key)
    return entry.get("url") if entry else None


def cached_year(key: str) -> int | None:
    entry = _load().get(key)
    return entry.get("year") if entry else None


def _set(key: str, url: str | None, year: int | None) -> None:
    with _LOCK:
        data = _load()
        data[key] = {"url": url, "year": year, "ts": time.time()}
        _save(data)


def ensure(key: str, query: str, kind: str) -> None:
    """Resolve ``query``'s poster + year in the background if not cached."""
    if not query.strip():
        return
    if key in _load():
        return
    with _LOCK:
        if key in _INFLIGHT:
            return
        _INFLIGHT.add(key)

    def _work() -> None:
        url: str | None = None
        year: int | None = None
        try:
            tvdb_kind = "movie" if kind == "movie" else "series"
            items = asyncio.run(discover.search(query, kind=tvdb_kind, limit=1))
            if items:
                url = items[0].poster_url
                year = items[0].year
        except Exception as exc:  # poster lookup is best-effort
            log.debug("poster lookup failed for %r: %s", query, exc)
        _set(key, url, year)
        with _LOCK:
            _INFLIGHT.discard(key)

    threading.Thread(target=_work, daemon=True).start()
