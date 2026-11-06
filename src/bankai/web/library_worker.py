"""The passes that rewrite library files, in a process of their own: `bankai library worker`.

Encode (most of an hour on the GPU for a 4K film), browser audio (Ant-Man's 45 minutes) and track
cleanup ran as tasks of the web process, so every restart of bankai-web -- each deploy -- cut the
encode off and its picture started over. With BANKAI_LIBRARY_WORKER=external the web process
leaves them to this worker (systemd unit ``bankai-library-worker``): bankai-web restarts at any
time, the worker carries on.

The two talk through files only. What to do is where it always was -- ``encode_queue.json``,
``browser_audio.json``, the track rules and removals -- written by the web UI and read here each
turn; a change to one of them makes its pass due at once, so a file queued starts within seconds.
What the worker is doing goes into its heartbeat, ``library_worker.json`` (every 2 s): each pass's
progress, which the web reads in place of its own in-memory ``progress`` (``progress(name)``), and
each pass's run, result and next turn for the Dashboard (``pass_row``). Both write the same state
files under web.state_lock.

One process runs the passes at a time, whatever starts first: the one running them holds
``library_passes.lock`` for its life (``claim``). bankai-web without the worker takes it at startup;
the worker waits for it -- started while bankai-web still ran them, its recovery would have thrown
away the half-written result of the encode running there. A bankai-web that finds the worker
holding it leaves the passes to it, as if told to.

Its own code changed (a deploy): the worker starts no new file, waits for the running ones to
finish, and exits; systemd starts it again on the new code. So nothing waits for the right moment
to restart, and no encode is cut off by an update.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

PASSES = ("track_clean", "browser_audio", "encode")
_HEARTBEAT_SECONDS = 2.0
# A worker not heard from in this long is taken to be down: the web shows no progress of it.
_STALE_SECONDS = 60.0
_TURN_SECONDS = 3.0
_ENCODE_SECONDS = 60.0
# The files whose change makes a pass due at once.
_INPUTS = {
    "encode": ("encode_queue.json",),
    "browser_audio": ("browser_audio.json",),
    "track_clean": ("track_rules.json", "track_removals.json"),
}

_IS_WORKER = False
# This process could not have the passes: the worker has them (see ``claim``).
_DEFERRED = False
_CLAIM_FD: int | None = None
_STOP = threading.Event()
# Each pass's run, as the Dashboard shows it: running, last_run, due_at, result, error.
_PASSES: dict[str, dict[str, Any]] = {}


def _path(name: str) -> Path:
    return bgjobs.jobs_root().parent / name


def delegated() -> bool:
    """Does the library worker run the passes that rewrite files, rather than this process?"""
    if _IS_WORKER:
        return False
    return _DEFERRED or os.environ.get("BANKAI_LIBRARY_WORKER", "").strip().casefold() == "external"


def claim(*, wait: bool) -> bool:
    """Hold the passes for this process's life (an flock on ``library_passes.lock``). ``wait``:
    until it is free, or the worker is stopped; else at once or not at all. Without fcntl (Windows),
    always."""
    global _CLAIM_FD, _DEFERRED
    try:
        import fcntl
    except ImportError:
        return True
    if _CLAIM_FD is not None:
        return True
    path = _path("library_passes.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _CLAIM_FD = fd
            return True
        except BlockingIOError:
            if not wait or _STOP.wait(2.0):
                os.close(fd)
                if not _IS_WORKER:
                    _DEFERRED = True
                return False


def heartbeat() -> dict[str, Any] | None:
    """The worker's last heartbeat, if it is alive."""
    try:
        beat = json.loads(_path("library_worker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(beat, dict) or beat.get("stopped"):
        return None
    if time.time() - float(beat.get("updated_at") or 0) > _STALE_SECONDS:
        return None
    return beat


def progress(name: str) -> dict[str, Any]:
    """A pass's progress as the worker last told it (empty: doing nothing, or down)."""
    return dict(((heartbeat() or {}).get("progress") or {}).get(name) or {})


def pass_row(name: str) -> dict[str, Any]:
    """A pass's run as the worker last told it, for the Dashboard."""
    return dict(((heartbeat() or {}).get("passes") or {}).get(name) or {})


def status() -> dict[str, Any]:
    """Whether the worker runs these passes and is alive, for the Dashboard."""
    beat = heartbeat()
    return {"mode": "worker" if delegated() else "inline", "alive": beat is not None or not delegated(),
            "draining": bool((beat or {}).get("draining"))}


# -- the worker ------------------------------------------------------------------


def _code_stamp() -> float:
    """The newest change to bankai's own code: a deploy moves it."""
    root = Path(__file__).resolve().parents[1]
    newest = 0.0
    for directory, _dirs, files in os.walk(root):
        if "frontend" in directory or "__pycache__" in directory:
            continue
        for name in files:
            if name.endswith(".py"):
                try:
                    newest = max(newest, os.stat(os.path.join(directory, name)).st_mtime)
                except OSError:
                    continue
    return newest


def _inputs_stamp(name: str) -> tuple:
    stamps = []
    for file in _INPUTS[name]:
        try:
            stat = _path(file).stat()
            stamps.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append(None)
    return tuple(stamps)


def _progress_of(name: str) -> dict[str, Any]:
    from bankai.web import browser_audio, encode_library, track_cleanup

    module = {"encode": encode_library, "browser_audio": browser_audio, "track_clean": track_cleanup}[name]
    return dict(module.progress)


def _write_heartbeat(*, draining: bool = False, stopped: bool = False, waiting: bool = False) -> None:
    beat = {
        "pid": os.getpid(),
        "updated_at": time.time(),
        "draining": draining,
        # Waiting for bankai-web to leave the passes (it runs them itself).
        **({"waiting": True} if waiting else {}),
        "progress": {} if stopped else {name: _progress_of(name) for name in PASSES},
        "passes": {
            name: {key: value for key, value in row.items() if key != "task"} for name, row in _PASSES.items()
        },
        **({"stopped": True} if stopped else {}),
    }
    path = _path("library_worker.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(beat, default=str), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("library worker: heartbeat not written: %s", exc)


def _due(name: str) -> bool:
    """Is a pass to run now: switched on (encode: something queued)."""
    from bankai.web import browser_audio, encode_library, track_cleanup

    if name == "encode":
        return encode_library.has_work()
    if name == "browser_audio":
        return browser_audio.enabled()
    return track_cleanup.enabled()


def _interval(name: str) -> float:
    from bankai.web import jobs

    return _ENCODE_SECONDS if name == "encode" else jobs.interval(name)


def _run(name: str) -> Any:
    """One turn of a pass, in a thread: one file at most."""
    from bankai.web import browser_audio, encode_library, track_cleanup

    if name == "encode":
        return encode_library.encode_next()
    if name == "browser_audio":
        return browser_audio.convert_next() or {"idle": "Nothing to do now"}
    return track_cleanup.clean_next() or {"idle": "Nothing to take out now"}


def _recover() -> None:
    """What a stop cut off: the unfinished copies go, the steps done stay (as at the web's start)."""
    from bankai.web import browser_audio, encode_library, track_cleanup

    browser_audio.recover()
    track_cleanup.recover()
    encode_library.recover()


async def worker() -> int:
    """Run the passes until stopped (0), or until bankai's code changed and they are done (3:
    systemd starts the worker again on the new code)."""
    global _IS_WORKER
    _IS_WORKER = True
    _STOP.clear()
    started_code = await asyncio.to_thread(_code_stamp)
    tasks: dict[str, asyncio.Future] = {}
    draining = False
    waiting = True
    stopping = threading.Event()

    def beat() -> None:
        while not stopping.wait(_HEARTBEAT_SECONDS):
            _write_heartbeat(draining=draining, waiting=waiting)

    beater = threading.Thread(target=beat, name="library-worker-heartbeat", daemon=True)
    beater.start()
    try:
        if not await asyncio.to_thread(claim, wait=False):
            log.info("library worker: bankai-web runs the passes itself; waiting until it leaves them")
            if not await asyncio.to_thread(claim, wait=True):
                return 0
        waiting = False
        await asyncio.to_thread(_recover)
        next_at = {name: time.monotonic() + 5.0 for name in PASSES}
        seen = {name: _inputs_stamp(name) for name in PASSES}
        log.info("library worker: running %s", ", ".join(PASSES))
        while not _STOP.is_set():
            for name, task in list(tasks.items()):
                if task.done():
                    tasks.pop(name)
                    row = _PASSES.setdefault(name, {})
                    row["running"] = False
                    if task.exception() is not None:
                        row["error"] = f"{type(task.exception()).__name__}: {task.exception()}"
                        log.warning("library worker: %s failed: %r", name, task.exception())
                    else:
                        row["result"], row["error"] = task.result(), None
            if not draining and await asyncio.to_thread(_code_stamp) != started_code:
                draining = True
                log.info("library worker: bankai's code changed; no new file, restarting once %s",
                         "the running ones are done" if tasks else "now")
            if draining and not tasks:
                return 3
            now = time.monotonic()
            for name in PASSES:
                if draining or name in tasks:
                    continue
                stamp = _inputs_stamp(name)
                if stamp != seen[name]:
                    # Queued, switched on, a rule changed: due now, not at the next turn.
                    seen[name] = stamp
                    next_at[name] = now
                if now < next_at[name]:
                    continue
                next_at[name] = now + _interval(name)
                row = _PASSES.setdefault(name, {})
                row["due_at"] = time.time() + _interval(name)
                if not await asyncio.to_thread(_due, name):
                    continue
                row.update(running=True, last_run=time.time())
                tasks[name] = asyncio.ensure_future(asyncio.to_thread(_run, name))
            _write_heartbeat(draining=draining)
            await asyncio.sleep(_TURN_SECONDS)
        return 0
    finally:
        stopping.set()
        # Stopped (SIGTERM): the programs running got the signal too and end; their threads are
        # left to finish what they were doing, briefly, before the heartbeat says it is down.
        if tasks:
            await asyncio.wait(list(tasks.values()), timeout=20)
        _write_heartbeat(stopped=True)
        _release()


def _release() -> None:
    """Let the passes go (the process ends right after; a test carries on)."""
    global _CLAIM_FD, _IS_WORKER
    fd, _CLAIM_FD = _CLAIM_FD, None
    if fd is not None:
        os.close(fd)
    _IS_WORKER = False


def stop() -> None:
    _STOP.set()


__all__ = ["PASSES", "claim", "delegated", "heartbeat", "pass_row", "progress", "status", "stop", "worker"]
