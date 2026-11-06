"""The episode check: placement by air date, picture scores, verdicts, the checks across files."""

from __future__ import annotations

import random

import pytest

from bankai.metadata import anilist, animelists
from bankai.web import anime_verify as verify


def _episodes() -> list[dict]:
    # One long TMDB season, as TMDB keeps Re:Zero: 25 episodes from April 2016, 13 from July 2020.
    rows = []
    for number in range(1, 26):
        rows.append({"season": 1, "episode": number, "name": f"A{number}", "air_date": f"2016-{4 + (number - 1) // 4:02d}-{1 + ((number - 1) % 4) * 7:02d}", "runtime": 50 if number == 1 else 25})
    for number in range(26, 39):
        rows.append({"season": 1, "episode": number, "name": f"B{number}", "air_date": f"2020-{7 + (number - 26) // 4:02d}-{8 + ((number - 26) % 4) * 5:02d}", "runtime": 25})
    rows.append({"season": 0, "episode": 1, "name": "OVA", "air_date": "2018-10-06", "runtime": 60})
    return rows


@pytest.fixture
def entries(monkeypatch):
    media = {
        1: {"start": "2016-04-01", "episodes": 25, "duration": 25, "status": "FINISHED"},
        2: {"start": "2020-07-08", "episodes": 13, "duration": 26, "status": "FINISHED"},
    }
    monkeypatch.setattr(anilist, "media", lambda aid: media.get(int(aid), {}))
    monkeypatch.setattr(animelists, "ids", lambda aid: {"tmdb_tv": 65942, "tmdb_season": 1})
    return media


def test_an_entry_is_placed_by_its_start_date_within_one_long_tmdb_season(entries):
    episodes = _episodes()
    assert verify.expected(1, 1, episodes)["episode"] == 1
    assert verify.expected(2, 1, episodes)["episode"] == 26
    assert verify.expected(2, 13, episodes)["episode"] == 38
    assert verify.expected(2, 14, episodes) is None


def test_the_entry_a_tmdb_episode_is_comes_from_the_same_placement(entries):
    episodes = _episodes()
    assert verify.owner(episodes[25], [1, 2], episodes) == (2, 1)
    assert verify.owner(episodes[3], [1, 2], episodes) == (1, 4)


def test_an_entry_far_from_any_air_date_falls_back_to_its_seasons_start(entries):
    entries[1]["start"] = "2010-01-01"
    assert verify.expected(1, 3, _episodes())["episode"] == 3


def _picture(seed: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(verify.SIZE))


def _near(raw: bytes, seed: int) -> bytes:
    # The same frame, a little noise: a still TMDB took and the file's keyframe of that shot.
    rng = random.Random(seed)
    return bytes(max(0, min(255, value + rng.randrange(-12, 13))) for value in raw)


def test_a_file_scores_highest_against_the_episode_whose_stills_are_its_frames():
    shots = [_picture(seed) for seed in range(12)]
    file_frames = shots[:6]
    pictures = {
        "right": [_near(shots[1], 1), _near(shots[4], 2), _picture(99)],
        "wrong": [shots[8], shots[9]],
    }
    scores = verify.scores(file_frames, pictures)
    assert scores["right"] > 0.95
    assert scores["wrong"] < 0.5


def test_one_still_alone_counts_a_little_less():
    shot = _picture(1)
    assert verify.scores([shot], {"one": [shot]})["one"] == pytest.approx(0.95, abs=0.001)


def test_reading_stops_once_two_of_the_expected_stills_were_seen():
    shots = [_picture(seed) for seed in range(4)]
    seen = verify.Seen([shots[0], shots[2], _picture(50)])
    assert not seen(shots[1])
    assert not seen(shots[0])
    assert seen(_near(shots[2], 3))


def test_verdicts():
    assert verify.verdict({"picture": {"state": "ok"}, "length": {"ok": False}}) == "verified"
    far = {"state": "wrong", "expected": "S01E01", "score": 0.3, "match": "S01E26", "match_score": 0.95}
    assert verify.verdict({"picture": far}) == "wrong"
    assert verify.verdict({"picture": {"state": "none"}, "length": {"ok": True}}) == "plausible"
    assert verify.verdict({"picture": {"state": "none"}, "length": {"ok": False}}) == "unsure"
    assert verify.verdict({"picture": {"state": "unsure"}, "length": {"ok": True}}) == "unsure"


@pytest.mark.parametrize(
    "change",
    [
        # The next episode's preview, or a recap of the one before.
        {"match": "S01E03"},
        # A recap special holds the season's footage; a special itself shows the season's.
        {"match": "S00E01"},
        {"expected": "S00E04"},
        # Its own stills seen too, or the other's not clearly.
        {"score": 0.7},
        {"match_score": 0.88},
    ],
)
def test_a_picture_leaning_elsewhere_is_wrong_only_when_nothing_else_explains_it(change):
    picture = {"state": "wrong", "expected": "S01E01", "score": 0.3, "match": "S01E26", "match_score": 0.95, **change}
    assert verify.verdict({"picture": picture}) == "unsure"


