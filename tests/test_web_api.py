"""Smoke tests for the web API. Skipped if FastAPI isn't installed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from bankai.config import get_settings, reset_settings_cache
from bankai.web.app import _parse_range, create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    library = tmp_path / "library"
    (library / "Movies").mkdir(parents=True)
    (library / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(library))
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()
    get_settings()
    app = create_app()
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_library_empty(client: TestClient) -> None:
    r = client.get("/api/library")
    assert r.status_code == 200
    assert r.json()["entries"] == []


def test_queue_snapshot(client: TestClient) -> None:
    r = client.get("/api/queue")
    assert r.status_code == 200
    assert "jobs" in r.json()


def test_settings_get_masks_secrets(client: TestClient) -> None:
    r = client.get("/api/settings")
    assert r.status_code == 200
    rows = {row["key"]: row for row in r.json()["settings"]}
    assert "metadata.tvdb_api_key" in rows
    assert rows["metadata.tvdb_api_key"]["secret"] is True


def test_settings_exposes_hq_torrent_preferences(client: TestClient) -> None:
    rows = {row["key"]: row for row in client.get("/api/settings").json()["settings"]}
    assert rows["selector.preferred_resolutions"]["value"] == ["2160p", "1080p"]
    assert rows["selector.min_size_gib"]["value"] == 0.5
    assert rows["selector.max_size_gib"]["value"] == 80.0
    assert rows["selector.min_seeders"]["value"] == 1


def test_settings_saves_and_validates_hq_torrent_preferences(client: TestClient) -> None:
    r = client.post("/api/settings", json={"key": "selector.min_size_gib", "value": 6.5})
    assert r.status_code == 200
    r = client.post(
        "/api/settings",
        json={"key": "selector.preferred_resolutions", "value": ["1080p", "2160p"]},
    )
    assert r.status_code == 200

    rows = {row["key"]: row for row in client.get("/api/settings").json()["settings"]}
    assert rows["selector.min_size_gib"]["value"] == 6.5
    assert rows["selector.preferred_resolutions"]["value"] == ["1080p", "2160p"]

    invalid = client.post("/api/settings", json={"key": "selector.min_seeders", "value": -1})
    assert invalid.status_code == 422
    invalid_bounds = client.post(
        "/api/settings",
        json={"key": "selector.min_size_gib", "value": 100},
    )
    assert invalid_bounds.status_code == 422


def test_video_clip_muxes_reference_audio(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "movie.mkv"
    movie.write_bytes(b"source")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"clip")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("bankai.web.media.ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr("subprocess.run", fake_run)

    r = client.get(
        "/api/media/videoclip",
        params={"path": str(movie), "start": 30, "dur": 10, "height": 480, "audio": 2},
    )
    assert r.status_code == 200
    assert calls
    cmd = calls[0]
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "0:2" in cmd
    assert "-an" not in cmd


def test_settings_rejects_unknown_key(client: TestClient) -> None:
    r = client.post("/api/settings", json={"key": "qbittorrent.password", "value": "x"})
    assert r.status_code == 403


def test_media_info_path_traversal_blocked(client: TestClient) -> None:
    r = client.get("/api/media/info", params={"path": "/etc/passwd"})
    assert r.status_code == 403


def test_parse_range() -> None:
    assert _parse_range("bytes=0-99", 1000) == (0, 99)
    assert _parse_range("bytes=100-", 1000) == (100, 999)
    assert _parse_range("bytes=-", 1000) == (0, 999)
    assert _parse_range("garbage", 1000) == (0, 999)
