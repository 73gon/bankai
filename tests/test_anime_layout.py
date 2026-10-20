"""The anime library on disk: one folder per collection, seasons, Season 00, renames, NFO files."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from bankai.config import reset_settings_cache
from bankai.web import anime_collections as collections
from bankai.web import anime_files, anime_layout
from bankai.web.anime_collections import Store
from bankai.web.anime_layout import Layout

S1, S2, S3, S3P2, OVA, MOVIE = 16498, 20958, 99147, 104578, 18397, 20291
KNY1, MUGEN, KNY2 = 101922, 112151, 142329
AOT = "Attack on Titan"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    anime = tmp_path / "anime"
    staging = tmp_path / "staging"
    anime.mkdir()
    (staging / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(anime))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()
    yield SimpleNamespace(anime=anime, staging=staging / "Shows", tmp=tmp_path)
    reset_settings_cache()


@pytest.fixture
def titan(anilist_data):
    anilist_data.add(
        S1, "Shingeki no Kyojin", english=AOT, episodes=25, start="2013-04-07",
        relations=[(S2, "SEQUEL"), (OVA, "SIDE_STORY", "OVA"), (MOVIE, "SIDE_STORY", "MOVIE")],
        streaming={1: "To You, in 2000 Years", 2: "That Day"},
        airing={1: "2013-04-07", 2: "2013-04-14"},
        cover="https://img/aot.jpg", banner="https://img/aot-banner.jpg",
        description="Centuries ago, mankind was slaughtered.<br><br>Now <i>Eren</i> swears revenge.",
    )
    anilist_data.add(
        OVA, "Shingeki no Kyojin OVA", english="Attack on Titan OVA", format="OVA", episodes=8,
        start="2013-12-09", relations=[(S1, "PARENT")],
    )
    anilist_data.add(
        MOVIE, "Shingeki no Kyojin Movie 1: Guren no Yumiya", english="Attack on Titan: Crimson Bow and Arrow",
        format="MOVIE", episodes=1, start="2014-11-22", relations=[(S1, "PARENT")],
    )
    anilist_data.add(
        S2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", episodes=12, start="2017-04-01",
        relations=[(S1, "PREQUEL"), (S3, "SEQUEL")],
    )
    anilist_data.add(
        S3, "Shingeki no Kyojin Season 3", english="Attack on Titan Season 3", episodes=12, start="2018-07-23",
        relations=[(S2, "PREQUEL"), (S3P2, "SEQUEL")],
    )
    anilist_data.add(
        S3P2, "Shingeki no Kyojin Season 3 Part 2", english="Attack on Titan Season 3 Part 2", episodes=10,
        start="2019-04-29", relations=[(S3, "PREQUEL")], cover="https://img/s3p2.jpg",
    )
    return anilist_data


@pytest.fixture
def demon_slayer(anilist_data):
    anilist_data.add(
        KNY1, "Kimetsu no Yaiba", english="Demon Slayer: Kimetsu no Yaiba", episodes=26, start="2019-04-06",
        relations=[(MUGEN, "SEQUEL", "MOVIE")],
    )
    anilist_data.add(
        MUGEN, "Kimetsu no Yaiba Movie: Mugen Ressha-hen", english="Demon Slayer: Mugen Train", format="MOVIE",
        episodes=1, start="2020-10-16", relations=[(KNY1, "PREQUEL"), (KNY2, "SEQUEL", "TV")],
    )
    anilist_data.add(
        KNY2, "Kimetsu no Yaiba: Yuukaku-hen", english="Demon Slayer: Entertainment District Arc", episodes=11,
        start="2021-12-05", relations=[(MUGEN, "PREQUEL")],
    )
    return anilist_data


def _titan_store(*, ova: bool = True, movie: bool = False) -> Store:
    store = Store()
    collections.join([S1], store)
    if ova:
        collections.include(store, 1, OVA)
    if movie:
        collections.include(store, 1, MOVIE)
    return store


def _save(store: Store) -> None:
    with collections.editing() as current:
        current.collections = store.collections
        current.alone = store.alone
        current.next = store.next


def _index(rows: dict[str, list[tuple[int, int]]]) -> None:
    with anime_files.editing() as current:
        current.update({rel: list(pairs) for rel, pairs in rows.items()})


def _touch(path: Path, text: str = "video") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _xml(text: str) -> ET.Element:
    return ET.fromstring(text.encode("utf-8"))


def _place(folder: str, season: int, number: int) -> str:
    return f"{folder}/Season {season:02d}/{folder} - S{season:02d}E{number:02d}.mkv"


# -- Names -------------------------------------------------------------------------------


def test_a_folder_is_its_collections_name_made_safe(anilist_data) -> None:
    anilist_data.add(1, "Re:Zero kara Hajimeru Isekai Seikatsu", english="Re:ZERO -Starting Life in Another World-")
    store = Store()
    store.create([1])

    assert anime_layout.folder_names(store) == {1: "ReZERO -Starting Life in Another World-"}
    assert anime_layout.season_folder(4) == "Season 04"
    assert anime_layout.file_name(AOT, 0, 3, ".MKV") == "Attack on Titan - S00E03.mkv"
    assert anime_layout.file_name("One Piece", 1, 1071, ".mkv") == "One Piece - S01E1071.mkv"


def test_two_collections_of_one_name_are_told_apart_by_year(anilist_data) -> None:
    anilist_data.add(136, "Hunter x Hunter", episodes=62, start="1999-10-16")
    anilist_data.add(11061, "Hunter x Hunter (2011)", english="Hunter x Hunter", episodes=148, start="2011-10-02")
    anilist_data.add(99, "HUNTER X HUNTER", episodes=1, start="2011-12-01")
    store = Store()
    for aid in (136, 11061, 99):
        store.create([aid])

    # The older collection keeps the plain name; one name and year twice, the id.
    assert anime_layout.folder_names(store) == {
        1: "Hunter x Hunter",
        2: "Hunter x Hunter (2011)",
        3: "HUNTER X HUNTER [3]",
    }


# -- Where each episode goes ----------------------------------------------------------------


def test_attack_on_titan_is_four_seasons_and_a_season_00_block_per_side_entry(titan) -> None:
    layout = Layout.build(_titan_store(movie=True), {})

    assert layout.target(S1, 25, ".mkv") == _place(AOT, 1, 25)
    assert layout.target(S2, 1, ".MKV") == _place(AOT, 2, 1)
    assert layout.target(S3, 12, ".mkv") == _place(AOT, 3, 12)
    assert layout.target(S3P2, 1, ".mkv") == _place(AOT, 4, 1)
    # The OVA's eight episodes, then the movie.
    assert layout.target(OVA, 1, ".mkv") == _place(AOT, 0, 1)
    assert layout.target(OVA, 8, ".mkv") == _place(AOT, 0, 8)
    assert layout.target(MOVIE, 1, ".mkv") == _place(AOT, 0, 9)
    assert layout.target(12345, 1, ".mkv") is None


def test_a_movie_between_two_seasons_is_a_special_and_does_not_break_the_chain(demon_slayer) -> None:
    store = Store()
    collections.join([KNY1], store)

    layout = Layout.build(store, {})

    folder = "Demon Slayer Kimetsu no Yaiba"
    assert layout.target(KNY1, 26, ".mkv") == _place(folder, 1, 26)
    assert layout.target(KNY2, 1, ".mkv") == _place(folder, 2, 1)
    assert layout.target(MUGEN, 1, ".mkv") == _place(folder, 0, 1)


def test_a_newcomer_special_does_not_renumber_the_ones_on_disk(titan) -> None:
    store = _titan_store(ova=False, movie=True)
    collections.settle_specials(store.collections[1])
    assert Layout.build(store, {}).target(MOVIE, 1, ".mkv") == _place(AOT, 0, 1)

    # The OVA aired first, but joins later: it goes after the movie.
    collections.include(store, 1, OVA)
    layout = Layout.build(store, {})

    assert layout.target(MOVIE, 1, ".mkv") == _place(AOT, 0, 1)
    assert layout.target(OVA, 1, ".mkv") == _place(AOT, 0, 2)
    # Given both at once, they go as they aired.
    fresh = Layout.build(_titan_store(movie=True), {})
    assert fresh.target(OVA, 1, ".mkv") == _place(AOT, 0, 1)


def test_a_season_00_block_is_as_long_as_the_episodes_on_disk(titan) -> None:
    rows = {"x/ova - 10.mkv": [(OVA, 10)]}

    layout = Layout.build(_titan_store(movie=True), rows)

    assert layout.target(MOVIE, 1, ".mkv") == _place(AOT, 0, 11)


# -- The file index ------------------------------------------------------------------------


def test_a_file_is_known_by_its_path_below_the_library_or_staging(roots) -> None:
    assert anime_files.key(roots.anime / "A" / "Season 01" / "x.mkv") == "A/Season 01/x.mkv"
    assert anime_files.key(roots.staging / "A" / "x.mkv") == "A/x.mkv"
    assert anime_files.key(roots.tmp / "elsewhere" / "x.mkv") is None
    assert anime_files.locations("A/x.mkv") == [roots.anime / "A" / "x.mkv", roots.staging / "A" / "x.mkv"]


def test_a_staged_file_and_its_published_copy_are_one_entry_in_the_index(roots) -> None:
    anime_files.record(roots.staging / "A" / "x.mkv", [(1, 2)])

    assert anime_files.of(roots.anime / "A" / "x.mkv") == [(1, 2)]
    assert anime_files.by_entry() == {1: {2: ["A/x.mkv"]}}

    anime_files.forget(roots.anime / "A" / "x.mkv")
    assert anime_files.load() == {}
    # Outside both roots there is nothing to record.
    anime_files.record(roots.tmp / "elsewhere.mkv", [(1, 1)])
    assert anime_files.load() == {}


def test_on_disk_lists_only_the_episodes_whose_file_exists(roots) -> None:
    _index({"A/a - 01.mkv": [(1, 1)], "A/a - 02.mkv": [(1, 2)], "A/b - 01.mkv": [(2, 1)]})
    staged = _touch(roots.staging / "A" / "a - 01.mkv")
    published = _touch(roots.anime / "A" / "a - 01.mkv")

    assert anime_files.on_disk(1) == {1: [published, staged]}
    assert anime_files.on_disk(2) == {}


# -- Renaming files into place ------------------------------------------------------------------


OLD = "Shingeki no Kyojin/Season 1/Shingeki no Kyojin - 05.mkv"
NEW = _place(AOT, 1, 5)
COMPANIONS = (".en.ass", ".nfo", "-thumb.jpg")


def _old_episode(root: Path) -> None:
    video = _touch(root / OLD)
    for extra in COMPANIONS:
        _touch(video.with_name(video.stem + extra), extra)
    _index({OLD: [(S1, 5)]})


def test_files_move_into_place_with_their_companions_and_the_index_follows(titan, roots) -> None:
    _old_episode(roots.anime)
    moves = anime_layout.plan_moves(Layout.build(_titan_store(), anime_files.load()))
    assert [(move.old, move.new) for move in moves.moves] == [(OLD, NEW)]

    result = anime_layout.apply(moves)

    assert (result["moved"], result["skipped"]) == (1, [])
    video = roots.anime / NEW
    assert video.read_text(encoding="utf-8") == "video"
    for extra in COMPANIONS:
        assert video.with_name(video.stem + extra).read_text(encoding="utf-8") == extra
    # The emptied folders are gone, the library root is not.
    assert not (roots.anime / "Shingeki no Kyojin").exists()
    assert roots.anime.is_dir()
    assert anime_files.load() == {NEW: [(S1, 5)]}
    log = Path(result["log"])
    assert log.parent == anime_layout.moves_dir()
    assert len([json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]) == 4


def test_undo_puts_the_files_and_the_index_back(titan, roots) -> None:
    _old_episode(roots.anime)
    result = anime_layout.apply(anime_layout.plan_moves(Layout.build(_titan_store(), anime_files.load())))

    assert anime_layout.undo(Path(result["log"])) == 4

    video = roots.anime / OLD
    assert video.is_file() and not (roots.anime / NEW).exists()
    for extra in COMPANIONS:
        assert video.with_name(video.stem + extra).is_file()
    assert anime_files.load() == {OLD: [(S1, 5)]}


def test_a_staged_file_is_renamed_where_it_is_staged(titan, roots) -> None:
    _old_episode(roots.staging)

    result = anime_layout.apply(anime_layout.plan_moves(Layout.build(_titan_store(), anime_files.load())))

    assert result["moved"] == 1
    assert (roots.staging / NEW).is_file()
    assert not (roots.anime / NEW).exists()
    assert anime_files.load() == {NEW: [(S1, 5)]}


def test_a_move_into_a_place_another_file_is_leaving_waits_for_it(titan, roots) -> None:
    # The first season's episode sits where the second season's must leave.
    first, second = _place(AOT, 0, 1), _place(AOT, 1, 1)
    _touch(roots.anime / first, "first season")
    _touch(roots.anime / second, "second season")
    _index({first: [(S1, 1)], second: [(S2, 1)]})
    moves = anime_layout.plan_moves(Layout.build(_titan_store(), anime_files.load()))
    assert [(move.old, move.new) for move in moves.moves] == [(first, second), (second, _place(AOT, 2, 1))]

    result = anime_layout.apply(moves)

    assert (result["moved"], result["skipped"]) == (2, [])
    assert (roots.anime / second).read_text(encoding="utf-8") == "first season"
    assert (roots.anime / _place(AOT, 2, 1)).read_text(encoding="utf-8") == "second season"
    assert anime_files.load() == {second: [(S1, 1)], _place(AOT, 2, 1): [(S2, 1)]}

    # Undone, last first.
    assert anime_layout.undo(Path(result["log"])) == 2
    assert (roots.anime / first).read_text(encoding="utf-8") == "first season"
    assert (roots.anime / second).read_text(encoding="utf-8") == "second season"
    assert anime_files.load() == {first: [(S1, 1)], second: [(S2, 1)]}


def test_two_files_that_would_swap_places_stay_and_are_reported(titan, roots) -> None:
    first, ova = _place(AOT, 0, 1), _place(AOT, 1, 1)
    _touch(roots.anime / first, "first season")
    _touch(roots.anime / ova, "ova")
    _index({first: [(S1, 1)], ova: [(OVA, 1)]})

    result = anime_layout.apply(anime_layout.plan_moves(Layout.build(_titan_store(), anime_files.load())))

    assert result["moved"] == 0 and result["log"] is None
    assert len(result["skipped"]) == 2
    assert (roots.anime / first).read_text(encoding="utf-8") == "first season"
    assert anime_files.load() == {first: [(S1, 1)], ova: [(OVA, 1)]}
    assert not list(anime_layout.moves_dir().glob("*.jsonl"))


def test_a_second_file_of_one_episode_and_a_file_of_no_collection_stay_where_they_are(titan) -> None:
    rows = {
        _place(AOT, 1, 5): [(S1, 5)],
        "Shingeki no Kyojin/Shingeki no Kyojin - 05.mkv": [(S1, 5)],
        "Other/Other - 01.mkv": [(777, 1)],
    }

    moves = anime_layout.plan_moves(Layout.build(_titan_store(), rows))

    assert moves.moves == []
    assert moves.duplicates == [("Shingeki no Kyojin/Shingeki no Kyojin - 05.mkv", _place(AOT, 1, 5))]
    assert moves.unplaced == ["Other/Other - 01.mkv"]


def test_only_the_collections_asked_for_are_moved(titan, demon_slayer) -> None:
    store = _titan_store()
    collections.join([KNY1], store)
    rows = {"aot/aot - 01.mkv": [(S1, 1)], "kny/kny - 01.mkv": [(KNY1, 1)]}

    moves = anime_layout.plan_moves(Layout.build(store, rows), cids={1})

    assert [(move.old, move.new) for move in moves.moves] == [("aot/aot - 01.mkv", _place(AOT, 1, 1))]


def test_each_run_of_renames_has_a_log_of_its_own(titan, roots) -> None:
    """Two runs in one second wrote to one log, so undoing the second undid the first too."""
    store = _titan_store()
    _touch(roots.anime / "a" / "a - 01.mkv")
    _touch(roots.anime / "a" / "a - 02.mkv")
    _index({"a/a - 01.mkv": [(S1, 1)], "a/a - 02.mkv": [(S1, 2)]})
    first = anime_layout.apply(anime_layout.plan_moves(Layout.build(store, {"a/a - 01.mkv": [(S1, 1)]})))
    second = anime_layout.apply(anime_layout.plan_moves(Layout.build(store, {"a/a - 02.mkv": [(S1, 2)]})))

    assert first["log"] != second["log"]
    assert anime_layout.undo(Path(second["log"])) == 1
    assert (roots.anime / _place(AOT, 1, 1)).is_file()
    assert (roots.anime / "a" / "a - 02.mkv").is_file()


# -- NFO files ---------------------------------------------------------------------------------


def test_an_episode_nfo_carries_its_anilist_title_and_air_date(titan) -> None:
    nfo = _xml(anime_layout.episode_nfo(AOT, 1, 2, [(S1, 2)]))

    assert nfo.tag == "episodedetails"
    assert [nfo.findtext(tag) for tag in ("title", "showtitle", "season", "episode", "aired")] == [
        "That Day", AOT, "1", "2", "2013-04-14"
    ]
    assert nfo.find("uniqueid").attrib == {"type": "anilist", "default": "true"}
    assert nfo.findtext("uniqueid") == str(S1)
    # Nothing known of an episode: its number, and no date.
    unknown = _xml(anime_layout.episode_nfo(AOT, 1, 7, [(S1, 7)]))
    assert unknown.findtext("title") == "Episode 7" and unknown.find("aired") is None


def test_a_special_is_titled_by_its_entry(titan) -> None:
    ova = _xml(anime_layout.episode_nfo(AOT, 0, 3, [(OVA, 3)]))
    movie = _xml(anime_layout.episode_nfo(AOT, 0, 9, [(MOVIE, 1)]))

    assert (ova.findtext("title"), ova.findtext("aired")) == ("Attack on Titan OVA - Episode 3", "2013-12-09")
    assert (movie.findtext("title"), movie.findtext("aired")) == ("Attack on Titan: Crimson Bow and Arrow", "2014-11-22")
    assert (movie.findtext("season"), movie.findtext("episode")) == ("0", "9")


def test_a_file_of_two_episodes_says_so(titan) -> None:
    nfo = _xml(anime_layout.episode_nfo(AOT, 1, 1, [(S1, 1), (S1, 2)]))

    assert nfo.findtext("title") == "To You, in 2000 Years (+1)"


def test_a_season_is_titled_by_its_entry_without_the_shows_name(titan) -> None:
    part = _xml(anime_layout.season_nfo(4, S3P2, AOT))
    first = _xml(anime_layout.season_nfo(1, S1, AOT))
    specials = _xml(anime_layout.season_nfo(0, None, AOT))

    assert [part.findtext(tag) for tag in ("title", "seasonnumber", "premiered", "uniqueid")] == [
        "Season 3 Part 2", "4", "2019-04-29", str(S3P2)
    ]
    assert first.findtext("title") == "Season 1"
    assert (specials.findtext("title"), specials.findtext("seasonnumber"), specials.find("uniqueid")) == (
        "Specials", "0", None
    )


def test_the_show_nfo_reads_plainly_and_says_whether_it_continues(titan) -> None:
    row = _titan_store().collections[1]

    nfo = _xml(anime_layout.tvshow_nfo(row, AOT))

    assert [nfo.findtext(tag) for tag in ("title", "originaltitle", "year", "premiered", "status", "uniqueid")] == [
        AOT, "Shingeki no Kyojin", "2013", "2013-04-07", "Ended", str(S1)
    ]
    assert nfo.findtext("plot") == "Centuries ago, mankind was slaughtered.\n\nNow Eren swears revenge."

    titan.add(S3P2, "Shingeki no Kyojin Season 3 Part 2", episodes=10, start="2019-04-29", status="RELEASING",
              relations=[(S3, "PREQUEL")])
    assert _xml(anime_layout.tvshow_nfo(row, AOT)).findtext("status") == "Continuing"


def test_nfo_files_are_written_next_to_the_files_in_the_library(titan, roots) -> None:
    store = _titan_store()
    files = {_place(AOT, 1, 1): [(S1, 1)], _place(AOT, 4, 1): [(S3P2, 1)], _place(AOT, 0, 2): [(OVA, 2)]}
    for rel in files:
        _touch(roots.anime / rel)
    _index(files)
    layout = Layout.build(store, anime_files.load())

    written, pictures = anime_layout.write_nfo(layout, [1])

    show = roots.anime / AOT
    assert written == 7  # the show, three seasons, three episodes
    assert _xml((show / "tvshow.nfo").read_text(encoding="utf-8")).findtext("title") == AOT
    titles = {
        season: _xml((show / f"Season {season:02d}" / "season.nfo").read_text(encoding="utf-8")).findtext("title")
        for season in (0, 1, 4)
    }
    assert titles == {0: "Specials", 1: "Season 1", 4: "Season 3 Part 2"}
    episode = _xml((roots.anime / _place(AOT, 1, 1)).with_suffix(".nfo").read_text(encoding="utf-8"))
    assert episode.findtext("title") == "To You, in 2000 Years"
    special = _xml((roots.anime / _place(AOT, 0, 2)).with_suffix(".nfo").read_text(encoding="utf-8"))
    assert (special.findtext("season"), special.findtext("episode")) == ("0", "2")
    assert {(picture.path.relative_to(show).as_posix(), picture.url) for picture in pictures} == {
        ("poster.jpg", "https://img/aot.jpg"),
        ("fanart.jpg", "https://img/aot-banner.jpg"),
        ("Season 01/poster.jpg", "https://img/aot.jpg"),
        ("Season 04/poster.jpg", "https://img/s3p2.jpg"),
    }
    # Nothing changed, nothing written.
    assert anime_layout.write_nfo(layout, [1])[0] == 0


def test_write_nfo_leaves_a_collection_with_no_folder_on_disk_alone(titan, roots) -> None:
    layout = Layout.build(_titan_store(), {_place(AOT, 1, 1): [(S1, 1)]})

    assert anime_layout.write_nfo(layout, [1, 99]) == (0, [])
    assert not (roots.anime / AOT).exists()


def test_a_staged_file_gets_its_nfo_once_it_is_in_the_library(titan, roots) -> None:
    published, staged = _place(AOT, 1, 1), _place(AOT, 1, 2)
    _touch(roots.anime / published)
    _touch(roots.staging / staged)
    _index({published: [(S1, 1)], staged: [(S1, 2)]})

    anime_layout.write_nfo(Layout.build(_titan_store(), anime_files.load()), [1])

    assert (roots.anime / published).with_suffix(".nfo").is_file()
    assert not (roots.anime / staged).with_suffix(".nfo").exists()
    assert not (roots.staging / staged).with_suffix(".nfo").exists()


# -- Keeping it current --------------------------------------------------------------------------


def test_relayout_moves_a_library_into_place_and_writes_its_nfo(titan, roots) -> None:
    old = {
        "Shingeki no Kyojin/Season 1/Shingeki no Kyojin - 01.mkv": [(S1, 1)],
        "Shingeki no Kyojin/Season 2/Shingeki no Kyojin S2 - 01.mkv": [(S2, 1)],
        "Shingeki no Kyojin/Specials/Shingeki no Kyojin OVA - 01.mkv": [(OVA, 1)],
        "Unknown/Unknown - 01.mkv": [(777, 1)],
    }
    for rel in old:
        _touch(roots.anime / rel)
    _touch(roots.anime / "Shingeki no Kyojin/Season 1/Shingeki no Kyojin - 01.de.srt", "subs")
    _index(old)
    _save(_titan_store())

    result = asyncio.run(anime_layout.relayout())

    new = {_place(AOT, 1, 1): [(S1, 1)], _place(AOT, 2, 1): [(S2, 1)], _place(AOT, 0, 1): [(OVA, 1)]}
    assert (result["moved"], result["duplicates"], result["unplaced"]) == (3, [], ["Unknown/Unknown - 01.mkv"])
    assert (result["nfo_written"], result["pictures"]) == (7, 0)
    assert anime_files.load() == {**new, "Unknown/Unknown - 01.mkv": [(777, 1)]}
    for rel in new:
        assert (roots.anime / rel).is_file() and (roots.anime / rel).with_suffix(".nfo").is_file()
    assert (roots.anime / _place(AOT, 1, 1)).with_suffix(".de.srt").read_text(encoding="utf-8") == "subs"
    assert (roots.anime / AOT / "tvshow.nfo").is_file()
    assert not (roots.anime / "Shingeki no Kyojin").exists()
    assert (roots.anime / "Unknown/Unknown - 01.mkv").is_file()

    again = asyncio.run(anime_layout.relayout())
    assert (again["moved"], again["nfo_written"], again["log"]) == (0, 0, None)


def test_renaming_a_collection_moves_its_files_to_a_folder_of_the_new_name(titan, roots) -> None:
    _touch(roots.anime / _place(AOT, 1, 1))
    _index({_place(AOT, 1, 1): [(S1, 1)]})
    store = _titan_store()
    collections.set_name(store, 1, "Shingeki")
    _save(store)

    result = asyncio.run(anime_layout.relayout({1, 99}))

    assert result["moved"] == 1
    assert (roots.anime / _place("Shingeki", 1, 1)).is_file()
    assert _xml((roots.anime / "Shingeki" / "tvshow.nfo").read_text(encoding="utf-8")).findtext("title") == "Shingeki"
    assert anime_files.load() == {_place("Shingeki", 1, 1): [(S1, 1)]}


def test_a_renamed_collection_leaves_no_empty_show_behind(titan, roots) -> None:
    """The old folder's own NFO files and pictures go with its last video; Jellyfin would list it empty."""
    _touch(roots.anime / _place(AOT, 1, 1))
    _index({_place(AOT, 1, 1): [(S1, 1)]})
    _save(_titan_store())
    asyncio.run(anime_layout.relayout({1}))
    _touch(roots.anime / AOT / "poster.jpg", "picture")
    _touch(roots.anime / AOT / "Season 01" / "poster.jpg", "picture")
    store = collections.load()
    collections.set_name(store, 1, "Shingeki")
    _save(store)

    asyncio.run(anime_layout.relayout({1}))

    assert not (roots.anime / AOT).exists()
    assert (roots.anime / _place("Shingeki", 1, 1)).is_file()


