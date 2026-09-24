"""Web job scheduler: concurrency-limited wrapper around bgjobs.

The web UI enqueues pipeline runs (movies / show episodes) which are
executed by the existing detached background-job supervisor
(:mod:`bankai.cli.bgjobs`). To avoid overloading the box, only
``web.max_concurrent_jobs`` movie/show pipelines run at once; the rest wait in
a persisted pending queue and start as slots free up. Transfers use a separate
unrestricted lane so copying an approved file never waits for a download.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.torrent.qbittorrent import login_succeeded
from bankai.web import reasons, updates

log = get_logger(__name__)
_LOCK = threading.RLock()
_OPERATION_KINDS = {"transfer", "repack", "torrent_replace"}
_OPERATION_COMMANDS = {"transfer-run", "review-repack", "review-replace-torrent"}
# A running job says nothing about what it is actually doing: waiting on a
# torrent and copying into the library are both "running". The stage key the
# worker already logs distinguishes them, so surface it as the row's phase.
_PHASE_BY_STEP = {
    "torrent": "downloading",
    # Organising and transferring are two copies -- pulling the file off the
    # download host, then writing it into the library -- but they are one
    # state to anyone reading the queue, and the step label still says which
    # half is running.
    "organize": "transferring",
    "transfer": "transferring",
    "repack": "repacking",
    "replace": "replacing",
}
_STREAM_FAILURE_THRESHOLD = 2
_STREAM_FAILURE_WINDOW_SECONDS = 10 * 60
_STREAM_FAILURE_COOLDOWN_SECONDS = 15 * 60
_READ_CONTEXT = threading.local()
_ROW_CACHE_LOCK = threading.Lock()
# Completed jobs never change again.  Cache their parsed log/progress row by
# metadata + log revision so the queue does not re-read hundreds of historical
# logs on every poll.  Running jobs naturally miss whenever their log grows.
_ROW_CACHE: dict[str, tuple[tuple[object, ...], dict]] = {}
_ANIME_STORAGE_CACHE_SECONDS = 15.0
_ANIME_STORAGE_STALE_SECONDS = 120.0
_ANIME_CACHE_LOCK = threading.Lock()
_QBIT_COMPLETED_CACHE: tuple[float, frozenset[str]] = (0.0, frozenset())
_QBIT_COMPLETED_REFRESHING = False
_QBIT_COMPLETED_LAST_ATTEMPT = 0.0
_QBIT_STATUS_CLIENT = None
_QBIT_STATUS_SIGNATURE: tuple[str, str, str] | None = None
_ANIME_RESERVE_CACHE: tuple[float, bool] = (0.0, False)


def _is_operation(kind: str | None, args: list[str] | None = None) -> bool:
    """Recognise hidden row-level work, including jobs made by older builds."""
    return bool(kind in _OPERATION_KINDS or (args and args[0] in _OPERATION_COMMANDS))


def _is_anime_job(args: list[str] | None) -> bool:
    return bool(args and args[0] == "anime-download")


def _completed_anime_hashes() -> frozenset[str]:
    """Return cached hashes and refresh qBittorrent outside request/queue locks."""

    global _QBIT_COMPLETED_LAST_ATTEMPT, _QBIT_COMPLETED_REFRESHING
    now = time.monotonic()
    with _ANIME_CACHE_LOCK:
        updated_at, hashes = _QBIT_COMPLETED_CACHE
        if now - updated_at < _ANIME_STORAGE_CACHE_SECONDS:
            return hashes
        if (
            not _QBIT_COMPLETED_REFRESHING
            and now - _QBIT_COMPLETED_LAST_ATTEMPT >= _ANIME_STORAGE_CACHE_SECONDS
        ):
            _QBIT_COMPLETED_REFRESHING = True
            _QBIT_COMPLETED_LAST_ATTEMPT = now
            threading.Thread(
                target=_refresh_completed_anime_hashes,
                name="bankai-qbit-completed",
                daemon=True,
            ).start()
        return hashes if now - updated_at <= _ANIME_STORAGE_STALE_SECONDS else frozenset()


def _fetch_completed_anime_hashes() -> frozenset[str]:
    """Fetch completed hashes with a reusable authenticated HTTP session."""

    global _QBIT_STATUS_CLIENT, _QBIT_STATUS_SIGNATURE
    import httpx

    settings = get_settings().qbittorrent
    signature = (settings.url.rstrip("/"), settings.username, settings.password)
    if _QBIT_STATUS_CLIENT is None or _QBIT_STATUS_SIGNATURE != signature:
        if _QBIT_STATUS_CLIENT is not None:
            with suppress(Exception):
                _QBIT_STATUS_CLIENT.close()
        _QBIT_STATUS_CLIENT = httpx.Client(
            base_url=signature[0],
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=True,
        )
        _QBIT_STATUS_SIGNATURE = signature
        login = _QBIT_STATUS_CLIENT.post(
            "/api/v2/auth/login",
            data={"username": signature[1], "password": signature[2]},
            headers={"Referer": settings.url},
        )
        if not login_succeeded(login):
            raise RuntimeError(f"qBittorrent login failed ({login.status_code})")

    response = _QBIT_STATUS_CLIENT.get(
        "/api/v2/torrents/info",
        params={"category": settings.category, "filter": "completed"},
    )
    if response.status_code in {401, 403}:
        login = _QBIT_STATUS_CLIENT.post(
            "/api/v2/auth/login",
            data={"username": signature[1], "password": signature[2]},
            headers={"Referer": settings.url},
        )
        if not login_succeeded(login):
            raise RuntimeError(f"qBittorrent login failed ({login.status_code})")
        response = _QBIT_STATUS_CLIENT.get(
            "/api/v2/torrents/info",
            params={"category": settings.category, "filter": "completed"},
        )
    response.raise_for_status()
    return frozenset(
        str(row.get("hash", "")).casefold()
        for row in response.json()
        if row.get("hash") and float(row.get("progress", 0)) >= 1.0
    )


def _refresh_completed_anime_hashes() -> None:
    global _QBIT_COMPLETED_CACHE, _QBIT_COMPLETED_REFRESHING
    try:
        hashes = _fetch_completed_anime_hashes()
        with _ANIME_CACHE_LOCK:
            _QBIT_COMPLETED_CACHE = (time.monotonic(), hashes)
    except Exception as exc:
        log.warning("could not inspect completed Anime torrents: %s", exc)
    finally:
        with _ANIME_CACHE_LOCK:
            _QBIT_COMPLETED_REFRESHING = False


def _anime_reserve_available() -> bool:
    global _ANIME_RESERVE_CACHE
    now = time.monotonic()
    with _ANIME_CACHE_LOCK:
        if now - _ANIME_RESERVE_CACHE[0] < _ANIME_STORAGE_CACHE_SECONDS:
            return _ANIME_RESERVE_CACHE[1]
    from bankai.web.erai import download_free_space_gib, free_space_gib

    free = free_space_gib()
    download_free = download_free_space_gib()
    reserve = get_settings().anime.min_free_space_gib
    ready = free is not None and free > reserve and (
        download_free is None or download_free > reserve
    )
    with _ANIME_CACHE_LOCK:
        _ANIME_RESERVE_CACHE = (now, ready)
    return ready


def _anime_storage_ready(args: list[str] | None) -> bool:
    if not _is_anime_job(args) or "--require-german-subtitles" not in (args or []):
        return True
    info_hash = (bgjobs.argument_value(args, "--info-hash") or "").casefold()
    # A finished torrent needs no additional download space. Let its worker
    # organize and transfer it even while new/incomplete downloads remain held
    # behind the configured reserve.
    if info_hash and info_hash in _completed_anime_hashes():
        return True
    return _anime_reserve_available()


def _pending_path() -> Path:
    return bgjobs.jobs_root().parent / "web_pending.json"


@dataclass
class PendingJob:
    id: str
    kind: str  # "movie" | "show" | "transfer"
    title: str
    args: list[str]
    created_at: float = field(default_factory=time.time)


def _load_pending() -> list[PendingJob]:
    p = _pending_path()
    if not p.exists():
        return []
    try:
        return [PendingJob(**row) for row in json.loads(p.read_text() or "[]")]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def _save_pending(items: list[PendingJob]) -> None:
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(i) for i in items], indent=2))
    tmp.replace(p)


def _context_jobs() -> list | None:
    return getattr(_READ_CONTEXT, "jobs", None)


def _context_pending() -> list[PendingJob] | None:
    return getattr(_READ_CONTEXT, "pending", None)


def _call_with_jobs(function, jobs: list):
    """Call a no-argument helper against ``jobs`` via the read context."""

    previous_jobs = _context_jobs()
    _READ_CONTEXT.jobs = jobs
    try:
        return function()
    finally:
        if previous_jobs is None:
            with suppress(AttributeError):
                del _READ_CONTEXT.jobs
        else:
            _READ_CONTEXT.jobs = previous_jobs


@contextmanager
def dashboard_read() -> Iterator[None]:
    """Share one reconciled filesystem snapshot across a dashboard request.

    ``/api/titles`` needs pipeline, transfer and repack views.  Previously each
    helper reconciled and scanned the complete job registry independently.
    Keeping the snapshot thread-local preserves the public helper APIs (and
    their tests) while collapsing that repeated work into one pass.
    """

    reconcile()
    previous_jobs = _context_jobs()
    previous_pending = _context_pending()
    _READ_CONTEXT.jobs = bgjobs.list_jobs()
    _READ_CONTEXT.pending = _load_pending()
    try:
        yield
    finally:
        if previous_jobs is None:
            with suppress(AttributeError):
                del _READ_CONTEXT.jobs
        else:
            _READ_CONTEXT.jobs = previous_jobs
        if previous_pending is None:
            with suppress(AttributeError):
                del _READ_CONTEXT.pending
        else:
            _READ_CONTEXT.pending = previous_pending


def _running_count(jobs: list | None = None) -> int:
    """Count active or stopped pipelines against the worker-slot limit.

    A stopped job reserves its slot; otherwise stopping every active job would
    immediately promote queued work and defeat the user's request to halt it.
    """
    return sum(
        1
        for job in (
            jobs
            if jobs is not None
            else (_context_jobs() if _context_jobs() is not None else bgjobs.list_jobs())
        )
        if job.status in {"running", "stopped"}
        and not _is_operation(job.kind, getattr(job, "args", None))
        # Anime publishing is governed by anime.max_concurrent_transfers and is
        # spawned by the release reconciler, not from this queue. Counting it
        # here would let a couple of episode copies stall every movie pipeline.
        and not _is_anime_job(getattr(job, "args", None))
    )


def _norm_job_title(title: str) -> str:
    import re

    t = title.lower()
    t = re.sub(r"\(\d{4}\)", "", t)
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t


def _running_titles(jobs: list | None = None) -> set[str]:
    """Normalised titles of all running jobs, including transfers.

    Transfer titles are prefixed with ``Transfer`` and therefore cannot clash
    with pipeline titles, but including them prevents a double-click from
    starting two copies of the same approved file.
    """
    return {
        _norm_job_title(job.title)
        for job in (
            jobs
            if jobs is not None
            else (_context_jobs() if _context_jobs() is not None else bgjobs.list_jobs())
        )
        if job.status in {"running", "stopped"}
    }


def enqueue(*, kind: str, title: str, args: list[str]) -> dict:
    """Queue a job. Transfers start immediately; pipelines obey the limit.

    Refuses to queue a duplicate of something already running or already
    pending (same title) — two copies would race on the same extract dir and
    one would fail with no obvious reason.
    """
    with _LOCK:
        settings = get_settings()
        limit = max(1, settings.web.max_concurrent_jobs)
        nt = _norm_job_title(title)
        pending = _load_pending()
        if nt and (nt in _running_titles() or any(_norm_job_title(p.title) == nt for p in pending)):
            return {"status": "duplicate", "title": title}
        paused = updates.maintenance_active()
        if not paused and kind in _OPERATION_KINDS:
            job = bgjobs.spawn(kind=kind, title=title, args=args)
            return {"status": "running", "id": job.id, "title": title}
        if not paused and _running_count() < limit and _anime_storage_ready(args):
            job = bgjobs.spawn(kind=kind, title=title, args=args)
            return {"status": "running", "id": job.id, "title": title}
        item = PendingJob(id=uuid.uuid4().hex[:8], kind=kind, title=title, args=args)
        pending.append(item)
        _save_pending(pending)
        return {"status": "queued", "id": item.id, "title": title}


def reconcile() -> int:
    """Promote pending jobs, bypassing pipeline slots for transfers."""
    with _LOCK:
        if updates.maintenance_active():
            return 0
        pending = _load_pending()
        if not pending:
            return 0
        settings = get_settings()
        limit = max(1, settings.web.max_concurrent_jobs)
        jobs = bgjobs.list_jobs()
        # Keep these no-argument calls compatible with existing scheduler
        # hooks/tests while they consume the shared context internally.
        running_titles = _call_with_jobs(_running_titles, jobs)
        running_count = _call_with_jobs(_running_count, jobs)
        started = 0

        # Migrate transfers queued by older releases immediately, even when a
        # movie/show pipeline currently occupies every configured slot.
        transfer_items = [item for item in pending if item.kind in _OPERATION_KINDS]
        pending = [item for item in pending if item.kind not in _OPERATION_KINDS]
        for item in transfer_items:
            nt = _norm_job_title(item.title)
            if nt and nt in running_titles:
                continue
            try:
                bgjobs.spawn(
                    kind=item.kind,
                    title=item.title,
                    args=item.args,
                    created_at=item.created_at,
                )
                running_titles.add(nt)
                if not _is_operation(item.kind, item.args):
                    running_count += 1
                started += 1
            except Exception as exc:  # pragma: no cover - spawn failure
                log.warning("failed to start pending job %s: %s", item.id, exc)

        # A cluster of stream-extraction failures generally means the shared
        # hoster/browser path is unhealthy, not that every queued title is
        # invalid. Keep the remaining queue intact until the source has had a
        # chance to recover. Explicit Force start remains available as a
        # deliberate canary and operations above are never held back.
        cooldown_until = _call_with_jobs(_stream_failure_cooldown_until, jobs)
        if pending and cooldown_until is not None:
            log.warning("stream source circuit open; non-Anime pipelines are paused")

        while pending and running_count < limit:
            index = next(
                (
                    i
                    for i, item in enumerate(pending)
                    if (cooldown_until is None or _is_anime_job(item.args))
                    and _anime_storage_ready(item.args)
                ),
                None,
            )
            if index is None:
                break
            item = pending.pop(index)
            nt = _norm_job_title(item.title)
            if nt and nt in running_titles:
                continue  # already running -> drop the duplicate instead of colliding
            try:
                bgjobs.spawn(
                    kind=item.kind,
                    title=item.title,
                    args=item.args,
                    created_at=item.created_at,
                )
                running_titles.add(nt)
                running_count += 1
                started += 1
            except Exception as exc:  # pragma: no cover - spawn failure
                log.warning("failed to start pending job %s: %s", item.id, exc)
        _save_pending(pending)
        return started


# Reconciling releases means talking to qBittorrent about every tracked
# torrent, which is far heavier than promoting a pending job and does not need
# to happen on every tick.
_RELEASE_RECONCILE_SECONDS = 20.0
# Identifying every episode's encode is a one-off crawl of the whole library.
# It runs in slices so it never competes for long with publishing, and stops
# entirely once every file has been identified.
_CODEC_SWEEP_SECONDS = 60.0
_CODEC_SWEEP_BATCH = 40


async def scheduler(*, poll_seconds: float = 2.0) -> None:
    """Dispatch queued work continuously, independent of an open browser page."""

    next_release_pass = 0.0
    next_codec_pass = 0.0
    while True:
        try:
            await asyncio.to_thread(reconcile)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("queue scheduler failed: %s", exc)
        if time.monotonic() >= next_release_pass:
            next_release_pass = time.monotonic() + _RELEASE_RECONCILE_SECONDS
            try:
                from bankai.web import erai

                await erai.reconcile_releases()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("release reconciliation failed: %s", exc)
        if time.monotonic() >= next_codec_pass:
            next_codec_pass = time.monotonic() + _CODEC_SWEEP_SECONDS
            try:
                from pathlib import Path

                from bankai.web import anime_library

                result = await asyncio.to_thread(
                    anime_library.sweep_codecs,
                    Path(get_settings().transfer.anime_shows_dir),
                    limit=_CODEC_SWEEP_BATCH,
                )
                if result["probed"]:
                    log.info(
                        "Identified the encode of %d episode(s); %d still unidentified",
                        result["probed"],
                        result["remaining"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("codec sweep failed: %s", exc)
        await asyncio.sleep(poll_seconds)


def list_pending() -> list[PendingJob]:
    return _load_pending()


def cancel_pending(job_id: str) -> bool:
    with _LOCK:
        pending = _load_pending()
        kept = [i for i in pending if not (i.id == job_id or i.id.startswith(job_id))]
        if len(kept) == len(pending):
            return False
        _save_pending(kept)
        return True


def force_start_pending(job_id: str) -> bgjobs.BgJob | None:
    """Start a queued pipeline immediately, deliberately bypassing the limit."""
    with _LOCK:
        if updates.maintenance_active():
            raise RuntimeError("Bankai is updating; new jobs remain queued")
        pending = _load_pending()
        index = next(
            (
                i
                for i, item in enumerate(pending)
                if item.id == job_id or item.id.startswith(job_id)
            ),
            None,
        )
        if index is None:
            return None
        item = pending[index]
        if not _anime_storage_ready(item.args):
            raise RuntimeError("Anime free-space reserve reached; download remains queued")
        nt = _norm_job_title(item.title)
        if nt and nt in _running_titles():
            raise RuntimeError("a job for this title is already running")
        pending.pop(index)
        _save_pending(pending)
        try:
            return bgjobs.spawn(
                kind=item.kind,
                title=item.title,
                args=item.args,
                created_at=item.created_at,
            )
        except Exception:
            pending.insert(index, item)
            _save_pending(pending)
            raise


def reorder_pending(job_id: str, position: int) -> int | None:
    """Move a queued job to a one-based priority position."""
    with _LOCK:
        pending = _load_pending()
        index = next(
            (
                i
                for i, item in enumerate(pending)
                if item.id == job_id or item.id.startswith(job_id)
            ),
            None,
        )
        if index is None:
            return None
        item = pending.pop(index)
        new_index = max(0, min(len(pending), int(position) - 1))
        pending.insert(new_index, item)
        _save_pending(pending)
        return new_index + 1


def continue_job(job_id: str) -> bgjobs.BgJob | None:
    """Resume a stopped background job in place when a slot is available."""
    with _LOCK:
        job = bgjobs.get_job(job_id)
        if job is None or job.status != "stopped":
            return None
        # The stopped job already reserves a scheduler slot, so continuing it
        # replaces that reservation without increasing occupancy.
        _set_torrent_paused(job.id, paused=False)
        return bgjobs.resume(job)


def stop_job(job_id: str) -> bgjobs.BgJob | None:
    """Stop the process tree and pause its active qBittorrent download."""
    with _LOCK:
        job = bgjobs.get_job(job_id)
        if job is None or not job.stop():
            return None
        _set_torrent_paused(job.id, paused=True)
        return job


def _set_torrent_paused(job_id: str, *, paused: bool) -> None:
    from bankai.torrent import actions as torrent_actions

    torrent_hash = torrent_actions.get_active_torrent(job_id)
    if not torrent_hash:
        return
    try:
        import asyncio

        from bankai.torrent.qbittorrent import QBittorrentClient

        async def apply() -> None:
            async with QBittorrentClient() as client:
                if paused:
                    await client.pause(torrent_hash)
                else:
                    await client.resume(torrent_hash)

        asyncio.run(apply())
    except Exception as exc:  # stopping the local worker still matters
        log.warning("failed to %s torrent for %s: %s", "pause" if paused else "resume", job_id, exc)


def _job_reason(j: bgjobs.BgJob) -> str | None:
    """Human-readable reason for a failed job.

    Falls back to a clear message when the log holds no exception -- e.g. the
    job process was interrupted (service restart) or timed out, which would
    otherwise leave the Reason column blank.
    """
    if j.status != "failed":
        return None
    return bgjobs.failure_reason(j) or (
        "Stopped before completing \u2014 no error was logged (the job was likely interrupted or timed out)"
    )


def _stream_failure_cooldown_until(
    now: float | None = None, *, jobs: list | None = None
) -> float | None:
    """Return the extraction circuit's recovery time after clustered failures."""
    current = time.time() if now is None else now
    failures: list[float] = []
    for job in (
        jobs
        if jobs is not None
        else (_context_jobs() if _context_jobs() is not None else bgjobs.list_jobs())
    ):
        if _is_operation(job.kind, getattr(job, "args", None)):
            continue
        finished_at = getattr(job, "finished_at", None)
        if (
            job.status != "failed"
            or finished_at is None
            or finished_at < current - _STREAM_FAILURE_WINDOW_SECONDS
        ):
            continue
        classified = reasons.classify_reason(_job_reason(job))
        if classified and classified[0] == "extract":
            failures.append(finished_at)
    if len(failures) < _STREAM_FAILURE_THRESHOLD:
        return None
    until = max(failures) + _STREAM_FAILURE_COOLDOWN_SECONDS
    return until if until > current else None


