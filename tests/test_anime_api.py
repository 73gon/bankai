from __future__ import annotations

import asyncio
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


def test_the_server_library_manages_anime_roots_too(client: TestClient, tmp_path) -> None:
    """The bottom library is where anything on the server is added.

    It scanned movie and show roots only, so anime -- which the pipeline
    publishes to its own directory -- could not be reached from it.
    """
    body = client.get("/api/server/dirs").json()
    assert set(body) == {"movie_dirs", "show_dirs", "anime_dirs"}

    root = tmp_path / "extra_anime_root"
    root.mkdir()
    added = client.post("/api/server/dirs", json={"kind": "anime", "path": str(root)})
    assert added.status_code == 200
    assert str(root) in added.json()["dirs"]
    assert str(root) in client.get("/api/server/dirs").json()["anime_dirs"]

    # Anime is listed beside movies and shows, not folded into them.
    assert "anime" in client.get("/api/server/contents").json()

    removed = client.request("DELETE", "/api/server/dirs", json={"kind": "anime", "path": str(root)})
    assert removed.status_code == 200
    assert str(root) not in removed.json()["dirs"]


def test_an_unknown_server_directory_kind_is_refused(client: TestClient) -> None:
    response = client.post("/api/server/dirs", json={"kind": "podcast", "path": "/tmp/x"})
    assert response.status_code == 400
    assert "movie, show or anime" in response.json()["detail"]


def test_sidebar_counts_answers_every_badge_in_one_call(client: TestClient) -> None:
    """Five badges polling five endpoints would be five state reads a tick."""
    body = client.get("/api/sidebar/counts").json()
    assert set(body) == {"counts"}
    # Every value is a number; a count that could not be gathered is absent
    # rather than zero, so a badge dims instead of lying.
    assert all(isinstance(value, int) for value in body["counts"].values())
    assert body["counts"]["anime_review"] == 0
    assert body["counts"]["anime_blacklist"] == 0


def test_a_count_that_cannot_be_gathered_is_left_out(client: TestClient, monkeypatch) -> None:
    """qBittorrent being unreachable must not empty the rest of the row."""
    from bankai.web import erai as erai_mod

    def boom() -> list:
        raise RuntimeError("state unreadable")

    monkeypatch.setattr(erai_mod, "blacklist_items", boom)
    counts = client.get("/api/sidebar/counts").json()["counts"]
    assert "anime_blacklist" not in counts
    assert "anime_review" in counts


def test_marking_a_series_owned_is_not_read_as_an_info_hash(client: TestClient) -> None:
    """/api/anime/review/owned once matched /api/anime/review/{info_hash}.

    FastAPI takes routes in registration order, so "owned" arrived as an info
    hash, no release by that name existed, and every "Already downloaded"
    click answered "Held release was not found".
    """
    response = client.post("/api/anime/review/owned", json={"key": "nothing-held"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "cleared": 0}


def test_marking_owned_still_requires_something_to_mark(client: TestClient) -> None:
    assert client.post("/api/anime/review/owned", json={}).status_code == 422


def test_anime_automation_defaults_to_100_gib_reserve(client: TestClient) -> None:
    body = client.get("/api/anime/automation").json()
    assert body["min_free_space_gib"] == 100
    assert body["enabled"] is False
    assert body["backfill"]["complete"] is False


def test_anime_queue_uses_separate_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bankai.web.jobs.anime_snapshot", lambda: [{"id": "anime"}])
    assert client.get("/api/anime/queue").json() == {
        "jobs": [{"id": "anime"}],
        "total": 1,
        "counts": {"unknown": 1},
        "page": 0,
        "page_size": 100,
    }


def test_anime_queue_is_server_paginated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bankai.web.jobs.anime_snapshot",
        lambda: [{"id": str(index)} for index in range(205)],
    )
    body = client.get("/api/anime/queue", params={"page": 2, "page_size": 100}).json()
    assert body["total"] == 205
    assert [row["id"] for row in body["jobs"]] == [str(index) for index in range(200, 205)]