def test_a_name_changed_only_in_case_is_renamed_too(titan, roots) -> None:
    """NTFS takes "ATTACK ON TITAN" for "Attack on Titan": the file and its folder must still take the new case."""
    _touch(roots.anime / _place(AOT, 1, 1))
    _index({_place(AOT, 1, 1): [(S1, 1)]})
    store = _titan_store()
    collections.set_name(store, 1, AOT.upper())
    _save(store)

    result = asyncio.run(anime_layout.relayout({1}))

    assert result["moved"] == 1 and result["skipped"] == []
    assert [path.name for path in roots.anime.iterdir()] == [AOT.upper()]
    season = roots.anime / AOT.upper() / "Season 01"
    assert [path.name for path in season.iterdir() if path.suffix == ".mkv"] == [f"{AOT.upper()} - S01E01.mkv"]
    assert anime_files.load() == {_place(AOT.upper(), 1, 1): [(S1, 1)]}


def test_a_folder_left_in_its_old_case_takes_its_collection_s_name(titan, roots) -> None:
    """On a disk that ignores case, files moved into "ATTACK ON TITAN" landed in "Attack on Titan"."""
    probe = roots.anime / "Probe"
    probe.mkdir()
    if not (roots.anime / "PROBE").exists():
        pytest.skip("only a disk that ignores case can hold a folder under the wrong case")
    probe.rmdir()
    _touch(roots.anime / AOT / "Season 01" / f"{AOT.upper()} - S01E01.mkv")
    _index({_place(AOT.upper(), 1, 1): [(S1, 1)]})
    store = _titan_store()
    collections.set_name(store, 1, AOT.upper())
    _save(store)

    asyncio.run(anime_layout.relayout({1}))

    assert [path.name for path in roots.anime.iterdir()] == [AOT.upper()]
    assert anime_files.existing(_place(AOT.upper(), 1, 1))


