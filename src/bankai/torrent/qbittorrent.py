"""qBittorrent Web API client (thin async wrapper).

We use a subset of the API directly with httpx instead of the
``qbittorrent-api`` lib so the entire client is async-friendly. Auth is
session-cookie based.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from bankai.config import QBittorrentSettings, get_settings
from bankai.logging import get_logger

log = get_logger(__name__)


def login_succeeded(response: httpx.Response) -> bool:
    """qBittorrent up to 5.0 answers ``200 Ok.``; 5.1 onwards ``204`` and no body.

    A wrong password is ``200 Fails.`` on the old versions and ``401`` on the
    new, so neither shape can be mistaken for the other.
    """
    if response.status_code == 204:
        return True
    return response.status_code == 200 and response.text.strip() == "Ok."


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
    upspeed: int = 0
    seeds: int = 0
    peers: int = 0
    seeds_total: int = 0
    peers_total: int = 0
    added_on: int = 0
    last_activity: int = 0
    completion_on: int = 0  # when it finished downloading; 0 while it has not


_DONE_STATES = {"uploading", "stalledUP", "queuedUP", "pausedUP", "forcedUP", "checkingUP"}
_ERROR_STATES = {"error", "missingFiles"}


class QBittorrentError(Exception):
    pass


# How long a running download waits for qBittorrent to answer again before it fails.
UNREACHABLE_GIVE_UP_SECONDS = 12 * 3600.0


class TorrentStalled(QBittorrentError):
    """A torrent whose progress stopped: no metadata, or nobody to download from."""


class QBittorrentClient:
    def __init__(self, settings: QBittorrentSettings | None = None, *, timeout: float = 30.0) -> None:
        """``timeout``: per request; listings the pages wait on take a short one, so a stalled
        qBittorrent costs the sidebar seconds, not the full 30 (it held it 279 s once, Nov 2026)."""
        self._settings = settings or get_settings().qbittorrent
        self._client = httpx.AsyncClient(
            base_url=self._settings.url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
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
        if not login_succeeded(resp):
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
        torrent_file: bytes | None = None,
        category: str | None = None,
        save_path: Path | None = None,
    ) -> bool:
        """Add a torrent: a magnet, a URL qBittorrent fetches itself, or a .torrent file's bytes.
        Returns once qBittorrent acknowledges the add.

        ``True`` when it was added, ``False`` when qBittorrent already had it.
        """
        await self._ensure_login()
        if magnet and not torrent_file:
            from bankai.torrent import uploads
            from bankai.torrent.worker import torrent_info_hash

            # A .torrent dropped where a magnet goes stands behind its magnet: a private
            # tracker's magnet finds nothing, its file does.
            kept = uploads.stored(torrent_info_hash(magnet, None))
            if kept is not None:
                magnet, torrent_file = None, kept
        if not magnet and not torrent_url and not torrent_file:
            raise ValueError("magnet, torrent_url or torrent_file required")
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
        files = (
            {"torrents": ("release.torrent", torrent_file, "application/x-bittorrent")}
            if torrent_file and not magnet and not torrent_url
            else None
        )
        resp = await self._client.post("/api/v2/torrents/add", data=data, files=files)
        if resp.status_code == 409:
            # qBittorrent 5.x answers 409 Conflict for a torrent it already
            # has, where 4.x answered 200. Treated as an error, every job for a
            # torrent the release reconciler had re-added failed in its first
            # second while the download completed anyway: 259 finished
            # downloads spent all their publish retries on this and were never
            # moved. The torrent is there, which is all the caller needs; it
            # follows it by info hash from here.
            log.info("torrent already in qBittorrent; following the existing one")
            return False
        if resp.status_code != 200:
            raise QBittorrentError(f"add failed: {resp.status_code} {resp.text!r}")
        return True

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

    async def file_priority(self, torrent_hash: str, indexes: list[int], priority: int) -> None:
        """Set some of a torrent's files' priority (their ``index`` in ``files``); 0 leaves them out."""
        await self._ensure_login()
        response = await self._client.post(
            "/api/v2/torrents/filePrio",
            data={"hash": torrent_hash, "id": "|".join(str(index) for index in indexes), "priority": str(priority)},
        )
        response.raise_for_status()

    async def free_space_bytes(self) -> int | None:
        """Return free bytes for qBittorrent's configured download location."""
        await self._ensure_login()
        response = await self._client.get("/api/v2/sync/maindata")
        response.raise_for_status()
        value = response.json().get("server_state", {}).get("free_space_on_disk")
        try:
            return max(0, int(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def remove(self, torrent_hash: str, *, delete_files: bool = False, force: bool = False) -> None:
        """Remove torrents ("|"-joined hashes). A finished private one whose tracker's seeding is
        not yet done is not removed but handed to ``torrent.seeding`` (its own category), which
        removes it once it is: AvistaZ and AnimeZ ask 72 h or ratio 1, and every place bankai
        tidies a torrent away would have left a hit and run there. ``force``: removed whatever
        (a delete asked for by hand)."""
        await self._ensure_login()
        hashes = [part for part in torrent_hash.split("|") if part]
        if not force:
            from bankai.torrent import seeding

            hashes = await seeding.hold_unseeded(self, hashes)
            if not hashes:
                return
        response = await self._client.post(
            "/api/v2/torrents/delete",
            data={"hashes": "|".join(hashes), "deleteFiles": str(delete_files).lower()},
        )
        response.raise_for_status()

    async def info(self, hashes: list[str]) -> list[dict[str, Any]]:
        """qBittorrent's rows for these torrents, as it gives them (``private``, ``ratio``,
        ``seeding_time``, ...)."""
        await self._ensure_login()
        response = await self._client.get("/api/v2/torrents/info", params={"hashes": "|".join(hashes)})
        response.raise_for_status()
        return list(response.json())

    async def set_category(self, hashes: list[str], category: str, *, save_path: str | None = None) -> None:
        """Move torrents into a category, made first if qBittorrent has none of that name."""
        await self._ensure_login()
        made = await self._client.post(
            "/api/v2/torrents/createCategory", data={"category": category, "savePath": save_path or ""}
        )
        if made.status_code not in (200, 409):
            made.raise_for_status()
        response = await self._client.post(
            "/api/v2/torrents/setCategory", data={"hashes": "|".join(hashes), "category": category}
        )
        response.raise_for_status()

    async def pause(self, torrent_hash: str) -> None:
        await self._ensure_login()
        response = await self._client.post("/api/v2/torrents/stop", data={"hashes": torrent_hash})
        if response.status_code == 404:  # qBittorrent < 5 uses the old verb.
            response = await self._client.post(
                "/api/v2/torrents/pause", data={"hashes": torrent_hash}
            )
        response.raise_for_status()

    async def resume(self, torrent_hash: str) -> None:
        await self._ensure_login()
        response = await self._client.post("/api/v2/torrents/start", data={"hashes": torrent_hash})
        if response.status_code == 404:  # qBittorrent < 5 uses the old verb.
            response = await self._client.post(
                "/api/v2/torrents/resume", data={"hashes": torrent_hash}
            )
        response.raise_for_status()

    async def top_priority(self, torrent_hash: str) -> None:
        """Move a torrent ahead of the autonomous prefill backlog."""
        await self._ensure_login()
        response = await self._client.post(
            "/api/v2/torrents/topPrio", data={"hashes": torrent_hash}
        )
        response.raise_for_status()

    async def preferences(self) -> dict[str, Any]:
        """qBittorrent's settings, such as its queue limits."""
        await self._ensure_login()
        response = await self._client.get("/api/v2/app/preferences")
        response.raise_for_status()
        return dict(response.json())

    async def bottom_priority(self, hashes: list[str]) -> None:
        """Send torrents to the back of the download queue."""
        await self._ensure_login()
        response = await self._client.post(
            "/api/v2/torrents/bottomPrio", data={"hashes": "|".join(hashes)}
        )
        response.raise_for_status()

    async def recheck(self, torrent_hash: str) -> None:
        """Check the torrent's pieces on disk again (what clears an errored torrent, with a start)."""
        await self._ensure_login()
        response = await self._client.post("/api/v2/torrents/recheck", data={"hashes": torrent_hash})
        response.raise_for_status()

    async def force_start(self, torrent_hash: str, *, enabled: bool) -> None:
        """Let an active Bankai worker bypass blocked qBittorrent queue slots."""
        await self._ensure_login()
        response = await self._client.post(
            "/api/v2/torrents/setForceStart",
            data={"hashes": torrent_hash, "value": str(enabled).lower()},
        )
        response.raise_for_status()

    # ---- helpers -----------------------------------------------------------

    async def wait_until_complete(
        self,
        torrent_hash: str,
        *,
        cancel_token: asyncio.Event | None = None,
        progress_cb: Any = None,
        stall_seconds: float | None = None,
    ) -> TorrentStatus:
        """Poll until the torrent is in a "complete" state.

        ``stall_seconds``: give up (TorrentStalled) when its progress has not moved for that
        long -- no metadata, or no seeder: Modern Family S06E17 sat at 0% for hours.
        """
        interval = self._settings.poll_interval_seconds
        transport_failures = 0
        unreachable_since: float | None = None
        missing_since: float | None = None
        best, moved_at = -1.0, time.monotonic()
        while True:
            try:
                status = await self.get(torrent_hash)
                transport_failures = 0
                unreachable_since = None
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                # qBittorrent not answering, or a login it forgot (403): waited out, logged in again.
                # Ten failures (a minute and a half) used to fail the job: when the disk was busy on
                # Nov 17 2026 qBittorrent stopped answering for six minutes and 16 downloads failed.
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (401, 403, 500, 502, 503, 504):
                    raise
                transport_failures += 1
                unreachable_since = unreachable_since or time.monotonic()
                if time.monotonic() - unreachable_since > UNREACHABLE_GIVE_UP_SECONDS:
                    raise QBittorrentError(
                        f"qBittorrent did not answer for {round(UNREACHABLE_GIVE_UP_SECONDS / 3600)} h: {exc}"
                    ) from exc
                self._logged_in = False
                if transport_failures in (1, 10) or transport_failures % 30 == 0:
                    log.warning("qBittorrent does not answer (%d tries, waiting): %s", transport_failures, exc)
                await asyncio.sleep(min(60.0, max(interval, transport_failures * 2.0)))
                continue
            if status is None:
                # Gone for good, or a list qBittorrent gave while it was finding its feet again.
                missing_since = missing_since or time.monotonic()
                if time.monotonic() - missing_since > 600:
                    raise QBittorrentError(f"torrent {torrent_hash} disappeared")
                await asyncio.sleep(max(interval, 10.0))
                continue
            missing_since = None
            if progress_cb is not None:
                try:
                    progress_cb(status)
                except Exception:
                    log.exception("progress callback failed")
            if status.state in _ERROR_STATES:
                raise QBittorrentError(f"torrent in error state: {status.state}")
            if status.progress >= 1.0 or status.state in _DONE_STATES:
                return status
            if status.progress > best:
                best, moved_at = status.progress, time.monotonic()
            elif stall_seconds and time.monotonic() - moved_at >= stall_seconds:
                raise TorrentStalled(
                    f"no progress for {round(stall_seconds / 60)} min at {status.progress * 100:.1f}% "
                    f"({status.seeds_total} seeders, {status.state})"
                )
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
        upspeed=int(row.get("upspeed") or 0),
        seeds=int(row.get("num_seeds") or 0),
        peers=int(row.get("num_leechs") or 0),
        seeds_total=int(row.get("num_complete") or 0),
        peers_total=int(row.get("num_incomplete") or 0),
        added_on=int(row.get("added_on") or 0),
        last_activity=max(0, int(row.get("last_activity") or 0)),
        completion_on=max(0, int(row.get("completion_on") or 0)),
    )
