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

import asyncio
import re
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
from bankai.scraper.base import StreamHandle
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
                except Exception:
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
        mirror_urls: list[str] = []
        if stream_site and stream_site != "unknown":
            try:
                from bankai.scraper import get_backend

                backend = get_backend(stream_site)()
                try:
                    # Prefer the full mirror list when the backend exposes it
                    # so a dead top mirror (e.g. an expired voe link) can fall
                    # through to the next one instead of failing the job.
                    resolve_all = getattr(backend, "resolve_all_streams", None)
                    handles = []
                    if callable(resolve_all):
                        handles = await resolve_all(stream_url)
                    if handles:
                        primary = handles[0]
                        if primary.url and primary.url != stream_url:
                            log.info(
                                "[pipeline] %s resolved \u2192 %s (+%d more mirror(s))",
                                stream_site,
                                primary.url,
                                len(handles) - 1,
                            )
                            stream_url = primary.url
                            stream_hint = primary.hint
                        mirror_urls = [h.url for h in handles if h.url]
                    else:
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

        # Burning Series exposes German episode metadata reliably, but its
        # player handoff is protected by an interactive invisible reCAPTCHA.
        # Do not automate or bypass that challenge. When Filmpalast carries
        # the exact same episode, prefer its directly exposed hoster URL
        # (normally VOE) while retaining the Burning Series wrappers as later
        # attempts for environments where the challenge completes normally.
        if stream_site == "burningseries":
            fallbacks = await _resolve_episode_fallbacks(payload, site_id="filmpalast")
            if fallbacks:
                previous_urls = [stream_url, *mirror_urls]
                stream_url = fallbacks[0].url
                stream_hint = fallbacks[0].hint
                later_urls = [handle.url for handle in fallbacks[1:]] + previous_urls
                mirror_urls = list(dict.fromkeys(url for url in later_urls if url != stream_url))
                log.info(
                    "[pipeline] Burning Series player requires an interactive challenge; "
                    "using %d exact Filmpalast episode mirror(s), first=%s",
                    len(fallbacks),
                    stream_url,
                )
        extract_attempts = _extract_attempt_payloads(
            stream_url=stream_url,
            stream_hint=stream_hint,
            stream_site=stream_site,
            wrapper_url=original_stream_url if original_stream_url != stream_url else None,
            mirror_urls=mirror_urls,
            want_video=get_settings().sync.visual,
            max_height=get_settings().sync.visual_max_height or None,
        )
        extract_attempt_index = 0
        extract_attempt_index, extract_result = await self._run_extract_attempts(ctx, extract_attempts, extract_attempt_index)
        audio_path, visual_source = await self._prepare_german_source(ctx, extract_result)

        # ---- 2. Torrent HQ video ---------------------------------------
        _log_stage(2, "torrent", "Download HQ video")
        torrent_payload = {
            "query": payload["query"],
            "kind": payload.get("kind", "movie"),
            "category": payload.get("category"),
        }
        source_duration = await _probe_duration(Path(audio_path))
        if source_duration is not None:
            torrent_payload["target_runtime_seconds"] = source_duration
            log.info("[torrent] German source runtime %.1f min", source_duration / 60.0)
        for k in ("season", "episode", "series_title"):
            if k in payload:
                torrent_payload[k] = payload[k]
        torrent_result = await self._run_stage(ctx, self._torrent, JobKind.TORRENT, torrent_payload)
        video_path = torrent_result["path"]

        # ---- 3. Sync audio to video ------------------------------------
        _log_stage(3, "sync", "Sync audio")
        while True:
            sync_payload, visual_meta = await self._build_sync_payload(
                audio_path=audio_path,
                video_path=video_path,
                payload=payload,
                visual_source=visual_source,
            )
            try:
                sync_result = await self._run_stage(ctx, self._sync, JobKind.SYNC, sync_payload)
                break
            except PlaceholderAudioError:
                extract_attempt_index += 1
                if extract_attempt_index >= len(extract_attempts):
                    raise
                log.warning("[pipeline] extracted audio looked like a placeholder; retrying extract with the next source attempt")
                extract_attempt_index, extract_result = await self._run_extract_attempts(ctx, extract_attempts, extract_attempt_index)
                audio_path, visual_source = await self._prepare_german_source(ctx, extract_result)
        synced_audio = sync_result["path"]
        _account_for_applied_tempo(visual_meta, float(sync_result.get("tempo", 1.0) or 1.0))

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
            "audio_delay_ms": visual_meta.get("delay_ms", 0),
        }
        for k in ("language", "track_name", "default_track"):
            if k in payload:
                remux_payload[k] = payload[k]
        remux_result = await self._run_stage(ctx, self._remux, JobKind.REMUX, remux_payload)

        # Record the alignment outcome so the web UI can flag titles whose
        # automatic sync was low-confidence for a quick manual delay nudge.
        self._record_output_review(
            remux_result.get("path"),
            visual_meta,
            german_source_url=original_stream_url,
            torrent_result=torrent_result,
        )

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
        visual_source: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return ``(sync_payload, visual_meta)``.

        ``visual_meta`` carries the constant ``delay_ms`` to apply at remux
        (via ``mkvmerge --sync``) plus confidence/drift diagnostics used to
        flag the title for manual review.
        """
        sync_payload: dict[str, Any] = {"audio": audio_path, "reference": video_path}
        visual_meta: dict[str, Any] = {"delay_ms": 0}
        if "offset_seconds" in payload:
            # Explicit manual override wins; keep the ffmpeg -itsoffset path.
            sync_payload["offset_seconds"] = payload["offset_seconds"]
            return sync_payload, visual_meta

        settings = get_settings().sync
        if not settings.visual:
            return sync_payload, visual_meta

        # Prefer the dedicated German match video; fall back to the audio
        # file itself if it happens to be a video container.
        source_candidate = Path(visual_source) if visual_source else Path(audio_path)
        if not is_video_file(source_candidate):
            log.info("[visual-sync] no German video available for matching; skipping")
            return sync_payload, visual_meta
        # Capture the German source fps + reference fps so the review UI can
        # show a definitive frame-rate drift (e.g. 25fps PAL vs 23.976).
        source_fps = await _probe_fps(source_candidate)
        reference_fps = await _probe_fps(Path(video_path))
        if source_fps:
            visual_meta["source_fps"] = source_fps
        if reference_fps:
            visual_meta["reference_fps"] = reference_fps
        try:
            timeline = await estimate_visual_timeline(
                reference=Path(video_path),
                source=source_candidate,
                sample_count=settings.visual_sample_count,
            )
        except VisualSyncError as exc:
            log.info("[visual-sync] skipped: %s", exc)
            visual_meta["needs_review"] = True
            visual_meta["reason"] = str(exc)
            return sync_payload, visual_meta

        visual_meta.update(
            {
                "confidence": timeline.confidence,
                "offset_seconds": timeline.offset_seconds,
                "spread_seconds": timeline.spread_seconds,
                "drift_ratio": timeline.drift_ratio,
                "matches": len(timeline.matches),
            }
        )
        low_confidence = timeline.confidence < settings.visual_min_confidence
        visual_meta["needs_review"] = low_confidence

        # Speed drift: only auto-correct for a known PAL/NDF ratio at high
        # confidence when explicitly enabled; otherwise just report it.
        drift = timeline.drift_ratio
        if abs(drift - 1.0) > 0.005:
            if settings.visual_apply_drift and not low_confidence and _is_known_fps_ratio(drift):
                # source runs at `drift` x reference; play the audio at that
                # factor to match the reference timeline.
                sync_payload["tempo"] = drift
                log.info("[visual-sync] applying speed drift correction tempo=%.5f", drift)
            else:
                log.info("[visual-sync] detected speed drift ratio=%.5f (not auto-corrected)", drift)
                visual_meta["needs_review"] = True

        # source_time = reference_time + offset. Positive offset means the
        # German source has extra lead-in, so the dub must be pulled earlier
        # (negative mkvmerge --sync delay).
        offset = timeline.offset_seconds
        if abs(offset) < 0.10:
            log.info("[visual-sync] source/HQ offset %.3fs is negligible", offset)
        else:
            visual_meta["delay_ms"] = -round(offset * 1000)
            log.info(
                "[visual-sync] offset %.3fs (delay %+dms) from %d matches, confidence %.2f%s",
                offset,
                visual_meta["delay_ms"],
                len(timeline.matches),
                timeline.confidence,
                " [LOW CONFIDENCE -> review]" if low_confidence else "",
            )
        return sync_payload, visual_meta

    async def _prepare_german_source(self, ctx: WorkerContext, extract_result: dict[str, Any]) -> tuple[str, str | None]:
        """Return ``(dub_audio_path, visual_source_path)``.

        When visual sync is enabled and the extracted German media carries a
        video track, demux a clean dub-audio file (so the sync/remux stages
        get audio only) and keep the video for frame matching. Otherwise the
        extracted file is used directly as the dub audio.
        """
        raw_path = extract_result["path"]
        media = Path(raw_path)
        if not get_settings().sync.visual:
            return raw_path, None
        if not (extract_result.get("has_video") or await _probe_has_video(media)):
            return raw_path, None
        out_dir = ctx.work_dir / f"job-{ctx.job.id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        dub = out_dir / "dub.mka"
        try:
            await _demux_audio(media, dub)
        except Exception as exc:  # fall back to using the video directly
            log.warning("[visual-sync] audio demux failed (%s); using media as-is", exc)
            return str(media), str(media)
        return str(dub), str(media)

    def _record_output_review(
        self,
        final_path: str | None,
        visual_meta: dict[str, Any],
        *,
        german_source_url: str,
        torrent_result: dict[str, Any],
    ) -> None:
        if not final_path:
            return
        try:
            from bankai.web import review as review_mod

            review_mod.reset_for_new_output(final_path)
            review_mod.set_sync_review(
                final_path,
                needs_review=bool(visual_meta.get("needs_review")),
                confidence=visual_meta.get("confidence"),
                applied_delay_ms=int(visual_meta.get("delay_ms", 0) or 0),
                source_fps=visual_meta.get("source_fps"),
                reference_fps=visual_meta.get("reference_fps"),
                drift_ratio=visual_meta.get("drift_ratio"),
            )
            review_mod.set_sources(
                final_path,
                german_source_url=german_source_url,
                torrent_source_url=torrent_result.get("source_url"),
                torrent_source_title=torrent_result.get("source_title"),
            )
        except Exception as exc:  # review flagging is best-effort
            log.debug("[pipeline] could not record output review metadata: %s", exc)


def _account_for_applied_tempo(visual_meta: dict[str, Any], tempo: float) -> None:
    """Convert raw-source visual measurements to the final audio timeline.

    Visual matching runs before the sync worker. If that worker time-stretches
    the German audio, both the raw offset and raw slope must be divided by the
    applied tempo before they are persisted for review. Otherwise the UI would
    suggest applying an already-applied PAL/film correction a second time.
    """
    if tempo <= 0 or abs(tempo - 1.0) <= 1e-6:
        return
    delay_ms = int(visual_meta.get("delay_ms", 0) or 0)
    if delay_ms:
        visual_meta["delay_ms"] = round(delay_ms / tempo)
    drift = visual_meta.get("drift_ratio")
    if drift is not None:
        visual_meta["drift_ratio"] = float(drift) / tempo
    visual_meta["applied_tempo"] = tempo
    log.info(
        "[visual-sync] translated measurements after tempo %.6f: delay=%+dms residual_drift=%.6f",
        tempo,
        int(visual_meta.get("delay_ms", 0) or 0),
        float(visual_meta.get("drift_ratio", 1.0) or 1.0),
    )


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


async def _resolve_episode_fallbacks(
    payload: dict[str, Any],
    *,
    site_id: str,
) -> list[StreamHandle]:
    """Resolve all direct mirrors for the exact episode through another backend."""
    if payload.get("kind") != "episode":
        return []
    try:
        season = int(payload["season"])
        episode = int(payload["episode"])
    except (KeyError, TypeError, ValueError):
        return []
    show = str(payload.get("series_title") or payload.get("query") or "").strip()
    show = re.sub(r"\s+[Ss]\d{1,2}[Ee]\d{1,3}\s*$", "", show).strip()
    if not show:
        return []

    try:
        from bankai.scraper import get_backend

        backend = get_backend(site_id)()
    except Exception as exc:
        log.debug("[pipeline] could not open episode fallback %s: %s", site_id, exc)
        return []
    try:
        list_season = getattr(backend, "list_season", None)
        if not callable(list_season):
            return []
        episodes = await list_season(show, season)
        match = next((ref for ref in episodes if ref.episode == episode), None)
        if match is None:
            return []
        resolve_all = getattr(backend, "resolve_all_streams", None)
        handles = await resolve_all(match.url) if callable(resolve_all) else []
        if not handles:
            handles = [await backend.resolve_stream(match.url)]
        # A backend returning its own wrapper means no direct mirror was
        # exposed. It is not a useful fallback for the guarded BS wrapper.
        return [handle for handle in handles if handle.url and handle.url != match.url]
    except Exception as exc:
        log.warning(
            "[pipeline] episode fallback %s failed for %s S%02dE%02d: %s",
            site_id,
            show,
            season,
            episode,
            exc,
        )
        return []
    finally:
        await backend.aclose()


def _extract_attempt_payloads(
    *,
    stream_url: str,
    stream_hint: str,
    stream_site: str,
    wrapper_url: str | None = None,
    mirror_urls: list[str] | None = None,
    want_video: bool = False,
    max_height: int | None = None,
) -> list[dict[str, Any]]:
    specs: list[tuple[str, str]] = []

    def add(url: str | None, hint: str | None) -> None:
        if not url or not hint:
            return
        spec = (url, hint)
        if spec not in specs:
            specs.append(spec)

    add(stream_url, stream_hint)
    # Try every other hoster mirror (vinovo, streamtape, …) before falling
    # back to the wrapper page. Each is attempted with yt-dlp first, then the
    # Playwright/headful-browser capture, since most German hosters are not
    # natively supported by yt-dlp.
    for mirror in mirror_urls or []:
        add(mirror, "ytdlp")
    for mirror in mirror_urls or []:
        add(mirror, "playwright")
    # Filmpalast wrappers only link back to the same direct mirrors. Retrying
    # the wrapper after those mirrors fail repeats the same browser work and
    # used to keep a bad source busy for several additional minutes.
    if stream_site != "filmpalast":
        add(wrapper_url, "playwright")
    add(stream_url, "playwright")
    if stream_site != "filmpalast":
        add(wrapper_url, "ytdlp")
    add(stream_url, "ytdlp")
    return [
        {
            "url": url,
            "hint": hint,
            "site": stream_site,
            "attempt": i + 1,
            "want_video": want_video,
            "max_height": max_height,
        }
        for i, (url, hint) in enumerate(specs)
    ]


def _log_stage(step: int, key: str, label: str) -> None:
    log.info('BANKAI_STAGE step=%d total=4 key=%s label="%s"', step, key, label)
    log.info("[pipeline] stage %d/4 - %s", step, label)


# Same PAL/NDF ratios the sync worker recognises, used to gate automatic
# speed-drift correction.
_KNOWN_FPS_RATIOS = (
    23.976 / 25.0,
    25.0 / 23.976,
    24.0 / 25.0,
    25.0 / 24.0,
)


def _is_known_fps_ratio(ratio: float, *, tol: float = 0.004) -> bool:
    return any(abs(ratio - target) <= tol for target in _KNOWN_FPS_RATIOS)


async def _probe_has_video(path: Path) -> bool:
    """Return True if ``path`` contains at least one video stream."""
    if not path.exists():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return False
    stdout, _ = await proc.communicate()
    return proc.returncode == 0 and b"video" in stdout


async def _probe_fps(path: Path) -> float | None:
    """Return the video frame rate (fps) of ``path``, or None if unavailable."""
    if not path.exists():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return None
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    raw = stdout.decode(errors="ignore").strip()
    if not raw:
        return None
    try:
        if "/" in raw:
            num, den = raw.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(raw)
    except (ValueError, ZeroDivisionError):
        return None


async def _probe_duration(path: Path) -> float | None:
    """Return media duration in seconds for torrent runtime matching."""
    if not path.exists():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return None
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    try:
        duration = float(stdout.decode(errors="ignore").strip())
        return duration if duration > 0 else None
    except ValueError:
        return None


async def _demux_audio(media: Path, out: Path) -> None:
    """Copy the first audio track out of ``media`` into ``out`` (no re-encode)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(media),
        "-map",
        "0:a:0",
        "-c",
        "copy",
        str(out),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg demux failed: {stderr.decode(errors='ignore')[:300]}")
