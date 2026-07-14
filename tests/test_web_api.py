"""Smoke tests for the web API. Skipped if FastAPI isn't installed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from bankai.config import get_settings, reset_settings_cache
from bankai.web.app import _parse_range, _stream_site_from_url, _waveform_envelope, create_app


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


def test_titles_exposes_stable_created_and_updated_timestamps(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = 1_700_000_000.0
    finished = started + 600
    monkeypatch.setattr(
        "bankai.web.jobs.snapshot",
        lambda: [
            {
                "id": "finished-job",
                "kind": "movie",
                "title": "Finished Movie (2024)",
                "status": "done",
                "started_at": started,
                "finished_at": finished,
                "final_path": None,
                "reason": None,
                "reason_detail": None,
                "step_label": "Done",
                "overall_percent": 100.0,
                "total_steps": 4,
                "pending": False,
            },
            {
                "id": "running-job",
                "kind": "movie",
                "title": "Running Movie (2025)",
                "status": "running",
                "started_at": started + 1000,
                "finished_at": None,
                "final_path": None,
                "reason": None,
                "reason_detail": None,
                "step_label": "Downloading",
                "overall_percent": 25.0,
                "total_steps": 4,
                "pending": False,
            },
        ],
    )
    monkeypatch.setattr("bankai.web.jobs.transfer_states", lambda: {})
    monkeypatch.setattr("bankai.web.posters.ensure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankai.web.posters.cached", lambda _key: None)
    monkeypatch.setattr("bankai.web.posters.cached_year", lambda _key: None)

    r = client.get("/api/titles")

    assert r.status_code == 200
    rows = {row["id"]: row for row in r.json()["rows"]}
    assert rows["finished-job"]["created_at"] == started
    assert rows["finished-job"]["updated_at"] == finished
    assert rows["running-job"]["created_at"] == started + 1000
    assert rows["running-job"]["updated_at"] == started + 1000


def test_titles_library_uses_file_creation_and_modification_times(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "Timestamp Movie (2024).mkv"
    movie.write_bytes(b"movie")
    stat = movie.stat()
    monkeypatch.setattr("bankai.web.jobs.snapshot", lambda: [])
    monkeypatch.setattr("bankai.web.jobs.transfer_states", lambda: {})
    monkeypatch.setattr("bankai.web.posters.ensure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankai.web.posters.cached", lambda _key: None)
    monkeypatch.setattr("bankai.web.posters.cached_year", lambda _key: None)

    r = client.get("/api/titles")

    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["created_at"] == pytest.approx(float(getattr(stat, "st_birthtime", stat.st_ctime)))
    assert row["updated_at"] == pytest.approx(stat.st_mtime)


def test_queue_show_accepts_custom_episode_mirror_links(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[dict[str, object]] = []

    def fake_enqueue(*, kind: str, title: str, args: list[str]) -> dict:
        queued.append({"kind": kind, "title": title, "args": args})
        return {"status": "queued", "title": title}

    monkeypatch.setattr("bankai.web.jobs.enqueue", fake_enqueue)
    r = client.post(
        "/api/queue/show",
        json={
            "show": "Arcane",
            "season": 2,
            "custom_episodes": [
                {"episode": 1, "title": "Heavy Is the Crown", "url": "https://voe.sx/e/abc123"},
                {"episode": 2, "url": "https://filmpalast.to/stream/arcane-s02e02"},
            ],
        },
    )

    assert r.status_code == 200
    assert r.json()["count"] == 2
    assert [item["title"] for item in queued] == ["Arcane S02E01", "Arcane S02E02"]
    first_args = queued[0]["args"]
    second_args = queued[1]["args"]
    assert isinstance(first_args, list)
    assert isinstance(second_args, list)
    assert first_args[first_args.index("--site") + 1] == "unknown"
    assert first_args[first_args.index("--url") + 1] == "https://voe.sx/e/abc123"
    assert first_args[first_args.index("--episode-title") + 1] == "Heavy Is the Crown"
    assert second_args[second_args.index("--site") + 1] == "filmpalast"


def test_queue_show_rejects_invalid_or_duplicate_custom_episode_links(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("bankai.web.jobs.enqueue", lambda **_kwargs: {})
    invalid = client.post(
        "/api/queue/show",
        json={"show": "Arcane", "season": 2, "custom_episodes": [{"episode": 1, "url": "voe.sx/no-scheme"}]},
    )
    duplicate = client.post(
        "/api/queue/show",
        json={
            "show": "Arcane",
            "season": 2,
            "custom_episodes": [
                {"episode": 1, "url": "https://voe.sx/one"},
                {"episode": 1, "url": "https://voe.sx/two"},
            ],
        },
    )

    assert invalid.status_code == 400
    assert duplicate.status_code == 400


def test_stream_site_detection_keeps_direct_hosters_direct() -> None:
    assert _stream_site_from_url("https://voe.sx/e/abc") == "unknown"
    assert _stream_site_from_url("https://burningseries.ac/serie/Arcane/2/1-title/de") == "burningseries"
    assert _stream_site_from_url("https://filmpalast.to/stream/title") == "filmpalast"
    with pytest.raises(ValueError):
        _stream_site_from_url("javascript:alert(1)")


def test_waveform_envelope_is_not_flattened_by_one_outlier() -> None:
    samples: list[int] = []
    for _ in range(19):
        samples.extend([1000, -1000] * 50)
    samples.extend([32_000] + [0] * 99)

    peaks = _waveform_envelope(samples, 20)

    assert len(peaks) == 20
    assert min(peaks[:19]) >= 100
    assert max(_waveform_envelope([0] * 200, 20)) == 0


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