def _job_revision(job) -> tuple[object, ...] | None:
    """Cheap revision for parsed display data, or ``None`` for test doubles."""

    log_path = getattr(job, "log_path", None)
    job_id = getattr(job, "id", None)
    if job_id is None or log_path is None:
        return None
    try:
        stat = Path(log_path).stat()
        log_revision = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        log_revision = (0, 0)
    return (
        getattr(job, "status", None),
        getattr(job, "updated_at", None),
        getattr(job, "finished_at", None),
        getattr(job, "exit_code", None),
        getattr(job, "final_path", None),
        *log_revision,
    )


def _phase(status: str, step_key: str | None) -> str:
    """Narrow "running" to the stage the worker last announced."""
    if status != "running":
        return status
    return _PHASE_BY_STEP.get(step_key or "", "running")


def _display_row(job) -> dict:
    """Build the stable part of a queue row, reusing unchanged log parsing."""

    revision = _job_revision(job)
    job_id = str(getattr(job, "id", ""))
    if revision is not None:
        with _ROW_CACHE_LOCK:
            cached = _ROW_CACHE.get(job_id)
        if cached and cached[0] == revision:
            return dict(cached[1])

    snap = bgjobs.progress_snapshot(job)
    raw_reason = _job_reason(job)
    cls = reasons.classify_reason(raw_reason)
    row = {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "status": job.status,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at or job.finished_at or job.started_at,
        "exit_code": job.exit_code,
        "final_path": job.final_path,
        "reason": cls[1] if cls else None,
        "reason_code": cls[0] if cls else None,
        "reason_detail": raw_reason,
        "step": snap.step,
        "total_steps": snap.total_steps,
        "step_key": snap.step_key,
        "step_label": snap.step_label,
        "overall_percent": snap.overall_percent,
        "phase": _phase(job.status, snap.step_key),
        # Only the transfer part is carried. The full part map would multiply
        # the queue payload by every stage on every row.
        "transfer_percent": (
            snap.parts["transfer"].percent if "transfer" in snap.parts else None
        ),
        "pending": False,
        "queue_position": None,
        "queue_total": None,
        "german_source_url": getattr(job, "german_source_url", None)
        or bgjobs.argument_value(job.args, "--url"),
        "torrent_source_url": getattr(job, "torrent_source_url", None),
        "torrent_source_title": getattr(job, "torrent_source_title", None),
    }
    if revision is not None:
        with _ROW_CACHE_LOCK:
            _ROW_CACHE[job_id] = (revision, dict(row))
            if len(_ROW_CACHE) > 1000:
                live_ids = {path.parent.name for path in bgjobs.jobs_root().glob("*/meta.json")}
                for stale_id in set(_ROW_CACHE) - live_ids:
                    _ROW_CACHE.pop(stale_id, None)
    return row


