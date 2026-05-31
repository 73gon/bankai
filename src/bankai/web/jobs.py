"""Web job scheduler: concurrency-limited wrapper around bgjobs.

The web UI enqueues pipeline runs (movies / show episodes) which are
executed by the existing detached background-job supervisor
(:mod:`bankai.cli.bgjobs`). To avoid overloading the box, only
``web.max_concurrent_jobs`` run at once; the rest wait in a persisted
pending queue and start as slots free up.
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
    return sum(1 for j in bgjobs.list_jobs() if j.status == "running")


def enqueue(*, kind: str, title: str, args: list[str]) -> dict:
    """Queue a job. Starts immediately if a slot is free, else pends."""
    with _LOCK:
        settings = get_settings()
        limit = max(1, settings.web.max_concurrent_jobs)
        if _running_count() < limit:
            job = bgjobs.spawn(kind=kind, title=title, args=args)
            return {"status": "running", "id": job.id, "title": title}
        pending = _load_pending()
        item = PendingJob(id=uuid.uuid4().hex[:8], kind=kind, title=title, args=args)
        pending.append(item)
        _save_pending(pending)
        return {"status": "queued", "id": item.id, "title": title}


def reconcile() -> int:
    """Promote pending jobs into running slots. Returns number started."""
    with _LOCK:
        pending = _load_pending()
        if not pending:
            return 0
        settings = get_settings()
        limit = max(1, settings.web.max_concurrent_jobs)
        started = 0
        while pending and _running_count() < limit:
            item = pending.pop(0)
            try:
                bgjobs.spawn(kind=item.kind, title=item.title, args=item.args)
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


def snapshot() -> list[dict]:
    """Unified list of running/finished jobs + pending, newest first."""
    reconcile()
    out: list[dict] = []
    for j in bgjobs.list_jobs():
        snap = bgjobs.progress_snapshot(j)
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
                "step": snap.step,
                "total_steps": snap.total_steps,
                "step_label": snap.step_label,
                "overall_percent": snap.overall_percent,
                "pending": False,
            }
        )
    for item in _load_pending():
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
