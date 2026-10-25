"""Episode pictures from mid-episode, so Jellyfin shows no opening song."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bankai.web import anime_layout, anime_thumbs, library_walk


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    # Each picture's setting is remembered in the state directory: never the real one.
    monkeypatch.setattr(anime_thumbs, "_specs_path", lambda: tmp_path / "anime_thumb_specs.json")
    monkeypatch.setattr(anime_thumbs, "_DONE", {})
    monkeypatch.setattr(anime_thumbs, "_FAILED", {})
    monkeypatch.setattr(anime_layout, "pictures_allowed", lambda: True)


def _library(tmp_path, count=3):
    season = tmp_path / "shows_anime" / "Show" / "Season 01"
    season.mkdir(parents=True)
    for number in range(1, count + 1):
        (season / f"Show - S01E{number:02d}.mkv").write_bytes(b"video" * number)
    library_walk.refresh_all()
    return tmp_path / "shows_anime", season


def _fake_frames(monkeypatch, made: list[str]):
    def make(video):
        made.append(video.name)
        anime_thumbs.thumb_path(video).write_bytes(b"jpeg")
        return True

    monkeypatch.setattr(anime_thumbs, "make_thumb", make)


def test_every_episode_gets_a_picture_once(tmp_path, monkeypatch):
    root, season = _library(tmp_path)
    made: list[str] = []
    _fake_frames(monkeypatch, made)

    assert anime_thumbs.sweep(root, limit=10) == {"made": 3, "remaining": 0, "failed": 0}
    assert (season / "Show - S01E01-thumb.jpg").is_file()

    made.clear()
    assert anime_thumbs.sweep(root, limit=10)["made"] == 0
    assert made == []


def test_the_sweep_is_bounded(tmp_path, monkeypatch):
    root, _season = _library(tmp_path, count=5)
    _fake_frames(monkeypatch, [])

    assert anime_thumbs.sweep(root, limit=2) == {"made": 2, "remaining": 3, "failed": 0}
    assert anime_thumbs.sweep(root, limit=10)["made"] == 3


def test_a_picture_already_there_is_kept_and_an_upgraded_episode_gets_a_new_one(tmp_path, monkeypatch):
    root, season = _library(tmp_path, count=2)
    for number in (1, 2):
        (season / f"Show - S01E{number:02d}-thumb.jpg").write_bytes(b"old")
    # Episode 2 was upgraded after its picture was taken.
    later = time.time() + 10
    os.utime(season / "Show - S01E02.mkv", (later, later))
    library_walk.refresh_all()
    made: list[str] = []
    _fake_frames(monkeypatch, made)

    anime_thumbs.sweep(root, limit=10)
    assert made == ["Show - S01E02.mkv"]


def test_a_file_no_frame_comes_from_is_not_tried_on_every_pass(tmp_path, monkeypatch):
    root, _season = _library(tmp_path, count=1)
    tries: list[str] = []
    monkeypatch.setattr(anime_thumbs, "make_thumb", lambda video: tries.append(video.name) or False)

    assert anime_thumbs.sweep(root, limit=10)["failed"] == 1
    assert anime_thumbs.sweep(root, limit=10)["failed"] == 0
    assert len(tries) == 1


def test_no_pictures_while_the_disk_is_short_on_room(tmp_path, monkeypatch):
    root, _season = _library(tmp_path, count=1)
    monkeypatch.setattr(anime_layout, "pictures_allowed", lambda: False)
    monkeypatch.setattr(anime_thumbs, "make_thumb", lambda video: pytest.fail("no room for pictures"))

    assert anime_thumbs.sweep(root, limit=10)["held_for_space"] == 1


def test_the_frame_is_taken_halfway_through(tmp_path, monkeypatch):
    video = tmp_path / "Show - S01E01.mkv"
    video.write_bytes(b"video")
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "1440.0\n"

    def run(command, **_kwargs):
        calls.append(command)
        if "-frames:v" in command:
            # ffmpeg writes the frame to the path it is given last
            Path(command[-1]).write_bytes(b"jpeg")
        return Done()

    monkeypatch.setattr("bankai.web.media.ffprobe_bin", lambda: "ffprobe")
    monkeypatch.setattr("bankai.web.media.ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(anime_thumbs.subprocess, "run", run)

    assert anime_thumbs.make_thumb(video) is True
    grab = calls[-1]
    assert grab[grab.index("-ss") + 1] == "720.00"  # 12:00 of a 24-minute episode
    assert (tmp_path / "Show - S01E01-thumb.jpg").read_bytes() == b"jpeg"
    # Written beside and renamed over: nothing half-written is left.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Show - S01E01-thumb.jpg", "Show - S01E01.mkv"]


def test_the_layout_moves_the_picture_with_its_episode(tmp_path):
    """It is a companion: renaming an episode takes its picture along."""
    video = tmp_path / "Show - S01E01.mkv"
    video.write_bytes(b"video")
    picture = anime_thumbs.thumb_path(video)
    picture.write_bytes(b"jpeg")

    assert picture in anime_layout._companions(video)


def test_a_new_setting_or_regenerate_takes_every_picture_again(tmp_path, monkeypatch):
    from bankai.config import reset_settings_cache

    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    reset_settings_cache()
    root, season = _library(tmp_path, count=2)
    # Taken before the setting existed: halfway, which is still the default.
    for number in (1, 2):
        (season / f"Show - S01E{number:02d}-thumb.jpg").write_bytes(b"old")
    made: list[str] = []
    _fake_frames(monkeypatch, made)
    assert anime_thumbs.sweep(root, limit=10)["made"] == 0

    monkeypatch.setenv("BANKAI_ANIME__THUMB_PERCENT", "30")
    reset_settings_cache()
    assert anime_thumbs.sweep(root, limit=10)["made"] == 2
    assert anime_thumbs.sweep(root, limit=10)["made"] == 0

    anime_thumbs.regenerate()
    assert anime_thumbs.sweep(root, limit=10)["made"] == 2
    assert anime_thumbs.sweep(root, limit=10)["made"] == 0
    reset_settings_cache()


def test_a_time_past_a_short_episode_falls_back_to_the_share(monkeypatch):
    from bankai.config import reset_settings_cache

    monkeypatch.setenv("BANKAI_ANIME__THUMB_MODE", "time")
    monkeypatch.setenv("BANKAI_ANIME__THUMB_TIME_SECONDS", "600")
    monkeypatch.setenv("BANKAI_ANIME__THUMB_PERCENT", "40")
    reset_settings_cache()
    assert anime_thumbs.position(1440.0) == 600.0
    assert anime_thumbs.position(300.0) == 120.0
    reset_settings_cache()