def test_anime_queue_hides_done_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bankai.web.jobs.anime_snapshot",
        lambda: [
            {"id": "done", "status": "done"},
            {"id": "running", "status": "running"},
            {"id": "failed", "status": "failed"},
        ],
    )
    hidden = client.get("/api/anime/queue").json()
    assert hidden["total"] == 2
    assert [row["id"] for row in hidden["jobs"]] == ["running", "failed"]

    shown = client.get("/api/anime/queue", params={"include_done": True}).json()
    assert shown["total"] == 3
    assert [row["id"] for row in shown["jobs"]] == ["done", "running", "failed"]


def test_anime_queue_filters_by_title_and_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bankai.web.jobs.anime_snapshot",
        lambda: [
            {"id": "1", "title": "Frieren S01E04", "status": "running"},
            {"id": "2", "title": "Frieren S01E05", "status": "queued"},
            {"id": "3", "title": "Dandadan S01E01", "status": "failed"},
            {"id": "4", "title": "Frieren S01E03", "status": "done"},
        ],
    )

    unfiltered = client.get("/api/anime/queue").json()
    assert unfiltered["total"] == 3  # the done row stays hidden by default
    assert unfiltered["counts"] == {"running": 1, "queued": 1, "failed": 1}

    searched = client.get("/api/anime/queue", params={"q": "frieren"}).json()
    assert [row["id"] for row in searched["jobs"]] == ["1", "2"]
    # Counts follow the search, so every chip advertises its own result.
    assert searched["counts"] == {"running": 1, "queued": 1}

    combined = client.get(
        "/api/anime/queue", params={"q": "FRIEREN", "status": "queued"}
    ).json()
    assert [row["id"] for row in combined["jobs"]] == ["2"]
    assert combined["total"] == 1
    # Narrowing to one status must not collapse the other chips to zero.
    assert combined["counts"] == {"running": 1, "queued": 1}

    done = client.get(
        "/api/anime/queue", params={"include_done": True, "status": "done"}
    ).json()
    assert [row["id"] for row in done["jobs"]] == ["4"]

    assert client.get("/api/anime/queue", params={"status": "all"}).json()["total"] == 3
    assert client.get("/api/anime/queue", params={"q": "nothing"}).json()["total"] == 0


