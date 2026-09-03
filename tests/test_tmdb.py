from __future__ import annotations

import httpx
import pytest

from bankai.metadata.tmdb import TMDBClient


@pytest.mark.asyncio
async def test_tmdb_search_merges_english_and_japanese_titles() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "api-key"
        language = request.url.params["language"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 209867,
                        "name": (
                            "Frieren: Beyond Journey's End"
                            if language == "en-US"
                            else "葬送のフリーレン"
                        ),
                        "original_name": "葬送のフリーレン",
                        "original_language": "ja",
                        "first_air_date": "2023-09-29",
                        "poster_path": "/frieren.jpg",
                        "popularity": 100,
                    }
                ]
            },
        )

    client = TMDBClient(api_key="api-key", transport=httpx.MockTransport(handler))
    try:
        (match,) = await client.search_titles("Frieren", kind="show")
    finally:
        await client.aclose()

    assert match.tmdb_id == 209867
    assert match.kind == "show"
    assert match.english_title == "Frieren: Beyond Journey's End"
    assert match.japanese_title == "葬送のフリーレン"
    assert match.year == 2023
    assert match.poster_url == "https://image.tmdb.org/t/p/w342/frieren.jpg"


@pytest.mark.asyncio
async def test_tmdb_episode_map_uses_provider_seasons_and_absolute_order() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/tv/209867":
            return httpx.Response(
                200,
                json={"seasons": [{"season_number": 0}, {"season_number": 1}, {"season_number": 2}]},
            )
        if request.url.path.endswith("/season/1"):
            return httpx.Response(
                200,
                json={
                    "episodes": [
                        {"episode_number": 1, "name": "The Journey's End"},
                        {"episode_number": 2, "name": "It Didn't Have to Be Magic"},
                    ]
                },
            )
        if request.url.path.endswith("/season/2"):
            return httpx.Response(
                200,
                json={"episodes": [{"episode_number": 1, "name": "A New Journey"}]},
            )
        raise AssertionError(request.url)

    client = TMDBClient(api_key="api-key", transport=httpx.MockTransport(handler))
    try:
        episodes = await client.series_episodes(209867)
    finally:
        await client.aclose()

    assert [(item.season, item.episode, item.absolute_number) for item in episodes] == [
        (1, 1, 1),
        (1, 2, 2),
        (2, 1, 3),
    ]
    assert episodes[-1].name == "A New Journey"


@pytest.mark.asyncio
async def test_tmdb_read_token_uses_bearer_authentication() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer eyJ.header.payload"
        assert "api_key" not in request.url.params
        return httpx.Response(200, json={"results": []})

    client = TMDBClient(
        api_key="eyJ.header.payload",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.search_titles("Frieren", kind="show") == []
    finally:
        await client.aclose()
