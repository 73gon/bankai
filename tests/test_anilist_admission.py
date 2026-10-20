"""Releases identified on AniList, and filed where bankai's layout puts each episode."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bankai.config import reset_settings_cache
from bankai.processor import anime as processor
from bankai.web import anime_files, erai
from bankai.web.anime import NyaaEntry

FRIEREN = 154587


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
def roots(monkeypatch, tmp_path):
    """The anime library and staging, as every module reads them from the settings."""
    library = tmp_path / "shows_anime"
    staging = tmp_path / "staging"
    library.mkdir()
    staging.mkdir()
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(library))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    monkeypatch.setenv("BANKAI_ANIME__ENABLED", "true")
    reset_settings_cache()
    yield {"library": library, "staging": staging / "Shows"}
    reset_settings_cache()


@pytest.fixture()
def world(monkeypatch, roots, anilist_data):
    """AniList knowing Frieren and a show of two cours, a Nyaa detail page listing German."""
    anilist_data.add(FRIEREN, "Sousou no Frieren", english="Frieren: Beyond Journey's End", episodes=28)
    anilist_data.add(1001, "Dr. Stone: Science Future", episodes=12, start="2025-01-09",
                     relations=[(1002, "SEQUEL")])
    anilist_data.add(1002, "Dr. Stone: Science Future Dai 2 Cour", episodes=12, start="2025-07-10",
                     relations=[(1001, "PREQUEL")])
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai, "_load_policies", lambda: {})
    monkeypatch.setattr(erai, "_SHOW_KEYS_CACHE", None)

    async def detail(_client, _url):
        return ("Subtitles Info:\nGerman (CR_German) | ASS", None, "Erai-raws")

    monkeypatch.setattr(erai.anime_mod, "_detail_url", detail)
    monkeypatch.setattr(erai.erai_site, "german", lambda info_hash: None)
    monkeypatch.setattr(erai.erai_site, "configured", lambda: False)
    added: list[dict] = []

    class Qbit:
        async def add(self, **kwargs):
            added.append(kwargs)

    admission = erai._ADMISSION.set({"qbit": Qbit(), "remaining": 10 * 1024**3})
    disk = erai._DISK_INDEX.set(None)
    yield {"state": erai._default_state(), **roots, "added": added}
    erai._ADMISSION.reset(admission)
    erai._DISK_INDEX.reset(disk)


def consider(world, item):
    return asyncio.run(erai._consider(world["state"], item, object()))


def filed(root: Path, rel: str, pairs: list[tuple[int, int]]) -> Path:
    """A file on disk that the file index knows as these episodes."""
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    anime_files.record(path, pairs)
    return path


def test_a_release_is_its_anilist_entry_and_its_own_episode_number(world):
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is True

    record = world["state"]["releases"][item.info_hash]
    assert record["status"] == "queued"
    assert record["canonical"] == f"anilist:{FRIEREN}|5"
    assert record["display_title"] == "Frieren: Beyond Journey's End - 05"
    args = record["args"]
    assert args[args.index("--anilist-id") + 1] == str(FRIEREN)
    assert args[args.index("--anilist-title") + 1] == "Frieren: Beyond Journey's End"
    assert args[args.index("--episode") + 1] == "5"
    assert "--season" not in args
    # Nothing of TVDB or AniDB is left in what publishing is told, or in the record.
    assert not any(arg.startswith(("--tvdb", "--anidb")) for arg in args)
    assert (record["anilist_id"], record["episode"]) == (FRIEREN, 5)
    assert world["state"]["anime"][str(FRIEREN)]["anilist_id"] == FRIEREN
    assert world["added"], "the torrent goes to qBittorrent once recorded"


def test_a_cour_numbered_straight_on_is_its_sequel_s_episode(world):
    """Crunchyroll's "13" of the second cour is that cour's first episode on AniList."""
    item = release("[Erai-raws] Dr. Stone Science Future - 13 [1080p][HEVC][MultiSub]", 13)

    assert consider(world, item) is True

    assert world["state"]["releases"][item.info_hash]["canonical"] == "anilist:1002|1"


def test_an_episode_already_taken_is_not_fetched_again(world):
    """The key for an episode is its AniList entry and its number there."""
    world["state"]["canonical"][f"anilist:{FRIEREN}|5"] = {"info_hash": "f" * 40, "resolution": 1080}
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False
    assert world["state"]["releases"][item.info_hash]["status"] == "duplicate"


def test_an_episode_the_file_index_has_is_existing_under_any_folder_name(world):
    """Filed by hand, or in the TVDB days, under another name: the index says what it is."""
    filed(world["library"], "Frieren/Season 01/Frieren - S01E05.mkv", [(FRIEREN, 5)])
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False
    assert world["state"]["releases"][item.info_hash]["status"] == "existing"
    # Episode 6 is not on disk, and is still fetched.
    assert consider(world, release("[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC][MultiSub]", 6)) is True


def test_a_staged_episode_counts_as_on_disk(world):
    filed(world["staging"], "Frieren/Season 01/Frieren - S01E05.mkv", [(FRIEREN, 5)])

    assert consider(world, release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")) is False


def test_a_file_the_index_does_not_know_is_not_taken_for_the_episode(world):
    """No folder-name guessing: a file of the right name but no record is not the episode."""
    stray = world["library"] / "Sousou no Frieren" / "Sousou no Frieren - 05.mkv"
    stray.parent.mkdir()
    stray.write_bytes(b"x")

    assert consider(world, release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")) is True


def test_an_indexed_file_that_is_gone_is_not_on_disk(world):
    path = filed(world["library"], "Frieren/Season 01/Frieren - S01E05.mkv", [(FRIEREN, 5)])
    path.unlink()

    assert consider(world, release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")) is True


def test_a_show_blacklisted_by_its_anilist_entry_is_blacklisted(world, monkeypatch):
    monkeypatch.setattr(
        erai, "_load_policies", lambda: {"x": {"mode": "blacklisted", "anilist_id": FRIEREN, "source_title": "x"}}
    )
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    # Told by its name alone, before anything is asked of Nyaa.
    assert erai._title_blacklisted(item.title) is True
    assert consider(world, item) is False
    assert world["state"]["releases"].get(item.info_hash, {}).get("status") != "queued"
    assert not world["added"]


def test_an_entry_blacklisted_once_resolved_is_recorded_as_such(world, monkeypatch):
    """A name the title check cannot tell, which resolves to a blocked entry on the way in."""
    policies = {"x": {"mode": "blacklisted", "anilist_id": FRIEREN, "source_title": "x"}}
    monkeypatch.setattr(erai, "_load_policies", lambda: policies)
    monkeypatch.setattr(erai, "_title_blacklisted", lambda *args, **kwargs: False)
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False
    assert world["state"]["releases"][item.info_hash]["status"] == "blacklisted"


def test_an_unknown_title_is_held_with_an_anilist_reason(world):
    """The reason names AniList, which is what offers "Match on AniList" in review."""
    item = release("[Erai-raws] Something AniList Does Not Know - 01 [1080p][HEVC]")

    assert consider(world, item) is False
    record = world["state"]["releases"][item.info_hash]
    assert record["status"] == "held"
    assert "AniList" in record["reason"]


def test_a_choice_made_in_review_wins_over_the_title_lookup(world, monkeypatch):
    key = erai._mapping_key("[Erai-raws] Frieren Oddly Named - 02 [1080p][HEVC]")
    monkeypatch.setattr(erai, "_load_mappings", lambda: {key: {"anilist_id": FRIEREN, "anilist_episode_offset": 1}})
    item = release("[Erai-raws] Frieren Oddly Named - 02 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is True
    assert world["state"]["releases"][item.info_hash]["canonical"] == f"anilist:{FRIEREN}|3"


def test_the_mapping_choice_is_saved_under_anilist_names(roots, monkeypatch, tmp_path):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    title = "[Erai-raws] Frieren Oddly Named - 02 [1080p][HEVC]"

    key = erai.save_anilist_mapping(title, {"anilist_id": FRIEREN, "name": "Frieren: Beyond Journey's End"},
                                    episode_offset=2)

    assert erai._load_mappings()[key] == {
        "anilist_id": FRIEREN,
        "anilist_title": "Frieren: Beyond Journey's End",
        "anilist_episode_offset": 2,
    }


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


@pytest.fixture()
def copies(monkeypatch):
    monkeypatch.setattr(
        processor, "_atomic_copy2", lambda src, dst, **kw: Path(dst).write_bytes(Path(src).read_bytes())
    )


def sources(tmp_path: Path, name: str, numbers) -> list[Path]:
    found = []
    for number in numbers:
        source = tmp_path / "dl" / f"[Erai-raws] {name} - {number:02d} [1080p][HEVC].mkv"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(b"video")
        found.append(source)
    return found


def organize(files, aid, **kwargs):
    defaults = {"episode_override": None, "require_german_subtitles": False, "replace_existing": False}
    return asyncio.run(processor._organize_show(files, anilist_id=aid, **{**defaults, **kwargs}))


def test_the_download_is_staged_where_the_layout_puts_the_episode(world, copies, tmp_path):
    placed = organize(sources(tmp_path, "Sousou no Frieren", [5]), FRIEREN)

    show = "Frieren Beyond Journey's End"
    expected = world["staging"] / show / "Season 01" / f"{show} - S01E05.mkv"
    assert placed == [(expected, FRIEREN, 5)]
    assert expected.read_bytes() == b"video"
    # The file index knows it, under the key it will keep in the library.
    assert anime_files.of(expected) == [(FRIEREN, 5)]
    assert anime_files.on_disk(FRIEREN) == {5: [expected]}


def test_a_batch_file_past_its_entry_s_end_goes_to_the_sequel(world, copies, tmp_path):
    placed = organize(sources(tmp_path, "Dr. Stone Science Future", [12, 13]), 1001)

    show = "Dr. Stone Science Future"
    assert [(path.relative_to(world["staging"]).as_posix(), aid, episode) for path, aid, episode in placed] == [
        (f"{show}/Season 01/{show} - S01E12.mkv", 1001, 12),
        (f"{show}/Season 02/{show} - S02E01.mkv", 1002, 1),
    ]


def test_an_episode_override_is_taken_as_it_is(world, copies, tmp_path):
    placed = organize(sources(tmp_path, "Some Name", [1]), FRIEREN, episode_override=7)

    assert [(aid, episode) for _path, aid, episode in placed] == [(FRIEREN, 7)]


def test_a_kept_episode_is_not_published(world, copies, tmp_path):
    placed = organize(sources(tmp_path, "Sousou no Frieren", [5, 6]), FRIEREN, keep_episodes=frozenset({6}))

    assert [episode for _path, _aid, episode in placed] == [5]


def test_the_episode_number_of_a_file():
    assert processor.episode_number("[Erai-raws] Show - 07 [1080p].mkv") == 7
    assert processor.episode_number("Show Episode 3.mkv") == 3
    assert processor.episode_number("anything.mkv", episode_override=4) == 4
    assert processor.episode_number("no number here.mkv") is None


class _Qbit:
    """qBittorrent with the torrent already complete in ``folder``."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.removed: list[str] = []

    async def login(self): ...

    async def list_torrents(self, **_kwargs):
        return []

    async def add(self, **_kwargs): ...

    async def top_priority(self, _hash): ...

    async def resume(self, _hash): ...

    async def force_start(self, _hash, *, enabled): ...

    async def wait_until_complete(self, _hash, **_kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(content_path=str(self.folder), save_path=str(self.folder.parent), name=self.folder.name)

    async def remove(self, info_hash, *, delete_files=False):
        self.removed.append(info_hash)

    async def aclose(self): ...


def download(monkeypatch, tmp_path, files: dict[str, bytes], **kwargs):
    folder = tmp_path / "dl" / "torrent"
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    qbit = _Qbit(folder)

    async def locate(*_args, **_kwargs):
        return "a" * 40

    monkeypatch.setattr(processor, "QBittorrentClient", lambda: qbit)
    monkeypatch.setattr(processor, "_locate_torrent", locate)
    monkeypatch.setattr(processor, "_has_german_subtitles", lambda _path: True)
    for name in ("set_stage", "set_sources", "set_transfer"):
        monkeypatch.setattr(processor.review, name, lambda *args, **kw: None)
    monkeypatch.delenv("BANKAI_BG_JOB_ID", raising=False)
    defaults = {
        "release_title": "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC]",
        "torrent_url": "https://nyaa.si/download/1.torrent",
        "detail_url": "https://nyaa.si/view/1",
        "magnet_uri": "magnet:?xt=urn:btih:" + "a" * 40,
        "info_hash": "a" * 40,
        "media_kind": "show",
        "english_title": "Frieren: Beyond Journey's End",
        "year": 2023,
        "anilist_id": FRIEREN,
        "anilist_title": "Frieren: Beyond Journey's End",
    }
    return asyncio.run(processor.download_anime(**{**defaults, **kwargs}))


def test_a_published_episode_lands_in_its_place_with_its_nfo_files(world, monkeypatch, tmp_path):
    show = "Frieren Beyond Journey's End"
    result = download(
        monkeypatch, tmp_path, {"[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"video"},
        episode_override=5, require_german_subtitles=True,
    )

    published = world["library"] / show / "Season 01" / f"{show} - S01E05.mkv"
    assert result["final_path"] == str(published)
    assert result["anilist_id"] == FRIEREN
    assert published.read_bytes() == b"video"
    # Transfer moved it to exactly its key; the file index follows it there.
    assert anime_files.on_disk(FRIEREN) == {5: [published]}
    # The collection was laid out: Jellyfin reads the show from these.
    assert (world["library"] / show / "tvshow.nfo").exists()
    assert (world["library"] / show / "Season 01" / "season.nfo").exists()
    assert "<episode>5</episode>" in published.with_suffix(".nfo").read_text(encoding="utf-8")


def test_an_upgrade_is_published_over_the_episode_it_replaces(world, monkeypatch, tmp_path):
    show = "Frieren Beyond Journey's End"
    old = filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])
    old.write_bytes(b"old avc")

    download(
        monkeypatch, tmp_path, {"[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"new hevc"},
        episode_override=5, require_german_subtitles=True, replace_existing=True,
    )

    assert old.read_bytes() == b"new hevc"
    assert not list(world["staging"].rglob("*.mkv"))


def test_without_the_subtitle_step_the_episode_waits_in_staging(world, monkeypatch, tmp_path):
    show = "Frieren Beyond Journey's End"
    result = download(
        monkeypatch, tmp_path, {
            "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"five",
            "[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC].mkv": b"six",
        },
    )

    staged = world["staging"] / show / "Season 01"
    assert result["paths"] == [str(staged / f"{show} - S01E05.mkv"), str(staged / f"{show} - S01E06.mkv")]
    assert sorted(anime_files.on_disk(FRIEREN)) == [5, 6]
