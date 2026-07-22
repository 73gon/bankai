from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from bankai.config import reset_settings_cache
from bankai.metadata.tvdb import TVDBClient, get_title_aliases
from bankai.queue.models import MediaKind


@pytest.fixture(autouse=True)
def _clear_settings() -> None:
    reset_settings_cache()


@pytest.mark.asyncio
async def test_tvdb_client_expands_series_aliases() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token-123"}})
        if request.url.path == "/v4/search":
            assert request.url.params["type"] == "series"
            assert request.headers["authorization"] == "Bearer token-123"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": "series",
                            "tvdb_id": 123,
                            "name": "Arcane",
                            "year": 2021,
                        }
                    ]
                },
            )
        if request.url.path == "/v4/series/123/translations/deu":
            return httpx.Response(200, json={"data": {"name": "Arcane"}})
        if request.url.path == "/v4/series/123/translations/eng":
            return httpx.Response(200, json={"data": {"name": "Arcane"}})
        return httpx.Response(404)

    client = TVDBClient(
        api_key="api-key",
        pin="pin",
        languages=["deu", "eng"],
        transport=httpx.MockTransport(handler),
    )
    try:
        aliases = await client.search_aliases("Arcane", kind=MediaKind.EPISODE)
    finally:
        await client.aclose()

    assert len(aliases) == 1
    assert aliases[0].name == "Arcane"
    assert aliases[0].tvdb_id == 123
    assert aliases[0].kind is MediaKind.EPISODE


@pytest.mark.asyncio
async def test_tvdb_movie_alias_uses_worldwide_release_instead_of_earliest_year() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/login":
            return httpx.Response(200, json={"data": {"token": "token-123"}})
        if request.url.path == "/v4/search":
            return httpx.Response(
                200,
                json={"data": [{"type": "movie", "tvdb_id": 807, "name": "300", "year": 2006}]},
            )
        if request.url.path.endswith("/translations/deu") or request.url.path.endswith("/translations/eng"):
            return httpx.Response(200, json={"data": {"name": "300"}})
        if request.url.path == "/v4/movies/807/extended":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "releases": [
                            {"country": "usa", "date": "2006-12-09", "detail": "Festival"},
                            {"country": "Worldwide", "date": "2007-03-09", "detail": "Theatrical"},
                        ]
                    }
                },
            )
        return httpx.Response(404)

    client = TVDBClient(api_key="api-key", transport=httpx.MockTransport(handler))
    try:
        aliases = await client.search_aliases("300", kind=MediaKind.MOVIE)
    finally:
        await client.aclose()

    assert aliases[0].year == 2007


@pytest.mark.asyncio
async def test_get_title_aliases_is_empty_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BANKAI_CONFIG", raising=False)
    monkeypatch.delenv("BANKAI_METADATA__TVDB_API_KEY", raising=False)

    aliases = await get_title_aliases("Arcane", kind=MediaKind.EPISODE)

    assert aliases == []
