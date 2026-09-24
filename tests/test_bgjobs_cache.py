"""Listing jobs re-reads only what changed; archiving moves only what nobody needs."""

from __future__ import annotations

import json
import time

import pytest

from bankai.cli import bgjobs


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(bgjobs, "_JOB_CACHE", {})
    return tmp_path


def _job(job_id, *, status="done", args=None, kind="show", age_days=0.0, log=""):
    ended = time.time() - age_days * 86400
    job = bgjobs.BgJob(
        id=job_id,
        kind=kind,
        title=job_id,
        args=args or ["run", "--title", job_id],
        started_at=ended - 60,
        status=status,
        finished_at=ended,
    )
    job.save()
    job.log_path.write_text(log, encoding="utf-8")
    return job


def _anime(job_id, info_hash, *, status="failed", started=0.0):
    job = _job(
        job_id,
        status=status,
        args=["anime-download", "--info-hash", info_hash, "--episode", "1"],
    )
    job.started_at = time.time() - 3600 + started
    job.save()
    return job


def test_an_unchanged_finished_job_is_not_read_again(state, monkeypatch):
    _job("a1", status="failed", log="Traceback\nboom\n")
    assert [job.id for job in bgjobs.list_jobs()] == ["a1"]

    reads = []
    real = bgjobs.BgJob.refresh
    monkeypatch.setattr(bgjobs.BgJob, "refresh", lambda self: reads.append(self.id) or real(self))
    bgjobs.list_jobs()
    bgjobs.get_job("a1")

    # Neither the meta nor the log scan for a late success line happened again.
    assert reads == []


def test_a_changed_job_is_read_again(state):
    job = _job("a1", status="failed")
    assert bgjobs.get_job("a1").status == "failed"

    job.status = "done"
    job.save()
    # Make the change visible even where mtimes are coarse.
    meta = job.meta_path
    meta.write_text(json.dumps({**json.loads(meta.read_text()), "title": "renamed"}))

    assert bgjobs.get_job("a1").title == "renamed"


def test_handed_out_jobs_do_not_share_state(state):
    _job("a1")
    first = bgjobs.get_job("a1")
    first.status = "cancelled"  # changed but never saved
    assert bgjobs.get_job("a1").status == "done"


def test_get_job_finds_one_by_prefix(state):
    _job("20260924-abcdef")
    assert bgjobs.get_job("20260924-abc").id == "20260924-abcdef"
    assert bgjobs.get_job("nope") is None
    assert bgjobs.get_job("../etc") is None


def test_superseded_anime_attempts_are_archived_and_the_newest_kept(state):
    _anime("old-fail", "h1", started=0)
    _anime("older-fail", "h1", started=-10)
    _anime("newest", "h1", status="done", started=10)
    _anime("other-release", "h2")

    assert bgjobs.archive_finished_jobs() == 2

    assert sorted(job.id for job in bgjobs.list_jobs()) == ["newest", "other-release"]
    archived = state / "bankai" / "jobs_archive"
    assert sorted(path.name for path in archived.iterdir()) == ["old-fail", "older-fail"]
    # Moved, not deleted: the log is still there.
    assert (archived / "old-fail" / "log").exists()


def test_old_jobs_go_but_completed_titles_and_live_jobs_stay(state):
    _job("old-failed-movie", status="failed", kind="movie", age_days=40)
    _job("old-transfer", kind="transfer", args=["transfer-run", "x"], age_days=40)
    # Marks the title as already added in Search and Discover.
    _job("old-done-movie", kind="movie", age_days=40)
    _job("recent-failed", status="failed", age_days=2)
    _job("old-but-running", status="running", age_days=40)

    assert bgjobs.archive_finished_jobs() == 2
    assert sorted(job.id for job in bgjobs.list_jobs()) == [
        "old-but-running",
        "old-done-movie",
        "recent-failed",
    ]


def test_a_dry_run_moves_nothing(state):
    _anime("old", "h1", started=0)
    _anime("new", "h1", started=10)

    assert bgjobs.archive_finished_jobs(dry_run=True) == 1
    assert len(bgjobs.list_jobs()) == 2


def test_a_job_a_release_still_points_at_is_kept(state):
    _anime("superseded-but-referenced", "h1", started=0)
    _anime("newest", "h1", started=10)

    assert bgjobs.archive_finished_jobs(keep_ids={"superseded-but-referenced"}) == 0
