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
from bankai.metadata.tvdb import worldwide_release_date

log = get_logger(__name__)

_BASE_URL = "https://api4.thetvdb.com/v4"
_ARTWORK_BASE = "https://artworks.thetvdb.com"
_CACHE: dict[str, tuple[float, list[DiscoverItem]]] = {}
_DETAIL_CACHE: dict[str, tuple[float, TitleDetails]] = {}
_BROWSE_META: dict[str, dict] = {}
# Keys currently being refreshed in the background (stale-while-revalidate),
# so we never launch duplicate refreshes for the same key.
_REFRESHING: set[str] = set()
_REFRESH_LOCK = threading.Lock()
_REFRESH_TASKS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class DiscoverItem:
    name: str
    kind: str  # "movie" | "show"
    tvdb_id: int | None = None
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    is_new: bool = False
    release_date: str | None = None  # ISO date (YYYY-MM-DD) when known
    status: str | None = None  # TVDB status name e.g. "Released", "Announced"


@dataclass(frozen=True, slots=True)
class DiscoverPage:
    items: list[DiscoverItem]
    page: int
    page_size: int
    total: int | None
    has_next: bool


@dataclass(frozen=True, slots=True)
class TitleDetails:
    german: str | None = None
    worldwide_release_date: str | None = None

    @property
    def worldwide_year(self) -> int | None:
        return _to_int((self.worldwide_release_date or "")[:4])


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
    worldwide_date = worldwide_release_date(rec) if kind == "movie" else None
    date = worldwide_date or rec.get("first_air_time") or rec.get("release_date") or rec.get("firstAired") or rec.get("date")
    date_s = str(date)[:10] if date else None
    year = _to_int((worldwide_date or "")[:4]) or _to_int(rec.get("year")) or _to_int((date_s or "")[:4])
    poster = rec.get("image_url") or rec.get("image") or rec.get("thumbnail")
    status_raw = rec.get("status")
    if isinstance(status_raw, dict):
        status = status_raw.get("name")
    elif isinstance(status_raw, str):
        status = status_raw
    else:
        status = None
    return DiscoverItem(
        name=str(name),
        kind=kind,
        tvdb_id=_to_int(rec.get("tvdb_id") or rec.get("id")),
        year=year,
        poster_url=_abs_image(poster),
        overview=rec.get("overview"),
        release_date=date_s,
        status=str(status) if status else None,
    )


