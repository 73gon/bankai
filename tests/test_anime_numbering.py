"""Episode numbering: files read per season against an episode list, and held releases rechecked by reason."""

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


def test_read_per_season_the_same_files_look_mostly_missing():
    files = [_file("Season 02 Chunin Exams", 2, 5)]
    merged = anime_library.merge_episodes(files, _roster(6), ended=True)
    rows = {(row["season_number"], row["episode"]): row for row in merged["episodes"]}
    # Episode 5 is listed as S02E02: it reads as missing, and S02E05 has no title.
    assert rows[(2, 2)]["missing"]
    assert rows[(2, 5)].get("episode_title") is None


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
