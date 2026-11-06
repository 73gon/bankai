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

    async def metadata(title, tmdb_id=None, kind="show"):
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

    async def metadata(title, tmdb_id=None, kind="show"):
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


def test_a_folder_matched_by_hand_keeps_that_title_until_unmatched(roots, monkeypatch):
    """TMDB has two "Avatar: The Last Airbender"; a folder without a year is matched by hand."""
    monkeypatch.setattr(mas_library, "discover", type("D", (), {"is_configured": staticmethod(lambda: False)}))
    mas_library.set_match("Severance", "show", {"tmdb_id": 95396, "english_title": "Severance", "year": 2022,
                                                "poster_url": "https://image.tmdb.org/p.jpg"})
    card = asyncio.run(mas_library.shows(roots["shows"]))[0]
    assert (card["tmdb_id"], card["year"], card["matched_by_hand"]) == (95396, 2022, True)
    # A film of the same name is not matched by it.
    assert mas_library.matches().keys() == {"show:" + mas_library._name("Severance")}
    mas_library.set_match("Severance", "show", None)
    card = asyncio.run(mas_library.shows(roots["shows"]))[0]
    assert (card["tmdb_id"], card["matched_by_hand"]) == (None, False)
    with pytest.raises(ValueError):
        mas_library.set_match("Severance", "anime", None)


def test_films_of_one_name_and_two_years_are_two_cards_and_an_undated_folder_joins_its_film(tmp_path, monkeypatch):
    """How to Train Your Dragon (2010) and (2025) were one card "stored twice"."""
    rows = [
        {"series": "How to Train Your Dragon (2010)", "root": "/e", "path": "/e/a.mkv"},
        {"series": "How to Train Your Dragon (2025)", "root": "/g", "path": "/g/b.mkv"},
        {"series": "Arrival (2016)", "root": "/g", "path": "/g/c.mkv"},
        {"series": "Arrival", "root": "/e", "path": "/e/d.mkv"},
    ]
    buckets = mas_library._bucket(rows, "movie")
    titles = sorted(tuple(bucket["titles"]) for bucket in buckets.values())
    assert titles == [("Arrival", "Arrival (2016)"), ("How to Train Your Dragon (2010)",), ("How to Train Your Dragon (2025)",)]
    # A folder matched by hand goes by its TMDB title; an old match by name alone not onto another year.
    mas_library.set_match("How to Train Your Dragon", "movie", {"tmdb_id": 10191, "year": 2010})
    assert mas_library._chosen(mas_library.matches(), "How to Train Your Dragon (2025)", "movie") is None
    assert mas_library._chosen(mas_library.matches(), "How to Train Your Dragon (2010)", "movie")["tmdb_id"] == 10191


def test_an_audio_track_that_names_no_language_goes_by_the_folder_it_was_filed_into(monkeypatch):
    inventory = {
        "/g/movies/A/A.mkv": {"tracks": [{"kind": "audio", "lang": "und", "name": None}]},
        "/g/movies_en/B/B.mkv": {"tracks": [{"kind": "audio", "lang": "und", "name": None}]},
        "/g/movies/C/C.mkv": {"tracks": [{"kind": "audio", "lang": "eng", "name": None}]},
    }
    monkeypatch.setattr("bankai.web.track_cleanup.load_inventory", lambda: inventory)
    rows = [{"path": path, "root": path.rsplit("/", 2)[0]} for path in inventory]
    mas_library._tag(rows)
    assert [row["german"] for row in rows] == [True, False, False]


def test_a_file_holding_several_episodes_counts_for_each_of_them():
    """Avatar The Last Airbender - S03E18-E21.mkv is four episodes, none of them missing."""
    from bankai.metadata.tmdb_titles import Episode
    from bankai.web.anime_library import merge_episodes

    files = [{"path": "/s/A - S03E18-E21.mkv", "name": "A - S03E18-E21.mkv", "season_number": 3, "episode": 18,
              "episode_last": 21, "size": 4, "staged": False}]
    roster = [Episode(season=3, episode=n, name=f"E{n}", aired="2008-07-19") for n in range(18, 22)]
    merged = merge_episodes(files, roster, ended=True, german_dubbed={(3, 18), (3, 19), (3, 20), (3, 21)})
    rows = {row["episode"]: row for row in merged["episodes"]}
    assert not any(row.get("missing") for row in rows.values())
    assert rows[20]["part_of"] == "A - S03E18-E21.mkv" and rows[20]["german_dub"] is True
    assert merged["completion_state"] == "complete"