def snapshot(*, anime_only: bool = False) -> list[dict]:
    """Unified list of running/finished jobs + pending, newest first.

    Transfer jobs are intentionally excluded — they are surfaced as a column
    on the library entry (see :func:`transfer_states`) rather than as their
    own queue point.
    """
    context_jobs = _context_jobs()
    if context_jobs is None:
        reconcile()
    jobs = context_jobs if context_jobs is not None else bgjobs.list_jobs()
    out: list[dict] = []
    for j in jobs:
        if (
            _is_operation(j.kind, getattr(j, "args", None))
            or _is_anime_job(getattr(j, "args", None)) != anime_only
        ):
            continue
        from bankai.torrent import actions as torrent_actions

        action = torrent_actions.get_request(j.id) if j.status == "running" else None
        row = _display_row(j)
        if anime_only:
            row["tvdb_id"] = bgjobs.argument_value(j.args, "--tvdb-id")
        row["action_required"] = bool(action and action.get("status") == "waiting")
        out.append(row)
    pending = _context_pending()
    visible_pending = [
        item
        for item in (pending if pending is not None else _load_pending())
        if not _is_operation(item.kind, item.args) and _is_anime_job(item.args) == anime_only
    ]
    queue_total = len(visible_pending)
    stream_cooldown = _call_with_jobs(_stream_failure_cooldown_until, jobs)
    # Every pending Anime row shares the same two filesystem reserve checks.
    # Calling this per row re-read the multi-megabyte Erai state file more than
    # a thousand times on large backlogs and made /api/anime/queue take minutes.
    anime_storage_ready = (
        _anime_storage_ready(visible_pending[0].args) if anime_only and visible_pending else True
    )
    for queue_position, item in enumerate(visible_pending, start=1):
        if _is_operation(item.kind, item.args):
            continue
        out.append(
            {
                "id": item.id,
                "kind": item.kind,
                "title": item.title,
                "status": "queued",
                "started_at": item.created_at,
                "finished_at": None,
                "updated_at": item.created_at,
                "exit_code": None,
                "final_path": None,
                "step": None,
                "total_steps": None,
                "step_key": None,
                "phase": "queued",
                "transfer_percent": None,
                "step_label": (
                    "Waiting for Anime storage reserve"
                    if anime_only and not anime_storage_ready
                    else "Waiting for Anime storage reserve"
                    if not anime_only and not _anime_storage_ready(item.args)
                    else "Waiting for stream source recovery"
                    if stream_cooldown is not None and not _is_anime_job(item.args)
                    else "Waiting for a free slot"
                ),
                "overall_percent": 0.0,
                "pending": True,
                "action_required": False,
                "queue_position": queue_position,
                "queue_total": queue_total,
                "german_source_url": bgjobs.argument_value(item.args, "--url"),
                "torrent_source_url": None,
                "torrent_source_title": None,
            }
        )
    if anime_only:
        pending_args = {item.id: item.args for item in visible_pending}
        for row in out:
            if row["id"] in pending_args:
                row["tvdb_id"] = bgjobs.argument_value(pending_args[row["id"]], "--tvdb-id")
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out


