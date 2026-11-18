"""German dubs in the anime library: counted from Jellyfin's tracks, written as tvshow.nfo's tag."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.config import reset_settings_cache
from bankai.web import anime_dubs

LIB = "G:\\media\\shows_anime"


def _episode(folder: str, season: int, number: int, *languages: str) -> dict:
    return {
        "Path": f"{LIB}\\{folder}\\Season {season:02}\\{folder} - S{season:02}E{number:02}.mkv",
        "ParentIndexNumber": season,
        "MediaStreams": [{"Type": "Video", "Language": "jpn"}]
        + [{"Type": "Audio", "Language": lang} for lang in languages]
        # A German subtitle is no dub.
        + [{"Type": "Subtitle", "Language": "ger"}],
    }


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()
    yield tmp_path
    reset_settings_cache()


def test_episodes_are_counted_by_collection_folder_and_specials_left_out() -> None:
    items = [
        _episode("Frieren", 1, 1, "jpn", "ger"),
        _episode("Frieren", 1, 2, "jpn", "deu"),
        _episode("Frieren", 2, 1, "jpn"),
        _episode("Frieren", 0, 1, "jpn"),
        _episode("Bleach", 1, 1, "jpn", "eng"),
    ]

    assert anime_dubs.count(items, [LIB]) == {"Frieren": [2, 3], "Bleach": [0, 1]}


def test_a_collection_with_any_german_episode_gets_the_tag(state, monkeypatch: pytest.MonkeyPatch) -> None:
    anime_dubs._save({"at": 1.0, "folders": {"Frieren": [10, 38], "Bleach": [0, 366]}})

    assert anime_dubs.tags("Frieren") == ["German Dub"]
    assert anime_dubs.tags("Bleach") == []
    assert anime_dubs.tags("Unknown") == []
    assert anime_dubs.tally("Frieren") == (10, 38)
