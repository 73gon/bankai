from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bankai.config import reset_settings_cache
from bankai.web import review
from bankai.web.app import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    staging = tmp_path / "staging"
    (staging / "Shows").mkdir(parents=True)
    root = tmp_path / "shows_anime"
    root.mkdir()
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(root))
    monkeypatch.setenv("BANKAI_ANIME__ENABLED", "false")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()
    with TestClient(create_app()) as value:
        yield value
    reset_settings_cache()


def test_anime_nested_pages_have_spa_routes(client: TestClient) -> None:
    for route in ("/anime/discover", "/anime/queue", "/anime/library", "/anime/settings"):
        response = client.get(route)
        assert response.status_code == 200
        assert "<html" in response.text


def test_anime_automation_defaults_to_100_gib_reserve(client: TestClient) -> None:
    body = client.get("/api/anime/automation").json()
    assert body["min_free_space_gib"] == 100
    assert body["enabled"] is False
    assert body["backfill"]["complete"] is False


def test_anime_queue_uses_separate_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bankai.web.jobs.anime_snapshot", lambda: [{"id": "anime"}])
    assert client.get("/api/anime/queue").json() == {"jobs": [{"id": "anime"}]}


def test_anime_settings_are_validated(client: TestClient) -> None:
    rows = {row["key"]: row for row in client.get("/api/settings").json()["settings"]}
    assert rows["anime.min_free_space_gib"]["value"] == 100
    assert "transfer.anime_shows_dir" in rows
    bad = client.post("/api/settings", json={"key": "anime.poll_interval_seconds", "value": 1})
    assert bad.status_code == 422
    good = client.post("/api/settings", json={"key": "anime.min_free_space_gib", "value": 100})
    assert good.status_code == 200


def test_anime_library_includes_final_and_staged_nyaa_files_only(
    client: TestClient,
    tmp_path: Path,
) -> None:
    final = tmp_path / "shows_anime" / "Test Show" / "Season 01" / "Test Show - S01E01.mkv"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"final")
    staged = tmp_path / "staging" / "Shows" / "Anime" / "Season 01" / "Anime - S01E01.mkv"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"staged")
    review.set_sources(staged, torrent_source_url="https://nyaa.si/view/1")
    review.set_stage(staged, "approved")
    normal = tmp_path / "staging" / "Shows" / "Normal" / "Season 01" / "Normal - S01E01.mkv"
    normal.parent.mkdir(parents=True)
    normal.write_bytes(b"normal")
    entries = client.get("/api/anime/library").json()["entries"]
    assert {entry["name"] for entry in entries} == {final.name, staged.name}
    assert {entry["staged"] for entry in entries} == {True, False}
    main = client.get("/api/library").json()["entries"]
    assert [entry["name"] for entry in main] == [normal.stem]
    titles = client.get("/api/titles").json()["rows"]
    assert str(staged) not in {row["path"] for row in titles}
