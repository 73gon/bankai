"""Show identity used to group the anime library."""

from __future__ import annotations

import pytest

from bankai.web.anime_library import _name


# Every pair below appeared twice in the library: once as the folder holding
# the episodes, once as an empty card built from the tracked TVDB title.
@pytest.mark.parametrize(
    "folder_name,tvdb_title",
    [
        # sanitise() drops "?" and ":" from folder names; the TVDB title keeps them.
        (
            "Heroine Saint No, I'm an All-Works Maid (And Proud of It)! (2026)",
            "Heroine? Saint? No, I'm an All-Works Maid (And Proud of It)!",
        ),
        ("Jaadugar A Witch in Mongolia (2026)", "Jaadugar: A Witch in Mongolia"),
        (
            "Magical Girl Lyrical Nanoha EXCEEDS Gun Blaze Vengeance",
            "Magical Girl Lyrical Nanoha EXCEEDS: Gun Blaze Vengeance",
        ),
        # Folders left behind by the double-year bug still belong to the series.
        ("LIAR GAME (2026) (2026)", "LIAR GAME (2026)"),
        (
            "Love Unseen Beneath the Clear Night Sky (2026) (2026)",
            "Love Unseen Beneath the Clear Night Sky (2026)",
        ),
    ],
)
def test_a_folder_and_its_tvdb_title_are_the_same_show(folder_name, tvdb_title):
    assert _name(folder_name) == _name(tvdb_title)


def test_genuinely_different_shows_stay_apart():
    assert _name("Grand Blue") != _name("Grand Blue Dreaming")
    assert _name("Show A (2024)") != _name("Show B (2024)")


def test_a_single_year_is_still_stripped():
    assert _name("Test Show (2024)") == _name("Test Show")


def test_a_name_that_is_only_punctuation_does_not_collapse_to_nothing():
    """sanitise() would otherwise fall back and merge unrelated shows."""
    assert _name("???") != _name("***")


def test_episode_codecs_are_read_from_the_release_that_produced_them(monkeypatch):
    """Probing thousands of files on a slow library disk is not worth it."""
    from bankai.web import anime_library, erai

    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {"status": "done", "title": "[Erai-raws] Show - 01 [1080p][HEVC][ENG]"},
        "b" * 40: {"status": "done", "title": "[Erai-raws] Show - 02 [1080p CR WEB-DL AVC AAC]"},
        "c" * 40: {"status": "done", "title": "[Erai-raws] Other - 01 [1080p][HEVC]"},
    }
    state["canonical"] = {
        "555|1|1": {"info_hash": "a" * 40},
        "555|1|2": {"info_hash": "b" * 40},
        "999|1|1": {"info_hash": "c" * 40},
    }
    monkeypatch.setattr(erai, "_load_state", lambda: state)

    codecs = anime_library.episode_codecs(555)
    assert codecs == {(1, 1): "hevc", (1, 2): "avc"}
    # A different series is not mixed in.
    assert anime_library.episode_codecs(999) == {(1, 1): "hevc"}
    assert anime_library.episode_codecs(None) == {}


def test_merge_episodes_marks_each_episode_with_its_codec():
    from bankai.metadata.tvdb import TVDBEpisode
    from bankai.web.anime_library import merge_episodes

    files = [
        {"path": "1", "season_number": 1, "episode": 1, "name": "1", "staged": False},
        {"path": "2", "season_number": 1, "episode": 2, "name": "2", "staged": False},
    ]
    roster = [
        TVDBEpisode(1, 1, aired="2020-01-01"),
        TVDBEpisode(1, 2, aired="2020-01-08"),
        TVDBEpisode(1, 3, aired="2020-01-15"),
    ]
    merged = merge_episodes(
        files, roster, ended=True, codecs={(1, 1): "hevc", (1, 2): "avc"}
    )
    by_number = {row["episode"]: row for row in merged["episodes"]}
    assert by_number[1]["codec"] == "hevc"
    assert by_number[2]["codec"] == "avc"
    # An episode that is not downloaded has no encode to report.
    assert by_number[3]["codec"] is None


def test_the_release_state_is_read_once_for_the_whole_library(monkeypatch):
    """It is a fourteen megabyte file; reading it per show cost twenty seconds."""
    import asyncio
    from pathlib import Path

    from bankai.web import anime_library, discover, erai

    reads = []
    state = erai._default_state()
    state["releases"] = {
        f"{n:040x}": {"status": "done", "title": f"[Erai-raws] Show {n} - 01 [1080p][HEVC]"}
        for n in range(1, 21)
    }
    state["canonical"] = {
        f"{n}|1|1": {"info_hash": f"{n:040x}"} for n in range(1, 21)
    }

    def load():
        reads.append(1)
        return state

    monkeypatch.setattr(erai, "_load_state", load)
    monkeypatch.setattr(anime_library, "known_ids", lambda: {})
    monkeypatch.setattr(discover, "is_configured", lambda: False)
    monkeypatch.setattr(anime_library, "_nfo_id", lambda path: None)
    monkeypatch.setattr(anime_library, "probed_codecs", lambda files: {})
    monkeypatch.setattr(anime_library, "german_dubbed_episodes", lambda files: set())

    async def metadata(title, tvdb_id=None):
        return {"english_title": title, "tvdb_id": tvdb_id}

    monkeypatch.setattr(anime_library, "show_metadata", metadata)

    entries = [
        {
            "path": f"/library/Show {n}/Season 01/ep.mkv",
            "rel_path": f"Show {n}/Season 01/ep.mkv",
            "name": f"Show {n} - S01E01.mkv",
            "series": f"Show {n}",
            "season": "Season 01",
            "size": 1,
            "mtime": 0,
            "staged": False,
            "stage": "final",
            "transfer_status": "idle",
        }
        for n in range(1, 21)
    ]
    shows = asyncio.run(anime_library.group_shows(entries, Path("/library")))

    assert len(shows) == 20
    # One read for the codec index, whatever the library holds.
    assert len(reads) == 1

