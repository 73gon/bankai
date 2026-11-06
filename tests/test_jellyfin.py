"""Telling Jellyfin what bankai changed, so new titles and pictures show without a refresh by hand."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

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
def test_a_video_jellyfin_has_not_scanned_yet_has_its_nearest_known_folder_refreshed(anime, monkeypatch):
    """Its watcher misses files written over /mnt/g: Spider-Man: Brand New Day waited for a scan."""
    _configure(monkeypatch)
    refresh = _jellyfin()
    monkeypatch.setattr(jellyfin, "_local", lambda location: anime.parent / "movies" if "movies" in location else None)
    jellyfin.queue([
        anime / "Bleach" / "Season 01" / "Bleach - S01E99.mkv",
        anime / "Bleach" / "Season 01" / "Bleach - S01E98.mkv",
        anime.parent / "movies" / "New Film (2026)" / "New Film (2026).mkv",
    ])

    result = asyncio.run(jellyfin.flush())
    assert result["not_in_jellyfin"] == 3
    # The season once for both episodes (recursively, which finds them), the movie library for the film.
    calls = [(call.request.url.path.split("/")[2], call.request.url.params.get("Recursive")) for call in refresh.calls]
    assert calls == [("season1", "true"), ("movies", "true")]
    assert jellyfin._load() == []


@respx.mock
def test_with_jellyfin_unreachable_the_queue_waits(anime, monkeypatch):
    _configure(monkeypatch)
    respx.get(f"{URL}/Library/VirtualFolders").mock(side_effect=httpx.ConnectTimeout(""))
    jellyfin.queue([anime / "Bleach" / "Season 01" / "Bleach - S01E02.mkv"])

    assert asyncio.run(jellyfin.flush())["failed"] == 1
    assert [row["path"] for row in jellyfin._load()] == [str(anime / "Bleach" / "Season 01" / "Bleach - S01E02.mkv")]
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


def test_queue_file_is_paths_and_whether_to_probe_again(anime, monkeypatch):
    _configure(monkeypatch)
    video = anime / "Bleach" / "Season 01" / "Bleach - S01E01.mkv"
    jellyfin.queue([video, "relative.mkv"])
    jellyfin.queue([video], reprobe=True)  # a second ask for a probe is kept
    assert json.loads(jellyfin._queue_path().read_text()) == [{"path": str(video), "reprobe": True}]


def test_an_earlier_queue_of_anime_paths_is_read(anime):
    jellyfin._save(["Bleach/Season 01"])  # type: ignore[list-item]
    assert jellyfin._load() == [{"path": str(anime / "Bleach" / "Season 01"), "reprobe": False}]


@respx.mock
def test_a_video_written_anew_is_probed_again_and_its_old_subtitles_go(anime, monkeypatch, tmp_path):
    """Its tracks are numbered anew; Jellyfin's old list and extracted subtitles point at others."""
    _configure(monkeypatch)
    refresh = _jellyfin()
    data = tmp_path / "jellyfin"
    item = "2e36d62f16c097c827d9d9bc5bab8d81"
    extracted = data / "data" / "subtitles" / "2e" / "2e36d62f-16c0-97c8-27d9-d9bc5bab8d81"
    extracted.mkdir(parents=True)
    (extracted / "20.srt").write_text("old")
    monkeypatch.setattr(jellyfin, "_local", lambda location: data if location == "C:\\Jellyfin" else None)
    respx.get(f"{URL}/System/Info").mock(return_value=httpx.Response(200, json={"ProgramDataPath": "C:\\Jellyfin"}))
    respx.get(f"{URL}/Items").mock(return_value=httpx.Response(200, json={"Items": [
        {"Id": item, "Path": "G:\\media\\shows_anime\\Bleach\\Season 01\\Bleach - S01E01.mkv"},
    ]}))

    jellyfin.queue([anime / "Bleach" / "Season 01" / "Bleach - S01E01.mkv"], reprobe=True)
    assert asyncio.run(jellyfin.flush())["refreshed"] == 1
    assert refresh.calls[0].request.url.params["MetadataRefreshMode"] == "FullRefresh"
    assert refresh.calls[0].request.url.params["ReplaceAllMetadata"] == "false"
    assert not extracted.exists()


@respx.mock
def test_movies_and_shows_on_a_windows_drive_are_found_too(anime, monkeypatch):
    _configure(monkeypatch)
    respx.get(f"{URL}/Library/VirtualFolders").mock(return_value=httpx.Response(200, json=[
        {"Name": "Filme", "ItemId": "movies", "CollectionType": "movies", "Locations": ["E:\\media\\movies"]}, LIBRARY,
    ]))

    def items(request):
        if request.url.params["ParentId"] == "movies":
            return httpx.Response(200, json={"Items": [{"Id": "film", "Path": "E:\\media\\movies\\Memento (2000)\\Memento (2000).mkv"}]})
        return httpx.Response(200, json={"Items": ITEMS})

    respx.get(f"{URL}/Items").mock(side_effect=items)
    respx.get(f"{URL}/System/Info").mock(return_value=httpx.Response(200, json={}))
    refresh = respx.post(url__regex=rf"{URL}/Items/\w+/Refresh").mock(return_value=httpx.Response(204))

    jellyfin.queue([Path("/mnt/e/media/movies/Memento (2000)/Memento (2000).mkv")], reprobe=True)
    assert asyncio.run(jellyfin.flush())["refreshed"] == 1
    assert refresh.calls[0].request.url.path == "/Items/film/Refresh"
