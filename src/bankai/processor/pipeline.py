"""Pipeline orchestrator worker.

A PIPELINE job represents a user-facing intent ("download Inception with
its German dub") and chains the four worker stages:

    EXTRACT  â”€â”
              â”œâ”€â†’ SYNC â”€â†’ REMUX â”€â†’ done
    TORRENT â”€â”˜

Implementation strategy
-----------------------

Rather than fan out into discrete child jobs (which would require the
dispatcher to support job dependencies), the pipeline worker calls the
stage workers' ``run`` method directly with synthesized
:class:`WorkerContext` instances. This keeps state in one place while
still benefiting from each stage's error semantics.

Job payload schema::

    {
        "query": "Inception 2010",          # used for Prowlarr search
        "kind": "movie",
        "stream_url": "https://filmpalast.to/...",
        "stream_hint": "ytdlp" | "playwright" | "direct",
        "stream_site": "filmpalast",
        "out": "/library/Inception (2010) [ger].mkv",
        # ... optional language/track_name/offset overrides
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.processor.extractor import ExtractWorker
from bankai.processor.remux import RemuxWorker
from bankai.processor.sync import SyncWorker
from bankai.queue.models import Job, JobKind, JobStatus
from bankai.queue.worker import (
    PermanentWorkerError,
    Worker,
    WorkerContext,
)
from bankai.torrent.worker import TorrentWorker

log = get_logger(__name__)


class PipelineWorker(Worker):
    kind = JobKind.PIPELINE

    def __init__(
        self,
        *,
        extractor: ExtractWorker | None = None,
        torrent: TorrentWorker | None = None,
        sync: SyncWorker | None = None,
        remux: RemuxWorker | None = None,
    ) -> None:
        self._extractor = extractor or ExtractWorker()
        self._torrent = torrent or TorrentWorker()
        self._sync = sync or SyncWorker()
        self._remux = remux or RemuxWorker()

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        from bankai.notify import notify_failure, notify_success

        try:
            result = await self._run_inner(ctx)
        except Exception as exc:
            await notify_failure(
                query=ctx.job.payload.get("query", ""),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        if result and result.get("final_path"):
            try:
                size = Path(result["final_path"]).stat().st_size
            except OSError:
                size = None
            await notify_success(
                query=ctx.job.payload.get("query", ""),
                final_path=result["final_path"],
                size_bytes=size,
            )
        return result

    async def _run_inner(self, ctx: WorkerContext) -> dict[str, Any] | None:
        payload = ctx.job.payload
        for required in ("query", "stream_url"):
            if not payload.get(required):
                raise PermanentWorkerError(f"pipeline payload missing {required!r}")

        # ---- 1. Extract dub audio --------------------------------------
        log.info("[pipeline] stage 1/4 \u2014 extract")
        stream_url = payload["stream_url"]
        stream_hint = payload.get("stream_hint", "ytdlp")
        stream_site = payload.get("stream_site", "unknown")
        # If the source URL is a stream-site wrapper (e.g. filmpalast),
        # ask the backend to resolve the underlying hoster URL first.
        if stream_site and stream_site != "unknown":
            try:
                from bankai.scraper import get_backend

                backend = get_backend(stream_site)()
                try:
                    handle = await backend.resolve_stream(stream_url)
                    if handle.url and handle.url != stream_url:
                        log.info(
                            "[pipeline] %s resolved \u2192 %s (hint=%s)",
                            stream_site,
                            handle.url,
                            handle.hint,
                        )
                        stream_url = handle.url
                        stream_hint = handle.hint
                finally:
                    await backend.aclose()
            except KeyError:
                pass  # unknown backend id; fall through with original URL
            except Exception as exc:
                log.warning("[pipeline] resolve_stream failed: %s", exc)
        extract_payload = {
            "url": stream_url,
            "hint": stream_hint,
            "site": stream_site,
        }
        extract_result = await self._run_stage(
            ctx, self._extractor, JobKind.EXTRACT, extract_payload
        )
        audio_path = extract_result["path"]

        # ---- 2. Torrent HQ video ---------------------------------------
        log.info("[pipeline] stage 2/4 â€” torrent")
        torrent_payload = {
            "query": payload["query"],
            "kind": payload.get("kind", "movie"),
            "category": payload.get("category"),
        }
        for k in ("season", "episode", "series_title"):
            if k in payload:
                torrent_payload[k] = payload[k]
        torrent_result = await self._run_stage(ctx, self._torrent, JobKind.TORRENT, torrent_payload)
        video_path = torrent_result["path"]

        # ---- 3. Sync audio to video ------------------------------------
        log.info("[pipeline] stage 3/4 â€” sync")
        sync_payload = {"audio": audio_path, "reference": video_path}
        if "offset_seconds" in payload:
            sync_payload["offset_seconds"] = payload["offset_seconds"]
        sync_result = await self._run_stage(ctx, self._sync, JobKind.SYNC, sync_payload)
        synced_audio = sync_result["path"]

        # ---- 4. Remux ---------------------------------------------------
        log.info("[pipeline] stage 4/4 â€” remux")
        out_path = payload.get("out") or _default_output_path(
            payload["query"],
            kind=payload.get("kind", "movie"),
            season=payload.get("season"),
            episode=payload.get("episode"),
            episode_title=payload.get("episode_title"),
        )
        remux_payload: dict[str, Any] = {
            "video": video_path,
            "audio": synced_audio,
            "out": str(out_path),
        }
        for k in ("language", "track_name", "default_track"):
            if k in payload:
                remux_payload[k] = payload[k]
        remux_result = await self._run_stage(ctx, self._remux, JobKind.REMUX, remux_payload)

        # ---- 5. Cleanup intermediates ----------------------------------
        if get_settings().paths.cleanup_after_success:
            await self._cleanup(
                ctx=ctx,
                audio_path=audio_path,
                synced_audio=synced_audio,
                video_path=video_path,
                torrent_hash=torrent_result.get("torrent_hash"),
                final_path=remux_result["path"],
            )

        return {
            "extract": extract_result,
            "torrent": torrent_result,
            "sync": sync_result,
            "remux": remux_result,
            "final_path": remux_result["path"],
        }

    async def _cleanup(
        self,
        *,
        ctx: WorkerContext,
        audio_path: str,
        synced_audio: str,
        video_path: str,
        torrent_hash: str | None,
        final_path: str,
    ) -> None:
        """Best-effort removal of intermediate files + source torrent.

        Anything that fails is logged but doesn't raise â€” the user
        already has their final MKV.
        """
        import shutil

        from bankai.torrent.qbittorrent import QBittorrentClient

        # 1. Remove the torrent + its downloaded files from qBittorrent.
        if torrent_hash:
            try:
                qbit = QBittorrentClient()
                await qbit.remove(torrent_hash, delete_files=True)
                log.info("[cleanup] removed torrent %s + files", torrent_hash[:8])
            except Exception as exc:
                log.warning("[cleanup] failed to remove torrent %s: %s", torrent_hash[:8], exc)

        # 2. Remove intermediate audio artifacts.
        for label, p in (("extracted-audio", audio_path), ("synced-audio", synced_audio)):
            try:
                pp = Path(p)
                if pp.exists() and pp.resolve() != Path(final_path).resolve():
                    pp.unlink()
                    log.info("[cleanup] removed %s %s", label, pp)
            except Exception as exc:
                log.warning("[cleanup] failed to remove %s %s: %s", label, p, exc)

        # 3. Remove the per-job work directory (job-N) if it's now empty
        # of meaningful artifacts.
        try:
            job_dir = Path(synced_audio).parent
            if job_dir.exists() and job_dir.is_dir() and job_dir != ctx.work_dir:
                # Only remove if it sits under the configured work_dir.
                try:
                    job_dir.relative_to(ctx.work_dir)
                except ValueError:
                    return
                shutil.rmtree(job_dir, ignore_errors=True)
                log.info("[cleanup] removed work dir %s", job_dir)
        except Exception as exc:
            log.warning("[cleanup] failed to remove work dir: %s", exc)

    async def _run_stage(
        self,
        ctx: WorkerContext,
        worker: Worker,
        kind: JobKind,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a stage worker inline against the parent job's context.

        Artifacts produced by the stage are attached to the parent
        pipeline job, so the user sees a single job in ``bankai jobs
        list`` with multiple artifacts.
        """
        # Synthesize a child Job-like context so the worker can read
        # ``ctx.job.payload``. We reuse the parent's id/media_id so
        # artifacts roll up to the pipeline job.
        child_job = Job(
            id=ctx.job.id,
            media_id=ctx.job.media_id,
            kind=kind,
            status=JobStatus.RUNNING,
            payload=payload,
            priority=ctx.job.priority,
        )
        child_ctx = WorkerContext(
            job=child_job,
            repo=ctx.repo,
            work_dir=ctx.work_dir,
            cancel_token=ctx.cancel_token,
        )
        result = await worker.run(child_ctx)
        return result or {}


