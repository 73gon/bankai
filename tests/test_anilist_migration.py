"""Moving the anime library and its state from AniDB and Shoko to AniList, once."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankai.config import reset_settings_cache
from bankai.web import anilist_migration as migration
from bankai.web import anime_collections, anime_files, erai
from bankai.web.anilist_migration import Translator

# AniList entries, and the AniDB ids anime-offline-database links them to.
S1, S2, S3, OVA = 16498, 20958, 99147, 18397
DR_STONE = 1000  # AniDB splits it in two: 100 and 101
KAGUYA, KAGUYA2 = 2000, 2001  # AniDB keeps both seasons as one: 200
WITCH = 3000  # not linked to AniDB yet
SIDE = 4000  # a twelve-episode side story of the first season
AOT = "Attack on Titan"


@pytest.fixture
def world(anilist_data):
    anilist_data.add(
        S1, "Shingeki no Kyojin", english=AOT, episodes=25, start="2013-04-07", anidb=[9541],
        relations=[(S2, "SEQUEL"), (OVA, "SIDE_STORY", "OVA")],
    )
    anilist_data.add(OVA, "Shingeki no Kyojin OVA", english="Attack on Titan OVA", format="OVA", episodes=8,
                     start="2013-12-09", relations=[(S1, "PARENT")])
    anilist_data.add(S2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", episodes=12,
                     start="2017-04-01", anidb=[10944], relations=[(S1, "PREQUEL"), (S3, "SEQUEL")])
    anilist_data.add(S3, "Shingeki no Kyojin Season 3", english="Attack on Titan Season 3", episodes=12,
                     start="2018-07-23", relations=[(S2, "PREQUEL")])
    anilist_data.add(DR_STONE, "Dr. Stone: Science Future", episodes=24, start="2025-01-09", anidb=[100, 101])
    anilist_data.add(KAGUYA, "Kaguya-sama wa Kokurasetai", english="Kaguya-sama: Love is War", episodes=12,
                     start="2019-01-12", anidb=[200], relations=[(KAGUYA2, "SEQUEL")])
    anilist_data.add(KAGUYA2, "Kaguya-sama wa Kokurasetai: Tensai-tachi no Renai Zunousen",
                     english="Kaguya-sama: Love is War Season 2", episodes=12, start="2020-04-11",
                     relations=[(KAGUYA, "PREQUEL")])
    anilist_data.add(WITCH, "Release That Witch", format="ONA", episodes=12, start="2026-01-01")
    return anilist_data


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    anime = tmp_path / "anime"
    staging = tmp_path / "staging"
    anime.mkdir()
    (staging / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(anime))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()
    state = migration._state_dir()
    monkeypatch.setattr(erai, "_state_path", lambda: state / "erai_automation.json")
    yield SimpleNamespace(anime=anime, staging=staging / "Shows", tmp=tmp_path, state=state)
    reset_settings_cache()


# -- AniDB -> AniList ---------------------------------------------------------------------


def test_an_anidb_entry_is_the_anilist_entry_the_offline_database_links(world) -> None:
    translator = Translator()

    assert translator.entry(9541) == S1
    assert translator.entry("10944") == S2
    assert translator.entry(9) is None
    assert translator.episode(9541, 5) == (S1, 5)
    assert translator.episode(9, 5) is None


def test_an_episode_past_its_entrys_end_moves_on_into_the_sequel(world) -> None:
    """AniDB keeps Kaguya-sama's two seasons as one entry; AniList as two."""
    assert Translator().episode(200, 13) == (KAGUYA2, 1)
    assert Translator().episode(200, 12) == (KAGUYA, 12)


def test_parts_anidb_splits_are_numbered_on_in_the_one_anilist_entry(world) -> None:
    series = [
        {"anidb": 101, "episodes": 12, "aired": "2025-07-10"},
        {"anidb": 100, "episodes": 12, "aired": "2025-01-09"},
        {"anidb": 9541, "episodes": 25, "aired": "2013-04-07"},
        {"anidb": 9, "episodes": 3},
    ]

    offsets = migration.offsets_from_shoko(series)

    assert offsets == {100: 0, 101: 12}
    translator = Translator(offsets=offsets)
    assert translator.episode(101, 3) == (DR_STONE, 15)
    assert translator.episode(100, 3) == (DR_STONE, 3)


