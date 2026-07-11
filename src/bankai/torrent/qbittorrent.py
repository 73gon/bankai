"""qBittorrent Web API client (thin async wrapper).

We use a subset of the API directly with httpx instead of the
``qbittorrent-api`` lib so the entire client is async-friendly. Auth is
session-cookie based.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from bankai.config import QBittorrentSettings, get_settings
from bankai.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TorrentStatus:
    hash: str
    name: str
    state: str  # "downloading", "uploading", "stalledDL", "queuedDL", "pausedDL", ...
    progress: float  # 0.0 .. 1.0
    save_path: str
    content_path: str
    size_bytes: int
    dlspeed: int
    eta: int


_DONE_STATES = {"uploading", "stalledUP", "queuedUP", "pausedUP", "forcedUP", "checkingUP"}
_ERROR_STATES = {"error", "missingFiles"}


class QBittorrentError(Exception):
    pass


class QBittorrentClient:
    def __init__(self, settings: QBittorrentSettings | None = None) -> None:
        self._settings = settings or get_settings().qbittorrent
        self._client = httpx.AsyncClient(
            base_url=self._settings.url.rstrip("/"),
            timeout=30.0,
            follow_redirects=True,
        )
        self._logged_in = False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> QBittorrentClient:
        await self.login()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---- auth --------------------------------------------------------------

    async def login(self) -> None:
        resp = await self._client.post(
            "/api/v2/auth/login",
            data={"username": self._settings.username, "password": self._settings.password},
            headers={"Referer": self._settings.url},
        )
        if resp.status_code != 200 or resp.text.strip() != "Ok.":
            raise QBittorrentError(f"login failed: {resp.status_code} {resp.text!r}")
        self._logged_in = True

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.login()

    # ---- add ---------------------------------------------------------------

    async def add(
        self,
        *,
        magnet: str | None = None,
        torrent_url: str | None = None,
        category: str | None = None,
        save_path: Path | None = None,
    ) -> None:
        """Add a torrent. Returns once qBittorrent acknowledges the add."""
        await self._ensure_login()
        if not magnet and not torrent_url:
            raise ValueError("magnet or torrent_url required")
        data: dict[str, Any] = {
            "category": category or self._settings.category,
            "autoTMM": "false",
        }
        if save_path is not None:
            # qBittorrent runs on a POSIX host, so the save path must use
            # forward slashes. On Windows ``str(Path("/downloads/bankai"))``
            # yields ``\downloads\bankai`` which qBittorrent then treats as a
            # single literal directory name -- producing bogus folders like
            # ``downloads/\downloads\bankai``. Normalise to forward slashes.
            data["savepath"] = str(save_path).replace("\\", "/")
        if magnet:
            data["urls"] = magnet
        elif torrent_url:
            data["urls"] = torrent_url
        resp = await self._client.post("/api/v2/torrents/add", data=data)
        if resp.status_code != 200:
            raise QBittorrentError(f"add failed: {resp.status_code} {resp.text!r}")

    # ---- status ------------------------------------------------------------

    async def list_torrents(self, *, category: str | None = None) -> list[TorrentStatus]:
        await self._ensure_login()
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        resp = await self._client.get("/api/v2/torrents/info", params=params)
        resp.raise_for_status()
        rows = resp.json()
        return [_to_status(r) for r in rows]

    async def get(self, torrent_hash: str) -> TorrentStatus | None:
        rows = await self.list_torrents()
        for s in rows:
            if s.hash == torrent_hash:
                return s
        return None

    async def files(self, torrent_hash: str) -> list[dict[str, Any]]:
        await self._ensure_login()
        resp = await self._client.get("/api/v2/torrents/files", params={"hash": torrent_hash})
        resp.raise_for_status()
        return list(resp.json())

    async def remove(self, torrent_hash: str, *, delete_files: bool = False) -> None:
        await self._ensure_login()
        await self._client.post(
            "/api/v2/torrents/delete",
            data={"hashes": torrent_hash, "deleteFiles": str(delete_files).lower()},
        )

    # ---- helpers -----------------------------------------------------------

    async def wait_until_complete(
        self,
        torrent_hash: str,
        *,
        cancel_token: asyncio.Event | None = None,
        progress_cb: Any = None,
    ) -> TorrentStatus:
        """Poll until the torrent is in a "complete" state."""
        interval = self._settings.poll_interval_seconds
        while True:
            status = await self.get(torrent_hash)
            if status is None:
                raise QBittorrentError(f"torrent {torrent_hash} disappeared")
            if progress_cb is not None:
                try:
                    progress_cb(status)
                except Exception:
                    log.exception("progress callback failed")
            if status.state in _ERROR_STATES:
                raise QBittorrentError(f"torrent in error state: {status.state}")
            if status.progress >= 1.0 or status.state in _DONE_STATES:
                return status
            if cancel_token is not None and cancel_token.is_set():
                raise QBittorrentError("cancelled")
            await asyncio.sleep(interval)


def _to_status(row: dict[str, Any]) -> TorrentStatus:
    return TorrentStatus(
        hash=row["hash"],
        name=row.get("name", ""),
        state=row.get("state", ""),
        progress=float(row.get("progress", 0.0)),
        save_path=row.get("save_path", ""),
        content_path=row.get("content_path", ""),
        size_bytes=int(row.get("size") or 0),
        dlspeed=int(row.get("dlspeed") or 0),
        eta=int(row.get("eta") or 0),
    )