def test_anime_queue_filters_running_jobs_by_phase(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting on a torrent and copying into the library are both "running"."""
    monkeypatch.setattr(
        "bankai.web.jobs.anime_snapshot",
        lambda: [
            {"id": "dl", "title": "A S01E01", "status": "running", "phase": "downloading"},
            {"id": "tx", "title": "B S01E01", "status": "running", "phase": "transferring"},
            {"id": "q", "title": "C S01E01", "status": "queued", "phase": "queued"},
        ],
    )

    body = client.get("/api/anime/queue").json()
    assert body["counts"] == {"downloading": 1, "transferring": 1, "queued": 1}

    only_transfers = client.get("/api/anime/queue", params={"status": "transferring"}).json()
    assert [row["id"] for row in only_transfers["jobs"]] == ["tx"]

    # The raw status is no longer a selectable bucket; the phases replace it.
    assert client.get("/api/anime/queue", params={"status": "running"}).json()["total"] == 0


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
    entries = client.get("/api/anime/library", params={"include_entries": True}).json()["entries"]
    assert {entry["name"] for entry in entries} == {final.name, staged.name}
    assert {entry["staged"] for entry in entries} == {True, False}
    main = client.get("/api/library").json()["entries"]
    assert [entry["name"] for entry in main] == [normal.stem]
    titles = client.get("/api/titles").json()["rows"]
    assert str(staged) not in {row["path"] for row in titles}


def test_library_groups_episodes_into_one_show_with_sorted_seasons(
    client: TestClient, tmp_path: Path
) -> None:
    for season, episode in [(2, 3), (1, 2), (1, 1)]:
        path = (
            tmp_path
            / "shows_anime"
            / "Example Anime"
            / f"Season {season:02d}"
            / f"Example - S{season:02d}E{episode:02d}.mkv"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
    # Rescan: the app's scheduler may already hold a tree walked while these
    # files were still being written, as it would until its next refresh.
    body = client.get("/api/anime/library", params={"rescan": True}).json()
    assert len(body["shows"]) == 1
    show = body["shows"][0]
    assert show["episode_count"] == 3 and show["season_count"] == 2
    assert show["episodes"] == []
    detail = client.get("/api/anime/library", params={"show": "Example Anime"}).json()["shows"][0]
    assert [(row["season_number"], row["episode"]) for row in detail["episodes"]] == [
        (1, 1),
        (1, 2),
        (2, 3),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/?page=rss&u=Erai-raws",
        "https://nyaa.si/?page=rss&u=Somebody",
        "http://nyaa.si/?page=rss&u=Erai-raws",
        "https://nyaa.si/?page=rss&u=Erai-raws&c=1_4",
    ],
)
def test_rss_setting_rejects_non_erai_nyaa_feeds(client: TestClient, url: str) -> None:
    response = client.post("/api/settings", json={"key": "anime.rss_url", "value": url})
    assert response.status_code == 422


def test_queue_cover_uses_canonical_tvdb_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bankai.web import anime_library

    anime_library._CACHE.clear()

    async def metadata(title, tvdb_id):
        assert tvdb_id == 74796
        return {"english_title": "Bleach", "poster_url": "https://example.com/bleach.jpg"}

    monkeypatch.setattr(
        "bankai.web.jobs.anime_snapshot",
        lambda: [{"id": "anime", "title": "Bleach S17E14", "tvdb_id": "74796"}],
    )
    monkeypatch.setattr(anime_library, "show_metadata", metadata)
    row = client.get("/api/anime/queue").json()["jobs"][0]
    assert row["poster_url"] == "https://example.com/bleach.jpg" and row["series_title"] == "Bleach"


@pytest.mark.parametrize(
    "downloaded,future,ended,expected,finished",
    [
        (0, False, False, "empty", False),
        (1, False, False, "partial", False),
        (1, True, False, "upcoming", False),
        (2, False, False, "complete", False),
        (2, False, True, "complete", True),
    ],
)
def test_show_completion_uses_full_tvdb_roster(downloaded, future, ended, expected, finished):
    from bankai.metadata.tvdb import TVDBEpisode
    from bankai.web.anime_library import merge_episodes

    files = [
        {"path": str(n), "season_number": 1, "episode": n, "name": str(n), "staged": False}
        for n in range(1, downloaded + 1)
    ]
    roster = [
        TVDBEpisode(1, 1, aired="2020-01-01"),
        TVDBEpisode(1, 2, aired="2099-01-01" if future else "2020-01-08"),
    ]
    result = merge_episodes(files, roster, ended=ended)
    assert result["completion_state"] == expected and result["finished"] == finished
    assert result["downloaded_count"] == downloaded and result["total_count"] == 2
    assert len(result["episodes"]) == 2
    assert sum(row.get("missing", False) for row in result["episodes"]) == 2 - downloaded


def test_staged_and_duplicate_files_do_not_inflate_download_count():
    from bankai.metadata.tvdb import TVDBEpisode
    from bankai.web.anime_library import merge_episodes

    files = [
        {"path": "final", "season_number": 1, "episode": 1, "name": "final", "staged": False},
        {"path": "staged", "season_number": 1, "episode": 1, "name": "staged", "staged": True},
        {"path": "waiting", "season_number": 1, "episode": 2, "name": "waiting", "staged": True},
    ]
    result = merge_episodes(
        files,
        [TVDBEpisode(1, 1, aired="2020-01-01"), TVDBEpisode(1, 2, aired="2020-01-08")],
        ended=True,
    )
    assert result["downloaded_count"] == 1 and result["completion_state"] == "partial"
    assert result["episodes"][0]["path"] == "final"


def test_missing_episode_search_reverses_anidb_part_offset(monkeypatch):
    from dataclasses import replace
    from xml.etree import ElementTree as ET

    from bankai.metadata import anime_mapping
    from bankai.metadata.tvdb import TVDBEpisode
    from bankai.web import anime, anime_library, erai

    part_name = "Bleach Sennen Kessen Hen Ketsubetsu Tan"
    part = anime_mapping.Part(
        74796, 17765, ET.fromstring('<anime defaulttvdbseason="17" episodeoffset="13"/>')
    )
    roster = [TVDBEpisode(17, 14, 380), TVDBEpisode(17, 15, 381), TVDBEpisode(1, 1, 1)]
    match = anime.AnimeTVDBMatch(74796, "show", "Bleach", aliases=(part_name,))
    sample = anime.NyaaEntry(
        1,
        f"[Erai-raws] {part_name} - 01 [1080p][MultiSub]",
        "https://nyaa.si/download/1.torrent",
        "https://nyaa.si/view/1",
        "magnet:?xt=urn:btih:" + "1" * 40,
        "1" * 40,
        "1_2",
        "Anime",
        "1 GiB",
        1024**3,
        10,
        0,
        100,
        0,
        True,
        False,
        None,
        "Erai-raws",
        "1080p",
    )
    searched = []

    async def metadata(*args):
        return match

    async def episodes(*args):
        return roster

    async def titles(*args):
        return [part_name]

    async def parts(*args):
        return [part]

    async def candidates(*args, **kwargs):
        return [match]

    async def fetch(client, query, *args):
        searched.append(query)
        return [
            sample,
            replace(sample, id=2, info_hash="2" * 40, title=sample.title.replace(" - 01", " - 02")),
        ]

    monkeypatch.setattr(anime, "series_metadata", metadata)
    monkeypatch.setattr(anime_library, "episode_roster", episodes)
    monkeypatch.setattr(anime_mapping, "related_titles", titles)
    monkeypatch.setattr(anime_mapping, "anidb_parts", parts)
    monkeypatch.setattr(anime, "tvdb_candidates", candidates)
    monkeypatch.setattr(anime, "_fetch_rss", fetch)
    monkeypatch.setattr(erai, "_load_state", erai._default_state)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    result = asyncio.run(anime_library.search_episode(74796, 17, 14))
    assert any(part_name in query and "- 01" in query for query in searched)
    assert len(result["items"]) == 1
    assert (result["items"][0]["season"], result["items"][0]["episode"]) == (17, 14)


def test_retry_held_endpoint_returns_scheduled_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bankai.web.erai.retry_held", lambda: {"requested": 14, "retry_pending": 14}
    )
    response = client.post("/api/anime/automation/retry-held")
    assert response.status_code == 200
    assert response.json() == {"requested": 14, "retry_pending": 14}


def test_anime_review_and_blacklist_actions(client, monkeypatch):
    review = [
        {
            "key": "show",
            "info_hash": "1" * 40,
            "source_title": "Show",
            "title": "Show",
            "poster_url": None,
            "updated_at": 1,
        }
    ]
    monkeypatch.setattr("bankai.web.erai.review_items", lambda: review)
    monkeypatch.setattr("bankai.web.erai.blacklist_items", lambda: review)
    monkeypatch.setattr(
        "bankai.web.anime_library.enrich_review_rows",
        lambda rows: asyncio.sleep(0, result=rows),
    )
    async def review_action(info_hash, action):
        return {"ok": True, "requested": 2, "action": action}

    monkeypatch.setattr("bankai.web.erai.review_action", review_action)
    monkeypatch.setattr(
        "bankai.web.erai.remove_blacklist",
        lambda key: {"ok": True, "requested": 2},
    )
    assert client.get("/api/anime/review").json()["items"] == review
    assert client.get("/api/anime/blacklist").json()["items"] == review
    assert (
        client.post("/api/anime/review/" + "1" * 40, json={"action": "allow_german"}).json()[
            "requested"
        ]
        == 2
    )
    assert client.post("/api/anime/blacklist/remove", json={"key": "show"}).json()["requested"] == 2
