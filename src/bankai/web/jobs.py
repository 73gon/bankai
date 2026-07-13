"""Web job scheduler: concurrency-limited wrapper around bgjobs.

The web UI enqueues pipeline runs (movies / show episodes) which are
executed by the existing detached background-job supervisor
(:mod:`bankai.cli.bgjobs`). To avoid overloading the box, only
``web.max_concurrent_jobs`` movie/show pipelines run at once; the rest wait in
a persisted pending queue and start as slots free up. Transfers use a separate
unrestricted lane so copying an approved file never waits for a download.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web import reasons

log = get_logger(__name__)
_LOCK = threading.RLock()


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


def _running_count() -> int:
    """Count pipeline jobs against the configured concurrency limit."""
    return sum(
        1
        for job in bgjobs.list_jobs()
        if job.status == "running" and job.kind != "transfer"
    )


def _norm_job_title(title: str) -> str:
    import re

    t = title.lower()
    t = re.sub(r"\(\d{4}\)", "", t)
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t


def _running_titles() -> set[str]:
    """Normalised titles of all running jobs, including transfers.

    Transfer titles are prefixed with ``Transfer`` and therefore cannot clash
    with pipeline titles, but including them prevents a double-click from
    starting two copies of the same approved file.
    """
    return {
        _norm_job_title(job.title)
        for job in bgjobs.list_jobs()
        if job.status == "running"
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
        if kind == "transfer":
            job = bgjobs.spawn(kind=kind, title=title, args=args)
            return {"status": "running", "id": job.id, "title": title}
        if _running_count() < limit:
            job = bgjobs.spawn(kind=kind, title=title, args=args)
            return {"status": "running", "id": job.id, "title": title}
        item = PendingJob(id=uuid.uuid4().hex[:8], kind=kind, title=title, args=args)
        pending.append(item)
        _save_pending(pending)
        return {"status": "queued", "id": item.id, "title": title}


def reconcile() -> int:
    """Promote pending jobs, bypassing pipeline slots for transfers."""
    with _LOCK:
        pending = _load_pending()
        if not pending:
            return 0
        settings = get_settings()
        limit = max(1, settings.web.max_concurrent_jobs)
        running_titles = _running_titles()
        started = 0

        # Migrate transfers queued by older releases immediately, even when a
        # movie/show pipeline currently occupies every configured slot.
        transfer_items = [item for item in pending if item.kind == "transfer"]
        pending = [item for item in pending if item.kind != "transfer"]
        for item in transfer_items:
            nt = _norm_job_title(item.title)
            if nt and nt in running_titles:
                continue
            try:
                bgjobs.spawn(kind=item.kind, title=item.title, args=item.args)
                running_titles.add(nt)
                started += 1
            except Exception as exc:  # pragma: no cover - spawn failure
                log.warning("failed to start pending job %s: %s", item.id, exc)

        while pending and _running_count() < limit:
            item = pending.pop(0)
            nt = _norm_job_title(item.title)
            if nt and nt in running_titles:
                continue  # already running -> drop the duplicate instead of colliding
            try:
                bgjobs.spawn(kind=item.kind, title=item.title, args=item.args)
                running_titles.add(nt)
                started += 1
            except Exception as exc:  # pragma: no cover - spawn failure
                log.warning("failed to start pending job %s: %s", item.id, exc)
        _save_pending(pending)
        return started


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


def _job_reason(j: bgjobs.BgJob) -> str | None:
    """Human-readable reason for a failed job.

    Falls back to a clear message when the log holds no exception -- e.g. the
    job process was interrupted (service restart) or timed out, which would
    otherwise leave the Reason column blank.
    """
    if j.status != "failed":
        return None
    return bgjobs.failure_reason(j) or ("Stopped before completing \u2014 no error was logged (the job was likely interrupted or timed out)")


def snapshot() -> list[dict]:
    """Unified list of running/finished jobs + pending, newest first.

    Transfer jobs are intentionally excluded — they are surfaced as a column
    on the library entry (see :func:`transfer_states`) rather than as their
    own queue point.
    """
    reconcile()
    out: list[dict] = []
    for j in bgjobs.list_jobs():
        if j.kind == "transfer":
            continue
        snap = bgjobs.progress_snapshot(j)
        raw_reason = _job_reason(j)
        cls = reasons.classify_reason(raw_reason)
        out.append(
            {
                "id": j.id,
                "kind": j.kind,
                "title": j.title,
                "status": j.status,
                "started_at": j.started_at,
                "finished_at": j.finished_at,
                "exit_code": j.exit_code,
                "final_path": j.final_path,
                "reason": cls[1] if cls else None,
                "reason_code": cls[0] if cls else None,
                "reason_detail": raw_reason,
                "step": snap.step,
                "total_steps": snap.total_steps,
                "step_label": snap.step_label,
                "overall_percent": snap.overall_percent,
                "pending": False,
            }
        )
    for item in _load_pending():
        if item.kind == "transfer":
            continue
        out.append(
            {
                "id": item.id,
                "kind": item.kind,
                "title": item.title,
                "status": "queued",
                "started_at": item.created_at,
                "finished_at": None,
                "exit_code": None,
                "final_path": None,
                "step": None,
                "total_steps": None,
                "step_label": "Waiting for a free slot",
                "overall_percent": 0.0,
                "pending": True,
            }
        )
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out


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
    reconcile()
    by_path: dict[str, dict] = {}
    jobs = sorted(bgjobs.list_jobs(), key=lambda j: j.started_at or 0)
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
    for item in _load_pending():
        if item.kind != "transfer":
            continue
        target = _transfer_target(item.args)
        if not target:
            continue
        try:
            key = str(Path(target).resolve())
        except OSError:
            key = target
        by_path.setdefault(key, {"status": "transferring", "percent": 0.0, "id": item.id, "exit_code": None})
    return by_path