async def _request_data(
    client: httpx.AsyncClient,
    endpoint: str,
    *,
    headers: dict[str, str],
    params: dict[str, object] | None = None,
) -> object:
    """Fetch one TVDB data payload without making search fail as a whole."""
    try:
        response = await client.get(endpoint, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("data")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("TVDB request failed for %s: %s", endpoint, exc)
        return None


def _dedupe_items(items: list[DiscoverItem], limit: int) -> list[DiscoverItem]:
    unique: list[DiscoverItem] = []
    seen: set[tuple[object, ...]] = set()
    for item in items:
        key: tuple[object, ...]
        if item.tvdb_id is not None:
            key = ("tvdb", item.tvdb_id)
        else:
            key = ("title", item.name.casefold(), item.year)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


async def _person_movies(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    limit: int,
) -> list[DiscoverItem]:
    """Return movies linked to matching TVDB people (cast or crew)."""
    people_data = await _request_data(
        client,
        "search",
        headers=headers,
        params={"query": query, "type": "person", "limit": 5},
    )
    people = [record for record in people_data if isinstance(record, dict)] if isinstance(people_data, list) else []

    # Prefer exact full-name hits, but keep fuzzy TVDB matches useful for partial
    # names. Limiting extended lookups keeps a broad name from fanning out into
    # dozens of API calls.
    normalized_query = " ".join(query.casefold().split())
    exact = [
        record
        for record in people
        if " ".join(str(record.get("name") or record.get("title") or "").casefold().split())
        == normalized_query
    ]
    candidates = (exact or people)[:3]
    person_ids = [
        person_id
        for record in candidates
        if (person_id := _to_int(record.get("tvdb_id") or record.get("id"))) is not None
    ]
    extended_data = await asyncio.gather(
        *(
            _request_data(client, f"people/{person_id}/extended", headers=headers)
            for person_id in person_ids
        )
    )

    credits: list[DiscoverItem] = []
    for person in extended_data:
        if not isinstance(person, dict):
            continue
        characters = person.get("characters") or []
        if not isinstance(characters, list):
            continue
        for character in characters:
            if not isinstance(character, dict):
                continue
            movie_id = _to_int(character.get("movieId"))
            movie = character.get("movie")
            if movie_id is None or not isinstance(movie, dict) or not movie.get("name"):
                continue
            record = {**movie, "tvdb_id": movie_id}
            credits.append(_item_from_record(record, "movie"))

    return _dedupe_items(credits, limit)


_STUDIO_HINTS = ("studio", "picture", "animation", "film", "production")
_NON_STUDIO_HINTS = ("channel", "television", "network", "junior", "kids", "cinemagic", "xd")


def _company_rank(record: dict, query: str, index: int) -> tuple[int, int, int, int]:
    name = " ".join(str(record.get("name") or record.get("title") or "").casefold().split())
    normalized_query = " ".join(query.casefold().split())
    exact = int(name == normalized_query)
    studio_hint = 2 if any(hint in name for hint in _STUDIO_HINTS) else int("company" in name)
    penalty = int(any(hint in name for hint in _NON_STUDIO_HINTS) or "+" in name or "(" in name)
    return (exact, studio_hint, -penalty, -index)


async def _studio_movies(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    limit: int,
) -> list[DiscoverItem]:
    """Resolve matching TVDB companies, then fetch their linked movies."""
    company_data = await _request_data(
        client,
        "search",
        headers=headers,
        params={"query": query, "type": "company", "limit": 30},
    )
    companies = (
        [record for record in company_data if isinstance(record, dict)]
        if isinstance(company_data, list)
        else []
    )
    normalized_query = " ".join(query.casefold().split())
    exact = [
        record
        for record in companies
        if " ".join(str(record.get("name") or record.get("title") or "").casefold().split())
        == normalized_query
    ]
    if exact:
        candidates = exact[:3]
    else:
        candidates = [
            record
            for index, record in sorted(
                enumerate(companies),
                key=lambda pair: _company_rank(pair[1], query, pair[0]),
                reverse=True,
            )[:3]
        ]
    company_ids = [
        company_id
        for record in candidates
        if (company_id := _to_int(record.get("tvdb_id") or record.get("id"))) is not None
    ]
    movie_data = await asyncio.gather(
        *(
            _request_data(
                client,
                "movies/filter",
                headers=headers,
                params={"company": company_id, "sort": "score"},
            )
            for company_id in company_ids
        )
    )
    buckets = [
        [_item_from_record(record, "movie") for record in data if isinstance(record, dict)]
        for data in movie_data
        if isinstance(data, list)
    ]
    # Round-robin avoids one broad parent company crowding out more specific
    # matching studios while preserving TVDB's score order within each list.
    merged: list[DiscoverItem] = []
    for position in range(max((len(bucket) for bucket in buckets), default=0)):
        for bucket in buckets:
            if position < len(bucket):
                merged.append(bucket[position])
        if len(merged) >= limit * 2:
            break
    return _dedupe_items(merged, limit)


async def search(
    query: str,
    *,
    kind: str,
    limit: int = 51,
    search_by: str = "title",
) -> list[DiscoverItem]:
    if not is_configured() or not query.strip():
        return []
    mode = search_by.strip().casefold()
    if mode not in {"title", "person", "studio"}:
        return []
    if kind != "movie" and mode != "title":
        return []
    cache_key = f"search:{kind}:{mode}:{query.strip().casefold()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    tvdb_type = "movie" if kind == "movie" else "series"
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        if mode == "person":
            items = await _person_movies(client, headers, query.strip(), limit)
        elif mode == "studio":
            items = await _studio_movies(client, headers, query.strip(), limit)
        else:
            params: dict[str, object] = {"type": tvdb_type, "limit": limit}
            params["query"] = query.strip()
            data = await _request_data(
                client,
                "search",
                headers=headers,
                params=params,
            )
            items = (
                [_item_from_record(record, kind) for record in data if isinstance(record, dict)]
                if isinstance(data, list)
                else []
            )
    _cache_put(cache_key, items)
    return items


async def search_page(
    query: str,
    *,
    kind: str,
    page: int,
    page_size: int = 50,
    search_by: str = "title",
) -> DiscoverPage:
    """Return one UI page without imposing an application-level page cap.

    TVDB's title search supports ``offset`` + ``limit``. Person/studio searches
    are assembled from related records, so those are sliced after resolution
    instead. Bankai never imposes its own maximum page number.
    """
    page = max(0, int(page))
    page_size = max(10, min(100, int(page_size)))
    mode = search_by.strip().casefold()
    if not is_configured() or not query.strip() or mode not in {"title", "person", "studio"}:
        return DiscoverPage([], page, page_size, 0, False)
    if kind != "movie" and mode != "title":
        return DiscoverPage([], page, page_size, 0, False)

    if mode != "title":
        # Credits/company filter responses are not offset-paginated by TVDB.
        # Resolve enough rows for this UI page, then slice deterministically.
        end = (page + 1) * page_size
        if page == 0:
            items = await search(query, kind=kind, limit=page_size + 1, search_by=mode)
        else:
            items = await search(query, kind=kind, limit=end + 1, search_by=mode)
        return DiscoverPage(
            items=items[page * page_size : end],
            page=page,
            page_size=page_size,
            total=len(items) if len(items) <= end else None,
            has_next=len(items) > end,
        )

    if page == 0:
        # Reuse the regular search path for the first page (and its cache).
        # Fetch one look-ahead row so has_next remains accurate without an
        # arbitrary UI cap.
        items = await search(query, kind=kind, limit=page_size + 1, search_by=mode)
        return DiscoverPage(items[:page_size], page, page_size, None, len(items) > page_size)

    cache_key = f"search-page:{kind}:{query.strip().casefold()}:{page}:{page_size}"
    cached = _cache_get(cache_key)
    if cached is not None:
        # A full page means there may be another page. The live response below
        # carries an exact total when TVDB supplies links metadata.
        return DiscoverPage(cached, page, page_size, None, len(cached) == page_size)

    tvdb_type = "movie" if kind == "movie" else "series"
    items: list[DiscoverItem] = []
    total: int | None = None
    has_next = False
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return DiscoverPage([], page, page_size, 0, False)
        try:
            response = await client.get(
                "search",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "query": query.strip(),
                    "type": tvdb_type,
                    "offset": page * page_size,
                    "limit": page_size,
                },
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or []
            links = payload.get("links") or {}
            items = [_item_from_record(record, kind) for record in data if isinstance(record, dict)]
            total = _to_int(links.get("total_items"))
            if total is not None:
                has_next = (page + 1) * page_size < total
            else:
                has_next = len(items) == page_size
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("TVDB paginated search failed: %s", exc)
    _cache_put(cache_key, items)
    return DiscoverPage(items, page, page_size, total, has_next)


async def browse_page(kind: str, *, page: int, page_size: int = 50) -> DiscoverPage:
    """Slice the complete TVDB movies/series catalogue into UI-sized pages.

    TVDB's catalogue endpoints use their own (larger) provider page size. We
    translate the UI page index to that provider page, using
    the response ``links.page_size`` rather than assuming it is always 500.
    """
    page = max(0, int(page))
    page_size = max(10, min(100, int(page_size)))
    if not is_configured():
        return DiscoverPage([], page, page_size, 0, False)
    endpoint = "movies" if kind == "movie" else "series"
    start = page * page_size
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return DiscoverPage([], page, page_size, 0, False)
        headers = {"Authorization": f"Bearer {token}"}

        async def provider_page(number: int) -> tuple[list[DiscoverItem], dict]:
            key = f"browse-provider:{kind}:{number}"
            cached_items = _cache_get(key)
            meta_hit = _BROWSE_META.get(key)
            if cached_items is not None and meta_hit is not None:
                return cached_items, meta_hit
            try:
                response = await client.get(endpoint, headers=headers, params={"page": number})
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or []
                links = payload.get("links") or {}
                converted = [_item_from_record(record, kind) for record in data if isinstance(record, dict)]
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("TVDB browse page %s failed: %s", number, exc)
                return [], {}
            _cache_put(key, converted)
            _BROWSE_META[key] = dict(links) if isinstance(links, dict) else {}
            return converted, _BROWSE_META[key]

        first, first_links = await provider_page(0)
        provider_size = _to_int(first_links.get("page_size")) or len(first) or page_size
        total = _to_int(first_links.get("total_items"))
        source_page = start // provider_size
        source_offset = start % provider_size
        source_items, source_links = (first, first_links) if source_page == 0 else await provider_page(source_page)
        combined = list(source_items[source_offset:])
        next_page = source_page + 1
        while len(combined) < page_size and source_items:
            source_items, _ = await provider_page(next_page)
            if not source_items:
                break
            combined.extend(source_items)
            next_page += 1
        items = combined[:page_size]
        if total is None:
            total = _to_int(source_links.get("total_items"))
        has_next = start + len(items) < total if total is not None else len(items) == page_size
        return DiscoverPage(items, page, page_size, total, has_next)


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
                        release_date=base.release_date,
                        status=base.status,
                    )
                )
                if len(items) >= limit:
                    break
    items = items[:limit]
    _cache_put(cache_key, items)
    return items


