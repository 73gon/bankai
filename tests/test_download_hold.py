"""Automatic downloads off: bankai's waiting torrents stay paused, and come back when it is on again."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import ClassVar

import pytest

from bankai.torrent.qbittorrent import TorrentStatus
from bankai.web import erai


GIB = 1024**3


def _torrent(info_hash: str, state: str, progress: float = 0.2, size: int = 1) -> TorrentStatus:
    return TorrentStatus(info_hash, info_hash, state, progress, "/downloads", "", size, 0, 0)


class FakeQbit:
    torrents: ClassVar[list[TorrentStatus]] = []
    paused: ClassVar[list[str]] = []
    resumed: ClassVar[list[str]] = []
    free: ClassVar[int | None] = 1000 * GIB

    async def login(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def list_torrents(self, category=None):
        return list(self.torrents)

    async def free_space_bytes(self):
        return FakeQbit.free

    @staticmethod
    def _set(hashes: list[str], state: str) -> None:
        FakeQbit.torrents = [
            dataclasses.replace(t, state=state) if t.hash in hashes else t for t in FakeQbit.torrents
        ]

    async def pause(self, hashes: str) -> None:
        FakeQbit.paused.extend(hashes.split("|"))
        self._set(hashes.split("|"), "stoppedDL")

    async def resume(self, hashes: str) -> None:
        FakeQbit.resumed.extend(hashes.split("|"))
        self._set(hashes.split("|"), "queuedDL")


@pytest.fixture
def qbit(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", FakeQbit)
    # Free space as FakeQbit reports it, not measured on a real download folder.
    monkeypatch.setattr(erai, "_local_download_dir", lambda: None)
    # No download jobs running unless a test says so.
    monkeypatch.setattr("bankai.cli.bgjobs.list_jobs", lambda: [])
    FakeQbit.torrents = [
        _torrent("waiting", "queuedDL"),
        _torrent("stalled", "stalledDL"),
        _torrent("mine", "stoppedDL"),  # paused by hand
        _torrent("broken", "error"),
        _torrent("seeding", "queuedUP", progress=1.0),
    ]
    FakeQbit.paused, FakeQbit.resumed = [], []
    FakeQbit.free = 1000 * GIB
    return FakeQbit


def test_turning_automatic_downloads_off_pauses_only_the_unfinished_running_ones(qbit) -> None:
    assert asyncio.run(erai.hold_downloads(hold=True)) == 2

    assert sorted(qbit.paused) == ["stalled", "waiting"]


def test_turning_them_on_again_resumes_exactly_what_was_paused(qbit) -> None:
    asyncio.run(erai.hold_downloads(hold=True))

    assert asyncio.run(erai.hold_downloads(hold=False)) == 2

    assert sorted(qbit.resumed) == ["stalled", "waiting"]
    # Nothing is held any more: a second pass touches nothing.
    assert asyncio.run(erai.hold_downloads(hold=False)) == 0


def test_with_nothing_held_and_downloads_on_qbittorrent_is_not_asked(qbit, monkeypatch) -> None:
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", None)

    assert asyncio.run(erai.hold_downloads(hold=False)) == 0


def _asked_for(monkeypatch, *hashes: str) -> None:
    """Releases an HEVC upgrade queued: they carry the files they replace."""
    state = erai._default_state()
    for info_hash in hashes:
        state["releases"][info_hash] = {"status": "queued", "replace_files": [["x.mkv", 1, 1]]}
    monkeypatch.setattr(erai, "_load_state", lambda: state)


def _upgrades(qbit, monkeypatch, *rows: tuple[str, str, float, int]) -> None:
    """Only hand-started downloads in qBittorrent: (hash, state, progress, GiB)."""
    qbit.torrents = [_torrent(h, state, progress, gib * GIB) for h, state, progress, gib in rows]
    _asked_for(monkeypatch, *(h for h, *_ in rows))


def test_a_download_asked_for_by_hand_is_not_paused(qbit, monkeypatch) -> None:
    """The HEVC upgrade button queued downloads that the next pass paused again."""
    _asked_for(monkeypatch, "stalled")

    assert asyncio.run(erai.hold_downloads(hold=True)) == 1

    assert qbit.paused == ["waiting"]


def test_a_download_from_the_library_drawer_is_not_paused(qbit, monkeypatch) -> None:
    """Started from the drawer, it is a job waiting on its torrent: held as backlog, it sat at 0 B/s."""
    from types import SimpleNamespace

    job = SimpleNamespace(status="running", args=["anime-download", "--info-hash", "STALLED", "--publish"])
    done = SimpleNamespace(status="done", args=["anime-download", "--info-hash", "waiting"])
    monkeypatch.setattr("bankai.cli.bgjobs.list_jobs", lambda: [job, done])

    assert asyncio.run(erai.hold_downloads(hold=True)) == 1

    assert qbit.paused == ["waiting"]


def test_one_paused_before_the_exemption_is_resumed(qbit, monkeypatch) -> None:
    asyncio.run(erai.hold_downloads(hold=True))
    _asked_for(monkeypatch, "stalled")
    qbit.paused = []

    assert asyncio.run(erai.hold_downloads(hold=True)) == 1

    assert qbit.resumed == ["stalled"]
    # No longer held: turning downloads on resumes only the backlog one.
    qbit.resumed = []
    asyncio.run(erai.hold_downloads(hold=False))
    assert qbit.resumed == ["waiting"]


def test_hand_downloads_that_all_fit_all_run(qbit, monkeypatch) -> None:
    _upgrades(qbit, monkeypatch, ("a", "downloading", 0.1, 5), ("b", "queuedDL", 0.0, 5))
    qbit.free = 50 * GIB

    assert asyncio.run(erai.hold_downloads(hold=True)) == 0
    assert qbit.paused == []


def test_on_a_tight_disk_hand_downloads_go_one_at_a_time(qbit, monkeypatch) -> None:
    """Resuming them all at once was 99 GiB onto 55 GiB free."""
    _upgrades(
        qbit,
        monkeypatch,
        ("small", "queuedDL", 0.0, 3),
        ("along", "downloading", 0.6, 20),
        ("large", "downloading", 0.0, 14),
    )
    qbit.free = 30 * GIB  # 20 GiB above the 10 GiB floor; together they need 25

    asyncio.run(erai.hold_downloads(hold=True))

    # The one furthest along keeps going: published first, it frees its old files.
    assert sorted(qbit.paused) == ["large", "small"]
    assert {t.hash: t.state for t in qbit.torrents}["along"] == "downloading"

    # It finished: the next goes, smallest first, the other still waits.
    qbit.torrents = [t for t in qbit.torrents if t.hash != "along"]
    _asked_for(monkeypatch, "small", "large")
    qbit.free = 22 * GIB
    qbit.paused, qbit.resumed = [], []
    asyncio.run(erai.hold_downloads(hold=True))
    assert qbit.resumed == ["small"]
    assert qbit.paused == []


def test_one_too_big_for_the_disk_waits(qbit, monkeypatch) -> None:
    _upgrades(qbit, monkeypatch, ("huge", "downloading", 0.0, 40))
    qbit.free = 30 * GIB

    asyncio.run(erai.hold_downloads(hold=True))
    assert qbit.paused == ["huge"]


def test_a_hand_download_paused_in_qbittorrent_is_left_alone(qbit, monkeypatch) -> None:
    _upgrades(qbit, monkeypatch, ("mine", "stoppedDL", 0.1, 1))

    assert asyncio.run(erai.hold_downloads(hold=True)) == 0
    assert qbit.resumed == []


def test_the_space_rule_holds_with_automation_on_too(qbit, monkeypatch) -> None:
    _upgrades(qbit, monkeypatch, ("a", "downloading", 0.5, 10), ("b", "downloading", 0.0, 10))
    qbit.free = 20 * GIB

    asyncio.run(erai.hold_downloads(hold=False))
    assert qbit.paused == ["b"]


def test_a_pause_that_times_out_is_still_resumed_once_there_is_room(qbit, monkeypatch) -> None:
    """qBittorrent stalled: it paused them, the answer never came, and they were never resumed."""
    import httpx

    _upgrades(qbit, monkeypatch, ("huge", "downloading", 0.0, 40))
    qbit.free = 30 * GIB

    async def timing_out(self, hashes: str) -> None:
        FakeQbit._set(hashes.split("|"), "stoppedDL")  # done...
        raise httpx.ReadTimeout("")  # ...but no answer

    monkeypatch.setattr(FakeQbit, "pause", timing_out)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(erai.hold_downloads(hold=True))

    qbit.free = 100 * GIB
    asyncio.run(erai.hold_downloads(hold=True))
    assert qbit.resumed == ["huge"]


def test_free_space_is_measured_on_the_download_folder(qbit, monkeypatch, tmp_path) -> None:
    """qBittorrent's own figure comes through its API, which stalls; the folder is right there."""
    from types import SimpleNamespace

    _upgrades(qbit, monkeypatch, ("a", "downloading", 0.0, 5))
    qbit.free = 0  # what a stalling qBittorrent reported
    monkeypatch.setattr(erai, "_local_download_dir", lambda: tmp_path)
    monkeypatch.setattr(erai.shutil, "disk_usage", lambda _path: SimpleNamespace(free=500 * GIB))

    asyncio.run(erai.hold_downloads(hold=True))
    assert qbit.paused == []
