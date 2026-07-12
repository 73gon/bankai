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
