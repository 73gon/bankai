"""Smoke tests for the web API. Skipped if FastAPI isn't installed."""

from __future__ import annotations

import os
from array import array
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from bankai.config import get_settings, reset_settings_cache
from bankai.torrent.prowlarr import TorrentCandidate
from bankai.web.app import (
    _ebur128_envelope,
    _parse_range,
    _stream_site_from_url,
    _waveform_envelope,
    create_app,
)
from bankai.web.discover import DiscoverItem


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    library = tmp_path / "library"
    (library / "Movies").mkdir(parents=True)
    (library / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(library))
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()
    get_settings()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_discover_search_forwards_movie_search_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    async def fake_search(
        query: str, *, kind: str, search_by: str, limit: int = 51
    ) -> list[object]:
        calls.append((query, kind, search_by))
        return []

    monkeypatch.setattr("bankai.web.discover.search", fake_search)
    monkeypatch.setattr("bankai.web.discover.is_configured", lambda: True)

    response = client.get(
        "/api/discover/search", params={"q": "Anne Hathaway", "kind": "movie", "by": "person"}
    )

    assert response.status_code == 200
    assert calls == [("Anne Hathaway", "movie", "person")]


def test_discover_search_rejects_non_title_show_mode(client: TestClient) -> None:
    response = client.get(
        "/api/discover/search", params={"q": "Disney", "kind": "show", "by": "studio"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "person and studio search are only available for movies"


def test_discover_search_marks_titles_already_added(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_search(
        query: str, *, kind: str, search_by: str, limit: int = 51
    ) -> list[DiscoverItem]:
        return [
            DiscoverItem(name="Queued Movie", kind="movie", year=2024),
            DiscoverItem(name="Staged Movie", kind="movie", year=2023),
            DiscoverItem(name="Server Movie", kind="movie", year=2022),
            DiscoverItem(name="New Movie", kind="movie", year=2021),
        ]

    monkeypatch.setattr("bankai.web.discover.search", fake_search)
    monkeypatch.setattr("bankai.web.discover.is_configured", lambda: True)
    monkeypatch.setattr("bankai.web.jobs.snapshot", lambda: [{"title": "Queued Movie (2024)"}])
    monkeypatch.setattr(
        "bankai.web.media.scan_library",
        lambda: [SimpleNamespace(kind="movie", name="Staged Movie (2023)", series=None)],
    )
    monkeypatch.setattr(
        "bankai.web.media.scan_server",
        lambda kind: [SimpleNamespace(name="Server Movie (2022)")],
    )

    response = client.get("/api/discover/search", params={"q": "Movie", "kind": "movie"})

    assert response.status_code == 200
    added_by_name = {item["name"]: item["added"] for item in response.json()["items"]}
    assert added_by_name == {
        "Queued Movie": True,
        "Staged Movie": True,
        "Server Movie": True,
        "New Movie": False,
    }


def test_library_empty(client: TestClient) -> None:
    r = client.get("/api/library")
    assert r.status_code == 200
    assert r.json()["entries"] == []


def test_queue_snapshot(client: TestClient) -> None:
    r = client.get("/api/queue")
    assert r.status_code == 200
    assert "jobs" in r.json()


def test_queue_movie_revalidates_filmpalast_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[dict[str, object]] = []

    async def no_mirrors(_url: str) -> int:
        return 0

    monkeypatch.setattr("bankai.web.app._verify_filmpalast_source", no_mirrors)
    monkeypatch.setattr(
        "bankai.web.jobs.enqueue",
        lambda **kwargs: queued.append(kwargs) or {"status": "queued"},
    )

    response = client.post(
        "/api/queue/movie",
        json={
            "title": "A Star Is Born",
            "german": "A Star Is Born",
            "year": 2018,
            "site": "filmpalast",
            "url": "https://filmpalast.to/stream/a-star-is-born",
        },
    )

    assert response.status_code == 409
    assert "no longer has a supported German stream mirror" in response.json()["detail"]
    assert queued == []


def test_queue_movie_accepts_revalidated_filmpalast_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[dict[str, object]] = []

    async def working_mirror(_url: str) -> int:
        return 2

    monkeypatch.setattr("bankai.web.app._verify_filmpalast_source", working_mirror)
    monkeypatch.setattr(
        "bankai.web.jobs.enqueue",
        lambda **kwargs: queued.append(kwargs) or {"status": "queued"},
    )

    response = client.post(
        "/api/queue/movie",
        json={
            "title": "A Star Is Born",
            "year": 2018,
            "site": "filmpalast",
            "url": "https://filmpalast.to/stream/a-star-is-born",
        },
    )

    assert response.status_code == 200
    assert len(queued) == 1


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

    # A repack atomically replaces the file and therefore changes st_ctime on
    # Windows. The UI's Created value must remain the first value we recorded.
    replacement = movie.with_suffix(".replacement")
    replacement.write_bytes(b"repacked movie")
    replacement.replace(movie)
    os.utime(movie, (stat.st_mtime + 60, stat.st_mtime + 60))
    second = client.get("/api/titles")
    second_row = second.json()["rows"][0]
    assert second_row["created_at"] == pytest.approx(row["created_at"])
    assert second_row["updated_at"] > row["updated_at"]


def test_titles_library_exposes_both_saved_sources(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "Sourced Movie (2024).mkv"
    movie.write_bytes(b"movie")
    from bankai.web import review as review_mod

    review_mod.set_sources(
        movie,
        german_source_url="https://voe.sx/german",
        torrent_source_url="https://indexer.test/details/123",
        torrent_source_title="Sourced.Movie.2024.1080p",
    )
    monkeypatch.setattr("bankai.web.jobs.snapshot", lambda: [])
    monkeypatch.setattr("bankai.web.jobs.transfer_states", lambda: {})
    monkeypatch.setattr("bankai.web.posters.ensure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("bankai.web.posters.cached", lambda _key: None)
    monkeypatch.setattr("bankai.web.posters.cached_year", lambda _key: None)

    response = client.get("/api/titles")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["german_source_url"] == "https://voe.sx/german"
    assert row["torrent_source_url"] == "https://indexer.test/details/123"
    assert row["torrent_source_title"] == "Sourced.Movie.2024.1080p"


def test_titles_never_surfaces_a_repack_as_a_job_row(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "Hidden Repack (2024).mkv"
    from bankai.web import review as review_mod

    review_mod.set_repack(movie, "repacking", kind="audio")
    monkeypatch.setattr("bankai.web.media.scan_library", lambda: [])
    monkeypatch.setattr("bankai.web.jobs.transfer_states", lambda: {})
    monkeypatch.setattr("bankai.web.jobs.repack_states", lambda: {})
    monkeypatch.setattr(
        "bankai.web.jobs.snapshot",
        lambda: [
            {
                "id": "old-pipeline",
                "kind": "movie",
                "title": "Hidden Repack (2024)",
                "status": "done",
                "started_at": 100.0,
                "finished_at": 200.0,
                "final_path": str(movie),
                "pending": False,
            }
        ],
    )

    response = client.get("/api/titles")

    assert response.status_code == 200
    assert response.json()["rows"] == []


def test_titles_hides_atomic_repack_and_replacement_files(client: TestClient) -> None:
    movies = Path(get_settings().output.directory) / "Movies" / "The Irishman (2019)"
    movies.mkdir(parents=True)
    (movies / "The Irishman (2019).mkv").write_bytes(b"original")
    (movies / "The Irishman (2019).mkv.replace.mkv").write_bytes(b"working")
    (movies / "The Irishman (2019).mkv.repack.mkv").write_bytes(b"working")

    rows = client.get("/api/titles").json()["rows"]

    library_rows = [row for row in rows if row["row_kind"] == "library"]
    assert [row["name"] for row in library_rows] == ["The Irishman (2019)"]


def test_titles_redo_reuses_source_and_forces_transactional_output(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "Arcane S02E01.mkv"
    movie.write_bytes(b"existing review file")
    source = "https://voe.sx/qld3siz1iznp"
    previous = SimpleNamespace(
        kind="show",
        title="Arcane S02E01",
        args=[
            "run",
            "Arcane S02E01",
            "--url",
            source,
            "--kind",
            "episode",
            "--out",
            "C:\\obsolete\\old.mkv",
        ],
        started_at=200.0,
        final_path=str(movie),
    )
    queued: list[dict[str, object]] = []
    monkeypatch.setattr("bankai.cli.bgjobs.list_jobs", lambda: [previous])
    monkeypatch.setattr(
        "bankai.web.jobs.enqueue",
        lambda **kwargs: queued.append(kwargs) or {"status": "running", "id": "redo1"},
    )

    response = client.post("/api/titles/redo", json={"path": str(movie)})

    assert response.status_code == 200
    args = queued[0]["args"]
    assert isinstance(args, list)
    assert args[args.index("--url") + 1] == source
    assert args.count("--out") == 1
    assert Path(args[args.index("--out") + 1]) == movie.resolve()


def test_queue_force_and_priority_endpoints(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bankai.web.jobs.force_start_pending",
        lambda job_id: SimpleNamespace(id=f"started-{job_id}", status="running"),
    )
    monkeypatch.setattr("bankai.web.jobs.reorder_pending", lambda job_id, position: position)

    forced = client.post("/api/queue/queued1/force")
    moved = client.post("/api/queue/queued1/priority", json={"position": 2})

    assert forced.json() == {"started": True, "id": "started-queued1", "status": "running"}
    assert moved.json() == {"id": "queued1", "position": 2}


def test_approve_with_changed_delay_starts_background_repack_on_same_entry(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "Repack Movie (2024).mkv"
    movie.write_bytes(b"movie")
    queued: list[dict[str, object]] = []

    def fake_enqueue(*, kind: str, title: str, args: list[str]) -> dict:
        queued.append({"kind": kind, "title": title, "args": args})
        return {"status": "running", "id": "repack1", "title": title}

    monkeypatch.setattr("bankai.web.jobs.enqueue", fake_enqueue)

    response = client.post(
        "/api/review/approve",
        json={"path": str(movie), "delay_ms": 275, "track_index": 2},
    )

    assert response.status_code == 200
    assert response.json()["background"] is True
    assert response.json()["stage"] == "repacking"
    assert queued == [
        {
            "kind": "repack",
            "title": "Repack Repack Movie (2024).mkv",
            "args": ["review-repack", str(movie), "--delay-ms", "275", "--track-index", "2"],
        }
    ]


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


def test_episode_torrent_replacement_passes_show_identity(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = (
        Path(get_settings().output.directory)
        / "Shows"
        / "Arcane"
        / "Season 02"
        / "Arcane - S02E01.mkv"
    )
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"episode")
    queued: list[dict[str, object]] = []

    monkeypatch.setattr(
        "bankai.web.jobs.enqueue",
        lambda **kwargs: queued.append(kwargs) or {"status": "running", "id": "replace1"},
    )

    response = client.post(
        "/api/review/replace-torrent",
        json={
            "path": str(episode),
            "query": "Arcane S02E01",
            "kind": "episode",
            "series_title": "Arcane",
            "season": 2,
            "episode": 1,
            "magnet_uri": "magnet:?xt=urn:btih:" + "a" * 40,
        },
    )

    assert response.status_code == 200
    args = queued[0]["args"]
    assert isinstance(args, list)
    assert args[args.index("--kind") + 1] == "episode"
    assert args[args.index("--series-title") + 1] == "Arcane"
    assert args[args.index("--season") + 1] == "2"
    assert args[args.index("--episode") + 1] == "1"
    assert "--candidate-json" in args


def test_torrent_picker_searches_tv_categories_and_applies_temporary_filters(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[int] | None]] = []

    def candidate(title: str, seeders: int, marker: str) -> TorrentCandidate:
        return TorrentCandidate(
            title=title,
            indexer="Test",
            indexer_id=1,
            download_url=f"https://example.test/{marker}.torrent",
            info_url=f"https://example.test/{marker}",
            magnet_uri=None,
            info_hash=marker * 40,
            size_bytes=5 * 1024**3,
            seeders=seeders,
            leechers=0,
            publish_date=None,
        )

    class FakeProwlarr:
        async def search(
            self,
            query: str,
            *,
            categories: list[int] | None = None,
        ) -> list[TorrentCandidate]:
            calls.append((query, categories))
            if query == "Arcane S02":
                return [
                    candidate("Arcane.S02.1080p.WEB-DL.x265-GRP", 40, "a"),
                    candidate("Arcane.S02.1080p.WEB-DL.x265-BUSY", 200, "b"),
                ]
            return []

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("bankai.torrent.prowlarr.ProwlarrClient", FakeProwlarr)
    response = client.get(
        "/api/torrents/search",
        params={
            "q": "Arcane S02E01",
            "kind": "episode",
            "series_title": "Arcane",
            "season": 2,
            "episode": 1,
            "min_seeders": 10,
            "max_seeders": 100,
            "min_size_gib": 1,
            "max_size_gib": 20,
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in calls] == ["Arcane S02", "Arcane S02E01"]
    assert all(call[1] and call[1][0] == 5000 for call in calls)
    by_seeders = {row["seeders"]: row for row in response.json()["candidates"]}
    assert by_seeders[40]["eligible"] is True
    assert by_seeders[200]["eligible"] is False


def test_queue_show_rejects_invalid_or_duplicate_custom_episode_links(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("bankai.web.jobs.enqueue", lambda **_kwargs: {})
    invalid = client.post(
        "/api/queue/show",
        json={
            "show": "Arcane",
            "season": 2,
            "custom_episodes": [{"episode": 1, "url": "voe.sx/no-scheme"}],
        },
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
    assert (
        _stream_site_from_url("https://burningseries.ac/serie/Arcane/2/1-title/de")
        == "burningseries"
    )
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
    # 1,000 / 32,768 is roughly -30 dBFS, which maps to the middle of the
    # fixed display range.  A single full-scale impulse must not flatten the
    # other bins or make the whole response look loud.
    assert min(peaks[:18]) >= 68
    assert max(peaks[:18]) <= 80
    assert peaks[-1] <= peaks[0]
    assert max(_waveform_envelope([0] * 200, 20)) == 0


def test_ebur128_envelope_uses_fixed_perceived_loudness_scale() -> None:
    output = "\n".join(
        ["lavfi.r128.M=-120.691", "lavfi.r128.M=-55.0", "lavfi.r128.M=-23.0", "lavfi.r128.M=-8.0"]
    )

    bars = _ebur128_envelope(output, 4)

    assert bars[0] == 0
    assert 20 < bars[1] < bars[2] < bars[3] <= 127


def test_close_zoom_waveform_uses_detailed_pcm(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "waveform.mkv"
    movie.write_bytes(b"source")
    calls: list[list[str]] = []
    pcm = array("h", [1000, -1000] * 1200).tobytes()

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=pcm, stderr=b"")

    monkeypatch.setattr("bankai.web.media.ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr("subprocess.run", fake_run)

    response = client.get(
        "/api/media/waveform",
        params={"path": str(movie), "stream": 1, "start": 30, "dur": 9, "bins": 600},
    )

    assert response.status_code == 200
    assert response.json()["detail"] == "pcm"
    assert response.json()["bins"] == 600
    assert calls[0][calls[0].index("-f") + 1] == "s16le"


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


def test_audio_clip_applies_leading_silence_and_drift_rate(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = Path(get_settings().output.directory) / "Movies" / "audio-preview.mkv"
    movie.write_bytes(b"source")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"clip")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("bankai.web.media.ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr("subprocess.run", fake_run)

    response = client.get(
        "/api/media/audioclip",
        params={
            "path": str(movie),
            "stream": 2,
            "start": 0,
            "dur": 10,
            "lead": 2.5,
            "rate": 1.0427,
        },
    )

    assert response.status_code == 200
    audio_filter = calls[0][calls[0].index("-af") + 1]
    assert "atempo=1.04270000" in audio_filter
    assert "adelay=2500:all=1" in audio_filter


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
