"""Discover page data: TVDB-backed browse + search with posters.

Fail-soft like the rest of the TVDB integration: when no API key is
configured the endpoints return an empty list and ``configured=False`` so
the UI can show a friendly "configure TVDB" empty state.
"""

from __future__ import annotations

import asyncio
import datetime
import threading
import time
from dataclasses import asdict, dataclass

import httpx

from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

_BASE_URL = "https://api4.thetvdb.com/v4"
_ARTWORK_BASE = "https://artworks.thetvdb.com"
_CACHE: dict[str, tuple[float, list["DiscoverItem"]]] = {}
# Keys currently being refreshed in the background (stale-while-revalidate),
# so we never launch duplicate refreshes for the same key.
_REFRESHING: set[str] = set()
_REFRESH_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class DiscoverItem:
    name: str
    kind: str  # "movie" | "show"
    tvdb_id: int | None = None
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    is_new: bool = False


def is_configured() -> bool:
    m = get_settings().metadata
    return bool(m.tvdb_enabled and m.tvdb_api_key)


async def _login(client: httpx.AsyncClient) -> str | None:
    m = get_settings().metadata
    req: dict[str, str] = {"apikey": m.tvdb_api_key}
    if m.tvdb_pin:
        req["pin"] = m.tvdb_pin
    try:
        r = await client.post("login", json=req)
        r.raise_for_status()
        return (r.json().get("data") or {}).get("token")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("TVDB login failed: %s", exc)
        return None


def _cache_get(key: str) -> list[DiscoverItem] | None:
    ttl = get_settings().web.cache_ttl_seconds
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_put(key: str, items: list[DiscoverItem]) -> None:
    _CACHE[key] = (time.time(), items)


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _abs_image(url: object) -> str | None:
    """TVDB returns some image paths as absolute URLs and others as
    site-relative paths (``/banners/...``). Normalise to absolute so the
    poster proxy (which only accepts http/https) can fetch them."""
    if not url:
        return None
    s = str(url)
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("/"):
        return _ARTWORK_BASE + s
    return _ARTWORK_BASE + "/" + s


def _item_from_record(rec: dict, kind: str) -> DiscoverItem:
    name = rec.get("name") or rec.get("title") or rec.get("slug") or "Untitled"
    year = _to_int(rec.get("year")) or _to_int((rec.get("first_air_time") or "")[:4])
    poster = rec.get("image_url") or rec.get("image") or rec.get("thumbnail")
    return DiscoverItem(
        name=str(name),
        kind=kind,
        tvdb_id=_to_int(rec.get("tvdb_id") or rec.get("id")),
        year=year,
        poster_url=_abs_image(poster),
        overview=rec.get("overview"),
    )


async def search(query: str, *, kind: str, limit: int = 24) -> list[DiscoverItem]:
    if not is_configured() or not query.strip():
        return []
    cache_key = f"search:{kind}:{query.strip().casefold()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    tvdb_type = "movie" if kind == "movie" else "series"
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return []
        try:
            r = await client.get(
                "search",
                headers={"Authorization": f"Bearer {token}"},
                params={"query": query.strip(), "type": tvdb_type, "limit": limit},
            )
            r.raise_for_status()
            data = r.json().get("data") or []
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("TVDB search failed: %s", exc)
            return []
    items = [_item_from_record(rec, kind) for rec in data if isinstance(rec, dict)]
    _cache_put(cache_key, items)
    return items


async def trending(kind: str, *, limit: int = 60) -> list[DiscoverItem]:
    """Browse feed. TVDB has no true trending endpoint on the free tier,
    so we page the movies/series list as a "popular" browse surface."""
    if not is_configured():
        return []
    cache_key = f"trending:{kind}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    endpoint = "movies" if kind == "movie" else "series"
    items: list[DiscoverItem] = []
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        page = 0
        # Each TVDB list page returns ~500 records; one page is plenty, but
        # loop defensively in case a page comes back short.
        while len(items) < limit and page < 4:
            try:
                r = await client.get(endpoint, headers=headers, params={"page": page})
                r.raise_for_status()
                data = r.json().get("data") or []
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("TVDB browse failed: %s", exc)
                break
            if not data:
                break
            items.extend(_item_from_record(rec, kind) for rec in data if isinstance(rec, dict))
            page += 1
    items = items[:limit]
    _cache_put(cache_key, items)
    return items


