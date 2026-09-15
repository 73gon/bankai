"""Tests for the lightweight interactive-menu background job ledger."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bankai.cli import bgjobs


def test_failed_job_with_final_path_is_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="abc12345",
        kind="movie",
        title="Zootopia",
        args=["run", "Zootopia 2016"],
        started_at=time.time(),
        status="failed",
    )
    job.save()
    job.log_path.write_text(
        'warning: transient error: ignored\n  "final_path": "/library/Zootopia.mkv",\n',
        encoding="utf-8",
    )

    (refreshed,) = bgjobs.list_jobs()

    assert refreshed.status == "done"
    assert refreshed.final_path == "/library/Zootopia.mkv"


def test_source_video_fps_is_recovered_from_job_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="fps12345",
        kind="movie",
        title="The Princess and the Frog",
        args=["run", "The Princess and the Frog 2009"],
        started_at=time.time(),
        status="done",
    )
    job.save()
    job.log_path.write_text(
        "[23:07:03] INFO [visual-sync] source nominal frame rate 23.000fps\n",
        encoding="utf-8",
    )

    assert bgjobs.source_video_fps(job) == pytest.approx(23.0)


def test_running_job_killed_without_final_path_is_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running job whose supervisor vanished mid-run (e.g. the web service
    restarted) must be reported as failed, not a phantom "done"."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="cars2bug",
        kind="movie",
        title="Cars 2",
        args=["run", "Cars 2"],
        started_at=time.time(),
        status="running",
        pid=2**30,  # a pid that is not alive
    )
    job.save()
    # Log ends mid-download with no final_path and no traceback.
    job.log_path.write_text(
        "BANKAI_PROGRESS stage=torrent pct=69.3 state=downloading\n",
        encoding="utf-8",
    )

    (refreshed,) = bgjobs.list_jobs()

    assert refreshed.status == "failed"
    assert refreshed.final_path is None


def test_clear_jobs_removes_finished_background_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    done = bgjobs.BgJob(
        id="done1234",
        kind="movie",
        title="Done",
        args=["run", "Done"],
        started_at=time.time(),
        status="done",
    )
    running = bgjobs.BgJob(
        id="run12345",
        kind="movie",
        title="Running",
        args=["run", "Running"],
        started_at=time.time(),
        status="running",
    )
    done.save()
    running.save()

    assert bgjobs.clear_jobs(statuses={"done"}) == 1
    assert not done.dir.exists()
    assert running.dir.exists()


def test_delete_hides_job_even_when_windows_cleanup_is_temporarily_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="locked12",
        kind="movie",
        title="Locked",
        args=["run", "Locked"],
        started_at=time.time(),
        status="failed",
    )
    job.save()
    real_rmtree = bgjobs.shutil.rmtree

    def blocked(_path: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("temporarily locked")

    monkeypatch.setattr(bgjobs.shutil, "rmtree", blocked)
    assert job.delete() is True
    assert not job.dir.exists()

    monkeypatch.setattr(bgjobs.shutil, "rmtree", real_rmtree)
    assert bgjobs.list_jobs() == []


def test_stopped_job_can_resume_in_same_ledger_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="resume12",
        kind="movie",
        title="Movie",
        args=["run", "Movie 2024"],
        started_at=1.0,
        status="stopped",
        finished_at=2.0,
    )
    monkeypatch.setattr(bgjobs, "_launch", lambda value: value)

    resumed = bgjobs.resume(job)

    assert resumed.id == "resume12"
    assert resumed.status == "running"
    assert resumed.started_at == 1.0
    assert resumed.updated_at is not None and resumed.updated_at > resumed.started_at
    assert resumed.finished_at is None
    assert resumed.args == ["run", "Movie 2024"]


def test_job_provenance_is_persisted_with_original_stream_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="source12",
        kind="show",
        title="Arcane S02E01",
        args=["run", "Arcane S02E01", "--url", "https://voe.sx/german"],
        started_at=1.0,
    )
    job.german_source_url = bgjobs.argument_value(job.args, "--url")
    job.save()

    assert bgjobs.set_provenance(
        job.id,
        torrent_source_url="https://indexer.test/torrent/1",
        torrent_source_title="Arcane.S02E01.1080p",
    )
    saved = bgjobs.get_job(job.id)

    assert saved is not None
    assert saved.german_source_url == "https://voe.sx/german"
    assert saved.torrent_source_url == "https://indexer.test/torrent/1"
    assert saved.torrent_source_title == "Arcane.S02E01.1080p"


def test_progress_snapshot_parses_pipeline_download_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="prog1234",
        kind="movie",
        title="Cars",
        args=["run", "Cars 2006"],
        started_at=time.time(),
        status="running",
    )
    job.save()
    job.log_path.write_text(
        "\n".join(
            [
                'BANKAI_STAGE step=2 total=4 key=torrent label="Download HQ video"',
                "BANKAI_PROGRESS stage=stream pct=100.0 status=finished",
                "BANKAI_PROGRESS stage=torrent pct=42.5 speed=1048576 eta=120 state=downloading",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = bgjobs.progress_snapshot(job)

    assert snapshot.step == 2
    assert snapshot.step_label == "Download HQ video"
    assert snapshot.overall_percent == pytest.approx(35.625)
    assert snapshot.parts["stream"].percent == 100.0
    assert snapshot.parts["torrent"].percent == 42.5
    assert snapshot.parts["torrent"].speed == 1_048_576


def test_progress_snapshot_parses_transfer_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    job = bgjobs.BgJob(
        id="tran1234",
        kind="transfer",
        title="Transfer library",
        args=["transfer-run", "/library"],
        started_at=time.time(),
        status="running",
    )
    job.save()
    job.log_path.write_text(
        "BANKAI_PROGRESS stage=transfer pct=75.0 status=running\n",
        encoding="utf-8",
    )

    snapshot = bgjobs.progress_snapshot(job)

    assert snapshot.step_label == "Transfer files"
    assert snapshot.overall_percent == 75.0
    assert snapshot.parts["transfer"].percent == 75.0


def test_pid_access_denied_still_means_process_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(_pid: int, _signal: int) -> None:
        raise PermissionError("access denied")

    monkeypatch.setattr(bgjobs.os, "kill", denied)
    monkeypatch.setattr(bgjobs, "_windows_pid_alive", lambda pid: denied(pid, 0))

    assert bgjobs._pid_alive(1234) is True


def test_windows_liveness_never_sends_a_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    if bgjobs.os.name != "nt":
        pytest.skip("Windows process query")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        monkeypatch.setattr(
            bgjobs.os,
            "kill",
            lambda *args: pytest.fail("Liveness must not send signals on Windows"),
        )
        assert bgjobs._pid_alive(process.pid)
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=10)
    assert not bgjobs._pid_alive(process.pid)


def test_windows_launch_preserves_environment_outside_service_tree(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("BANKAI_TEST_SECRET", "not-on-command-line")
    monkeypatch.setattr(bgjobs.sys, "platform", "win32")
    calls = []

    def launch(command, cwd):
        calls.append(command)
        payload = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        assert payload["env"]["BANKAI_TEST_SECRET"] == "not-on-command-line"
        assert payload["args"] == ["run", "Movie"]
        assert "not-on-command-line" not in " ".join(command)
        assert bgjobs._load_job(payload["id"]).restart_safe
        return 12345

    monkeypatch.setattr(bgjobs, "launch_windows_detached", launch)
    job = bgjobs.spawn(kind="movie", title="Movie", args=["run", "Movie"])
    assert job.pid == 12345
    assert job.restart_safe
    assert "--launch-job" in calls[0]
    assert bgjobs._load_job(job.id).pid == 12345


def test_windows_launch_failure_is_not_a_phantom_running_job(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(bgjobs.sys, "platform", "win32")

    def fail(*args):
        raise RuntimeError("WMI unavailable")

    monkeypatch.setattr(bgjobs, "launch_windows_detached", fail)
    with pytest.raises(RuntimeError, match="WMI unavailable"):
        bgjobs.spawn(kind="movie", title="Movie", args=["run", "Movie"])
    assert bgjobs.list_jobs()[0].status == "failed"
    assert not list(bgjobs.jobs_root().glob("*/launch.json"))


def test_wmi_launch_is_hidden_and_requests_job_breakaway(monkeypatch, tmp_path):
    import base64
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(bgjobs.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="4321", stderr="")

    monkeypatch.setattr(bgjobs.subprocess, "run", run)
    assert bgjobs.launch_windows_detached(["python", "name with spaces"], tmp_path) == 4321
    command, options = calls[0]
    script = base64.b64decode(command[-1]).decode("utf-16-le")
    assert "Win32_Process" in script
    assert "ShowWindow=[uint16]0" in script
    assert "CreateFlags=[uint32]16777216" in script
    assert options["creationflags"] == 0x08000000
