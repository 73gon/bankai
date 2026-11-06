"""A lock for state files two bankai processes write: the web UI and the library worker.

The passes that rewrite library files (encode, browser audio, track cleanup) run in the worker
(`bankai library worker`, see web.library_worker) while the web UI changes the same queues and
records -- a file queued, a rule changed, a refresh asked of Jellyfin. Each module's lock was a
thread lock; this one is that and an ``flock`` on ``<state>/<name>.lock``, so a change made in one
process is never lost to the other's read-modify-write. Used as ``with _LOCK:``, not re-entrant.
On Windows (no fcntl) it is the thread lock alone.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from bankai.cli import bgjobs

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]


class StateLock:
    def __init__(self, name: str) -> None:
        self._name = name
        self._thread = threading.Lock()
        self._fd: int | None = None

    def __enter__(self) -> StateLock:
        self._thread.acquire()
        if fcntl is None:
            return self
        try:
            path = bgjobs.jobs_root().parent / f"{self._name}.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd
        except BaseException:
            self._thread.release()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        fd, self._fd = self._fd, None
        try:
            if fd is not None and fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        finally:
            self._thread.release()


__all__ = ["StateLock"]