async def title_details(tvdb_id: int, *, kind: str) -> TitleDetails:
    """Resolve localized title details with TVDB's worldwide movie date.

    The regular search result's generic year may be an earlier country or
    festival release. Only the extended movie record exposes an explicit
    Worldwide release row, so it is resolved when the user opens a title and
    cached with its German translation.
    """
    if not is_configured() or not tvdb_id:
        return TitleDetails()
    cache_key = f"details:{kind}:{tvdb_id}"
    hit = _DETAIL_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < get_settings().web.cache_ttl_seconds:
        return hit[1]
    entity = "movies" if kind == "movie" else "series"
    async with httpx.AsyncClient(base_url=_BASE_URL + "/", timeout=10.0) as client:
        token = await _login(client)
        if not token:
            return TitleDetails()
        headers = {"Authorization": f"Bearer {token}"}

        async def german_translation() -> str | None:
            for lang in ("deu", "ger"):
                try:
                    response = await client.get(f"{entity}/{tvdb_id}/translations/{lang}", headers=headers)
                    if response.status_code != 200:
                        continue
                    name = (response.json().get("data") or {}).get("name")
                except (httpx.HTTPError, ValueError):
                    continue
                if name:
                    return str(name)
            return None

        async def worldwide_date() -> str | None:
            if kind != "movie":
                return None
            data = await _request_data(client, f"movies/{tvdb_id}/extended", headers=headers)
            return worldwide_release_date(data) if isinstance(data, dict) else None

        german, release_date = await asyncio.gather(german_translation(), worldwide_date())
    details = TitleDetails(german=german, worldwide_release_date=release_date)
    _DETAIL_CACHE[cache_key] = (time.time(), details)
    return details


