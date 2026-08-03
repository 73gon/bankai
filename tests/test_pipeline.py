"""Pipeline orchestration tests using fake stage workers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bankai.config import get_settings, reset_settings_cache
from bankai.db import StateRepository, initialize
from bankai.processor.pipeline import (
    PipelineWorker,
    _account_for_applied_tempo,
    _derive_content_fps,
    _extract_attempt_payloads,
    _resolve_episode_fallbacks,
)
from bankai.processor.sync import PlaceholderAudioError
from bankai.queue.models import Job, JobKind, JobStatus
from bankai.queue.worker import Worker, WorkerContext, WorkerError
from bankai.scraper.base import EpisodeRef, StreamHandle


def test_content_fps_ignores_duplicate_padded_nominal_rate() -> None:
    assert _derive_content_fps(
        reference_fps=23.976,
        drift_ratio=1.0,
        confidence=0.9,
        min_confidence=0.6,
    ) == pytest.approx(23.976)


def test_content_fps_derives_pal_cadence_from_visual_timing() -> None:
    assert _derive_content_fps(
        reference_fps=23.976,
        drift_ratio=23.976 / 25.0,
        confidence=0.9,
        min_confidence=0.6,
    ) == pytest.approx(25.0)


def test_content_fps_is_omitted_when_visual_match_is_uncertain() -> None:
    assert _derive_content_fps(
        reference_fps=24.0,
        drift_ratio=1.0,
        confidence=0.4,
        min_confidence=0.6,
    ) is None


class _FakeWorker(Worker):
    def __init__(self, kind: JobKind, result: dict[str, Any]) -> None:
        self.kind = kind
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        self.calls.append(ctx.job.payload)
        return self._result


class _SequenceWorker(Worker):
    def __init__(self, kind: JobKind, results: list[dict[str, Any]]) -> None:
        self.kind = kind
        self._results = results
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        self.calls.append(ctx.job.payload)
        return self._results.pop(0)


class _FailThenSucceedWorker(Worker):
    kind = JobKind.EXTRACT

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        self.calls.append(ctx.job.payload)
        if len(self.calls) == 1:
            raise WorkerError("first source failed")
        return {"path": "/tmp/audio.aac"}


class _PlaceholderOnceSync(Worker):
    kind = JobKind.SYNC

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        self.calls.append(ctx.job.payload)
        if len(self.calls) == 1:
            raise PlaceholderAudioError(audio_duration=4.7, video_duration=11_686.8)
        return {"path": "/tmp/synced.aac"}


def test_account_for_applied_tempo_translates_visual_timeline() -> None:
    meta: dict[str, Any] = {
        "delay_ms": 5125,
        "drift_ratio": 0.9578,
    }

    _account_for_applied_tempo(meta, 0.9578)

    assert meta["delay_ms"] == 5351
    assert meta["drift_ratio"] == pytest.approx(1.0)
    assert meta["applied_tempo"] == pytest.approx(0.9578)


def test_account_for_applied_tempo_leaves_passthrough_measurements_unchanged() -> None:
    meta: dict[str, Any] = {"delay_ms": -250, "drift_ratio": 1.001}

    _account_for_applied_tempo(meta, 1.0)

    assert meta == {"delay_ms": -250, "drift_ratio": 1.001}


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


async def test_pipeline_retries_extract_when_sync_detects_placeholder(tmp_path: Path) -> None:
    extract = _SequenceWorker(
        JobKind.EXTRACT,
        [
            {"path": "/tmp/placeholder.aac"},
            {"path": "/tmp/full.aac"},
        ],
    )
    torrent = _FakeWorker(JobKind.TORRENT, {"path": "/tmp/video.mkv"})
    sync = _PlaceholderOnceSync()
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
    assert [c["hint"] for c in extract.calls] == ["ytdlp", "playwright"]
    assert [c["audio"] for c in sync.calls] == ["/tmp/placeholder.aac", "/tmp/full.aac"]


async def test_pipeline_retries_next_extract_attempt_when_hoster_fails(
    tmp_path: Path,
) -> None:
    extract = _FailThenSucceedWorker()
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
                "query": "Arcane S01E01",
                "stream_url": "https://filmpalast.to/stream/arcane-s01e01",
                "stream_hint": "ytdlp",
                "stream_site": "unknown",
                "kind": "episode",
                "out": str(tmp_path / "out.mkv"),
            },
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )

    result = await pipeline.run(ctx)

    assert result is not None
    assert [call["hint"] for call in extract.calls] == ["ytdlp", "playwright"]


async def test_burningseries_episode_fallback_resolves_exact_filmpalast_voe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFilmpalast:
        async def list_season(self, show: str, season: int) -> list[EpisodeRef]:
            assert show == "Arcane - League of Legends"
            assert season == 2
            return [
                EpisodeRef(
                    site="filmpalast",
                    series_title=show,
                    season=2,
                    episode=1,
                    title="Episode 1",
                    url="https://filmpalast.invalid/stream/arcane-s02e01",
                ),
                EpisodeRef(
                    site="filmpalast",
                    series_title=show,
                    season=2,
                    episode=2,
                    title="Episode 2",
                    url="https://filmpalast.invalid/stream/arcane-s02e02",
                ),
            ]

        async def resolve_stream(self, url: str) -> StreamHandle:
            assert url.endswith("s02e02")
            return StreamHandle(
                site="filmpalast",
                url="https://voe.sx/direct-arcane-s02e02",
                hint="ytdlp",
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "bankai.scraper.get_backend",
        lambda site_id: FakeFilmpalast if site_id == "filmpalast" else None,
    )

    handles = await _resolve_episode_fallbacks(
        {
            "kind": "episode",
            "series_title": "Arcane - League of Legends",
            "season": 2,
            "episode": 2,
        },
        site_id="filmpalast",
    )

    assert len(handles) == 1
    assert handles[0].url == "https://voe.sx/direct-arcane-s02e02"
    assert handles[0].hint == "ytdlp"


async def test_pipeline_prefers_exact_filmpalast_voe_for_burningseries_episode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    burningseries_wrapper = (
        "https://burningseries.ac/serie/Arcane-League-of-Legends/2/2/de"
    )
    filmpalast_wrapper = "https://filmpalast.invalid/stream/arcane-s02e02"
    voe_url = "https://voe.sx/direct-arcane-s02e02"

    class FakeBurningSeries:
        async def resolve_all_streams(self, url: str) -> list[StreamHandle]:
            assert url == burningseries_wrapper
            return [StreamHandle(site="burningseries", url=f"{url}/VOE", hint="playwright")]

        async def aclose(self) -> None:
            return None

    class FakeFilmpalast:
        async def list_season(self, show: str, season: int) -> list[EpisodeRef]:
            assert show == "Arcane - League of Legends"
            assert season == 2
            return [
                EpisodeRef(
                    site="filmpalast",
                    series_title=show,
                    season=2,
                    episode=2,
                    title="Episode 2",
                    url=filmpalast_wrapper,
                )
            ]

        async def resolve_all_streams(self, url: str) -> list[StreamHandle]:
            assert url == filmpalast_wrapper
            return [
                StreamHandle(site="filmpalast", url=voe_url, hint="ytdlp"),
                StreamHandle(
                    site="filmpalast",
                    url="https://streamtape.invalid/direct-arcane-s02e02",
                    hint="ytdlp",
                ),
            ]

        async def aclose(self) -> None:
            return None

    backends = {
        "burningseries": FakeBurningSeries,
        "filmpalast": FakeFilmpalast,
    }
    monkeypatch.setattr("bankai.scraper.get_backend", lambda site_id: backends[site_id])
    provenance: list[tuple[str, str | None]] = []
    monkeypatch.setenv("BANKAI_BG_JOB_ID", "arcane-job")
    monkeypatch.setattr(
        "bankai.cli.bgjobs.set_provenance",
        lambda job_id, **values: provenance.append(
            (job_id, values.get("german_source_url"))
        )
        or True,
    )

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
                "query": "Arcane - League of Legends S02E02",
                "series_title": "Arcane - League of Legends",
                "season": 2,
                "episode": 2,
                "stream_url": burningseries_wrapper,
                "stream_hint": "playwright",
                "stream_site": "burningseries",
                "kind": "episode",
                "out": str(tmp_path / "out.mkv"),
            },
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )

    result = await pipeline.run(ctx)

    assert result is not None
    assert extract.calls[0]["url"] == voe_url
    assert extract.calls[0]["hint"] == "ytdlp"
    assert result["extract"]["source_url"] == voe_url
    assert provenance == [("arcane-job", voe_url)]


def test_filmpalast_extract_attempts_do_not_repeat_wrapper() -> None:
    attempts = _extract_attempt_payloads(
        stream_url="https://st-us-01.vidsonic.net/e/current",
        stream_hint="playwright",
        stream_site="filmpalast",
        wrapper_url="https://filmpalast.to/stream/example",
        mirror_urls=["https://voe.sx/backup"],
    )

    assert all(attempt["url"] != "https://filmpalast.to/stream/example" for attempt in attempts)
    assert attempts[0]["url"] == "https://st-us-01.vidsonic.net/e/current"
    assert [(attempt["url"], attempt["hint"], attempt["fallback_only"]) for attempt in attempts] == [
        ("https://st-us-01.vidsonic.net/e/current", "ytdlp", False),
        ("https://st-us-01.vidsonic.net/e/current", "playwright", True),
        ("https://voe.sx/backup", "ytdlp", False),
        ("https://voe.sx/backup", "playwright", True),
    ]


@pytest.mark.asyncio
async def test_extract_attempts_skip_repeating_a_completed_browser_failure(
    tmp_path: Path,
) -> None:
    class BrowserFailureThenSuccess(Worker):
        kind = JobKind.EXTRACT

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
            self.calls.append(ctx.job.payload)
            if "vidsonic" in str(ctx.job.payload["url"]):
                raise WorkerError(
                    "playwright fallback failed: no media URL captured at vidsonic"
                )
            return {"path": "/tmp/audio.aac"}

    worker = BrowserFailureThenSuccess()
    pipeline = PipelineWorker(extractor=worker)  # type: ignore[arg-type]
    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)
    job = repo.create_job(
        Job(
            kind=JobKind.PIPELINE,
            status=JobStatus.RUNNING,
            payload={"query": "Example", "stream_url": "https://example.test"},
        )
    )
    ctx = WorkerContext(
        job=job,
        repo=repo,
        work_dir=tmp_path / "work",
        cancel_token=asyncio.Event(),
    )
    attempts = _extract_attempt_payloads(
        stream_url="https://vidsonic.net/e/dead",
        stream_hint="playwright",
        stream_site="filmpalast",
        mirror_urls=["https://voe.sx/working"],
    )

    index, result = await pipeline._run_extract_attempts(ctx, attempts, 0)

    assert index == 2
    assert result["source_url"] == "https://voe.sx/working"
    assert [(call["url"], call["hint"]) for call in worker.calls] == [
        ("https://vidsonic.net/e/dead", "ytdlp"),
        ("https://voe.sx/working", "ytdlp"),
    ]
