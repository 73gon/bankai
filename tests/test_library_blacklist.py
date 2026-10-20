"""Removing a show from the library, and blacklisting by AniList title."""

from __future__ import annotations

import asyncio

import pytest

from bankai.config import reset_settings_cache
from bankai.web import anime_files, erai


@pytest.fixture()
def store(monkeypatch, tmp_path):
    state = {"releases": {}, "held": [], "anime": {}}
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
    staging = tmp_path / "staging"
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(root))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    reset_settings_cache()
    yield {"state": state, "policies": policies, "root": root, "staging": staging / "Shows"}
    reset_settings_cache()


# An entry as the pick-an-anime dialog offers it (anilist.as_choice).
FRIEREN = {
    "anilist_id": 154587,
    "name": "Frieren: Beyond Journey's End",
    "romaji": "Sousou no Frieren",
    "english": "Frieren: Beyond Journey's End",
    "format": "TV",
    "cover": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/154587.jpg",
}


def indexed(root, rel: str, pairs, data: bytes = b"x"):
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    anime_files.record(path, pairs)
    return path


def test_a_release_under_another_anilist_title_is_blacklisted(store):
    """Erai names releases after the romaji title; the card may carry the English one."""
    store["policies"]["anilist:154587"] = {
        "mode": "blacklisted",
        "anilist_id": 154587,
        "source_title": "Frieren: Beyond Journey's End",
        "anilist_titles": ["Sousou no Frieren", "Frieren: Beyond Journey's End"],
    }
    assert erai._title_blacklisted("[Erai-raws] Sousou no Frieren - 03 [1080p][HEVC]")
    # The second season is an AniList entry of its own and is not blocked with it.
    assert not erai._title_blacklisted("[Erai-raws] Sousou no Frieren 2nd Season - 01 [1080p]")
    assert not erai._title_blacklisted("[Erai-raws] Spy x Family - 01 [1080p]")


def test_a_decision_made_by_name_still_blocks_the_whole_show(store):
    store["policies"]["sousou no frieren"] = {"mode": "blacklisted", "source_title": "Sousou no Frieren"}
    assert erai._title_blacklisted("[Erai-raws] Sousou no Frieren 2nd Season - 01 [1080p]")


def test_two_names_of_one_anime_are_one_blacklist_card(store):
    store["policies"].update(
        {
            "sousou no frieren": {"mode": "blacklisted", "source_title": "Sousou no Frieren", "anilist_id": 154587},
            "frieren": {"mode": "blacklisted", "source_title": "Frieren", "anilist_id": 154587},
            "spy x family": {"mode": "blacklisted", "source_title": "Spy x Family"},
        }
    )
    cards = erai.blacklist_items()
    assert len(cards) == 2
    frieren = next(card for card in cards if card.get("anilist_id") == 154587)
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
    policy = store["policies"]["frieren"]
    assert policy["anilist_id"] == 154587
    assert policy["anilist_title"] == "Frieren: Beyond Journey's End"
    assert policy["anilist_titles"] == ["Sousou no Frieren", "Frieren: Beyond Journey's End"]
    assert policy["anilist_poster_url"] == FRIEREN["cover"]


def test_removing_a_show_blacklists_it_and_deletes_its_folders(store, monkeypatch, anilist_data):
    """A library card is a whole show: every AniList entry on it is blocked.

    Exactly those, and the one picked for it -- not every entry related to
    them. A side story the card does not hold stays.
    """
    anilist_data.add(154587, "Sousou no Frieren", english="Frieren: Beyond Journey's End",
                     relations=[(182255, "SEQUEL"), (170068, "SIDE_STORY", "SPECIAL")])
    anilist_data.add(182255, "Sousou no Frieren 2nd Season", relations=[(154587, "PREQUEL")], start="2026-01-01")
    anilist_data.add(170068, "Sousou no Frieren: Marumaru no Mahou", format="SPECIAL",
                     relations=[(154587, "PARENT")])
    show = "Frieren Beyond Journey's End"
    folder = store["root"] / show
    indexed(store["root"], f"{show}/Season 01/{show} - S01E01.mkv", [(154587, 1)], b"x" * 10)
    for aid in ("154587", "182255", "170068"):
        store["state"]["anime"][aid] = {"anilist_id": int(aid), "name": "Sousou no Frieren"}
    store["state"]["releases"]["b" * 40] = {
        "status": "queued",
        "title": "[Erai-raws] Sousou no Frieren - 07 [1080p][HEVC]",
    }

    class NoQbit:
        async def login(self): ...
        async def list_torrents(self, category=None):
            return []
        async def aclose(self): ...

    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", NoQbit)

    result = asyncio.run(
        erai.blacklist_show(
            title="Frieren",
            source_title="Sousou no Frieren",
            # The card's own entries; the one picked for it (154587) is added to them.
            anilist_ids=[182255],
            folders=[folder],
            anime=FRIEREN,
        )
    )

    assert not folder.exists()
    assert result["deleted_files"] == 1
    # Each entry is its own blacklist card, restorable on its own.
    assert set(store["policies"]) == {"anilist:154587", "anilist:182255"}
    assert store["policies"]["anilist:182255"]["anilist_titles"] == ["Sousou no Frieren 2nd Season"]
    assert result["entries"] == [154587, 182255]
    assert store["state"]["releases"]["b" * 40]["status"] == "blacklisted"
    # Taken, it would have come back as an empty library card.
    assert set(store["state"]["anime"]) == {"170068"}
    # What was deleted is out of the file index.
    assert anime_files.load() == {}


