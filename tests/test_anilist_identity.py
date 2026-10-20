"""Erai show names to AniList entries: the title levels, season markers, settling."""

from __future__ import annotations

import asyncio

import pytest

from bankai.metadata import anilist


@pytest.fixture()
def table(anilist_data):
    """A few shows, as AniList and anime-offline-database have them."""
    add = anilist_data.add
    add(154587, "Sousou no Frieren", english="Frieren: Beyond Journey's End", episodes=28, start="2023-09-29")
    # My Hero Academia: six TV seasons, each its own entry, sequel to sequel.
    # AniList names the sixth by number; Erai calls it "6th Season".
    seasons = [21459, 21856, 100166, 104276, 117193, 139630]
    for number, aid in enumerate(seasons, start=1):
        sequel = seasons[number] if number < len(seasons) else None
        prequel = seasons[number - 2] if number > 1 else None
        add(
            aid,
            "Boku no Hero Academia" if number == 1 else f"Boku no Hero Academia {number}",
            english="My Hero Academia" if number == 1 else f"My Hero Academia Season {number}",
            episodes=13 if number == 1 else 25,
            start=f"{2015 + number}-04-01",
            relations=[
                *([(sequel, "SEQUEL")] if sequel else []),
                *([(prequel, "PREQUEL")] if prequel else []),
                # A movie hangs off the second season; it is no season.
                *([(99000, "SEQUEL", "MOVIE")] if number == 2 else []),
            ],
        )
    add(99000, "Boku no Hero Academia the Movie", format="MOVIE", episodes=1, start="2018-08-03",
        relations=[(21856, "PREQUEL")])
    # Dr. Stone: Science Future, one season in three cours, AniList's three entries.
    add(1001, "Dr. Stone: Science Future", episodes=12, start="2025-01-09", relations=[(1002, "SEQUEL")])
    add(1002, "Dr. Stone: Science Future Dai 2 Cour", episodes=12, start="2025-07-10",
        relations=[(1001, "PREQUEL"), (1003, "SEQUEL")])
    add(1003, "Dr. Stone: Science Future Dai 3 Cour", episodes=None, start="2026-04-02", status="RELEASING",
        relations=[(1002, "PREQUEL")])
    add(2001, "A-Rank Party o Ridatsu Shita Ore wa, Moto Oshiego-tachi to Meikyuu Shinbu o Mezasu",
        start="2025-01-11")
    add(2002, "Arafou Otoko no Isekai Tsuuhan", start="2024-10-03")
    add(2003, "Ao no Miburo", english="Blue Miburo", episodes=24, start="2024-10-19")
    return anilist_data.index()


@pytest.mark.parametrize(
    ("name", "aid", "method"),
    [
        ("Sousou no Frieren", 154587, "exact"),
        # The English name resolves just as well.
        ("Frieren: Beyond Journey's End", 154587, "exact"),
        # Erai writes "wo" and a short vowel where AniList writes "o" and a long one.
        ("A-Rank Party wo Ridatsu shita Ore wa, Moto Oshiego-tachi to Meikyuu Shinbu wo Mezasu", 2001, "loose"),
        ("Arafo Otoko No Isekai Tsuhan", 2002, "loose"),
        # "Romaji | English" names.
        ("Ao no Miburo | Blue Miburo", 2003, "split"),
        # A sequel named by its season, found by walking the sequels.
        ("Boku no Hero Academia 6th Season", 139630, "season"),
        ("Boku no Hero Academia Season 3", 100166, "season"),
        ("Boku no Hero Academia S2", 21856, "season"),
        # A bare "Part N" continues the season it names.
        ("Dr. Stone: Science Future Part 2", 1002, "season"),
        ("Dr. Stone: Science Future Part 3", 1003, "season"),
    ],
)
def test_an_erai_name_resolves_to_one_anilist_entry(table, name, aid, method):
    resolution = anilist.resolve_in(table, name)
    assert resolution.error is None
    assert resolution.anime.id == aid
    assert resolution.method == method


def test_a_movie_between_two_seasons_is_not_counted_as_one(table):
    """The second season's sequels are a movie and the third season: Season 3 is the TV one."""
    assert anilist.resolve_in(table, "Boku no Hero Academia 3rd Season").anime.id == 100166


def test_a_season_the_sequels_do_not_reach_yet_is_not_guessed(table):
    resolution = anilist.resolve_in(table, "Boku no Hero Academia Season 9")
    assert resolution.anime is None
    assert "do not reach" in resolution.error


