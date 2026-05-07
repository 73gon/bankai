"""Pipeline orchestration tests using fake stage workers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bankai.config import get_settings, reset_settings_cache
from bankai.db import StateRepository, initialize
from bankai.processor.pipeline import PipelineWorker
from bankai.queue.models import Job, JobKind, JobStatus
from bankai.queue.worker import Worker, WorkerContext


class _FakeWorker(Worker):
    def __init__(self, kind: JobKind, result: dict[str, Any]) -> None:
        self.kind = kind
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        self.calls.append(ctx.job.payload)
        return self._result


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANKAI_PATHS__STATE_DB", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("BANKAI_PATHS__WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(tmp_path / "library"))
    reset_settings_cache()
    _ = get_settings()


async def test_pipeline_chains_all_four_stages(tmp_path: Path) -> None:
    extract = _FakeWorker(JobKind.EXTRACT, {"path": "/tmp/audio.aac"})
    torrent = _FakeWorker(JobKind.TORRENT, {"path": "/tmp/video.mkv"})
    sync = _FakeWorker(JobKind.SYNC, {"path": "/tmp/synced.aac"})
    remux = _FakeWorker(JobKind.REMUX, {"path": "/tmp/final.mkv"})

    pipeline = PipelineWorker(extractor=extract, torrent=torrent, sync=sync, remux=remux)  # type: ignore[arg-type]

    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)
    job = repo.create_job(
        Job(
            kind=JobKind.PIPELINE,
            status=JobStatus.RUNNING,
            payload={
                "query": "Inception 2010",
                "stream_url": "https://x/y",
                "stream_hint": "ytdlp",
                "stream_site": "filmpalast",
                "kind": "movie",
                "out": str(tmp_path / "out.mkv"),
            },
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )

    result = await pipeline.run(ctx)
    assert result is not None
    assert result["final_path"] == "/tmp/final.mkv"

    # Each stage was called exactly once with the expected payload chain.
    assert extract.calls[0]["url"] == "https://x/y"
    assert torrent.calls[0]["query"] == "Inception 2010"
    assert sync.calls[0]["audio"] == "/tmp/audio.aac"
    assert sync.calls[0]["reference"] == "/tmp/video.mkv"
    assert remux.calls[0]["video"] == "/tmp/video.mkv"
    assert remux.calls[0]["audio"] == "/tmp/synced.aac"
