"""Collections: the AniDB entries of one show kept together as one library card."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bankai.web import anime_collections
from bankai.web.shoko_catalog import Catalog, Entry, EntryEpisode, Group, build
from tests.test_anime_library_names import _run_library


def _entry(aid: int, title: str, group: int, *, series: int | None = None, episodes: int = 2) -> Entry:
    return Entry(
        aid=aid,
        title=title,
        poster_url=None,
        episodes=tuple(EntryEpisode(n, None, "2020-01-01") for n in range(1, episodes + 1)),
        series_id=series if series is not None else aid,
        group_id=group,
    )


def test_shoko_s_listings_give_each_series_its_group_and_each_entry_its_relations() -> None:
    catalog = build(
        {"List": [{"IDs": {"AniDB": 16188, "ID": 262, "TopLevelGroup": 238, "ParentGroup": 238}, "Name": "A Couple of Cuckoos"}]},
        {"List": []},
        {"List": [
            {"IDs": {"ID": 238, "MainAnime": 16188, "ParentGroup": None}, "Name": "A Couple of Cuckoos", "HasCustomName": False},
            {"IDs": {"ID": 239, "MainAnime": 1, "ParentGroup": 238}, "Name": "a sub-group"},
        ]},
        {"List": [{"IDs": {"AniDB": 16188}, "RelatedIDs": {"AniDB": 18773}, "Type": "Sequel"}]},
    )

    assert catalog.entries[16188].series_id == 262
    assert catalog.entries[16188].group_id == 238
    # A sub-group is part of its top-level group, not a collection of its own.
    assert set(catalog.groups) == {238}
    assert catalog.groups[238].main_aid == 16188
    assert catalog.relations == [(16188, 18773, "Sequel")]


def test_sequels_and_prequels_join_through_entries_not_on_disk() -> None:
    """Season 2 and 3 are not downloaded, and a special sits between them."""
    catalog = Catalog(
        entries={1: _entry(1, "Show", 10)},
        groups={10: Group(10, "Show", 1)},
        relations=[
            (1, 2, "Sequel"), (2, 1, "Prequel"),
            (2, 3, "Sequel"),  # the special between the seasons
            (3, 4, "Sequel"),
            (1, 9, "SideStory"),  # a spin-off movie: not the main line
        ],
    )

    found = anime_collections.assign(catalog)

    assert found.members(10) == [1, 2, 3, 4]
    assert 9 not in found.of


def test_groups_shoko_made_are_kept_and_an_entry_between_two_joins_the_nearer() -> None:
    catalog = Catalog(
        entries={1: _entry(1, "Show", 10), 5: _entry(5, "Show II", 20)},
        groups={10: Group(10, "Show", 1), 20: Group(20, "Show II", 5)},
        relations=[(1, 2, "Sequel"), (2, 3, "Sequel"), (3, 4, "Sequel"), (4, 5, "Sequel")],
    )

    found = anime_collections.assign(catalog)

    # Shoko put 1 and 5 in two groups; that is not undone here.
    assert (found.of[1], found.of[5]) == (10, 20)
    assert (found.of[2], found.of[4]) == (10, 20)
    # Equally far from both: the older group, the same answer every time.
    assert found.of[3] == 10


def test_without_shoko_s_groups_nothing_is_collected() -> None:
    assert anime_collections.assign(None).of == {}
    assert anime_collections.assign(Catalog(entries={1: _entry(1, "Show", 10)})).of == {}


def test_a_show_s_season_folders_become_one_card_with_its_missing_sequel_as_a_tab(monkeypatch) -> None:
    """Two seasons in two folders, the third not downloaded: one card, three tabs."""
    catalog = Catalog(
        entries={
            101: _entry(101, "Kaguya-sama", 7, series=1),
            102: _entry(102, "Kaguya-sama 2", 7, series=2),
        },
        groups={7: Group(7, "Kaguya-sama: Love Is War", 101)},
        relations=[(101, 102, "Sequel"), (102, 103, "Sequel"), (101, 900, "SideStory")],
        files={
            "/library/Kaguya-sama/Season 01/Kaguya-sama - 01.mkv": [(101, 1)],
            "/library/Kaguya-sama 2/Season 01/Kaguya-sama 2 - 01.mkv": [(102, 1)],
        },
    )
    shows = _run_library(
        monkeypatch,
        {"Kaguya-sama": ["Kaguya-sama - 01.mkv"], "Kaguya-sama 2": ["Kaguya-sama 2 - 01.mkv"]},
        titles=[(101, "Kaguya-sama", None), (102, "Kaguya-sama 2", None), (103, "Kaguya-sama 3", None)],
        catalog=catalog,
    )

    assert len(shows) == 1
    card = shows[0]
    assert card["title"] == card["key"] == "Kaguya-sama: Love Is War"
    assert card["collection_id"] == 7
    assert card["anidb_id"] == 101  # the group's title entry
    assert sorted(card["folders"]) == ["Kaguya-sama", "Kaguya-sama 2"]
    assert [entry["anidb_id"] for entry in card["collection_entries"]] == [101, 102, 103]
    assert [entry["owned"] for entry in card["collection_entries"]] == [True, True, False]
    # The side-story movie stays out.
    assert 900 not in card["anidb_ids"]


def test_a_blacklisted_sequel_is_not_offered_as_missing(monkeypatch) -> None:
    catalog = Catalog(
        entries={101: _entry(101, "Show", 7)},
        groups={7: Group(7, "Show", 101)},
        relations=[(101, 102, "Sequel")],
        files={"/library/Show/Season 01/Show - 01.mkv": [(101, 1)]},
    )

    shows = _run_library(
        monkeypatch,
        {"Show": ["Show - 01.mkv"]},
        titles=[(101, "Show", None), (102, "Show 2", None)],
        policies={"anidb:102": {"mode": "blacklisted", "anidb_id": 102}},
        catalog=catalog,
    )

    assert shows[0]["anidb_ids"] == [101]


def test_a_taken_entry_of_a_collection_is_its_tab_not_a_card_of_its_own(monkeypatch) -> None:
    catalog = Catalog(
        entries={101: _entry(101, "Show", 7)},
        groups={7: Group(7, "Show", 101)},
        relations=[(101, 102, "Sequel")],
        files={"/library/Show/Season 01/Show - 01.mkv": [(101, 1)]},
    )

    shows = _run_library(
        monkeypatch,
        {"Show": ["Show - 01.mkv"]},
        titles=[(101, "Show", None), (102, "Show 2", None), (555, "Other", None)],
        taken={"102": {"anidb_id": 102, "title": "Show 2"}, "555": {"anidb_id": 555, "title": "Other"}},
        catalog=catalog,
    )

    assert sorted(show["key"] for show in shows) == ["Other", "Show"]
    assert next(show for show in shows if show["key"] == "Show")["anidb_ids"] == [101, 102]


def test_naming_a_collection_writes_shoko_s_preferred_series_after_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bankai.web import shoko

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sent: list[tuple] = []

    async def get(endpoint, /, **params):
        if endpoint == "/api/v3/Group":
            return {"List": [{"IDs": {"ID": 7}, "Name": "Old", "HasCustomName": False}]}
        if endpoint == "/api/v3/Series":
            return {"List": [{"IDs": {"ID": 2, "ParentGroup": 7}, "Name": "Season 2"}]}
        return {"IDs": {"ID": 7}, "Name": "Season 2"}

    async def send(method, endpoint, /, *, json_body=None, **params):
        sent.append((method, endpoint, json_body))
        return True

    monkeypatch.setattr(shoko, "_get", get)
    monkeypatch.setattr(shoko, "_send", send)

    group = asyncio.run(anime_collections.set_title(7, 2))

    assert group["Name"] == "Season 2"
    assert sent == [("PATCH", "/api/v3/Group/7", [{"op": "replace", "path": "/PreferredSeriesID", "value": 2}])]
    snapshots = list((tmp_path / "bankai" / "shoko_groups").glob("groups-*.json"))
    assert len(snapshots) == 1 and '"Old"' in snapshots[0].read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from bankai.config import reset_settings_cache
    from bankai.web.app import create_app

    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("BANKAI_ANIME__ENABLED", "false")
    reset_settings_cache()
    with TestClient(create_app()) as value:
        yield value
    reset_settings_cache()


def test_only_an_entry_of_the_card_s_own_group_can_name_it(client: TestClient) -> None:
    """The request picks among the collection's entries; it never names a group itself."""
    response = client.post("/api/anime/collections/title", json={"key": "Nothing here", "anidb_id": 5})
    assert response.status_code == 404
    assert client.post("/api/anime/collections/title", json={"key": "x"}).status_code == 422
