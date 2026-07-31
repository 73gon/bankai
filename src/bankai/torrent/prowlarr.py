"""Prowlarr API client.

Wraps the subset of Prowlarr's HTTP API we need: search across configured
indexers and return normalized :class:`TorrentCandidate` objects.

Docs: https://prowlarr.com/docs/api/
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from bankai.config import ProwlarrSettings, get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

_RES_RE = re.compile(r"\b(2160p|1440p|1080p|720p|480p)\b", re.IGNORECASE)
_CODEC_RE = re.compile(r"\b(x265|h265|HEVC|x264|h264|AV1)\b", re.IGNORECASE)
_SOURCE_RE = re.compile(r"\b(BluRay|BDRip|WEB-DL|WEBRip|HDTV|DVDRip|REMUX)\b", re.IGNORECASE)
_GROUP_RE = re.compile(r"-([A-Za-z0-9]+)$")
_MINUTES_RE = re.compile(r"\b(?P<minutes>\d{2,3})\s*(?:min|mins|minutes)\b", re.IGNORECASE)
_HOURS_RE = re.compile(r"\b(?P<hours>\d{1,2})\s*h(?:ours?)?\s*(?P<minutes>\d{1,2})?\s*m?\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b(?P<hours>\d{1,2}):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)\b")


@dataclass(frozen=True, slots=True)
class TorrentCandidate:
    """One result row from Prowlarr search, normalized."""

    title: str
    indexer: str
    indexer_id: int | None
    download_url: str
    info_url: str | None
    magnet_uri: str | None
    info_hash: str | None
    size_bytes: int
    seeders: int
    leechers: int
    publish_date: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def resolution(self) -> str | None:
        m = _RES_RE.search(self.title)
        return m.group(1).lower() if m else None

    @property
    def codec(self) -> str | None:
        m = _CODEC_RE.search(self.title)
        return m.group(1).lower() if m else None

    @property
    def source(self) -> str | None:
        m = _SOURCE_RE.search(self.title)
        return m.group(1) if m else None

    @property
    def release_group(self) -> str | None:
        # Strip a trailing file extension if present, then look for "-GROUP".
        stem = re.sub(r"\.(mkv|mp4|avi|m4v|mov)$", "", self.title, flags=re.IGNORECASE)
        m = _GROUP_RE.search(stem)
        return m.group(1) if m else None

    @property
    def runtime_seconds(self) -> float | None:
        """Best-effort runtime from Prowlarr/indexer metadata or release text.

        Runtime is not part of BitTorrent's core metainfo. Some indexers do
        expose it in custom fields, and some releases include it in the title;
        callers must therefore treat a missing value as normal.
        """
        value = _runtime_from_mapping(self.raw)
        return value if value is not None else _parse_runtime(self.title)


def _parse_runtime(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        # Generic runtime fields are commonly minutes; large values are much
        # more likely seconds (or milliseconds for very large values).
        if number >= 100_000:
            return number / 1000.0
        if number > 1000:
            return number
        return number * 60.0
    text = str(value).strip()
    if not text:
        return None
    iso = re.search(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text, re.IGNORECASE)
    if iso:
        return int(iso.group(1) or 0) * 3600 + int(iso.group(2) or 0) * 60 + int(iso.group(3) or 0)
    clock = _CLOCK_RE.search(text)
    if clock:
        return int(clock.group("hours")) * 3600 + int(clock.group("minutes")) * 60 + int(clock.group("seconds"))
    hours = _HOURS_RE.search(text)
    if hours:
        return int(hours.group("hours")) * 3600 + int(hours.group("minutes") or 0) * 60
    minutes = _MINUTES_RE.search(text)
    if minutes:
        return int(minutes.group("minutes")) * 60.0
    return None


def _runtime_from_mapping(raw: dict[str, Any]) -> float | None:
    second_keys = {
        "durationseconds",
        "durationinseconds",
        "runtimeseconds",
        "runtimeinseconds",
        "lengthseconds",
    }
    minute_keys = {
        "duration",
        "durationminutes",
        "durationinminutes",
        "runtime",
        "runtimeminutes",
        "runtimeinminutes",
        "movielength",
    }

    def walk(value: object, depth: int = 0) -> float | None:
        if depth > 3:
            return None
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r"[^a-z]", "", str(key).casefold())
                if normalized in second_keys:
                    try:
                        seconds = float(child)
                        if seconds > 0:
                            return seconds
                    except (TypeError, ValueError):
                        parsed = _parse_runtime(child)
                        if parsed is not None:
                            return parsed
                if normalized in minute_keys:
                    parsed = _parse_runtime(child)
                    if parsed is not None:
                        return parsed
            for child in value.values():
                found = walk(child, depth + 1)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child, depth + 1)
                if found is not None:
                    return found
        return None

    return walk(raw)


class ProwlarrClient:
    def __init__(self, settings: ProwlarrSettings | None = None) -> None:
        self._settings = settings or get_settings().prowlarr
        if not self._settings.api_key:
            log.warning("Prowlarr api_key is empty â€” searches will fail")
        self._client = httpx.AsyncClient(
            base_url=self._settings.url.rstrip("/"),
            headers={"X-Api-Key": self._settings.api_key},
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def indexer_unavailable_reason(self) -> str | None:
        """Return Prowlarr's explanation when no indexer can be searched.

        Prowlarr answers an aggregate search with ``[]`` both when a healthy
        search finds no releases and when every indexer has been disabled by
        its failure backoff.  Consult the health endpoint so callers can keep
        those two very different outcomes separate.
        """
        try:
            resp = await self._client.get("/api/v1/health")
            resp.raise_for_status()
            issues = resp.json()
        except Exception as exc:
            log.warning("Prowlarr health check after empty search failed: %s", exc)
            return None
        if not isinstance(issues, list):
            return None
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            message = str(issue.get("message") or "").strip()
            normalized = message.lower()
            if "indexer" in normalized and "unavailable" in normalized:
                return message
        return None

    async def search(
        self,
        query: str,
        *,
        categories: list[int] | None = None,
        limit: int = 50,
    ) -> list[TorrentCandidate]:
        params: dict[str, Any] = {"query": query, "limit": limit}
        if categories:
            params["categories"] = categories
        if self._settings.indexer_ids:
            params["indexerIds"] = self._settings.indexer_ids
        resp = await self._client.get("/api/v1/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        return [self._normalize(row) for row in data]

    @staticmethod
    def _normalize(row: dict[str, Any]) -> TorrentCandidate:
        return TorrentCandidate(
            title=row.get("title", ""),
            indexer=row.get("indexer", ""),
            indexer_id=row.get("indexerId"),
            download_url=row.get("downloadUrl") or row.get("guid", ""),
            info_url=row.get("infoUrl"),
            magnet_uri=row.get("magnetUrl"),
            info_hash=((row.get("infoHash") or "").lower() or None),
            size_bytes=int(row.get("size") or 0),
            seeders=int(row.get("seeders") or 0),
            leechers=int(row.get("leechers") or 0),
            publish_date=row.get("publishDate"),
            raw=row,
        )