def test_a_show_no_anilist_entry_answers_for_is_blacklisted_by_its_name(store, monkeypatch):
    """Nothing to block by entry; the name is what its releases will carry."""

    class NoQbit:
        async def login(self): ...
        async def list_torrents(self, category=None):
            return []
        async def aclose(self): ...

    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", NoQbit)
    store["state"]["releases"]["c" * 40] = {
        "status": "held",
        "title": "[Erai-raws] Nobody Knows This - 02 [1080p][HEVC]",
    }

    result = asyncio.run(
        erai.blacklist_show(
            title="Nobody Knows This", source_title="Nobody Knows This", anilist_ids=[], folders=[]
        )
    )

    assert result["entries"] == []
    assert list(store["policies"]) == [result["key"]]
    assert store["policies"][result["key"]]["source_title"] == "Nobody Knows This"
    assert "anilist_id" not in store["policies"][result["key"]]
    assert store["state"]["releases"]["c" * 40]["status"] == "blacklisted"


def test_the_library_root_itself_is_never_deleted(store):
    """A show folder resolving to the root would take the whole library."""
    (store["root"] / "Other Show").mkdir()
    result = asyncio.run(
        erai.purge_series("x", english_title="", delete_files=True, extra_folders=[store["root"]])
    )
    assert store["root"].exists() and (store["root"] / "Other Show").exists()
    assert result["deleted_folders"] == []


def test_no_folder_is_found_by_name(store):
    """bankai lays the library out: only the folders and files named are deleted."""
    (store["root"] / "Test Show" / "Season 01").mkdir(parents=True)
    (store["root"] / "Test Show" / "Season 01" / "Test Show - S01E01.mkv").write_bytes(b"x")

    result = asyncio.run(erai.purge_series("k", english_title="Test Show", delete_files=True))

    assert result["deleted_files"] == 0
    assert (store["root"] / "Test Show" / "Season 01" / "Test Show - S01E01.mkv").exists()


def test_discard_and_delete_takes_only_the_entry_s_own_episodes(store):
    """A show folder holds every season; one AniList entry's files are only its own.

    The old folder is named after the series, which no entry is called; the
    file index says which entry and episode each file is. A staged copy is
    the entry's too; a file of a likely name the index does not know is not.
    """
    for number in range(1, 25):
        indexed(store["root"], f"Dr. Stone/Season 04/Dr. Stone - S04E{number:02d}.mkv",
                [(1001, number) if number <= 12 else (1002, number - 12)])
    indexed(store["root"], "Dr. Stone/Season 03/Dr. Stone - S03E13.mkv", [(3, 13)])
    staged = indexed(store["staging"], "Dr. Stone/Season 05/Dr. Stone - S05E13.mkv", [(1002, 13)])
    stray = store["root"] / "Dr. Stone Science Future Dai 2 Cour" / "Dr. Stone Science Future Dai 2 Cour - 14.mkv"
    stray.parent.mkdir()
    stray.write_bytes(b"x")

    picked = erai.entry_files(1002)

    assert sorted(path.name for path in picked) == sorted(
        [f"Dr. Stone - S04E{n:02d}.mkv" for n in range(13, 25)] + [staged.name]
    )


def test_deleting_an_episode_takes_its_companions_and_its_record(store):
    show = "Test Show"
    video = indexed(store["root"], f"{show}/Season 02/{show} - S02E01.mkv", [(7, 1)])
    for extra in (".nfo", ".de.ass", "-thumb.jpg"):
        video.with_name(video.stem + extra).write_bytes(b"x")
    (video.parent / "season.nfo").write_bytes(b"x")
    (video.parent / "poster.jpg").write_bytes(b"x")
    kept = indexed(store["root"], f"{show}/Season 01/{show} - S01E01.mkv", [(6, 1)])

    result = erai._delete_series_files(delete_files=True, extra_folders=None, extra_files=[video])

    assert result["deleted_files"] == 1
    # Season 02 held nothing else but the layout's own files: it goes; the show stays.
    assert not video.parent.exists()
    assert kept.exists()
    assert str(video.parent) in result["deleted_folders"]
    assert list(anime_files.load()) == [f"{show}/Season 01/{show} - S01E01.mkv"]
