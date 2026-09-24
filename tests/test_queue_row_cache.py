"""The queue row cache must stay linear in the number of jobs."""

from __future__ import annotations

import time

from bankai.cli import bgjobs
from bankai.web import jobs as webjobs


def _job(index: int) -> bgjobs.BgJob:
    return bgjobs.BgJob(
        id=f"job{index:05d}",
        kind="show",
        title=f"Show {index}",
        args=["show", "--title", f"Show {index}"],
        started_at=time.time(),
        status="done",
    )


def test_building_rows_never_walks_the_jobs_directory(monkeypatch, tmp_path):
    """With 3,900 jobs and a cold cache, every miss used to glob every job.

    That was ~15 million directory reads per snapshot, and it took the whole
    web UI down after a restart.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    webjobs._ROW_CACHE.clear()
    globs = []
    real_root = bgjobs.jobs_root

    class CountingRoot(type(real_root())):
        def glob(self, pattern):  # pragma: no cover - failing path only
            globs.append(pattern)
            return super().glob(pattern)

    monkeypatch.setattr(bgjobs, "jobs_root", lambda: CountingRoot(real_root()))

    for index in range(1_200):
        webjobs._display_row(_job(index))

    assert globs == []
    assert len(webjobs._ROW_CACHE) == 1_200
    webjobs._ROW_CACHE.clear()


def test_rows_for_jobs_that_are_gone_are_pruned():
    webjobs._ROW_CACHE.clear()
    webjobs._ROW_CACHE.update({"kept": ((1,), {}), "gone": ((1,), {})})

    webjobs._prune_row_cache({"kept", "new"})

    assert set(webjobs._ROW_CACHE) == {"kept"}
    webjobs._ROW_CACHE.clear()