def test_the_final_season_is_the_last_tv_season_there_is(anilist_data):
    anilist_data.add(10, "Shinsekai", relations=[(11, "SEQUEL")], start="2019-01-01")
    anilist_data.add(11, "Shinsekai 2", relations=[(10, "PREQUEL"), (12, "SEQUEL")], start="2020-01-01")
    anilist_data.add(12, "Shinsekai Kanketsu-hen", relations=[(11, "PREQUEL"), (13, "SEQUEL", "MOVIE")],
                     start="2021-01-01")
    anilist_data.add(13, "Shinsekai Gekijouban", format="MOVIE", episodes=1, relations=[(12, "PREQUEL")],
                     start="2022-01-01")
    table = anilist_data.index()

    assert anilist.resolve_in(table, "Shinsekai Final Season").anime.id == 12
    # "Season 2 Part 2": the second season, then one part further.
    assert anilist.resolve_in(table, "Shinsekai Season 2 Part 2").anime.id == 12


def test_an_erai_language_tag_is_ignored(table):
    assert anilist.resolve_in(table, "Ao no Miburo (JA)").anime.id == 2003


def test_a_name_two_entries_share_is_held_not_guessed(anilist_data):
    """Two main titles that differ only in punctuation: nothing to choose by."""
    anilist_data.add(1, "Hataraku Maou-sama!", start="2013-04-01")
    anilist_data.add(2, "Hataraku Maou-sama!!", start="2022-07-01")

    resolution = anilist.resolve_in(anilist_data.index(), "Hataraku Maou-sama!!")

    assert resolution.anime is None
    assert resolution.candidates == [1, 2]
    assert "ambiguous" in resolution.error


def test_the_newer_marker_forms_and_near_spellings(anilist_data):
    add = anilist_data.add
    add(1, "Spy x Family", relations=[(2, "SEQUEL")], start="2022-04-09")
    add(2, "Spy x Family Part 2 (Cour)", relations=[(1, "PREQUEL")], start="2022-10-01")
    add(3, "Diamond no Ace", relations=[(4, "SEQUEL")], start="2013-10-06")
    add(4, "Diamond no Ace: Act II", relations=[(3, "PREQUEL")], start="2019-04-02")
    add(5, "Ore dake Level Up na Ken", relations=[(6, "SEQUEL")], start="2024-01-06")
    add(6, "Ore dake Level Up na Ken: Arise from the Shadow", relations=[(5, "PREQUEL")], start="2025-01-04")
    add(7, "Kumo desu ga, Nanika?", start="2021-01-08")
    add(8, "Mao", start="2024-04-01")
    add(9, "Rikujou Boueitai Mao-chan", synonyms=["Mao"], start="2002-10-01")
    table = anilist_data.index()

    # "Cour 2" is the second part of the season.
    assert anilist.resolve_in(table, "Spy x Family Cour 2").anime.id == 2
    # Ordinals written out.
    assert anilist.resolve_in(table, "Diamond no Ace Second Season").anime.id == 4
    # A season marker followed by a subtitle.
    assert anilist.resolve_in(table, "Ore dake Level Up na Ken Season 2: Arise from the Shadows").anime.id == 6
    # One word apart only in spacing.
    resolution = anilist.resolve_in(table, "Kumo Desu ga, Nani ka")
    assert (resolution.anime.id, resolution.method) == (7, "loose")
    # A main title wins over another show's synonym.
    assert anilist.resolve_in(table, "Mao").anime.id == 8


def test_a_title_anilist_does_not_have_says_so(table):
    resolution = anilist.resolve_in(table, "Nothing Like Any Anime")
    assert resolution.anime is None
    assert resolution.error


def test_resolve_answers_as_the_index_does(table):
    resolution = asyncio.run(anilist.resolve("Boku no Hero Academia 6th Season"))
    assert resolution.anime.id == 139630


@pytest.mark.parametrize(
    ("aid", "episode", "expected"),
    [
        # Fits its own entry.
        (1001, 5, (1001, 5)),
        (1002, 3, (1002, 3)),
        # The first cour numbered straight on: its 13th is the second cour's first.
        (1001, 13, (1002, 1)),
        (1001, 25, (1003, 1)),
        (1002, 13, (1003, 1)),
        # The last part, still airing, has no end to overrun.
        (1003, 40, (1003, 40)),
        # No sequel: left alone.
        (154587, 30, (154587, 30)),
        # A movie is no season to overflow into.
        (21856, 26, (100166, 1)),
        (139630, 30, (139630, 30)),
    ],
)
def test_an_episode_past_its_entry_lands_in_the_sequel_that_holds_it(table, aid, episode, expected):
    settled, number = anilist.settle_in(table.anime[aid], episode)
    assert (settled.id, number) == expected


def test_settle_fetches_nothing_it_does_not_need(table):
    settled, number = asyncio.run(anilist.settle(table.anime[1001], 14))
    assert (settled.id, number) == (1002, 2)


def test_the_names_of_an_entry(table):
    frieren = table.anime[154587]
    assert frieren.title == "Sousou no Frieren"  # as a release is named
    assert frieren.name == "Frieren: Beyond Journey's End"  # as it is shown
    assert frieren.english == "Frieren: Beyond Journey's End"
    stone = table.anime[1001]
    assert stone.name == stone.title == "Dr. Stone: Science Future"  # no English title: the romaji
