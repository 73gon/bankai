"""Application services shared by the CLI and future HTTP/UI frontends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bankai import scraper as scraper_registry
from bankai.logging import get_logger
from bankai.metadata.tvdb import TitleAlias, get_title_aliases
from bankai.queue.models import MediaKind
from bankai.scraper.base import EpisodeRef, SearchResult

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BatchMovie:
    title: str
    german_title: str | None = None
    url: str | None = None
    year: int | None = None


@dataclass(frozen=True, slots=True)
class SeriesLookupResult:
    episodes: list[EpisodeRef]
    site: str
    query: str


def parse_movie_batch(path: Path) -> list[BatchMovie]:
    """Parse a movie batch file.

    Supported line formats:

    ``English Title 2010``
    ``English Title 2010 | German Title``
    ``English Title 2010 | German Title | https://stream-url``
    """
    movies: list[BatchMovie] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        title = parts[0]
        german_title = parts[1] if len(parts) > 1 and parts[1] else None
        url = parts[2] if len(parts) > 2 and parts[2] else None
        movies.append(BatchMovie(title=title, german_title=german_title, url=url))
    return movies


def build_movie_args(movie: BatchMovie, *, site: str = "filmpalast") -> list[str]:
    # Bake the release year into the search query ("Title YYYY") so the
    # torrent search + selector can disambiguate same-name titles (e.g.
    # "Smile" 2022 vs "Smile 2" 2024) and the output filename gets the
    # correct year instead of "(unknown)".
    query = f"{movie.title} {movie.year}" if movie.year else movie.title
    args = [
        "run",
        query,
        "--de",
        movie.german_title or movie.title,
        "--site",
        site,
        "--auto",
    ]
    if movie.url:
        args.extend(["--url", movie.url])
    return args


async def search_stream_sources(
    query: str,
    *,
    site: str | None,
    limit: int,
    kind: MediaKind | None = None,
) -> list[SearchResult]:
    if site:
        try:
            backends_to_query = [(site, scraper_registry.get_backend(site))]
        except Exception as exc:
            log.warning("backend %s failed: %s", site, exc)
            return []
    else:
        backends_to_query = [
            (site_id, backend_cls)
            for site_id, backend_cls in scraper_registry.all_backends().items()
            if (
                (kind == MediaKind.MOVIE and getattr(backend_cls, "supports_movies", False))
                or (kind == MediaKind.EPISODE and getattr(backend_cls, "supports_series", False))
                or (
                    kind is None
                    and (
                        getattr(backend_cls, "supports_movies", False)
                        or getattr(backend_cls, "supports_series", False)
                    )
                )
            )
        ]
    results: list[SearchResult] = []
    for sid, cls in backends_to_query:
        backend = cls()
        try:
            hits = await backend.search(query, kind=kind, limit=limit)
        except Exception as exc:
            log.warning("backend %s failed: %s", sid, exc)
            hits = []
        finally:
            await backend.aclose()
        results.extend(hits)
    # Filmpalast remains the default choice, while Burning Series appears next
    # to it as a show-only alternative. Preserve each backend's relevance order.
    site_order = {"filmpalast": 0, "burningseries": 1}
    results.sort(key=lambda result: site_order.get(result.site, 10))
    seen: set[tuple[str, str]] = set()
    unique: list[SearchResult] = []
    for result in results:
        key = (result.site, result.url)
        if key not in seen:
            seen.add(key)
            unique.append(result)
    return unique


async def title_aliases(query: str, *, kind: MediaKind) -> list[str]:
    aliases = await get_title_aliases(query, kind=kind)
    return _dedupe_aliases(query, aliases)


async def check_stream_url(url: str, *, site: str = "filmpalast") -> dict:
    """Validate a user-supplied stream URL and report what was found.

    Returns a dict with ``ok``/``found``/``title``/``hosters`` so the UI can
    tell the user immediately whether the link resolves to a playable page.
    """
    try:
        cls = scraper_registry.get_backend(site)
        backend = cls()
    except Exception as exc:
        return {"ok": False, "found": False, "error": f"backend {site}: {exc}"}
    try:
        checker = getattr(backend, "check_url", None)
        if not callable(checker):
            return {"ok": False, "found": False, "error": f"{site} has no URL check"}
        return await checker(url)
    except Exception as exc:
        return {"ok": False, "found": False, "error": str(exc)}
    finally:
        await backend.aclose()


async def list_series_episodes(
    show: str,
    *,
    season: int,
    site: str | None,
) -> SeriesLookupResult | None:
    queries = await title_aliases(show, kind=MediaKind.EPISODE)
    sites = [site] if site else _series_sites()
    for query in queries:
        for site_id in sites:
            if site_id is None:
                continue
            eps = await _list_series_on_site(site_id, query, season)
            if eps:
                return SeriesLookupResult(episodes=eps, site=site_id, query=query)
    return None


async def _list_series_on_site(site_id: str, show: str, season: int) -> list[EpisodeRef]:
    try:
        cls = scraper_registry.get_backend(site_id)
        backend = cls()
    except Exception as exc:
        log.warning("series lookup could not open backend %s: %s", site_id, exc)
        return []
    try:
        list_season = getattr(backend, "list_season", None)
        if callable(list_season):
            episodes = await list_season(show, season)
        else:
            episodes = await _search_then_list_episodes(backend, show, season)
        return [e for e in episodes if e.season == season]
    except Exception as exc:
        log.warning("series lookup failed on %s for %r: %s", site_id, show, exc)
        return []
    finally:
        await backend.aclose()


async def _search_then_list_episodes(backend: Any, show: str, season: int) -> list[EpisodeRef]:
    hits = await backend.search(show, kind=MediaKind.EPISODE, limit=10)
    for hit in hits:
        episodes = await backend.list_episodes(hit.url)
        episodes = [e for e in episodes if e.season == season]
        if episodes:
            return episodes
    return []


def _series_sites() -> list[str]:
    sites: list[str] = []
    for site_id, cls in scraper_registry.all_backends().items():
        if getattr(cls, "supports_series", False):
            sites.append(site_id)
    preferred_order = {"filmpalast": 0, "burningseries": 1}
    sites.sort(key=lambda site_id: (preferred_order.get(site_id, 10), site_id))
    return sites


def _dedupe_aliases(query: str, aliases: list[TitleAlias]) -> list[str]:
    values = [query]
    for alias in aliases:
        for value in (alias.english_title, alias.german_title, alias.name):
            if value:
                values.append(value)
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = value.strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out
