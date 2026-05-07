"""Sync worker tests using a fake AlassRunner + ffmpeg invocation."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from bankai.config import get_settings, reset_settings_cache
from bankai.db import StateRepository, initialize
from bankai.processor.sync import AlassRunner, SyncWorker, _parse_alass_offset
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
