"""Releases identified on AniDB, the only route since TVDB left the anime side."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bankai.config import Settings
from bankai.metadata import anidb
from bankai.processor import anime as processor
from bankai.web import anime_library, erai, shoko_catalog
from bankai.web.anime import NyaaEntry

FRIEREN = anidb.AniDBAnime(
    aid=17617,
    title="Sousou no Frieren",
    english_title="Frieren: Beyond Journey's End",
    titles=("Sousou no Frieren",),
    tvdb_id=424536,
    tvdb_season="1",
    tvdb_offset=0,
)


def release(title: str, number: int = 1) -> NyaaEntry:
    return NyaaEntry(
        id=number,
        title=title,
        download_url=f"https://nyaa.si/download/{number}.torrent",
        detail_url=f"https://nyaa.si/view/{number}",
        magnet_uri=f"magnet:?xt=urn:btih:{number:040x}",
        info_hash=f"{number:040x}",
        category_id="1_2",
        category="Anime",
        size="1 GiB",
        size_bytes=1024**3,
        seeders=10,
        leechers=2,
        downloads=100,
        comments=0,
        trusted=True,
        remake=False,
        published_at="Mon, 01 Jan 2024 00:00:00 +0000",
        publisher="Erai-raws",
        quality="1080p",
    )


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """A library root, AniDB knowing Frieren, a Nyaa detail page listing German."""
    library = tmp_path / "shows_anime"
    library.mkdir()
    settings = Settings(anime={"enabled": True}, transfer={"anime_shows_dir": str(library)})
    monkeypatch.setattr(erai, "get_settings", lambda: settings)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    # What places a file of any folder name: Shoko's links, the user's folder links.
    monkeypatch.setattr(shoko_catalog, "saved_links", lambda: None)
    monkeypatch.setattr(anime_library, "load_prefs", lambda: {})
    monkeypatch.setattr(erai, "_load_policies", lambda: {})
    monkeypatch.setattr(erai, "_SHOW_KEYS_CACHE", None)

    async def resolve(name):
        if anidb.normalise(name) == "sousou no frieren":
            return anidb.Resolution(FRIEREN, "exact")
        return anidb.Resolution(error="No AniDB anime has this title")

    monkeypatch.setattr(erai.anidb_mod, "resolve", resolve)

    async def settle(entry, episode):
        return entry, episode  # covered in test_anidb_identity; never the network here

    monkeypatch.setattr(erai.anidb_mod, "settle", settle)

    async def detail(_client, _url):
        return ("Subtitles Info:\nGerman (CR_German) | ASS", None, "Erai-raws")

    monkeypatch.setattr(erai.anime_mod, "_detail_url", detail)
    added: list[dict] = []

    class Qbit:
        async def add(self, **kwargs):
            added.append(kwargs)

    admission = erai._ADMISSION.set({"qbit": Qbit(), "remaining": 10 * 1024**3})
    disk = erai._DISK_INDEX.set(None)
    yield {"state": erai._default_state(), "library": library, "added": added}
    erai._ADMISSION.reset(admission)
    erai._DISK_INDEX.reset(disk)


def consider(world, item):
    return asyncio.run(erai._consider(world["state"], item, object()))


def test_a_release_is_its_anidb_anime_and_its_own_episode_number(world):
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is True

    record = world["state"]["releases"][item.info_hash]
    assert record["status"] == "queued"
    assert record["canonical"] == "anidb:17617|5"
    assert record["display_title"] == "Frieren: Beyond Journey's End - 05"
    args = record["args"]
    assert args[args.index("--anidb-id") + 1] == "17617"
    assert args[args.index("--anidb-title") + 1] == "Sousou no Frieren"
    assert args[args.index("--episode") + 1] == "5"
    assert "--season" not in args
    # Nothing of TVDB is left in what publishing is told, or in the record.
    assert "--tvdb-id" not in args
    assert (record["anidb_id"], record["episode"]) == (17617, 5)
    assert "tvdb_id" not in record
    assert "tvdb_id" not in world["state"]["anime"]["17617"]
    assert world["added"], "the torrent goes to qBittorrent once recorded"


def test_an_episode_already_taken_is_not_fetched_again(world):
    """The key for an episode is its AniDB entry and its number there."""
    world["state"]["canonical"]["anidb:17617|5"] = {"info_hash": "f" * 40, "resolution": 1080}
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False
    assert world["state"]["releases"][item.info_hash]["status"] == "duplicate"


def test_an_episode_already_filed_under_its_anidb_folder_is_existing(world):
    folder = world["library"] / "Sousou no Frieren"
    folder.mkdir()
    (folder / "Sousou no Frieren - 05.mkv").write_bytes(b"x")
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False
    assert world["state"]["releases"][item.info_hash]["status"] == "existing"


def test_an_episode_in_the_old_tvdb_folder_is_existing(world, monkeypatch):
    """Filed in the TVDB days under another name; Shoko's link says which episode it is."""
    season = world["library"] / "Frieren" / "Season 01"
    season.mkdir(parents=True)
    old = season / "Frieren - S01E05.mkv"
    old.write_bytes(b"x")
    monkeypatch.setattr(
        shoko_catalog, "saved_links", lambda: shoko_catalog.Catalog(files={str(old): [(17617, 5)]})
    )
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False
    assert world["state"]["releases"][item.info_hash]["status"] == "existing"
    # Episode 6 is not in that folder, and is still fetched.
    assert consider(world, release("[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC][MultiSub]", 6)) is True


