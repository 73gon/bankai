from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from bankai.queue.models import MediaKind
from bankai.scraper.base import SearchResult, StreamHandle
from bankai.web import availability


@pytest.fixture(autouse=True)
def clear_availability_cache() -> None:
    availability._CACHE.clear()
    availability._INFLIGHT.clear()


class _FakeFilmpalast:
    results: ClassVar[list[SearchResult]] = []
    mirrors: ClassVar[dict[str, list[StreamHandle]]] = {}

    async def search(
        self,
        query: str,
        *,
        kind: MediaKind | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        return self.results

    async def resolve_live_streams(self, url: str) -> list[StreamHandle]:
        return self.mirrors.get(url, [])

    async def aclose(self) -> None:
        return None


def _result(title: str, year: int | None, url: str) -> SearchResult:
    return SearchResult(
        site="filmpalast",
        title=title,
        year=year,
        url=url,
        kind=MediaKind.MOVIE,
    )


def _mirror(url: str = "https://voe.sx/working") -> StreamHandle:
    return StreamHandle(site="filmpalast", url=url, hint="playwright")


def test_availability_title_match_accepts_subtitle_but_not_leading_extra_word() -> None:
    assert availability._matches("Maleficent", "Maleficent - Die dunkle Fee")
    assert not availability._matches("Up", "Step Up")
    assert not availability._matches("Up", "Up in the Air")


def test_availability_requires_supported_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://filmpalast.to/stream/a-star-is-born"
    _FakeFilmpalast.results = [_result("A Star Is Born (2018)", 2018, url)]
    _FakeFilmpalast.mirrors = {url: [_mirror()]}
    monkeypatch.setattr(availability, "FilmpalastBackend", _FakeFilmpalast)

    asyncio.run(availability._check("A Star Is Born", year=2018))

    status = availability.get_status("A Star Is Born", year=2018)
    assert status is not None
    assert status["status"] == "available"
    assert status["url"] == url
    assert status["mirrors"] == 1


def test_availability_rejects_page_without_supported_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://filmpalast.to/stream/a-star-is-born"
    _FakeFilmpalast.results = [_result("A Star Is Born (2018)", 2018, url)]
    _FakeFilmpalast.mirrors = {url: []}
    monkeypatch.setattr(availability, "FilmpalastBackend", _FakeFilmpalast)

    asyncio.run(availability._check("A Star Is Born", year=2018))

    assert availability.get_status("A Star Is Born", year=2018)["status"] == "unavailable"


def test_availability_does_not_certify_wrong_release_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://filmpalast.to/stream/carrie"
    _FakeFilmpalast.results = [_result("Carrie (1976)", 1976, url)]
    _FakeFilmpalast.mirrors = {url: [_mirror()]}
    monkeypatch.setattr(availability, "FilmpalastBackend", _FakeFilmpalast)

    asyncio.run(availability._check("Carrie", year=2013))

    assert availability.get_status("Carrie", year=2013)["status"] == "unavailable"
