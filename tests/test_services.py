"""Tests for cross-backend source search orchestration."""

from __future__ import annotations

import pytest

from bankai import scraper as scraper_registry
from bankai.backend.services import _series_sites, search_stream_sources
from bankai.queue.models import MediaKind
from bankai.scraper.base import SearchResult


@pytest.mark.asyncio
async def test_show_search_combines_filmpalast_and_burningseries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, MediaKind | None]] = []

    class FakeFilmpalast:
        supports_movies = True
        supports_series = True

        async def search(self, query: str, *, kind: MediaKind | None, limit: int) -> list[SearchResult]:
            calls.append(("filmpalast", kind))
            return [
                SearchResult(
                    site="filmpalast",
                    title=query,
                    url="https://filmpalast.invalid/stream/arcane",
                    kind=kind or MediaKind.MOVIE,
                )
            ]

        async def aclose(self) -> None:
            return None

    class FakeBurningSeries:
        supports_movies = False
        supports_series = True

        async def search(self, query: str, *, kind: MediaKind | None, limit: int) -> list[SearchResult]:
            calls.append(("burningseries", kind))
            hit = SearchResult(
                site="burningseries",
                title=query,
                url="https://burningseries.invalid/serie/Arcane",
                kind=MediaKind.EPISODE,
            )
            return [hit, hit]

        async def aclose(self) -> None:
            return None

    class LegacyAlias:
        supports_movies = False
        supports_series = False

        def __init__(self) -> None:
            raise AssertionError("legacy alias must not be auto-searched")

    monkeypatch.setattr(
        scraper_registry,
        "all_backends",
        lambda: {
            "burningseries": FakeBurningSeries,
            "filmpalast": FakeFilmpalast,
            "bs.to": LegacyAlias,
        },
    )

    show_results = await search_stream_sources(
        "Arcane",
        site=None,
        limit=10,
        kind=MediaKind.EPISODE,
    )
    assert [result.site for result in show_results] == ["filmpalast", "burningseries"]
    assert calls == [
        ("burningseries", MediaKind.EPISODE),
        ("filmpalast", MediaKind.EPISODE),
    ]

    calls.clear()
    movie_results = await search_stream_sources(
        "Arcane",
        site=None,
        limit=10,
        kind=MediaKind.MOVIE,
    )
    assert [result.site for result in movie_results] == ["filmpalast"]
    assert calls == [("filmpalast", MediaKind.MOVIE)]


def test_series_site_fallback_prefers_burningseries_after_filmpalast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SeriesBackend:
        supports_series = True

    monkeypatch.setattr(
        scraper_registry,
        "all_backends",
        lambda: {
            "burningseries": SeriesBackend,
            "aniworld": SeriesBackend,
            "filmpalast": SeriesBackend,
        },
    )

    assert _series_sites() == ["filmpalast", "burningseries", "aniworld"]
