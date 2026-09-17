"""Library path naming."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.processor.naming import render_episode_path

_SEASON = "Season {season:02d}"
_FILE = "{series_title} - S{season:02d}E{episode:02d}.mkv"


def _episode_path(series_title: str, year: str | None) -> Path:
    return render_episode_path(
        library=Path("/library"),
        query=series_title,
        series_title=series_title,
        season=1,
        episode=1,
        episode_title=None,
        year_override=year,
        audio_lang="jpn",
        include_year=True,
        season_folder_template=_SEASON,
        file_template=_FILE,
    )


@pytest.mark.parametrize(
    "series_title,year,expected_folder",
    [
        # The reported duplicate: TVDB already put the year in the title, and
        # appending it again produced a second folder for the same series.
        ("LIAR GAME (2026)", "2026", "LIAR GAME (2026)"),
        # A title carrying a different year is still not doubled up.
        ("Some Show (2024)", "2026", "Some Show (2024)"),
        # The ordinary case still gets its year.
        ("Test Show", "2024", "Test Show (2024)"),
        # No year to add, nothing invented.
        ("Test Show", None, "Test Show"),
    ],
)
def test_the_year_is_never_appended_twice(series_title, year, expected_folder):
    path = _episode_path(series_title, year)
    assert path.parent.parent.name == expected_folder


def test_both_spellings_of_a_series_land_in_one_folder():
    """This is what made the same anime appear twice in the library."""
    with_year = _episode_path("LIAR GAME (2026)", "2026")
    assert with_year.parent.parent.name == _episode_path("LIAR GAME (2026)", None).parent.parent.name
