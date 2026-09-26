"""Erai show names to AniDB anime, the way the probe on the real data found them."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from bankai.metadata import anidb

TITLES = ET.fromstring(
    """<animetitles>
  <anime aid="17617">
    <title xml:lang="x-jat" type="main">Sousou no Frieren</title>
    <title xml:lang="en" type="official">Frieren: Beyond Journey's End</title>
  </anime>
  <anime aid="11739">
    <title xml:lang="x-jat" type="main">Boku no Hero Academia</title>
    <title xml:lang="en" type="official">My Hero Academia</title>
  </anime>
  <anime aid="16793">
    <title xml:lang="x-jat" type="main">Boku no Hero Academia (2022)</title>
    <title xml:lang="en" type="official">My Hero Academia Season 6</title>
  </anime>
  <anime aid="19248">
    <title xml:lang="x-jat" type="main">Dr. Stone: Science Future (2025)</title>
  </anime>
  <anime aid="19614">
    <title xml:lang="x-jat" type="main">Dr. Stone: Science Future (2026)</title>
  </anime>
  <anime aid="19000">
    <title xml:lang="x-jat" type="main">Dr. Stone: Science Future</title>
  </anime>
  <anime aid="18852">
    <title xml:lang="x-jat" type="main">A-Rank Party o Ridatsu Shita Ore wa, Moto Oshiego-tachi to Meikyuu Shinbu o Mezasu</title>
  </anime>
  <anime aid="18818">
    <title xml:lang="x-jat" type="main">Arafou Otoko no Isekai Tsuuhan</title>
  </anime>
  <anime aid="18193">
    <title xml:lang="x-jat" type="main">Ao no Miburo</title>
  </anime>
  <anime aid="5001"><title xml:lang="x-jat" type="main">Teppen</title></anime>
  <anime aid="5002"><title xml:lang="x-jat" type="main">Teppen!!!!!!!!!!!!!!!</title></anime>
</animetitles>"""
)

RECORDS = ET.fromstring(
    """<anime-list>
  <anime anidbid="11739" tvdbid="305074" defaulttvdbseason="1" episodeoffset=""><name>Boku no Hero Academia</name></anime>
  <anime anidbid="16793" tvdbid="305074" defaulttvdbseason="6" episodeoffset=""><name>Boku no Hero Academia (2022)</name></anime>
  <anime anidbid="19000" tvdbid="400000" defaulttvdbseason="4" episodeoffset="0"><name>Dr. Stone: Science Future</name></anime>
  <anime anidbid="19248" tvdbid="400000" defaulttvdbseason="4" episodeoffset="12"><name>Dr. Stone: Science Future (2025)</name></anime>
  <anime anidbid="19614" tvdbid="400000" defaulttvdbseason="4" episodeoffset="24"><name>Dr. Stone: Science Future (2026)</name></anime>
</anime-list>"""
)


@pytest.fixture(scope="module")
def table():
    return anidb.build_index(TITLES, RECORDS)


@pytest.mark.parametrize(
    ("name", "aid", "method"),
    [
        ("Sousou no Frieren", 17617, "exact"),
        # The English name resolves just as well.
        ("Frieren: Beyond Journey's End", 17617, "exact"),
        # Erai writes "wo" and a short vowel where AniDB writes "o" and a long one.
        ("A-Rank Party wo Ridatsu shita Ore wa, Moto Oshiego-tachi to Meikyuu Shinbu wo Mezasu", 18852, "loose"),
        ("Arafo Otoko No Isekai Tsuhan", 18818, "loose"),
        # "Romaji | English" names.
        ("Ao no Miburo | Blue Miburo", 18193, "split"),
        # Sequels AniDB names by year, found through their TVDB season.
        ("Boku no Hero Academia 6th Season", 16793, "season 6 part 1"),
        ("Dr. Stone: Science Future Part 2", 19248, "season 4 part 2"),
        ("Dr. Stone: Science Future Part 3", 19614, "season 4 part 3"),
    ],
)
def test_an_erai_name_resolves_to_one_anidb_anime(table, name, aid, method):
    resolution = anidb.resolve_in(table, name)
    assert resolution.error is None
    assert resolution.anime.aid == aid
    assert resolution.method == method


def test_an_erai_language_tag_is_ignored(table):
    assert anidb.resolve_in(table, "Ao no Miburo (JA)").anime.aid == 18193


def test_a_name_two_anime_share_is_held_not_guessed(table):
    titles = ET.fromstring(
        """<animetitles>
  <anime aid="1"><title xml:lang="x-jat" type="main">Hataraku Maou-sama!!</title></anime>
  <anime aid="2"><title xml:lang="x-jat" type="main">Hataraku Maou-sama!! (2023)</title>
    <title xml:lang="x-jat" type="syn">Hataraku Maou-sama!!</title></anime>
</animetitles>"""
    )
    resolution = anidb.resolve_in(anidb.build_index(titles, None), "Hataraku Maou-sama!!")
    assert resolution.anime is None
    assert resolution.candidates == [1, 2]
    assert "ambiguous" in resolution.error


def test_a_title_anidb_does_not_have_says_so(table):
    resolution = anidb.resolve_in(table, "Nothing Like Any Anime")
    assert resolution.anime is None
    assert resolution.error


def test_the_tvdb_era_filing_is_known_for_what_it_already_downloaded(table):
    """Recognising episodes downloaded before the switch, keyed by TVDB."""
    part_two = table.anime[19248]
    assert anidb.legacy_tvdb_episode(part_two, 3) == (400000, 4, 15)
    assert anidb.legacy_tvdb_episode(table.anime[17617], 3) is None  # no Anime-Lists record


@pytest.mark.parametrize(
    ("aid", "episode", "expected"),
    [
        # Fits its own part.
        (19000, 5, (19000, 5)),
        (19248, 3, (19248, 3)),
        # Part 1 numbered straight on: its 13th is part 2's first.
        (19000, 13, (19248, 1)),
        (19000, 25, (19614, 1)),
        # Part 2 numbered from the season start, as Crunchyroll does.
        (19248, 13, (19248, 1)),
        (19248, 24, (19248, 12)),
        # The last part has no end to overrun.
        (19614, 40, (19614, 40)),
        # No Anime-Lists record: left alone.
        (17617, 30, (17617, 30)),
    ],
)
def test_an_episode_past_its_part_lands_in_the_part_that_holds_it(table, aid, episode, expected):
    settled, number = anidb.settle_episode(table, table.anime[aid], episode)
    assert (settled.aid, number) == expected


def test_english_title_is_the_official_english_one(table):
    frieren = table.anime[17617]
    assert frieren.title == "Sousou no Frieren"
    assert frieren.english_title == "Frieren: Beyond Journey's End"