def _default_output_path(
    query: str,
    *,
    kind: str = "movie",
    season: int | None = None,
    episode: int | None = None,
    episode_title: str | None = None,
) -> Path:
    """Build the default output path in Plex/Jellyfin layout.

    Movies::

        <library>/Movies/Title (Year)/Title (Year).mkv

    Episodes::

        <library>/Series/Show/Season 01/Show - S01E03 - Title.mkv
    """
    import re

    library = Path(get_settings().output.directory)
    cleaned = "".join(c if c.isalnum() or c in " ._-()[]" else "_" for c in query).strip() or "out"
    if kind == "episode" and season is not None and episode is not None:
        # Strip trailing year/SxxExx markers from query to get show name.
        show = re.sub(r"\s*[Ss]\d{1,2}[EeXx]\d{1,3}.*$", "", cleaned).strip(" -_") or cleaned
        ep = f"S{season:02d}E{episode:02d}"
        suffix = f" - {episode_title.strip()}" if episode_title else ""
        return library / "Series" / show / f"Season {season:02d}" / f"{show} - {ep}{suffix}.mkv"
    # Movie
    m = re.search(r"\s*\(?(\b(?:19|20)\d{2}\b)\)?\s*$", cleaned)
    if m:
        title = cleaned[: m.start()].rstrip(" ._-")
        year = m.group(1)
        folder = f"{title} ({year})" if title else f"({year})"
    else:
        folder = cleaned
    return library / "Movies" / folder / f"{folder}.mkv"
