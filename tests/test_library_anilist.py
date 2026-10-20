"""The anime library by AniList: one card per collection, a tab per entry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankai.config import reset_settings_cache
from bankai.web import anime_collections as collections
from bankai.web import anime_files, anime_library, erai

S1, S2, S3, S3P2, OVA = 16498, 20958, 99147, 104578, 18397
FMA = 5114
AOT = "Attack on Titan"


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    anime = tmp_path / "anime"
    staging = tmp_path / "staging"
    anime.mkdir()
    (staging / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(anime))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    reset_settings_cache()
    yield SimpleNamespace(anime=anime, staging=staging / "Shows", tmp=tmp_path)
    reset_settings_cache()


@pytest.fixture
def titan(anilist_data):
    """Attack on Titan, a few episodes each, so what is missing can be counted."""
    anilist_data.add(
        S1, "Shingeki no Kyojin", english=AOT, episodes=4, start="2013-04-07",
        relations=[(S2, "SEQUEL"), (OVA, "SIDE_STORY", "OVA")],
        streaming={1: "To You, in 2000 Years", 2: "That Day"}, airing={1: "2013-04-07"},
        cover="https://img/aot.jpg",
    )
    anilist_data.add(OVA, "Shingeki no Kyojin OVA", english="Attack on Titan OVA", format="OVA", episodes=2,
                     start="2013-12-09", relations=[(S1, "PARENT")])
    anilist_data.add(S2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", episodes=2,
                     start="2017-04-01", relations=[(S1, "PREQUEL"), (S3, "SEQUEL")])
    anilist_data.add(S3, "Shingeki no Kyojin Season 3", english="Attack on Titan Season 3", episodes=2,
                     start="2018-07-23", relations=[(S2, "PREQUEL"), (S3P2, "SEQUEL")])
    anilist_data.add(S3P2, "Shingeki no Kyojin Season 3 Part 2", english="Attack on Titan Season 3 Part 2",
                     episodes=2, start="2019-04-29", relations=[(S3, "PREQUEL")])
    anilist_data.add(FMA, "Hagane no Renkinjutsushi: Fullmetal Alchemist",
                     english="Fullmetal Alchemist: Brotherhood", episodes=3, start="2009-04-05")
    return anilist_data


def _titan_collection() -> None:
    with collections.editing() as store:
        collections.join([S1], store)
        collections.include(store, 1, OVA)


def _place(folder: str, season: int, number: int) -> str:
    return f"{folder}/Season {season:02d}/{folder} - S{season:02d}E{number:02d}.mkv"


def _entry(root: Path, rel: str, *, staged: bool = False) -> dict:
    parts = Path(rel).parts
    return {
        "path": str(root.joinpath(*parts)),
        "rel_path": rel,
        "name": parts[-1],
        "series": parts[0],
        "season": parts[1] if len(parts) > 2 else None,
        "size": 1,
        "mtime": 0,
        "staged": staged,
        "stage": "final",
        "transfer_status": "idle",
    }


def _files(root: Path, rows: dict[str, list[tuple[int, int]]]) -> list[dict]:
    """Library entries for these keys, written to the file index as these episodes."""
    with anime_files.editing() as index:
        index.update({rel: list(pairs) for rel, pairs in rows.items() if pairs})
    return [_entry(root, rel) for rel in rows]


def _follow(*aids: int) -> None:
    state = erai._default_state()
    state["anime"] = {str(aid): {"anilist_id": aid} for aid in aids}
    erai._save_state(state)


def _blacklist(*aids: int) -> None:
    erai._save_policies({f"anilist:{aid}": {"mode": "blacklisted", "anilist_id": aid} for aid in aids})


def _shows(entries: list[dict], root: Path, **kwargs) -> dict[str, dict]:
    shows = asyncio.run(anime_library.group_shows(entries, root, **kwargs))
    by_key = {show["key"]: show for show in shows}
    assert len(by_key) == len(shows), "card keys must be unique"
    return by_key


def _titan_files(library) -> list[dict]:
    return _files(
        library.anime,
        {
            _place(AOT, 1, 1): [(S1, 1)],
            _place(AOT, 1, 2): [(S1, 2)],
            _place(AOT, 2, 1): [(S2, 1)],
            _place(AOT, 0, 1): [(OVA, 1)],
        },
    )


# -- Cards -----------------------------------------------------------------------------


def test_a_collection_is_one_card_with_a_tab_per_season_and_one_for_the_specials(titan, library) -> None:
    _titan_collection()

    show = _shows(_titan_files(library), library.anime)[AOT]

    assert (show["title"], show["folders"], show["unmatched"], show["numbering"]) == (AOT, [AOT], False, "anilist")
    assert (show["anilist_id"], show["anilist_ids"]) == (S1, [S1, S2, S3, S3P2, OVA])
    assert (show["collection_id"], show["collection_name_custom"]) == (1, False)
    tabs = {row["season_number"]: row["season"] for row in show["episodes"]}
    assert tabs == {1: "S1 · " + AOT, 2: "S2 · Season 2", 3: "S3 · Season 3", 4: "S4 · Season 3 Part 2", 5: "Specials"}
    assert show["season_anilist"]["4"] == [{"anilist_id": S3P2, "title": "Attack on Titan Season 3 Part 2"}]
    assert show["season_anilist"]["5"] == [{"anilist_id": OVA, "title": "Attack on Titan OVA"}]
    assert (show["poster_url"], show["year"], show["source_title"]) == ("https://img/aot.jpg", 2013, "Shingeki no Kyojin")
    assert (show["episode_count"], show["size"], show["staged_count"]) == (4, 4, 0)


def test_an_arranged_season_is_one_tab_numbered_as_on_disk(titan, library) -> None:
    _titan_collection()
    with collections.editing() as store:
        collections.arrange(store, 1, [[S1], [S2, S3], [S3P2]], [OVA])
    files = _files(library.anime, {_place(AOT, 2, 3): [(S3, 1)]})

    show = _shows(files, library.anime)[AOT]

    tabs = {row["season_number"]: row["season"] for row in show["episodes"]}
    assert tabs[2] == "S2 · Season 2 +1"
    # Season 3's first episode follows Season 2's two in the season they make up.
    row = next(row for row in show["episodes"] if row.get("path"))
    assert (row["season_number"], row["episode"], row["anilist_id"], row["anilist_episode"]) == (2, 3, S3, 1)
    missing = next(row for row in show["episodes"] if (row["season_number"], row["episode"]) == (2, 4))
    assert (missing["missing"], missing["anilist_id"], missing["anilist_episode"]) == (True, S3, 2)
    assert [entry["anilist_id"] for entry in show["season_anilist"]["2"]] == [S2, S3]


def test_missing_episodes_are_counted_from_anilist(titan, library) -> None:
    _titan_collection()

    show = _shows(_titan_files(library), library.anime)[AOT]

    # The OVA's missing episode is listed, but a show is complete without it.
    assert (show["downloaded_count"], show["total_count"]) == (4, 11)
    assert show["completion_state"] == "partial"
    assert show["metadata_available"] is True
    missing = [(row["season_number"], row["episode"]) for row in show["episodes"] if row["missing"]]
    assert missing == [(1, 3), (1, 4), (2, 2), (3, 1), (3, 2), (4, 1), (4, 2), (5, 2)]
    # A finished entry's episodes have aired, dated or not: missing, not to be announced.
    assert not any(row["tba"] for row in show["episodes"])
    first = next(row for row in show["episodes"] if (row["season_number"], row["episode"]) == (1, 1))
    assert (first["episode_title"], first["aired"]) == ("To You, in 2000 Years", "2013-04-07")


def test_the_collections_entries_say_where_each_one_is(titan, library) -> None:
    _titan_collection()

    show = _shows(_titan_files(library), library.anime)[AOT]

    assert [(row["anilist_id"], row["owned"], row["season"]) for row in show["collection_entries"]] == [
        (S1, True, "Season 1"),
        (S2, True, "Season 2"),
        (S3, False, "Season 3"),
        (S3P2, False, "Season 4"),
        (OVA, True, "Specials"),
    ]
    assert show["collection_entries"][3]["title"] == "Attack on Titan Season 3 Part 2"


def test_a_name_given_by_hand_names_the_card(titan, library) -> None:
    _titan_collection()
    with collections.editing() as store:
        collections.set_name(store, 1, "Shingeki")

    shows = _shows(_files(library.anime, {_place("Shingeki", 1, 1): [(S1, 1)]}), library.anime)

    assert shows["Shingeki"]["title"] == "Shingeki"
    assert shows["Shingeki"]["collection_name_custom"] is True


def test_files_no_anilist_entry_is_known_for_are_a_card_of_their_folder(titan, library) -> None:
    entries = _files(library.anime, {"Mystery Show/Season 01/Mystery Show - S01E03.mkv": []})

    show = _shows(entries, library.anime)["Mystery Show"]

    assert (show["unmatched"], show["title"], show["folders"]) == (True, "Mystery Show", ["Mystery Show"])
    assert (show["anilist_id"], show["anilist_ids"], show["collection_id"]) == (None, [], None)
    assert (show["numbering"], show["collection_entries"], show["season_anilist"]) == ("files", [], {})
    assert [(row["season_number"], row["episode"]) for row in show["episodes"]] == [(1, 3)]
    # Nothing to say how many there are: no claim to be complete.
    assert show["completion_state"] == "unknown"


def test_stray_files_in_a_collections_folder_are_an_unmatched_card_beside_it(titan, library) -> None:
    _titan_collection()
    entries = _titan_files(library) + _files(library.anime, {f"{AOT}/Season 01/stray - 07.mkv": []})

    shows = _shows(entries, library.anime)

    assert set(shows) == {AOT, f"{AOT} (unmatched)"}
    stray = shows[f"{AOT} (unmatched)"]
    assert (stray["unmatched"], stray["title"], stray["episode_count"]) == (True, AOT, 1)
    assert shows[AOT]["episode_count"] == 4
    assert [show["key"] for show in _shows(entries, library.anime, only_key=f"{AOT} (unmatched)").values()] == [
        f"{AOT} (unmatched)"
    ]


def test_a_followed_entry_with_nothing_on_disk_still_has_a_card(titan, library) -> None:
    _follow(FMA)

    show = _shows([], library.anime)["Fullmetal Alchemist: Brotherhood"]

    assert (show["folders"], show["episode_count"], show["anilist_ids"]) == ([], 0, [FMA])
    assert (show["downloaded_count"], show["total_count"], show["completion_state"]) == (0, 3, "empty")
    assert show["collection_entries"] == [
        {"anilist_id": FMA, "title": "Fullmetal Alchemist: Brotherhood", "owned": False, "season": None}
    ]


def test_a_followed_entry_of_a_collection_is_that_collections_card(titan, library) -> None:
    _titan_collection()
    _follow(S3)

    shows = _shows([], library.anime)

    assert list(shows) == [AOT]
    # Its seasons' episodes; the OVA's do not count while none is on disk.
    assert (shows[AOT]["episode_count"], shows[AOT]["total_count"]) == (0, 10)


def test_a_blacklisted_followed_entry_gets_no_card(titan, library) -> None:
    _follow(FMA)
    _blacklist(FMA)

    assert _shows([], library.anime) == {}


def test_two_cards_of_one_name_have_keys_of_their_own(anilist_data, library) -> None:
    anilist_data.add(136, "Hunter x Hunter", episodes=2, start="1999-10-16")
    anilist_data.add(11061, "Hunter x Hunter (2011)", english="Hunter x Hunter", episodes=2, start="2011-10-02")
    with collections.editing() as store:
        collections.join([136], store)
    _follow(11061)
    entries = _files(library.anime, {_place("Hunter x Hunter", 1, 1): [(136, 1)]})

    shows = _shows(entries, library.anime)

    assert {key: show["anilist_id"] for key, show in shows.items()} == {
        "Hunter x Hunter": 136,
        "Hunter x Hunter (2)": 11061,
    }
    # Each key asks for exactly its own card back.
    for key, show in shows.items():
        assert [one["anilist_id"] for one in _shows(entries, library.anime, only_key=key).values()] == [show["anilist_id"]]


def test_one_card_can_be_asked_for_without_its_episodes(titan, library) -> None:
    _titan_collection()
    _follow(FMA)

    shows = _shows(_titan_files(library), library.anime, only_key=AOT, include_episodes=False)

    assert list(shows) == [AOT]
    assert shows[AOT]["episodes"] == []
    assert shows[AOT]["downloaded_count"] == 4


def test_the_release_state_is_read_once_for_the_whole_library(titan, library, monkeypatch) -> None:
    reads: list[int] = []
    load = erai._load_state

    def counted():
        reads.append(1)
        return load()

    monkeypatch.setattr(erai, "_load_state", counted)
    _follow(FMA)
    entries = _files(library.anime, {f"Show {n}/Show {n} - S01E01.mkv": [] for n in range(10)})

    assert len(_shows(entries, library.anime)) == 11
    assert len(reads) == 1


def test_the_anilist_title_is_shown_under_the_name_only_when_it_says_something_else(anilist_data, library) -> None:
    anilist_data.add(8074, "Akame ga Kill!", english="Akame ga Kill")
    with collections.editing() as store:
        collections.join([8074], store)

    show = _shows(_files(library.anime, {_place("Akame ga Kill", 1, 1): [(8074, 1)]}), library.anime)["Akame ga Kill"]

    assert show["source_title"] == ""


def test_an_entry_still_airing_is_upcoming_not_missing(anilist_data, library) -> None:
    anilist_data.add(1, "Airing Show", episodes=3, start="2020-01-01", status="RELEASING",
                     airing={1: "2020-01-01", 2: "2020-01-08", 3: "2099-01-01"})
    with collections.editing() as store:
        collections.join([1], store)
    entries = _files(library.anime, {_place("Airing Show", 1, 1): [(1, 1)], _place("Airing Show", 1, 2): [(1, 2)]})

    show = _shows(entries, library.anime)["Airing Show"]

    assert show["completion_state"] == "upcoming"
    last = show["episodes"][-1]
    assert (last["episode"], last["missing"], last["tba"], last["aired"]) == (3, True, True, "2099-01-01")


def test_an_entrys_roster_is_dated_by_its_start_once_it_has_finished(anilist_data) -> None:
    anilist_data.add(1, "Finished", episodes=3, start="2010-04-01", airing={1: "2010-04-01"}, streaming={2: "Two"})
    anilist_data.add(2, "Airing", episodes=2, start="2026-10-01", status="RELEASING", airing={1: "2026-10-01"})
    anilist_data.add(3, "Unknown Length", episodes=None)

    assert anime_library.roster(1) == [
        anime_library.EntryEpisode(1, None, "2010-04-01"),
        anime_library.EntryEpisode(2, "Two", "2010-04-01"),
        anime_library.EntryEpisode(3, None, "2010-04-01"),
    ]
    assert [episode.aired for episode in anime_library.roster(2)] == ["2026-10-01", None]
    assert anime_library.roster(3) is None


# -- Matching by hand --------------------------------------------------------------------


def test_an_unmatched_folders_files_become_an_entrys_episodes_by_their_numbers(titan, library) -> None:
    folder = library.anime / "Mystery Show"
    paths = [
        folder / "Mystery Show - 03.mkv",
        folder / "Mystery Show S01E04.mkv",
        folder / "Mystery Show - Extras.mkv",
        library.tmp / "elsewhere" / "Mystery Show - 05.mkv",
    ]

    result = anime_library.match_files(paths, FMA)

    assert result == {"matched": [str(paths[0]), str(paths[1])], "skipped": [str(paths[2]), str(paths[3])]}
    assert anime_files.load() == {
        "Mystery Show/Mystery Show - 03.mkv": [(FMA, 3)],
        "Mystery Show/Mystery Show S01E04.mkv": [(FMA, 4)],
    }
    shows = _shows([_entry(library.anime, "Mystery Show/Mystery Show - 03.mkv")], library.anime)
    assert list(shows) == ["Fullmetal Alchemist: Brotherhood"]


def test_every_file_of_one_entry_can_become_anothers(titan, library) -> None:
    with anime_files.editing() as index:
        index.update({"a/1.mkv": [(S1, 1)], "a/2.mkv": [(S1, 2), (OVA, 1)], "b/1.mkv": [(S2, 1)]})

    assert anime_library.rematch_entry(S1, FMA) == 2

    assert anime_files.load() == {"a/1.mkv": [(FMA, 1)], "a/2.mkv": [(FMA, 2), (OVA, 1)], "b/1.mkv": [(S2, 1)]}


# -- Review and queue rows ------------------------------------------------------------------


def test_review_cards_show_their_own_entrys_name_year_and_cover(titan) -> None:
    rows = [
        {"key": f"anilist:{S2}", "source_title": "Shingeki no Kyojin S2", "anilist_id": S2, "tvdb_id": 1},
        {"key": "nobody", "source_title": "Nobody Knows", "anilist_poster_url": "https://img/search.jpg"},
        {"key": "anilist:777", "source_title": "Gone", "anilist_id": 777, "anilist_title": "Gone Away",
         "anilist_poster_url": "https://img/gone.jpg"},
    ]

    asyncio.run(anime_library.enrich_review_rows(rows))

    assert [row["title"] for row in rows] == ["Attack on Titan Season 2", "Nobody Knows", "Gone Away"]
    assert [row.get("year") for row in rows] == [2017, None, None]
    assert [row["poster_url"] for row in rows] == [None, "https://img/search.jpg", "https://img/gone.jpg"]
    assert all("tvdb_id" not in row for row in rows)


def test_queue_rows_wear_their_entrys_cover_and_name(titan) -> None:
    rows = [{"anilist_id": str(S1), "series_title": "Shingeki"}, {"anilist_id": None, "series_title": "Other"}]

    asyncio.run(anime_library.queue_covers(rows))

    assert rows[0] == {"anilist_id": str(S1), "series_title": AOT, "poster_url": "https://img/aot.jpg"}
    assert rows[1] == {"anilist_id": None, "series_title": "Other"}


# -- Batches, carried over from the AniDB library ---------------------------------------------


def test_a_batch_torrent_is_numbered_file_by_file(tmp_path: Path) -> None:
    from bankai.processor import anime as processor

    one = [tmp_path / "Show - 05.mkv"]
    batch = [tmp_path / f"Show - {n:02d}.mkv" for n in range(1, 13)]
    assert processor.batch_override(5, one) == 5
    assert processor.batch_override(1, batch) is None
    assert processor.batch_override(None, batch) is None
    assert [processor.episode_number(path.name) for path in batch[:3]] == [1, 2, 3]


def test_a_batch_release_name_reads_like_its_first_episode() -> None:
    assert erai._erai_identity_name("[Erai-raws] Date A Live V - 01 ~ 12 [1080p CR WEBRip HEVC AAC][MultiSub]") == (
        "Date A Live V",
        1,
    )
    assert erai._erai_identity_name("[Erai-raws] Date A Live V - 05 [1080p][HEVC]") == ("Date A Live V", 5)
    assert erai._erai_identity_name("[Erai-raws] Date A Live V [1080p]") is None
    # Admission still reads a batch as no single episode.
    assert erai._erai_name_episode("[Erai-raws] Date A Live V - 01 ~ 12 [1080p]") is None


def test_an_entry_in_no_collection_yet_is_a_card_of_its_own(titan, library) -> None:
    """Until the maintenance pass joins it to its show's collection."""
    entries = _files(library.anime, {"Loose/Loose - 01.mkv": [(S2, 1)]})

    shows = _shows(entries, library.anime)

    assert list(shows) == ["Attack on Titan Season 2"]
    show = shows["Attack on Titan Season 2"]
    assert (show["collection_id"], show["anilist_ids"], show["folders"]) == (None, [S2], ["Loose"])
    assert [row["season"] for row in show["episodes"]] == ["Attack on Titan Season 2"] * 2


def test_a_single_file_numbered_0_is_the_entrys_first_episode(titan, library) -> None:
    path = library.anime / "One-Punch Man (2015)" / "One Punch Man (2025) - 00.mkv"
    path.parent.mkdir(parents=True)
    path.write_text("video")

    result = anime_library.match_files([path], S1)

    assert result["matched"] == [str(path)] and anime_files.of(path) == [(S1, 1)]


def test_several_files_numbered_0_are_reported_not_guessed(titan, library) -> None:
    paths = [library.anime / "Extras" / f"Extra {n} - 00.mkv" for n in (1, 2)]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("video")

    result = anime_library.match_files(paths, S1)

    assert result["matched"] == [] and len(result["skipped"]) == 2
