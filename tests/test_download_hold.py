"""Automatic downloads off: bankai's waiting torrents stay paused, and come back when it is on again."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from bankai.torrent.qbittorrent import TorrentStatus
from bankai.web import erai


def _torrent(info_hash: str, state: str, progress: float = 0.2) -> TorrentStatus:
    return TorrentStatus(info_hash, info_hash, state, progress, "/downloads", "", 1, 0, 0)


class FakeQbit:
    torrents: ClassVar[list[TorrentStatus]] = []
    paused: ClassVar[list[str]] = []
    resumed: ClassVar[list[str]] = []

    async def login(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def list_torrents(self, category=None):
        return list(self.torrents)

    async def pause(self, hashes: str) -> None:
        FakeQbit.paused.extend(hashes.split("|"))

    async def resume(self, hashes: str) -> None:
        FakeQbit.resumed.extend(hashes.split("|"))


@pytest.fixture
def qbit(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", FakeQbit)
    FakeQbit.torrents = [
        _torrent("waiting", "queuedDL"),
        _torrent("stalled", "stalledDL"),
        _torrent("mine", "stoppedDL"),  # paused by hand
        _torrent("broken", "error"),
        _torrent("seeding", "queuedUP", progress=1.0),
    ]
    FakeQbit.paused, FakeQbit.resumed = [], []
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
