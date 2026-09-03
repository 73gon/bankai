"""The Movie Database metadata used by the dedicated anime workflow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from bankai.config import get_settings

TMDBKind = Literal["show", "movie"]
_BASE_URL = "https://api.themoviedb.org/3"
_POSTER_BASE = "https://image.tmdb.org/t/p/w342"


class TMDBError(Exception):
    """TMDB returned an unusable response."""


@dataclass(frozen=True, slots=True)
class TMDBTitle:
    tmdb_id: int
    kind: TMDBKind
    english_title: str
    japanese_title: str | None = None
    year: int | None = None
    poster_url: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TMDBEpisode:
    season: int
    episode: int
    absolute_number: int
    name: str | None = None


def is_configured() -> bool:
    metadata = get_settings().metadata
    return bool(metadata.tmdb_enabled and metadata.tmdb_api_key.strip())


class TMDBClient:
    """Small async TMDB v3 client supporting an API key or read token."""

    def __init__(
        self,
        *,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        credential = api_key.strip()
        headers = {"Accept": "application/json"}
        self._params: dict[str, str] = {}
        # TMDB API Read Access Tokens are JWT-like values beginning with eyJ.
        # Shorter v3 keys are sent through the documented api_key parameter.
        if credential.startswith("eyJ") or credential.count(".") == 2:
            headers["Authorization"] = f"Bearer {credential}"
        elif credential:
            self._params["api_key"] = credential
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=headers,
            timeout=30,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {**self._params, **(params or {})}
        response = await self._client.get(path, params=query)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TMDBError("TMDB response was not an object")
        return payload

    async def search_titles(
        self,
        query: str,
        *,
        kind: TMDBKind,
        limit: int = 8,
    ) -> list[TMDBTitle]:
        endpoint = "tv" if kind == "show" else "movie"
        english, japanese = await asyncio.gather(
            self._get(f"/search/{endpoint}", params={"query": query, "language": "en-US"}),
            self._get(f"/search/{endpoint}", params={"query": query, "language": "ja-JP"}),
        )
        merged: dict[int, dict[str, Any]] = {}
        for language, payload in (("eng", english), ("jpn", japanese)):
            for raw in payload.get("results") or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    tmdb_id = int(raw["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                item = merged.setdefault(tmdb_id, {"aliases": []})
                title = str(
                    raw.get("name")
                    or raw.get("title")
                    or raw.get("original_name")
                    or raw.get("original_title")
                    or ""
                ).strip()
                original = str(raw.get("original_name") or raw.get("original_title") or "").strip()
                if title:
                    item[f"{language}_title"] = title
                    item["aliases"].append(title)
                if original:
                    item["original_title"] = original
                    item["aliases"].append(original)
                item.setdefault("date", raw.get("first_air_date") or raw.get("release_date"))
                item.setdefault("poster_path", raw.get("poster_path"))
                item["popularity"] = max(
                    float(item.get("popularity") or 0),
                    float(raw.get("popularity") or 0),
                )

        results: list[TMDBTitle] = []
        for tmdb_id, raw in merged.items():
            english_title = str(
                raw.get("eng_title") or raw.get("original_title") or raw.get("jpn_title") or ""
            ).strip()
            if not english_title:
                continue
            japanese_title = str(raw.get("jpn_title") or "").strip() or None
            if japanese_title == english_title:
                japanese_title = None
            date = str(raw.get("date") or "")
            year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            poster_path = str(raw.get("poster_path") or "").strip()
            aliases = tuple(
                dict.fromkeys(
                    value
                    for value in raw["aliases"]
                    if value and value not in {english_title, japanese_title}
                )
            )
            results.append(
                TMDBTitle(
                    tmdb_id=tmdb_id,
                    kind=kind,
                    english_title=english_title,
                    japanese_title=japanese_title,
                    year=year,
                    poster_url=f"{_POSTER_BASE}{poster_path}" if poster_path else None,
                    aliases=aliases,
                )
            )
        results.sort(key=lambda item: float(merged[item.tmdb_id].get("popularity") or 0), reverse=True)
        return results[:limit]

    async def series_episodes(self, tmdb_id: int) -> list[TMDBEpisode]:
        series = await self._get(f"/tv/{int(tmdb_id)}", params={"language": "en-US"})
        season_numbers = sorted(
            {
                int(item["season_number"])
                for item in series.get("seasons") or []
                if isinstance(item, dict)
                and isinstance(item.get("season_number"), int)
                and int(item["season_number"]) > 0
            }
        )
        seasons = await asyncio.gather(
            *(
                self._get(
                    f"/tv/{int(tmdb_id)}/season/{season}",
                    params={"language": "en-US"},
                )
                for season in season_numbers
            )
        )
        rows: list[tuple[int, int, str | None]] = []
        for season_number, payload in zip(season_numbers, seasons, strict=True):
            for raw in payload.get("episodes") or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    episode_number = int(raw["episode_number"])
                except (KeyError, TypeError, ValueError):
                    continue
                name = str(raw.get("name") or "").strip() or None
                rows.append((season_number, episode_number, name))
        rows.sort(key=lambda item: (item[0], item[1]))
        return [
            TMDBEpisode(
                season=season,
                episode=episode,
                absolute_number=index,
                name=name,
            )
            for index, (season, episode, name) in enumerate(rows, start=1)
        ]


__all__ = [
    "TMDBClient",
    "TMDBEpisode",
    "TMDBError",
    "TMDBKind",
    "TMDBTitle",
    "is_configured",
]