def anime_snapshot() -> list[dict]:
    """Queue rows belonging exclusively to direct Anime downloads."""

    return snapshot(anime_only=True)


def catalog_titles() -> set[str]:
    """Titles that should be marked as already added without parsing logs.

    Search/Discover only need membership, not progress or failure reasons.  A
    metadata-only pass is an order of magnitude cheaper than ``snapshot()``
    and deliberately excludes failed/cancelled attempts so they remain
    discoverable for a new run.
    """

    jobs = _context_jobs()
    if jobs is None:
        jobs = bgjobs.list_jobs()
    titles = {
        job.title
        for job in jobs
        if not _is_operation(job.kind, getattr(job, "args", None))
        and job.status in {"running", "stopped", "done"}
    }
    pending = _context_pending()
    for item in pending if pending is not None else _load_pending():
        if not _is_operation(item.kind, item.args):
            titles.add(item.title)
    return titles


def _transfer_target(args: list[str]) -> str | None:
    """Return the library path a ``transfer-run`` job operates on."""
    if not args or args[0] != "transfer-run":
        return None
    skip_next = False
    for a in args[1:]:
        if skip_next:
            skip_next = False
            continue
        if a.startswith("-"):
            skip_next = True  # this flag consumes the following value
            continue
        return a
    return None


