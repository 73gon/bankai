"""The process is stopping: long passes running in worker threads end at their next file.

Python waits for every worker thread before the process exits, so a restart of bankai-web
waited out the file an episode check or the track inventory was reading -- over 9p, long enough
for systemd to kill it after 90 s (Nov 2026). Set once, as the app shuts down.
"""

from __future__ import annotations

import threading

_EVENT = threading.Event()


class Stopped(Exception):
    """Raised inside a pass's work when the process is stopping; nothing of it is recorded."""


def stop() -> None:
    _EVENT.set()


def reset() -> None:
    """At startup: an app started again in the same process (tests) runs its passes again."""
    _EVENT.clear()


def requested() -> bool:
    return _EVENT.is_set()