async def german_title(tvdb_id: int, *, kind: str) -> str | None:
    """Backward-compatible German-title-only lookup."""
    return (await title_details(tvdb_id, kind=kind)).german


def to_dict(item: DiscoverItem) -> dict:
    return asdict(item)


def is_released(item: DiscoverItem, *, today: datetime.date | None = None) -> bool:
    """True if the title has already been released.

    The release *date* is authoritative: anything dated in the future is hidden.
    When no date is available a future year is likewise treated as unreleased,
    and a past year as released. Only for the ambiguous current-year-without-date
    case do we consult TVDB's status hint as a last resort. This keeps upcoming
    titles out of Discover even when TVDB's ``status`` is stale or missing.
    """
    today = today or datetime.date.today()
    if item.release_date:
        try:
            return datetime.date.fromisoformat(item.release_date) <= today
        except ValueError:
            pass
    if item.year is not None:
        if item.year > today.year:
            return False
        if item.year < today.year:
            return True
    # Current year without a date, or no year at all: use the status hint.
    st = (item.status or "").strip().lower()
    return not any(h in st for h in _UNRELEASED_HINTS)


_UNRELEASED_HINTS = (
    "announc",
    "plan",
    "pre-prod",
    "pre prod",
    "post-prod",
    "post prod",
    "filming",
    "production",
    "upcoming",
    "rumor",
    "develop",
    "cancel",
    "unreleased",
)


# ---------------------------------------------------------------------------
# Merged Discover feed with stale-while-revalidate
# ---------------------------------------------------------------------------


async def _build_feed(kind: str) -> list[DiscoverItem]:
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


async def discover_feed(kind: str) -> tuple[list[DiscoverItem], bool]:
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
        task = loop.create_task(_run())
        _REFRESH_TASKS.add(task)
        task.add_done_callback(_REFRESH_TASKS.discard)
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
