"""Taking an Erai-raws release, batch or episode, in place of what a review card holds."""

from __future__ import annotations

import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from bankai.config import Settings
from bankai.metadata import anidb
from bankai.processor import anime as processor
from bankai.web import erai

BATCH = "[Erai-raws] Dr. Stone Science Future (2025) - 01 ~ 12 [1080p][Multiple Subtitle]"


@pytest.fixture()
def library(monkeypatch, tmp_path):
    """Dr. Stone season 4 in a TVDB folder; AniDB entry 2 is its second half."""
    state = {"releases": {}, "held": [], "series": {"99": {"english_title": "Dr. Stone"}}, "canonical": {}}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    root = tmp_path / "shows_anime"
    season = root / "Dr. Stone" / "Season 04"
    season.mkdir(parents=True)
    for number in range(1, 25):
        (season / f"Dr. Stone - S04E{number:02d}.mkv").write_bytes(b"old")
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
    monkeypatch.setattr(erai, "get_settings", lambda: Settings(transfer={"anime_shows_dir": str(root)}))
    monkeypatch.setattr(erai, "_series_roots", lambda: [root])
    monkeypatch.setattr(
        "bankai.backend.transfer._existing_show_folder",
        lambda name, cache, roots=None: root / "Dr. Stone",
    )
    monkeypatch.setattr(erai, "_release_anidb_id", lambda title, **kw: 2)
    monkeypatch.setattr(
        erai.erai_site,
        "lookup",
        lambda info_hash: {"name": BATCH, "subs": ["us", "de"]} if info_hash == "a" * 40 else None,
    )
    # Episode 3 of the entry, S04E15 on disk, carries a German dub.
    monkeypatch.setattr(erai, "_is_german_dub", lambda path: path.name.endswith("S04E15.mkv"))
    return {"state": state, "root": root, "season": season}


def held(state, info_hash, title):
    state["releases"][info_hash] = {"status": "held", "title": title, "reason": "no German"}


