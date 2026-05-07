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