def test_a_show_blacklisted_by_its_anidb_anime_is_blacklisted(world, monkeypatch):
    monkeypatch.setattr(
        erai, "_load_policies", lambda: {"x": {"mode": "blacklisted", "anidb_id": 17617, "source_title": "x"}}
    )
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False
    assert world["state"]["releases"][item.info_hash]["status"] == "blacklisted"


def test_an_unknown_title_is_held_with_an_anidb_reason(world):
    """The reason names AniDB, which is what offers "Choose AniDB anime" in review."""
    item = release("[Erai-raws] Something AniDB Does Not Know - 01 [1080p][HEVC]")

    assert consider(world, item) is False
    record = world["state"]["releases"][item.info_hash]
    assert record["status"] == "held"
    assert "AniDB" in record["reason"]


def test_a_choice_made_in_review_wins_over_the_title_lookup(world, monkeypatch):
    key = erai._mapping_key("[Erai-raws] Frieren Oddly Named - 02 [1080p][HEVC]")
    monkeypatch.setattr(erai, "_load_mappings", lambda: {key: {"anidb_id": 17617}})

    async def known(aid):
        return FRIEREN if aid == 17617 else None

    monkeypatch.setattr(erai.anidb_mod, "anime", known)
    item = release("[Erai-raws] Frieren Oddly Named - 02 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is True
    assert world["state"]["releases"][item.info_hash]["canonical"] == "anidb:17617|2"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("[Erai-raws] Yuru Camp Season 2 - 13 END [1080p HEVC][Multiple Subtitle].mkv", ("Yuru Camp Season 2", 13)),
        ("[Erai-raws] Oshi no Ko 2nd Season - 01 (Repack) [1080p][HEVC]", ("Oshi no Ko 2nd Season", 1)),
        ("[Erai-raws] Shiguang Dailiren Season 3 - 07 (CA) [1080p CR WEBRip HEVC AAC]", ("Shiguang Dailiren Season 3", 7)),
        ("[Erai-raws] Kidou Senshi Gundam - Suisei no Majo - 05 [1080p][HEVC]", ("Kidou Senshi Gundam - Suisei no Majo", 5)),
        ("[Erai-raws] One Piece - 1100 [1080p][HEVC]", ("One Piece", 1100)),
        ("[Erai-raws] Dandadan - 03v2 [1080p][HEVC]", ("Dandadan", 3)),
        ("[Erai-raws] Some Show - 01 ~ 12 [1080p][HEVC][BATCH]", None),
    ],
)
def test_the_name_and_episode_of_an_erai_release(title, expected):
    assert erai._erai_name_episode(title) == expected


def test_the_download_files_one_folder_per_anidb_entry(tmp_path, monkeypatch):
    source = tmp_path / "dl" / "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv"
    source.parent.mkdir()
    source.write_bytes(b"video")
    monkeypatch.setattr(processor, "_atomic_copy2", lambda src, dst, **kw: Path(dst).write_bytes(Path(src).read_bytes()))

    outputs = processor._organize_anidb(
        [source],
        title="Sousou no Frieren",
        episode_override=None,
        library=tmp_path / "library",
        require_german_subtitles=False,
        replace_existing=False,
    )

    assert outputs == [tmp_path / "library" / "Shows" / "Sousou no Frieren" / "Sousou no Frieren - 05.mkv"]
    assert outputs[0].read_bytes() == b"video"
