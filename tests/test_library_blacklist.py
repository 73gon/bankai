"""Removing a show from the library, and blacklisting by AniDB title."""

from __future__ import annotations

import asyncio

import pytest

from bankai.web import erai, shoko


@pytest.fixture()
def store(monkeypatch, tmp_path):
    state = {"releases": {}, "held": [], "series": {}}
    policies: dict = {}
    retries: dict = {}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(erai, "_load_policies", lambda: policies)
    monkeypatch.setattr(erai, "_save_policies", lambda value: None)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai, "_load_retry_requests", lambda: retries)
    monkeypatch.setattr(erai, "_save_retry_requests", lambda value: None)
    monkeypatch.setattr(erai, "_SHOW_KEYS_CACHE", None)
    root = tmp_path / "shows_anime"
    root.mkdir()
    monkeypatch.setattr(erai, "_series_roots", lambda: [root])
    monkeypatch.setattr(erai, "series_files", lambda title: [])
    return {"state": state, "policies": policies, "root": root}


FRIEREN = {
    "anidb_id": 17617,
    "title": "Sousou no Frieren",
    "english_title": "Frieren: Beyond Journey's End",
    "matching_titles": ["Sousou no Frieren", "Frieren: Beyond Journey's End"],
    "poster_url": "/api/anime/anidb/image/AniDB/Poster/1",
}


def test_a_release_under_another_anidb_title_is_blacklisted(store):
    """Erai names releases after AniDB's romaji title; the card may carry another."""
    store["policies"]["anidb:17617"] = {
        "mode": "blacklisted",
        "anidb_id": 17617,
        "source_title": "Frieren: Beyond Journey's End",
        "anidb_titles": FRIEREN["matching_titles"],
    }
    assert erai._title_blacklisted("[Erai-raws] Sousou no Frieren - 03 [1080p][HEVC]")
    # The second season is an AniDB entry of its own and is not blocked with it.
    assert not erai._title_blacklisted("[Erai-raws] Sousou no Frieren 2nd Season - 01 [1080p]")
    assert not erai._title_blacklisted("[Erai-raws] Spy x Family - 01 [1080p]")


def test_a_decision_from_before_anidb_entries_still_blocks_the_whole_show(store):
    store["policies"]["sousou no frieren"] = {"mode": "blacklisted", "source_title": "Sousou no Frieren"}
    assert erai._title_blacklisted("[Erai-raws] Sousou no Frieren 2nd Season - 01 [1080p]")


def test_two_names_of_one_anime_are_one_blacklist_card(store):
    store["policies"].update(
        {
            "sousou no frieren": {"mode": "blacklisted", "source_title": "Sousou no Frieren", "anidb_id": 17617},
            "frieren": {"mode": "blacklisted", "source_title": "Frieren", "anidb_id": 17617},
            "spy x family": {"mode": "blacklisted", "source_title": "Spy x Family"},
        }
    )
    cards = erai.blacklist_items()
    assert len(cards) == 2
    frieren = next(card for card in cards if card.get("anidb_id") == 17617)
    assert sorted(frieren["keys"]) == ["frieren", "sousou no frieren"]
    assert frieren["linked"] is True


def test_linking_a_card_covers_every_title_and_catches_held_releases(store):
    store["policies"]["frieren"] = {"mode": "blacklisted", "source_title": "Frieren"}
    store["state"]["releases"]["a" * 40] = {
        "status": "held",
        "title": "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC]",
    }

    result = erai.link_blacklist("frieren", FRIEREN)

    assert result["caught"] == 1
    assert store["state"]["releases"]["a" * 40]["status"] == "blacklisted"
    assert store["policies"]["frieren"]["anidb_id"] == 17617


