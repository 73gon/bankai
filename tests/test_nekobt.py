"""nekobt.to as Discover's second source: its API's rows in Nyaa's shape, languages any of."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from bankai.web import nekobt


def _release(ident: str, title: str, *, subs: str = "", fansubs: str = "", audio: str = "ja", seeders: int = 1,
             group: str = "Ironclad", description: str = "", batch: bool = False) -> dict:
    return {
        "id": ident, "title": title, "infohash": ident.rjust(40, "0"), "magnet": f"magnet:?xt=urn:btih:{ident.rjust(40, '0')}",
        "filesize": str(2 * 2**30), "uploaded_at": 1787099818069, "seeders": str(seeders), "leechers": "2",
        "completed": "9", "comment_count": "0", "level": 3, "category": 1, "video_type": 12, "video_codec": 3,
        "audio_lang": audio, "sub_lang": subs, "fsub_lang": fansubs, "description": description, "anonymous": False,
        "uploader": {"display_name": "someone"},
        "groups": [{"display_name": group, "uploading_group": True, "anonymous": 0}], "batch": batch,
    }


@pytest.fixture
def site(monkeypatch):
    """nekobt answering from a table: each language a filter of its own, as the real one does (AND within one)."""
    releases = [
        _release("1", "[Ironclad] Show - S01 (BD 1080p AV1)", subs="en,de", seeders=5, description="German subs", batch=True),
        _release("2", "[Fans] Show - 01 [720p]", fansubs="de", seeders=9, group="Fans"),
        _release("3", "[Raw] Show - 01 [1080p]", subs="en", audio="ja,en", seeders=3, group="Raw"),
    ]
    asked: list[dict[str, list[str]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        if request.url.path == "/api/v1/groups/search":
            return httpx.Response(200, json={"data": {"results": [{"id": "77", "display_name": "PHTMini", "name": "phtmini"}]}})
        asked.append(query)
        rows = releases
        for field, key in (("sub_lang", "sub_lang"), ("fansub_lang", "fsub_lang"), ("audio_lang", "audio_lang")):
            if field in query:
                rows = [row for row in rows if set(query[field][0].split(",")) <= set(row[key].split(","))]
        if "batch" in query:
            rows = [row for row in rows if row["batch"] == (query["batch"][0] == "true")]
        return httpx.Response(200, json={"error": False, "data": {"results": rows, "more": False}})

    monkeypatch.setattr(
        nekobt, "_client", lambda: httpx.AsyncClient(base_url=nekobt.BASE, transport=httpx.MockTransport(handle))
    )

    async def no_identify(items):
        for row in items:
            row["anilist"] = None

    monkeypatch.setattr(nekobt, "_identify", no_identify)
    return asked


def test_rows_come_in_nyaa_s_shape_with_what_only_nekobt_says(site):
    page = asyncio.run(nekobt.search("show"))

    first = page["items"][0]
    assert [row["id"] for row in page["items"]] == ["2", "1", "3"]  # most seeders first
    assert first["source"] == "nekobt" and first["detail_url"] == "https://nekobt.to/torrents/2"
    assert first["download_url"] == "" and first["magnet_uri"].startswith("magnet:?xt=urn:btih:")
    row = next(item for item in page["items"] if item["id"] == "1")
    assert (row["quality"], row["video_type"], row["codec"], row["publisher"]) == ("1080p", "BD Mini", "AV1", "Ironclad")
    assert row["size"] == "2.0 GiB" and row["published_at"].startswith("2026-")
    assert page["sent"] == ["https://nekobt.to/api/v1/torrents/search?query=show&limit=100&offset=0"]


def test_subtitles_are_any_of_the_languages_a_release_s_own_or_a_fansub_s(site):
    page = asyncio.run(nekobt.search("show", subtitles=["DE", "fr"]))

    assert sorted(row["id"] for row in page["items"]) == ["1", "2"]
    # One request per language and kind: nekobt reads a list as all of them.
    assert sorted((key, value[0]) for query in site for key, value in query.items() if key.endswith("_lang")) == [
        ("fansub_lang", "de"), ("fansub_lang", "fr"), ("sub_lang", "de"), ("sub_lang", "fr"),
    ]
    assert len(page["sent"]) == 4


def test_audio_quality_and_text_filters_ignore_case(site):
    assert [row["id"] for row in asyncio.run(nekobt.search("show", audio=["EN"]))["items"]] == ["3"]
    assert [row["id"] for row in asyncio.run(nekobt.search("", quality="1080P", title_filters="ironCLAD"))["items"]] == ["1"]
    assert [row["id"] for row in asyncio.run(nekobt.search("", description_filters="german SUBS"))["items"]] == ["1"]
    assert [row["id"] for row in asyncio.run(nekobt.search("", publisher="fans"))["items"]] == ["2"]


def test_a_known_group_is_asked_for_by_its_id_and_its_description_is_kept(site):
    page = asyncio.run(nekobt.search("", publisher="PHT Mini"))

    assert "group_id=77" in page["sent"][0]
    assert asyncio.run(nekobt.description("1")) == "German subs"
    with pytest.raises(ValueError):
        asyncio.run(nekobt.description("../etc"))


def test_batches_alone_or_single_episodes_alone(site):
    assert [row["id"] for row in asyncio.run(nekobt.search("", batch=True))["items"]] == ["1"]
    assert site[-1]["batch"] == ["true"]
    assert [row["id"] for row in asyncio.run(nekobt.search("", batch=False))["items"]] == ["2", "3"]
    assert {row["id"] for row in asyncio.run(nekobt.search(""))["items"]} == {"1", "2", "3"}


def test_releases_with_too_few_seeders_are_left_out(site):
    assert [row["id"] for row in asyncio.run(nekobt.search("", min_seeders=5))["items"]] == ["2", "1"]
