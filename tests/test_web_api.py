"""Smoke tests for the web API. Skipped if FastAPI isn't installed."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from bankai.config import get_settings, reset_settings_cache  # noqa: E402
from bankai.web.app import _parse_range, create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    library = tmp_path / "library"
    (library / "Movies").mkdir(parents=True)
    (library / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(library))
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