def test_an_entry_the_offline_database_does_not_link_yet_is_found_through_its_prequel(world) -> None:
    translator = Translator()

    learned = migration.learn_unmapped(translator, {9542}, names={}, relations={9542: [(10944, "Prequel")]})

    assert learned == Counter({"relation": 1})
    assert translator.entry(9542) == S3
    assert translator.episode(9542, 2) == (S3, 2)


def test_a_chain_of_new_seasons_is_placed_one_link_at_a_time(world, anilist_data) -> None:
    anilist_data.add(S3, "Shingeki no Kyojin Season 3", episodes=12, start="2018-07-23",
                     relations=[(S2, "PREQUEL"), (104578, "SEQUEL")])
    anilist_data.add(104578, "Shingeki no Kyojin Season 3 Part 2", episodes=10, start="2019-04-29",
                     relations=[(S3, "PREQUEL")])
    translator = Translator()
    # The later season sorts first: it is placed on the second pass, through the first.
    relations = {9500: [(9542, "Prequel")], 9542: [(10944, "Prequel")]}

    learned = migration.learn_unmapped(translator, {9500, 9542}, names={}, relations=relations)

    assert learned == Counter({"relation": 2})
    assert (translator.entry(9542), translator.entry(9500)) == (S3, 104578)


def test_an_ova_is_found_as_the_related_entry_of_as_many_episodes(world) -> None:
    translator = Translator()

    learned = migration.learn_unmapped(
        translator, {9600}, names={}, relations={9600: [(9541, "Side Story")]}, counts={9600: 8}
    )

    assert learned == Counter({"relation": 1})
    assert translator.entry(9600) == OVA


def test_an_ova_is_never_placed_on_an_entry_another_anidb_entry_already_is(world, anilist_data) -> None:
    """Twelve episodes, as many as Season 2 -- which AniDB's 10944 already is."""
    translator = Translator()
    relations = {9600: [(9541, "Side Story")]}

    migration.learn_unmapped(translator, {9600}, names={}, relations=relations, counts={9600: 12})
    assert translator.entry(9600) is None

    # With another twelve-episode entry beside it, that one is the only choice.
    anilist_data.add(S1, "Shingeki no Kyojin", english=AOT, episodes=25, start="2013-04-07", anidb=[9541],
                     relations=[(S2, "SEQUEL"), (OVA, "SIDE_STORY", "OVA"), (SIDE, "SIDE_STORY", "OVA")])
    anilist_data.add(SIDE, "Shingeki no Kyojin: Lost Girls", format="OVA", episodes=12, start="2017-12-08",
                     relations=[(S1, "PARENT")])
    migration.learn_unmapped(translator, {9600}, names={}, relations=relations, counts={9600: 12})
    assert translator.entry(9600) == SIDE


def test_an_entry_is_found_by_its_name_without_the_year_added_to_it(world) -> None:
    translator = Translator()

    learned = migration.learn_unmapped(translator, {300}, names={300: ["Release That Witch (2026)"]}, relations={})

    assert learned == Counter({"name": 1})
    assert translator.entry(300) == WITCH


def test_a_name_is_never_placed_on_an_entry_another_anidb_entry_already_is(world) -> None:
    """"Attack on Titan" names the show; a second AniDB entry by that name is its OVA or a recap."""
    translator = Translator()
    names = {301: [AOT], 302: ["Release That Witch"], 303: ["Release That Witch"]}

    learned = migration.learn_unmapped(translator, {301, 302, 303}, names=names, relations={})

    assert learned == Counter({"name": 1})
    assert (translator.entry(301), translator.entry(302), translator.entry(303)) == (None, WITCH, None)


# -- Which episode each file is --------------------------------------------------------------


def _paths(root: Path, *rels: str) -> list[Path]:
    paths = [root.joinpath(*Path(rel).parts) for rel in rels]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("video", encoding="utf-8")
    return paths


