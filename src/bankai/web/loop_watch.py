"""Says where the event loop is stuck: a thread that, when the loop has not ticked for a while, writes
every thread's stack to the log.

bankai-web froze for minutes at a time while qBittorrent wrote 145 GB onto G: (Nov 2026): one read on
that drive, made on the loop itself, took up to 105 s, and every request waited behind it. py-spy needs
root; this needs nothing, and names the line.
"""

from __future__ import annotations

import asyncio
import faulthandler
import sys
import threading
import time

from bankai.logging import get_logger

log = get_logger(__name__)

STALL_SECONDS = 20.0
_beat = [time.monotonic()]
_tasks: list[asyncio.Task] = []  # the loop holds its tasks weakly


async def _tick() -> None:
    while True:
        _beat[0] = time.monotonic()
        await asyncio.sleep(1.0)


def _watch() -> None:
    told = 0.0
    while True:
        time.sleep(2.0)
        stalled = time.monotonic() - _beat[0]
        # Once per stall (again after a minute of it), not every two seconds.
        if stalled > STALL_SECONDS and time.monotonic() - told > 60.0:
            told = time.monotonic()
            log.warning("The event loop has not run for %.0f s; every thread's stack follows", stalled)
            sys.stderr.flush()
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            sys.stderr.flush()


def start() -> asyncio.Task:
    """Begin watching the running loop; the task returned is its heartbeat."""
    threading.Thread(target=_watch, name="loop-watch", daemon=True).start()
    task = asyncio.get_running_loop().create_task(_tick())
    _tasks.append(task)
    return task
