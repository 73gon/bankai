"""Shows kept in arc folders whose file numbers count through the whole show."""

from __future__ import annotations

import pytest

from bankai.metadata.tvdb import TVDBEpisode
from bankai.web import anime_library, erai


def _file(folder: str, season: int | None, episode: int | None) -> dict:
    return {
        "path": f"/lib/Naruto/{folder}/Naruto - S{season or 0:02d}E{episode or 0:02d}.mkv",
        "rel_path": "",
        "name": f"Naruto - {episode}",
        "series": "Naruto",
        "season": folder,
        "season_number": season,
        "episode": episode,
        "size": 1,
        "mtime": 1,
        "staged": False,
        "stage": "approved",
        "transfer_status": "idle",
    }


def _roster(count: int) -> list[TVDBEpisode]:
    # TVDB's own seasons do not match the user's arcs: 3 + 3 here, arcs 2 + 4.
    return [
        TVDBEpisode(
            season=1 if number <= 3 else 2,
            episode=number if number <= 3 else number - 3,
            name=f"Episode {number}",
            aired="2002-10-03",
            absolute_number=number,
        )
        for number in range(1, count + 1)
    ]


def test_absolute_numbers_match_tvdb_across_arc_folders():
    files = [
        _file("Season 01 Land of Waves", 1, 1),
        _file("Season 01 Land of Waves", 1, 2),
        _file("Season 02 Chunin Exams", 2, 3),
        _file("Season 02 Chunin Exams", 2, 5),
    ]
    merged = anime_library.merge_episodes_absolute(files, _roster(6), ended=True)
    rows = {row["episode"]: row for row in merged["episodes"]}

    assert merged["downloaded_count"] == 4
    assert merged["total_count"] == 6
    assert merged["completion_state"] == "partial"
    # "S02E05" is episode 5 overall, not season 2 episode 5.
    assert rows[5]["episode_title"] == "Episode 5" and not rows[5]["missing"]
    # A gap goes to the arc whose range holds it, named after its folder.
    assert rows[4]["missing"] and rows[4]["season_number"] == 2
    assert rows[4]["season"] == "Season 02 Chunin Exams"
    # Past the last file: the last arc.
    assert rows[6]["missing"] and rows[6]["season_number"] == 2


def test_read_per_season_the_same_files_look_mostly_missing():
    files = [_file("Season 02 Chunin Exams", 2, 5)]
    merged = anime_library.merge_episodes(files, _roster(6), ended=True)
    rows = {(row["season_number"], row["episode"]): row for row in merged["episodes"]}
    # TVDB's episode 5 is S02E02: it reads as missing, and S02E05 has no title.
    assert rows[(2, 2)]["missing"]
    assert rows[(2, 5)].get("episode_title") is None


def test_codecs_probed_by_file_numbers_still_count():
    files = [_file("Season 02 Chunin Exams", 2, 5)]
    # Probed codecs use the file's S02E05; TVDB would call it S02E02.
    merged = anime_library.merge_episodes_absolute(
        files, _roster(6), ended=True, codecs={(2, 5): "avc"}
    )
    assert next(row for row in merged["episodes"] if row["episode"] == 5)["codec"] == "avc"


def test_flat_numbering_is_one_list():
    files = [_file("Season 01 Land of Waves", 1, 1), _file("Season 02 Chunin Exams", 2, 3)]
    merged = anime_library.merge_episodes_absolute(files, _roster(4), ended=True, flat=True)
    assert {row["season_number"] for row in merged["episodes"]} == {1}
    assert {row["season"] for row in merged["episodes"]} == {"All episodes"}
    assert [row["episode"] for row in merged["episodes"]] == [1, 2, 3, 4]


def test_files_beyond_tvdb_are_still_shown():
    merged = anime_library.merge_episodes_absolute(
        [_file("Season 01 Land of Waves", 1, 9)], _roster(2), ended=False
    )
    assert 9 in {row["episode"] for row in merged["episodes"] if not row["missing"]}


@pytest.fixture
def prefs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    return tmp_path


def test_numbering_is_kept_per_show_by_tvdb_id_and_key(prefs_dir):
    anime_library.save_numbering(key="Naruto", tvdb_id=78857, mode="absolute")
    prefs = anime_library.load_prefs()

    assert anime_library.numbering_for(prefs, tvdb_id=78857, key="anything") == "absolute"
    assert anime_library.numbering_for(prefs, tvdb_id=None, key="Naruto") == "absolute"
    assert anime_library.numbering_for(prefs, tvdb_id=1, key="Bleach") == "season"


def test_back_to_per_season_drops_the_entry(prefs_dir):
    anime_library.save_numbering(key="Naruto", tvdb_id=78857, mode="absolute_flat")
    anime_library.save_numbering(key="Naruto", tvdb_id=78857, mode="season")
    assert anime_library.load_prefs() == {}


def test_unknown_numbering_is_refused(prefs_dir):
    with pytest.raises(ValueError):
        anime_library.save_numbering(key="Naruto", tvdb_id=None, mode="sideways")


def test_retry_held_can_be_limited_by_reason(monkeypatch):
    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {"status": "held", "title": "A", "reason": "No confident TVDB match"},
        "b" * 40: {"status": "held", "title": "B", "reason": "Not on Nyaa"},
        "c" * 40: {"status": "published", "title": "C", "reason": "No confident TVDB match"},
    }
    saved = {}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_catalog_entries", lambda state: {})
    monkeypatch.setattr(erai, "_load_retry_requests", lambda: {})
    monkeypatch.setattr(erai, "_save_retry_requests", lambda value: saved.update(value))
    monkeypatch.setattr(erai, "status", lambda **kwargs: {})
    monkeypatch.setattr(erai, "_RETRY_TASK", None)
    monkeypatch.setattr(erai.asyncio, "create_task", lambda coro: coro.close())

    result = erai.retry_held("TVDB")

    assert result["requested"] == 1
    assert set(saved) == {"a" * 40}
