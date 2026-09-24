"""Tests for web job scheduler helpers (transfer column plumbing)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import ClassVar

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


def test_completed_anime_bypasses_full_download_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info_hash = "a" * 40
    args = [
        "anime-download",
        "--info-hash",
        info_hash,
        "--require-german-subtitles",
    ]
    monkeypatch.setattr(webjobs, "_completed_anime_hashes", lambda: frozenset({info_hash}))
    monkeypatch.setattr(
        webjobs,
        "_anime_reserve_available",
        lambda: (_ for _ in ()).throw(AssertionError("reserve should not be checked")),
    )

    assert webjobs._anime_storage_ready(args) is True


def test_incomplete_anime_still_obeys_download_reserve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = [
        "anime-download",
        "--info-hash",
        "b" * 40,
        "--require-german-subtitles",
    ]
    monkeypatch.setattr(webjobs, "_completed_anime_hashes", lambda: frozenset())
    monkeypatch.setattr(webjobs, "_anime_reserve_available", lambda: False)

    assert webjobs._anime_storage_ready(args) is False


def test_completed_hash_refresh_is_non_blocking_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info_hash = "c" * 40
    targets: list = []

    class Thread:
        def __init__(self, *, target, **_kwargs) -> None:
            targets.append(target)

        def start(self) -> None:
            pass

    monkeypatch.setattr(webjobs, "_QBIT_COMPLETED_CACHE", (0.0, frozenset()))
    monkeypatch.setattr(webjobs, "_QBIT_COMPLETED_REFRESHING", False)
    monkeypatch.setattr(webjobs, "_QBIT_COMPLETED_LAST_ATTEMPT", 0.0)
    monkeypatch.setattr(webjobs.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(webjobs.threading, "Thread", Thread)
    monkeypatch.setattr(webjobs, "_fetch_completed_anime_hashes", lambda: frozenset({info_hash}))

    assert webjobs._completed_anime_hashes() == frozenset()
    assert len(targets) == 1
    assert webjobs._QBIT_COMPLETED_REFRESHING

    targets[0]()
    assert webjobs._completed_anime_hashes() == frozenset({info_hash})


def test_queue_scheduler_reconciles_without_browser_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconciled: list[bool] = []

    async def to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    async def stop_after_first(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(webjobs, "reconcile", lambda: reconciled.append(True) or 0)
    monkeypatch.setattr(webjobs, "_archive_jobs", lambda: 0)
    monkeypatch.setattr(webjobs.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(webjobs.asyncio, "sleep", stop_after_first)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(webjobs.scheduler())
    assert reconciled == [True]


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


def test_pending_snapshot_exposes_saved_german_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = PendingJob(
        id="source1",
        kind="show",
        title="Arcane S02E01",
        args=["run", "Arcane S02E01", "--url", "https://voe.sx/german"],
        created_at=10,
    )
    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [item])
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: [])

    row = webjobs.snapshot()[0]

    assert row["german_source_url"] == "https://voe.sx/german"
    assert row["torrent_source_url"] is None


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


def test_dashboard_read_reuses_one_registry_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def list_jobs() -> list:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [])
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", list_jobs)

    with webjobs.dashboard_read():
        assert webjobs.snapshot() == []
        assert webjobs.transfer_states() == {}
        assert webjobs.repack_states() == {}

    assert calls == 1


def test_catalog_titles_does_not_parse_progress_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [
        SimpleNamespace(id="done", title="Done Movie", kind="movie", args=[], status="done"),
        SimpleNamespace(id="failed", title="Failed Movie", kind="movie", args=[], status="failed"),
    ]
    pending = PendingJob(id="queued", title="Queued Movie", kind="movie", args=[])
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: jobs)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [pending])
    monkeypatch.setattr(
        webjobs.bgjobs,
        "progress_snapshot",
        lambda _job: pytest.fail("catalog membership must not parse logs"),
    )

    assert webjobs.catalog_titles() == {"Done Movie", "Queued Movie"}


@pytest.mark.parametrize(
    "status,step_key,expected",
    [
        ("running", "torrent", "downloading"),
        # Both halves of publishing read as one state in the queue.
        ("running", "organize", "transferring"),
        ("running", "transfer", "transferring"),
        # A worker that has not announced a stage yet stays plain "running".
        ("running", None, "running"),
        ("running", "something-new", "running"),
        # Everything that is not running already says what it is.
        ("queued", None, "queued"),
        ("failed", "torrent", "failed"),
        ("done", "transfer", "done"),
    ],
)
def test_phase_narrows_running_to_the_announced_stage(status, step_key, expected):
    assert webjobs._phase(status, step_key) == expected


# Appended to tests/test_webjobs.py. Lines are held as a list so the wrapped
# sample stays readable and needs no escape sequences.

# Copied from a stuck job on the live box: Rich wrapped the stage marker at 80
# columns, so nothing downstream could read it.
_WRAPPED_LINES = [
    '[02:44:19] INFO     BANKAI_STAGE step=1 total=3 key=torrent label="Download    ',
    '                    from Nyaa"                                                 ',
    "[02:44:20] INFO     BANKAI_PROGRESS stage=torrent pct=100.0 speed=0 eta=8640000",
    '           INFO     BANKAI_STAGE step=2 total=3 key=organize label="Organize   ',
    '                    with TVDB"                                                 ',
]

_FLAT_LINES = [
    'BANKAI_STAGE step=1 total=3 key=torrent label="Download from Nyaa"',
    "BANKAI_PROGRESS stage=torrent pct=40.0 speed=1 eta=2",
]


def _snapshot_of(tmp_path, lines):
    from bankai.cli import bgjobs

    log = tmp_path / "log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    class Job:
        id = "j1"
        kind = "show"
        title = "Test Show S01E01"
        args: ClassVar[list[str]] = ["anime-download"]
        status = "running"
        log_path = log

        def refresh(self):
            return self

    return bgjobs.progress_snapshot(Job())


def test_wrapped_stage_markers_are_still_parsed(tmp_path):
    """Every running job read as "Starting" at 100% because of this."""
    snap = _snapshot_of(tmp_path, _WRAPPED_LINES)
    assert snap.step_key == "organize"
    assert snap.step_label == "Organize with TVDB"
    assert (snap.step, snap.total_steps) == (2, 3)
    # A finished first stage must not make the whole job look finished.
    assert snap.overall_percent is not None
    assert snap.overall_percent < 100.0
    assert webjobs._phase("running", snap.step_key) == "transferring"


def test_unwrapping_leaves_intact_logs_alone(tmp_path):
    from bankai.cli import bgjobs

    assert bgjobs._unwrap_markers(list(_FLAT_LINES)) == _FLAT_LINES
    snap = _snapshot_of(tmp_path, _FLAT_LINES)
    assert snap.step_key == "torrent"
    assert snap.step_label == "Download from Nyaa"


def test_log_console_is_wide_enough_for_markers_when_redirected():
    """Rich defaults a redirected stream to 80 columns; the markers are longer."""
    from bankai import logging as bankai_logging

    console = bankai_logging._log_console()
    assert not console.is_terminal
    assert console.width >= 200


def test_anime_queue_phases_stay_inside_the_release_state_machine():
    """The queue must not invent states the reconciler does not track.

    Publishing is two copies internally, but a reader of the queue should see
    the same vocabulary the release table uses, not an extra stage name.
    """
    from bankai.web import erai

    anime_phases = {webjobs._PHASE_BY_STEP[key] for key in ("torrent", "organize", "transfer")}
    assert anime_phases == {"downloading", "transferring"}
    assert anime_phases <= erai._ACTIVE_RELEASE_STATES

