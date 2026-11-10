"""A stopping process: the long passes in worker threads end at their next file."""

from __future__ import annotations

import pytest

from bankai.web import anime_verify, stopping


@pytest.fixture(autouse=True)
def _clear():
    stopping.reset()
    yield
    stopping.reset()


def test_a_file_being_read_is_left_unjudged_when_the_process_stops(tmp_path, monkeypatch) -> None:
    video = tmp_path / "Show - S01E01.mkv"
    video.write_bytes(b"x" * 1024)
    stopping.stop()

    with pytest.raises(stopping.Stopped):
        anime_verify.frames(video)


def test_the_episode_check_takes_no_file_once_the_process_stops(monkeypatch) -> None:
    checked: list[str] = []
    monkeypatch.setattr(anime_verify, "due", lambda: ["Show/Season 01/Show - S01E01.mkv"])
    monkeypatch.setattr("bankai.web.anime_files.load", lambda: {"Show/Season 01/Show - S01E01.mkv": [(1, 1)]})
    monkeypatch.setattr(anime_verify, "check", lambda rel, pairs, http=None: checked.append(rel) or {})
    stopping.stop()

    anime_verify.run(seconds=60)

    assert checked == []
