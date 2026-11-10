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


def test_a_replacement_never_writes_another_entry_s_file_over_this_one(world, copies, tmp_path):
    """Re:Zero's second season, its batch taken for the first season, replaced the first (Nov 2026)."""
    left: list = []
    files = sources(tmp_path, "Dr. Stone Science Future Dai 2 Cour", [1, 2])

    placed = organize(files, 1001, replace_existing=True, leftover=left)

    assert placed == []
    assert [(path.name, "Dai 2 Cour" in reason) for path, reason in left] == [(path.name, True) for path in files]
    # The entry's own files replace as before, and a cour numbered straight on settles into its sequel.
    placed = organize(sources(tmp_path, "Dr. Stone Science Future", [5, 13]), 1001, replace_existing=True)
    assert [(aid, episode) for _path, aid, episode in placed] == [(1001, 5), (1002, 1)]


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
        # Files set to "do not download", by name.
        self.left_out: set[str] = set()

    def _names(self) -> list[str]:
        return sorted(path.name for path in self.folder.iterdir())

    async def files(self, _hash):
        return [
            {"index": index, "name": f"{self.folder.name}/{name}", "priority": 0 if name in self.left_out else 1}
            for index, name in enumerate(self._names())
        ]

    async def file_priority(self, _hash, indexes, priority):
        assert priority == 0
        names = self._names()
        self.left_out |= {names[index] for index in indexes}

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
    download.qbit = qbit

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


def test_only_the_library_s_own_copy_counts_as_in_the_library(world):
    show = "Frieren Beyond Journey's End"
    filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])
    # Staged, not published: a download of it again carries on with it.
    filed(world["staging"], f"{show}/Season 01/{show} - S01E06.mkv", [(FRIEREN, 6)])

    assert anime_files.in_library([FRIEREN, 1001]) == {FRIEREN: {5}, 1001: set()}


def test_an_episode_the_library_has_is_left_out_while_filing(world, copies, tmp_path):
    show = "Frieren Beyond Journey's End"
    filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])
    owned_left: list[Path] = []

    placed = organize(sources(tmp_path, "Sousou no Frieren", [5, 6]), FRIEREN, skip_owned=True, owned_left=owned_left)

    assert [episode for _path, _aid, episode in placed] == [6]
    assert [path.name for path in owned_left] == ["[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv"]
    # Nothing staged beside the library's copy.
    assert not list(world["staging"].rglob("*S01E05*"))


def test_a_batch_downloads_only_the_episodes_the_library_lacks(world, monkeypatch, tmp_path):
    show = "Frieren Beyond Journey's End"
    old = filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])
    old.write_bytes(b"library copy")

    result = download(
        monkeypatch, tmp_path, {
            "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"other encode",
            "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].en.ass": b"subs",
            "[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC].mkv": b"six",
        },
        release_title="[Erai-raws] Sousou no Frieren - 05 ~ 06 [1080p][HEVC]", publish=True,
    )

    # qBittorrent was told to leave out episode 5 and its subtitle.
    assert download.qbit.left_out == {
        "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv",
        "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].en.ass",
    }
    assert result["paths"] == [str(world["library"] / show / "Season 01" / f"{show} - S01E06.mkv")]
    assert old.read_bytes() == b"library copy"
    assert not list(world["staging"].rglob("*.mkv"))


def test_a_release_the_library_has_whole_is_not_downloaded(world, monkeypatch, tmp_path):
    show = "Frieren Beyond Journey's End"
    filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])

    result = download(
        monkeypatch, tmp_path, {"[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"again"},
        episode_override=5, publish=True,
    )

    assert result["already_in_library"] == 1
    assert result["paths"] == []
    assert download.qbit.removed == ["a" * 40]


def test_a_replacement_still_downloads_the_episode_the_library_has(world, monkeypatch, tmp_path):
    show = "Frieren Beyond Journey's End"
    old = filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])

    download(
        monkeypatch, tmp_path, {"[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"new"},
        episode_override=5, publish=True, replace_existing=True,
    )

    assert download.qbit.left_out == set()
    assert old.read_bytes() == b"new"


def test_discover_rows_say_which_episodes_the_library_has(world):
    from bankai.web import anime as anime_mod

    show = "Frieren Beyond Journey's End"
    filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])
    rows = [{"anilist": {"anilist_id": FRIEREN, "episode": None}}, {"anilist": None}]

    anime_mod.mark_owned(rows)

    assert rows[0]["anilist"]["owned"] == [5]
    assert rows[0]["anilist"]["episodes"] == 28
    assert rows[1]["anilist"] is None


