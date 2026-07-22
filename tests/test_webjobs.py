"""Tests for web job scheduler helpers (transfer column plumbing)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bankai.web import jobs as webjobs
from bankai.web.jobs import PendingJob, _transfer_target


def test_transfer_target_extracts_path() -> None:
    assert (
        _transfer_target(["transfer-run", "/mnt/media/bankai/Movies/X/X.mkv", "--kind", "movie"])
        == "/mnt/media/bankai/Movies/X/X.mkv"
    )


def test_transfer_target_skips_leading_flags() -> None:
    assert (
        _transfer_target(["transfer-run", "--kind", "show", "/lib/Shows/S/E.mkv"])
        == "/lib/Shows/S/E.mkv"
    )


def test_transfer_target_ignores_non_transfer_jobs() -> None:
    assert _transfer_target(["run", "Movie", "--url", "http://x"]) is None
    assert _transfer_target([]) is None


def test_running_count_excludes_transfers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        webjobs.bgjobs,
        "list_jobs",
        lambda: [
            SimpleNamespace(status="running", kind="movie"),
            SimpleNamespace(status="stopped", kind="show"),
            SimpleNamespace(status="running", kind="transfer"),
            SimpleNamespace(status="running", kind="repack"),
            SimpleNamespace(status="running", kind="torrent_replace"),
            SimpleNamespace(status="done", kind="show"),
        ],
    )

    assert webjobs._running_count() == 2


def test_snapshot_hides_detached_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [])
    monkeypatch.setattr(
        webjobs.bgjobs,
        "list_jobs",
        lambda: [
            SimpleNamespace(kind="repack"),
            SimpleNamespace(kind="torrent_replace"),
            SimpleNamespace(kind="transfer"),
        ],
    )

    assert webjobs.snapshot() == []


def test_snapshot_hides_legacy_misclassified_repack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [])
    monkeypatch.setattr(
        webjobs.bgjobs,
        "list_jobs",
        lambda: [SimpleNamespace(kind="movie", args=["review-repack", "movie.mkv"])],
    )

    assert webjobs.snapshot() == []


def test_pending_snapshot_exposes_priority_order(monkeypatch: pytest.MonkeyPatch) -> None:
    first = PendingJob(
        id="first", kind="movie", title="First", args=["run", "First"], created_at=10
    )
    second = PendingJob(
        id="second", kind="movie", title="Second", args=["run", "Second"], created_at=20
    )
    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [first, second])
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: [])

    rows = {row["id"]: row for row in webjobs.snapshot()}

    assert rows["first"]["queue_position"] == 1
    assert rows["second"]["queue_position"] == 2
    assert rows["first"]["queue_total"] == rows["second"]["queue_total"] == 2


def test_reorder_pending_changes_persisted_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        PendingJob(id="one", kind="movie", title="One", args=["run", "One"]),
        PendingJob(id="two", kind="movie", title="Two", args=["run", "Two"]),
        PendingJob(id="three", kind="movie", title="Three", args=["run", "Three"]),
    ]
    saved: list[PendingJob] = []
    monkeypatch.setattr(webjobs, "_load_pending", lambda: list(items))
    monkeypatch.setattr(webjobs, "_save_pending", lambda value: saved.extend(value))

    position = webjobs.reorder_pending("three", 1)

    assert position == 1
    assert [item.id for item in saved] == ["three", "one", "two"]


def test_force_start_pending_bypasses_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    item = PendingJob(id="queued1", kind="movie", title="Queued", args=["run", "Queued"])
    saved: list[list[PendingJob]] = []
    spawned: list[dict[str, object]] = []
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [item])
    monkeypatch.setattr(webjobs, "_save_pending", lambda value: saved.append(list(value)))
    monkeypatch.setattr(webjobs, "_running_titles", lambda: set())
    monkeypatch.setattr(
        webjobs.bgjobs,
        "spawn",
        lambda **kwargs: spawned.append(kwargs) or SimpleNamespace(id="started", status="running"),
    )

    job = webjobs.force_start_pending("queued1")

    assert job is not None and job.id == "started"
    assert saved == [[]]
    assert spawned == [
        {
            "kind": "movie",
            "title": "Queued",
            "args": ["run", "Queued"],
            "created_at": item.created_at,
        }
    ]


def test_transfer_starts_immediately_when_pipeline_limit_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[dict[str, object]] = []
    monkeypatch.setattr(
        webjobs,
        "get_settings",
        lambda: SimpleNamespace(web=SimpleNamespace(max_concurrent_jobs=1)),
    )
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [])
    monkeypatch.setattr(webjobs, "_running_titles", lambda: set())
    monkeypatch.setattr(webjobs, "_running_count", lambda: 1)
    monkeypatch.setattr(
        webjobs.bgjobs,
        "spawn",
        lambda **kwargs: spawned.append(kwargs) or SimpleNamespace(id="transfer1"),
    )

    result = webjobs.enqueue(
        kind="transfer",
        title="Transfer Inside Out (2015).mkv",
        args=["transfer-run", "Inside Out (2015).mkv", "--kind", "movie"],
    )

    assert result == {
        "status": "running",
        "id": "transfer1",
        "title": "Transfer Inside Out (2015).mkv",
    }
    assert [call["kind"] for call in spawned] == ["transfer"]


def test_reconcile_promotes_old_transfer_without_free_pipeline_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transfer = PendingJob(
        id="transfer1",
        kind="transfer",
        title="Transfer Inside Out (2015).mkv",
        args=["transfer-run", "Inside Out (2015).mkv", "--kind", "movie"],
    )
    movie = PendingJob(
        id="movie1",
        kind="movie",
        title="Queued Movie (2026)",
        args=["run", "Queued Movie (2026)"],
    )
    saved: list[PendingJob] = []
    spawned: list[dict[str, object]] = []
    monkeypatch.setattr(
        webjobs,
        "get_settings",
        lambda: SimpleNamespace(web=SimpleNamespace(max_concurrent_jobs=1)),
    )
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [transfer, movie])
    monkeypatch.setattr(webjobs, "_save_pending", lambda items: saved.extend(items))
    monkeypatch.setattr(webjobs, "_running_titles", lambda: set())
    monkeypatch.setattr(webjobs, "_running_count", lambda: 1)
    monkeypatch.setattr(
        webjobs.bgjobs,
        "spawn",
        lambda **kwargs: spawned.append(kwargs) or SimpleNamespace(id="started"),
    )

    assert webjobs.reconcile() == 1
    assert [call["kind"] for call in spawned] == ["transfer"]
    assert saved == [movie]


def test_reconcile_pauses_pipeline_queue_after_clustered_extraction_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = PendingJob(
        id="movie1",
        kind="movie",
        title="Queued Movie (2026)",
        args=["run", "Queued Movie (2026)"],
    )
    failures = [
        SimpleNamespace(
            id=f"failed{i}",
            kind="movie",
            args=[],
            status="failed",
            finished_at=995.0 + i,
        )
        for i in range(2)
    ]
    saved: list[PendingJob] = []
    spawned: list[dict[str, object]] = []
    monkeypatch.setattr(webjobs.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        webjobs,
        "get_settings",
        lambda: SimpleNamespace(web=SimpleNamespace(max_concurrent_jobs=3)),
    )
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [movie])
    monkeypatch.setattr(webjobs, "_save_pending", lambda items: saved.extend(items))
    monkeypatch.setattr(webjobs, "_running_titles", lambda: set())
    monkeypatch.setattr(webjobs, "_running_count", lambda: 0)
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: failures)
    monkeypatch.setattr(webjobs, "_job_reason", lambda job: "no media URL captured")
    monkeypatch.setattr(
        webjobs.bgjobs,
        "spawn",
        lambda **kwargs: spawned.append(kwargs) or SimpleNamespace(id="started"),
    )

    assert webjobs.reconcile() == 0
    assert spawned == []
    assert saved == [movie]


def test_pending_snapshot_reports_stream_recovery_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = PendingJob(id="movie1", kind="movie", title="Movie", args=["run", "Movie"])
    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [item])
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: [])
    monkeypatch.setattr(webjobs, "_stream_failure_cooldown_until", lambda now=None: 1234.0)

    row = next(row for row in webjobs.snapshot() if row["id"] == "movie1")

    assert row["step_label"] == "Waiting for stream source recovery"
