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

import os
import re
import time as _time
from pathlib import Path
from typing import Any

from bankai.config import get_settings
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
from bankai.torrent.selector import ScoredCandidate, TorrentSelector

log = get_logger(__name__)

# Prowlarr Newznab category IDs.
_CAT_MOVIES = [2000, 2010, 2020, 2030, 2040, 2045, 2050]
_CAT_TV = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070]

_EPISODE_TAG_RE = re.compile(r"\bS\d{1,2}\s*E\d{1,3}\b", re.IGNORECASE)


def _strip_episode_tag(text: str) -> str:
    """Remove a trailing ``SxxExx`` tag (and anything after it) from a query."""
    m = _EPISODE_TAG_RE.search(text)
    cleaned = text[: m.start()] if m else text
    return " ".join(cleaned.split()).strip(" -–—:")


def episode_search_queries(payload: dict[str, Any]) -> list[str]:
    """Ordered torrent search queries for an episode job.

    Single-episode releases routinely fail quality/size policy (a 25-min
    episode is ~1-3 GiB while ``selector.min_size_gib`` is tuned for
    feature-length content). Season packs pass the policy and the
    downstream :func:`match_episodes` step extracts the wanted episode, so
    we search the season pack first and fall back to the per-episode
    query.
    """
    query = payload["query"]
    season = payload.get("season")
    series = payload.get("series_title") or _strip_episode_tag(query)
    queries: list[str] = []
    if season is not None and series:
        queries.append(f"{series} S{int(season):02d}")
    queries.append(query)
    seen: set[str] = set()
    ordered: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            ordered.append(q)
    return ordered


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
        # Resolve settings at run time so a just-saved web preference (notably
        # min_seeders) cannot be captured by a long-lived worker instance.
        self._selector = selector

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        query = ctx.job.payload.get("query")
        if not query:
            raise PermanentWorkerError("torrent job payload missing 'query'")
        media_kind = ctx.job.payload.get("kind", "movie")
        categories = _CAT_TV if media_kind == "episode" else _CAT_MOVIES
        target_runtime = ctx.job.payload.get("target_runtime_seconds")
        try:
            target_runtime = float(target_runtime) if target_runtime is not None else None
        except (TypeError, ValueError):
            target_runtime = None
        selector = self._selector or TorrentSelector()
        manual_raw = ctx.job.payload.get("manual_candidate")
        manual_override = isinstance(manual_raw, dict)

        # ---- search ------------------------------------------------------
        # Episodes prefer a season pack (single-episode releases usually
        # fail the size/seeder policy); fall back to the per-episode query.
        if media_kind == "episode":
            search_queries = episode_search_queries(ctx.job.payload)
        else:
            search_queries = [query]

        chosen: ScoredCandidate | None = None
        attempts: list[str] = []
        all_candidates = []
        eligible_ids: set[str] = set()
        if manual_override:
            from bankai.torrent.actions import candidate_from_dict

            chosen = ScoredCandidate(
                candidate=candidate_from_dict(manual_raw),
                score=0.0,
                reasons=("manually selected",),
            )
        else:
            from bankai.torrent.actions import candidate_id

            for search_query in search_queries:
                try:
                    candidates = await self._prowlarr.search(search_query, categories=categories)
                except Exception as exc:
                    raise WorkerError(f"prowlarr search failed: {exc}") from exc
                attempts.append(f"{search_query!r}={len(candidates)}")
                if not candidates:
                    continue
                relevant = selector.relevant(candidates, query=search_query)
                all_candidates.extend(relevant)
                ranked = selector.rank(
                    relevant,
                    target_runtime_seconds=target_runtime,
                )
                eligible_ids.update(candidate_id(item.candidate) for item in ranked)
                if ranked:
                    chosen = ranked[0]
                    if search_query != query:
                        log.info("[torrent] using season pack search %r", search_query)
                    break

        if chosen is None:
            background_id = os.environ.get("BANKAI_BG_JOB_ID")
            if background_id and all_candidates and ctx.job.payload.get("wait_for_manual", True):
                from bankai.torrent import actions as torrent_actions

                torrent_actions.create_request(
                    background_id,
                    query=query,
                    candidates=all_candidates,
                    eligible_ids=eligible_ids,
                    target_runtime_seconds=target_runtime,
                )
                log.info(
                    'BANKAI_PROGRESS stage=torrent_action status=waiting pct=0 message="Waiting for torrent selection"'
                )
                log.warning(
                    "[torrent] no release met the configured size/seeder/quality policy; waiting for a manual choice"
                )
                picked = await torrent_actions.wait_for_choice(background_id, cancel_token=ctx.cancel_token)
                chosen = ScoredCandidate(picked, 0.0, ("manually selected after policy rejection",))
                manual_override = True
            else:
                raise PermanentWorkerError(f"no candidate met selector criteria (tried {', '.join(attempts) or 'nothing'})")
        policy = get_settings().selector
        log.info(
            "[torrent] picked %s (seeders=%d, configured minimum=%d, runtime=%s, score=%.1f, reasons=%s)",
            chosen.candidate.title,
            chosen.candidate.seeders,
            policy.min_seeders,
            f"{chosen.candidate.runtime_seconds / 60:.1f} min" if chosen.candidate.runtime_seconds else "unknown",
            chosen.score,
            ", ".join(chosen.reasons),
        )
        # Defensive final gate immediately before qBittorrent. This catches a
        # stale selector/settings object even if a caller constructed one long
        # before the web settings were updated. Explicit UI choices override
        # policy by design.
        if not manual_override and chosen.candidate.seeders < policy.min_seeders:
            raise PermanentWorkerError(
                f"selected release dropped below minimum seeders ({chosen.candidate.seeders} < {policy.min_seeders})"
            )
        torrent_source_url = (
            chosen.candidate.info_url
            or chosen.candidate.magnet_uri
            or chosen.candidate.download_url
        )
        background_id = os.environ.get("BANKAI_BG_JOB_ID")
        if background_id:
            from bankai.cli import bgjobs

            bgjobs.set_provenance(
                background_id,
                torrent_source_url=torrent_source_url,
                torrent_source_title=chosen.candidate.title,
            )

        # ---- add to qBittorrent -----------------------------------------
        qbit_settings = get_settings().qbittorrent
        link = chosen.candidate.magnet_uri or chosen.candidate.download_url
        save_path_override = ctx.job.payload.get("save_path") or qbit_settings.save_path
        # qBittorrent doesn't return the new hash on /add. Snapshot the
        # category hash-set before adding, then diff after to find the
        # exact new torrent (fuzzy name match was unreliable when older
        # torrents in the same category shared release tags like
        # "1080p BluRay" with the new one).
        before_listing = await self._qbit.list_torrents(category=ctx.job.payload.get("category") or qbit_settings.category)
        before_hashes = {t.hash for t in before_listing}

        # Extract the info-hash up-front so we can find the torrent even
        # when qBit silently ignores a re-add (idempotent on existing
        # hashes). For magnets: parse xt=urn:btih:. For .torrent URLs:
        # fetch the file and SHA1 the bencoded `info` dict.
        magnet_hash: str | None = None
        # Most reliable: Prowlarr reports the info-hash directly. Prefer it so
        # we always track the exact torrent we picked, even when other
        # same-title torrents already exist in qBittorrent.
        if chosen.candidate.info_hash:
            magnet_hash = chosen.candidate.info_hash.lower()
        elif link.startswith("magnet:"):
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

        if background_id:
            from bankai.torrent import actions as torrent_actions

            torrent_actions.set_active_torrent(background_id, torrent_hash)
        # A continued job may be reattaching to the exact torrent that its
        # Stop action paused. Starting an already-active torrent is harmless.
        try:
            await self._qbit.resume(torrent_hash)
        except QBittorrentError as exc:
            log.warning("could not explicitly resume torrent %s: %s", torrent_hash[:8], exc)

        # ---- wait for completion ----------------------------------------
        try:
            status = await self._qbit.wait_until_complete(
                torrent_hash,
                cancel_token=ctx.cancel_token,
                progress_cb=_log_torrent_progress,
            )
        except QBittorrentError as exc:
            raise WorkerError(f"download failed: {exc}") from exc
        if background_id:
            torrent_actions.clear_active_torrent(background_id)

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

        result: dict[str, Any] = {
            "torrent_hash": torrent_hash,
            "name": status.name,
            "source_url": torrent_source_url,
            "source_title": chosen.candidate.title,
        }
        artifact_metadata = {
            "torrent_hash": torrent_hash,
            "release": chosen.candidate.title,
            "source_url": torrent_source_url,
            "indexer": chosen.candidate.indexer,
        }
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
                    metadata=artifact_metadata,
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
                    metadata=artifact_metadata,
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
            # Never fall back to a torrent that already existed before we
            # added ours -- that is how the pipeline used to lock onto an
            # unrelated same-title torrent (e.g. a leftover 2160p) and then
            # wait forever. Only fuzzy-match among genuinely new hashes.
            pool = new_hashes if new_hashes else [t.hash for t in listings if t.hash not in before_hashes]
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


def _log_torrent_progress(status: Any) -> None:
    # Throttle: the qBittorrent poll fires every few seconds and would
    # otherwise flood the log with near-identical progress lines. Only emit
    # when the percentage moves by >=1 point or ~15s have passed. We also
    # drop the noisy ``state=`` token — the percentage/speed already convey
    # activity and the queue column doesn't need it.
    pct = max(0.0, min(100.0, status.progress * 100.0))
    now = _time.monotonic()
    last_pct = _log_torrent_progress._last_pct  # type: ignore[attr-defined]
    last_t = _log_torrent_progress._last_t  # type: ignore[attr-defined]
    if pct < 100 and abs(pct - last_pct) < 1.0 and (now - last_t) < 15.0:
        return
    _log_torrent_progress._last_pct = pct  # type: ignore[attr-defined]
    _log_torrent_progress._last_t = now  # type: ignore[attr-defined]
    log.info(
        "BANKAI_PROGRESS stage=torrent pct=%.1f speed=%s eta=%s",
        pct,
        int(status.dlspeed),
        int(status.eta),
    )


_log_torrent_progress._last_pct = -1.0  # type: ignore[attr-defined]
_log_torrent_progress._last_t = 0.0  # type: ignore[attr-defined]


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