def test_a_batch_replaces_its_held_episodes_and_files_but_never_a_german_dub(library):
    held(library["state"], "1" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 05 [1080p HEVC][Multiple Subtitle].mkv")
    held(library["state"], "2" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 01 ~ 04 [1080p HEVC][Multiple Subtitle]")
    # Outside the batch's episodes: stays in review.
    held(library["state"], "3" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 13 [1080p HEVC][Multiple Subtitle].mkv")

    plan = erai._replacement_plan("anidb:2", "a" * 40)

    assert (plan["first"], plan["last"], plan["batch"], plan["german"]) == (1, 12, True, True)
    assert sorted(plan["held"]) == ["1" * 40, "2" * 40]
    assert plan["german_dubs_kept"] == [3]
    # The entry's half of the TVDB season, S04E13-S04E24, less the dubbed one.
    names = sorted(Path(row["path"]).name for row in plan["files"])
    assert names == [f"Dr. Stone - S04E{n:02d}.mkv" for n in range(13, 25) if n != 15]


def test_a_release_of_another_entry_is_refused(library, monkeypatch):
    monkeypatch.setattr(erai, "_release_anidb_id", lambda title, **kw: 1)
    with pytest.raises(ValueError, match="not the AniDB entry of this card"):
        erai._replacement_plan("anidb:2", "a" * 40)


def test_a_release_already_downloading_is_refused(library):
    library["state"]["releases"]["a" * 40] = {"status": "downloading", "title": BATCH}
    with pytest.raises(ValueError, match="already downloading"):
        erai._replacement_plan("anidb:2", "a" * 40)


def test_the_old_files_go_once_the_new_ones_are_published(library):
    season, root = library["season"], library["root"]

    def recorded(name, episode):
        stat = (season / name).stat()
        return {"path": str(season / name), "episode": episode, "size": stat.st_size, "mtime": stat.st_mtime}

    release = {
        "anidb_id": 2,
        "replace_files": [
            recorded("Dr. Stone - S04E13.mkv", 1),
            recorded("Dr. Stone - S04E14.mkv", 2),
            recorded("Dr. Stone - S04E16.mkv", 4),
        ],
        "replaced_holds": ["1" * 40],
    }
    own = root / "Dr. Stone Science Future (2025)"
    own.mkdir()
    (own / "Dr. Stone Science Future (2025) - 01.mkv").write_bytes(b"new")
    (own / "Dr. Stone Science Future (2025) - 04.mkv").write_bytes(b"new")
    # Episode 4's old file changed since it was recorded: not the one to remove.
    changed = season / "Dr. Stone - S04E16.mkv"
    os.utime(changed, (1, 1))

    removed = erai._finish_replacement(release)

    assert removed == 1
    assert not (season / "Dr. Stone - S04E13.mkv").exists()
    # No new file for episode 2 arrived: the old one stays.
    assert (season / "Dr. Stone - S04E14.mkv").exists()
    assert changed.exists()
    assert "replace_files" not in release


def test_a_replacement_that_fails_for_good_puts_the_episodes_back_in_review(library):
    state = library["state"]
    state["releases"]["1" * 40] = {"status": erai.REPLACED_STATUS, "title": "x", "reason": "Replaced by y"}
    release = {"status": "failed", "reason": "Publishing job failed", "replaced_holds": ["1" * 40], "replace_files": []}

    erai._undo_replacement(state, release)

    assert state["releases"]["1" * 40]["status"] == "held"
    assert "replaced_holds" not in release


def test_a_kept_episode_is_not_published(tmp_path, monkeypatch):
    sources = []
    for number in (5, 6):
        source = tmp_path / "dl" / f"[Erai-raws] Sousou no Frieren - {number:02d} [1080p][HEVC].mkv"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(b"video")
        sources.append(source)
    monkeypatch.setattr(processor, "_atomic_copy2", lambda src, dst, **kw: Path(dst).write_bytes(Path(src).read_bytes()))

    outputs = processor._organize_anidb(
        sources,
        title="Sousou no Frieren",
        episode_override=None,
        library=tmp_path / "library",
        require_german_subtitles=False,
        replace_existing=True,
        keep_episodes=frozenset({6}),
    )

    assert [path.name for path in outputs] == ["Sousou no Frieren - 05.mkv"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("[Erai-raws] Vinland Saga Season 2 - 01 ~ 24 (NF) [1080p][Multiple Subtitle]", (1, 24)),
        ("[Erai-raws] Vinland Saga - 24 END [1080p HEVC][Multiple Subtitle].mkv", (24, 24)),
        ("[Erai-raws] Vinland Saga - 11 ~ 21 [1080p HEVC][Multiple Subtitle]", (11, 21)),
        ("[Erai-raws] Vinland Saga [1080p]", None),
    ],
)
def test_the_episodes_a_release_covers(title, expected):
    assert erai._release_range(title) == expected


def test_a_release_is_found_on_nyaa_under_a_title_that_differs_from_erai_s(monkeypatch):
    """Erai-raws said EAC3 and gave a checksum; Nyaa's title said AAC and had none."""
    import asyncio

    from bankai.web.anime import NyaaEntry

    info_hash = "41dc3dd4318453b5736825a0b1a07b597c69c70c"
    nyaa = NyaaEntry(
        id=1, title="[Erai-raws] Watashi no Shiawase na Kekkon 2nd Season - 12 (REPACK) [1080p NF WEBRip HEVC AAC][MultiSub]",
        download_url="https://nyaa.si/download/1.torrent", detail_url="https://nyaa.si/view/1",
        magnet_uri=f"magnet:?xt=urn:btih:{info_hash}", info_hash=info_hash, category_id="1_2", category="Anime",
        size="701 MiB", size_bytes=1, seeders=1, leechers=0, downloads=0, comments=0,
        trusted=False, remake=False, published_at="", publisher="Erai-raws", quality="1080p",
    )
    asked = []

    class Client:
        async def get(self, url, params):
            asked.append(params["q"])
            text = "match" if params["q"] == "Watashi no Shiawase na Kekkon 2nd Season 12" else ""
            return type("R", (), {"text": text, "raise_for_status": lambda self: None})()

    monkeypatch.setattr(erai, "parse_listing", lambda text: [nyaa] if text == "match" else [])
    name = "[Erai-raws] Watashi no Shiawase na Kekkon 2nd Season - 12 (REPACK) [1080p NF WEBRip HEVC EAC3][MultiSub][0DA30B54].mkv"

    found = asyncio.run(erai._nyaa_release(name, info_hash, Client()))

    assert found is nyaa
    assert asked[-1] == "Watashi no Shiawase na Kekkon 2nd Season 12"


def test_the_nyaa_lookup_started_for_the_plan_is_reused_by_the_replace(monkeypatch):
    import asyncio

    calls = []

    async def lookup(name, info_hash, client):
        calls.append(info_hash)
        return "entry"

    monkeypatch.setattr(erai, "_nyaa_release", lookup)
    monkeypatch.setattr(erai, "_NYAA_LOOKUPS", {})

    async def go():
        first = erai._find_on_nyaa("name", "b" * 40)
        await first
        return await erai._find_on_nyaa("name", "b" * 40)

    assert asyncio.run(go()) == "entry"
    assert calls == ["b" * 40]


def test_a_batch_held_for_the_card_is_planned_from_its_own_release(library):
    """No Erai-raws listing needed: the held batch is the release to take."""
    title = "[Erai-raws] Dr. Stone Science Future (2025) - 01 ~ 12 [1080p][HEVC][BATCH][Multiple Subtitle] [ENG][GER]"
    held(library["state"], "c" * 40, title)
    held(library["state"], "1" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 05 [1080p HEVC][Multiple Subtitle].mkv")

    plan = erai._replacement_plan("anidb:2", "c" * 40)

    assert (plan["name"], plan["first"], plan["last"], plan["german"]) == (title, 1, 12, True)
    # The batch itself is the release being taken, not one it replaces.
    assert plan["held"] == ["1" * 40]


def test_a_season_pack_is_a_batch_of_the_whole_season():
    title = "[Erai-raws] Fairy Tail - 100 Years Quest - S01 [1080p][HEVC][Multiple Subtitle] [ENG][POR-BR][SPA-LA][GER]"
    assert erai._release_range(title) == (1, erai.SEASON_PACK_LAST)
    assert erai._erai_identity_name(title) == ("Fairy Tail - 100 Years Quest", 1)
    assert erai._erai_name_episode(title) is None