def test_a_file_is_identified_by_shoko_then_its_folders_link_then_its_folders_name(world, roots) -> None:
    linked, by_folder_link, by_name, both = _paths(
        roots.anime,
        "Whatever/Whatever 01.mkv",
        "Kaguya/Kaguya - 13.mkv",
        "Shingeki no Kyojin/Shingeki no Kyojin - 03.mkv",
        "Shingeki no Kyojin/Shingeki no Kyojin - 04.mkv",
    )
    links = {str(linked): [[9541, 5], [9541, 6]], str(both): [[10944, 1]]}

    found = migration.identify(
        [linked, by_folder_link, by_name, both],
        links=links,
        folder_links={"Kaguya": 200, "Shingeki no Kyojin": 100},
        translator=Translator(),
    )

    assert found.rows == {
        "Whatever/Whatever 01.mkv": [(S1, 5), (S1, 6)],
        "Kaguya/Kaguya - 13.mkv": [(KAGUYA2, 1)],
        # The folder's link wins over its name.
        "Shingeki no Kyojin/Shingeki no Kyojin - 03.mkv": [(DR_STONE, 3)],
        # Shoko's link wins over both.
        "Shingeki no Kyojin/Shingeki no Kyojin - 04.mkv": [(S2, 1)],
    }
    assert found.how == Counter({"shoko": 2, "folder link": 2})


def test_a_folders_name_and_season_folder_say_which_entry_it_is(world, roots) -> None:
    paths = _paths(
        roots.anime,
        "Shingeki no Kyojin/Season 01/Shingeki no Kyojin - S01E02.mkv",
        "Shingeki no Kyojin/Season 2/Shingeki no Kyojin - S02E04.mkv",
        "Shingeki no Kyojin/Shingeki no Kyojin - 27.mkv",
        "Release That Witch (2026)/Release That Witch (2026) - 03.mkv",
    )
    paths += _paths(roots.staging, "Attack on Titan/Season 02 - Shingeki no Kyojin Season 2/AoT - 05.mkv")

    found = migration.identify(paths, links={}, folder_links={}, translator=Translator())

    assert found.rows == {
        "Shingeki no Kyojin/Season 01/Shingeki no Kyojin - S01E02.mkv": [(S1, 2)],
        "Shingeki no Kyojin/Season 2/Shingeki no Kyojin - S02E04.mkv": [(S2, 4)],
        # Past the first season's 25 episodes: the second's.
        "Shingeki no Kyojin/Shingeki no Kyojin - 27.mkv": [(S2, 2)],
        "Release That Witch (2026)/Release That Witch (2026) - 03.mkv": [(WITCH, 3)],
        "Attack on Titan/Season 02 - Shingeki no Kyojin Season 2/AoT - 05.mkv": [(S2, 5)],
    }
    assert found.how == Counter({"folder name": 5})


def test_a_special_is_never_told_by_its_shows_name(world, roots) -> None:
    """It would be taken for the regular episode of its number."""
    in_season_00, named_s00, linked = _paths(
        roots.anime,
        "Shingeki no Kyojin/Season 00/Shingeki no Kyojin - 01.mkv",
        "Shingeki no Kyojin/Shingeki no Kyojin - S00E02.mkv",
        "Shingeki no Kyojin/Season 00/Shingeki no Kyojin - 03.mkv",
    )

    found = migration.identify(
        [in_season_00, named_s00, linked], links={str(linked): [[9600, 3]]}, folder_links={},
        translator=Translator(extra={9600: OVA}),
    )

    assert found.rows == {"Shingeki no Kyojin/Season 00/Shingeki no Kyojin - 03.mkv": [(OVA, 3)]}
    assert found.unmatched == [
        "Shingeki no Kyojin/Season 00/Shingeki no Kyojin - 01.mkv",
        "Shingeki no Kyojin/Shingeki no Kyojin - S00E02.mkv",
    ]


