"""Telling Jellyfin what bankai changed, so new titles and pictures show without a refresh by hand."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from bankai.config import Settings
from bankai.web import anime_files, anime_thumbs, jellyfin

URL = "http://jellyfin.test:8096"
LIBRARY = {"Name": "Anime Serien", "ItemId": "lib", "Locations": ["G:\\media\\shows_anime"]}
ITEMS = [
    {"Id": "show", "Path": "G:\\media\\shows_anime\\Bleach"},
    {"Id": "season1", "Path": "G:\\media\\shows_anime\\Bleach\\Season 01"},
    {"Id": "ep1", "Path": "G:\\media\\shows_anime\\Bleach\\Season 01\\Bleach - S01E01.mkv"},
    {"Id": "ep2", "Path": "G:\\media\\shows_anime\\Bleach\\Season 01\\Bleach - S01E02.mkv"},
]


@pytest.fixture
def anime(tmp_path, monkeypatch):
    root = tmp_path / "shows_anime"
    (root / "Bleach" / "Season 01").mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(anime_files, "anime_root", lambda: root)
    monkeypatch.setattr(jellyfin, "_INDEX", None)
    monkeypatch.setattr(jellyfin, "last_error", {})
    return root


def _configure(monkeypatch, **jellyfin_settings):
    settings = Settings(jellyfin={"url": URL, "api_key": "key", **jellyfin_settings})
    monkeypatch.setattr(jellyfin, "get_settings", lambda: settings)


def _jellyfin():
    respx.get(f"{URL}/Library/VirtualFolders").mock(return_value=httpx.Response(200, json=[
        {"Name": "Filme", "ItemId": "movies", "Locations": ["G:\\media\\movies"]}, LIBRARY,
    ]))
    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(200, json={"Items": ITEMS}))
    return respx.post(url__regex=rf"{URL}/Items/\w+/Refresh").mock(return_value=httpx.Response(204))


def test_nothing_is_queued_without_a_jellyfin_to_tell(anime):
    assert jellyfin.queue([anime / "Bleach" / "Season 01" / "Bleach - S01E01.mkv"]) == 0
    assert jellyfin._load() == []


@respx.mock
def test_changed_episodes_seasons_and_shows_are_refreshed_by_their_path(anime, monkeypatch):
    _configure(monkeypatch)
    refresh = _jellyfin()
    season = anime / "Bleach" / "Season 01"

    jellyfin.queue([season / "Bleach - S01E01.mkv", season, anime / "Bleach"])
    jellyfin.queue([season / "Bleach - S01E01.mkv"])  # once is enough
    result = asyncio.run(jellyfin.flush())

    assert result == {"refreshed": 3, "not_in_jellyfin": 0, "remaining": 0}
    refreshed = [call.request.url.path.split("/")[2] for call in refresh.calls]
    assert refreshed == ["ep1", "season1", "show"]
    # A normal refresh: it takes a new local picture, and replaces nothing.
    params = refresh.calls[0].request.url.params
    assert (params["ImageRefreshMode"], params["ReplaceAllImages"]) == ("Default", "false")
    assert refresh.calls[0].request.headers["X-Emby-Token"] == "key"
    assert jellyfin._load() == []


@respx.mock
def test_an_episode_jellyfin_has_not_scanned_yet_is_left_to_its_first_scan(anime, monkeypatch):
    _configure(monkeypatch)
    refresh = _jellyfin()
    jellyfin.queue([anime / "Bleach" / "Season 01" / "Bleach - S01E99.mkv"])

    assert asyncio.run(jellyfin.flush())["not_in_jellyfin"] == 1
    assert refresh.call_count == 0 and jellyfin._load() == []


@respx.mock
def test_with_jellyfin_unreachable_the_queue_waits(anime, monkeypatch):
    _configure(monkeypatch)
    respx.get(f"{URL}/Library/VirtualFolders").mock(side_effect=httpx.ConnectTimeout(""))
    jellyfin.queue([anime / "Bleach" / "Season 01" / "Bleach - S01E02.mkv"])

    assert asyncio.run(jellyfin.flush())["failed"] == 1
    assert jellyfin._load() == ["Bleach/Season 01/Bleach - S01E02.mkv"]
    assert jellyfin.last_error["message"] == "ConnectTimeout"


@respx.mock
def test_the_library_can_be_named(anime, monkeypatch):
    _configure(monkeypatch, library="Nope")
    _jellyfin()
    jellyfin.queue([anime / "Bleach"])

    assert asyncio.run(jellyfin.flush())["failed"] == 1
    assert "Nope" in str(jellyfin.last_error["message"])


def test_a_new_episode_picture_is_queued_for_jellyfin(anime, monkeypatch):
    from bankai.web import anime_layout, library_walk

    video = anime / "Bleach" / "Season 01" / "Bleach - S01E01.mkv"
    video.write_bytes(b"video")
    library_walk.refresh_all()
    monkeypatch.setattr(anime_thumbs, "_DONE", {})
    monkeypatch.setattr(anime_thumbs, "_FAILED", {})
    monkeypatch.setattr(anime_layout, "pictures_allowed", lambda: True)
    monkeypatch.setattr(anime_thumbs, "make_thumb", lambda v: anime_thumbs.thumb_path(v).write_bytes(b"j") or True)
    queued: list = []
    monkeypatch.setattr(jellyfin, "queue", lambda paths: queued.extend(paths) or len(paths))

    anime_thumbs.sweep(anime, limit=5)
    assert queued == [video]


def test_queue_file_is_plain_relative_paths(anime, monkeypatch):
    _configure(monkeypatch)
    jellyfin.queue([anime / "Bleach" / "Season 01" / "Bleach - S01E01.mkv", anime.parent / "elsewhere.mkv"])
    assert json.loads(jellyfin._queue_path().read_text()) == ["Bleach/Season 01/Bleach - S01E01.mkv"]
