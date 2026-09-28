"""The anime library by AniDB entry: Bleach and its sequel are two cards."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from bankai.metadata import anidb, anidb_art
from bankai.metadata.tvdb import TVDBEpisode
from bankai.web import anime_library, discover, erai, shoko_catalog

TITLES = ET.fromstring(
    """<animetitles>
  <anime aid="2369"><title xml:lang="x-jat" type="main">Bleach</title></anime>
  <anime aid="15449">
    <title xml:lang="x-jat" type="main">Bleach: Sennen Kessen Hen</title>
    <title xml:lang="en" type="official">Bleach: Thousand-Year Blood War</title>
  </anime>
  <anime aid="17765"><title xml:lang="x-jat" type="main">Bleach: Sennen Kessen Hen - Ketsubetsu Tan</title></anime>
  <anime aid="18220"><title xml:lang="x-jat" type="main">Bleach: Sennen Kessen Hen - Soukoku Tan</title></anime>
</animetitles>"""
)
# TYBW is TVDB's season 17 of Bleach; each cour starts partway into it.
RECORDS = ET.fromstring(
    """<anime-list>
  <anime anidbid="2369" tvdbid="74796" defaulttvdbseason="a" episodeoffset=""/>
  <anime anidbid="15449" tvdbid="74796" defaulttvdbseason="17" episodeoffset="0"/>
  <anime anidbid="17765" tvdbid="74796" defaulttvdbseason="17" episodeoffset="2"/>
  <anime anidbid="18220" tvdbid="74796" defaulttvdbseason="17" episodeoffset="4"/>
