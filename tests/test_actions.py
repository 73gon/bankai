"""The user's actions, run in the background and shown on the dashboard."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from bankai.web import actions, dashboard


def test_an_action_runs_in_the_background_and_reports_its_result():
    async def run():
        order = []

        async def work(action):
            action.detail = "Deleting files"
            await asyncio.sleep(0.01)
            order.append("work")
            return {"deleted_files": 3}

        action = actions.start("purge", "Discard and delete: X", work, on_done=lambda: order.append("done"))
        assert action.status == "running"
        await actions.wait(action)
        assert action.status == "done" and action.result == {"deleted_files": 3}
        # Invalidated before it reads as done, so a reload on "done" is fresh.
        assert order == ["work", "done"]
        assert action.detail is None

    asyncio.run(run())


def test_a_failed_action_says_why():
    async def run():
        async def work(action):
            raise ValueError("Held release was not found")

        action = actions.start("review", "Discard: X", work)
        await actions.wait(action)
        assert action.status == "failed" and action.error == "Held release was not found"
        assert action.as_dict()["id"] in {row["id"] for row in actions.listing()}

    asyncio.run(run())


def test_the_dashboard_lists_running_and_finished_actions():
    board = dashboard.build(
        mas_rows=[], anime_rows=[], operations=[], automation={"alive": True, "running": False},
        passes=[], torrents=[], download_slots=None, pipeline_slots=1, publish_slots=1,
        retry_pending=None, recent={"mas": [], "anime": []},
        actions=[
            {"id": "a", "kind": "purge", "title": "Discard and delete: X", "status": "running", "detail": "Deleting files", "started_at": 1},
            {"id": "b", "kind": "review", "title": "Discard: Y", "status": "done", "result": {"blacklisted": 2}, "started_at": 0, "finished_at": 2},
        ],
    )
    lane = next(lane for lane in board["lanes"] if lane["key"] == "actions")
    assert lane["busy"] == 1
    assert [w["task"]["detail"] for w in lane["workers"]] == ["Deleting files", "2 releases blacklisted"]
    # The finished one is shown, not counted as a worker.
    assert lane["workers"][1]["extra"] is True


def test_the_page_gets_an_action_id_at_once(tmp_path, monkeypatch):
    from bankai.config import reset_settings_cache
    from bankai.web.app import create_app

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_settings_cache()

    async def slow(info_hash, action):
        await asyncio.sleep(0.2)
        return {"ok": True, "requested": 0, "blacklisted": 4}

    monkeypatch.setattr("bankai.web.erai.review_action", slow)
    with TestClient(create_app()) as client:
        started = client.post("/api/anime/review/" + "a" * 40, json={"action": "blacklist", "label": "Show", "background": True}).json()
        assert set(started) == {"action_id", "status"}
        followed = client.get("/api/actions/" + started["action_id"]).json()
        assert followed["title"] == "Discard: Show"
        # Without "background" the reply waits and carries the result, as before.
        waited = client.post("/api/anime/review/" + "a" * 40, json={"action": "blacklist"}).json()
        assert waited["blacklisted"] == 4
    reset_settings_cache()