def test_a_file_nothing_places_is_left_unmatched(world, roots) -> None:
    nobody, no_number = _paths(roots.anime, "Nobody Knows/Nobody Knows - 01.mkv", "Shingeki no Kyojin/Extras.mkv")
    outside = roots.tmp / "elsewhere" / "x - 01.mkv"

    found = migration.identify([nobody, no_number, outside], links={}, folder_links={}, translator=Translator())

    assert found.rows == {}
    assert found.unmatched == ["Nobody Knows/Nobody Knows - 01.mkv", "Shingeki no Kyojin/Extras.mkv"]


# -- Collections from Shoko's groups ------------------------------------------------------------


def test_shokos_groups_become_collections_with_their_titles_and_names(world) -> None:
    groups = [
        {"id": 7, "name": "Shingeki no Kyojin", "custom": False, "main_anidb": 9541},
        {"id": 8, "name": "My Kaguya", "custom": True, "main_anidb": 200, "preferred_series": 31},
        {"id": 9, "name": "Dr. Stone: Science Future", "custom": True, "main_anidb": 100},
        {"id": 10, "name": "Stray", "custom": False, "main_anidb": 9541},
    ]
    series = [
        {"id": 1, "anidb": 9541, "group": 7},
        {"id": 2, "anidb": 10944, "group": 7},
        {"id": 3, "anidb": 9, "group": 7},
        {"id": 31, "anidb": 200, "group": 8},
        {"id": 4, "anidb": 100, "group": 9},
        {"id": 5, "anidb": 101, "group": 9},
        # In a second group too: it stays in the first.
        {"id": 6, "anidb": 9541, "group": 10},
    ]
    chosen: set[int] = set()

    store = migration.collections_from_shoko(
        groups, series, Translator(),
        titles={"7": 10944},
        bankai_names={"9": "Dr. Stone: Science Future"},
        places={"100": 7},
        chosen=chosen,
    )

    rows = {row.id: row for row in store.collections.values()}
    assert [(row.members, row.title, row.name) for row in rows.values()] == [
        ([S1, S2], S2, None),
        ([KAGUYA], KAGUYA, "My Kaguya"),
        # A name bankai gave the group is AniList's again.
        ([DR_STONE], DR_STONE, None),
    ]
    # Titles picked by hand, in bankai or in Shoko ("Shown as"), are kept.
    assert chosen == {1, 2}


def test_an_entry_taken_out_of_every_collection_stays_out(world) -> None:
    groups = [{"id": 7, "name": "Shingeki no Kyojin", "custom": False, "main_anidb": 9541}]
    series = [{"id": 1, "anidb": 9541, "group": 7}]

    store = migration.collections_from_shoko(
        groups, series, Translator(), titles={}, bankai_names={}, places={"10944": None, "100": 7}
    )

    assert store.alone == {S2}
    assert store.collections[1].members == [S1, DR_STONE]


# -- The automation's state ----------------------------------------------------------------------


def test_job_arguments_name_the_anilist_entry_and_episode_instead(world) -> None:
    args = [
        "anime-download", "--info-hash", "a" * 40, "--anidb-id", "9541", "--anidb-title", "Shingeki no Kyojin",
        "--episode", "27", "--require-german-subtitles",
    ]

    converted = migration.convert_args(args, Translator())

    # Episode 27 of AniDB's first season is the second season's second on AniList.
    assert converted == [
        "anime-download", "--info-hash", "a" * 40, "--anilist-id", str(S2),
        "--anilist-title", "Attack on Titan Season 2", "--episode", "2", "--require-german-subtitles",
    ]


def test_arguments_without_an_episode_name_the_entry_only(world) -> None:
    converted = migration.convert_args(["anime-download", "--kind", "movie", "--anidb-id", "9541"], Translator())

    assert converted == ["anime-download", "--kind", "movie", "--anilist-id", str(S1)]


def test_arguments_already_on_anilist_are_left_as_they_are(world) -> None:
    args = ["anime-download", "--anilist-id", "5", "--episode", "3"]

    converted = migration.convert_args(args, Translator())

    assert converted == args and converted is not args


