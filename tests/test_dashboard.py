"""The dashboard: lanes of workers, what is due, what arrived lately."""

from __future__ import annotations

from bankai.web import dashboard

NOW = 1_800_000_000.0


def _build(**overrides):
    parts = {
        "mas_rows": [],
        "anime_rows": [],
        "operations": [],
        "automation": {"alive": True, "running": False, "activity": {}},
        "passes": [],
        "torrents": [],
        "download_slots": 3,
        "pipeline_slots": 1,
        "publish_slots": 2,
        "retry_pending": None,
        "recent": {"mas": [], "anime": []},
        "now": NOW,
    }
    parts.update(overrides)
    return dashboard.build(**parts)


def _lane(board, key):
    return next(lane for lane in board["lanes"] if lane["key"] == key)


def test_each_slot_is_a_worker_busy_or_idle():
    board = _build(
        mas_rows=[
            {"id": "a", "title": "Heat (1995)", "status": "running", "step_label": "Syncing", "overall_percent": 60},
            {"id": "b", "title": "Ronin (1998)", "status": "queued", "pending": True, "queue_position": 1},
        ],
        anime_rows=[
            {"id": "c", "title": "Frieren S01E05", "status": "running", "step_label": "Publishing"},
            {"id": "d", "title": "Oshi no Ko 01", "status": "queued", "pending": True, "phase": "complete", "updated_at": 5},
            {"id": "e", "title": "Oshi no Ko 02", "status": "queued", "pending": True, "phase": "downloading"},
        ],
    )
    pipelines = _lane(board, "pipelines")
    assert [worker["busy"] for worker in pipelines["workers"]] == [True]
    assert pipelines["workers"][0]["task"]["detail"] == "Syncing"
    assert pipelines["workers"][0]["task"]["percent"] == 60
    publishing = _lane(board, "publishing")
    assert [worker["busy"] for worker in publishing["workers"]] == [True, False]
    due = {group["lane"]: group for group in board["due"]}
    assert [item["title"] for item in due["pipelines"]["items"]] == ["Ronin (1998)"]
    assert [item["title"] for item in due["publishing"]["items"]] == ["Oshi no Ko 01"]
    assert due["backlog"]["items"][0]["count"] == 1
    assert board["summary"]["due_total"] == 3


def test_more_running_than_slots_shows_every_one():
    rows = [{"id": str(n), "title": f"Job {n}", "status": "running"} for n in range(3)]
    assert len(_lane(_build(mas_rows=rows), "pipelines")["workers"]) == 3


def test_the_automation_says_what_it_is_checking():
    board = _build(
        automation={
            "alive": True,
            "running": True,
            "activity": {"phase": "Checking releases", "done": 5, "total": 20, "current": "[Erai-raws] X - 01"},
        }
    )
    worker = _lane(board, "automation")["workers"][0]
    assert worker["busy"]
    assert worker["task"]["title"] == "Checking releases (5 of 20)"
    assert worker["task"]["detail"] == "[Erai-raws] X - 01"
    assert worker["task"]["percent"] == 25


def test_a_worker_that_is_not_reporting_is_down():
    board = _build(automation={"alive": False, "running": True})
    assert _lane(board, "automation")["state"] == "down"
    assert board["summary"]["automation"] == "down"


def test_downloads_fill_qbittorrents_slots_fastest_first():
    board = _build(
        torrents=[
            {"hash": "1", "name": "slow", "state": "downloading", "dlspeed": 10, "progress": 0.5},
            {"hash": "2", "name": "fast", "state": "downloading", "dlspeed": 5_000_000, "progress": 0.1},
            {"hash": "3", "name": "waiting", "state": "queuedDL"},
        ]
    )
    workers = _lane(board, "downloads")["workers"]
    assert [worker["task"]["title"] if worker["task"] else None for worker in workers] == ["fast", "slow", None]
    assert workers[0]["task"]["detail"] == "4.8 MiB/s"


def test_scheduled_passes_list_the_running_one_first():
    board = _build(
        passes=[
            {"label": "Check the libraries", "every_seconds": 60, "due_at": NOW + 30, "running": False},
            {"label": "Publish finished downloads", "every_seconds": 20, "due_at": NOW + 5, "running": True},
        ],
        automation={"alive": True, "running": False, "next_cycle_at": NOW + 400},
    )
    scheduled = next(group for group in board["due"] if group["lane"] == "scheduled")["items"]
    assert [item["title"] for item in scheduled] == [
        "Publish finished downloads",
        "Check the libraries",
        "Anime automation cycle",
    ]
    assert scheduled[1]["detail"] == "Every minute"
    assert _lane(board, "scheduler")["workers"][0]["busy"]


def _file(series: str, name: str, mtime: float, root: str = "/lib") -> dict:
    return {"series": series, "name": name, "mtime": mtime, "root": root}


def test_recent_episodes_of_a_show_are_one_addition():
    files = [
        _file("Frieren", "Frieren - S01E05.mkv", NOW - 10),
        _file("Frieren", "Frieren - S01E04.mkv", NOW - 20),
        _file("Frieren", "Frieren - S01E01.mkv", NOW - 30 * 86400),  # long before: not this addition
        _file("Bleach", "Bleach - S02E01.mkv", NOW - 15),
    ]
    cards = {"Frieren": {"key": "Frieren", "title": "Frieren: Beyond Journey's End", "poster_url": "/p/1"}}
    rows = dashboard.recent_additions(files, cards, kind="show", href="/a/library")
    assert [row["title"] for row in rows] == ["Frieren: Beyond Journey's End", "Bleach"]
    assert rows[0]["count"] == 2
    assert rows[0]["episode_label"] == "S01E04-E05"
    assert rows[0]["poster_url"] == "/p/1"
    assert rows[1]["episode_label"] == "S02E01"


def test_recent_additions_stop_at_the_limit():
    files = [_file(f"Movie {n}", f"Movie {n}.mkv", NOW - n) for n in range(30)]
    rows = dashboard.recent_additions(files, {}, kind="movie", href="/mas/library", limit=5)
    assert [row["title"] for row in rows] == [f"Movie {n}" for n in range(5)]
    assert rows[0]["episode_label"] is None


def test_the_dashboard_endpoint_answers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from bankai.config import reset_settings_cache
    from bankai.web.app import create_app

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("BANKAI_QBITTORRENT__URL", "http://127.0.0.1:9")
    reset_settings_cache()
    with TestClient(create_app()) as client:
        body = client.get("/api/dashboard").json()
    assert {lane["key"] for lane in body["lanes"]} >= {"pipelines", "publishing", "automation"}
    assert body["summary"]["workers_total"] >= 3
    assert set(body["recent"]) == {"mas", "anime"}
    reset_settings_cache()