def transfer_states() -> dict[str, dict]:
    """Map ``resolved library path -> {status, percent, id, exit_code}``.

    Reconciles the detached ``transfer-run`` background jobs into a per-entry
    status so the library can show transfer progress as a column instead of a
    standalone queue job. Newest job per path wins.
    """
    context_jobs = _context_jobs()
    if context_jobs is None:
        reconcile()
    by_path: dict[str, dict] = {}
    jobs = sorted(
        context_jobs if context_jobs is not None else bgjobs.list_jobs(),
        key=lambda j: j.started_at or 0,
    )
    for j in jobs:
        if j.kind != "transfer":
            continue
        target = _transfer_target(j.args)
        if not target:
            continue
        try:
            key = str(Path(target).resolve())
        except OSError:
            key = target
        if j.status == "running":
            status, percent = "transferring", bgjobs.progress_snapshot(j).overall_percent or 0.0
        elif j.status == "done" and (j.exit_code in (0, None)):
            status, percent = "done", 100.0
        elif j.status in ("failed", "error") or (j.exit_code not in (0, None)):
            status, percent = "failed", 0.0
        else:
            status, percent = "transferring", 0.0
        by_path[key] = {
            "status": status,
            "percent": percent,
            "id": j.id,
            "exit_code": j.exit_code,
        }
    # Include pending transfers (waiting for a slot) as queued transfers.
    pending = _context_pending()
    for item in pending if pending is not None else _load_pending():
        if item.kind != "transfer":
            continue
        target = _transfer_target(item.args)
        if not target:
            continue
        try:
            key = str(Path(target).resolve())
        except OSError:
            key = target
        by_path.setdefault(
            key, {"status": "transferring", "percent": 0.0, "id": item.id, "exit_code": None}
        )
    return by_path