def test_removing_a_show_blacklists_it_and_deletes_its_folders(store, monkeypatch):
    folder = store["root"] / "Frieren"
    (folder / "Season 01").mkdir(parents=True)
    (folder / "Season 01" / "Frieren - S01E01.mkv").write_bytes(b"x" * 10)
    store["state"]["series"]["424536"] = {"tvdb_id": 424536, "english_title": "Frieren"}
    store["state"]["releases"]["b" * 40] = {
        "status": "queued",
        "title": "[Erai-raws] Sousou no Frieren - 07 [1080p][HEVC]",
    }

    class NoQbit:
        async def login(self): ...
        async def remove(self, info_hash, delete_files): ...
        async def aclose(self): ...

    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", NoQbit)
    from xml.etree import ElementTree as ET

    from bankai.metadata import anidb

    table = anidb.build_index(
        ET.fromstring(
            """<animetitles>
  <anime aid="17617"><title xml:lang="x-jat" type="main">Sousou no Frieren</title></anime>
  <anime aid="18886"><title xml:lang="x-jat" type="main">Sousou no Frieren (2026)</title></anime>
</animetitles>"""
        ),
        ET.fromstring(
            """<anime-list>
  <anime anidbid="17617" tvdbid="424536" defaulttvdbseason="1" episodeoffset="0"/>
  <anime anidbid="18886" tvdbid="424536" defaulttvdbseason="2" episodeoffset="0"/>
</anime-list>"""
        ),
    )

    async def index():
        return table

    monkeypatch.setattr(anidb, "index", index)
    monkeypatch.setattr(anidb, "_INDEX", ((0, 0), table))

    result = asyncio.run(
        erai.blacklist_show(
            title="Frieren",
            source_title="Sousou no Frieren",
            tvdb_id=424536,
            folders=[folder],
            anime=FRIEREN,
        )
    )

    assert not folder.exists()
    assert result["deleted_files"] == 1
    # The whole show was removed, so each of its AniDB entries is blocked --
    # each its own blacklist card, restorable on its own.
    assert {key for key in store["policies"]} == {"anidb:17617", "anidb:18886"}
    assert result["entries"] == [17617, 18886]
    assert store["state"]["releases"]["b" * 40]["status"] == "blacklisted"
    # Tracked, it would have come back as an empty library card.
    assert "424536" not in store["state"]["series"]


def test_the_library_root_itself_is_never_deleted(store):
    """A show folder resolving to the root would take the whole library."""
    (store["root"] / "Other Show").mkdir()
    result = asyncio.run(
        erai.purge_series("x", english_title="", delete_files=True, extra_folders=[store["root"]])
    )
    assert store["root"].exists() and (store["root"] / "Other Show").exists()
    assert result["deleted_folders"] == []


def test_shoko_results_keep_only_titles_worth_matching():
    row = {
        "ID": 17617,
        "Title": "Sousou no Frieren",
        "Type": "TV",
        "EpisodeCount": 28,
        "AirDate": "2023-09-29",
        "Titles": [
            {"Name": "Frieren: Beyond Journey's End", "Language": "en", "Type": "Official"},
            {"Name": "Frieren", "Language": "en", "Type": "Short"},
            {"Name": "葬送のフリーレン", "Language": "ja", "Type": "Official"},
        ],
        "Poster": {"ID": 5, "Source": "AniDB", "Type": "Poster", "RelativeFilepath": "AniDB/5.jpg"},
    }
    anime = shoko._anime(row)
    assert anime["anidb_id"] == 17617
    assert anime["english_title"] == "Frieren: Beyond Journey's End"
    assert anime["year"] == 2023
    # The short name "Frieren" would match other shows; it is left out.
    assert "Frieren" not in anime["matching_titles"]
    assert "Sousou no Frieren" in anime["matching_titles"]
    assert anime["poster_url"] == "/api/anime/anidb/image/AniDB/Poster/5"


def test_search_is_exact_first_and_fuzzy_only_as_the_fallback(monkeypatch):
    """Fuzzy pads results with loose neighbours; exact answers when it can."""
    calls: list[dict] = []

    async def fake_get(path, /, **params):
        calls.append(params)
        if params["fuzzy"] == "false" and params["query"] == "Frieren":
            return {"List": [{"ID": 17617, "Title": "Sousou no Frieren", "Titles": []}]}
        if params["fuzzy"] == "true":
            return {"List": [{"ID": 1, "Title": "Friern Typo Match", "Titles": []}]}
        return {"List": []}

    monkeypatch.setattr(shoko, "_get", fake_get)
    found = asyncio.run(shoko.search_anidb("Frieren"))
    assert [row["anidb_id"] for row in found] == [17617]
    assert [c["fuzzy"] for c in calls] == ["false"]
    # AniDB's whole title list, not only what the Shoko collection holds.
    assert calls[0]["local"] == "false"

    calls.clear()
    assert [row["anidb_id"] for row in asyncio.run(shoko.search_anidb("Friern"))] == [1]
    assert [c["fuzzy"] for c in calls] == ["false", "true"]


