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
from bankai.processor.sync import PlaceholderAudioError, SyncWorker
from bankai.processor.visual_sync import VisualSyncError, estimate_visual_timeline, is_video_file
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

        # ---- 0. Skip if the target MKV already exists ------------------
        settings = get_settings()
        if settings.output.skip_existing and not payload.get("out"):
            planned_out = _default_output_path(
                payload["query"],
                kind=payload.get("kind", "movie"),
                season=payload.get("season"),
                episode=payload.get("episode"),
                episode_title=payload.get("episode_title"),
                series_title=payload.get("series_title"),
            )
            if planned_out.exists() and planned_out.stat().st_size > 0:
                log.info("[pipeline] skipping \u2014 already present: %s", planned_out)
                try:
                    from bankai.notify import notify_skipped

                    await notify_skipped(
                        query=payload.get("query", ""),
                        final_path=str(planned_out),
                    )
                except Exception:  # noqa: BLE001 -- notifications are best-effort.
                    log.debug("[pipeline] skip-notify failed", exc_info=True)
                return {
                    "status": "skipped",
                    "reason": "already_exists",
                    "out": str(planned_out),
                }

        # ---- 1. Extract dub audio --------------------------------------
        _log_stage(1, "extract", "Extract stream audio")
        original_stream_url = payload["stream_url"]
        stream_url = original_stream_url
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
        extract_attempts = _extract_attempt_payloads(
            stream_url=stream_url,
            stream_hint=stream_hint,
            stream_site=stream_site,
            wrapper_url=original_stream_url if original_stream_url != stream_url else None,
        )
        extract_attempt_index = 0
        extract_attempt_index, extract_result = await self._run_extract_attempts(
            ctx, extract_attempts, extract_attempt_index
        )
        audio_path = extract_result["path"]

        # ---- 2. Torrent HQ video ---------------------------------------
        _log_stage(2, "torrent", "Download HQ video")
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
        _log_stage(3, "sync", "Sync audio")
        while True:
            sync_payload = await self._build_sync_payload(
                audio_path=audio_path,
                video_path=video_path,
                payload=payload,
            )
            try:
                sync_result = await self._run_stage(ctx, self._sync, JobKind.SYNC, sync_payload)
                break
            except PlaceholderAudioError:
                extract_attempt_index += 1
                if extract_attempt_index >= len(extract_attempts):
                    raise
                log.warning(
                    "[pipeline] extracted audio looked like a placeholder; retrying extract "
                    "with the next source attempt"
                )
                extract_attempt_index, extract_result = await self._run_extract_attempts(
                    ctx, extract_attempts, extract_attempt_index
                )
                audio_path = extract_result["path"]
        synced_audio = sync_result["path"]

        # ---- 4. Remux ---------------------------------------------------
        _log_stage(4, "remux", "Write final MKV")
        out_path = payload.get("out") or _default_output_path(
            payload["query"],
            kind=payload.get("kind", "movie"),
            season=payload.get("season"),
            episode=payload.get("episode"),
            episode_title=payload.get("episode_title"),
            series_title=payload.get("series_title"),
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

    async def _run_extract_attempts(
        self,
        ctx: WorkerContext,
        attempts: list[dict[str, Any]],
        start_index: int,
    ) -> tuple[int, dict[str, Any]]:
        last_error: Exception | None = None
        for index in range(start_index, len(attempts)):
            attempt = attempts[index]
            log.info(
                "[pipeline] extract attempt %d/%d url=%s hint=%s",
                index + 1,
                len(attempts),
                attempt.get("url"),
                attempt.get("hint"),
            )
            try:
                result = await self._run_stage(ctx, self._extractor, JobKind.EXTRACT, attempt)
                return index, result
            except Exception as exc:
                last_error = exc
                if index + 1 >= len(attempts):
                    raise
                log.warning(
                    "[pipeline] extract attempt %d/%d failed: %s; trying next source",
                    index + 1,
                    len(attempts),
                    exc,
                )
        if last_error is not None:
            raise last_error
        raise PermanentWorkerError("no extract attempts available")

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

    async def _build_sync_payload(
        self,
        *,
        audio_path: str,
        video_path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        sync_payload: dict[str, Any] = {"audio": audio_path, "reference": video_path}
        if "offset_seconds" in payload:
            sync_payload["offset_seconds"] = payload["offset_seconds"]
            return sync_payload

        source_path = Path(audio_path)
        if not is_video_file(source_path):
            return sync_payload
        try:
            timeline = await estimate_visual_timeline(
                reference=Path(video_path),
                source=source_path,
            )
        except VisualSyncError as exc:
            log.info("[visual-sync] skipped: %s", exc)
            return sync_payload

        if abs(timeline.slope - 1.0) > 0.005:
            log.info(
                "[visual-sync] matched %d frames but timeline slope %.5f is not a simple "
                "offset; leaving audio sync automatic",
                len(timeline.matches),
                timeline.slope,
            )
            return sync_payload
        if abs(timeline.offset_seconds) < 0.25:
            log.info("[visual-sync] source/HQ offset %.3fs is negligible", timeline.offset_seconds)
            return sync_payload

        # source_time = reference_time + offset. If the source has extra
        # lead-in, offset is positive and the audio must be advanced.
        sync_payload["offset_seconds"] = -timeline.offset_seconds
        sync_payload["visual_offset_seconds"] = timeline.offset_seconds
        sync_payload["visual_matches"] = [
            {
                "reference_time": m.reference_time,
                "source_time": m.source_time,
                "distance": m.distance,
            }
            for m in timeline.matches
        ]
        log.info(
            "[visual-sync] applying offset %.3fs from %d frame matches",
            -timeline.offset_seconds,
            len(timeline.matches),
        )
        return sync_payload


def _default_output_path(
    query: str,
    *,
    kind: str = "movie",
    season: int | None = None,
    episode: int | None = None,
    episode_title: str | None = None,
    series_title: str | None = None,
) -> Path:
    """Build the default output path in Plex/Jellyfin layout.

    Movie layout::

        <library>/Movies/<movie_folder_template>/<filename_template>

    Episode layout::

        <library>/Shows/<show>/<season_folder_template>/<series_filename_template>

    Templates live in :class:`bankai.config.OutputSettings` so users can
    customise the layout without touching the code.
    """
    from bankai.processor.naming import render_episode_path, render_movie_path

    settings = get_settings()
    library = Path(settings.output.directory)
    out_cfg = settings.output
    audio_lang = settings.audio.language_tag or "ger"

    if kind == "episode" and season is not None and episode is not None:
        return render_episode_path(
            library=library,
            query=query,
            series_title=series_title,
            season=season,
            episode=episode,
            episode_title=episode_title,
            audio_lang=audio_lang,
            season_folder_template=out_cfg.season_folder_template,
            file_template=out_cfg.series_filename_template,
        )
    return render_movie_path(
        library=library,
        query=query,
        audio_lang=audio_lang,
        folder_template=out_cfg.movie_folder_template,
        file_template=out_cfg.filename_template,
    )


def _extract_attempt_payloads(
    *,
    stream_url: str,
    stream_hint: str,
    stream_site: str,
    wrapper_url: str | None = None,
) -> list[dict[str, Any]]:
    specs: list[tuple[str, str]] = []

    def add(url: str | None, hint: str | None) -> None:
        if not url or not hint:
            return
        spec = (url, hint)
        if spec not in specs:
            specs.append(spec)

    add(stream_url, stream_hint)
    # If a scraper resolved the original wrapper to a hoster page, direct
    # Playwright on that hoster may stall. The wrapper page often contains
    # the click flow that exposes the real media URL, so try it next.
    add(wrapper_url, "playwright")
    add(stream_url, "playwright")
    add(wrapper_url, "ytdlp")
    add(stream_url, "ytdlp")
    return [
        {"url": url, "hint": hint, "site": stream_site, "attempt": i + 1}
        for i, (url, hint) in enumerate(specs)
    ]


def _log_stage(step: int, key: str, label: str) -> None:
    log.info('BANKAI_STAGE step=%d total=4 key=%s label="%s"', step, key, label)
    log.info("[pipeline] stage %d/4 - %s", step, label)
