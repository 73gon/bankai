"""Each page's answer, kept ready rather than worked out while the page waits.

Building a tab's data is seconds of work -- the anime library reads a 20 MB
release state, the file tree, TVDB rosters and codecs, then merges every
show -- and it was done afresh for every request. A ``Snapshot`` holds the
last answer, already encoded and compressed, and hands it back at once.

Freshness, from strictest to loosest:

* After a change made through the API (any successful non-GET request), the
  snapshots it can affect are invalidated: the next request waits for a new
  answer rather than showing the one from before the change. See
  ``InvalidateOnWrite``.
* When an input changes -- a state file written by the automation worker, the
  library tree -- the old answer is served while a new one is built in the
  background, no more often than ``min_interval``.
* Nothing older than ``max_age`` is served without a rebuild being started.

Snapshots someone looked at recently are also kept fresh in the background by
``Registry.refresher``, so the page finds them current when it next polls.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Hashable, Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

from bankai.logging import get_logger

log = get_logger(__name__)

# Looked at within this long: kept fresh in the background. Anything else is
# rebuilt when next asked for, and shown stale until then.
HOT_SECONDS = 600.0
_GZIP_MIN_BYTES = 1024


def _encode(value: Any) -> tuple[bytes, bytes | None, str]:
    from fastapi.encoders import jsonable_encoder

    body = json.dumps(
        jsonable_encoder(value), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    gzipped = (
        gzip.compress(body, compresslevel=5, mtime=0) if len(body) >= _GZIP_MIN_BYTES else None
    )
    etag = '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'
    return body, gzipped, etag


class Snapshot:
    def __init__(
        self,
        name: str,
        build: Callable[[], Awaitable[Any]],
        *,
        inputs: Callable[[], Hashable] | None = None,
        tags: Iterable[str] = (),
        min_interval: float = 5.0,
        max_age: float = 300.0,
        max_stale: float | None = None,
        encode: bool = True,
    ) -> None:
        self.name = name
        self._build = build
        self._inputs = inputs
        self.tags = frozenset(tags)
        self.min_interval = min_interval
        self.max_age = max_age
        # A failing rebuild keeps the last answer on show, but not forever:
        # past this age the failure is raised instead (qBittorrent down).
        self.max_stale = max_stale
        self._encode = encode
        self.value: Any = None
        self.body: bytes | None = None
        self.gzipped: bytes | None = None
        self.etag = ""
        self.built_at = 0.0
        self.signature: Hashable = None
        self.asked_at = 0.0
        self.error: BaseException | None = None
        self.took = 0.0
        self._wanted = 0  # bumped by invalidate()
        self._built = -1  # the invalidation generation the value reflects
        self._task: asyncio.Task | None = None

    # -- freshness -----------------------------------------------------------

    def _signature(self) -> Hashable:
        if self._inputs is None:
            return None
        try:
            return self._inputs()
        except Exception as exc:  # an input that cannot be read counts as changed
            return ("unreadable", repr(exc), time.monotonic())

    def stale(self, now: float | None = None) -> bool:
        if self.value is None or self._built < self._wanted:
            return True
        age = (time.monotonic() if now is None else now) - self.built_at
        if age >= self.max_age:
            return True
        return age >= self.min_interval and self._signature() != self.signature

    def invalidate(self) -> None:
        """Make the next request wait for an answer built after this moment."""
        self._wanted += 1
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # called from a thread: the next request starts the rebuild
        if self.value is not None:
            self._start()  # begin now, so that wait is short

    # -- building ------------------------------------------------------------

    def _start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._rebuild(), name=f"snapshot:{self.name}")
        return self._task

    async def _rebuild(self) -> bool:
        generation = self._wanted
        # Read before building: a change that lands during the build then
        # shows as a new signature on the next look, rather than being missed.
        signature = await asyncio.to_thread(self._signature)
        started = time.monotonic()
        try:
            value = await self._build()
            encoded = await asyncio.to_thread(_encode, value) if self._encode else None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
            log.warning("could not build the %s snapshot: %s", self.name, exc)
            return False
        if encoded is not None:
            self.body, self.gzipped, self.etag = encoded
        self.value = value
        self.signature = signature
        self.built_at = started
        self.error = None
        self._built = max(self._built, generation)
        self.took = time.monotonic() - started
        if self.took > 2.0:
            log.info("built the %s snapshot in %.1f s", self.name, self.took)
        return True

    async def get(self) -> Snapshot:
        """This snapshot, current enough to serve; builds it if it must."""
        self.asked_at = time.monotonic()
        while self.value is None or self._built < self._wanted:
            task = self._start()
            ok = await asyncio.shield(task)
            if not ok:
                if self.value is None and self.error is not None:
                    raise self.error
                break  # an invalidated answer that cannot be rebuilt is still an answer
        if self.stale():
            self._start()
        if (
            self.error is not None
            and self.max_stale is not None
            and time.monotonic() - self.built_at > self.max_stale
        ):
            raise self.error
        return self


class Registry:
    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}

    def add(self, name: str, build: Callable[[], Awaitable[Any]], **options: Any) -> Snapshot:
        snapshot = Snapshot(name, build, **options)
        self._snapshots[name] = snapshot
        return snapshot

    def __getitem__(self, name: str) -> Snapshot:
        return self._snapshots[name]

    def __iter__(self):
        return iter(list(self._snapshots.values()))

    def invalidate(self, tags: Iterable[str] | None = None) -> None:
        wanted = None if tags is None else frozenset(tags)
        for snapshot in self:
            if wanted is None or snapshot.tags & wanted:
                snapshot.invalidate()

    async def warm(self) -> None:
        """Build everything once, one at a time, so the first visits are quick."""
        for snapshot in self:
            with suppress(Exception):
                await snapshot.get()
            snapshot.asked_at = 0.0  # warming is not someone looking

    async def refresher(self, interval: float = 3.0) -> None:
        """Keep the snapshots in use current, one rebuild at a time."""
        while True:
            now = time.monotonic()
            for snapshot in self:
                if (
                    snapshot.value is not None
                    and now - snapshot.asked_at < HOT_SECONDS
                    and snapshot.stale(now)
                ):
                    with suppress(Exception):
                        await asyncio.shield(snapshot._start())
            await asyncio.sleep(interval)


def file_stamps(paths: Callable[[], Iterable[str | Path]]) -> Callable[[], tuple]:
    """Inputs read from files: each one's modification time and size."""

    def stamps() -> tuple:
        out = []
        for path in paths():
            try:
                stat = Path(path).stat()
                out.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                out.append((str(path), None, None))
        return tuple(out)

    return stamps


