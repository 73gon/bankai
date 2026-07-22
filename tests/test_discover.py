from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest

from bankai.config import reset_settings_cache
from bankai.web import discover


@pytest.fixture(autouse=True)
def _configured_tvdb(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BANKAI_METADATA__TVDB_ENABLED", "true")
    monkeypatch.setenv("BANKAI_METADATA__TVDB_API_KEY", "test-key")
    reset_settings_cache()
    discover._CACHE.clear()
    discover._DETAIL_CACHE.clear()
    discover._BROWSE_META.clear()
    yield
    discover._CACHE.clear()
    discover._DETAIL_CACHE.clear()
    discover._BROWSE_META.clear()
    reset_settings_cache()


def _mock_tvdb(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        discover.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )


@pytest.mark.asyncio
async def test_title_details_prefers_explicit_worldwide_release_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token"}})
        if request.url.path == "/v4/movies/123/translations/deu":
            return httpx.Response(200, json={"data": {"name": "300"}})
        if request.url.path == "/v4/movies/123/extended":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "year": "2006",
                        "releases": [
                            {"country": "United States of America", "date": "2006-12-09", "detail": "Festival"},
                            {"country": "Worldwide", "date": "2007-03-09", "detail": "Theatrical"},
                        ],
                    }
                },
            )
        return httpx.Response(404)

    _mock_tvdb(monkeypatch, handler)

    details = await discover.title_details(123, kind="movie")

    assert details.german == "300"
    assert details.worldwide_release_date == "2007-03-09"
    assert details.worldwide_year == 2007


@pytest.mark.asyncio
async def test_studio_search_uses_tvdb_company_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token"}})
        if request.url.path == "/v4/search":
            assert request.url.params["type"] == "company"
            assert request.url.params["query"] == "Disney"
            return httpx.Response(
                200,
                json={"data": [{"tvdb_id": "123", "name": "Walt Disney Studios"}]},
            )
        assert request.url.path == "/v4/movies/filter"
        assert request.url.params["company"] == "123"
        assert request.url.params["sort"] == "score"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 789,
                        "name": "Inside Out",
                        "year": "2015",
                        "image": "https://artworks.thetvdb.com/posters/inside-out.jpg",
                    }
                ]
            },
        )

    _mock_tvdb(monkeypatch, handler)

    items = await discover.search("Disney", kind="movie", search_by="studio")

    assert [(item.name, item.tvdb_id, item.year) for item in items] == [("Inside Out", 789, 2015)]


@pytest.mark.asyncio
async def test_person_search_combines_cast_and_director_movies(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token"}})
        if request.url.path == "/v4/search":
            assert request.url.params["type"] == "person"
            assert request.url.params["query"] == "Anne Hathaway"
            return httpx.Response(
                200,
                json={"data": [{"tvdb_id": "456", "name": "Anne Hathaway"}]},
            )
        if request.url.path == "/v4/people/456/extended":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "characters": [
                            {
                                "movieId": 790,
                                "peopleType": "Actor",
                                "movie": {"name": "The Dark Knight Rises", "year": "2012"},
                            },
                            {
                                "movieId": 789,
                                "peopleType": "Actor",
                                "movie": {"name": "Interstellar", "year": "2014", "image": "/posters/interstellar.jpg"},
                            },
                        ]
                    }
                },
            )
        return httpx.Response(404)

    _mock_tvdb(monkeypatch, handler)

    items = await discover.search("Anne Hathaway", kind="movie", search_by="person")

    assert [item.name for item in items] == ["The Dark Knight Rises", "Interstellar"]
    assert items[1].tvdb_id == 789
    assert items[1].poster_url == "https://artworks.thetvdb.com/posters/interstellar.jpg"


@pytest.mark.asyncio
async def test_person_search_deduplicates_multiple_credits_for_one_movie(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token"}})
        if request.url.path == "/v4/search":
            return httpx.Response(
                200,
                json={"data": [{"tvdb_id": "200", "name": "Christopher Nolan"}]},
            )
        if request.url.path == "/v4/people/200/extended":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "characters": [
                            {"movieId": 100, "movie": {"name": "Oppenheimer", "year": "2023"}},
                            {"movieId": 100, "movie": {"name": "Oppenheimer", "year": "2023"}},
                            {"movieId": 101, "movie": {"name": "Tenet", "year": "2020"}},
                        ]
                    }
                },
            )
        return httpx.Response(404)

    _mock_tvdb(monkeypatch, handler)

    items = await discover.search("Christopher Nolan", kind="movie", search_by="person")

    assert [(item.tvdb_id, item.name) for item in items] == [(100, "Oppenheimer"), (101, "Tenet")]


@pytest.mark.asyncio
async def test_title_search_page_uses_fixed_50_row_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token"}})
        assert request.url.path == "/v4/search"
        assert request.url.params["offset"] == "100"
        assert request.url.params["limit"] == "50"
        return httpx.Response(
            200,
            json={
                "data": [{"id": 101, "name": "Result 101", "year": "2024"}],
                "links": {"total_items": 10_000},
            },
        )

    _mock_tvdb(monkeypatch, handler)

    result = await discover.search_page("Result", kind="movie", page=2)

    assert result.page_size == 50
    assert [item.name for item in result.items] == ["Result 101"]
    assert result.total == 10_000
    assert result.has_next is True


@pytest.mark.asyncio
async def test_browse_page_translates_fixed_ui_page_to_provider_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def records(start: int, count: int) -> list[dict[str, object]]:
        return [{"id": i, "name": f"Movie {i}", "year": "2024"} for i in range(start, start + count)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token"}})
        assert request.url.path == "/v4/movies"
        provider_page = int(request.url.params["page"])
        start = provider_page * 500
        return httpx.Response(
            200,
            json={
                "data": records(start, 500),
                "links": {"page_size": 500, "total_items": 12_345},
            },
        )

    _mock_tvdb(monkeypatch, handler)

    result = await discover.browse_page("movie", page=11)

    assert result.page_size == 50
    assert result.total == 12_345
    assert result.has_next is True
    assert [item.tvdb_id for item in result.items] == list(range(550, 600))


@pytest.mark.asyncio
async def test_browse_page_honors_adjustable_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token"}})
        return httpx.Response(
            200,
            json={
                "data": [{"id": i, "name": f"Movie {i}", "year": "2024"} for i in range(500)],
                "links": {"page_size": 500, "total_items": 10_000},
            },
        )

    _mock_tvdb(monkeypatch, handler)

    result = await discover.browse_page("movie", page=2, page_size=100)

    assert result.page_size == 100
    assert [item.tvdb_id for item in result.items] == list(range(200, 300))