def test_an_english_synonym_stands_in_where_anidb_has_no_official_english_title():
    row = {
        "ID": 2736,
        "Title": "Some Display Title",
        "Titles": [
            {"Name": "One Piece: Taose! Kaizoku Ganzack", "Language": "x-jat", "Type": "Main"},
            {"Name": "One Piece: Defeat the Pirate Ganzack!", "Language": "en", "Type": "Synonym"},
        ],
    }
    anime = shoko._anime(row)
    # The romaji main title, not Shoko's display title.
    assert anime["title"] == "One Piece: Taose! Kaizoku Ganzack"
    assert anime["english_title"] == "One Piece: Defeat the Pirate Ganzack!"
    # A synonym is shown, never matched on.
    assert "One Piece: Defeat the Pirate Ganzack!" not in anime["matching_titles"]


@pytest.mark.parametrize(
    "query",
    ["17617", " aid 17617 ", "aid:17617", "https://anidb.net/anime/17617", "https://anidb.net/perl-bin/animedb.pl?show=anime&aid=17617"],
)
def test_an_anidb_id_or_link_is_looked_up_by_id(monkeypatch, query):
    calls: list[dict] = []

    async def fake_get(path, /, **params):
        calls.append(params)
        return {"List": [{"ID": 17617, "Title": "Sousou no Frieren", "Titles": []}]}

    monkeypatch.setattr(shoko, "_get", fake_get)
    found = asyncio.run(shoko.search_anidb(query))
    assert [row["anidb_id"] for row in found] == [17617]
    assert calls[0]["searchById"] == "true" and calls[0]["query"] == "17617"


def test_a_poster_shoko_never_downloaded_is_not_offered():
    """For an anime outside the collection Shoko holds no file and answers 404."""
    assert shoko.poster_path({"ID": 17617, "Source": "AniDB", "Type": "Poster", "RelativeFilepath": None}) is None


def test_best_match_needs_one_exact_title():
    results = [
        {"anidb_id": 1, "matching_titles": ["Sousou no Frieren"]},
        {"anidb_id": 2, "matching_titles": ["Sousou no Frieren: Marumaru no Mahou"]},
    ]
    assert shoko.best_match(results, "Sousou no Frieren")["anidb_id"] == 1
    assert shoko.best_match(results, "Nothing like it") is None


def test_discard_and_delete_takes_only_the_entry_s_own_episodes(store, monkeypatch):
    """A show folder holds every season; one AniDB entry's files are only its own."""
    from xml.etree import ElementTree as ET

    from bankai.metadata import anidb

    table = anidb.build_index(
        ET.fromstring(
            """<animetitles>
  <anime aid="1"><title xml:lang="x-jat" type="main">Dr. Stone: Science Future</title></anime>
  <anime aid="2"><title xml:lang="x-jat" type="main">Dr. Stone: Science Future (2025)</title></anime>
</animetitles>"""
        ),
        ET.fromstring(
            """<anime-list>
  <anime anidbid="1" tvdbid="99" defaulttvdbseason="4" episodeoffset="0"/>
  <anime anidbid="2" tvdbid="99" defaulttvdbseason="4" episodeoffset="12"/>
</anime-list>"""
        ),
    )
    monkeypatch.setattr(anidb, "_INDEX", ((0, 0), table))
    from bankai.config import Settings

    monkeypatch.setattr(erai, "get_settings", lambda: Settings(transfer={"anime_shows_dir": str(store["root"])}))
    store["state"]["series"]["99"] = {"english_title": "Dr. Stone"}
    season = store["root"] / "Dr. Stone" / "Season 04"
    season.mkdir(parents=True)
    for number in range(1, 25):
        (season / f"Dr. Stone - S04E{number:02d}.mkv").write_bytes(b"x")
    (store["root"] / "Dr. Stone" / "Season 03").mkdir()
    (store["root"] / "Dr. Stone" / "Season 03" / "Dr. Stone - S03E13.mkv").write_bytes(b"x")
    own = store["root"] / "Dr. Stone Science Future (2025)"
    own.mkdir()
    (own / "Dr. Stone Science Future (2025) - 25.mkv").write_bytes(b"x")
    monkeypatch.setattr(
        "bankai.backend.transfer._existing_show_folder",
        lambda name, cache, roots=None: store["root"] / "Dr. Stone",
    )

    picked = sorted(path.name for path in erai.entry_files(2))

    # Part 2 of season 4 is episodes 13-24 there, plus its own folder.
    assert picked == sorted(
        [f"Dr. Stone - S04E{n:02d}.mkv" for n in range(13, 25)] + ["Dr. Stone Science Future (2025) - 25.mkv"]
    )
