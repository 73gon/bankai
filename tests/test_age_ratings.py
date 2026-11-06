"""German age ratings (FSK) for anime and shows: where each comes from, and how it is written."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bankai.metadata import tmdb
from bankai.web import age_ratings, jellyfin


def test_tmdb_s_german_rating_comes_first_then_adult_then_another_country_s():
    assert age_ratings.decide({"DE": "16", "US": "TV-14"}) == {"age": 16, "source": "de", "basis": "DE 16"}
    assert age_ratings.decide({"US": "TV-14", "AU": "MA15+"})["age"] == 12
    assert age_ratings.decide({"AU": "MA15+", "BR": "12"}) == {"age": 16, "source": "estimate", "basis": "AU MA15+"}
    assert age_ratings.decide({"KR": "19"})["age"] == 18
    assert age_ratings.decide({"US": "TV-14"}, adult=True)["source"] == "adult"
    # By hand beats everything; a rating no table knows gives none.
    assert age_ratings.decide({"DE": "16"}, hand=6)["age"] == 6
    assert age_ratings.decide({"FI": "K16"}) == {"age": None, "source": None, "basis": None}
    assert age_ratings.decide(None)["age"] is None


@pytest.mark.parametrize(("value", "age"), [("16", 16), ("FSK 12", 12), ("FSK-0", 0), ("0", 0), ("14", None), ("", None)])
def test_german_ratings_read_as_fsk_ages(value, age):
    assert age_ratings.german_age(value) == age


def _show_world(monkeypatch, items):
    """A Jellyfin with one show library's series (catalog, /Users, /Items/{id}) and TMDB's ratings."""
    posted = []

    async def catalog():
        return [
            {"item_id": item_id, "type": "Series", "name": item["Name"], "year": 2006, "library": "Serien",
             "genres": [], "tags": [], "provider_ids": item["ProviderIds"], "locked_fields": item["LockedFields"],
             "path": None, "rating": item.get("OfficialRating")}
            for item_id, item in items.items()
        ]

    def handler(request):
        if request.url.path == "/Users":
            return httpx.Response(200, json=[{"Id": "u1", "Policy": {"IsAdministrator": True}}])
        item_id = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200, json=items[item_id])
        body = json.loads(request.content)
        posted.append(body)
        items[item_id] = body
        return httpx.Response(204)

    async def ensure(ids, **_kwargs):
        return 0

    async def names(ids, kept):
        return {1405: "Dexter"}

    monkeypatch.setattr(jellyfin, "catalog", catalog)
    monkeypatch.setattr(jellyfin, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://jf"))
    monkeypatch.setattr(age_ratings, "_anime_titles", lambda: [])
    monkeypatch.setattr(age_ratings, "_names", names)
    monkeypatch.setattr(tmdb, "ensure_content_ratings", ensure)
    monkeypatch.setattr(tmdb, "content_ratings", lambda tmdb_id: {"DE": "18", "US": "TV-MA"})
    return posted


def test_a_show_is_written_whole_with_its_rating_locked_and_undone_to_what_it_was(monkeypatch):
    items = {"d": {"Id": "d", "Name": "Dexter", "Tags": [], "ProviderIds": {"Tmdb": "1405"}, "LockedFields": [],
                   "OfficialRating": "TV-MA", "Trickplay": {}}}
    posted = _show_world(monkeypatch, items)

    row = asyncio.run(age_ratings.plan())["titles"][0]
    assert (row["key"], row["rating"], row["source"], row["current"], row["due"]) == ("tmdb:tv:1405", "FSK-18", "de", "TV-MA", True)
    assert posted == []

    result = asyncio.run(age_ratings.apply({"tmdb:tv:1405"}))
    assert (result["shows"], result["failed"]) == (1, [])
    assert posted[-1]["OfficialRating"] == "FSK-18" and posted[-1]["LockedFields"] == ["OfficialRating"]
    # The whole item went back, but never Jellyfin's unreadable Trickplay.
    assert posted[-1]["Name"] == "Dexter" and "Trickplay" not in posted[-1]

    assert asyncio.run(age_ratings.undo("tmdb:tv:1405")) == 1
    assert (items["d"]["OfficialRating"], items["d"]["LockedFields"]) == ("TV-MA", [])
    assert age_ratings.load()["written"] == {}


def test_a_rating_set_by_hand_wins_and_an_anime_without_an_id_is_searched_for_once(monkeypatch):
    searched = []

    async def search(query, year=None):
        searched.append(query)
        return [{"id": 7, "name": "Live Action", "genre_ids": [18]}, {"id": 8, "name": "The Anime", "genre_ids": [16]}]

    async def catalog():
        return []

    async def ensure(ids, **_kwargs):
        return 0

    async def names(ids, kept):
        return {}

    monkeypatch.setattr(jellyfin, "catalog", catalog)
    monkeypatch.setattr(age_ratings, "_names", names)
    monkeypatch.setattr(age_ratings, "_anime_titles", lambda: [{
        "key": "anime:5", "cid": 5, "kind": "anime", "name": "Witch", "search": ["Release that Witch", None], "year": 2026,
        "adult": False, "tmdb_id": None, "match": None, "item_id": None, "current": None,
    }])
    monkeypatch.setattr(tmdb, "search_tv", search)
    monkeypatch.setattr(tmdb, "ensure_content_ratings", ensure)
    monkeypatch.setattr(tmdb, "content_ratings", lambda tmdb_id: {"AU": "M"} if tmdb_id == 8 else None)

    row = asyncio.run(age_ratings.plan())["titles"][0]
    # Animation first; kept, so the next review asks TMDB no more.
    assert (row["tmdb_id"], row["match"], row["rating"], row["basis"]) == (8, "name search", "FSK-12", "AU M")
    asyncio.run(age_ratings.plan())
    assert searched == ["Release that Witch"]

    age_ratings.set_hand("anime:5", 16)
    row = asyncio.run(age_ratings.plan())["titles"][0]
    assert (row["rating"], row["source"]) == ("FSK-16", "hand")
    with pytest.raises(ValueError):
        age_ratings.set_hand("anime:5", 14)