@pytest.mark.parametrize(
    "args",
    [
        ["anime-download", "--tvdb-id", "99", "--episode", "3"],
        ["anime-download", "--anidb-id", "9", "--episode", "3"],
        ["anime-download", "--anidb-id", "9"],
        ["anime-download", "--anidb-id", "x"],
    ],
)
def test_arguments_anilist_cannot_place_are_reported_not_guessed(world, args) -> None:
    assert migration.convert_args(args, Translator()) is None


def test_a_queued_job_from_before_is_read_by_anilist(world) -> None:
    assert migration.anilist_job_args(["anime-download", "--anidb-id", "200", "--episode", "13"]) == [
        "anime-download", "--anilist-id", str(KAGUYA2), "--episode", "1"
    ]


def test_the_release_state_moves_to_anilist_keys(world) -> None:
    state = {
        "canonical": {
            "anidb:9541|5": {"info_hash": "a" * 40},
            "anidb:9|1": {"info_hash": "b" * 40},
            "anidb:x|1": {"info_hash": "c" * 40},
            "anilist:5|1": {"info_hash": "d" * 40},
        },
        "releases": {
            "a" * 40: {
                "canonical": "anidb:9541|27", "anidb_id": 9541, "episode": 27,
                "args": ["anime-download", "--anidb-id", "9541", "--episode", "27"],
            },
            "b" * 40: {"canonical": "anidb:9|1", "anidb_id": 9, "args": ["anime-download", "--anidb-id", "9"]},
        },
        "anime": {"9541": {"title": "Shingeki no Kyojin"}, "9": {"title": "Gone"}},
    }
    notes: Counter = Counter()

    state = migration.convert_state(state, Translator(), notes)

    assert state["canonical"] == {"anilist:16498|5": {"info_hash": "a" * 40}, "anilist:5|1": {"info_hash": "d" * 40}}
    moved = state["releases"]["a" * 40]
    assert (moved["canonical"], moved["anilist_id"], moved["episode"]) == (f"anilist:{S2}|2", S2, 2)
    assert "anidb_id" not in moved and moved["args"] == ["anime-download", "--anilist-id", str(S2), "--episode", "2"]
    lost = state["releases"]["b" * 40]
    assert "canonical" not in lost and "anidb_id" not in lost
    assert state["anime"] == {
        str(S1): {
            "anilist_id": S1,
            "title": "Shingeki no Kyojin",
            "english_title": AOT,
            "matching_titles": ["Shingeki no Kyojin", AOT],
        }
    }
    assert notes == Counter({
        "canonical episodes moved to AniList": 1,
        "canonical episodes AniList cannot place (dropped)": 2,
        "releases AniList cannot place": 1,
        "release arguments moved to AniList": 1,
        "release arguments AniList cannot place": 1,
        "followed entries AniList cannot place (dropped)": 1,
    })


def test_the_blacklist_moves_to_anilist_entries_or_stays_a_decision_on_the_name(world) -> None:
    policies = {
        "shingeki": {"mode": "blacklisted", "anidb_id": 9541, "anidb_title": "x", "source_title": "Shingeki"},
        "gone": {"mode": "blacklisted", "anidb_id": "9", "anidb_titles": ["y"], "source_title": "Gone"},
        "by name": {"mode": "blacklisted", "source_title": "Foo"},
    }
    notes: Counter = Counter()

    out = migration.convert_policies(policies, Translator(), notes)

    assert out[f"anilist:{S1}"] == {
        "mode": "blacklisted",
        "source_title": "Shingeki",
        "anilist_id": S1,
        "anilist_title": "Shingeki no Kyojin",
        "english_title": AOT,
        "anilist_titles": ["Shingeki no Kyojin", AOT],
        "anilist_poster_url": None,
    }
    assert out["gone"] == {"mode": "blacklisted", "source_title": "Gone"}
    assert out["by name"] == policies["by name"]
    assert set(out) == {f"anilist:{S1}", "gone", "by name"}
    assert notes == Counter({"blacklist entries moved to AniList": 1, "blacklist entries kept by name (no AniList entry)": 1})


