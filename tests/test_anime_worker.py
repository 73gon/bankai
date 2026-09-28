"""The automation cycle in a worker process of its own."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from bankai.web import erai, library_walk


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr(erai, "_IS_WORKER", False)
    return tmp_path


def test_without_the_flag_cycles_run_in_this_process(state_dir, monkeypatch):
    assert not erai._delegated()
    started = []

    async def fake_cycle(**kwargs):
        started.append(kwargs)

    async def run() -> None:
        monkeypatch.setattr(erai, "run_cycle", fake_cycle)
        monkeypatch.setattr(erai, "_RETRY_TASK", None)
        erai._start_retry_cycle()
        await asyncio.sleep(0)

    asyncio.run(run())
    assert started == [{"prefill": True, "retries_only": True}]
    assert not erai._cycle_request_path().exists()


def test_with_an_external_worker_the_web_side_only_asks(state_dir, monkeypatch):
    monkeypatch.setenv("BANKAI_ANIME_WORKER", "external")
    assert erai._delegated()
    erai._start_retry_cycle()
    assert json.loads(erai._cycle_request_path().read_text())["retries_only"] is True
    # A full cycle asked for on top wins; a retries-only one after it does not undo that.
    erai._request_cycle(retries_only=False)
    erai._request_cycle(retries_only=True)
    request = erai._take_cycle_request()
    assert request["retries_only"] is False
    assert erai._take_cycle_request() is None


def test_status_reads_the_worker_heartbeat(state_dir, monkeypatch):
    from bankai.config import Settings

    settings = Settings(anime={"enabled": True}, metadata={"tvdb_api_key": "test"})
    monkeypatch.setattr(erai, "get_settings", lambda: settings)
    monkeypatch.setenv("BANKAI_ANIME_WORKER", "external")
    monkeypatch.setattr(erai, "free_space_gib", lambda: 1000.0)
    assert "worker is not running" in str(erai.status()["pause_reason"])
    erai._write_json(erai._heartbeat_path(), {"running": True, "updated_at": time.time()})
    report = erai.status()
    assert report["running"] is True
    assert "worker" not in str(report["pause_reason"] or "")
    erai._write_json(erai._heartbeat_path(), {"running": True, "updated_at": time.time() - 600})
    assert erai.status()["running"] is False


def test_the_worker_runs_what_is_asked_for_and_stops(state_dir, monkeypatch):
    ran = []

    async def fake_cycle(**kwargs):
        ran.append(kwargs)
        if len(ran) == 1:
            # Asked for while the scheduled one runs, as the web side would.
            erai._request_cycle(retries_only=True)
        else:
            erai.stop_worker()

    monkeypatch.setattr(erai, "run_cycle", fake_cycle)

    asyncio.run(asyncio.wait_for(erai.worker(poll_seconds=0.01), timeout=10))
    # The scheduled first cycle, then the request -- consumed.
    assert ran[0] == {"prefill": True, "retries_only": False}
    assert ran[1] == {"prefill": True, "retries_only": True}
    assert not erai._cycle_request_path().exists()
    beat = json.loads(erai._heartbeat_path().read_text())
    assert beat["running"] is False and beat.get("stopped")
    assert erai._IS_WORKER is True
    monkeypatch.setattr(erai, "_IS_WORKER", False)


def test_the_worker_cancels_a_cycle_in_progress_when_stopped(state_dir, monkeypatch):
    async def endless(**kwargs):
        erai.stop_worker()
        await asyncio.sleep(3600)

    monkeypatch.setattr(erai, "run_cycle", endless)
    asyncio.run(asyncio.wait_for(erai.worker(poll_seconds=0.01), timeout=10))
    monkeypatch.setattr(erai, "_IS_WORKER", False)


def test_the_state_lock_is_reentrant(state_dir):
    with erai._STATE_LOCK:
        with erai._STATE_LOCK:
            erai._save_retry_requests({"a": {}})
        assert erai._load_retry_requests() == {"a": {}}
    assert erai._STATE_LOCK._depth == 0


def test_the_library_walk_generation_moves_on_change():
    before = library_walk.generation()
    library_walk.mark_stale()
    assert library_walk.generation() > before


def test_a_cycle_saving_keeps_decisions_made_meanwhile(state_dir):
    """The cycle held the state for minutes and wrote it back whole, undoing
    a Discard made in the web process in the meantime."""
    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {"status": "held", "title": "A"},
        "b" * 40: {"status": "held", "title": "B"},
    }
    erai._save_state(state)

    cycle_state = erai._load_state()
    token = erai._begin_cycle_state(cycle_state)
    try:
        # Meanwhile, in the web process: A is discarded, C is new.
        web = erai._load_state()
        web["releases"]["a" * 40] = {"status": "blacklisted", "title": "A", "reason": "Series blacklisted by user"}
        web["releases"]["c" * 40] = {"status": "held", "title": "C"}
        erai._save_state(web)
        # The cycle changes B and saves its copy, twice.
        cycle_state["releases"]["b" * 40] = {"status": "queued", "title": "B"}
        erai._save_state(cycle_state)
        erai._save_state(cycle_state)
    finally:
        erai._CYCLE_BASE.reset(token)

    saved = erai._load_state()["releases"]
    assert saved["a" * 40]["status"] == "blacklisted"  # the user's decision stands
    assert saved["b" * 40]["status"] == "queued"  # the cycle's own change too
    assert saved["c" * 40]["status"] == "held"  # and what was added meanwhile


def test_a_change_the_cycle_made_itself_wins(state_dir):
    state = erai._default_state()
    state["releases"] = {"a" * 40: {"status": "held", "title": "A"}}
    erai._save_state(state)
    cycle_state = erai._load_state()
    token = erai._begin_cycle_state(cycle_state)
    try:
        web = erai._load_state()
        web["releases"]["a" * 40]["reason"] = "touched elsewhere"
        erai._save_state(web)
        cycle_state["releases"]["a" * 40] = {"status": "queued", "title": "A"}
        erai._save_state(cycle_state)
    finally:
        erai._CYCLE_BASE.reset(token)
    assert erai._load_state()["releases"]["a" * 40]["status"] == "queued"
