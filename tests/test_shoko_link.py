"""Telling Shoko which AniDB episode each published file is."""

from __future__ import annotations

import asyncio

import pytest

from bankai.config import Settings
from bankai.web import shoko, shoko_link


@pytest.fixture()
def world(monkeypatch, tmp_path):
    root = tmp_path / "shows_anime"
    settings = Settings(transfer={"anime_shows_dir": str(root)})
    monkeypatch.setattr(shoko_link, "get_settings", lambda: settings)
    monkeypatch.setattr(shoko, "configured", lambda: True)
    files: dict[str, dict] = {}
    series: dict[int, dict] = {}
    calls: list[tuple] = []

    async def get(path, /, **params):
        calls.append(("GET", path, params))
        if path == "/api/v3/File/PathEndsWith":
            found = files.get(params["path"])
            return [found] if found else []
        if path == "/api/v3/ImportFolder":
            return [{"ID": 1, "Path": str(root) + "/"}]
        raise AssertionError(path)

    async def send(method, path, /, *, json_body=None, **params):
        calls.append((method, path, json_body or params))
        if method == "GET" and path.endswith("/Series"):
            aid = int(path.split("/")[-2])
            return series.get(aid)
        return True

    monkeypatch.setattr(shoko, "_get", get)
    monkeypatch.setattr(shoko, "_send", send)
    return {"root": root, "files": files, "series": series, "calls": calls}


def published(world, aid=17617, episode=5, name="Sousou no Frieren"):
    path = world["root"] / name / f"{name} - {episode:02d}.mkv"
    return {
        "releases": {
            "a" * 40: {
                "status": "done",
                "canonical": f"anidb:{aid}|{episode}",
                "published_path": str(path),
            }
        }
    }, f"{name}/{name} - {episode:02d}.mkv"


def test_an_unidentified_file_is_linked_to_its_anidb_episode(world):
    state, relative = published(world)
    world["files"][relative] = {"ID": 39, "SeriesIDs": []}
    world["series"][17617] = {"IDs": {"ID": 11, "AniDB": 17617}}

    tally = asyncio.run(shoko_link.link_published(state))

    assert tally["linked"] == 1
    # Shoko requires both ends of the range, even for one episode.
    assert (
        "POST",
        "/api/v3/File/39/LinkFromSeries",
        {"SeriesID": 11, "RangeStart": "5", "RangeEnd": "5"},
    ) in world["calls"]
    assert state["releases"]["a" * 40]["shoko_linked"] is True


def test_a_file_shoko_identified_itself_is_left_alone(world):
    state, relative = published(world)
    world["files"][relative] = {"ID": 39, "SeriesIDs": [{"SeriesID": {"ID": 2}, "EpisodeIDs": [{"ID": 7}]}]}

    tally = asyncio.run(shoko_link.link_published(state))

    assert tally["already"] == 1
    assert not any(call[1].endswith("LinkFromSeries") for call in world["calls"])


def test_a_file_shoko_has_not_seen_asks_for_a_scan_and_waits(world):
    state, _ = published(world)

    tally = asyncio.run(shoko_link.link_published(state))

    assert tally["waiting_for_scan"] == 1
    assert ("GET", "/api/v3/ImportFolder/1/Scan", {}) in world["calls"]
    assert "shoko_linked" not in state["releases"]["a" * 40]


def test_an_anime_shoko_lacks_is_fetched_once_and_linked_later(world):
    state, relative = published(world)
    world["files"][relative] = {"ID": 39, "SeriesIDs": []}

    tally = asyncio.run(shoko_link.link_published(state))

    assert tally["waiting_for_series"] == 1
    refreshes = [call for call in world["calls"] if call[1] == "/api/v3/Series/AniDB/17617/Refresh"]
    assert len(refreshes) == 1
    assert "shoko_linked" not in state["releases"]["a" * 40]


def test_only_anidb_releases_bankai_published_are_touched(world):
    state = {
        "releases": {
            "b" * 40: {"status": "done", "canonical": "424536|1|5", "published_path": "/x.mkv"},
            "c" * 40: {"status": "queued", "canonical": "anidb:1|1"},
            "d" * 40: {"status": "done", "canonical": "anidb:1|1", "published_path": "/elsewhere/x.mkv"},
        }
    }
    assert shoko_link._candidates(state) == []


@pytest.mark.parametrize(("changed", "scans"), [("anime", 1), ("movies", 0), (None, 0)])
def test_a_change_in_the_anime_library_is_what_makes_shoko_scan(monkeypatch, tmp_path, changed, scans):
    from bankai.web import jobs, library_walk

    anime_root = tmp_path / "shows_anime"
    roots = {"anime": str(anime_root), "movies": str(tmp_path / "movies")}
    settings = Settings(transfer={"anime_shows_dir": str(anime_root)})
    monkeypatch.setattr(jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(library_walk, "refresh_all", lambda: {roots[changed]} if changed else set())
    calls: list[bool] = []

    async def scan():
        calls.append(True)
        return True

    monkeypatch.setattr(shoko_link, "scan_import_folder", scan)
    asyncio.run(jobs._refresh_library())
    assert len(calls) == scans