def test_name_mappings_move_to_anilist_with_the_parts_numbered_on(world) -> None:
    mappings = {
        "dr stone 2": {"anidb_id": 101, "anidb_title": "x", "anidb_episode_offset": 2},
        "gone": {"anidb_id": 9, "anidb_title": "y"},
        "tvdb era": {"tvdb_id": 5},
    }
    notes: Counter = Counter()

    out = migration.convert_mappings(mappings, Translator(offsets={101: 12}), notes)

    assert out == {
        "dr stone 2": {"anilist_id": DR_STONE, "anilist_title": "Dr. Stone: Science Future", "anilist_episode_offset": 14},
        "tvdb era": {"tvdb_id": 5},
    }
    assert notes == Counter({"name mappings moved to AniList": 1, "name mappings dropped (no AniList entry)": 1})


# -- The whole run -----------------------------------------------------------------------------


GROUPS = [{"id": 7, "name": "Shingeki no Kyojin", "custom": False, "main_anidb": 10944, "preferred_series": None}]
SERIES = [
    {"id": 1, "name": "Shingeki no Kyojin", "anidb": 9541, "group": 7, "episodes": 25, "aired": "2013-04-07"},
    {"id": 2, "name": "Shingeki no Kyojin Season 2", "anidb": 10944, "group": 7, "episodes": 12, "aired": "2017-04-01"},
    {"id": 3, "name": "Shingeki no Kyojin Season 3", "anidb": 9542, "group": 7, "episodes": 12, "aired": "2018-07-23"},
]
LINKED = "Shingeki no Kyojin/Season 01/Shingeki no Kyojin - S01E01.mkv"
BY_NAME = "Shingeki no Kyojin/Season 01/Shingeki no Kyojin - S01E02.mkv"
SPECIAL = "Shingeki no Kyojin/Season 00/Shingeki no Kyojin - S00E01.mkv"
NOBODY = "Nobody Knows/Nobody Knows - 01.mkv"
STAGED = "Kaguya/Kaguya - 13.mkv"