def test_files_on_one_episode_or_past_the_end_are_named(entries):
    rows = {"a.mkv": [(1, 3)], "b.mkv": [(1, 3)], "c.mkv": [(1, 30)], "d.mkv": [(2, 1)], "e.mkv": [(1, 1)]}
    found = {
        "d.mkv": {"picture": {"state": "ok", "tmdb": 65942, "expected": "S01E26"}},
        "e.mkv": {"picture": {"state": "wrong", "tmdb": 65942, "expected": "S01E01", "match": "S01E26"}},
    }
    out = verify.issues(rows, found)
    assert out["a.mkv"] == ["2 files are this episode"]
    assert out["c.mkv"] == ["episode 30 of an entry with 25"]
    # Pictures are no fact: a file leaning to another's episode is not named here.
    assert "d.mkv" not in out and "e.mkv" not in out


def test_the_library_rows_carry_current_results_only(monkeypatch, tmp_path):
    from bankai.web import anime_files

    monkeypatch.setattr(anime_files, "anime_root", lambda: tmp_path)
    found = {
        "Show/Season 01/Show - S01E01.mkv": {"verdict": "wrong", "size": 10, "mtime": 100.0, "picture": {"state": "wrong", "tmdb": 1, "match": "S01E05", "match_score": 0.95, "expected": "S01E01", "score": 0.4}},
        "Show/Season 01/Show - S01E02.mkv": {"verdict": "verified", "size": 10, "mtime": 100.0, "picture": {"state": "ok", "tmdb": 1, "expected": "S01E02"}},
    }
    monkeypatch.setattr(verify, "results", lambda: found)
    monkeypatch.setattr(anilist, "media", lambda aid: {})
    rows = [
        {"path": str(tmp_path / "Show/Season 01/Show - S01E01.mkv"), "anilist_id": 7, "anilist_episode": 1, "size": 10, "mtime": 100.0},
        # Changed since its check: shown as not checked.
        {"path": str(tmp_path / "Show/Season 01/Show - S01E02.mkv"), "anilist_id": 7, "anilist_episode": 2, "size": 11, "mtime": 100.0},
        {"path": "", "anilist_id": 7, "anilist_episode": 3, "missing": True},
    ]
    counts = verify.annotate(rows)
    assert counts == {"verify_checked": 1, "verify_wrong": 1, "verify_unsure": 0, "verify_files": 2}
    assert rows[0]["verify"]["verdict"] == "wrong"
    assert "verify" not in rows[1]


def test_results_follow_the_layouts_renames():
    verify._save({"files": {"Old/a.mkv": {"verdict": "verified"}}, "first": []})
    verify.follow_renames({"Old/a.mkv": "New/a.mkv"})
    assert set(verify.load()["files"]) == {"New/a.mkv"}


def test_files_asked_for_by_hand_come_first(monkeypatch):
    from bankai.web import anime_files

    monkeypatch.setattr(anime_files, "load", lambda: {"a.mkv": [[1, 1]], "b.mkv": [[1, 2]], "c.mkv": [[1, 3]]})
    verify._save({"files": {}, "first": []})
    verify.ask_first(["c.mkv"])
    assert verify.due()[:3] == ["c.mkv", "a.mkv", "b.mkv"]


def test_the_users_word_outweighs_the_check_while_the_file_stays_as_it_was(monkeypatch, tmp_path):
    from bankai.web import anime_files

    monkeypatch.setattr(anime_files, "anime_root", lambda: tmp_path)
    monkeypatch.setattr(anime_files, "load", lambda: {"Show/a.mkv": [[7, 1]], "Show/b.mkv": [[7, 2]]})
    monkeypatch.setattr(anilist, "media", lambda aid: {})
    (tmp_path / "Show").mkdir()
    for name in ("a.mkv", "b.mkv"):
        (tmp_path / "Show" / name).write_bytes(b"x" * 10)
    stat = (tmp_path / "Show" / "a.mkv").stat()
    verify._save({"files": {"Show/a.mkv": {"verdict": "unsure", "size": 10, "mtime": stat.st_mtime, "picture": {"state": "unsure"}}}, "first": []})

    assert verify.confirm(["Show/a.mkv"]) == 1
    row = {"path": str(tmp_path / "Show/a.mkv"), "anilist_id": 7, "anilist_episode": 1, "size": 10, "mtime": stat.st_mtime}
    counts = verify.annotate([row])
    assert row["verify"]["verdict"] == "confirmed" and row["verify"]["picture"] == {"state": "unsure"}
    assert counts["verify_unsure"] == 0 and counts["verify_checked"] == 1
    # Not checked again while it is as it was.
    assert "Show/a.mkv" not in verify.due()

    # A different file under the same name: the word no longer holds.
    changed = {**{k: v for k, v in row.items() if k != "verify"}, "size": 11}
    verify.annotate([changed])
    assert "verify" not in changed

    assert verify.confirm(["Show/a.mkv"], right=False) == 1
    row.pop("verify")
    verify.annotate([row])
    assert row["verify"]["verdict"] == "unsure"


def test_a_confirmation_follows_the_layouts_renames():
    verify._save({"files": {}, "first": [], "confirmed": {"Old/a.mkv": {"size": 1, "mtime": 1.0}}})
    verify.follow_renames({"Old/a.mkv": "New/a.mkv"})
    assert set(verify.load()["confirmed"]) == {"New/a.mkv"}
