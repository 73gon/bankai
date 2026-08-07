"""Sync worker tests using a fake AlassRunner + ffmpeg invocation."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from bankai.config import get_settings, reset_settings_cache
from bankai.db import StateRepository, initialize
from bankai.processor.sync import (
    AlassRunner,
    IncompleteAudioError,
    SyncResult,
    SyncWorker,
    _classify_ratio,
    _parse_alass_offset,
)
from bankai.queue.models import Job, JobKind, JobStatus
from bankai.queue.worker import WorkerContext


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANKAI_PATHS__STATE_DB", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("BANKAI_PATHS__WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("BANKAI_SYNC__MODE", "skip")
    reset_settings_cache()
    _ = get_settings()


def test_parse_alass_offset_positive() -> None:
    assert _parse_alass_offset("Detected offset: 1.234s\n") == pytest.approx(1.234)


def test_parse_alass_offset_negative() -> None:
    assert _parse_alass_offset("Offset of -2.5s detected") == pytest.approx(-2.5)


def test_parse_alass_offset_failure() -> None:
    from bankai.processor.sync import SyncError

    with pytest.raises(SyncError):
        _parse_alass_offset("nothing useful here")


def test_classify_short_pal_speed_audio_for_slowdown() -> None:
    assert _classify_ratio(2313.636, 2415.584) == "pal_to_ndf"


def test_classify_long_film_speed_audio_for_speedup() -> None:
    assert _classify_ratio(2415.584, 2313.636) == "ndf_to_pal"


async def test_sync_worker_skip_mode(tmp_path: Path) -> None:
    audio = tmp_path / "in.aac"
    audio.write_bytes(b"PAYLOAD")
    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)
    job = repo.create_job(
        Job(
            kind=JobKind.SYNC,
            status=JobStatus.RUNNING,
            payload={"audio": str(audio), "reference": str(audio)},
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )
    worker = SyncWorker()
    result = await worker.run(ctx)
    assert result is not None
    out_path = Path(result["path"])
    assert out_path.exists()
    assert out_path.read_bytes() == b"PAYLOAD"
    assert result["method"] == "skip"
    assert result["offset_seconds"] == 0.0


async def test_sync_worker_manual_offset_invokes_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "in.aac"
    audio.write_bytes(b"PAYLOAD")
    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)

    captured_cmd: list[str] = []

    async def fake_exec(*cmd: str, **_kw: object) -> object:
        captured_cmd.extend(cmd)

        class _P:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                # Simulate ffmpeg producing the output file by copying input.
                src = cmd[cmd.index("-i") + 1]
                dst = cmd[-1]
                shutil.copyfile(src, dst)
                return b"", b""

        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    job = repo.create_job(
        Job(
            kind=JobKind.SYNC,
            status=JobStatus.RUNNING,
            payload={
                "audio": str(audio),
                "reference": str(audio),
                "offset_seconds": -1.5,
            },
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )
    worker = SyncWorker()
    result = await worker.run(ctx)
    assert result is not None
    assert result["method"] == "manual"
    assert "-itsoffset" in captured_cmd
    assert "-1.500000" in captured_cmd


async def test_sync_worker_uses_audio_over_video_as_atempo_factor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "in.aac"
    reference = tmp_path / "reference.mkv"
    audio.write_bytes(b"AUDIO")
    reference.write_bytes(b"VIDEO")
    monkeypatch.setenv("BANKAI_SYNC__MODE", "auto")
    reset_settings_cache()
    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)

    async def fake_duration(path: Path) -> float:
        return 2313.636 if path == audio else 2415.584

    captured: list[float] = []

    async def fake_apply_tempo(src: Path, dst: Path, tempo: float) -> SyncResult:
        captured.append(tempo)
        shutil.copyfile(src, dst)
        return SyncResult(path=dst, offset_seconds=0.0, method="atempo", tempo=tempo)

    monkeypatch.setattr("bankai.processor.sync._ffprobe_duration", fake_duration)
    worker = SyncWorker()
    monkeypatch.setattr(worker, "_apply_tempo", fake_apply_tempo)
    job = repo.create_job(
        Job(
            kind=JobKind.SYNC,
            status=JobStatus.RUNNING,
            payload={"audio": str(audio), "reference": str(reference)},
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )

    result = await worker.run(ctx)

    assert result is not None
    assert result["method"] == "atempo"
    assert result["tempo"] == pytest.approx(2313.636 / 2415.584)
    assert captured == [pytest.approx(2313.636 / 2415.584)]


async def test_sync_worker_preserves_different_cut_when_frame_rates_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "in.aac"
    reference = tmp_path / "reference.mkv"
    audio.write_bytes(b"AUDIO")
    reference.write_bytes(b"VIDEO")
    monkeypatch.setenv("BANKAI_SYNC__MODE", "auto")
    reset_settings_cache()
    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)

    async def fake_duration(path: Path) -> float:
        return 2313.0 if path == audio else 2415.0

    monkeypatch.setattr("bankai.processor.sync._ffprobe_duration", fake_duration)
    job = repo.create_job(
        Job(
            kind=JobKind.SYNC,
            status=JobStatus.RUNNING,
            payload={
                "audio": str(audio),
                "reference": str(reference),
                "source_fps": 24.0,
                "reference_fps": 24.0,
            },
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )

    result = await SyncWorker().run(ctx)

    assert result is not None
    assert result["method"] == "passthrough"
    assert result["tempo"] == pytest.approx(1.0)


async def test_sync_worker_rejects_materially_truncated_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "partial.aac"
    reference = tmp_path / "reference.mkv"
    audio.write_bytes(b"AUDIO")
    reference.write_bytes(b"VIDEO")
    monkeypatch.setenv("BANKAI_SYNC__MODE", "auto")
    reset_settings_cache()
    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)

    async def fake_duration(path: Path) -> float:
        return 3_600.0 if path == audio else 5_400.0

    monkeypatch.setattr("bankai.processor.sync._ffprobe_duration", fake_duration)
    job = repo.create_job(
        Job(
            kind=JobKind.SYNC,
            status=JobStatus.RUNNING,
            payload={"audio": str(audio), "reference": str(reference)},
        )
    )
    ctx = WorkerContext(
        job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event()
    )

    with pytest.raises(IncompleteAudioError, match=r"1800\.0s missing"):
        await SyncWorker().run(ctx)


async def test_alass_runner_parses_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*_cmd: str, **_kw: object) -> object:
        class _P:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"Detected offset: 0.847s\n", b""

        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    runner = AlassRunner(binary="alass")
    offset = await runner.detect_offset(reference=Path("a"), target=Path("b"))
    assert offset == pytest.approx(0.847)
