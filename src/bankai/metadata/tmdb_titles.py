"""Films and shows on TMDB: search, details, German titles, a show's episodes.

The Movies & Shows side's metadata -- Discover, Search, the library's episode
lists, the German names Filmpalast is searched by. TheTVDB did this until
Oct 2026; one key less, and the genres and artwork already went by TMDB ids.
Anime keeps AniList.

A show's episodes come in TMDB's own order, which is the release groups' order
for nearly every show; where a show was split differently (specials counted,
seasons by year) an episode can show as missing that is on disk under another
number.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from bankai.logging import get_logger
from bankai.metadata import tmdb
from bankai.queue.models import MediaKind

log = get_logger(__name__)

_API = "https://api.themoviedb.org/3"
# TMDB takes up to 20 appended requests in one; a show's seasons come that many at a time.
_APPEND_MAX = 20


@dataclass(frozen=True, slots=True)
class TitleAlias:
    name: str | None = None
    english_title: str | None = None
    german_title: str | None = None
    aliases: tuple[str, ...] = ()
    year: int | None = None
    tmdb_id: int | None = None
    kind: MediaKind | None = None
    poster_url: str | None = None


@dataclass(frozen=True, slots=True)
class Episode:
    season: int
    episode: int
    absolute_number: int | None = None
    name: str | None = None
    aired: str | None = None
    overview: str | None = None
    runtime: int | None = None


def configured() -> bool:
    return tmdb.configured()


def client(timeout: float = 15.0) -> httpx.AsyncClient:
    headers, params = tmdb._auth()
    return httpx.AsyncClient(base_url=_API + "/", headers=headers, params=params, timeout=timeout)


def section(kind: str) -> str:
    """TMDB's word for a kind: "movie" or "tv" (bankai says "show" / "episode")."""
    return "movie" if kind in {"movie", MediaKind.MOVIE} else "tv"


def poster(path: object, size: str = "w342") -> str | None:
    return tmdb.url(str(path), size) if path else None


def year(date: object) -> int | None:
    text = str(date or "")[:4]
    return int(text) if text.isdigit() else None