def _operation_target(args: list[str], command: str) -> str | None:
    """Return the first positional path after a hidden operation command."""
    if not args or args[0] != command:
        return None
    for value in args[1:]:
        if not value.startswith("-"):
            return value
    return None


def repack_states() -> dict[str, dict]:
    """Newest detached audio-repack/torrent-replacement state per path."""
    context_jobs = _context_jobs()
    if context_jobs is None:
        reconcile()
    by_path: dict[str, dict] = {}
    for job in sorted(
        context_jobs if context_jobs is not None else bgjobs.list_jobs(),
        key=lambda item: item.started_at or 0,
    ):
        if job.kind not in {"repack", "torrent_replace"}:
            continue
        command = "review-repack" if job.kind == "repack" else "review-replace-torrent"
        target = _operation_target(job.args, command)
        if not target:
            continue
        try:
            key = str(Path(target).resolve())
        except OSError:
            key = target
        if job.status == "running":
            status = "repacking"
            percent = bgjobs.progress_snapshot(job).overall_percent or 0.0
        elif job.status == "done" and job.exit_code in (0, None):
            status, percent = "done", 100.0
        else:
            status, percent = "failed", 0.0
        by_path[key] = {
            "status": status,
            "percent": percent,
            "kind": "audio" if job.kind == "repack" else "torrent",
            "label": "Repacking audio" if job.kind == "repack" else "Replacing torrent",
            "id": job.id,
            "reason": _job_reason(job),
        }
    return by_path