def test_rows_say_which_languages_the_library_s_episodes_have_and_what_german_a_release_adds(world, monkeypatch):
    from bankai.web import anime as anime_mod
    from bankai.web import track_cleanup

    show = "Frieren Beyond Journey's End"
    five = filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])
    six = filed(world["library"], f"{show}/Season 01/{show} - S01E06.mkv", [(FRIEREN, 6)])
    # German Erai-raws uploaded later, beside episode 6.
    six.with_name(six.stem + ".de.ass").write_text("subs")
    tracks = [{"kind": "audio", "lang": "jpn"}, {"kind": "subtitles", "lang": "eng"}]
    monkeypatch.setattr(track_cleanup, "load_inventory", lambda: {str(five): {"tracks": tracks}, str(six): {"tracks": tracks}})
    batch = {"anilist": {"anilist_id": FRIEREN, "episode": None}, "audio_langs": ["ja", "de"], "sub_langs": ["en", "de"]}
    single = {"anilist": {"anilist_id": FRIEREN, "episode": 6}, "sub_langs": ["de"], "fansub_langs": []}

    anime_mod.mark_owned([batch, single])

    assert batch["anilist"]["languages"] == {"read": 2, "audio": {"jpn": 2}, "subtitles": {"eng": 2, "ger": 1}}
    assert batch["anilist"]["gains"] == [
        {"kind": "audio", "language": "ger", "episodes": 2, "of": 2},
        {"kind": "subtitles", "language": "ger", "episodes": 1, "of": 2},
    ]
    # Episode 6 has German subtitles already: nothing gained.
    assert single["anilist"]["languages"]["subtitles"] == {"eng": 1, "ger": 1}
    assert single["anilist"]["gains"] == []


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


# -- German subtitles Erai-raws uploaded later (see erai_subs) ------------------

SUB_URL = "https://www.erai-raws.info/subs/Sub/2023/Fall/Frieren/05/%5BErai-raws%5D%20Sousou%20no%20Frieren%20-%2005%20%5B1080p%5D.ger.ass"


@pytest.fixture()
def uploaded_german(monkeypatch):
    """The member login set, and Erai-raws having uploaded a German file for each release asked about."""
    from bankai.web import erai_subs

    found: dict[str, str] = {}

    async def lookup(name, info_hash=None):
        found[info_hash] = SUB_URL
        return SUB_URL

    monkeypatch.setattr(erai_subs, "configured", lambda: True)
    monkeypatch.setattr(erai_subs, "german_sub_file", lookup)
    monkeypatch.setattr(erai_subs, "cached", lambda name, info_hash=None: found.get(info_hash))
    return found


def _no_german_on_nyaa(monkeypatch):
    async def detail(_client, _url):
        return ("Subtitles Info:\nEnglish | ASS\nPortuguese | ASS", None, "Erai-raws")

    monkeypatch.setattr(erai.anime_mod, "_detail_url", detail)


def test_a_release_without_german_is_taken_with_the_german_file_erai_raws_uploaded(world, monkeypatch, uploaded_german):
    _no_german_on_nyaa(monkeypatch)
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is True

    args = world["state"]["releases"][item.info_hash]["args"]
    assert args[args.index("--german-sub-url") + 1] == SUB_URL
    # Still checked for German when published: the file is staged beside it first.
    assert "--require-german-subtitles" in args


def test_without_that_file_or_the_login_such_a_release_is_held(world, monkeypatch):
    _no_german_on_nyaa(monkeypatch)
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert consider(world, item) is False

    record = world["state"]["releases"][item.info_hash]
    assert record["status"] == "held" and "German" in record["reason"]


def test_an_upgrade_or_missing_episode_counts_the_uploaded_german(world, monkeypatch, uploaded_german):
    from bankai.web import anime_missing

    async def erai_says_none(_entry):
        return False

    monkeypatch.setattr(erai, "_erai_german", erai_says_none)
    item = release("[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC][MultiSub]")

    assert asyncio.run(erai._carries_german(item, object())) is True
    assert anime_missing._release_row(item, 5, 5)["german_sub_url"] == SUB_URL
    # A batch is no one episode's file.
    assert anime_missing._release_row(item, 1, 12)["german_sub_url"] is None


