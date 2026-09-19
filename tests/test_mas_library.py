"""The Movies & Shows library, over the configured server roots."""

from __future__ import annotations

import asyncio

import pytest

from bankai.web import mas_library


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """Two movie roots and one show root, as a real install has."""
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    shows = tmp_path / "shows"
    for path in (local, remote, shows):
        path.mkdir()

    # A film as a bare file, and the same film again under the other root
    # inside a folder -- both spellings of how they actually land.
    (local / "Arrival (2016).mkv").write_bytes(b"x" * 10)
    (remote / "Arrival (2016)").mkdir()
    (remote / "Arrival (2016)" / "Arrival (2016).mkv").write_bytes(b"x" * 20)
    (local / "Dune (2021).mkv").write_bytes(b"x" * 30)
    # Not a video, and must not become a card.
    (local / "Arrival (2016).srt").write_bytes(b"x")

    series = shows / "Severance" / "Season 01"
    series.mkdir(parents=True)
    (series / "Severance - S01E01.mkv").write_bytes(b"x" * 40)
    (series / "Severance - S01E02.mkv").write_bytes(b"x" * 50)

    async def metadata(title, tvdb_id=None, kind="show"):
        return {}

    monkeypatch.setattr(mas_library, "show_metadata", metadata)
    monkeypatch.setattr(mas_library, "flush_persistent_cache", lambda: None)
    return {"movies": [local, remote], "shows": [shows]}


def test_a_film_stored_under_two_roots_is_one_card(roots):
    """The same title routinely sits on a local disk and a remote one."""
    cards = asyncio.run(mas_library.movies(roots["movies"]))
    by_title = {card["title"]: card for card in cards}
    assert sorted(by_title) == ["Arrival (2016)", "Dune (2021)"]

    arrival = by_title["Arrival (2016)"]
    assert arrival["file_count"] == 2
    assert arrival["size"] == 30
    # Both roots are named, which is how a duplicate becomes visible.
    assert len(arrival["roots"]) == 2
    assert len(by_title["Dune (2021)"]["roots"]) == 1


def test_a_subtitle_file_is_not_a_title(roots):
    cards = asyncio.run(mas_library.movies(roots["movies"]))
    assert all(not card["title"].endswith(".srt") for card in cards)
    assert all(
        not file["name"].endswith(".srt") for card in cards for file in card["files"]
    )


def test_a_series_is_counted_by_episode(roots):
    cards = asyncio.run(mas_library.shows(roots["shows"]))
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "Severance"
    assert card["episode_count"] == 2
    assert card["downloaded_count"] == 2
    assert card["season_count"] == 1
    assert card["size"] == 90
    # Without a TVDB roster there is no denominator to claim completeness
    # against, so the state says so rather than reporting "complete".
    assert card["completion_state"] == "unknown"


def test_episodes_can_be_left_out_for_the_grid(roots):
    cards = asyncio.run(mas_library.shows(roots["shows"], include_episodes=False))
    assert cards[0]["episodes"] == []
    # The counts still stand; only the per-episode rows are dropped.
    assert cards[0]["downloaded_count"] == 2


def test_a_root_that_does_not_exist_is_skipped(tmp_path, monkeypatch):
    """A configured path can point at a disk that is not mounted."""

    async def metadata(title, tvdb_id=None, kind="show"):
        return {}

    monkeypatch.setattr(mas_library, "show_metadata", metadata)
    assert asyncio.run(mas_library.movies([tmp_path / "missing"])) == []


def test_both_halves_come_back_together(roots):
    result = asyncio.run(mas_library.library(roots["movies"], roots["shows"]))
    assert {card["title"] for card in result["movies"]} == {
        "Arrival (2016)",
        "Dune (2021)",
    }
    assert [card["title"] for card in result["shows"]] == ["Severance"]


def test_the_walk_is_held_and_rescan_goes_back_to_disk(roots, monkeypatch):
    """The disk these libraries sit on is the slowest thing in the system.

    Every page load walking every root again is what made the anime library
    take twenty seconds, so the walk is cached for the configured TTL and the
    Rescan button is what asks for a fresh one.
    """
    mas_library.invalidate()
    walks = []
    original = mas_library._walk

    def counted(*args, **kwargs):
        walks.append(kwargs.get("use_cache", True))
        return original(*args, **kwargs)

    monkeypatch.setattr(mas_library, "_walk", counted)

    asyncio.run(mas_library.movies(roots["movies"]))
    first = len(asyncio.run(mas_library.movies(roots["movies"])))
    # Both calls went through _walk, but only the first touched the disk.
    assert walks == [True, True]
    assert first == 2

    (roots["movies"][0] / "Late Arrival (2030).mkv").write_bytes(b"x" * 5)
    # The cached answer does not know about it yet.
    assert len(asyncio.run(mas_library.movies(roots["movies"]))) == 2
    # A rescan does.
    assert len(asyncio.run(mas_library.movies(roots["movies"], rescan=True))) == 3
    mas_library.invalidate()
