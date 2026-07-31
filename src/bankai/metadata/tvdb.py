"""Optional TheTVDB v4 metadata lookup.

The provider is deliberately fail-soft: missing credentials or transient TVDB
errors should never block the local scraper flow.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.queue.models import MediaKind

log = get_logger(__name__)

_BASE_URL = "https://api4.thetvdb.com/v4"


class TVDBError(Exception):
    """TheTVDB returned an unusable response."""


@dataclass(frozen=True, slots=True)
class TitleAlias:
    name: str | None = None
    english_title: str | None = None
    german_title: str | None = None
    japanese_title: str | None = None
    year: int | None = None
    tvdb_id: int | None = None
    kind: MediaKind | None = None


@dataclass(frozen=True, slots=True)
class TVDBEpisode:
    season: int
    episode: int
    absolute_number: int | None = None
    name: str | None = None


class TVDBClient:
    def __init__(
        self,
        *,
        api_key: str,
        pin: str = "",
        languages: list[str] | None = None,
        base_url: str = _BASE_URL,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._pin = pin
        self._languages = [
            _normalise_language(language) for language in (languages or ["deu", "eng"])
        ]
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            transport=transport,
        )
        self._token: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search_aliases(
        self,
        query: str,
        *,
        kind: MediaKind,
        limit: int = 5,
    ) -> list[TitleAlias]:
        clean = query.strip()
        if not clean:
            return []
        token = await self._ensure_token()
        tvdb_type = _tvdb_search_type(kind)
        params: dict[str, str | int] = {"query": clean, "limit": limit}
        if tvdb_type:
            params["type"] = tvdb_type
        response = await self._client.get(
            "search",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        response.raise_for_status()
        payload = _as_dict(response.json())
        items = _as_list(payload.get("data"))
        aliases: list[TitleAlias] = []
        for raw_item in items:
            item = _as_dict(raw_item)
            if not item:
                continue
            record_type = _record_type(item, fallback=tvdb_type)
            tvdb_id = _record_id(item)
            base = _alias_from_record(item, kind=kind)
            translations = await self._fetch_translations(record_type, tvdb_id, token)
            worldwide_date = await self._fetch_worldwide_release(record_type, tvdb_id, token)
            alias = TitleAlias(
                name=base.name,
                english_title=translations.get("eng") or base.english_title,
                german_title=translations.get("deu") or base.german_title,
                japanese_title=translations.get("jpn") or base.japanese_title,
                year=int(worldwide_date[:4]) if worldwide_date else base.year,
                tvdb_id=tvdb_id,
                kind=base.kind,
            )
            if alias.name or alias.english_title or alias.german_title:
                aliases.append(alias)
        return aliases

    async def series_episodes(self, tvdb_id: int) -> list[TVDBEpisode]:
        """Return TVDB's default-order episode map for one series."""
        token = await self._ensure_token()
        episodes: list[TVDBEpisode] = []
        page = 0
        while page < 100:
            response = await self._client.get(
                f"series/{int(tvdb_id)}/episodes/default",
                headers={"Authorization": f"Bearer {token}"},
                params={"page": page},
            )
            response.raise_for_status()
            payload = _as_dict(response.json())
            data = _as_dict(payload.get("data"))
            rows = _as_list(data.get("episodes"))
            for raw in rows:
                row = _as_dict(raw)
                season = _optional_int(row.get("seasonNumber"))
                episode = _optional_int(row.get("number"))
                if season is None or episode is None:
                    continue
                episodes.append(
                    TVDBEpisode(
                        season=season,
                        episode=episode,
                        absolute_number=_optional_int(row.get("absoluteNumber")),
                        name=_first_text(row, "name", "title"),
                    )
                )
            links = _as_dict(payload.get("links")) or _as_dict(data.get("links"))
            if not rows or not links.get("next"):
                break
            page += 1
        return episodes

    async def _fetch_worldwide_release(
        self,
        record_type: str | None,
        tvdb_id: int | None,
        token: str,
    ) -> str | None:
        """Return TVDB's explicit worldwide movie release date.

        Search records expose a generic ``year`` that can represent an early
        country or festival release. The extended movie record carries the
        country-specific release list, including TVDB's ``Worldwide`` entry.
        """
        if record_type != "movie" or tvdb_id is None:
            return None
        try:
            response = await self._client.get(
                f"movies/{tvdb_id}/extended",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.debug("TVDB worldwide release lookup failed for %s: %s", tvdb_id, exc)
            return None
        return worldwide_release_date(_as_dict(_as_dict(response.json()).get("data")))

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        request: dict[str, str] = {"apikey": self._api_key}
        if self._pin:
            request["pin"] = self._pin
        response = await self._client.post("login", json=request)
        response.raise_for_status()
        payload = _as_dict(response.json())
        data = _as_dict(payload.get("data"))
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise TVDBError("TVDB login response did not include a bearer token")
        self._token = token
        return token

    async def _fetch_translations(
        self,
        record_type: str | None,
        tvdb_id: int | None,
        token: str,
    ) -> dict[str, str]:
        endpoint_type = _translation_endpoint_type(record_type)
        if endpoint_type is None or tvdb_id is None:
            return {}
        translations: dict[str, str] = {}
        for lang in self._languages:
            try:
                response = await self._client.get(
                    f"{endpoint_type}/{tvdb_id}/translations/{lang}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code == 404:
                    continue
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.debug("TVDB translation lookup failed for %s/%s: %s", tvdb_id, lang, exc)
                continue
            payload = _as_dict(response.json())
            data = _as_dict(payload.get("data"))
            title = _title_from_translation(data)
            if title:
                translations[lang] = title
        return translations


async def get_title_aliases(query: str, *, kind: MediaKind) -> list[TitleAlias]:
    settings = get_settings().metadata
    if not settings.tvdb_enabled or not settings.tvdb_api_key:
        return []
    client = TVDBClient(
        api_key=settings.tvdb_api_key,
        pin=settings.tvdb_pin,
        languages=settings.tvdb_languages,
    )
    try:
        return await client.search_aliases(query, kind=kind)
    except (TVDBError, httpx.HTTPError) as exc:
        log.warning("TVDB lookup failed for %r: %s", query, exc)
        return []
    finally:
        await client.aclose()


def _alias_from_record(record: dict[str, Any], *, kind: MediaKind) -> TitleAlias:
    name = _first_text(record, "name", "title", "slug")
    english = _translated_title(record, "eng")
    german = _translated_title(record, "deu")
    record_type = _record_type(record, fallback=None)
    inferred_kind = MediaKind.MOVIE if record_type == "movie" else kind
    if record_type == "series":
        inferred_kind = MediaKind.EPISODE
    return TitleAlias(
        name=name,
        english_title=english,
        german_title=german,
        japanese_title=_translated_title(record, "jpn"),
        year=_year(record),
        tvdb_id=_record_id(record),
        kind=inferred_kind,
    )


def _tvdb_search_type(kind: MediaKind) -> str:
    return "movie" if kind is MediaKind.MOVIE else "series"


def _translation_endpoint_type(record_type: str | None) -> str | None:
    if record_type == "movie":
        return "movies"
    if record_type == "series":
        return "series"
    return None


def _record_type(record: dict[str, Any], *, fallback: str | None) -> str | None:
    raw = record.get("type") or record.get("entityType") or fallback
    if not isinstance(raw, str):
        return fallback
    raw = raw.lower()
    if raw in {"movie", "movies"}:
        return "movie"
    if raw in {"series", "tv", "show"}:
        return "series"
    return fallback


def _record_id(record: dict[str, Any]) -> int | None:
    for key in ("tvdb_id", "tvdbId", "id", "objectID", "objectId"):
        raw = record.get(key)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            match = re.search(r"\d+", raw)
            if match:
                return int(match.group(0))
    return None


def _year(record: dict[str, Any]) -> int | None:
    for key in ("year", "releaseYear", "first_air_time", "firstAired", "release_date"):
        raw = record.get(key)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            match = re.search(r"\b(19|20)\d{2}\b", raw)
            if match:
                return int(match.group(0))
    return None


def _optional_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def worldwide_release_date(record: dict[str, Any]) -> str | None:
    """Pick the explicit Worldwide date from a TVDB extended movie record.

    TVDB release rows contain ``country``, ``date`` and ``detail``. We do not
    substitute the earliest national release because that is precisely what
    makes titles such as *300* appear as 2006 instead of their 2007 worldwide
    release. If multiple Worldwide rows exist, the earliest valid Worldwide
    date is deterministic and still stays within that release scope.
    """
    candidates: list[str] = []
    for raw in _as_list(record.get("releases")):
        release = _as_dict(raw)
        scope = " ".join(str(release.get(key) or "") for key in ("country", "detail")).casefold()
        normalized_scope = re.sub(r"[^a-z]+", "", scope)
        if "worldwide" not in normalized_scope and "global" not in normalized_scope:
            continue
        raw_date = str(release.get("date") or "")[:10]
        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError:
            continue
        candidates.append(parsed.isoformat())
    return min(candidates) if candidates else None


def _translated_title(record: dict[str, Any], lang: str) -> str | None:
    for key in ("translations", "name_translated", "title_translated"):
        title = _title_from_translation_map(record.get(key), lang)
        if title:
            return title
    return None


def _title_from_translation_map(raw: object, lang: str) -> str | None:
    if isinstance(raw, str):
        text = raw
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            if lang == "eng":
                return text.strip() or None
            return None
    if isinstance(raw, dict):
        for key in (lang, _short_language(lang)):
            value = raw.get(key)
            title = _title_from_translation(value)
            if title:
                return title
    if isinstance(raw, list):
        for item in raw:
            data = _as_dict(item)
            code = _normalise_language(str(data.get("language") or data.get("lang") or ""))
            if code == lang:
                title = _title_from_translation(data)
                if title:
                    return title
    return None


def _title_from_translation(raw: object) -> str | None:
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        return _first_text(raw, "name", "title", "translatedName", "officialName")
    return None


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = data.get(key)
        if isinstance(raw, str):
            clean = raw.strip()
            if clean:
                return clean
    return None


def _normalise_language(lang: str) -> str:
    clean = lang.strip().lower()
    if clean in {"de", "ger", "deu"}:
        return "deu"
    if clean in {"en", "eng"}:
        return "eng"
    if clean in {"ja", "jp", "jpn"}:
        return "jpn"
    return clean


def _short_language(lang: str) -> str:
    if lang == "deu":
        return "de"
    if lang == "eng":
        return "en"
    if lang == "jpn":
        return "ja"
    return lang


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
