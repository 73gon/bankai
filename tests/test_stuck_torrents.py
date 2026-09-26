"""Dead swarms must not hold every download slot."""

from __future__ import annotations

import asyncio

from bankai.torrent.qbittorrent import TorrentStatus, _to_status
from bankai.web import erai

HOUR = 3600.0


def _torrent(info_hash: str, state: str, last_activity: int = 0) -> TorrentStatus:
    return TorrentStatus(
        hash=info_hash,
        name=info_hash,
        state=state,
        progress=0.0,
        save_path="",
        content_path="",
        size_bytes=1,
        dlspeed=0,
        eta=0,
        last_activity=last_activity,
    )


class _Qbit:
    def __init__(self) -> None:
        self.bottom: list[str] = []

    async def bottom_priority(self, hashes: list[str]) -> None:
        self.bottom.extend(hashes)


def _rotate(state, torrents, now):
    qbit = _Qbit()
    count = asyncio.run(erai._rotate_stuck_torrents(state, qbit, torrents, now=now))
    return count, qbit.bottom


def test_a_download_stuck_for_hours_goes_to_the_back():
    state: dict = {}
    torrents = [_torrent("dead", "metaDL"), _torrent("busy", "downloading")]
    assert _rotate(state, torrents, now=1000.0) == (0, [])
    count, bottom = _rotate(state, torrents, now=1000.0 + 6 * HOUR)
    assert (count, bottom) == (1, ["dead"])
    # Rotated: it starts over when it comes back round.
    assert "dead" not in state["stuck_since"]


def test_the_clock_starts_when_bankai_first_sees_it_stuck():
    """A torrent back from the queue is old, but has had no time in its slot."""
    state: dict = {}
    count, _ = _rotate(state, [_torrent("old", "stalledDL")], now=100 * HOUR)
    assert count == 0
    assert state["stuck_since"] == {"old": 100 * HOUR}


def test_activity_while_watched_keeps_it():
    state = {"stuck_since": {"slow": 0.0}}
    count, _ = _rotate(state, [_torrent("slow", "stalledDL", last_activity=int(5 * HOUR))], now=7 * HOUR)
    assert count == 0


def test_one_that_moves_again_is_forgotten():
    state = {"stuck_since": {"was": 0.0}}
    _rotate(state, [_torrent("was", "downloading")], now=HOUR)
    assert state["stuck_since"] == {}


def test_last_activity_is_read_and_minus_one_is_never():
    row = {"hash": "h", "last_activity": -1}
    assert _to_status(row).last_activity == 0
    assert _to_status({**row, "last_activity": 1700000000}).last_activity == 1700000000
