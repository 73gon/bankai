"""Base ``Worker`` and the async ``Dispatcher`` that orchestrates them.

Concurrency model
-----------------

* The dispatcher tracks per-kind in-flight counts. Limits come from
  ``QueueSettings``.
* A polling loop wakes periodically (and on demand via ``notify``) to claim
  queued jobs from SQLite for whichever kinds currently have free slots.
* Each worker invocation runs in its own task, so slow stages don't block
  the polling loop.
* Workers are async; CPU-bound or blocking subprocess work should use
  ``asyncio.to_thread`` or ``asyncio.create_subprocess_exec``.

Failure handling
----------------

* Workers raise on failure. The dispatcher asks the repository to either
  re-queue (``attempts < max_attempts``) or mark failed.
* :class:`PermanentWorkerError` skips retries.
* Cancellation is cooperative â€” workers should periodically check
  ``ctx.cancel_token``.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bankai.config import QueueSettings, get_settings
from bankai.db.state import StateRepository
from bankai.logging import get_logger
from bankai.queue.models import Job, JobKind, JobStatus

log = get_logger(__name__)


# ---- worker base -----------------------------------------------------------


@dataclass
class WorkerContext:
    """Per-invocation context handed to a worker.

    Workers receive a fresh ``StateRepository`` so they can record artifacts
    and update related rows without sharing the dispatcher's connection
    (sqlite3 connections are not safe to share across threads).
    """

    job: Job
    repo: StateRepository
    work_dir: Path
    cancel_token: asyncio.Event


class WorkerError(Exception):
    """Base class for worker failures."""

    retry: bool = True


class PermanentWorkerError(WorkerError):
    """Failure that should not be retried (e.g. invalid config, 404)."""

    retry = False


class Worker(abc.ABC):
    """Base class for all pipeline workers.

    Subclasses set :attr:`kind` and implement :meth:`run`. The dispatcher
    handles claim/complete/fail bookkeeping.
    """

    kind: JobKind

    @abc.abstractmethod
    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        """Execute the job. Return optional ``result`` JSON. Raise on failure."""


# ---- dispatcher ------------------------------------------------------------


class Dispatcher:
    """Async worker pool driven by SQLite job rows."""

    def __init__(
        self,
        *,
        db_path: Path,
        work_dir: Path,
        workers: Mapping[JobKind, Worker],
        queue_settings: QueueSettings | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._db_path = db_path
        self._work_dir = work_dir
        self._workers = dict(workers)
        self._settings = queue_settings or get_settings().queue
        self._poll_interval = poll_interval
        self._limits: dict[JobKind, int] = {
            kind: self._concurrency_for(kind) for kind in self._workers
        }
        self._in_flight: dict[JobKind, int] = dict.fromkeys(self._workers, 0)
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._cancel_tokens: dict[int, asyncio.Event] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- public API --------------------------------------------------------

    def notify(self) -> None:
        """Hint that new jobs may be available; wake the polling loop."""
        self._wake.set()

    def request_cancel(self, job_id: int) -> None:
        token = self._cancel_tokens.get(job_id)
        if token is not None:
            token.set()

    async def run(self) -> None:
        """Main loop. Returns when :meth:`stop` is called and tasks drain."""
        log.info(
            "dispatcher starting (workers=%s)",
            sorted(k.value for k in self._workers),
        )
        try:
            while not self._stopping.is_set():
                await self._tick()
                await self._wait_for_work()
        finally:
            log.info("dispatcher draining %d in-flight tasks", len(self._tasks))
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            log.info("dispatcher stopped")

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    # ---- internals ---------------------------------------------------------

    def _concurrency_for(self, kind: JobKind) -> int:
        mapping = {
            JobKind.SEARCH: self._settings.search_workers,
            JobKind.TORRENT: self._settings.torrent_workers,
            JobKind.EXTRACT: self._settings.extract_workers,
            JobKind.SYNC: self._settings.sync_workers,
            JobKind.REMUX: self._settings.remux_workers,
            JobKind.PIPELINE: 4,  # cheap orchestration jobs
        }
        return mapping.get(kind, 1)

    def _available_kinds(self) -> list[JobKind]:
        return [k for k, n in self._in_flight.items() if n < self._limits[k]]

    async def _tick(self) -> None:
        """Try to dispatch as many jobs as our limits allow."""
        with StateRepository(self._db_path) as repo:
            while True:
                available = self._available_kinds()
                if not available:
                    return
                job = repo.claim_next_job(available)
                if job is None:
                    return
                self._spawn(job)

    async def _wait_for_work(self) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)
        self._wake.clear()

    def _spawn(self, job: Job) -> None:
        worker = self._workers.get(job.kind)
        if worker is None:
            log.error("no worker registered for kind=%s job=%s", job.kind.value, job.id)
            return
        self._in_flight[job.kind] += 1
        task = asyncio.create_task(self._invoke(job, worker), name=f"job-{job.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _invoke(self, job: Job, worker: Worker) -> None:
        assert job.id is not None
        cancel = asyncio.Event()
        self._cancel_tokens[job.id] = cancel
        repo = StateRepository(self._db_path)
        try:
            ctx = WorkerContext(
                job=job,
                repo=repo,
                work_dir=self._work_dir,
                cancel_token=cancel,
            )
            log.info(
                "[job %s] %s starting (attempt %d)",
                job.id,
                job.kind.value,
                job.attempts,
            )
            try:
                result = await worker.run(ctx)
            except PermanentWorkerError as exc:
                log.error("[job %s] permanent failure: %s", job.id, exc)
                repo.fail_job(job.id, str(exc), retry=False)
            except WorkerError as exc:
                status = repo.fail_job(job.id, str(exc), retry=exc.retry)
                log.warning("[job %s] failed â†’ %s: %s", job.id, status.value, exc)
            except asyncio.CancelledError:
                repo.fail_job(job.id, "cancelled", retry=False)
                raise
            except Exception as exc:
                log.exception("[job %s] unhandled error", job.id)
                repo.fail_job(job.id, repr(exc), retry=True)
            else:
                repo.complete_job(job.id, result)
                log.info("[job %s] done", job.id)
        finally:
            self._cancel_tokens.pop(job.id, None)
            with contextlib.suppress(Exception):
                repo.close()
            self._in_flight[job.kind] -= 1
            self._wake.set()


# ---- convenience helpers ---------------------------------------------------


async def run_until_idle(dispatcher: Dispatcher, timeout: float = 30.0) -> None:
    """Wait until the dispatcher has no in-flight or queued jobs.

    Useful in tests and CLI ``run --foreground`` flows. Raises on timeout.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if dispatcher.in_flight == 0:
            with StateRepository(dispatcher._db_path) as repo:
                queued = repo.list_jobs(status=JobStatus.QUEUED, limit=1)
                running = repo.list_jobs(status=JobStatus.RUNNING, limit=1)
            if not queued and not running:
                return
        if loop.time() > deadline:
            raise TimeoutError("dispatcher did not become idle within timeout")
        await asyncio.sleep(0.05)
