from __future__ import annotations

import asyncio
from xml.etree import ElementTree as ET

import httpx
import pytest

from bankai.metadata import anime_mapping as mapping
from bankai.metadata.tvdb import TVDBClient
from bankai.queue.models import MediaKind


def test_anidb_title_alias_locates_parent_tvdb_id(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ET.fromstring(
        '<animetitles><anime aid="17765"><title>Bleach: Sennen Kessen Hen - Ketsubetsu Tan</title>'
        "<title>Bleach: Thousand-Year Blood War - The Separation</title></anime></animetitles>"
    )
    records = ET.fromstring(
        '<anime-list><anime anidbid="17765" tvdbid="74796" defaulttvdbseason="17" episodeoffset="13">'
        "<name>Bleach: Sennen Kessen Hen - Ketsubetsu Tan</name></anime></anime-list>"
    )

    async def resource(name: str, *args, **kwargs):
        return names if name.endswith(".gz") else records

    monkeypatch.setattr(mapping, "_resource", resource)
    found = asyncio.run(mapping.anidb_parts("Bleach: Thousand-Year Blood War - The Separation"))
    assert [(item.anidb_id, item.tvdb_id) for item in found] == [(17765, 74796)]


def test_tvdb_anime_filter_excludes_same_named_live_action() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/login"):
            return httpx.Response(200, json={"data": {"token": "test"}})
        if path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"tvdb_id": "1", "type": "series", "name": "Liar Game"},
                        {"tvdb_id": "2", "type": "series", "name": "Liar Game"},
                    ]
                },
            )
        if "/extended" in path:
            is_anime = "/2/" in path
            return httpx.Response(
                200,
                json={
                    "data": {
                        "genres": [{"name": "Anime" if is_anime else "Drama"}],
                        "image": "https://example.com/poster.jpg",
                    }
                },
            )
        return httpx.Response(404)

    async def run():
        client = TVDBClient(api_key="test", transport=httpx.MockTransport(handle))
        try:
            return await client.search_aliases("Liar Game", kind=MediaKind.EPISODE, anime_only=True)
        finally:
            await client.aclose()

    assert [item.tvdb_id for item in asyncio.run(run())] == [2]
