"""Moving what the TVDB era left in the anime state over to AniDB, once."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from bankai.metadata import anidb
from bankai.web import anidb_migration as migration

# Dr. Stone's fourth TVDB season is two AniDB entries: episodes 1-12 are
# entry 1, 13-24 entry 2. One Piece is numbered absolutely, which a season
# episode cannot be placed in without TVDB.
TABLE = anidb.build_index(
    ET.fromstring(
        """<animetitles>
  <anime aid="1"><title xml:lang="x-jat" type="main">Dr. Stone: Science Future</title></anime>
  <anime aid="2">
    <title xml:lang="x-jat" type="main">Dr. Stone: Science Future (2025)</title>
    <title xml:lang="en" type="official">Dr. Stone: Science Future Part 2</title>
  </anime>
  <anime aid="69"><title xml:lang="x-jat" type="main">One Piece</title></anime>
  <anime aid="5"><title xml:lang="x-jat" type="main">Kaiju no 8</title></anime>
</animetitles>"""
    ),
    ET.fromstring(
        """<anime-list>
  <anime anidbid="1" tvdbid="99" defaulttvdbseason="4" episodeoffset="0"/>
  <anime anidbid="2" tvdbid="99" defaulttvdbseason="4" episodeoffset="12"/>
  <anime anidbid="69" tvdbid="81797" defaulttvdbseason="a" episodeoffset="0"/>
