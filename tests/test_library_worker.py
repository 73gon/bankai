"""The library worker: encode, browser audio and track cleanup in a process of their own."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time

from bankai.cli import bgjobs
from bankai.web import browser_audio, encode_library, library_worker, track_cleanup
from bankai.web.state_lock import StateLock


def _beat(**fields):
    path = bgjobs.jobs_root().parent / "library_worker.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 1, "updated_at": time.time(), **fields}), encoding="utf-8")


def test_with_the_worker_the_web_reads_each_pass_s_progress_from_its_heartbeat(monkeypatch):
    encode_library.progress.update(path="/in/this/process.mkv")
    try:
        # Without the worker: this process's own.
        assert encode_library.current_progress()["path"] == "/in/this/process.mkv"
        monkeypatch.setenv("BANKAI_LIBRARY_WORKER", "external")
        assert encode_library.current_progress() == {}
        _beat(progress={"encode": {"path": "/g/Avengers.mkv", "step": "encode", "fraction": 0.5, "started": time.time() - 60},
                        "browser_audio": {}, "track_clean": {"path": "/g/Coco.mkv"}})
        assert encode_library.current_progress()["path"] == "/g/Avengers.mkv"
        assert encode_library.lane()["current"]["eta"] > 0
        assert track_cleanup.current_progress()["path"] == "/g/Coco.mkv"
        assert browser_audio.current_progress() == {}
        # A worker silent for over a minute is down: nothing runs.
        _beat(updated_at=time.time() - 300, progress={"encode": {"path": "/g/Avengers.mkv"}})
        assert encode_library.current_progress() == {}
    finally:
        encode_library.progress.clear()


def test_the_worker_runs_what_is_due_says_so_and_restarts_once_its_code_changed(monkeypatch):
    ran = []
    gate = threading.Event()

    def run(name):
        ran.append(name)
        gate.wait(5)
        return {"done": name}

    monkeypatch.setattr(library_worker, "_recover", lambda: None)
    monkeypatch.setattr(library_worker, "_due", lambda name: name == "encode")
    monkeypatch.setattr(library_worker, "_run", run)
    monkeypatch.setattr(library_worker, "_interval", lambda name: 60.0)
    # bankai's code changes once the encode is under way.
    monkeypatch.setattr(library_worker, "_code_stamp", lambda: 2.0 if ran else 1.0)
    monkeypatch.setattr(library_worker, "_TURN_SECONDS", 0.05)
    monkeypatch.setattr(library_worker, "_PASSES", {})

    async def go():
        task = asyncio.ensure_future(library_worker.worker())
        # Its first turn is 5 s off; brought forward by a queue change.
        await asyncio.sleep(0.1)
        path = bgjobs.jobs_root().parent / "encode_queue.json"
        path.write_text('{"items": [{"path": "/g/a.mkv"}]}', encoding="utf-8")
        for _ in range(100):
            beat = library_worker.heartbeat() or {}
            if (beat.get("passes") or {}).get("encode", {}).get("running"):
                break
            await asyncio.sleep(0.05)
        assert ran == ["encode"]
        # The code changed while the encode runs: it is not cut off, the worker waits for it.
        await asyncio.sleep(0.3)
        assert not task.done() and library_worker.heartbeat()["draining"] is True
        gate.set()
        return await asyncio.wait_for(task, 5)

    code = asyncio.run(go())
    assert code == 3 and ran == ["encode"]
    beat = json.loads((bgjobs.jobs_root().parent / "library_worker.json").read_text())
    assert beat["stopped"] is True and beat["passes"]["encode"]["result"] == {"done": "encode"}
    assert library_worker.heartbeat() is None


def test_a_state_lock_holds_off_another_process(tmp_path):
    with StateLock("encode"):
        pass
    lock = bgjobs.jobs_root().parent / "encode.lock"
    with subprocess.Popen(
        [sys.executable, "-c", (
            "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR); fcntl.flock(fd, fcntl.LOCK_EX); "
            "print('held', flush=True); time.sleep(0.6)"
        ), str(lock)],
        stdout=subprocess.PIPE, text=True,
    ) as holder:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with StateLock("encode"):
            waited = time.monotonic() - started
    assert waited >= 0.3


def test_one_process_has_the_passes_and_the_worker_waits_for_it(monkeypatch):
    """Started while bankai-web still ran them, the worker's recovery would have thrown away the
    half-written result of the encode running there."""
    from bankai.web import library_worker as worker

    monkeypatch.setattr(worker, "_CLAIM_FD", None)
    monkeypatch.setattr(worker, "_DEFERRED", False)
    lock = bgjobs.jobs_root().parent / "library_passes.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    with subprocess.Popen(
        [sys.executable, "-c", (
            "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR); fcntl.flock(fd, fcntl.LOCK_EX); "
            "print('held', flush=True); time.sleep(3)"
        ), str(lock)],
        stdout=subprocess.PIPE, text=True,
    ) as holder:
        assert holder.stdout.readline().strip() == "held"
        # bankai-web finding them taken leaves them, as if told to.
        assert worker.claim(wait=False) is False and worker.delegated() is True
        monkeypatch.setattr(worker, "_DEFERRED", False)
        started = time.monotonic()
        assert worker.claim(wait=True) is True
        assert time.monotonic() - started >= 1.0
    worker._release()
