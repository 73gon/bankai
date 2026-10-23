"""The background search for the anime library's missing episodes, and downloading what it found."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bankai.config import reset_settings_cache
from bankai.web import anime, anime_files, anime_missing, erai
from bankai.web.app import create_app


def release(title: str, number: int, *, seeders: int = 10) -> anime.NyaaEntry:
    return anime.NyaaEntry(
        id=number,
        title=title,
        download_url=f"https://nyaa.si/download/{number}.torrent",
        detail_url=f"https://nyaa.si/view/{number}",
        magnet_uri=f"magnet:?xt=urn:btih:{number:040x}",
        info_hash=f"{number:040x}",
        category_id="1_2",
        category="Anime",
        size="1 GiB",
        size_bytes=1024**3,
        seeders=seeders,
        leechers=0,
        downloads=0,
        comments=0,
        trusted=True,
        remake=False,
        published_at=None,
        publisher="Erai-raws",
        quality="1080p",
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    (tmp_path / "staging" / "Shows").mkdir(parents=True)
    (tmp_path / "shows_anime").mkdir()
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(tmp_path / "staging"))
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(tmp_path / "shows_anime"))
    monkeypatch.setenv("BANKAI_ANIME__ENABLED", "false")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()
    with TestClient(create_app()) as value:
        yield value
    reset_settings_cache()


@pytest.fixture()
def show(client, tmp_path, monkeypatch, anilist_data):
    """An entry of four aired episodes with 1 and 2 on disk; what Nyaa has is the test's."""
    anilist_data.add(555, "Show", episodes=4)
    folder = tmp_path / "shows_anime" / "Show" / "Season 01"
    folder.mkdir(parents=True)
    for number in (1, 2):
        path = folder / f"Show - S01E{number:02d}.mkv"
        path.write_bytes(b"video")
        anime_files.record(path, [(555, number)])
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai, "save_anilist_mapping", lambda *args, **kwargs: None)
    # German as the release name says it; the real check also asks Erai-raws and Nyaa.
    async def german(item, _client):
        return "[GER]" in item.title

    monkeypatch.setattr(erai, "_carries_german", german)
    monkeypatch.setattr(erai.erai_site, "german_sibling", lambda _hash: None)

    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(anime_missing.asyncio, "sleep", no_sleep)
    on_nyaa: list[anime.NyaaEntry] = []

    async def fetch(_client, _term, _category, _page):
        return list(on_nyaa)

    monkeypatch.setattr(anime, "_fetch_rss", fetch)
    return on_nyaa


def _grid(client) -> dict:
    return client.get("/api/anime/library", params={"rescan": True}).json()["shows"][0]


def test_a_missing_episode_with_a_german_release_is_recorded_and_counted(client, show):
    german = release("[Erai-raws] Show - 03 [1080p][HEVC][MultiSub][GER]", 3)
    show.extend([german, release("[Erai-raws] Show - 03 [1080p][AVC][MultiSub][GER]", 4, seeders=0)])

    result = asyncio.run(anime_missing.check_missing_episodes())

    assert result == {"checked": 1, "episodes_found": 1, "remaining": 0}
    found = anime_missing.checked()
    assert found[(555, 3)]["info_hash"] == german.info_hash  # the one without seeders is none
    assert found[(555, 3)]["hevc"] is True and found[(555, 3)]["first"] == 3
    # Episode 4 was looked for too, and nothing was found.
    assert "info_hash" not in found[(555, 4)]
    assert _grid(client)["missing_available_count"] == 1
    # Looked at a moment ago: not again until a week has passed.
    assert asyncio.run(anime_missing.check_missing_episodes())["checked"] == 0


def test_a_release_without_german_is_not_offered(client, show):
    show.append(release("[Erai-raws] Show - 03 [1080p][HEVC][MultiSub]", 3))

    assert asyncio.run(anime_missing.check_missing_episodes())["episodes_found"] == 0
    assert "info_hash" not in anime_missing.checked()[(555, 3)]
    assert _grid(client)["missing_available_count"] == 0


def test_the_dialog_downloads_only_the_releases_picked(client, show, monkeypatch):
    third = release("[Erai-raws] Show - 03 [1080p][HEVC][MultiSub][GER]", 3)
    fourth = release("[Erai-raws] Show - 04 [1080p][HEVC][MultiSub][GER]", 4)
    show.extend([third, fourth])
    asyncio.run(anime_missing.check_missing_episodes())
    assert _grid(client)["missing_available_count"] == 2

    listed = client.get("/api/anime/library/missing", params={"key": "Show"}).json()["items"]
    assert [item["info_hash"] for item in listed] == [third.info_hash, fourth.info_hash]
    assert listed[0]["episodes"][0]["episode"] == 3

    queued: list[dict] = []
    monkeypatch.setattr(
        "bankai.web.jobs.enqueue", lambda **kwargs: queued.append(kwargs) or {"status": "queued", "id": "x"}
    )
    # Only a release the search found for this show is taken.
    refused = client.post(
        "/api/anime/library/missing/download", json={"key": "Show", "info_hashes": ["f" * 40]}
    )
    assert refused.status_code == 404 and queued == []

    response = client.post(
        "/api/anime/library/missing/download", json={"key": "Show", "info_hashes": [third.info_hash]}
    )

    assert response.status_code == 200
    assert response.json()["queued"] == 1
    assert len(queued) == 1
    args = queued[0]["args"]
    assert args[args.index("--info-hash") + 1] == third.info_hash
    assert args[args.index("--episode") + 1] == "3"
    assert args[args.index("--anilist-id") + 1] == "555"
    # Straight into the library, as the drawer's own download goes.
    assert "--publish" in args
    # Queued: no longer offered; the other one still is.
    assert _grid(client)["missing_available_count"] == 1
    left = client.get("/api/anime/library/missing", params={"key": "Show"}).json()["items"]
    assert [item["info_hash"] for item in left] == [fourth.info_hash]
