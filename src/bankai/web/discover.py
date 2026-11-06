"""Discover page data: TMDB-backed browse + search with posters.

Fail-soft: when no TMDB key is configured the endpoints return an empty list
and ``configured=False`` so the UI can show a friendly empty state. TheTVDB did
this until Oct 2026 (see metadata.tmdb_titles).
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
from bankai.metadata import tmdb_titles as titles

log = get_logger(__name__)

# TMDB's lists come 20 to a page, and no list goes past page 500.
_TMDB_PAGE = 20
_TMDB_LAST_PAGE = 500
_CACHE: dict[str, tuple[float, list[DiscoverItem]]] = {}
_PERSON_CACHE: dict[str, tuple[float, list[PersonSuggestion]]] = {}
_DETAIL_CACHE: dict[str, tuple[float, TitleDetails]] = {}
_TOTALS: dict[str, int | None] = {}
# Keys currently being refreshed in the background (stale-while-revalidate),
# so we never launch duplicate refreshes for the same key.
_REFRESHING: set[str] = set()
_REFRESH_LOCK = threading.Lock()
_REFRESH_TASKS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class DiscoverItem:
    name: str
    kind: str  # "movie" | "show"
    tmdb_id: int | None = None
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    is_new: bool = False
    release_date: str | None = None  # ISO date (YYYY-MM-DD) when known
    status: str | None = None  # e.g. "Released", "Ended", "Planned" where TMDB says


@dataclass(frozen=True, slots=True)
class PersonSuggestion:
    name: str
    tmdb_id: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoverPage:
    items: list[DiscoverItem]
    page: int
    page_size: int
    total: int | None
    has_next: bool


@dataclass(frozen=True, slots=True)
class TitleDetails:
    english: str | None = None
    german: str | None = None
    worldwide_release_date: str | None = None

    @property
    def worldwide_year(self) -> int | None:
        return titles.year(self.worldwide_release_date)


def is_configured() -> bool:
    return titles.configured()


def _cache_get(key: str) -> list[DiscoverItem] | None:
    ttl = get_settings().web.cache_ttl_seconds
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_put(key: str, items: list[DiscoverItem]) -> None:
    _CACHE[key] = (time.time(), items)


def _item(record: dict, kind: str, *, is_new: bool = False) -> DiscoverItem:
    date = titles.date_of(record)
    return DiscoverItem(
        name=titles.name_of(record) or "Untitled",
        kind=kind,
        tmdb_id=int(record["id"]) if str(record.get("id") or "").isdigit() else None,
        year=titles.year(date),
        poster_url=titles.poster(record.get("poster_path")),
        overview=record.get("overview") or None,
        is_new=is_new,
        release_date=date,
        status=record.get("status") or None,
    )


# TMDB's search/multi says per row what it is; people are left out.
_MULTI_KINDS = {"movie": "movie", "tv": "show"}


def _items(records: object, kind: str, *, is_new: bool = False) -> list[DiscoverItem]:
    """TMDB rows as items of ``kind``; ``"all"`` (search/multi) takes each row's own kind, films and shows only."""
    rows = [row for row in records or [] if isinstance(row, dict) and row.get("id")]  # type: ignore[union-attr]
    if kind == "all":
        return [_item(row, _MULTI_KINDS[row.get("media_type")], is_new=is_new) for row in rows if row.get("media_type") in _MULTI_KINDS]
    return [_item(row, kind, is_new=is_new) for row in rows]


def _search_path(kind: str) -> str:
    """Films, shows, or both ("all": TMDB's search/multi, which ranks them together)."""
    return "search/multi" if kind == "all" else f"search/{titles.section(kind)}"


def _dedupe_items(items: list[DiscoverItem], limit: int) -> list[DiscoverItem]:
    seen: set[tuple] = set()
    out: list[DiscoverItem] = []
    for item in items:
        key = (item.kind, item.tmdb_id) if item.tmdb_id is not None else (item.kind, item.name.casefold(), item.year)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


async def _pages(
    http: httpx.AsyncClient, path: str, kind: str, *, first: int, last: int, **params: object
) -> tuple[list[DiscoverItem], int | None]:
    """TMDB pages ``first``..``last`` (1-based) of a list, in order, and the list's total."""
    last = min(last, _TMDB_LAST_PAGE)
    answers = await asyncio.gather(*(titles.get(http, path, page=number, **params) for number in range(first, last + 1)))
    items: list[DiscoverItem] = []
    total: int | None = None
    for answer in answers:
        if not answer:
            continue
        if total is None and isinstance(answer.get("total_results"), int):
            total = min(int(answer["total_results"]), _TMDB_LAST_PAGE * _TMDB_PAGE)
        items.extend(_items(answer.get("results"), kind))
    return items, total


