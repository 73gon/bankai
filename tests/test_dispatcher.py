"""Tests for the async dispatcher."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bankai.config import QueueSettings
from bankai.db.state import StateRepository
from bankai.queue.models import Job, JobKind, JobStatus
from bankai.queue.worker import (
    Dispatcher,
    PermanentWorkerError,
    Worker,
    WorkerContext,
    WorkerError,
    run_until_idle,
)


class _RecordingWorker(Worker):
    kind = JobKind.EXTRACT

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[int] = []
        self.concurrent: int = 0
        self.peak: int = 0
        self._delay = delay

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        try:
            assert ctx.job.id is not None
            self.calls.append(ctx.job.id)
            if self._delay:
                await asyncio.sleep(self._delay)
            return {"ok": True, "job": ctx.job.id}
        finally:
            self.concurrent -= 1


class _AlwaysFailWorker(Worker):
    kind = JobKind.SYNC

    def __init__(self, *, permanent: bool = False) -> None:
        self.permanent = permanent
        self.attempts = 0

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        self.attempts += 1
        if self.permanent:
            raise PermanentWorkerError("nope")
        raise WorkerError("transient")


def _settings(**overrides: int) -> QueueSettings:
    base = {
        "search_workers": 1,
        "extract_workers": 2,
        "torrent_workers": 1,
        "sync_workers": 1,
        "remux_workers": 1,
    }
    base.update(overrides)
    return QueueSettings(**base)


async def _start(disp: Dispatcher) -> asyncio.Task[None]:
    return asyncio.create_task(disp.run())


@pytest.mark.asyncio
async def test_dispatcher_runs_queued_jobs(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite3"
    worker = _RecordingWorker()
    disp = Dispatcher(
        db_path=db,
        work_dir=tmp_path,
        workers={JobKind.EXTRACT: worker},
        queue_settings=_settings(),
        poll_interval=0.05,
    )
    with StateRepository(db) as repo:
        for _ in range(3):
            repo.create_job(Job(kind=JobKind.EXTRACT))

    task = await _start(disp)
    try:
        await run_until_idle(disp, timeout=5.0)
    finally:
        await disp.stop()
        await task

    assert sorted(worker.calls) == [1, 2, 3]
    with StateRepository(db) as repo:
        jobs = repo.list_jobs()
    assert all(j.status is JobStatus.DONE for j in jobs)
    assert all(j.result == {"ok": True, "job": j.id} for j in jobs)


@pytest.mark.asyncio
async def test_concurrency_limit_is_honored(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite3"
    worker = _RecordingWorker(delay=0.1)
    disp = Dispatcher(
        db_path=db,
        work_dir=tmp_path,
        workers={JobKind.EXTRACT: worker},
        queue_settings=_settings(extract_workers=2),
        poll_interval=0.02,
    )
    with StateRepository(db) as repo:
        for _ in range(6):
            repo.create_job(Job(kind=JobKind.EXTRACT))

    task = await _start(disp)
    try:
        await run_until_idle(disp, timeout=5.0)
    finally:
        await disp.stop()
        await task

    assert worker.peak <= 2
    assert worker.peak >= 2  # should actually use both slots


@pytest.mark.asyncio
async def test_transient_failure_retries_until_max(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite3"
    worker = _AlwaysFailWorker(permanent=False)
    disp = Dispatcher(
        db_path=db,
        work_dir=tmp_path,
        workers={JobKind.SYNC: worker},
        queue_settings=_settings(),
        poll_interval=0.02,
    )
    with StateRepository(db) as repo:
        repo.create_job(Job(kind=JobKind.SYNC, max_attempts=3))

    task = await _start(disp)
    try:
        await run_until_idle(disp, timeout=5.0)
    finally:
        await disp.stop()
        await task

    assert worker.attempts == 3
    with StateRepository(db) as repo:
        (job,) = repo.list_jobs()
    assert job.status is JobStatus.FAILED
    assert job.attempts == 3


@pytest.mark.asyncio
async def test_permanent_failure_does_not_retry(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite3"
    worker = _AlwaysFailWorker(permanent=True)
    disp = Dispatcher(
        db_path=db,
        work_dir=tmp_path,
        workers={JobKind.SYNC: worker},
        queue_settings=_settings(),
        poll_interval=0.02,
    )
    with StateRepository(db) as repo:
        repo.create_job(Job(kind=JobKind.SYNC, max_attempts=5))

    task = await _start(disp)
    try:
        await run_until_idle(disp, timeout=5.0)
    finally:
        await disp.stop()
        await task

    assert worker.attempts == 1
    with StateRepository(db) as repo:
        (job,) = repo.list_jobs()
    assert job.status is JobStatus.FAILED
