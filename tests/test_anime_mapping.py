from __future__ import annotations

import asyncio
from xml.etree import ElementTree as ET

import httpx
import pytest

from bankai.metadata import anime_mapping as mapping
from bankai.metadata.tvdb import TVDBClient, TVDBEpisode
from bankai.queue.models import MediaKind


def part(attributes: str, children: str = "") -> mapping.Part:
    record = ET.fromstring(f'<anime anidbid="17765" tvdbid="74796" {attributes}>{children}</anime>')
    return mapping.Part(74796, 17765, record)


def test_bleach_anidb_part_offset_becomes_tvdb_s17e14() -> None:
    value = part('defaulttvdbseason="17" episodeoffset="13"')
    episodes = [TVDBEpisode(17, 14, 380, "Part two")]
    assert mapping.map_part(value, 1, episodes) == (17, 14)
    assert mapping.map_part(value, 99, episodes) is None


def test_bleach_kashin_offset_starts_at_tvdb_s17e41() -> None:
    value = part('defaulttvdbseason="17" episodeoffset="40"')
    assert mapping.map_part(value, 5, [TVDBEpisode(17, 45)]) == (17, 45)


def test_specific_mapping_overrides_generic_offset() -> None:
    value = part(
        'defaulttvdbseason="17" episodeoffset="13"',
        '<mapping-list><mapping anidbseason="1" tvdbseason="2">;1-4;</mapping></mapping-list>',
    )
    assert mapping.map_part(value, 1, [TVDBEpisode(2, 4)]) == (2, 4)


@pytest.mark.parametrize("target", ["0", "1+2"])
def test_unmapped_and_split_episodes_do_not_fabricate_identity(target: str) -> None:
    value = part(
        'defaulttvdbseason="1"',
        f'<mapping-list><mapping anidbseason="1" tvdbseason="1">;1-{target};</mapping></mapping-list>',
    )
    assert mapping.map_part(value, 1, [TVDBEpisode(1, 1), TVDBEpisode(1, 2)]) is None


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


def test_xem_scene_alias_and_episode_translate_to_tvdb(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_parts(title: str):
        return []

    async def resource(name: str, *args, **kwargs):
        if name == "xem_names.json":
            return {"74796": [{"Bleach TYBW": 17}]}
        return [{"scene": {"season": 17, "episode": 1}, "tvdb": {"season": 17, "episode": 1}}]

    monkeypatch.setattr(mapping, "anidb_parts", no_parts)
    monkeypatch.setattr(mapping, "_resource", resource)
    target, known = asyncio.run(
        mapping.mapped_episode("Bleach TYBW", 1, 74796, [TVDBEpisode(17, 1)])
    )
    assert known and target == (17, 1)
    target, known = asyncio.run(
        mapping.mapped_episode("Bleach TYBW", 99, 74796, [TVDBEpisode(17, 1)])
    )
    assert known and target is None


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