def response(snapshot: Snapshot, request: Any) -> Any:
    """The snapshot as an HTTP response: 304 when the page has it, gzip when it can."""
    from fastapi.responses import Response

    headers = {"ETag": snapshot.etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
    if snapshot.etag and request.headers.get("if-none-match") == snapshot.etag:
        return Response(status_code=304, headers=headers)
    if snapshot.gzipped is not None and "gzip" in request.headers.get("accept-encoding", ""):
        return Response(
            snapshot.gzipped,
            media_type="application/json",
            headers={**headers, "Content-Encoding": "gzip"},
        )
    return Response(snapshot.body or b"null", media_type="application/json", headers=headers)


# -- invalidation ----------------------------------------------------------

# Which snapshots a write under a path can change. Anything not listed can
# change anything.
_WRITE_TAGS: tuple[tuple[str, frozenset[str]], ...] = (
    # A review decision changes what is held, not the library: rebuilding the
    # library grid for it cost seconds of CPU after every Discard. Deleted
    # files reach the library through its own inputs (the library walk).
    ("/api/anime/review", frozenset({"review"})),
    ("/api/anime/blacklist", frozenset({"review"})),
    ("/api/anime/", frozenset({"anime"})),
    ("/api/qbittorrent/", frozenset({"qbit", "anime"})),
    ("/api/mas/", frozenset({"mas"})),
    ("/api/server/", frozenset({"mas", "movies", "anime"})),
    ("/api/queue", frozenset({"movies"})),
    ("/api/jobs", frozenset({"movies", "anime"})),
    ("/api/review", frozenset({"movies"})),
    ("/api/library", frozenset({"movies"})),
    ("/api/titles", frozenset({"movies"})),
)
_READS = {"GET", "HEAD", "OPTIONS"}


def write_tags(path: str) -> frozenset[str] | None:
    for prefix, tags in _WRITE_TAGS:
        if path.startswith(prefix):
            return tags
    return None


class InvalidateOnWrite:
    """ASGI middleware: a successful change through the API invalidates what it touches.

    Done just before the last byte of the response goes out, so the page's
    follow-up load -- which it makes on receiving the response -- always
    finds the snapshot invalidated and waits for the new answer.
    """

    def __init__(self, app: Any, registry: Registry) -> None:
        self.app = app
        self.registry = registry

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method", "GET") in _READS
            or not scope.get("path", "").startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        status = 500
        path = scope["path"]

        async def watched(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message.get("status", 500))
            elif (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and status < 400
            ):
                self.registry.invalidate(write_tags(path))
            await send(message)

        await self.app(scope, receive, watched)