async def get(http: httpx.AsyncClient, path: str, **params: Any) -> dict | None:
    """One TMDB answer, or None: a search must not fail as a whole over one call."""
    try:
        for attempt in range(3):
            response = await http.get(path.lstrip("/"), params={k: v for k, v in params.items() if v is not None})
            # TMDB allows about 50 requests a second; a search with its details asked at once went
            # over and came back empty (search/movie 429, 40 times in three days, Nov 2026).
            if response.status_code != 429 or attempt == 2:
                break
            try:
                wait = float(response.headers.get("Retry-After", 1))
            except ValueError:
                wait = 1.0
            await asyncio.sleep(min(max(wait, 0.5), 5.0))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("TMDB %s failed: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def name_of(record: dict) -> str:
    return str(record.get("title") or record.get("name") or record.get("original_title") or record.get("original_name") or "")


def date_of(record: dict) -> str | None:
    value = record.get("release_date") or record.get("first_air_date")
    return str(value)[:10] if value else None


def german_name(details: dict) -> str | None:
    """The German title from a details answer with translations, Germany's before Austria's."""
    rows = ((details.get("translations") or {}).get("translations")) or []
    german = [row for row in rows if row.get("iso_639_1") == "de"]
    german.sort(key=lambda row: row.get("iso_3166_1") != "DE")
    for row in german:
        title = str((row.get("data") or {}).get("title") or (row.get("data") or {}).get("name") or "").strip()
        if title:
            return title
    return None


def alternative_titles(details: dict) -> list[str]:
    block = details.get("alternative_titles") or {}
    rows = block.get("titles") or block.get("results") or []
    return [str(row["title"]).strip() for row in rows if str(row.get("title") or "").strip()]


async def details(kind: str, tmdb_id: int, *, http: httpx.AsyncClient | None = None) -> dict | None:
    """A film's or show's record in English, with translations, other titles and outside ids."""
    async def ask(h: httpx.AsyncClient) -> dict | None:
        return await get(
            h, f"{section(kind)}/{tmdb_id}", language="en-US",
            append_to_response="translations,alternative_titles,external_ids",
        )

    if http is not None:
        return await ask(http)
    async with client() as h:
        return await ask(h)


def alias_from(record: dict, kind: MediaKind, full: dict | None = None) -> TitleAlias:
    english = name_of(full or record)
    return TitleAlias(
        name=english,
        english_title=english,
        german_title=german_name(full) if full else None,
        aliases=tuple(
            value for value in dict.fromkeys(
                [str(record.get("original_title") or record.get("original_name") or ""), *alternative_titles(full or {})]
            ) if value and value != english
        ),
        year=year(date_of(full or record)),
        tmdb_id=int(record["id"]) if str(record.get("id") or "").isdigit() else None,
        kind=kind,
        poster_url=poster((full or record).get("poster_path")),
    )


async def get_title_aliases(query: str, *, kind: MediaKind, detailed: int = 5) -> list[TitleAlias]:
    """TMDB's matches for a name, the first few with their German and other titles."""
    if not configured() or not query.strip():
        return []
    async with client() as http:
        found = await get(http, f"search/{section(kind)}", query=query.strip(), language="en-US", include_adult="false")
        records = [row for row in (found or {}).get("results") or [] if isinstance(row, dict) and row.get("id")]
        fulls = await asyncio.gather(*(details(kind, int(row["id"]), http=http) for row in records[:detailed]))
    return [alias_from(row, kind, fulls[index] if index < len(fulls) else None) for index, row in enumerate(records)]


async def show(tmdb_id: int) -> dict | None:
    """A show and every episode TMDB lists, specials (season 0) too.

    ``{tmdb_id, name, year, status, poster_url, seasons: [{season, episodes, air_date, name}],
    episodes: [Episode], runtime, german, tvdb_id}``; None when TMDB has no such show.
    """
    if not configured():
        return None
    async with client(timeout=30.0) as http:
        record = await details("tv", tmdb_id, http=http)
        if not record:
            return None
        numbers = [int(row["season_number"]) for row in record.get("seasons") or [] if isinstance(row.get("season_number"), int)]
        chunks = [numbers[i : i + _APPEND_MAX] for i in range(0, len(numbers), _APPEND_MAX)]
        answers = await asyncio.gather(*(
            get(http, f"tv/{tmdb_id}", language="en-US", append_to_response=",".join(f"season/{n}" for n in chunk))
            for chunk in chunks
        ))
    episodes: list[Episode] = []
    for answer in answers:
        for number in numbers:
            block = (answer or {}).get(f"season/{number}")
            for row in (block or {}).get("episodes") or []:
                if not isinstance(row.get("episode_number"), int):
                    continue
                episodes.append(Episode(
                    season=number,
                    episode=int(row["episode_number"]),
                    name=row.get("name") or None,
                    aired=row.get("air_date") or None,
                    overview=row.get("overview") or None,
                    runtime=row.get("runtime") if isinstance(row.get("runtime"), int) else None,
                ))
    episodes.sort(key=lambda row: (row.season, row.episode))
    runtimes = [value for value in record.get("episode_run_time") or [] if isinstance(value, int)]
    return {
        "tmdb_id": tmdb_id,
        "name": name_of(record),
        "german": german_name(record),
        "year": year(record.get("first_air_date")),
        "status": record.get("status"),
        "poster_url": poster(record.get("poster_path")),
        "tvdb_id": (record.get("external_ids") or {}).get("tvdb_id"),
        "imdb_id": (record.get("external_ids") or {}).get("imdb_id"),
        "runtime": runtimes[0] if runtimes else None,
        "seasons": [
            {
                "season": int(row["season_number"]),
                "episodes": int(row.get("episode_count") or 0),
                "air_date": row.get("air_date"),
                "name": row.get("name"),
            }
            for row in record.get("seasons") or []
            if isinstance(row.get("season_number"), int)
        ],
        "episodes": episodes,
    }


__all__ = ["Episode", "TitleAlias", "configured", "details", "german_name", "get_title_aliases", "show"]