@pytest.fixture
def before(world, roots, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A library and state as the AniDB era left them."""
    paths = _paths(roots.anime, LINKED, BY_NAME, SPECIAL, NOBODY) + _paths(roots.staging, STAGED)

    async def groups():
        return GROUPS, SERIES

    async def relations(series_ids):
        return {9542: [(10944, "Prequel")]}

    monkeypatch.setattr(migration, "shoko_groups", groups)
    monkeypatch.setattr(migration, "shoko_relations", relations)
    files = {
        "shoko_file_links.json": {str(paths[0]): [[9541, 1]]},
        "anime_library_prefs.json": {"folder:Kaguya": {"anidb_id": 200}},
        "erai_automation.json": {
            "canonical": {"anidb:9541|1": {"info_hash": "a" * 40}, "anidb:9|1": {"info_hash": "b" * 40}},
            "releases": {},
            "anime": {
                "10944": {"title": "Shingeki no Kyojin Season 2"},
                "300": {"title": "Release That Witch (2026)"},
                "301": {"title": AOT},
            },
        },
        "erai_series_policies.json": {"shingeki": {"mode": "blacklisted", "anidb_id": 9541, "source_title": "x"}},
        "erai_mappings.json": {"dr stone": {"anidb_id": 101, "anidb_title": "x"}},
        "web_pending.json": [{"args": ["anime-download", "--anidb-id", "9541", "--episode", "3"]}],
    }
    for name, value in files.items():
        (roots.state / name).write_text(json.dumps(value), encoding="utf-8")
    return SimpleNamespace(roots=roots, paths=paths, files=files)


def _aot(season: int, number: int) -> str:
    return f"{AOT}/Season {season:02d}/{AOT} - S{season:02d}E{number:02d}.mkv"


KAGUYA_FOLDER = "Kaguya-sama Love is War"
KAGUYA_NEW = f"{KAGUYA_FOLDER}/Season 02/{KAGUYA_FOLDER} - S02E01.mkv"


def test_a_dry_run_works_everything_out_and_changes_nothing(before) -> None:
    roots = before.roots
    saved = {name: (roots.state / name).read_text(encoding="utf-8") for name in before.files}

    report = asyncio.run(migration.migrate(dry_run=True))

    assert report["dry_run"] is True
    assert report["files"] == {
        "total": 5,
        "matched": 3,
        "how": {"shoko": 1, "folder name": 1, "folder link": 1},
        "unmatched": 2,
        "unmatched_files": [NOBODY, SPECIAL],
    }
    assert report["anidb_entries_placed_by"] == {"relation": 1, "name": 1}
    # "Attack on Titan" is the first season's name, which AniDB's 9541 already is.
    assert report["anidb_entries_not_on_anilist"] == {"9": None, "301": AOT}
    assert report["collections"] == {
        "total": 3, "from_shoko_groups": 1, "made_or_joined_by_relations": 2, "grown_by_sequels": 0
    }
    # Shoko named the group after its second season: the collection is the show's.
    assert {(move["from"], move["to"], move["files"]) for move in report["layout"]["folder_moves"]} == {
        ("Shingeki no Kyojin", AOT, 2),
        ("Kaguya", KAGUYA_FOLDER, 1),
    }
    assert (report["layout"]["moves"], report["layout"]["already_in_place"]) == (3, 0)
    preview = {row["folder"]: row for row in report["preview"]}
    assert [season[:3] for season in preview[AOT]["seasons"]] == [
        [1, AOT, S1], [2, "Attack on Titan Season 2", S2], [3, "Attack on Titan Season 3", S3]
    ]
    assert report["state"]["canonical episodes moved to AniList"] == 1
    assert report["state"]["queued jobs moved to AniList"] == 1
    assert Path(report["report_file"]).name == "anilist_migration_dry_run.json"
    assert Path(report["report_file"]).is_file()

    # Nothing changed: the state, the index, the collections, the files.
    assert {name: (roots.state / name).read_text(encoding="utf-8") for name in before.files} == saved
    assert anime_files.load() == {}
    assert anime_collections.load().collections == {}
    assert all(path.is_file() for path in before.paths)
    assert not (roots.state / "anilist_migration_backup").exists()


def test_the_run_backs_up_the_state_and_moves_everything_to_anilist(before) -> None:
    roots = before.roots

    report = asyncio.run(migration.migrate(dry_run=False))

    backup = Path(report["backup"])
    assert json.loads((backup / "erai_automation.json").read_text(encoding="utf-8")) == before.files["erai_automation.json"]
    state = erai._load_state()
    assert set(state["anime"]) == {str(S2), str(WITCH)}
    assert set(state["canonical"]) == {f"anilist:{S1}|1"}
    assert set(erai._load_policies()) == {f"anilist:{S1}"}
    assert json.loads((roots.state / "web_pending.json").read_text(encoding="utf-8")) == [
        {"args": ["anime-download", "--anilist-id", str(S1), "--episode", "3"]}
    ]
    # The library is laid out, and the index follows it.
    assert report["applied"]["moved"] == 3
    assert anime_files.load() == {_aot(1, 1): [(S1, 1)], _aot(1, 2): [(S1, 2)], KAGUYA_NEW: [(KAGUYA2, 1)]}
    assert (roots.anime / _aot(1, 1)).is_file() and (roots.staging / KAGUYA_NEW).is_file()
    assert (roots.anime / AOT / "tvshow.nfo").is_file()
    assert (roots.anime / SPECIAL).is_file() and (roots.anime / NOBODY).is_file()
    store = anime_collections.load()
    assert {frozenset(row.members) for row in store.collections.values()} == {
        frozenset({S1, S2, S3}), frozenset({KAGUYA, KAGUYA2}), frozenset({WITCH})
    }


def test_a_second_run_is_refused(before) -> None:
    """It would read the AniList ids it wrote as AniDB ones."""
    asyncio.run(migration.migrate(dry_run=False))

    with pytest.raises(RuntimeError, match="already"):
        asyncio.run(migration.migrate(dry_run=False))
    # A dry run is still fine: it changes nothing.
    asyncio.run(migration.migrate(dry_run=True))
