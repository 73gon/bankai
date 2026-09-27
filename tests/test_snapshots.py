"""Tab answers kept ready: freshness rules, invalidation and compression."""

from __future__ import annotations

import asyncio
import gzip
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from bankai.web import snapshots
from bankai.web.compression import GzipJson


class _Counter:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def __call__(self) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return {"n": self.calls}


def test_an_answer_is_built_once_and_then_served():
    async def run() -> None:
        build = _Counter()
        snap = snapshots.Snapshot("t", build, max_age=60)
        assert (await snap.get()).value == {"n": 1}
        assert (await snap.get()).value == {"n": 1}
        assert build.calls == 1
        assert json.loads(snap.body) == {"n": 1}

    asyncio.run(run())


def test_after_invalidation_the_next_request_waits_for_a_new_answer():
    async def run() -> None:
        build = _Counter()
        snap = snapshots.Snapshot("t", build, max_age=60)
        await snap.get()
        snap.invalidate()
        assert (await snap.get()).value == {"n": 2}

    asyncio.run(run())


def test_a_changed_input_is_rebuilt_in_the_background():
    async def run() -> None:
        build = _Counter()
        stamp = {"v": 1}
        snap = snapshots.Snapshot("t", build, inputs=lambda: stamp["v"], min_interval=0, max_age=60)
        await snap.get()
        stamp["v"] = 2
        # Served at once from what is held, while the new one is built.
        assert (await snap.get()).value == {"n": 1}
        await snap._task
        assert (await snap.get()).value == {"n": 2}

    asyncio.run(run())


def test_a_failed_rebuild_keeps_the_last_answer_until_max_stale():
    async def run() -> None:
        build = _Counter()
        snap = snapshots.Snapshot("t", build, max_age=60, max_stale=30)
        await snap.get()
        build.fail = True
        snap.invalidate()
        assert (await snap.get()).value == {"n": 1}
        snap.built_at -= 31
        try:
            await snap.get()
        except RuntimeError as exc:
            assert "provider down" in str(exc)
        else:
            raise AssertionError("a stale answer past max_stale was served")

    asyncio.run(run())


def test_a_first_build_that_fails_raises():
    async def run() -> None:
        build = _Counter()
        build.fail = True
        snap = snapshots.Snapshot("t", build)
        try:
            await snap.get()
        except RuntimeError:
            return
        raise AssertionError("expected the build's error")

    asyncio.run(run())


def _app() -> tuple[FastAPI, _Counter]:
    app = FastAPI()
    registry = snapshots.Registry()
    build = _Counter()
    registry.add("anime", build, tags={"anime"}, max_age=60)
    app.add_middleware(snapshots.InvalidateOnWrite, registry=registry)
    app.add_middleware(GzipJson)

    @app.get("/api/anime/thing", response_model=None)
    async def thing(request: Request):
        return snapshots.response(await registry["anime"].get(), request)

    @app.post("/api/anime/change")
    async def change() -> dict:
        return {"ok": True}

    @app.post("/api/anime/refuse")
    async def refuse():
        return JSONResponse({"detail": "no"}, status_code=422)

    @app.post("/api/qbittorrent/other")
    async def other() -> dict:
        return {"ok": True}

    @app.get("/api/big")
    async def big() -> dict:
        return {"items": ["x" * 50] * 100}

    @app.get("/api/stream")
    async def stream():
        async def chunks():
            yield b'{"a":'
            yield b"1}"

        return StreamingResponse(chunks(), media_type="application/json")

    return app, build


def test_a_successful_write_invalidates_what_it_touches():
    app, _ = _app()
    with TestClient(app) as client:
        assert client.get("/api/anime/thing").json() == {"n": 1}
        client.post("/api/anime/refuse")  # failed: nothing changed
        assert client.get("/api/anime/thing").json() == {"n": 1}
        client.post("/api/qbittorrent/other")  # "qbit" also touches "anime"
        assert client.get("/api/anime/thing").json() == {"n": 2}
        client.post("/api/anime/change")
        assert client.get("/api/anime/thing").json() == {"n": 3}


def test_the_page_already_holding_the_answer_gets_a_304():
    app, _ = _app()
    with TestClient(app) as client:
        first = client.get("/api/anime/thing")
        again = client.get("/api/anime/thing", headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304


def test_json_is_gzipped_and_streams_are_left_alone():
    app, _ = _app()
    with TestClient(app) as client:
        big = client.get("/api/big", headers={"Accept-Encoding": "gzip"})
        assert big.headers["content-encoding"] == "gzip"
        assert big.json()["items"][0] == "x" * 50
        stream = client.get("/api/stream", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in stream.headers
        assert stream.json() == {"a": 1}
        plain = client.get("/api/big", headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in plain.headers


def test_a_snapshot_answer_is_not_compressed_twice():
    app, _ = _app()
    with TestClient(app) as client:
        response = client.get("/api/anime/thing", headers={"Accept-Encoding": "gzip"})
        assert response.json() == {"n": 1}  # small: sent plain, and decodes either way


def test_gzip_bodies_decode_to_the_original():
    body = json.dumps({"items": list(range(500))}).encode()
    encoded = snapshots._encode({"items": list(range(500))})
    assert encoded[0] == body.replace(b" ", b"")
    assert gzip.decompress(encoded[1]) == encoded[0]
