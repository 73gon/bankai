"""The anime library by AniDB entry: Bleach and its sequel are two cards."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from bankai.metadata import anidb, anidb_art
from bankai.web import anime_library, erai, shoko_catalog

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
# Anime-Lists files TYBW as season 17 of Bleach; each cour starts partway into it.
RECORDS = ET.fromstring(
    """<anime-list>
  <anime anidbid="2369" tvdbid="74796" defaulttvdbseason="a" episodeoffset=""/>
  <anime anidbid="15449" tvdbid="74796" defaulttvdbseason="17" episodeoffset="0"/>
  <anime anidbid="17765" tvdbid="74796" defaulttvdbseason="17" episodeoffset="2"/>
  <anime anidbid="18220" tvdbid="74796" defaulttvdbseason="17" episodeoffset="4"/>
</anime-list>"""
)
TABLE = anidb.build_index(TITLES, RECORDS)

# What anime-offline-database knows of each entry: Bleach is four episodes
# here, each TYBW cour two.
FACTS = {
    2369: {"episodes": 4, "status": "FINISHED", "year": 2004, "type": "TV"},
    15449: {"episodes": 2, "status": "FINISHED", "year": 2022, "type": "TV"},
    17765: {"episodes": 2, "status": "FINISHED", "year": 2023, "type": "TV"},
    18220: {"episodes": 2, "status": "FINISHED", "year": 2024, "type": "TV"},
}

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

    monkeypatch.setattr(anidb, "index", index)
    monkeypatch.setattr(erai, "_load_state", erai._default_state)
    monkeypatch.setattr(erai, "_load_policies", lambda: {})
    monkeypatch.setattr(anime_library, "probed_codecs", lambda files: {})
    monkeypatch.setattr(anime_library, "german_dubbed_episodes", lambda files: set())
    monkeypatch.setattr(anime_library, "load_prefs", lambda: {})
    monkeypatch.setattr(anidb_art, "cover", lambda aid: f"cover:{aid}")

    def run(catalog=None, only_key=None, facts=FACTS):
        monkeypatch.setattr(anidb_art, "facts", lambda aid: facts.get(int(aid), {}))
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

    # Bleach counts only its own episodes, not its sequel's.
    assert bleach["anidb_id"] == 2369
    assert (bleach["downloaded_count"], bleach["total_count"]) == (4, 4)
    assert bleach["completion_state"] == "complete"
    assert bleach["numbering"] == "anidb"
    # Finished, as anime-offline-database says, and all of it is here.
    assert bleach["finished"] is True

    # The sequel is its three cours, one tab each, counted per cour.
    assert tybw["anidb_ids"] == [15449, 17765, 18220]
    assert tybw["title"] == "Bleach: Thousand-Year Blood War"
    assert (tybw["downloaded_count"], tybw["total_count"]) == (5, 6)
    missing = [row for row in tybw["episodes"] if row.get("missing")]
    # Each tab is its entry, named after it rather than the folder it is in.
    assert [(row["season"], row["episode"]) for row in missing] == [
        ("Bleach: Sennen Kessen Hen - Ketsubetsu Tan", 2)
    ]
    # Each card wears its own entry's cover, not one shared by the whole show.
    assert tybw["poster_url"] == "cover:15449"
    assert bleach["poster_url"] == "cover:2369"


def test_bleach_tabs_are_its_arc_folders(library):
    bleach = next(show for show in library(only_key=BLEACH) if show["key"] == BLEACH)
    tabs = {row["season_number"]: row["season"] for row in bleach["episodes"]}
    assert tabs == {1: "Season 01", 2: "Season 02"}


def test_each_tab_names_its_anidb_entry(library):
    """The drawer links each tab to AniDB: a cour kept in a folder of its own
    is its own entry; one entry kept in arc folders is that entry on every tab."""
    shows = {show["key"]: show for show in library()}
    assert shows[TYBW]["season_anidb"] == {
        "1": [{"anidb_id": 15449, "title": "Bleach: Thousand-Year Blood War"}],
        "2": [{"anidb_id": 17765, "title": "Bleach: Sennen Kessen Hen - Ketsubetsu Tan"}],
        "3": [{"anidb_id": 18220, "title": "Bleach: Sennen Kessen Hen - Soukoku Tan"}],
    }
    assert shows[BLEACH]["season_anidb"] == {
        "1": [{"anidb_id": 2369, "title": "Bleach"}],
        "2": [{"anidb_id": 2369, "title": "Bleach"}],
    }


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
    assert bleach["total_count"] == 5  # Shoko's episode list, not the offline count of 4
    assert bleach["poster_url"] == "/api/anime/anidb/image/AniDB/Poster/2369"


def test_an_entry_shoko_does_not_hold_is_counted_by_its_offline_episode_count(library):
    """anime-offline-database says how many episodes an entry has.

    Numbers without titles or dates, but enough to say which are missing:
    Bleach is six episodes long here, and four are on disk.
    """
    facts = {**FACTS, 2369: {"episodes": 6, "status": "FINISHED", "year": 2004}}
    bleach = next(show for show in library(facts=facts) if show["key"] == BLEACH)
    assert (bleach["downloaded_count"], bleach["total_count"]) == (4, 6)
    missing = [row for row in bleach["episodes"] if row["missing"]]
    assert [row["episode"] for row in missing] == [5, 6]
    # Finished, so they are not to be announced: they are simply not here.
    assert not any(row["tba"] or row["episode_title"] for row in missing)
    assert bleach["completion_state"] == "partial"
    assert bleach["metadata_available"] is True
    assert bleach["year"] == 2004

    # Still airing: the rest is yet to come, not missing.
    facts[2369] = {"episodes": 6, "status": "ONGOING", "year": 2004}
    bleach = next(show for show in library(facts=facts) if show["key"] == BLEACH)
    assert bleach["completion_state"] == "upcoming"

    # Known nowhere: the files alone, and no claim to be complete.
    bleach = next(show for show in library(facts={}) if show["key"] == BLEACH)
    assert (bleach["total_count"], bleach["completion_state"]) == (4, "unknown")


def test_a_file_numbered_through_a_tvdb_season_is_renumbered_for_its_cour():
    cour = TABLE.anime[17765]
    assert anime_library.anidb_episode_of({"season_number": 17, "episode": 3}, cour) == 1
    assert anime_library.anidb_episode_of({"season_number": 1, "episode": 3}, cour) == 3
    assert anime_library.anidb_episode_of({"anidb_episode": 7, "episode": 3}, cour) == 7


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


def test_cover_art_and_facts_are_condensed_to_anidb_ids(tmp_path, monkeypatch):
    """The facts are what an entry Shoko does not hold is counted by."""
    source = tmp_path / "db.json"
    source.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "picture": "https://cdn.myanimelist.net/images/anime/1/1.jpg",
                        "sources": [
                            "https://anilist.co/anime/116674",
                            "https://myanimelist.net/anime/5",
                            "https://anidb.net/anime/15449",
                        ],
                        "episodes": 13,
                        "status": "FINISHED",
                        "animeSeason": {"season": "FALL", "year": 2022},
                        "type": "TV",
                    },
                    {"picture": "https://cdn.myanimelist.net/images/qm_50.gif?no_pic", "sources": ["https://anidb.net/anime/9"]},
                    {"picture": "https://cdn.myanimelist.net/images/anime/2/2.jpg", "sources": ["https://myanimelist.net/anime/7"]},
                ]
            }
        )
    )
    target = tmp_path / "covers.json"
    assert anidb_art.condense(source, target) == 1
    assert json.loads(target.read_text()) == {"15449": "https://cdn.myanimelist.net/images/anime/1/1.jpg"}

    # The same anime on AniList rides along: its names and covers are what bankai shows.
    tybw = {"episodes": 13, "status": "FINISHED", "year": 2022, "type": "TV", "anilist": 116674}
    # An entry without a usable cover still has its facts; one with no AniDB source has neither.
    assert json.loads((tmp_path / "anidb_facts.json").read_text()) == {
        "15449": tybw,
        "9": {"episodes": None, "status": None, "year": None, "type": None, "anilist": None},
    }
    monkeypatch.setattr(anidb_art, "_store", lambda: target)
    monkeypatch.setattr(anidb_art, "_FACTS", None)
    assert anidb_art.facts(15449) == tybw
    assert anidb_art.facts(12345) == {}


def test_review_cards_show_their_own_entrys_name_and_cover(monkeypatch):
    """Each card is one AniDB entry, shown by its own name, year and cover.

    TVDB gave every cour of a show the one series poster. The cover is
    Shoko's, else anime-offline-database's, else the one the AniDB search
    returned; a card no entry answers for yet keeps its Erai name.
    """

    def never(*args, **kwargs):
        raise AssertionError("review cards are not looked up on TVDB or in the saved mappings")

    monkeypatch.setattr(anime_library, "show_metadata", never)
    monkeypatch.setattr(erai, "_load_mappings", never)
    monkeypatch.setattr(anidb_art, "cover", lambda aid: {15449: "cover:15449", 2369: "cover:2369"}.get(aid))
    monkeypatch.setattr(anidb_art, "facts", lambda aid: {15449: {"year": 2022}}.get(aid, {}))
    catalog = shoko_catalog.Catalog(
        entries={2369: shoko_catalog.Entry(aid=2369, title="Bleach", poster_url="shoko:2369", episodes=())}
    )
    rows = [
        {
            "key": "anidb:15449",
            "source_title": "Bleach: Sennen Kessen Hen",
            "anidb_id": 15449,
            "english_title": "Bleach: Thousand-Year Blood War",
            "tvdb_id": 74796,
        },
        {
            "key": "anidb:17765",
            "source_title": "Ketsubetsu Tan",
            "anidb_id": 17765,
            "anidb_title": "Bleach: Sennen Kessen Hen - Ketsubetsu Tan",
            "anidb_poster_url": "search:17765",
        },
        {"key": "anidb:2369", "source_title": "Bleach", "anidb_id": 2369},
        {"key": "legacy", "source_title": "Bleach Sennen Kessen Hen", "anidb_poster_url": None},
    ]
    asyncio.run(anime_library.enrich_review_rows(rows, catalog))

    assert [row["title"] for row in rows] == [
        "Bleach: Thousand-Year Blood War",
        "Bleach: Sennen Kessen Hen - Ketsubetsu Tan",
        "Bleach",
        "Bleach Sennen Kessen Hen",
    ]
    assert [row["poster_url"] for row in rows] == ["cover:15449", "search:17765", "shoko:2369", None]
    assert rows[0]["year"] == 2022
    assert all("tvdb_id" not in row for row in rows)


def test_a_batch_torrent_is_numbered_file_by_file(tmp_path):
    from bankai.processor import anime as processor

    one = [tmp_path / "Show - 05.mkv"]
    batch = [tmp_path / f"Show - {n:02d}.mkv" for n in range(1, 13)]
    assert processor.batch_override(5, one) == 5
    assert processor.batch_override(1, batch) is None
    assert processor.batch_override(None, batch) is None
    assert [processor.anidb_episode_number(path.name) for path in batch[:3]] == [1, 2, 3]


def test_an_episode_under_another_folder_name_is_on_disk(monkeypatch, tmp_path):
    """A show kept as "Demon Slayer" is AniDB's "Kimetsu no Yaiba": its
    episodes are not fetched again.

    The folder's name finds nothing on AniDB. Shoko's link for a file says
    which episode it is, from hashing it; a file Shoko has not matched takes
    the entry the user linked its folder to.
    """
    from bankai.config import Settings
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
    assert anidb.resolve_in(table, "Demon Slayer").anime is None
    season = tmp_path / "Demon Slayer" / "Season 01"
    files = [
        {
            "path": str(season / f"Demon Slayer - S01E0{n}.mkv"),
            "name": f"Demon Slayer - S01E0{n}.mkv",
            "series": "Demon Slayer",
            "season": "Season 01",
            "root": str(tmp_path),
        }
        for n in (1, 2)
    ]
    monkeypatch.setattr(erai, "get_settings", lambda: Settings(transfer={"anime_shows_dir": str(tmp_path)}))
    monkeypatch.setattr(library_walk, "files", lambda roots, rescan=False: files)
    monkeypatch.setattr(anidb, "cached_index", lambda: table)
    monkeypatch.setattr(
        shoko_catalog, "saved_links", lambda: shoko_catalog.Catalog(files={files[0]["path"]: [(14107, 1)]})
    )
    monkeypatch.setattr(anime_library, "load_prefs", lambda: {"folder:Demon Slayer": {"anidb_id": 14107}})
    token = erai._DISK_INDEX.set({})
    try:
        assert erai._anidb_episode_on_disk(table.anime[14107], 1)  # by Shoko's link
        assert erai._anidb_episode_on_disk(table.anime[14107], 2)  # by the folder's link
        assert not erai._anidb_episode_on_disk(table.anime[14107], 3)
    finally:
        erai._DISK_INDEX.reset(token)


def test_a_batch_release_name_reads_like_its_first_episode():
    assert erai._erai_identity_name("[Erai-raws] Date A Live V - 01 ~ 12 [1080p CR WEBRip HEVC AAC][MultiSub]") == (
        "Date A Live V",
        1,
    )
    assert erai._erai_identity_name("[Erai-raws] Date A Live V - 05 [1080p][HEVC]") == ("Date A Live V", 5)
    assert erai._erai_identity_name("[Erai-raws] Date A Live V [1080p]") is None
    # Admission still reads a batch as no single episode.
    assert erai._erai_name_episode("[Erai-raws] Date A Live V - 01 ~ 12 [1080p]") is None


def test_tvdb_era_holds_are_read_again_on_anidb(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    state = erai._default_state()
    held = {
        "a" * 40: "[Erai-raws] Bleach - 05 [1080p][HEVC]",
        "b" * 40: "[Erai-raws] Bleach - 250 [1080p][HEVC]",
        "c" * 40: "[Erai-raws] Nobody Knows This - 01 [1080p][HEVC]",
        "d" * 40: "[Erai-raws] Bleach: Sennen Kessen Hen - 01 ~ 13 [1080p][HEVC][BATCH]",
    }
    for info_hash, title in held.items():
        state["releases"][info_hash] = {"status": "held", "title": title, "reason": "No confident TVDB match"}
    state["releases"]["e" * 40] = {"status": "held", "title": "x", "reason": "German subtitles not listed"}
    erai._save_state(state)
    monkeypatch.setattr(erai, "_anidb_episode_on_disk", lambda anime, episode: (anime.aid, episode) == (2369, 5))

    counts = erai.reclassify_tvdb_holds(TABLE)

    releases = erai._load_state()["releases"]
    assert counts == {"existing": 1, "identified": 2, "unmatched": 1}
    assert releases["a" * 40]["status"] == "existing"
    assert releases["b" * 40]["reason"].startswith("Identified on AniDB as Bleach")
    assert releases["c" * 40]["reason"] == "No AniDB anime matches this title"
    # A batch is identified by its name, and waits for its recheck.
    assert releases["d" * 40]["reason"].startswith("Identified on AniDB as Bleach: Sennen Kessen Hen")
    assert releases["e" * 40]["reason"] == "German subtitles not listed"


def test_a_folder_linked_by_the_user_wins_over_its_name_but_not_over_shoko():
    from types import SimpleNamespace

    from bankai.web.anime_library import assign_entries, folder_links

    catalog = SimpleNamespace(files={"/lib/Show/Show - S01E01.mkv": [(111, 1)]})
    files = [
        {"path": "/lib/Show/Show - S01E01.mkv", "series": "Show", "season": "Season 01"},
        {"path": "/lib/Show/Show - S01E02.mkv", "series": "Show", "season": "Season 01"},
        {"path": "/lib/Other/Other - 01.mkv", "series": "Other", "season": None},
    ]
    # A numbering preference from the TVDB era may still be in the file; it is no link.
    links = folder_links({"folder:Show": {"anidb_id": 222}, "tvdb:5": {"numbering": "absolute"}})
    assert links == {"Show": 222}

    assign_entries(files, catalog=catalog, table=None, links=links)

    # Shoko's own match stays; the unmatched file of the folder takes the link.
    assert [row["anidb_id"] for row in files] == [111, 222, None]
