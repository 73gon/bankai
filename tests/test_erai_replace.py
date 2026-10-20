"""Taking an Erai-raws release, batch or episode, in place of what a review card holds."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bankai.config import reset_settings_cache
from bankai.web import anime_files, erai

BATCH = "[Erai-raws] Dr. Stone Science Future (2025) - 01 ~ 12 [1080p][Multiple Subtitle]"


@pytest.fixture()
def library(monkeypatch, tmp_path, anilist_data):
    """Dr. Stone's season in an old folder; AniList entry 1002 is its second cour.

    The folder's name is no AniList title, so the file index is what says
    which entry and episode each file is: S04E01-12 entry 1001, S04E13-24
    entry 1002.
    """
    root = tmp_path / "shows_anime"
    staging = tmp_path / "staging"
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(root))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    reset_settings_cache()
    anilist_data.add(1001, "Dr. Stone: Science Future", episodes=12, start="2025-01-09",
                     relations=[(1002, "SEQUEL")])
    anilist_data.add(1002, "Dr. Stone: Science Future Dai 2 Cour", episodes=12, start="2025-07-10",
                     synonyms=["Dr. Stone: Science Future (2025)"], relations=[(1001, "PREQUEL")])
    state = {"releases": {}, "held": [], "canonical": {}, "anime": {}}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    season = root / "Dr. Stone" / "Season 04"
    season.mkdir(parents=True)
    with anime_files.editing() as rows:
        for number in range(1, 25):
            path = season / f"Dr. Stone - S04E{number:02d}.mkv"
            path.write_bytes(b"old")
            rows[anime_files.key(path)] = [(1001, number) if number <= 12 else (1002, number - 12)]
    monkeypatch.setattr(
        erai.erai_site,
        "lookup",
        lambda info_hash: {"name": BATCH, "subs": ["us", "de"]} if info_hash == "a" * 40 else None,
    )
    # Episode 3 of the entry, S04E15 on disk, carries a German dub.
    monkeypatch.setattr(erai, "_is_german_dub", lambda path: path.name.endswith("S04E15.mkv"))
    yield {"state": state, "root": root, "staging": staging / "Shows", "season": season}
    reset_settings_cache()


def held(state, info_hash, title):
    state["releases"][info_hash] = {"status": "held", "title": title, "reason": "no German"}


def test_a_batch_replaces_its_held_episodes_and_files_but_never_a_german_dub(library):
    held(library["state"], "1" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 05 [1080p HEVC][Multiple Subtitle].mkv")
    held(library["state"], "2" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 01 ~ 04 [1080p HEVC][Multiple Subtitle]")
    # Outside the batch's episodes: stays in review.
    held(library["state"], "3" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 13 [1080p HEVC][Multiple Subtitle].mkv")

    plan = erai._replacement_plan("anilist:1002", "a" * 40)

    assert (plan["first"], plan["last"], plan["batch"], plan["german"]) == (1, 12, True, True)
    assert (plan["anilist_id"], plan["anilist_title"]) == (1002, "Dr. Stone: Science Future Dai 2 Cour")
    assert sorted(plan["held"]) == ["1" * 40, "2" * 40]
    assert plan["german_dubs_kept"] == [3]
    # The entry's half of the old folder, S04E13-S04E24, less the dubbed one.
    names = sorted(Path(row["path"]).name for row in plan["files"])
    assert names == [f"Dr. Stone - S04E{n:02d}.mkv" for n in range(13, 25) if n != 15]


def test_a_release_of_another_entry_is_refused(library, monkeypatch):
    monkeypatch.setattr(erai, "_release_anilist_id", lambda title, **kw: 1001)
    with pytest.raises(ValueError, match="not the AniList entry of this card"):
        erai._replacement_plan("anilist:1002", "a" * 40)


def test_a_release_already_downloading_is_refused(library):
    library["state"]["releases"]["a" * 40] = {"status": "downloading", "title": BATCH}
    with pytest.raises(ValueError, match="already downloading"):
        erai._replacement_plan("anilist:1002", "a" * 40)


def _recorded(path: Path, episode: int) -> dict:
    stat = path.stat()
    return {"path": str(path), "episode": episode, "size": stat.st_size, "mtime": stat.st_mtime}


def _published(root: Path, rel: str, pairs) -> Path:
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"new")
    anime_files.record(path, pairs)
    return path


def test_the_old_files_go_once_the_new_ones_are_published(library):
    season, root = library["season"], library["root"]
    release = {
        "anilist_id": 1002,
        "replace_files": [
            _recorded(season / "Dr. Stone - S04E13.mkv", 1),
            _recorded(season / "Dr. Stone - S04E14.mkv", 2),
            _recorded(season / "Dr. Stone - S04E16.mkv", 4),
        ],
        "replaced_holds": ["1" * 40],
    }
    show = "Dr. Stone Science Future"
    _published(root, f"{show}/Season 02/{show} - S02E01.mkv", [(1002, 1)])
    _published(root, f"{show}/Season 02/{show} - S02E04.mkv", [(1002, 4)])
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
    # The removed file leaves the file index; the new one is what it knows.
    assert anime_files.on_disk(1002)[1] == [root / show / "Season 02" / f"{show} - S02E01.mkv"]
    assert "Dr. Stone/Season 04/Dr. Stone - S04E13.mkv" not in anime_files.load()


def test_an_old_file_whose_new_copy_is_staged_at_its_place_keeps_its_record(library):
    """The layout puts the new file where the old one is: the staged copy is that key now."""
    season = library["season"]
    old = season / "Dr. Stone - S04E14.mkv"
    release = {"anilist_id": 1002, "replace_files": [_recorded(old, 2)]}
    staged = _published(library["staging"], "Dr. Stone/Season 04/Dr. Stone - S04E14.mkv", [(1002, 2)])

    assert erai._finish_replacement(release) == 1

    assert not old.exists()
    assert anime_files.on_disk(1002)[2] == [staged]


def test_a_replacement_that_fails_for_good_puts_the_episodes_back_in_review(library):
    state = library["state"]
    state["releases"]["1" * 40] = {"status": erai.REPLACED_STATUS, "title": "x", "reason": "Replaced by y"}
    release = {"status": "failed", "reason": "Publishing job failed", "replaced_holds": ["1" * 40], "replace_files": []}

    erai._undo_replacement(state, release)

    assert state["releases"]["1" * 40]["status"] == "held"
    assert "replaced_holds" not in release


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

    plan = erai._replacement_plan("anilist:1002", "c" * 40)

    assert (plan["name"], plan["first"], plan["last"], plan["german"]) == (title, 1, 12, True)
    # The batch itself is the release being taken, not one it replaces.
    assert plan["held"] == ["1" * 40]


def test_a_season_pack_is_a_batch_of_the_whole_season():
    title = "[Erai-raws] Fairy Tail - 100 Years Quest - S01 [1080p][HEVC][Multiple Subtitle] [ENG][POR-BR][SPA-LA][GER]"
    assert erai._release_range(title) == (1, erai.SEASON_PACK_LAST)
    assert erai._erai_identity_name(title) == ("Fairy Tail - 100 Years Quest", 1)
    assert erai._erai_name_episode(title) is None


def test_holds_whose_episodes_are_all_in_the_library_leave_review(library, monkeypatch):
    state = library["state"]
    held(state, "1" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 02 [1080p HEVC][Multiple Subtitle].mkv")
    held(state, "2" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 01 ~ 03 [1080p HEVC][Multiple Subtitle]")
    held(state, "3" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 01 ~ 05 [1080p HEVC][Multiple Subtitle]")
    on_disk = {(1002, 1), (1002, 2), (1002, 3)}
    monkeypatch.setattr(erai, "_episode_on_disk", lambda aid, episode: (aid, episode) in on_disk)

    assert erai._settle_holds_on_disk(state) == 2

    statuses = {h[0]: r["status"] for h, r in state["releases"].items()}
    # Episode 2 and the 01-03 batch are in the library; 04 and 05 are not.
    assert statuses == {"1": "existing", "2": "existing", "3": "held"}


def test_a_hold_numbered_past_its_entry_is_checked_as_the_sequel_s_episode(library, monkeypatch):
    state = library["state"]
    held(state, "1" * 40, "[Erai-raws] Dr. Stone Science Future - 13 [1080p HEVC][Multiple Subtitle].mkv")
    monkeypatch.setattr(erai, "_episode_on_disk", lambda aid, episode: (aid, episode) == (1002, 1))

    assert erai._settle_holds_on_disk(state) == 1


def test_holds_are_settled_from_the_file_index_itself(library):
    """The old folder's files are entry 1002's episodes 1-12, by the index."""
    state = library["state"]
    held(state, "1" * 40, "[Erai-raws] Dr. Stone Science Future (2025) - 05 [1080p HEVC][Multiple Subtitle].mkv")
    token = erai._DISK_INDEX.set({})
    try:
        assert erai._settle_holds_on_disk(state) == 1
    finally:
        erai._DISK_INDEX.reset(token)
