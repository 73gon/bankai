"""FastAPI application: JSON API under /api, WebSocket at /ws, static UI.

Reuses the same services and background-job store as the CLI so the web
UI and terminal stay in sync. Built as a single process that also serves
the prebuilt React frontend from :data:`STATIC_DIR`.
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from bankai import __version__
from bankai.config import get_settings, reset_settings_cache
from bankai.logging import get_logger
from bankai.queue.models import MediaKind
from bankai.web import discover as discover_mod
from bankai.web import jobs as webjobs
from bankai.web import media as media_mod
from bankai.web import posters as posters_mod
from bankai.web import review as review_mod

log = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# In-memory cache of downsampled audio waveforms keyed by (path, mtime, stream).
_WAVEFORM_CACHE: dict[tuple, dict] = {}


def _norm_title(s: str) -> str:
    """Loosely normalise a title/filename so a queue job and its finished
    library file collapse to the same key (one row per movie)."""
    s = s.lower()
    s = re.sub(r"\.[a-z0-9]{2,4}$", "", s)  # extension
    s = re.sub(r"\(\d{4}\)", "", s)  # (2016)
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)  # bare year
    s = re.sub(r"[^a-z0-9]+", "", s)  # punctuation / whitespace
    return s


def _extract_year(s: str) -> int | None:
    """Pull a 4-digit year out of a title/filename, if present."""
    m = re.search(r"\((\d{4})\)", s) or re.search(r"\b(19|20)\d{2}\b", s)
    if not m:
        return None
    try:
        return int(m.group(0).strip("()"))
    except ValueError:
        return None


def _clean_title(s: str) -> str:
    """Human title without the file extension, ``(year)`` or release cruft."""
    s = re.sub(r"\.[a-z0-9]{2,4}$", "", s, flags=re.I)  # extension
    s = re.sub(r"\s*\(\d{4}\)\s*", " ", s)  # (2016)
    s = re.sub(r"\s*\((?:unknown)\)\s*", " ", s, flags=re.I)
    return s.strip()


def _job_priority(job: dict) -> tuple[int, float]:
    """Rank jobs of the same title so the most relevant one wins.

    Active (running/queued) beats finished; ties break on recency.
    """
    status = job.get("status", "")
    active = 2 if (job.get("pending") or status == "running") else (1 if status in ("done", "success") else 0)
    return (active, float(job.get("started_at") or 0))


class MovieQueueRequest(BaseModel):
    title: str
    german: str | None = None
    url: str | None = None
    site: str = "filmpalast"


class ShowQueueRequest(BaseModel):
    show: str
    season: int
    episodes: list[int] | None = None
    site: str | None = None


class DelayRequest(BaseModel):
    path: str
    delay_ms: int


class PathRequest(BaseModel):
    path: str


class PathsRequest(BaseModel):
    paths: list[str]


class ServerDirRequest(BaseModel):
    kind: str  # "movie" | "show"
    path: str = ""


class SettingRequest(BaseModel):
    key: str
    value: Any

# Settings keys the web UI is allowed to edit (safe subset).
SAFE_SETTING_KEYS: set[str] = {
    "metadata.tvdb_api_key",
    "metadata.tvdb_pin",
    "metadata.tvdb_enabled",
    "notifications.webhook_url",
    "notifications.on_success",
    "notifications.on_failure",
    "output.directory",
    "output.skip_existing",
    "transfer.root",
    "transfer.movies_dir",
    "transfer.shows_dir",
    "scraper.interactive_pick",
    "web.port",
    "web.host",
    "web.max_concurrent_jobs",
    "web.transcode_fallback",
}


def _is_secret_key(key: str) -> bool:
    return any(part in key.casefold() for part in ("password", "api_key", "pin", "webhook"))


def create_app() -> Any:
    from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import (
        FileResponse,
        HTMLResponse,
        JSONResponse,
        Response,
        StreamingResponse,
    )
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="bankai", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------
    def _library_root() -> Path:
        return Path(get_settings().output.directory).resolve()

    def _safe_path(raw: str) -> Path:
        p = Path(raw).resolve()
        root = _library_root()
        try:
            p.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="path outside library") from exc
        if not p.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return p

    # ------------------------------------------------------------------
    # Health / meta
    # ------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        s = get_settings()
        return {
            "status": "ok",
            "version": __version__,
            "library": str(s.output.directory),
            "ffprobe": media_mod.ffprobe_bin() is not None,
            "ffmpeg": media_mod.ffmpeg_bin() is not None,
            "mkvmerge": media_mod.mkvmerge_bin() is not None,
            "tvdb_configured": discover_mod.is_configured(),
        }

    # ------------------------------------------------------------------
    # Discover
    # ------------------------------------------------------------------
    @app.get("/api/discover/trending")
    async def discover_trending(kind: str = Query("movie")) -> dict:
        k = "movie" if kind == "movie" else "show"
        new = await discover_mod.new_releases(k)
        browse = await discover_mod.trending(k)
        merged: list = []
        seen: set = set()
        for it in [*new, *browse]:
            key = (it.tvdb_id, it.name.casefold())
            if key in seen:
                continue
            seen.add(key)
            merged.append(it)
        return {
            "configured": discover_mod.is_configured(),
            "items": [discover_mod.to_dict(i) for i in merged],
        }

    @app.get("/api/discover/search")
    async def discover_search(q: str = Query(...), kind: str = Query("movie")) -> dict:
        k = "movie" if kind == "movie" else "show"
        items = await discover_mod.search(q, kind=k)
        return {
            "configured": discover_mod.is_configured(),
            "items": [discover_mod.to_dict(i) for i in items],
        }

    @app.get("/api/discover/poster")
    async def discover_poster(url: str = Query(...)) -> Response:
        import httpx

        if not url.startswith("https://") and not url.startswith("http://"):
            raise HTTPException(status_code=400, detail="invalid url")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url)
                r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="poster fetch failed") from exc
        return Response(
            content=r.content,
            media_type=r.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/discover/german")
    async def discover_german(id: int = Query(...), kind: str = Query("movie")) -> dict:
        k = "movie" if kind == "movie" else "show"
        name = await discover_mod.german_title(id, kind=k)
        return {"tvdb_id": id, "kind": k, "german": name}

    # ------------------------------------------------------------------
    # Search (stream sources)
    # ------------------------------------------------------------------
    @app.get("/api/search")
    async def search_sources(
        q: str = Query(...),
        kind: str = Query("movie"),
        site: str | None = Query(None),
        limit: int = Query(15, ge=1, le=50),
    ) -> dict:
        from bankai.backend import search_stream_sources

        media_kind = MediaKind.MOVIE if kind == "movie" else MediaKind.EPISODE
        results = await search_stream_sources(q, site=site, limit=limit, kind=media_kind)
        return {
            "results": [
                {
                    "site": r.site,
                    "title": r.title,
                    "year": r.year,
                    "kind": str(r.kind),
                    "url": r.url,
                }
                for r in results
            ]
        }

    @app.get("/api/series/episodes")
    async def series_episodes(
        show: str = Query(...),
        season: int = Query(...),
        site: str | None = Query(None),
    ) -> dict:
        from bankai.backend import list_series_episodes

        result = await list_series_episodes(show, season=season, site=site)
        if result is None:
            return {"found": False, "site": None, "episodes": []}
        return {
            "found": True,
            "site": result.site,
            "query": result.query,
            "episodes": [
                {"season": e.season, "episode": e.episode, "title": e.title, "url": e.url}
                for e in sorted(result.episodes, key=lambda e: e.episode)
            ],
        }

    # ------------------------------------------------------------------
    # Queue / jobs
    # ------------------------------------------------------------------
    @app.get("/api/queue")
    def queue_list() -> dict:
        return {"jobs": webjobs.snapshot()}

    @app.post("/api/queue/movie")
    def queue_movie(req: MovieQueueRequest) -> dict:
        from bankai.backend import BatchMovie, build_movie_args

        movie = BatchMovie(title=req.title, german_title=req.german, url=req.url)
        args = build_movie_args(movie, site=req.site)
        return webjobs.enqueue(kind="movie", title=req.title, args=args)

    @app.post("/api/queue/show")
    async def queue_show(req: ShowQueueRequest) -> dict:
        from bankai.backend import list_series_episodes

        result = await list_series_episodes(req.show, season=req.season, site=req.site)
        if result is None:
            raise HTTPException(status_code=404, detail="no episodes found")
        episodes = sorted(result.episodes, key=lambda e: e.episode)
        if req.episodes:
            wanted = set(req.episodes)
            episodes = [e for e in episodes if e.episode in wanted]
        if not episodes:
            raise HTTPException(status_code=400, detail="no matching episodes")
        queued: list[dict] = []
        for ep in episodes:
            q = f"{req.show} S{req.season:02d}E{ep.episode:02d}"
            args = [
                "run", q, "--url", ep.url, "--site", result.site,
                "--kind", "episode", "--season", str(req.season),
                "--episode", str(ep.episode), "--series-title", req.show, "--auto",
            ]
            if ep.title:
                args.extend(["--episode-title", ep.title])
            queued.append(webjobs.enqueue(kind="show", title=q, args=args))
        return {"queued": queued, "count": len(queued)}

    @app.post("/api/queue/{job_id}/cancel")
    def queue_cancel(job_id: str) -> dict:
        from bankai.cli import bgjobs

        if webjobs.cancel_pending(job_id):
            return {"cancelled": True, "pending": True}
        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        ok = job.cancel()
        return {"cancelled": ok, "pending": False}

    @app.post("/api/queue/{job_id}/retry")
    def queue_retry(job_id: str) -> dict:
        from bankai.cli import bgjobs

        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return webjobs.enqueue(kind=job.kind, title=job.title, args=job.args)

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str, lines: int = Query(200, ge=1, le=5000)) -> dict:
        from bankai.cli import bgjobs

        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"id": job.id, "status": job.status, "log": bgjobs.tail(job, lines=lines)}

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------
    @app.get("/api/library")
    def library_list() -> dict:
        entries = media_mod.scan_library()
        transfers = webjobs.transfer_states()
        out = []
        for e in entries:
            state = review_mod.get_state(e.path)
            # Reconcile the detached transfer job into the per-entry status so
            # the library shows transfer progress as a column (not a queue job).
            try:
                tkey = str(Path(e.path).resolve())
            except OSError:
                tkey = e.path
            tinfo = transfers.get(tkey)
            if tinfo and tinfo["status"] != state.transfer_status:
                state = review_mod.set_transfer(
                    e.path, tinfo["status"], percent=tinfo.get("percent")
                )
            out.append(
                {
                    "kind": e.kind,
                    "path": e.path,
                    "rel_path": e.rel_path,
                    "name": e.name,
                    "size": e.size,
                    "mtime": e.mtime,
                    "series": e.series,
                    "season": e.season,
                    "stage": state.stage,
                    "delay_ms": state.delay_ms,
                    "needs_sync_review": state.needs_sync_review,
                    "sync_confidence": state.sync_confidence,
                    "auto_delay_ms": state.auto_delay_ms,
                    "transfer_status": state.transfer_status,
                    "transfer_percent": (
                        tinfo.get("percent", state.transfer_percent)
                        if tinfo
                        else state.transfer_percent
                    ),
                }
            )
        return {"entries": out, "library": str(get_settings().output.directory)}

    @app.get("/api/titles")
    def titles_list() -> dict:
        """Unified one-row-per-title view merging the library and the queue.

        Each movie/episode appears exactly once: a title being downloaded
        shows its job status; once finished it becomes its library row. Extra
        state (sync, transfer, download progress) is carried as columns, never
        as extra rows.
        """
        entries = media_mod.scan_library()
        transfers = webjobs.transfer_states()
        jobs = webjobs.snapshot()  # already excludes transfer jobs
        # Map normalised title -> newest job, so a finished library row can
        # still surface its download log (expandable row) and be re-run.
        job_by_norm: dict[str, dict] = {}
        for j in jobs:
            nt = _norm_title(j.get("title", ""))
            cur = job_by_norm.get(nt)
            if cur is None or (j.get("started_at") or 0) >= (cur.get("started_at") or 0):
                job_by_norm[nt] = j
        rows: list[dict] = []
        lib_paths: set[str] = set()
        lib_norms: set[str] = set()
        for e in entries:
            state = review_mod.get_state(e.path)
            try:
                rp = str(Path(e.path).resolve())
            except OSError:
                rp = e.path
            tinfo = transfers.get(rp)
            if tinfo and tinfo["status"] != state.transfer_status:
                state = review_mod.set_transfer(e.path, tinfo["status"], percent=tinfo.get("percent"))
            lib_paths.add(rp)
            norm = _norm_title(e.name)
            lib_norms.add(norm)
            clean = _clean_title(e.name)
            poster_key = ("show:" if e.kind == "episode" else "movie:") + (
                _norm_title(e.series or clean) if e.kind == "episode" else norm
            )
            posters_mod.ensure(
                poster_key, e.series or clean, "series" if e.kind == "episode" else "movie"
            )
            related = job_by_norm.get(norm)
            rows.append(
                {
                    "row_kind": "library",
                    "id": e.path,
                    "title": clean,
                    "kind": e.kind,
                    "year": _extract_year(e.name) or posters_mod.cached_year(poster_key),
                    "poster": posters_mod.cached(poster_key),
                    "done_at": e.mtime,
                    "path": e.path,
                    "rel_path": e.rel_path,
                    "name": e.name,
                    "size": e.size,
                    "mtime": e.mtime,
                    "series": e.series,
                    "season": e.season,
                    "stage": state.stage,
                    "delay_ms": state.delay_ms,
                    "needs_sync_review": state.needs_sync_review,
                    "sync_confidence": state.sync_confidence,
                    "auto_delay_ms": state.auto_delay_ms,
                    "transfer_status": state.transfer_status,
                    "transfer_percent": (
                        tinfo.get("percent", state.transfer_percent)
                        if tinfo
                        else state.transfer_percent
                    ),
                    "job_id": related["id"] if related else None,
                    "job_status": None,
                    "step_label": None,
                    "overall_percent": None,
                    "total_steps": None,
                    "pending": False,
                }
            )
        # Collapse queue jobs that have no finished library file yet into one
        # row per title (prefer the most relevant attempt).
        best_by_norm: dict[str, dict] = {}
        for j in jobs:
            fp = j.get("final_path")
            if fp:
                try:
                    rfp = str(Path(fp).resolve())
                except OSError:
                    rfp = fp
                if rfp in lib_paths:
                    continue  # already represented by its library row
            nt = _norm_title(j.get("title", ""))
            if nt in lib_norms:
                continue  # a library file already covers this title
            cur = best_by_norm.get(nt)
            if cur is None or _job_priority(j) >= _job_priority(cur):
                best_by_norm[nt] = j
        for j in best_by_norm.values():
            clean = _clean_title(j.get("title", ""))
            is_ep = j.get("kind") == "show"
            poster_key = ("show:" if is_ep else "movie:") + _norm_title(clean)
            posters_mod.ensure(poster_key, clean, "series" if is_ep else "movie")
            rows.append(
                {
                    "row_kind": "job",
                    "id": j["id"],
                    "title": clean,
                    "kind": "episode" if is_ep else "movie",
                    "year": _extract_year(j.get("title", "")) or posters_mod.cached_year(poster_key),
                    "poster": posters_mod.cached(poster_key),
                    "done_at": j.get("finished_at") or j.get("started_at"),
                    "path": None,
                    "rel_path": None,
                    "name": j.get("title", ""),
                    "size": None,
                    "mtime": j.get("started_at"),
                    "series": None,
                    "season": None,
                    "stage": None,
                    "delay_ms": 0,
                    "needs_sync_review": False,
                    "sync_confidence": None,
                    "auto_delay_ms": 0,
                    "transfer_status": "idle",
                    "transfer_percent": 0.0,
                    "job_id": j["id"],
                    "job_status": j.get("status"),
                    "step_label": j.get("step_label"),
                    "overall_percent": j.get("overall_percent"),
                    "total_steps": j.get("total_steps"),
                    "pending": j.get("pending", False),
                }
            )
        return {"rows": rows, "library": str(get_settings().output.directory)}

    @app.post("/api/titles/redo")
    def titles_redo(req: PathRequest) -> dict:
        """Re-run the pipeline for a title, reusing its original source args.

        ``req.path`` may be a library file path OR a plain title. We find the
        most recent movie/show job with a matching normalised title and
        re-enqueue its exact args (same german title + hoster URL).
        """
        from bankai.cli import bgjobs

        raw = req.path
        want = _norm_title(Path(raw).name if ("/" in raw or "\\" in raw) else raw)
        best = None
        for j in bgjobs.list_jobs():
            if j.kind not in ("movie", "show"):
                continue
            if _norm_title(j.title) != want:
                continue
            if best is None or (j.started_at or 0) >= (best.started_at or 0):
                best = j
        if best is None or not best.args:
            raise HTTPException(status_code=404, detail="no previous run found to redo")
        job = webjobs.enqueue(kind=best.kind, title=best.title, args=list(best.args))
        return {"redo": job, "title": best.title}

    @app.get("/api/media/info")
    def media_info(path: str = Query(...)) -> dict:
        p = _safe_path(path)
        info = media_mod.probe(p)
        if info is None:
            raise HTTPException(status_code=422, detail="could not probe file")
        state = review_mod.get_state(str(p))
        return {
            "path": info.path,
            "size": info.size,
            "duration": info.duration,
            "video_codec": info.video_codec,
            "width": info.width,
            "height": info.height,
            "has_german": info.has_german,
            "browser_playable": info.browser_playable,
            "stage": state.stage,
            "delay_ms": state.delay_ms,
            "needs_sync_review": state.needs_sync_review,
            "sync_confidence": state.sync_confidence,
            "auto_delay_ms": state.auto_delay_ms,
            "audio_tracks": [
                {
                    "index": t.index,
                    "order": t.order,
                    "language": t.language,
                    "title": t.title,
                    "codec": t.codec,
                    "channels": t.channels,
                    "default": t.default,
                    "is_german": t.is_german,
                }
                for t in info.audio_tracks
            ],
        }

    @app.delete("/api/library/file")
    def library_delete(req: PathRequest) -> dict:
        p = _safe_path(req.path)
        try:
            p.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        review_mod.forget(str(p))
        return {"deleted": True, "path": str(p)}

    # ------------------------------------------------------------------
    # Streaming + transcode preview
    # ------------------------------------------------------------------
    @app.get("/api/media/stream")
    def media_stream(request: Request, path: str = Query(...)) -> Response:
        p = _safe_path(path)
        file_size = p.stat().st_size
        content_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        range_header = request.headers.get("range")
        if range_header is None:
            return FileResponse(p, media_type=content_type)
        start, end = _parse_range(range_header, file_size)
        length = end - start + 1

        def iter_file():
            with p.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            iter_file(), status_code=206, headers=headers, media_type=content_type
        )

    @app.get("/api/media/transcode")
    def media_transcode(
        path: str = Query(...),
        t: float = Query(0.0, ge=0.0),
        audio: int = Query(0, ge=0),
    ) -> Response:
        p = _safe_path(path)
        ffmpeg = media_mod.ffmpeg_bin()
        if ffmpeg is None:
            raise HTTPException(status_code=501, detail="ffmpeg not available")
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
        ]
        if t > 0:
            cmd += ["-ss", str(t)]
        cmd += [
            "-i", str(p),
            "-map", "0:v:0", "-map", f"0:a:{audio}?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", "pipe:1",
        ]
        import subprocess

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def stream():
            try:
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(1024 * 256)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.kill()

        return StreamingResponse(stream(), media_type="video/mp4")

    # ------------------------------------------------------------------
    # Audio alignment (waveform review)
    # ------------------------------------------------------------------
    @app.get("/api/media/waveform")
    def media_waveform(
        path: str = Query(...),
        stream: int = Query(..., ge=0),
        start: float = Query(0.0, ge=0.0),
        dur: float = Query(30.0, gt=0.0, le=1800.0),
        bins: int = Query(1000, ge=50, le=4000),
    ) -> dict:
        """Downsampled peak envelope for a *window* of one audio track.

        Only the requested ``[start, start+dur]`` slice is decoded, so this
        stays fast even on weak hardware (a full-movie decode would take
        minutes). Returns base64 uint8 peaks; the client maps pixels to peaks
        using ``start``/``dur``.
        """
        import array
        import base64
        import subprocess

        p = _safe_path(path)
        try:
            st = p.stat()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        key = (str(p), st.st_mtime_ns, stream, round(start, 2), round(dur, 2), bins)
        hit = _WAVEFORM_CACHE.get(key)
        if hit is not None:
            return hit
        ffmpeg = media_mod.ffmpeg_bin()
        if ffmpeg is None:
            raise HTTPException(status_code=501, detail="ffmpeg not available")
        # Decode at a rate that yields a few samples per output bin.
        ar = max(200, min(8000, int(bins * 4 / dur)))
        cmd = [
            ffmpeg, "-v", "error", "-ss", str(start), "-t", str(dur), "-i", str(p),
            "-map", f"0:{stream}", "-ac", "1", "-ar", str(ar),
            "-f", "s16le", "pipe:1",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="waveform decode timed out") from exc
        if proc.returncode != 0:
            raise HTTPException(status_code=422, detail="could not decode audio track")
        raw = proc.stdout
        pcm = array.array("h")
        pcm.frombytes(raw[: len(raw) - (len(raw) % 2)])
        n = len(pcm)
        binsz = max(1, n // bins)
        peaks = bytearray()
        for i in range(0, n, binsz):
            chunk = pcm[i : i + binsz]
            if not chunk:
                continue
            hi = max(chunk)
            lo = min(chunk)
            m = hi if hi >= -lo else -lo
            peaks.append(min(127, (m * 127) // 32768))
        out = {
            "start": start,
            "dur": dur,
            "bins": len(peaks),
            "peaks": base64.b64encode(bytes(peaks)).decode("ascii"),
        }
        _WAVEFORM_CACHE[key] = out
        return out

    @app.get("/api/media/audioclip")
    def media_audioclip(
        path: str = Query(...),
        stream: int = Query(..., ge=0),
        start: float = Query(0.0, ge=0.0),
        dur: float = Query(10.0, gt=0.0, le=120.0),
    ) -> Response:
        """Stream a short MP3 clip of one audio track for A/B playback."""
        import subprocess

        p = _safe_path(path)
        ffmpeg = media_mod.ffmpeg_bin()
        if ffmpeg is None:
            raise HTTPException(status_code=501, detail="ffmpeg not available")
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", str(start), "-t", str(dur), "-i", str(p),
            "-map", f"0:{stream}", "-ac", "2",
            "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def stream_clip():
            try:
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(1024 * 64)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.kill()

        return StreamingResponse(stream_clip(), media_type="audio/mpeg")

    # ------------------------------------------------------------------
    # Review / approval workflow
    # ------------------------------------------------------------------
    @app.post("/api/review/delay")
    def review_delay(req: DelayRequest) -> dict:
        p = _safe_path(req.path)
        state = review_mod.set_delay(str(p), req.delay_ms)
        return review_mod.to_dict(state)

    @app.post("/api/review/repack")
    def review_repack(req: DelayRequest) -> dict:
        p = _safe_path(req.path)
        result = media_mod.repack_audio_delay(p, delay_ms=req.delay_ms)
        if not result.ok:
            raise HTTPException(status_code=422, detail=result.message)
        review_mod.set_delay(str(p), req.delay_ms)
        return {"ok": True, "message": result.message, "delay_ms": result.delay_ms}

    @app.post("/api/review/approve")
    def review_approve(req: PathRequest) -> dict:
        p = _safe_path(req.path)
        state = review_mod.set_stage(str(p), "approved")
        return review_mod.to_dict(state)

    @app.post("/api/review/approve-batch")
    def review_approve_batch(req: PathsRequest) -> dict:
        approved: list[dict] = []
        errors: list[dict] = []
        for raw in req.paths:
            try:
                p = _safe_path(raw)
            except HTTPException as exc:
                errors.append({"path": raw, "detail": str(exc.detail)})
                continue
            state = review_mod.set_stage(str(p), "approved")
            approved.append(review_mod.to_dict(state))
        return {"approved": approved, "count": len(approved), "errors": errors}

    @app.post("/api/review/transfer")
    def review_transfer(req: PathRequest) -> dict:
        p = _safe_path(req.path)
        state = review_mod.get_state(str(p))
        if state.stage not in {"approved", "transferred"}:
            raise HTTPException(status_code=409, detail="file must be approved before transfer")
        kind = "show" if "Shows" in p.parts else "movie"
        args = ["transfer-run", str(p), "--kind", kind]
        job = webjobs.enqueue(kind="transfer", title=f"Transfer {p.name}", args=args)
        review_mod.set_transfer(str(p), "transferring", percent=0.0)
        return {"transfer": job}

    @app.post("/api/review/transfer-batch")
    def review_transfer_batch(req: PathsRequest) -> dict:
        """Transfer every approved path (or all approved files when omitted)."""
        targets: list[Path] = []
        errors: list[dict] = []
        if req.paths:
            for raw in req.paths:
                try:
                    targets.append(_safe_path(raw))
                except HTTPException as exc:
                    errors.append({"path": raw, "detail": str(exc.detail)})
        else:
            for path_str, state in review_mod.all_states().items():
                if state.stage == "approved":
                    try:
                        targets.append(_safe_path(path_str))
                    except HTTPException:
                        continue
        jobs: list[dict] = []
        skipped: list[dict] = []
        for p in targets:
            state = review_mod.get_state(str(p))
            if state.stage not in {"approved", "transferred"}:
                skipped.append({"path": str(p), "stage": state.stage})
                continue
            kind = "show" if "Shows" in p.parts else "movie"
            args = ["transfer-run", str(p), "--kind", kind]
            jobs.append(webjobs.enqueue(kind="transfer", title=f"Transfer {p.name}", args=args))
            review_mod.set_transfer(str(p), "transferring", percent=0.0)
        return {"transferred": jobs, "count": len(jobs), "skipped": skipped, "errors": errors}

    # ------------------------------------------------------------------
    # Server page (media-server contents)
    # ------------------------------------------------------------------
    @app.get("/api/server/contents")
    def server_contents(rescan: bool = Query(False)) -> dict:
        if rescan:
            media_mod.invalidate_server_cache()
        movies = media_mod.scan_server("movie", use_cache=not rescan)
        shows = media_mod.scan_server("show", use_cache=not rescan)
        return {
            "movies": [{"name": t.name, "present": t.present, "location": t.location} for t in movies],
            "shows": [{"name": t.name, "present": t.present, "location": t.location} for t in shows],
        }

    @app.get("/api/server/show")
    def server_show(path: str = Query(...)) -> dict:
        s = get_settings()
        target = Path(path).resolve()
        allowed = [Path(d).resolve() for d in s.web.server_show_dirs]
        if not any(target == a or a in target.parents for a in allowed):
            raise HTTPException(status_code=403, detail="path not under a configured show dir")
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="not a directory")
        seasons = media_mod.scan_server_show(target)
        return {
            "path": str(target),
            "seasons": [
                {
                    "name": se.name,
                    "season": se.season,
                    "episodes": [
                        {"name": e.name, "path": e.path, "size": e.size} for e in se.episodes
                    ],
                }
                for se in seasons
            ],
        }

    @app.get("/api/server/dirs")
    def server_dirs() -> dict:
        s = get_settings()
        return {
            "movie_dirs": [str(p) for p in s.web.server_movie_dirs],
            "show_dirs": [str(p) for p in s.web.server_show_dirs],
        }

    @app.post("/api/server/dirs")
    def server_dirs_add(req: ServerDirRequest) -> dict:
        if req.kind not in {"movie", "show"}:
            raise HTTPException(status_code=400, detail="kind must be movie or show")
        path = req.path.strip()
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        key = "web.server_movie_dirs" if req.kind == "movie" else "web.server_show_dirs"
        s = get_settings()
        current = [
            str(p) for p in (s.web.server_movie_dirs if req.kind == "movie" else s.web.server_show_dirs)
        ]
        if path not in current:
            current.append(path)
        from bankai.cli.main import _set_config_value

        _set_config_value(key, current)
        reset_settings_cache()
        media_mod.invalidate_server_cache()
        return {"kind": req.kind, "dirs": current}

    @app.delete("/api/server/dirs")
    def server_dirs_remove(req: ServerDirRequest) -> dict:
        if req.kind not in {"movie", "show"}:
            raise HTTPException(status_code=400, detail="kind must be movie or show")
        key = "web.server_movie_dirs" if req.kind == "movie" else "web.server_show_dirs"
        s = get_settings()
        current = [
            str(p) for p in (s.web.server_movie_dirs if req.kind == "movie" else s.web.server_show_dirs)
        ]
        current = [p for p in current if p != req.path.strip()]
        from bankai.cli.main import _set_config_value

        _set_config_value(key, current)
        reset_settings_cache()
        media_mod.invalidate_server_cache()
        return {"kind": req.kind, "dirs": current}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @app.get("/api/settings")
    def settings_get() -> dict:
        s = get_settings()
        data = s.model_dump(mode="json")
        out: list[dict] = []
        for key in sorted(SAFE_SETTING_KEYS):
            parts = key.split(".")
            cur: Any = data
            for part in parts:
                cur = cur.get(part) if isinstance(cur, dict) else None
            secret = _is_secret_key(key)
            out.append(
                {
                    "key": key,
                    "value": cur,
                    "secret": secret,
                    "is_set": bool(cur),
                }
            )
        return {"settings": out}

    @app.post("/api/settings")
    def settings_set(req: SettingRequest) -> dict:
        if req.key not in SAFE_SETTING_KEYS:
            raise HTTPException(status_code=403, detail="key not editable via web")
        from bankai.cli.main import _set_config_value

        path, written = _set_config_value(req.key, req.value)
        reset_settings_cache()
        return {"key": req.key, "saved": True, "path": str(path)}

    # ------------------------------------------------------------------
    # WebSocket: live queue + job log stream
    # ------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        from bankai.cli import bgjobs

        follow_job: str | None = None
        try:
            while True:
                # Non-blocking receive for control messages (e.g. follow a job).
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.01)
                    if isinstance(msg, dict) and "follow" in msg:
                        follow_job = msg.get("follow") or None
                except (asyncio.TimeoutError, ValueError):
                    pass
                payload: dict[str, Any] = {"type": "queue", "jobs": webjobs.snapshot()}
                if follow_job:
                    job = bgjobs.get_job(follow_job)
                    if job is not None:
                        payload["log"] = {"id": job.id, "tail": bgjobs.tail(job, lines=200)}
                await ws.send_json(payload)
                await asyncio.sleep(1.5)
        except WebSocketDisconnect:
            return
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("websocket closed: %s", exc)
            return

    # ------------------------------------------------------------------
    # Static frontend (prebuilt React) — mounted last so /api wins.
    # ------------------------------------------------------------------
    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").exists():
        assets = STATIC_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def index() -> Any:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> Any:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")
    else:
        @app.get("/")
        def placeholder() -> Any:
            return HTMLResponse(
                "<h1>bankai web</h1><p>Frontend assets not built yet. "
                "Run the Vite build and place output in "
                f"<code>{STATIC_DIR}</code>.</p>"
            )

    return app


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse a single ``bytes=start-end`` range header."""
    try:
        units, _, rng = range_header.partition("=")
        if units.strip() != "bytes":
            return 0, file_size - 1
        start_s, _, end_s = rng.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        start = max(0, start)
        end = min(end, file_size - 1)
        if start > end:
            start, end = 0, file_size - 1
        return start, end
    except (ValueError, AttributeError):
        return 0, file_size - 1
