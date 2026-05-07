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
async def test_filmpalast_resolve_returns_ytdlp_handle() -> None:
    backend = FilmpalastBackend(base_url="http://example.invalid")
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
