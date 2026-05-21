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