</anime-list>"""
)
TABLE = anidb.build_index(TITLES, RECORDS)

# Bleach itself is four episodes over two TVDB seasons here, TYBW six.
ROSTER = [
    TVDBEpisode(season=1, episode=1, absolute_number=1, name="B1", aired="2004-10-05"),
    TVDBEpisode(season=1, episode=2, absolute_number=2, name="B2", aired="2004-10-12"),
    TVDBEpisode(season=2, episode=3, absolute_number=3, name="B3", aired="2005-01-01"),
    TVDBEpisode(season=2, episode=4, absolute_number=4, name="B4", aired="2005-01-08"),
    *(
        TVDBEpisode(season=17, episode=n, absolute_number=366 + n, name=f"T{n}", aired=f"2022-10-{10 + n}")
        for n in range(1, 7)
    ),
]

BLEACH = "Bleach"
TYBW = "Bleach - Thousand Year Blood War"
FILES = {
    (BLEACH, "Season 01"): ["Bleach - S01E001.mkv", "Bleach - S01E002.mkv"],
    (BLEACH, "Season 02"): ["Bleach - S02E003.mkv", "Bleach - S02E004.mkv"],
    (TYBW, "Season 01 - Sennen Kessen Hen"): ["S17E01.mkv", "S17E02.mkv"],
    (TYBW, "Season 02 - Sennen Kessen Hen - Ketsubetsu Tan"): ["S17E03.mkv"],
    (TYBW, "Season 03 - Sennen Kessen Hen - Soukoku Tan"): ["S17E05.mkv", "S17E06.mkv"],
}


def _entries() -> list[dict]:
    return [
        {
            "path": f"/library/{folder}/{season}/{name}",
            "rel_path": f"{folder}/{season}/{name}",
            "name": name,
            "series": folder,
            "season": season,
            "size": 1,
            "mtime": 0,
            "staged": False,
            "stage": "final",
            "transfer_status": "idle",
        }
        for (folder, season), names in FILES.items()
        for name in names
    ]


@pytest.fixture
def library(monkeypatch):
    async def index():
        return TABLE

    async def metadata(title, tvdb_id=None):
        return {"english_title": "Bleach", "tvdb_id": 74796, "poster_url": "tvdb-poster", "year": 2004}

    async def roster(tvdb_id):
        return ROSTER

    async def second_name(*args):
        return ""

    monkeypatch.setattr(anidb, "index", index)
    monkeypatch.setattr(erai, "_load_state", erai._default_state)
    monkeypatch.setattr(erai, "_policy_tvdb_ids", lambda: set())
    monkeypatch.setattr(anime_library, "known_ids", lambda: {})
    monkeypatch.setattr(anime_library, "_nfo_id", lambda path: None)
    monkeypatch.setattr(anime_library, "probed_codecs", lambda files: {})
    monkeypatch.setattr(anime_library, "german_dubbed_episodes", lambda files: set())
    monkeypatch.setattr(anime_library, "show_metadata", metadata)
    monkeypatch.setattr(anime_library, "episode_roster", roster)
    monkeypatch.setattr(anime_library, "second_name", second_name)
    monkeypatch.setattr(anime_library, "flush_persistent_cache", lambda: None)
    monkeypatch.setattr(anime_library, "load_prefs", lambda: {})
    monkeypatch.setattr(discover, "is_configured", lambda: True)
    monkeypatch.setattr(anidb_art, "cover", lambda aid: f"cover:{aid}")

    def run(catalog=None, only_key=None):
        return asyncio.run(
            anime_library.group_shows(
                _entries(), Path("/library"), catalog=catalog, only_key=only_key
            )
        )

    return run


def test_bleach_and_its_sequel_are_two_cards(library):
    shows = {show["key"]: show for show in library()}
    assert set(shows) == {BLEACH, TYBW}
    bleach, tybw = shows[BLEACH], shows[TYBW]

    # Bleach counts only its own episodes, not TVDB's season 17.
    assert bleach["anidb_id"] == 2369
    assert (bleach["downloaded_count"], bleach["total_count"]) == (4, 4)
    assert bleach["completion_state"] == "complete"
    assert bleach["numbering"] == "anidb"

    # The sequel is its three cours, one tab each, counted per cour.
    assert tybw["anidb_ids"] == [15449, 17765, 18220]
    assert tybw["title"] == "Bleach: Thousand-Year Blood War"
    assert (tybw["downloaded_count"], tybw["total_count"]) == (5, 6)
    missing = [row for row in tybw["episodes"] if row.get("missing")]
    assert [(row["season"], row["episode"]) for row in missing] == [
        ("Season 02 - Sennen Kessen Hen - Ketsubetsu Tan", 2)
    ]
    # Each card wears its own entry's cover, not TVDB's shared one.
    assert tybw["poster_url"] == "cover:15449"
    assert bleach["poster_url"] == "cover:2369"


def test_bleach_tabs_are_its_arc_folders(library):
    bleach = next(show for show in library(only_key=BLEACH) if show["key"] == BLEACH)
    tabs = {row["season_number"]: row["season"] for row in bleach["episodes"]}
    assert tabs == {1: "Season 01", 2: "Season 02"}


def test_shoko_links_and_episode_lists_win(library):
    catalog = shoko_catalog.Catalog(
        entries={
            2369: shoko_catalog.Entry(
                aid=2369,
                title="Bleach",
                poster_url="/api/anime/anidb/image/AniDB/Poster/2369",
                episodes=tuple(shoko_catalog.EntryEpisode(n, f"E{n}", "2004-10-05") for n in range(1, 6)),
            )
        },
        files={f"/library/{BLEACH}/Season 01/Bleach - S01E001.mkv": [(2369, 1)]},
    )
    bleach = next(show for show in library(catalog=catalog) if show["key"] == BLEACH)
    assert bleach["total_count"] == 5  # Shoko's episode list, not TVDB's slice
    assert bleach["poster_url"] == "/api/anime/anidb/image/AniDB/Poster/2369"


def test_a_file_numbered_through_a_tvdb_season_is_renumbered_for_its_cour():
    cour = TABLE.anime[17765]
    assert anime_library.anidb_episode_of({"season_number": 17, "episode": 3}, cour) == 1
    assert anime_library.anidb_episode_of({"season_number": 1, "episode": 3}, cour) == 3
    assert anime_library.anidb_episode_of({"anidb_episode": 7, "episode": 3}, cour) == 7


def test_tvdb_is_sliced_to_the_entry():
    family = TABLE.by_tvdb[74796]
    assert [ep.number for ep in anime_library.tvdb_slice(TABLE.anime[2369], ROSTER, family)] == [1, 2, 3, 4]
    assert [ep.title for ep in anime_library.tvdb_slice(TABLE.anime[17765], ROSTER, family)] == ["T3", "T4"]
    assert [ep.number for ep in anime_library.tvdb_slice(TABLE.anime[18220], ROSTER, family)] == [1, 2]


def test_the_catalogue_is_built_from_shokos_listings():
    series = {
        "List": [
            {
                "IDs": {"AniDB": 2369, "TvDB": [74796]},
                "Name": "Bleach",
                "Images": {"Posters": [{"ID": 2369, "Source": "AniDB", "Type": "Poster", "RelativeFilepath": "/2369"}]},
            }
        ]
    }
    episodes = [
        {
            "AniDB": {"AnimeID": 2369, "Type": "Episode", "EpisodeNumber": 2, "AirDate": "2004-10-12", "Title": "Two"},
            "Files": [{"Locations": [{"AbsolutePath": "/lib/Bleach/Season 01/Bleach - S01E002.mkv"}]}],
        },
        {"AniDB": {"AnimeID": 2369, "Type": "Episode", "EpisodeNumber": 1, "AirDate": "2004-10-05"}, "Files": []},
        {"AniDB": {"AnimeID": 2369, "Type": "Credits", "EpisodeNumber": 1}, "Files": []},
    ]
    catalog = shoko_catalog.build(series, episodes)
    entry = catalog.entries[2369]
    assert [ep.number for ep in entry.episodes] == [1, 2]
    assert entry.poster_url == "/api/anime/anidb/image/AniDB/Poster/2369"
    assert catalog.files["/lib/Bleach/Season 01/Bleach - S01E002.mkv"] == [(2369, 2)]


def test_cover_art_is_condensed_to_anidb_ids(tmp_path):
    source = tmp_path / "db.json"
    source.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "picture": "https://cdn.myanimelist.net/images/anime/1/1.jpg",
                        "sources": ["https://myanimelist.net/anime/5", "https://anidb.net/anime/15449"],
                    },
                    {"picture": "https://cdn.myanimelist.net/images/qm_50.gif?no_pic", "sources": ["https://anidb.net/anime/9"]},
                ]
            }
        )
    )
    target = tmp_path / "covers.json"
    assert anidb_art.condense(source, target) == 1
    assert json.loads(target.read_text()) == {"15449": "https://cdn.myanimelist.net/images/anime/1/1.jpg"}


def test_review_cards_show_their_own_entrys_cover(monkeypatch):
    async def metadata(title, tvdb_id=None):
        return {"english_title": "Bleach", "tvdb_id": 74796, "poster_url": "tvdb-poster"}

    monkeypatch.setattr(anime_library, "show_metadata", metadata)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(anidb_art, "cover", lambda aid: f"cover:{aid}")
    rows = [
        {"key": "anidb:15449", "source_title": "Bleach: Sennen Kessen Hen", "anidb_id": 15449},
        {"key": "anidb:17765", "source_title": "Ketsubetsu Tan", "anidb_id": 17765},
        {"key": "legacy", "source_title": "Bleach"},
    ]
    asyncio.run(anime_library.enrich_review_rows(rows))
    assert [row["poster_url"] for row in rows] == ["cover:15449", "cover:17765", "tvdb-poster"]


def test_a_batch_torrent_is_numbered_file_by_file(tmp_path):
    from bankai.processor import anime as processor

    one = [tmp_path / "Show - 05.mkv"]
    batch = [tmp_path / f"Show - {n:02d}.mkv" for n in range(1, 13)]
    assert processor.batch_override(5, one) == 5
    assert processor.batch_override(1, batch) is None
    assert processor.batch_override(None, batch) is None
    assert [processor.anidb_episode_number(path.name) for path in batch[:3]] == [1, 2, 3]


def test_an_episode_under_another_folder_name_is_on_disk(monkeypatch, tmp_path):
    """A show kept as "Demon Slayer" is the one the TVDB route calls
    "Demon Slayer: Kimetsu no Yaiba": its episodes are not fetched again."""
    from bankai.web import library_walk

    titles = ET.fromstring(
        """<animetitles><anime aid="14107">
        <title xml:lang="x-jat" type="main">Kimetsu no Yaiba</title>
        <title xml:lang="en" type="official">Demon Slayer: Kimetsu no Yaiba</title>
        </anime></animetitles>"""
    )
    records = ET.fromstring(
        '<anime-list><anime anidbid="14107" tvdbid="348545" defaulttvdbseason="1" episodeoffset=""/></anime-list>'
    )
    table = anidb.build_index(titles, records)
    files = [
        {"path": f"/lib/Demon Slayer/Season 01/Demon Slayer - S01E0{n}.mkv", "name": f"Demon Slayer - S01E0{n}.mkv",
         "series": "Demon Slayer", "season": "Season 01", "root": "/lib"}
        for n in (1, 2)
    ]
    monkeypatch.setattr(library_walk, "files", lambda roots, rescan=False: files)
    monkeypatch.setattr(anidb, "cached_index", lambda: table)
    monkeypatch.setattr(anime_library, "folder_tvdb_ids", lambda folders, root: {"Demon Slayer": 348545})
    token = erai._DISK_INDEX.set({})
    try:
        # The TVDB route, asking under TVDB's name for the series:
        assert erai._episode_on_disk("Demon Slayer: Kimetsu no Yaiba", 1, 2, tvdb_id=348545)
        assert not erai._episode_on_disk("Demon Slayer: Kimetsu no Yaiba", 1, 3, tvdb_id=348545)
        # The AniDB route, by the entry's own episode, through its TVDB season:
        assert erai._anidb_episode_on_disk(table.anime[14107], 1)
        assert not erai._anidb_episode_on_disk(table.anime[14107], 3)
    finally:
        erai._DISK_INDEX.reset(token)