async def _rows(http: httpx.AsyncClient, path: str, kind: str, start: int, count: int, **params: object) -> tuple[list[DiscoverItem], int | None]:
    """Rows ``start`` .. ``start + count`` of a TMDB list, whatever its own page size."""
    first = start // _TMDB_PAGE + 1
    last = (start + count - 1) // _TMDB_PAGE + 1
    items, total = await _pages(http, path, kind, first=first, last=last, **params)
    offset = start - (first - 1) * _TMDB_PAGE
    return items[offset : offset + count], total


async def person_suggestions(query: str, *, limit: int = 8) -> list[PersonSuggestion]:
    """TMDB's people of a name, for search autocomplete."""
    clean = " ".join(query.split())
    if not is_configured() or len(clean) < 2:
        return []
    capped_limit = max(1, min(15, int(limit)))
    cache_key = f"{clean.casefold()}:{capped_limit}"
    cached = _PERSON_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < get_settings().web.cache_ttl_seconds:
        return cached[1]
    async with titles.client() as http:
        answer = await titles.get(http, "search/person", query=clean, include_adult="false")
    suggestions: list[PersonSuggestion] = []
    seen: set[str] = set()
    for record in (answer or {}).get("results") or []:
        name = str(record.get("name") or "").strip()
        key = " ".join(name.casefold().split())
        if not key or key in seen:
            continue
        seen.add(key)
        suggestions.append(PersonSuggestion(name=name, tmdb_id=record.get("id")))
        if len(suggestions) >= capped_limit:
            break
    _PERSON_CACHE[cache_key] = (time.time(), suggestions)
    return suggestions


# A person's films are the ones they acted in, directed or wrote.
_CREW_JOBS = {"Director", "Writer", "Screenplay", "Story", "Novel"}


async def _person_movies(http: httpx.AsyncClient, query: str, limit: int) -> list[DiscoverItem]:
    """Films of the people of that name: an exact name first, else TMDB's first match."""
    answer = await titles.get(http, "search/person", query=query, include_adult="false")
    people = [row for row in (answer or {}).get("results") or [] if row.get("id")]
    wanted = " ".join(query.casefold().split())
    exact = [row for row in people if " ".join(str(row.get("name") or "").casefold().split()) == wanted]
    chosen = (exact or people)[:2]
    credits = await asyncio.gather(*(titles.get(http, f"person/{row['id']}/movie_credits", language="en-US") for row in chosen))
    records: list[dict] = []
    for block in credits:
        records.extend((block or {}).get("cast") or [])
        records.extend(row for row in (block or {}).get("crew") or [] if row.get("job") in _CREW_JOBS)
    records.sort(key=lambda row: float(row.get("popularity") or 0), reverse=True)
    return _dedupe_items(_items(records, "movie"), limit)