async def new_releases(kind: str, *, limit: int = 24) -> list[DiscoverItem]:
    """Recent releases via TVDB's filter endpoint, sorted by score.

    Pulls the current and previous calendar year so the Discover page can
    surface (and tag) genuinely new titles. Fail-soft like the rest of the
    TVDB integration."""
    if not is_configured():
        return []
    cache_key = f"new:{kind}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    endpoint = "movies/filter" if kind == "movie" else "series/filter"
    year = datetime.date.today().year
    items: list[DiscoverItem] = []
    seen: set[int | None] = set()
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        for yr in (year, year - 1):
            if len(items) >= limit:
                break
            try:
                r = await client.get(
                    endpoint,
                    headers=headers,
                    params={"country": "usa", "lang": "eng", "year": yr, "sort": "score"},
                )
                r.raise_for_status()
                data = r.json().get("data") or []
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("TVDB new releases failed: %s", exc)
                break
            for rec in data:
                if not isinstance(rec, dict):
                    continue
                base = _item_from_record(rec, kind)
                if base.tvdb_id in seen:
                    continue
                seen.add(base.tvdb_id)
                items.append(
                    DiscoverItem(
                        name=base.name,
                        kind=base.kind,
                        tvdb_id=base.tvdb_id,
                        year=base.year,
                        poster_url=base.poster_url,
                        overview=base.overview,
                        is_new=True,
                    )
                )
                if len(items) >= limit:
                    break
    items = items[:limit]
    _cache_put(cache_key, items)
    return items


async def german_title(tvdb_id: int, *, kind: str) -> str | None:
    """Look up the German title (translation) for a TVDB record.

    Falls back to ``None`` when no German translation exists or TVDB is
    unreachable, so the caller can fall back to the original name.
    """
    if not is_configured() or not tvdb_id:
        return None
    cache_key = f"de:{kind}:{tvdb_id}"
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < get_settings().web.cache_ttl_seconds:
        return hit[1][0].name if hit[1] else None
    entity = "movies" if kind == "movie" else "series"
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        for lang in ("deu", "ger"):
            try:
                r = await client.get(
                    f"{entity}/{tvdb_id}/translations/{lang}", headers=headers
                )
                if r.status_code != 200:
                    continue
                name = (r.json().get("data") or {}).get("name")
            except (httpx.HTTPError, ValueError):
                continue
            if name:
                _CACHE[cache_key] = (
                    time.time(),
                    [DiscoverItem(name=str(name), kind=kind, tvdb_id=tvdb_id)],
                )
                return str(name)
    return None


def to_dict(item: DiscoverItem) -> dict:
    return asdict(item)


# ---------------------------------------------------------------------------
# Merged Discover feed with stale-while-revalidate
# ---------------------------------------------------------------------------


async def _build_feed(kind: str) -> list["DiscoverItem"]:
    """Merge new-releases + browse into the deduped Discover feed."""
    new = await new_releases(kind)
    browse = await trending(kind)
    merged: list[DiscoverItem] = []
    seen: set = set()
    for it in [*new, *browse]:
        key = (it.tvdb_id, it.name.casefold())
        if key in seen:
            continue
        seen.add(key)
        merged.append(it)
    return merged


async def discover_feed(kind: str) -> tuple[list["DiscoverItem"], bool]:
    """Return the Discover feed, fresh or stale-while-revalidate.

    * Warm + fresh cache  -> returned immediately (fresh=True).
    * Warm + stale cache  -> returned immediately (fresh=False) and a
      background refresh is kicked off so the next visit is current.
    * Cold cache          -> fetched synchronously once.
    """
    key = f"feed:{kind}"
    ttl = get_settings().web.cache_ttl_seconds
    hit = _CACHE.get(key)
    if hit is not None:
        age = time.time() - hit[0]
        if age < ttl:
            return hit[1], True
        _schedule_refresh(kind)
        return hit[1], False
    items = await _build_feed(kind)
    _CACHE[key] = (time.time(), items)
    return items, True


def _schedule_refresh(kind: str) -> None:
    """Refresh ``feed:<kind>`` in the background (deduped)."""
    key = f"feed:{kind}"
    with _REFRESH_LOCK:
        if key in _REFRESHING:
            return
        _REFRESHING.add(key)

    async def _run() -> None:
        try:
            items = await _build_feed(kind)
            _CACHE[key] = (time.time(), items)
        except Exception as exc:  # pragma: no cover - background best effort
            log.debug("discover refresh failed for %s: %s", kind, exc)
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.discard(key)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(_run()), daemon=True).start()


def prewarm() -> None:
    """Populate the Discover cache on startup so the first visit is instant."""
    if not is_configured():
        return

    def _warm() -> None:
        for kind in ("movie", "show"):
            try:
                items = asyncio.run(_build_feed(kind))
                _CACHE[f"feed:{kind}"] = (time.time(), items)
                log.info("prewarmed discover feed: %s (%d items)", kind, len(items))
            except Exception as exc:  # pragma: no cover
                log.debug("discover prewarm failed for %s: %s", kind, exc)

    threading.Thread(target=_warm, daemon=True).start()

