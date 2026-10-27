"""A movie or show downloaded from a torrent and filed as it is (web/direct.py).

No extract, sync or remux: qBittorrent downloads the release, its video goes
into the library folder picked for it -- a second name for the same bytes where
the volume allows, a copy elsewhere -- and the torrent is removed once filed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.processor.anime import _copy_with_sidecars, _download_root, _progress
from bankai.torrent import actions as torrent_actions
from bankai.torrent.matcher import find_video_files, pick_movie_file
from bankai.torrent.qbittorrent import QBittorrentClient, QBittorrentError
from bankai.web import direct

log = get_logger(__name__)

# A category of its own: the anime automation pauses every unfinished torrent of
# bankai's category while it is off, and that took these too (Oct 2026).
CATEGORY = "bankai-direct"


async def _link(magnet_uri: str | None, download_url: str | None) -> tuple[str | None, bytes | None, str | None]:
    """What to hand qBittorrent -- a magnet, or the .torrent file fetched here (qBittorrent,
    inside the VPN container, cannot reach Prowlarr) -- and the info hash it has."""
    from bankai.torrent.worker import fetch_torrent, torrent_info_hash

    if magnet_uri:
        return magnet_uri, None, torrent_info_hash(magnet_uri, None)
    magnet, data = await fetch_torrent(str(download_url))
    if not magnet and not data:
        raise RuntimeError("the release's download link gave no torrent")
    return magnet, data, torrent_info_hash(magnet, data)


async def _locate(client: QBittorrentClient, *, expected: str | None, before: set[str]) -> tuple[str, bool]:
    """The torrent's hash in qBittorrent, and whether it was there before this job added it."""
    for _ in range(30):
        rows = await client.list_torrents(category=None)
        if expected:
            exact = next((row.hash for row in rows if row.hash.casefold() == expected), None)
            if exact:
                return exact, exact in before
        added = [row.hash for row in rows if row.hash not in before]
        if len(added) == 1:
            return added[0], False
        await asyncio.sleep(1)
    raise QBittorrentError("could not find the torrent in qBittorrent")


async def download_direct(
    *,
    release_title: str,
    download_url: str | None,
    magnet_uri: str | None,
    info_hash: str | None,
    info_url: str | None,
    media_kind: str,
    root: str,
    title: str,
    year: int | None,
    season: int | None = None,
) -> dict[str, Any]:
    if media_kind not in {"movie", "show"}:
        raise ValueError("kind must be movie or show")
    if not (download_url or magnet_uri):
        raise ValueError("the release has neither a download link nor a magnet")
    if not title.strip():
        raise ValueError("a title is needed to name the folder")
    target = direct.target(root, media_kind)
    background_id = os.environ.get("BANKAI_BG_JOB_ID")
    if background_id:
        bgjobs.set_provenance(background_id, torrent_source_url=info_url or "", torrent_source_title=release_title)

    settings = get_settings()
    log.info('BANKAI_STAGE step=1 total=2 key=torrent label="Download"')
    qbit = QBittorrentClient()
    try:
        await qbit.login()
        before = {row.hash for row in await qbit.list_torrents(category=None)}
        magnet, torrent_file, found_hash = await _link(magnet_uri, download_url)
        expected = (info_hash or found_hash or "").casefold() or None
        save_path = Path(settings.qbittorrent.save_path) if settings.qbittorrent.save_path else None
        await qbit.add(
            magnet=magnet,
            torrent_file=None if magnet else torrent_file,
            category=CATEGORY,
            save_path=save_path,
        )
        torrent_hash, existed_before = await _locate(qbit, expected=expected, before=before)
        if background_id:
            torrent_actions.set_active_torrent(background_id, torrent_hash)
        await qbit.top_priority(torrent_hash)
        await qbit.resume(torrent_hash)
        await qbit.force_start(torrent_hash, enabled=True)
        try:
            status = await qbit.wait_until_complete(torrent_hash, progress_cb=_progress)
        finally:
            try:
                await qbit.force_start(torrent_hash, enabled=False)
            except Exception as exc:
                log.warning("Could not restore qBittorrent queueing for %s: %s", torrent_hash, exc)
        if background_id:
            torrent_actions.clear_active_torrent(background_id)

        log.info('BANKAI_STAGE step=2 total=2 key=organize label="Into %s"', target["label"])
        content = _download_root(status)
        if media_kind == "movie":
            picked = pick_movie_file(content)
            sources = [picked] if picked else []
        else:
            sources = find_video_files(content)
        if not sources:
            raise RuntimeError(f"the torrent holds no video under {content}")
        placed, left = direct.placements(
            sources, kind=media_kind, root=Path(target["root"]), title=title, year=year, season=season
        )
        filed: list[Path] = []
        already: list[Path] = []
        for index, (source, destination) in enumerate(placed):
            if destination.exists():
                # Never over a file the library has: that one may be the German one.
                log.info("[direct] already there, left as it is: %s", destination)
                already.append(destination)
                continue
            share = 100 / max(1, len(placed))
            _copy_with_sidecars(source, destination, start_percent=index * share, end_percent=(index + 1) * share)
            log.info("[direct] %s -> %s", source.name, destination)
            filed.append(destination)
        for source, reason in left:
            log.info("[direct] not filed, %s: %s", reason, source.name)
        if not filed and not already:
            raise RuntimeError("no file of the torrent could be filed: " + "; ".join(f"{s.name} ({r})" for s, r in left[:5]))
        log.info('BANKAI_PROGRESS stage=organize pct=100 status="ready"')

        if settings.paths.cleanup_after_success and not existed_before and filed:
            # The library holds its own name for the bytes (or a copy), so the torrent can go.
            try:
                await qbit.remove(torrent_hash, delete_files=True)
                log.info("[direct] removed torrent %s + files", torrent_hash[:8])
            except Exception as exc:
                log.warning("[direct] could not remove torrent %s: %s", torrent_hash[:8], exc)
        return {
            "final_path": str((filed or already)[0]),
            "paths": [str(path) for path in filed],
            "already": [str(path) for path in already],
            "not_filed": [{"name": source.name, "reason": reason} for source, reason in left],
            "torrent_hash": torrent_hash,
        }
    finally:
        await qbit.aclose()


__all__ = ["download_direct"]
