"""Torrent search â†’ download worker.

Pipeline stage 5: search Prowlarr for a media item, score and pick the
best release, hand it to qBittorrent, wait until complete, and record
the resulting local file path as a ``video`` :class:`Artifact`.

Job payload schema
------------------

For a movie::

    {"query": "Inception 2010", "kind": "movie"}

For an episode (one job per episode; the pipeline orchestrator handles
batching)::

    {"query": "Burning Series S01E01", "kind": "episode",
     "season": 1, "episode": 1, "series_title": "Burning Series"}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bankai.logging import get_logger
from bankai.queue.models import Artifact, JobKind
from bankai.queue.worker import (
    PermanentWorkerError,
    Worker,
    WorkerContext,
    WorkerError,
)
from bankai.torrent.matcher import EpisodeFile, find_video_files, match_episodes, pick_movie_file
from bankai.torrent.prowlarr import ProwlarrClient
from bankai.torrent.qbittorrent import QBittorrentClient, QBittorrentError
from bankai.torrent.selector import TorrentSelector

log = get_logger(__name__)

# Prowlarr Newznab category IDs.
_CAT_MOVIES = [2000, 2010, 2020, 2030, 2040, 2045, 2050]
_CAT_TV = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070]


class TorrentWorker(Worker):
    kind = JobKind.TORRENT

    def __init__(
        self,
        *,
        prowlarr: ProwlarrClient | None = None,
        qbit: QBittorrentClient | None = None,
        selector: TorrentSelector | None = None,
    ) -> None:
        self._prowlarr = prowlarr or ProwlarrClient()
        self._qbit = qbit or QBittorrentClient()
        self._selector = selector or TorrentSelector()

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        query = ctx.job.payload.get("query")
        if not query:
            raise PermanentWorkerError("torrent job payload missing 'query'")
        media_kind = ctx.job.payload.get("kind", "movie")
        categories = _CAT_TV if media_kind == "episode" else _CAT_MOVIES

        # ---- search ------------------------------------------------------
        try:
            candidates = await self._prowlarr.search(query, categories=categories)
        except Exception as exc:
            raise WorkerError(f"prowlarr search failed: {exc}") from exc
        if not candidates:
            raise PermanentWorkerError(f"no torrent candidates for {query!r}")

        chosen = self._selector.select(candidates, query=query)
        if chosen is None:
            raise PermanentWorkerError(
                f"no candidate met selector criteria (had {len(candidates)} hits)"
            )
        log.info(
            "[torrent] picked %s (score=%.1f, reasons=%s)",
            chosen.candidate.title,
            chosen.score,
            ", ".join(chosen.reasons),
        )

        # ---- add to qBittorrent -----------------------------------------
        from bankai.config import get_settings

        qbit_settings = get_settings().qbittorrent
        link = chosen.candidate.magnet_uri or chosen.candidate.download_url
        save_path_override = ctx.job.payload.get("save_path") or qbit_settings.save_path
        # qBittorrent doesn't return the new hash on /add. Snapshot the
        # category hash-set before adding, then diff after to find the
        # exact new torrent (fuzzy name match was unreliable when older
        # torrents in the same category shared release tags like
        # "1080p BluRay" with the new one).
        before_listing = await self._qbit.list_torrents(
            category=ctx.job.payload.get("category") or qbit_settings.category
        )
        before_hashes = {t.hash for t in before_listing}

        # Extract the info-hash up-front so we can find the torrent even
        # when qBit silently ignores a re-add (idempotent on existing
        # hashes). For magnets: parse xt=urn:btih:. For .torrent URLs:
        # fetch the file and SHA1 the bencoded `info` dict.
        magnet_hash: str | None = None
        if link.startswith("magnet:"):
            import re as _re

            m = _re.search(r"xt=urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})", link)
            if m:
                magnet_hash = m.group(1).lower()
        else:
            try:
                magnet_hash = await _fetch_torrent_info_hash(link)
            except Exception as exc:
                log.warning("could not pre-compute info-hash for %s: %s", link, exc)

        try:
            await self._qbit.add(
                magnet=link if link.startswith("magnet:") else None,
                torrent_url=None if link.startswith("magnet:") else link,
                category=ctx.job.payload.get("category"),
                save_path=Path(save_path_override) if save_path_override else None,
            )
        except QBittorrentError as exc:
            raise WorkerError(f"qbittorrent add failed: {exc}") from exc

        torrent_hash = await self._locate_added(
            chosen.candidate.title,
            before_hashes=before_hashes,
            known_hash=magnet_hash,
        )
        if torrent_hash is None:
            raise WorkerError("could not locate just-added torrent in qBittorrent")

        # ---- wait for completion ----------------------------------------
        try:
            status = await self._qbit.wait_until_complete(
                torrent_hash, cancel_token=ctx.cancel_token
            )
        except QBittorrentError as exc:
            raise WorkerError(f"download failed: {exc}") from exc

        # ---- pick local file(s) -----------------------------------------
        # Translate container paths to host paths via configured map.
        def _translate(p: str) -> str:
            for remote, local in qbit_settings.path_map.items():
                if p.startswith(remote):
                    return local + p[len(remote) :]
            return p

        save_path_str = _translate(status.save_path)
        content_path_str = _translate(status.content_path) if status.content_path else ""

        # Prefer ``content_path`` (the exact file or folder) over ``save_path``
        # (the parent dir) so we don't pick the wrong file when the save dir
        # contains many torrents.
        if content_path_str and Path(content_path_str).exists():
            save_root = Path(content_path_str)
        else:
            save_root = Path(save_path_str) / status.name
            if not save_root.exists():
                save_root = Path(save_path_str)

        result: dict[str, Any] = {"torrent_hash": torrent_hash, "name": status.name}
        assert ctx.job.id is not None
        if media_kind == "episode":
            # Single episode jobs: locate the matching file in the dir.
            files = find_video_files(save_root)
            if not files:
                raise WorkerError(f"no video files under {save_root}")
            # If the torrent is a season pack, match by S/E numbers.
            ep_season = ctx.job.payload.get("season")
            ep_number = ctx.job.payload.get("episode")
            picked: Path | None = None
            if ep_season is not None and ep_number is not None:
                from bankai.scraper.base import EpisodeRef

                refs = [
                    EpisodeRef(
                        site="",
                        series_title=ctx.job.payload.get("series_title", ""),
                        season=int(ep_season),
                        episode=int(ep_number),
                        title="",
                        url="",
                    )
                ]
                matched: list[EpisodeFile] = match_episodes(save_root, refs)
                picked = matched[0].path if matched else None
            picked = picked or max(files, key=lambda p: p.stat().st_size)
            artifact = ctx.repo.add_artifact(
                Artifact(
                    job_id=ctx.job.id,
                    kind="video",
                    path=picked,
                    size_bytes=picked.stat().st_size,
                    metadata={"torrent_hash": torrent_hash, "release": chosen.candidate.title},
                )
            )
            result["artifact_id"] = artifact.id
            result["path"] = str(picked)
        else:
            picked = pick_movie_file(save_root)
            if picked is None:
                raise WorkerError(f"no video file found in {save_root}")
            artifact = ctx.repo.add_artifact(
                Artifact(
                    job_id=ctx.job.id,
                    kind="video",
                    path=picked,
                    size_bytes=picked.stat().st_size,
                    metadata={"torrent_hash": torrent_hash, "release": chosen.candidate.title},
                )
            )
            result["artifact_id"] = artifact.id
            result["path"] = str(picked)
        return result

    async def _locate_added(
        self,
        expected_name: str,
        *,
        before_hashes: set[str] | None = None,
        known_hash: str | None = None,
    ) -> str | None:
        """Find the just-added torrent.

        Strategy (in order of reliability):
            1. If we extracted the info-hash from the magnet URI up-front,
               look it up directly across all torrents.
            2. Otherwise, diff the pre/post-add hash listing in our
               category and return whichever hash is *new*.
            3. Fall back to fuzzy title-token match against any new
               hashes (last resort, only when multiple were added).
        """
        import asyncio as _asyncio

        from bankai.config import get_settings

        category = get_settings().qbittorrent.category
        before_hashes = before_hashes or set()

        for _ in range(20):
            # 1. direct lookup by info-hash (most reliable)
            if known_hash:
                all_listings = await self._qbit.list_torrents(category=None)
                for t in all_listings:
                    if t.hash.lower() == known_hash:
                        return t.hash
            # 2. diff in category
            listings = await self._qbit.list_torrents(category=category)
            new_hashes = [t.hash for t in listings if t.hash not in before_hashes]
            if len(new_hashes) == 1:
                return new_hashes[0]
            # 3. fuzzy token match. If we have new hashes, only consider
            # those; otherwise (qBit silently no-op'd a duplicate add)
            # fall back to matching against any torrent in the category.
            normalized = expected_name.replace(".", " ").replace("_", " ").lower()
            norm_tokens = {t for t in normalized.split() if len(t) >= 3}
            stop = {
                "1080p",
                "2160p",
                "720p",
                "480p",
                "bluray",
                "web",
                "webrip",
                "web-dl",
                "x264",
                "x265",
                "h264",
                "h265",
                "hevc",
                "aac",
                "ac3",
                "dts",
                "atmos",
                "remux",
                "hdr",
                "dv",
                "the",
                "and",
            }
            content_tokens = norm_tokens - stop
            pool = new_hashes if new_hashes else [t.hash for t in listings]
            min_match = 1 if new_hashes else 2
            best: tuple[int, str] | None = None
            for t in listings:
                if t.hash not in pool:
                    continue
                cand = set(t.name.replace(".", " ").replace("_", " ").lower().split())
                shared = len(cand & content_tokens)
                if best is None or shared > best[0]:
                    best = (shared, t.hash)
            if best and best[0] >= min_match:
                return best[1]
            await _asyncio.sleep(1.0)
        return None


async def _fetch_torrent_info_hash(url: str) -> str | None:
    """Resolve a torrent download URL to its info-hash (hex).

    Handles two cases:
      1. The URL redirects to a ``magnet:`` link (typical for Prowlarr).
         We parse ``xt=urn:btih:`` from the Location header.
      2. The URL serves a .torrent file. We bencode-decode the ``info``
         dict and SHA1 it.
    """
    import hashlib
    import re as _re

    import httpx

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        # Walk redirects manually so we can intercept magnet: targets.
        cur = url
        for _ in range(5):
            r = await client.get(cur)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location", "")
                if loc.startswith("magnet:"):
                    m = _re.search(r"xt=urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})", loc)
                    return m.group(1).lower() if m else None
                cur = loc
                continue
            r.raise_for_status()
            data = r.content
            break
        else:
            return None

    # Bencoded .torrent: locate the `info` dict and SHA1 it.
    key = b"4:info"
    idx = data.find(key)
    if idx < 0:
        return None
    start = idx + len(key)
    end = _bencode_end(data, start)
    if end is None:
        return None
    return hashlib.sha1(data[start:end]).hexdigest().lower()


def _bencode_end(data: bytes, pos: int) -> int | None:
    """Return the index just past the bencoded value starting at ``pos``."""
    if pos >= len(data):
        return None
    c = data[pos : pos + 1]
    if c == b"d" or c == b"l":
        pos += 1
        while pos < len(data) and data[pos : pos + 1] != b"e":
            pos = _bencode_end(data, pos) or -1
            if pos < 0:
                return None
        return pos + 1
    if c == b"i":
        end = data.find(b"e", pos)
        return end + 1 if end > 0 else None
    if c.isdigit():
        colon = data.find(b":", pos)
        if colon < 0:
            return None
        length = int(data[pos:colon])
        return colon + 1 + length
    return None
