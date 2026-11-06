"""A magnet looked at before a replace: which ticked file each of the torrent's files would take the place of."""

from __future__ import annotations

from bankai.web import magnet_preview as preview


def _files(*names: str) -> list[dict]:
    return [{"path": f"Pack/{name}", "size": 100 + index} for index, name in enumerate(names)]


def test_anime_files_replace_the_ticked_episode_of_their_number():
    files = _files("[Erai-raws] Show - 01 [1080p].mkv", "[Erai-raws] Show - 02 [1080p].mkv", "[Erai-raws] Show - 03 [1080p].mkv", "Show - NCOP.mkv", "readme.txt")
    ticked = ["/lib/Show/Season 01/Show - S01E01.mkv", "/lib/Show/Season 01/Show - S01E03.mkv"]
    rows = {row["name"]: row for row in preview.plan(files, ticked, "anime")}
    assert rows["[Erai-raws] Show - 01 [1080p].mkv"]["replaces"] == "Show - S01E01.mkv"
    assert rows["[Erai-raws] Show - 03 [1080p].mkv"]["replaces"] == "Show - S01E03.mkv"
    assert rows["[Erai-raws] Show - 02 [1080p].mkv"]["why"] == "not one of the ticked episodes"
    assert rows["Show - NCOP.mkv"]["why"] == "no episode number in its name"
    assert rows["readme.txt"]["why"] == "not a video"


def test_a_ticked_double_episode_takes_both_numbers():
    files = _files("Show - 05.mkv", "Show - 06.mkv")
    rows = preview.plan(files, ["/lib/Show/Season 01/Show - S01E05-E06.mkv"], "anime")
    assert [row["replaces"] for row in rows] == ["Show - S01E05-E06.mkv"] * 2


def test_shows_match_by_season_and_episode():
    files = _files("Show.S02E04.1080p.WEB.mkv", "Show.S01E04.1080p.WEB.mkv")
    rows = {row["name"]: row for row in preview.plan(files, ["/lib/Show/Season 02/Show - S02E04.mkv"], "show")}
    assert rows["Show.S02E04.1080p.WEB.mkv"]["replaces"] == "Show - S02E04.mkv"
    assert rows["Show.S01E04.1080p.WEB.mkv"]["replaces"] is None


def test_a_film_is_the_torrents_largest_video():
    files = [{"path": "Film/sample.mkv", "size": 10}, {"path": "Film/Film.2009.mkv", "size": 9000}, {"path": "Film/Film.nfo", "size": 1}]
    rows = {row["name"]: row for row in preview.plan(files, ["/lib/Film (2009)/Film (2009).mkv"], "movie")}
    assert rows["Film.2009.mkv"]["replaces"] == "Film (2009).mkv"
    assert rows["sample.mkv"]["why"] == "not the film's largest video"
    assert rows["Film.nfo"]["why"] == "not a video"


def test_files_of_a_single_file_torrent():
    assert preview.files_of({"info": {"name": "One.mkv", "length": 5}}) == [{"path": "One.mkv", "size": 5}]