</anime-list>"""
    ),
)


def test_a_season_episode_belongs_to_the_part_that_starts_before_it() -> None:
    first = migration.tvdb_episode_to_anidb(TABLE, 99, 4, 12)
    second = migration.tvdb_episode_to_anidb(TABLE, 99, 4, 15)

    assert first is not None and (first[0].aid, first[1]) == (1, 12)
    assert second is not None and (second[0].aid, second[1]) == (2, 3)
    # Absolute numbering and specials cannot be placed without TVDB itself.
    assert migration.tvdb_episode_to_anidb(TABLE, 81797, 22, 5) is None
    assert migration.tvdb_episode_to_anidb(TABLE, 99, 0, 1) is None


def test_job_arguments_name_the_anidb_episode_instead() -> None:
    args = [
        "anime-download", "--info-hash", "a" * 40, "--kind", "show", "--tvdb-id", "99",
        "--english-title", "Dr. Stone", "--season", "4", "--episode", "15", "--year", "2019",
        "--require-german-subtitles",
    ]

    converted = migration.anidb_job_args(args, TABLE)

    assert converted is not None
    assert converted[:5] == ["anime-download", "--info-hash", "a" * 40, "--kind", "show"]
    for flag in ("--tvdb-id", "--season", "--year"):
        assert flag not in converted
    assert converted[converted.index("--anidb-id") + 1] == "2"
    assert converted[converted.index("--episode") + 1] == "3"
    assert converted[converted.index("--english-title") + 1] == "Dr. Stone: Science Future Part 2"
    assert "--require-german-subtitles" in converted


def test_arguments_already_on_anidb_only_lose_their_tvdb_id() -> None:
    args = ["anime-download", "--kind", "show", "--anidb-id", "2", "--episode", "3", "--tvdb-id", "99"]
    assert migration.anidb_job_args(args, TABLE) == ["anime-download", "--kind", "show", "--anidb-id", "2", "--episode", "3"]
    # A movie is filed by its title, so it needs no AniDB episode at all.
    movie = ["anime-download", "--kind", "movie", "--tvdb-id", "7", "--english-title", "Suzume"]
    assert migration.anidb_job_args(movie, TABLE) == ["anime-download", "--kind", "movie", "--english-title", "Suzume"]


def test_a_show_episode_anidb_cannot_place_is_reported_not_guessed() -> None:
    args = ["anime-download", "--kind", "show", "--tvdb-id", "81797", "--season", "22", "--episode", "5"]
    assert migration.anidb_job_args(args, TABLE) is None


@pytest.fixture()
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def index():
        return TABLE

    monkeypatch.setattr(anidb, "index", index)
    root = tmp_path / "bankai"
    root.mkdir()
    (root / "erai_automation.json").write_text(
        json.dumps(
            {
                "version": 5,
                "canonical": {
                    "99|4|15": {"info_hash": "a" * 40, "resolution": 1080},
                    # Already fetched on AniDB at a lower resolution: the better one stays.
                    "anidb:2|3": {"info_hash": "b" * 40, "resolution": 720},
                    "81797|22|5": {"info_hash": "c" * 40, "resolution": 1080},
                },
                "releases": {
                    "a" * 40: {
                        "status": "queued",
                        "title": "[Erai-raws] Dr. Stone Science Future Part 2 - 03",
                        "canonical": "99|4|15",
                        "tvdb_id": 99,
                        "args": ["anime-download", "--kind", "show", "--tvdb-id", "99", "--season", "4", "--episode", "15"],
                    },
                },
                "series": {
                    "99": {"tvdb_id": 99, "english_title": "Dr. Stone"},
                    # Nothing of it was fetched: found by its name instead.
                    "12345": {"tvdb_id": 12345, "english_title": "Kaiju No. 8", "aliases": ["Kaiju no 8"]},
                    "777": {"tvdb_id": 777, "english_title": "Nothing Anyone Knows"},
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "erai_mappings.json").write_text(
        json.dumps(
            {
                "dr stone": {"tvdb_id": 99, "english_title": "Dr. Stone", "season": 4, "episode_offset": 12},
                "kept": {"anidb_id": 5, "anidb_title": "Kaiju no 8", "tvdb_id": 12345, "kind": "show"},
            }
        ),
        encoding="utf-8",
    )
    (root / "erai_series_policies.json").write_text(
        json.dumps({"dr stone": {"mode": "blacklisted", "source_title": "Dr. Stone", "tvdb_id": "99"}}),
        encoding="utf-8",
    )
    (root / "anime_library_prefs.json").write_text(
        json.dumps({"tvdb:99": {"numbering": "absolute"}, "key:Dr. Stone": {"numbering": "absolute"},
                    "folder:Dr. Stone": {"anidb_id": 1}}),
        encoding="utf-8",
    )
    return root


def test_a_dry_run_reports_and_changes_nothing(state_dir: Path) -> None:
    before = (state_dir / "erai_automation.json").read_text(encoding="utf-8")

    report = asyncio.run(migration.migrate(dry_run=True))

    assert report["episodes moved to AniDB keys"] == 1
    assert report["episodes left on TVDB keys (Anime-Lists cannot place them)"] == 1
    assert (state_dir / "erai_automation.json").read_text(encoding="utf-8") == before
    assert not list(state_dir.glob("*.bak-anidb-*"))


def test_the_state_moves_to_anidb_with_a_copy_of_every_file_kept(state_dir: Path) -> None:
    report = asyncio.run(migration.migrate())

    state = json.loads((state_dir / "erai_automation.json").read_text(encoding="utf-8"))
    assert state["canonical"]["anidb:2|3"] == {"info_hash": "a" * 40, "resolution": 1080}
    assert "99|4|15" not in state["canonical"]
    assert "81797|22|5" in state["canonical"]  # inert, reported
    release = state["releases"]["a" * 40]
    assert (release["canonical"], release["anidb_id"], release["episode"]) == ("anidb:2|3", 2, 3)
    assert "tvdb_id" not in release and "--tvdb-id" not in release["args"]
    # Tracked shows are their AniDB entries now: they are the library's empty cards.
    assert not state.get("series")
    assert set(state["anime"]) == {"2", "5"}
    assert report["tracked shows not found on AniDB"] == 1

    mappings = json.loads((state_dir / "erai_mappings.json").read_text(encoding="utf-8"))
    assert mappings["dr stone"] == {"anidb_id": 2, "anidb_title": "Dr. Stone: Science Future (2025)", "anidb_episode_offset": 0}
    assert mappings["kept"] == {"anidb_id": 5, "anidb_title": "Kaiju no 8"}

    policies = json.loads((state_dir / "erai_series_policies.json").read_text(encoding="utf-8"))
    assert "tvdb_id" not in policies["dr stone"]
    assert {"anidb:1", "anidb:2"} <= set(policies)

    prefs = json.loads((state_dir / "anime_library_prefs.json").read_text(encoding="utf-8"))
    assert prefs == {"folder:Dr. Stone": {"anidb_id": 1}}

    for name in ("erai_automation.json", "erai_mappings.json", "erai_series_policies.json", "anime_library_prefs.json"):
        assert len(list(state_dir.glob(name + ".bak-anidb-*"))) == 1


def test_running_it_twice_changes_nothing_more(state_dir: Path) -> None:
    asyncio.run(migration.migrate())
    once = (state_dir / "erai_automation.json").read_text(encoding="utf-8")

    report = asyncio.run(migration.migrate(dry_run=True))

    assert report.get("episodes moved to AniDB keys", 0) == 0
    assert report.get("tracked shows moved to AniDB", 0) == 0
    asyncio.run(migration.migrate())
    assert json.loads((state_dir / "erai_automation.json").read_text(encoding="utf-8"))["canonical"] == json.loads(once)["canonical"]
