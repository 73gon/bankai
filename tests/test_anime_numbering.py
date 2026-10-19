"""Shows kept in arc folders whose file numbers count through the whole show."""

from __future__ import annotations

from bankai.web import anime_library, erai
from bankai.web.anime_library import _Episode


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


def _roster(count: int) -> list[_Episode]:
    # The episode list's own seasons do not match the user's arcs: 3 + 3
    # here, arcs 2 + 4.
    return [
        _Episode(
            season=1 if number <= 3 else 2,
            episode=number if number <= 3 else number - 3,
            name=f"Episode {number}",
            aired="2002-10-03",
            absolute_number=number,
        )
        for number in range(1, count + 1)
    ]


def test_absolute_numbers_match_the_episode_list_across_arc_folders():
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
    # Episode 5 is listed as S02E02: it reads as missing, and S02E05 has no title.
    assert rows[(2, 2)]["missing"]
    assert rows[(2, 5)].get("episode_title") is None


def test_codecs_probed_by_file_numbers_still_count():
    files = [_file("Season 02 Chunin Exams", 2, 5)]
    # Probed codecs use the file's S02E05; the episode list would call it S02E02.
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


def test_files_beyond_the_episode_list_are_still_shown():
    merged = anime_library.merge_episodes_absolute(
        [_file("Season 01 Land of Waves", 1, 9)], _roster(2), ended=False
    )
    assert 9 in {row["episode"] for row in merged["episodes"] if not row["missing"]}


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
