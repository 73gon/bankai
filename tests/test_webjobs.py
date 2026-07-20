"""Tests for web job scheduler helpers (transfer column plumbing)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bankai.web import jobs as webjobs
from bankai.web.jobs import PendingJob, _transfer_target


def test_transfer_target_extracts_path() -> None:
    assert _transfer_target(["transfer-run", "/mnt/media/bankai/Movies/X/X.mkv", "--kind", "movie"]) == "/mnt/media/bankai/Movies/X/X.mkv"


def test_transfer_target_skips_leading_flags() -> None:
    assert _transfer_target(["transfer-run", "--kind", "show", "/lib/Shows/S/E.mkv"]) == "/lib/Shows/S/E.mkv"


def test_transfer_target_ignores_non_transfer_jobs() -> None:
    assert _transfer_target(["run", "Movie", "--url", "http://x"]) is None
    assert _transfer_target([]) is None


def test_running_count_excludes_transfers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        webjobs.bgjobs,
        "list_jobs",
        lambda: [
            SimpleNamespace(status="running", kind="movie"),
            SimpleNamespace(status="running", kind="transfer"),
            SimpleNamespace(status="running", kind="repack"),
            SimpleNamespace(status="running", kind="torrent_replace"),
            SimpleNamespace(status="done", kind="show"),
        ],
    )

    assert webjobs._running_count() == 1


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