async def _studio_movies(http: httpx.AsyncClient, query: str, limit: int) -> list[DiscoverItem]:
    """Films of the companies of that name, most popular first."""
    answer = await titles.get(http, "search/company", query=query)
    companies = [row for row in (answer or {}).get("results") or [] if row.get("id")]
    wanted = query.casefold().strip()
    companies.sort(key=lambda row: (str(row.get("name") or "").casefold() != wanted, wanted not in str(row.get("name") or "").casefold()))
    chosen = "|".join(str(row["id"]) for row in companies[:5])
    if not chosen:
        return []
    items, _total = await _rows(
        http, "discover/movie", "movie", 0, limit,
        with_companies=chosen, sort_by="popularity.desc", language="en-US", include_adult="false",
    )
    return _dedupe_items(items, limit)


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
    async with titles.client() as http:
        if mode == "person":
            items = await _person_movies(http, query.strip(), limit)
        elif mode == "studio":
            items = await _studio_movies(http, query.strip(), limit)
        else:
            items, _total = await _rows(
                http, _search_path(kind), kind, 0, limit,
                query=query.strip(), language="en-US", include_adult="false",
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
    """One UI page of a search; TMDB's own pages of 20 are joined to fit."""
    page = max(0, int(page))
    page_size = max(10, min(100, int(page_size)))
    mode = search_by.strip().casefold()
    if not is_configured() or not query.strip() or mode not in {"title", "person", "studio"}:
        return DiscoverPage([], page, page_size, 0, False)
    if kind != "movie" and mode != "title":
        return DiscoverPage([], page, page_size, 0, False)
    start, end = page * page_size, (page + 1) * page_size
    if mode != "title":
        # Credits and company films are assembled, then sliced.
        items = await search(query, kind=kind, limit=end + 1, search_by=mode)
        return DiscoverPage(
            items=items[start:end],
            page=page,
            page_size=page_size,
            total=len(items) if len(items) <= end else None,
            has_next=len(items) > end,
        )
    if page == 0:
        # The first page is the plain search (and its cache), with one row more to tell a next page.
        items = await search(query, kind=kind, limit=page_size + 1, search_by=mode)
        return DiscoverPage(items[:page_size], page, page_size, None, len(items) > page_size)
    cache_key = f"search-page:{kind}:{query.strip().casefold()}:{page}:{page_size}"
    cached = _cache_get(cache_key)
    if cached is not None:
        total = _TOTALS.get(cache_key)
        return DiscoverPage(cached, page, page_size, total, end < total if total is not None else len(cached) == page_size)
    async with titles.client() as http:
        items, total = await _rows(
            http, _search_path(kind), kind, start, page_size,
            query=query.strip(), language="en-US", include_adult="false",
        )
    _cache_put(cache_key, items)
    _TOTALS[cache_key] = total
    return DiscoverPage(items, page, page_size, total, end < total if total is not None else len(items) == page_size)


async def browse_page(kind: str, *, page: int, page_size: int = 50) -> DiscoverPage:
    """The most popular films or shows, a UI page at a time (TMDB stops after 10,000)."""
    page = max(0, int(page))
    page_size = max(10, min(100, int(page_size)))
    if not is_configured():
        return DiscoverPage([], page, page_size, 0, False)
    start, end = page * page_size, (page + 1) * page_size
    cache_key = f"browse:{kind}:{page}:{page_size}"
    cached = _cache_get(cache_key)
    if cached is not None:
        total = _TOTALS.get(cache_key)
        return DiscoverPage(cached, page, page_size, total, end < total if total is not None else bool(cached))
    async with titles.client() as http:
        items, total = await _rows(
            http, f"discover/{titles.section(kind)}", kind, start, page_size,
            sort_by="popularity.desc", language="en-US", include_adult="false", **{"vote_count.gte": 200},
        )
    _cache_put(cache_key, items)
    _TOTALS[cache_key] = total
    return DiscoverPage(items, page, page_size, total, end < total if total is not None else len(items) == page_size)


async def trending(kind: str, *, limit: int = 60) -> list[DiscoverItem]:
    """What is watched this week."""
    if not is_configured():
        return []
    cache_key = f"trending:{kind}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    async with titles.client() as http:
        items, _total = await _rows(http, f"trending/{titles.section(kind)}/week", kind, 0, limit, language="en-US")
    _cache_put(cache_key, items)
    return items


async def new_releases(kind: str, *, limit: int = 24) -> list[DiscoverItem]:
    """Popular titles first released in the last four months."""
    if not is_configured():
        return []
    cache_key = f"new:{kind}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=120)).isoformat()
    field = "primary_release_date" if kind == "movie" else "first_air_date"
    async with titles.client() as http:
        items, _total = await _rows(
            http, f"discover/{titles.section(kind)}", kind, 0, limit,
            sort_by="popularity.desc", language="en-US", include_adult="false",
            **{f"{field}.gte": since, f"{field}.lte": today.isoformat(), "vote_count.gte": 10},
        )
    items = [DiscoverItem(**{**asdict(item), "is_new": True}) for item in items]
    _cache_put(cache_key, items)
    return items


async def title_details(tmdb_id: int, *, kind: str) -> TitleDetails:
    """A title's English and German names and its release date (a show's first airing)."""
    if not is_configured() or not tmdb_id:
        return TitleDetails()
    cache_key = f"details:{kind}:{tmdb_id}"
    hit = _DETAIL_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < get_settings().web.cache_ttl_seconds:
        return hit[1]
    record = await titles.details(kind, tmdb_id)
    if not record:
        return TitleDetails()
    details = TitleDetails(
        english=titles.name_of(record) or None,
        german=titles.german_name(record),
        worldwide_release_date=titles.date_of(record),
    )
    _DETAIL_CACHE[cache_key] = (time.time(), details)
    return details


async def german_title(tmdb_id: int, *, kind: str) -> str | None:
    """Backward-compatible German-title-only lookup."""
    return (await title_details(tmdb_id, kind=kind)).german



def to_dict(item: DiscoverItem) -> dict:
    return asdict(item)


def is_released(item: DiscoverItem, *, today: datetime.date | None = None) -> bool:
    """True if the title has already been released.

    The release *date* is authoritative: anything dated in the future is hidden.
    When no date is available a future year is likewise treated as unreleased,
    and a past year as released. Only for the ambiguous current-year-without-date
    case do we consult the status hint as a last resort. This keeps upcoming
    titles out of Discover even when ``status`` is stale or missing.
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
        key = (it.tmdb_id, it.name.casefold())
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