def test_a_folder_keeps_what_bankai_did_not_write(titan, roots) -> None:
    _touch(roots.anime / _place(AOT, 1, 1))
    _touch(roots.anime / AOT / "notes.txt", "mine")
    _index({_place(AOT, 1, 1): [(S1, 1)]})
    store = _titan_store()
    collections.set_name(store, 1, "Shingeki")
    _save(store)

    asyncio.run(anime_layout.relayout({1}))

    assert (roots.anime / AOT / "notes.txt").is_file()


def test_new_episodes_of_a_later_season_go_into_their_shows_collection(titan, roots) -> None:
    places = asyncio.run(anime_layout.place_new(S3, [1, 2]))

    assert places == {1: _place(AOT, 3, 1), 2: _place(AOT, 3, 2)}
    row = collections.load().collection_of(S3)
    assert row is not None and row.title == S1
    assert anime_layout.collection_of_entry(S1) == row.id


def test_a_side_story_downloaded_on_its_own_is_a_show_of_its_own(titan, roots) -> None:
    places = asyncio.run(anime_layout.place_new(OVA, [1]))

    assert places == {1: _place("Attack on Titan OVA", 1, 1)}


def test_new_specials_past_anilists_count_lengthen_their_block(titan, roots) -> None:
    _save(_titan_store(movie=True))

    places = asyncio.run(anime_layout.place_new(OVA, [10]))

    assert places == {10: _place(AOT, 0, 10)}
    # The order of the specials is fixed now, for the next newcomer.
    assert collections.load().collections[1].specials == [OVA, MOVIE]
