"""Remux worker / mkvmerge wrapper tests (no real binary needed)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bankai.config import get_settings, reset_settings_cache
from bankai.db import StateRepository, initialize
from bankai.processor.remux import RemuxWorker, build_mkvmerge_command, verify_output
from bankai.queue.models import Job, JobKind, JobStatus
from bankai.queue.worker import WorkerContext, WorkerError


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANKAI_PATHS__STATE_DB", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("BANKAI_PATHS__WORK_DIR", str(tmp_path / "work"))
    reset_settings_cache()
    _ = get_settings()


async def test_build_mkvmerge_command_shape() -> None:
    cmd = await build_mkvmerge_command(
        video=Path("v.mkv"),
        audio=Path("a.aac"),
        out=Path("out.mkv"),
        language="ger",
        track_name="German (Web-DL)",
        default_track=False,
    )
    assert cmd[0] == "mkvmerge"
    assert "--output" in cmd
    assert "out.mkv" in cmd
    # Track-name applies to source 1 (track 0 within source 1).
    assert "0:German (Web-DL)" in cmd
    assert "0:ger" in cmd
    assert "0:0" in cmd  # default-track-flag


async def test_build_mkvmerge_command_applies_audio_delay() -> None:
    cmd = await build_mkvmerge_command(
        video=Path("v.mkv"),
        audio=Path("a.aac"),
        out=Path("out.mkv"),
        language="ger",
        track_name="German (Web-DL)",
        default_track=False,
        audio_delay_ms=-1200,
    )
    assert "--sync" in cmd
    assert "0:-1200" in cmd
    # The delay flag precedes the dub audio source file.
    assert cmd.index("--sync") < cmd.index("a.aac")


async def test_build_mkvmerge_command_omits_sync_when_zero_delay() -> None:
    cmd = await build_mkvmerge_command(
        video=Path("v.mkv"),
        audio=Path("a.aac"),
        out=Path("out.mkv"),
        language="ger",
        track_name="German (Web-DL)",
        default_track=False,
        audio_delay_ms=0,
    )
    assert "--sync" not in cmd


async def test_remux_worker_invokes_mkvmerge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "v.mkv"
    audio = tmp_path / "a.aac"
    out = tmp_path / "out.mkv"
    video.write_bytes(b"VIDEO")
    audio.write_bytes(b"AUDIO")

    captured: list[list[str]] = []

    async def fake_exec(*cmd: str, **_kw: object) -> object:
        captured.append(list(cmd))

        class _P:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                # First call = mkvmerge real run â†’ write output file.
                # Second call = ``mkvmerge -J`` for verification.
                if "-J" in cmd:
                    return (
                        b'{"tracks":[{"id":0,"type":"video"},{"id":1,"type":"audio"},{"id":2,"type":"audio"}]}',
                        b"",
                    )
                mux_out = Path(cmd[cmd.index("--output") + 1])
                mux_out.write_bytes(b"MUXED")
                return b"", b""

        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)
    job = repo.create_job(
        Job(
            kind=JobKind.REMUX,
            status=JobStatus.RUNNING,
            payload={
                "video": str(video),
                "audio": str(audio),
                "out": str(out),
                "language": "ger",
                "track_name": "German",
            },
        )
    )
    ctx = WorkerContext(job=job, repo=repo, work_dir=tmp_path / "work", cancel_token=asyncio.Event())

    worker = RemuxWorker()
    result = await worker.run(ctx)
    assert result is not None
    assert Path(result["path"]) == out
    assert out.exists()
    assert len(captured) == 4  # probe video + probe audio + mkvmerge run + verify


async def test_remux_worker_preserves_existing_output_when_mux_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "v.mkv"
    audio = tmp_path / "a.aac"
    out = tmp_path / "out.mkv"
    video.write_bytes(b"VIDEO")
    audio.write_bytes(b"AUDIO")
    out.write_bytes(b"EXISTING")

    async def fake_exec(*cmd: str, **_kw: object) -> object:
        class _P:
            returncode = 0 if "-J" in cmd else 2

            async def communicate(self) -> tuple[bytes, bytes]:
                if "-J" in cmd:
                    return (
                        b'{"tracks":[{"id":0,"type":"video"},{"id":1,"type":"audio"}]}',
                        b"",
                    )
                return b"", b"mux failed"

        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    settings = get_settings()
    initialize(settings.paths.state_db)
    repo = StateRepository(settings.paths.state_db)
    job = repo.create_job(
        Job(
            kind=JobKind.REMUX,
            status=JobStatus.RUNNING,
            payload={"video": str(video), "audio": str(audio), "out": str(out)},
        )
    )
    ctx = WorkerContext(
        job=job,
        repo=repo,
        work_dir=tmp_path / "work",
        cancel_token=asyncio.Event(),
    )

    with pytest.raises(WorkerError, match="mkvmerge failed"):
        await RemuxWorker().run(ctx)

    assert out.read_bytes() == b"EXISTING"
    assert list(tmp_path.glob("*.partial.*.mkv")) == []


async def test_verify_output_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*_cmd: str, **_kw: object) -> object:
        class _P:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b'{"tracks":[{"id":0}]}', b""

        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    data = await verify_output(Path("x.mkv"))
    assert data["tracks"][0]["id"] == 0
