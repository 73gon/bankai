"""Tests for scraper backends and the registry."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from bankai.queue.models import MediaKind
from bankai.scraper import all_backends, get_backend
from bankai.scraper.backends.filmpalast import FilmpalastBackend
from bankai.scraper.base import ScraperBackend

FIXTURES = Path(__file__).parent / "fixtures"


def test_registry_lists_all_four_backends() -> None:
    backends = all_backends()
    for site_id in ("filmpalast", "kinox", "bs.to", "aniworld"):
        assert site_id in backends
        cls = get_backend(site_id)
        # Protocol conformance is structural â€” check at least the key members.
        assert hasattr(cls, "search")
        assert hasattr(cls, "resolve_stream")


def test_filmpalast_class_satisfies_protocol() -> None:
    assert isinstance(FilmpalastBackend(base_url="http://example.invalid"), ScraperBackend)


@pytest.mark.asyncio
async def test_filmpalast_search_parses_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "filmpalast" / "search_inception.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/search/title/")
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    # Swap the underlying client for one using our mock transport.
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        results = await backend.search("Inception", kind=MediaKind.MOVIE)
    finally:
        await backend.aclose()

    assert len(results) == 3
    titles = [r.title for r in results]
    assert "Inception (2010)" in titles
    assert "Interstellar (2014)" in titles
    inception = results[0]
    assert inception.year == 2010
    assert inception.url == "http://example.invalid/stream/inception-2010"
    assert inception.poster_url == "http://example.invalid/media/cover/inception.jpg"
    assert inception.kind is MediaKind.MOVIE
    assert inception.site == "filmpalast"


@pytest.mark.asyncio
async def test_filmpalast_series_lookup_tries_direct_slug_first() -> None:
    html = """
    <html>
      <body>
        <a href="/stream/arcane-s01e01">Welcome to the Playground</a>
        <a href="/stream/arcane-s01e02">Some Mysteries</a>
      </body>
    </html>
    """
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/stream/arcane":
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        episodes = await backend.list_season("Arcane", 1)
    finally:
        await backend.aclose()

    assert requested_paths[0] == "/stream/arcane"
    assert [ep.episode for ep in episodes] == [1, 2]
    assert episodes[0].title == "Welcome to the Playground"


@pytest.mark.asyncio
async def test_filmpalast_series_lookup_keeps_episode_search_hits() -> None:
    search_html = """
    <article class="liste rb">
      <a class="rb" href="/stream/arcane-s01e01"><h2>Arcane S01E01</h2></a>
    </article>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stream/arcane":
            return httpx.Response(404)
        if request.url.path == "/stream/arcane-s01e01":
            return httpx.Response(200, text=search_html)
        if request.url.path.startswith("/search/title/"):
            return httpx.Response(200, text=search_html)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        episodes = await backend.list_season("Arcane", 1)
    finally:
        await backend.aclose()

    assert [(ep.season, ep.episode) for ep in episodes] == [(1, 1)]
    assert episodes[0].url == "http://example.invalid/stream/arcane-s01e01"


@pytest.mark.asyncio
async def test_filmpalast_search_falls_back_to_shorter_query() -> None:
    """Long, punctuated German titles return nothing from filmpalast's search;
    the backend should retry with a trimmed query (the Green Book case)."""
    result_html = """
    <article class="liste rb">
      <a class="rb" href="/stream/green-book-eine-besondere-freundschaft"><h2>Green Book - Eine besondere Freundschaft (2018)</h2></a>
    </article>
    """
    queried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/search/title/")
        q = request.url.path[len("/search/title/") :]
        queried.append(q)
        if q == "Green+Book":
            return httpx.Response(200, text=result_html)
        return httpx.Response(200, text="<html></html>")

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        results = await backend.search(
            "Green Book - Eine besondere Freundschaft", kind=MediaKind.MOVIE
        )
    finally:
        await backend.aclose()

    assert len(results) == 1
    assert "Green Book" in results[0].title
    assert "Green+Book" in queried


@pytest.mark.asyncio
async def test_filmpalast_resolve_returns_ytdlp_handle() -> None:
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    try:
        handle = await backend.resolve_stream("http://example.invalid/stream/x")
    finally:
        await backend.aclose()
    # Network call to example.invalid fails, so we fall back to playwright.
    assert handle.hint in ("ytdlp", "playwright")
    assert handle.site == "filmpalast"


@pytest.mark.asyncio
async def test_search_propagates_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        with pytest.raises(Exception, match="HTTP 500"):
            await backend.search("anything")
    finally:
        await backend.aclose()