def test_the_uploaded_german_is_staged_beside_the_video_and_passes_the_check(world, copies, tmp_path, monkeypatch):
    from bankai.web import erai_subs

    # Nothing German in the torrent itself.
    monkeypatch.setattr(processor, "_has_german_subtitles", lambda _path: False)
    fetched = []

    async def fetch(url, target):
        fetched.append((url, target))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[Script Info]\n", encoding="utf-8")
        return True

    monkeypatch.setattr(erai_subs, "download", fetch)

    placed = organize(
        sources(tmp_path, "Sousou no Frieren", [5]), FRIEREN, require_german_subtitles=True, german_sub_url=SUB_URL
    )

    staged = placed[0][0]
    sidecar = staged.with_name(f"{staged.stem}.de.ass")
    assert fetched == [(SUB_URL, sidecar)]
    assert sidecar.exists() and staged.exists()


def test_the_german_check_still_refuses_a_release_with_none(world, copies, tmp_path, monkeypatch):
    monkeypatch.setattr(processor, "_has_german_subtitles", lambda _path: False)

    with pytest.raises(RuntimeError, match="German subtitle verification failed"):
        organize(sources(tmp_path, "Sousou no Frieren", [5]), FRIEREN, require_german_subtitles=True)


def test_the_uploaded_german_is_published_beside_the_episode(world, monkeypatch, tmp_path):
    from bankai.web import erai_subs

    async def fetch(url, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[Script Info]\n", encoding="utf-8")
        return True

    monkeypatch.setattr(erai_subs, "download", fetch)
    show = "Frieren Beyond Journey's End"
    download(
        monkeypatch, tmp_path, {"[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"video"},
        episode_override=5, require_german_subtitles=True, german_sub_url=SUB_URL,
    )

    published = world["library"] / show / "Season 01" / f"{show} - S01E05.mkv"
    assert published.exists()
    assert published.with_name(f"{show} - S01E05.de.ass").read_text(encoding="utf-8") == "[Script Info]\n"


# -- Files no episode can be told of: kept for matching by hand (see anime_unsorted) --

def test_a_season_and_episode_marker_is_read():
    name = "[SavI0r] Fullmetal Alchemist Brotherhood - S01E46 - Looming Shadows [BD][1078p][AV1].mkv"
    assert processor.episode_number(name) == 46
    assert processor.season_episode(name) == (1, 46)
    # Season 0 is specials: no episode of the show it came with.
    assert processor.episode_number("[SavI0r] Show - S00E01 -  [BD].mkv") is None
    assert processor.episode_number("Show S2E05.mkv") == 5


def batch(tmp_path: Path, names: list[str]) -> list[Path]:
    found = []
    for name in names:
        source = tmp_path / "dl" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        found.append(source)
    return found


def test_specials_in_a_season_batch_are_left_over_not_filed(world, copies, tmp_path):
    files = batch(tmp_path, ["Show - S01E01 - A.mkv", "Show - S01E02 - B.mkv", "OVA/Show - S00E01 - .mkv"])
    leftover: list = []

    placed = organize(files, FRIEREN, leftover=leftover)

    assert [(aid, episode) for _path, aid, episode in placed] == [(FRIEREN, 1), (FRIEREN, 2)]
    assert [(path.name, reason) for path, reason in leftover] == [("Show - S00E01 - .mkv", "a special (season 0)")]


def test_a_batch_s_leftovers_wait_in_review_and_the_download_does_not_fail(world, monkeypatch, tmp_path):
    from bankai.web import anime_unsorted

    result = download(
        monkeypatch, tmp_path, {"Show - S01E05 - Five.mkv": b"five", "Show - S00E02 - Recap.mkv": b"ova"},
        release_title="[SavI0r] Frieren S1 + OVAs [BD]",
    )

    assert sorted(anime_files.on_disk(FRIEREN)) == [5]
    assert result["kept_for_review"] == 1
    [group] = anime_unsorted.groups()
    assert group["release_title"] == "[SavI0r] Frieren S1 + OVAs [BD]"
    assert group["anilist_id"] == FRIEREN
    [kept] = group["files"]
    assert (kept["name"], kept["season"], kept["episode"]) == ("Show - S00E02 - Recap.mkv", 0, 2)
    assert (anime_unsorted.root() / "[SavI0r] Frieren S1 + OVAs [BD]" / kept["name"]).read_bytes() == b"ova"


def test_a_download_of_only_unknown_files_waits_whole(world, monkeypatch, tmp_path):
    from bankai.web import anime_unsorted

    result = download(monkeypatch, tmp_path, {"NCOP.mkv": b"op", "NCED.mkv": b"ed"})

    assert result["final_path"] is None
    assert result["kept_for_review"] == 2
    assert sorted(item["name"] for item in anime_unsorted.groups()[0]["files"]) == ["NCED.mkv", "NCOP.mkv"]


def test_a_left_over_file_matched_by_hand_goes_into_the_library_with_the_show(world, monkeypatch, anilist_data, tmp_path):
    from bankai.web import anime_collections, anime_unsorted

    anilist_data.add(FRIEREN, "Sousou no Frieren", english="Frieren: Beyond Journey's End", episodes=28,
                     relations=[(2001, "SIDE_STORY")])
    anilist_data.add(2001, "Sousou no Frieren: Mini Anime", format="ONA", episodes=4,
                     relations=[(FRIEREN, "PARENT")])
    download(
        monkeypatch, tmp_path, {"Show - S01E05 - Five.mkv": b"five", "Show - S00E03 - Mini.mkv": b"mini"},
        require_german_subtitles=True,
    )
    [group] = anime_unsorted.groups()
    assert [item["anilist_id"] for item in group["suggestions"]][:2] == [FRIEREN, 2001]
    [kept] = group["files"]

    result = asyncio.run(anime_unsorted.file_into([kept["id"]], 2001, [3]))

    assert result["filed"] == 1
    [published] = anime_files.on_disk(2001)[3]
    assert published.is_relative_to(world["library"]) and published.read_bytes() == b"mini"
    # The side story joined the show it was downloaded with, not a show of its own.
    store = anime_collections.load()
    assert store.collection_of(2001).id == store.collection_of(FRIEREN).id
    assert anime_unsorted.groups() == []
    assert not list(anime_unsorted.root().rglob("*.mkv"))


def test_matching_over_an_episode_the_library_has_asks_first(world, monkeypatch, tmp_path):
    from bankai.web import anime_unsorted

    download(monkeypatch, tmp_path, {"Show - S01E05 - Five.mkv": b"five", "Extra.mkv": b"fiv2"})
    [kept] = anime_unsorted.groups()[0]["files"]

    with pytest.raises(anime_unsorted.Taken) as taken:
        asyncio.run(anime_unsorted.file_into([kept["id"]], FRIEREN, [5]))
    assert taken.value.episodes == [5]

    asyncio.run(anime_unsorted.file_into([kept["id"]], FRIEREN, [5], replace=True))
    [only] = anime_files.on_disk(FRIEREN)[5]
    assert only.read_bytes() == b"fiv2"


def test_an_episode_map_files_each_number_as_the_ticked_entry_and_leaves_the_rest_out(world, copies, tmp_path):
    """Replace with magnet link: the torrent's episode 13 is the ticked S01E13, of the sequel entry."""
    left: list = []
    placed = organize(
        sources(tmp_path, "Dr. Stone Science Future", [12, 13, 14]), 1001,
        episode_map={12: (1001, 12), 13: (1002, 1)}, leftover=left,
    )
    assert [(aid, episode) for _path, aid, episode in placed] == [(1001, 12), (1002, 1)]
    assert [reason for _source, reason in left] == ["not ticked to be replaced"]


def test_a_torrent_numbered_by_its_entry_is_matched_to_the_episodes_ticked_by_the_library_s_numbers(world, copies, tmp_path):
    """Dr. STONE New World Part 2 is the library's S03E12-E22, Erai's batch 01-11: none was "ticked",
    and the download failed five times as holding no anime files (Nov 2026)."""
    placed = organize(
        sources(tmp_path, "Dr. Stone Science Future", [1, 2, 3]), 1001,
        episode_map={12: (1001, 1), 13: (1001, 2), 14: (1001, 3)},
    )
    assert sorted((aid, episode) for _path, aid, episode in placed) == [(1001, 1), (1001, 2), (1001, 3)]


def test_the_part_of_an_episode_a_file_is():
    assert processor.part_number("[Erai-raws] Re.Zero kara Hajimeru Isekai Seikatsu - 01 (Part One) [1080p].mkv") == 1
    assert processor.part_number("[Erai-raws] Re.Zero kara Hajimeru Isekai Seikatsu - 01 (Part Two) [1080p].mkv") == 2
    assert processor.part_number("Show - 05 Pt.2.mkv") == 2
    assert processor.part_number("[Erai-raws] Re.Zero kara Hajimeru Isekai Seikatsu - 02 [1080p].mkv") is None
    assert processor.part_number("Party Time - 03.mkv") is None


def test_an_episode_in_two_parts_is_joined_not_written_over(world, copies, monkeypatch, tmp_path):
    """Erai's Re:Zero batch has "01 (Part One)" and "01 (Part Two)": both were S01E01, Part Two over Part One (Nov 2026)."""
    folder = tmp_path / "dl"
    folder.mkdir()
    names = ["Sousou no Frieren - 05 (Part One) [1080p].mkv", "Sousou no Frieren - 05 (Part Two) [1080p].mkv",
             "Sousou no Frieren - 06 [1080p].mkv"]
    files = []
    for name in names:
        (folder / name).write_bytes(name.encode())
        files.append(folder / name)
    joined: list = []
    monkeypatch.setattr(processor, "_join_parts", lambda parts, dst: (joined.append([p.name for p in parts]), dst.write_bytes(b"both")))

    placed = organize(list(reversed(files)), FRIEREN)

    assert sorted(episode for _path, _aid, episode in placed) == [5, 6]
    assert joined == [names[:2]]
    five = next(path for path, _aid, episode in placed if episode == 5)
    assert five.read_bytes() == b"both"


def test_a_second_file_of_an_episode_waits_to_be_matched(world, copies, tmp_path):
    folder = tmp_path / "dl"
    folder.mkdir()
    files = []
    for name in ["[Erai-raws] Sousou no Frieren - 05 [1080p].mkv", "[Erai-raws] Sousou no Frieren - 05v2 [1080p].mkv"]:
        (folder / name).write_bytes(name.encode())
        files.append(folder / name)
    left: list = []

    placed = organize(files, FRIEREN, leftover=left)

    assert [(path.read_bytes(), episode) for path, _aid, episode in placed] == [(files[0].name.encode(), 5)]
    assert [(source.name, reason) for source, reason in left] == [(files[1].name, "a second file of episode 5")]


def test_the_parts_subtitle_scripts_are_joined_with_their_own_styles():
    def script(look: str, line: str) -> str:
        return (
            "[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname\n"
            f"Style: Default,Arial\nStyle: Title,{look}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Text\n"
            f"Dialogue: 0,0:00:01.00,0:00:02.50,Title,{line}\nComment: 0,0:00:03.00,0:00:04.00,Default,note, with comma\n"
        )

    merged = processor.merge_ass([script("Times", "one"), script("Verdana", "two")], [0.0, 1500.25])

    assert "Style: Title,Times" in merged and "Style: Title 2,Verdana" in merged
    assert merged.count("Style: Default,Arial") == 1
    assert "Dialogue: 0,0:00:01.00,0:00:02.50,Title,one" in merged
    assert "Dialogue: 0,0:25:01.25,0:25:02.75,Title 2,two" in merged
    assert "Comment: 0,0:25:03.25,0:25:04.25,Default,note, with comma" in merged
    # Another resolution: not joined script by script.
    other = script("Times", "x").replace("PlayResY: 1080", "PlayResY: 720")
    assert processor.merge_ass([script("Times", "one"), other], [0.0, 10.0]) is None


def test_a_replacement_with_an_episode_map_downloads_only_the_ticked_episodes(world, monkeypatch, tmp_path):
    show = "Frieren Beyond Journey's End"
    filed(world["library"], f"{show}/Season 01/{show} - S01E05.mkv", [(FRIEREN, 5)])

    download(
        monkeypatch, tmp_path, {
            "[Erai-raws] Sousou no Frieren - 05 [1080p][HEVC].mkv": b"new five",
            "[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC].mkv": b"six",
            "[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC].en.ass": b"subs",
        },
        release_title="[Erai-raws] Sousou no Frieren - 05 ~ 06 [1080p][HEVC]", publish=True,
        replace_existing=True, episode_map={5: (FRIEREN, 5)},
    )

    assert download.qbit.left_out == {
        "[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC].mkv",
        "[Erai-raws] Sousou no Frieren - 06 [1080p][HEVC].en.ass",
    }