def test_english_subtitles_count_only_when_they_carry_all_of_it(monkeypatch):
    inventory = {
        "/s/A.mkv": {"tracks": [{"kind": "audio", "lang": "eng"}, {"kind": "subtitles", "lang": "eng", "forced": False}]},
        "/s/B.mkv": {"tracks": [{"kind": "audio", "lang": "eng"}, {"kind": "subtitles", "lang": "eng", "forced": True}]},
        "/s/C.mkv": {"tracks": [{"kind": "audio", "lang": "eng"}, {"kind": "subtitles", "lang": "und", "name": "English"}]},
        "/s/D.mkv": {"tracks": [{"kind": "audio", "lang": "eng"}, {"kind": "subtitles", "lang": "eng", "name": "English (Forced)"}]},
        "/s/E.mkv": {"tracks": [{"kind": "audio", "lang": "eng"}]},
        "/s/F.mkv": {"unreadable": True},
    }
    monkeypatch.setattr("bankai.web.track_cleanup.load_inventory", lambda: inventory)
    rows = [{"path": path, "root": "/s"} for path in inventory]
    mas_library._tag(rows)
    assert [row["english_subs"] for row in rows] == [True, False, True, False, False, None]


def test_the_episode_strip_says_where_the_library_has_the_show_and_in_which_language(monkeypatch):
    """Season 1 missing, season 2 on disk in German, English with and without English
    subtitles and one not read yet, season 3 still to air; a special is no gap."""
    from bankai.metadata.tmdb_titles import Episode
    from bankai.web.anime_library import merge_episodes

    inventory = {
        "/s/X/Season 02/X - S02E01.mkv": {"tracks": [{"kind": "audio", "lang": "ger"}]},
        "/s/X/Season 02/X - S02E02.mkv": {"tracks": [{"kind": "audio", "lang": "eng"}, {"kind": "subtitles", "lang": "eng"}]},
        "/s/X/Season 02/X - S02E03.mkv": {"tracks": [{"kind": "audio", "lang": "eng"}]},
    }
    monkeypatch.setattr("bankai.web.track_cleanup.load_inventory", lambda: inventory)
    files = [
        {"path": path, "root": "/s", "name": path.rsplit("/", 1)[1], "season_number": 2, "episode": number, "size": 1, "staged": False}
        for number, path in enumerate([*inventory, "/s/X/Season 02/X - S02E04.mkv"], start=1)
    ]
    mas_library._tag(files)
    roster = [Episode(season, n, aired="2020-01-01") for season, count in ((1, 2), (2, 4)) for n in range(1, count + 1)]
    roster += [Episode(3, 1, aired="2099-01-01"), Episode(0, 1, aired="2020-01-01")]
    merged = merge_episodes(
        files, roster, ended=False,
        german_dubbed={(row["season_number"], row["episode"]) for row in files if row["german"]},
    )
    assert mas_library.episode_strip(merged["episodes"]) == [
        {"label": "Season 1", "states": "mm"},
        {"label": "Season 2", "states": "genu"},
        {"label": "Season 3", "states": "t"},
    ]
    # The bar counts what the show's count reads.
    assert sum(len(season["states"]) for season in mas_library.episode_strip(merged["episodes"])) == merged["total_count"]


def test_each_show_card_carries_its_strip(roots):
    cards = asyncio.run(mas_library.shows(roots["shows"], include_episodes=False))
    # Its tracks are not read in the test's inventory: on disk, languages unknown.
    assert cards[0]["strip"] == [{"label": "Season 1", "states": "uu"}]


def test_english_subtitles_in_a_file_beside_the_episode_count_as_the_followed_view_counts_them(tmp_path, monkeypatch):
    """Dexter S03-S05: English audio, no subtitle track, an .srt beside each episode -- the bar
    called them "without English subtitles" while the followed show's drawer said English."""
    season = tmp_path / "Dexter (2006)" / "Season 03"
    season.mkdir(parents=True)
    names = ["Dexter - S03E01.mkv", "Dexter - S03E02.mkv", "Dexter - S03E10.mkv"]
    for name in names:
        (season / name).write_bytes(b"x")
    (season / "Dexter - S03E01.srt").write_text("1")
    (season / "Dexter - S03E02.de.srt").write_text("1")
    # S03E1's prefix is no file of S03E10's.
    (season / "Dexter - S03E1.srt").write_text("1")
    inventory = {str(season / name): {"tracks": [{"kind": "audio", "lang": "eng"}]} for name in names}
    monkeypatch.setattr("bankai.web.track_cleanup.load_inventory", lambda: inventory)
    rows = [{"path": path, "root": str(tmp_path)} for path in inventory]
    mas_library._tag(rows)
    assert [row["english_subs"] for row in rows] == [True, False, False]
