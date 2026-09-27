"""Gzip for the API's JSON, and nothing else.

Starlette's GZipMiddleware compresses every response type, media included:
audio and video clips, ranged file responses and streams, where it costs CPU
for nothing and can break seeking. This compresses only what benefits -- a
JSON body sent in one piece -- and passes everything else through as it is.
The ready answers from ``snapshots`` arrive compressed already and are left
alone too.
"""

from __future__ import annotations

import asyncio
import gzip
from typing import Any

_MIN_BYTES = 1024
# Past this, compress off the event loop: 1 MB takes ~10 ms.
_THREAD_BYTES = 256 * 1024


class GzipJson:
    def __init__(self, app: Any, *, level: int = 5) -> None:
        self.app = app
        self.level = level

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        accepts = b""
        for name, value in scope.get("headers") or ():
            if name == b"accept-encoding":
                accepts = value
        if b"gzip" not in accepts:
            await self.app(scope, receive, send)
            return

        start: dict | None = None
        passing = False

        async def compressing(message: dict) -> None:
            nonlocal start, passing
            if message["type"] == "http.response.start":
                start = message
                return  # held until the body shows whether to compress
            if message["type"] != "http.response.body" or passing or start is None:
                await send(message)
                return
            body = message.get("body", b"")
            headers = list(start.get("headers") or [])
            names = {name.lower(): value for name, value in headers}
            if (
                message.get("more_body", False)  # a stream: leave it be
                or b"content-encoding" in names
                or not names.get(b"content-type", b"").startswith(b"application/json")
                or len(body) < _MIN_BYTES
            ):
                passing = True
                await send(start)
                await send(message)
                return
            if len(body) >= _THREAD_BYTES:
                packed = await asyncio.to_thread(gzip.compress, body, self.level, mtime=0)
            else:
                packed = gzip.compress(body, self.level, mtime=0)
            headers = [
                (name, value) for name, value in headers if name.lower() != b"content-length"
            ]
            headers += [
                (b"content-encoding", b"gzip"),
                (b"content-length", str(len(packed)).encode()),
                (b"vary", b"Accept-Encoding"),
            ]
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": packed})

        await self.app(scope, receive, compressing)
