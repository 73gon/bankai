"""Movies & Shows -> Torrents: release names read, folders picked, files placed."""

from __future__ import annotations

import pytest

from bankai.web import direct
from bankai.web import jobs as webjobs


@pytest.mark.parametrize(
    ("name", "title", "year", "season", "kind"),
    [
        ("Modern.Family.S01.1080p.BluRay.x264-DEMAND", "Modern Family", None, 1, "show"),
        ("Modern Family S03E21 720p WEB DL DD5 1 H 264 CtrlHD", "Modern Family", None, 3, "show"),
        ("The.Matrix.1999.2160p.UHD.BluRay.x265-TERMiNAL", "The Matrix", 1999, None, "movie"),
        ("The Matrix (1999) 1080p BrRip x264 - 1.85GB -YIFY", "The Matrix", 1999, None, "movie"),
        ("1917.2019.1080p.BluRay.x264", "1917", 2019, None, "movie"),
        ("Charlotte's Web 2006 720p", "Charlotte's Web", 2006, None, "movie"),
        ("The Office US Season 2 Complete 720p", "The Office US", None, 2, "show"),
    ],
)
def test_a_release_name_gives_title_year_and_season(name, title, year, season, kind):
    assert direct.guess(name) == {"title": title, "year": year, "season": season, "kind": kind}


def test_languages_and_resolution_are_read_from_the_name():
    assert direct.languages("Movie.2020.German.DL.1080p.WEB-DL") == ["German"]
    assert direct.languages("Movie.2020.1080p.WEB-DL.DDP5.1") == []
    assert direct.resolution("Movie 2020 UHD BluRay") == "2160p"
    assert direct.resolution("Movie.2020.720p") == "720p"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Modern.Family.S01E05.720p.mkv", (1, 5, None)),
        ("Show - S02E01E02 - Pilot.mkv", (2, 1, 2)),
        ("Show.S02E01-E02.mkv", (2, 1, 2)),
        ("show.3x07.hdtv.avi", (3, 7, None)),
        ("Show.Extras.mkv", None),
    ],
)
def test_a_files_season_and_episode(name, expected):
    assert direct.file_episode(name) == expected


def test_filters_include_any_term_exclude_every_one():
    row = {"title": "Modern Family S01 1080p BluRay x265", "resolution": "1080p", "seeders": 40}
    assert direct.matches(row, quality="1080p", include=["x265", "hevc"], exclude=[], min_seeders=10)
    assert not direct.matches(row, quality="720p", include=[], exclude=[], min_seeders=0)
    assert not direct.matches(row, quality="all", include=[], exclude=["bluray"], min_seeders=0)
    assert not direct.matches(row, quality="all", include=["german"], exclude=[], min_seeders=0)
    assert not direct.matches(row, quality="all", include=[], exclude=[], min_seeders=41)


def test_a_movie_gets_its_own_folder(tmp_path):
    source = tmp_path / "dl" / "The.Matrix.1999.1080p.mp4"
    placed, left = direct.placements([source], kind="movie", root=tmp_path / "movies_en", title="The Matrix", year=1999)
    assert placed == [(source, tmp_path / "movies_en" / "The Matrix (1999)" / "The Matrix (1999).mp4")]
    assert left == []


def test_a_show_goes_into_the_folder_the_root_has_and_season_folders(tmp_path):
    root = tmp_path / "shows_en"
    (root / "Modern Family (2009)" / "Season 01").mkdir(parents=True)
    sources = [
        tmp_path / "Modern.Family.S02E01.mkv",
        tmp_path / "Modern.Family.S02E02E03.mkv",
        tmp_path / "sample.mkv",
        tmp_path / "Modern.Family.Bloopers.mkv",
    ]
    placed, left = direct.placements(sources, kind="show", root=root, title="Modern Family", year=None)
    show = root / "Modern Family (2009)"
    assert [dest for _src, dest in placed] == [
        show / "Season 02" / "Modern Family - S02E01.mkv",
        show / "Season 02" / "Modern Family - S02E02-E03.mkv",
    ]
    assert [reason for _src, reason in left] == ["a sample", "its name says no episode"]


def test_a_show_kept_flat_stays_flat(tmp_path):
    root = tmp_path / "shows_en"
    show = root / "Mr. Bean"
    show.mkdir(parents=True)
    (show / "Mr. Bean - S01E01.mkv").write_bytes(b"x")
    placed, _left = direct.placements([tmp_path / "Mr.Bean.S01E02.mkv"], kind="show", root=root, title="Mr Bean", year=1990)
    assert placed[0][1] == show / "Mr. Bean - S01E02.mkv"


def test_an_episode_named_only_by_number_takes_the_releases_season(tmp_path):
    placed, left = direct.placements(
        [tmp_path / "Show E05.mkv"], kind="show", root=tmp_path / "shows", title="Show", year=None, season=3
    )
    assert placed[0][1] == tmp_path / "shows" / "Show" / "Season 03" / "Show - S03E05.mkv"
    assert left == []


def test_targets_are_the_movie_and_show_roots_and_a_kind_takes_only_its_own(tmp_path, monkeypatch):
    folders = {
        "movies": [tmp_path / "movies", tmp_path / "movies_en"],
        "shows": [tmp_path / "shows", tmp_path / "shows_en"],
    }
    for paths in folders.values():
        for path in paths:
            path.mkdir()
    monkeypatch.setattr("bankai.web.track_cleanup.roots", lambda scope: [str(p) for p in folders[scope]])
    labels = {row["root"]: row["label"] for row in direct.targets()}
    assert labels[str(tmp_path / "movies_en")] == "Movies · English"
    assert labels[str(tmp_path / "shows")] == "Shows · German"
    assert direct.target(str(tmp_path / "shows_en"), "show")["language"] == "English"
    with pytest.raises(ValueError, match="not a folder for shows"):
        direct.target(str(tmp_path / "movies"), "show")
    with pytest.raises(ValueError, match="not one of"):
        direct.target(str(tmp_path / "elsewhere"), "movie")


def test_a_direct_download_takes_no_pipeline_slot():
    class Job:
        def __init__(self, args):
            self.status, self.kind, self.args = "running", "movie", args

    jobs = [Job(["direct-download", "--title", "x"]), Job(["pipeline"])]
    assert webjobs._running_count(jobs) == 1
    assert webjobs._is_direct_job(["direct-download"])
