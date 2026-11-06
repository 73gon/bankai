"""Discover and Search on TMDB: titles, people, studios, pages, German names."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest

from bankai.config import reset_settings_cache
from bankai.metadata import tmdb_titles
from bankai.web import discover


@pytest.fixture(autouse=True)
def _configured_tmdb(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BANKAI_METADATA__TMDB_API_KEY", "test-key")
    reset_settings_cache()
    for cache in (discover._CACHE, discover._PERSON_CACHE, discover._DETAIL_CACHE, discover._TOTALS):
        cache.clear()
    yield
    for cache in (discover._CACHE, discover._PERSON_CACHE, discover._DETAIL_CACHE, discover._TOTALS):
        cache.clear()
    reset_settings_cache()


def _mock_tmdb(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tmdb_titles.httpx, "AsyncClient", lambda **kwargs: async_client(transport=transport, **kwargs))


def _page(results: list[dict], total: int | None = None) -> httpx.Response:
    return httpx.Response(200, json={"results": results, "total_results": len(results) if total is None else total})


@pytest.mark.asyncio
async def test_a_title_search_reads_tmdb_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/search/tv"
        assert request.url.params["query"] == "Modern Family"
        assert request.url.params["api_key"] == "test-key"
        return _page([{"id": 1421, "name": "Modern Family", "first_air_date": "2009-09-23", "poster_path": "/p.jpg"}])

    _mock_tmdb(monkeypatch, handler)
    items = await discover.search("Modern Family", kind="show", limit=10)
    assert items == [
        discover.DiscoverItem(
            name="Modern Family", kind="show", tmdb_id=1421, year=2009,
            poster_url="https://image.tmdb.org/t/p/w342/p.jpg", release_date="2009-09-23",
        )
    ]


@pytest.mark.asyncio
async def test_a_search_page_of_50_joins_tmdb_pages_of_20(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["page"])
        asked.append(number)
        start = (number - 1) * 20
        return _page([{"id": i, "title": f"Movie {i}"} for i in range(start, start + 20)], total=1000)

    _mock_tmdb(monkeypatch, handler)
    result = await discover.search_page("Movie", kind="movie", page=2)
    # Rows 100..149 are TMDB pages 6, 7 and 8.
    assert sorted(asked) == [6, 7, 8]
    assert [item.tmdb_id for item in result.items] == list(range(100, 150))
    assert result.total == 1000
    assert result.has_next is True


@pytest.mark.asyncio
async def test_browse_stops_at_tmdbs_last_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/discover/movie"
        assert request.url.params["sort_by"] == "popularity.desc"
        number = int(request.url.params["page"])
        return _page([{"id": number * 100 + i, "title": "x"} for i in range(20)], total=900_000)

    _mock_tmdb(monkeypatch, handler)
    result = await discover.browse_page("movie", page=0, page_size=50)
    assert len(result.items) == 50
    assert result.total == 10_000


@pytest.mark.asyncio
async def test_person_search_combines_cast_and_directing_most_popular_first(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/person":
            return _page([{"id": 7, "name": "Christopher Nolan"}, {"id": 8, "name": "Christopher Nolan Jr"}])
        assert request.url.path == "/3/person/7/movie_credits"
        return httpx.Response(200, json={
            "cast": [{"id": 1, "title": "Cameo", "popularity": 1}],
            "crew": [
                {"id": 2, "title": "Oppenheimer", "job": "Director", "popularity": 90},
                {"id": 2, "title": "Oppenheimer", "job": "Writer", "popularity": 90},
                {"id": 3, "title": "Catering", "job": "Caterer", "popularity": 99},
            ],
        })

    _mock_tmdb(monkeypatch, handler)
    items = await discover.search("Christopher Nolan", kind="movie", search_by="person", limit=10)
    assert [(item.tmdb_id, item.name) for item in items] == [(2, "Oppenheimer"), (1, "Cameo")]


@pytest.mark.asyncio
async def test_studio_search_asks_for_the_films_of_the_named_companies(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/company":
            return _page([{"id": 9, "name": "Pixar Something"}, {"id": 3, "name": "Pixar"}])
        assert request.url.path == "/3/discover/movie"
        assert request.url.params["with_companies"] == "3|9"
        return _page([{"id": 12, "title": "Finding Nemo"}])

    _mock_tmdb(monkeypatch, handler)
    items = await discover.search("Pixar", kind="movie", search_by="studio", limit=10)
    assert [item.name for item in items] == ["Finding Nemo"]


@pytest.mark.asyncio
async def test_title_details_give_the_german_name_germanys_first(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/movie/5"
        return httpx.Response(200, json={
            "id": 5, "title": "The Platform", "release_date": "2019-11-08",
            "translations": {"translations": [
                {"iso_639_1": "de", "iso_3166_1": "AT", "data": {"title": "Der Schacht (AT)"}},
                {"iso_639_1": "de", "iso_3166_1": "DE", "data": {"title": "Der Schacht"}},
            ]},
        })

    _mock_tmdb(monkeypatch, handler)
    details = await discover.title_details(5, kind="movie")
    assert (details.english, details.german, details.worldwide_year) == ("The Platform", "Der Schacht", 2019)


@pytest.mark.asyncio
async def test_person_suggestions_drop_repeated_names(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _page([{"id": 1, "name": "Chris Evans"}, {"id": 2, "name": "chris  evans"}, {"id": 3, "name": "Chris Pratt"}])

    _mock_tmdb(monkeypatch, handler)
    names = [row.name for row in await discover.person_suggestions("Chris")]
    assert names == ["Chris Evans", "Chris Pratt"]


@pytest.mark.asyncio
async def test_a_show_comes_with_every_season_in_appended_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        append = request.url.params.get("append_to_response", "")
        if append.startswith("translations"):
            return httpx.Response(200, json={
                "id": 1, "name": "Show", "first_air_date": "2009-09-23", "status": "Ended",
                "external_ids": {"tvdb_id": 95011},
                "seasons": [{"season_number": n, "episode_count": 2} for n in range(0, 23)],
            })
        numbers = [int(part.split("/")[1]) for part in append.split(",")]
        assert len(numbers) <= 20
        return httpx.Response(200, json={
            f"season/{n}": {"episodes": [{"episode_number": e, "name": f"S{n}E{e}", "air_date": "2010-01-01"} for e in (1, 2)]}
            for n in numbers
        })

    _mock_tmdb(monkeypatch, handler)
    show = await tmdb_titles.show(1)
    assert show is not None
    assert len(show["episodes"]) == 46
    assert show["episodes"][-1] == tmdb_titles.Episode(season=22, episode=2, name="S22E2", aired="2010-01-01")
    assert (show["status"], show["tvdb_id"], show["year"]) == ("Ended", 95011, 2009)


def test_a_search_of_all_kinds_takes_each_rows_own_kind_and_leaves_people_out():
    from bankai.web import discover

    rows = [
        {"id": 1, "media_type": "movie", "title": "Avatar", "release_date": "2009-12-15"},
        {"id": 2, "media_type": "tv", "name": "Avatar: The Last Airbender", "first_air_date": "2005-02-21"},
        {"id": 3, "media_type": "person", "name": "James Cameron"},
    ]
    items = discover._items(rows, "all")
    assert [(item.tmdb_id, item.kind) for item in items] == [(1, "movie"), (2, "show")]
    assert discover._search_path("all") == "search/multi"
    assert discover._search_path("show") == "search/tv"
