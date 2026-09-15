from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankai.web import jobs, updates


@pytest.fixture()
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "updates.json"
    monkeypatch.setattr(updates, "_path", lambda: path)
    return path


def test_diverged_main_never_offers_an_update(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def git(*args: str) -> str:
        if args == ("branch", "--show-current"):
            return "main"
        if args[0] == "rev-parse":
            return "a" * 40
        if args[0] == "rev-list":
            return "1 2"
        return ""

    monkeypatch.setattr(updates, "_git", git)
    result = updates.check()
    assert not result["available"]
    assert "commits outside" in result["error"]
    assert not updates.maintenance_active()


def test_tracked_user_changes_block_update(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def git(*args: str) -> str:
        return "main" if args[0] == "branch" else " M config.example.toml"

    monkeypatch.setattr(updates, "_git", git)
    monkeypatch.setattr(
        updates, "_launch_worker", lambda: pytest.fail("Dirty checkout must not start an updater")
    )
    with pytest.raises(RuntimeError, match="tracked changes"):
        updates.start()
    assert not updates.maintenance_active()


def test_update_wait_holds_movies_anime_and_transfers(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    updates._patch(phase="waiting", pid=123)
    pending = []
    monkeypatch.setattr(jobs, "_load_pending", lambda: pending.copy())
    monkeypatch.setattr(
        jobs, "_save_pending", lambda items: pending.__setitem__(slice(None), items)
    )
    monkeypatch.setattr(jobs.bgjobs, "list_jobs", lambda: [])
    monkeypatch.setattr(
        jobs.bgjobs,
        "spawn",
        lambda **kwargs: pytest.fail("Update wait must prevent all new workers"),
    )
    for kind, title, args in [
        ("movie", "Movie", ["run"]),
        ("show", "Anime", ["anime-download"]),
        ("transfer", "Transfer", ["transfer-run"]),
    ]:
        assert jobs.enqueue(kind=kind, title=title, args=args)["status"] == "queued"
    assert jobs.reconcile() == 0
    assert len(pending) == 3
    with pytest.raises(RuntimeError, match="updating"):
        jobs.force_start_pending(pending[0].id)


def test_dead_update_helper_releases_queue(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    updates._patch(phase="waiting", pid=123)
    row = updates._read()
    row["updated_at"] = time.time() - 121
    updates._write(row)
    monkeypatch.setattr(updates.bgjobs, "_pid_alive", lambda pid: False)
    assert not updates.maintenance_active()
    assert updates.status()["phase"] == "failed"


def test_update_worker_waits_for_existing_jobs(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = "a" * 40
    updates._patch(phase="waiting", target_commit=target)
    polls = iter([(False, 2), (False, 1), (True, 0)])
    sleeps = []
    applied = []
    monkeypatch.setattr(updates, "_idle", lambda: next(polls))
    monkeypatch.setattr(updates.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(updates, "_apply", lambda commit: applied.append(commit))
    updates.run_worker()
    assert sleeps == [15, 15]
    assert applied == [target]


def test_restart_requires_exact_commit_and_reports_health(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = "a" * 40
    repo = state.parent
    (repo / "src/bankai/web/static").mkdir(parents=True)
    (repo / "src/bankai/web/static/index.html").write_text("<html></html>")
    monkeypatch.setattr(updates, "_repo", lambda: repo)
    monkeypatch.setattr(updates, "_validate_checkout", lambda: None)
    monkeypatch.setattr(updates, "_git", lambda *args: target if args[0] == "rev-parse" else "")
    calls = []
    monkeypatch.setattr(updates, "_run", lambda args, **kwargs: calls.append(args) or "")
    monkeypatch.setattr(
        updates.httpx, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200)
    )
    updates._apply(target)
    assert any("Restart-Service bankai-web" in args for args in calls)
    assert updates.status()["phase"] == "done"
    assert updates.status()["current_commit"] == target
    assert not updates.maintenance_active()


def test_wrong_commit_never_restarts_service(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "_validate_checkout", lambda: None)
    monkeypatch.setattr(updates, "_git", lambda *args: "b" * 40 if args[0] == "rev-parse" else "")
    monkeypatch.setattr(
        updates,
        "_run",
        lambda *args, **kwargs: pytest.fail("Wrong commit must not install or restart"),
    )
    with pytest.raises(RuntimeError, match="requested commit"):
        updates._apply("a" * 40)


def test_only_one_update_worker_can_start(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    updates._patch(phase="idle", available=True, supported=True, latest_commit="a" * 40)
    monkeypatch.setattr(updates, "check", updates.status)
    monkeypatch.setattr(updates, "_validate_checkout", lambda: None)
    launches = []
    monkeypatch.setattr(updates, "_launch_worker", lambda: launches.append(1) or 123)
    assert updates.start()["phase"] == "waiting"
    assert updates.start()["phase"] == "waiting"
    assert launches == [1]
