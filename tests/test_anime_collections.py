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


# -- Phase 2: the relation graph and changing a collection ---------------------


def _kaguya() -> Catalog:
    """Two seasons on disk in one group, a third not on disk, a side story and a sequel elsewhere."""
    return Catalog(
        entries={
            101: _entry(101, "Kaguya-sama", 7, series=1),
            102: _entry(102, "Kaguya-sama 2", 7, series=2),
            300: _entry(300, "Kaguya-sama Movie", 9, series=3),
        },
        groups={7: Group(7, "Kaguya-sama", 101), 9: Group(9, "Kaguya-sama Movie", 300)},
        relations=[
            (101, 102, "Sequel"), (102, 101, "Prequel"),
            (102, 103, "Sequel"),
            (101, 200, "SideStory"), (200, 101, "MainStory"),
            (103, 300, "Sequel"),
        ],
        files={"/library/a.mkv": [(101, 1)], "/library/b.mkv": [(102, 1)]},
    )


def test_the_graph_is_the_collection_and_what_is_related_to_it() -> None:
    found = anime_collections.graph(_kaguya(), 7)

    nodes = {node["anidb_id"]: node for node in found["nodes"]}
    assert {aid for aid, node in nodes.items() if node["in_collection"]} == {101, 102, 103}
    # The side story is a suggestion; so is the movie, a sequel kept elsewhere.
    assert {aid for aid, node in nodes.items() if not node["in_collection"]} == {200, 300}
    assert nodes[101]["title_entry"] and nodes[101]["owned"] and nodes[101]["on_disk"] == 1
    assert not nodes[103]["owned"]
    # Each link once, read the same way round whichever end listed it.
    assert sorted((e["from"], e["to"], e["label"]) for e in found["edges"]) == [
        (101, 102, "sequel"), (101, 200, "side story"), (102, 103, "sequel"), (103, 300, "sequel"),
    ]


def test_a_sequel_kept_in_another_collection_is_offered_to_be_merged() -> None:
    nodes = {node["anidb_id"]: node for node in anime_collections.graph(_kaguya(), 7)["nodes"]}

    assert nodes[300]["in_collection"] is False
    assert (nodes[300]["collection_id"], nodes[300]["collection_name"]) == (9, "Kaguya-sama Movie")


def test_an_entry_not_on_disk_taken_out_takes_what_is_reached_only_through_it() -> None:
    """Cutting a prequel off cuts off the prequels behind it as well."""
    catalog = Catalog(
        entries={1: _entry(1, "Precure 5", 10)},
        groups={10: Group(10, "Precure 5", 1)},
        relations=[(1, 2, "Prequel"), (2, 3, "Prequel")],
    )

    assert anime_collections.assign(catalog).members(10) == [1, 2, 3]
    cut = anime_collections.assign(catalog, {2: None})
    assert cut.members(10) == [1]
    assert cut.alone == {2}


def test_an_entry_not_on_disk_can_be_pulled_into_a_collection() -> None:
    found = anime_collections.assign(_kaguya(), {200: 7})
    assert 200 in found.members(7)


@pytest.fixture()
def shoko_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    from bankai.web import shoko

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    sent: list[tuple] = []

    async def get(endpoint, /, **params):
        return {"List": []}

    async def send(method, endpoint, /, *, json_body=None, **params):
        sent.append((method, endpoint, json_body))
        return {"IDs": {"ID": 77}} if method == "POST" else True

    monkeypatch.setattr(shoko, "_get", get)
    monkeypatch.setattr(shoko, "_send", send)
    return sent


def test_splitting_makes_a_new_shoko_group_and_the_entries_not_on_disk_follow(shoko_calls) -> None:
    created = asyncio.run(anime_collections.split(_kaguya(), 7, [102, 103]))

    assert created == 77
    assert shoko_calls == [("POST", "/api/v3/Group", {"SeriesIDs": [2]})]
    assert anime_collections.load_places() == {103: 77}


def test_splitting_off_only_entries_not_on_disk_puts_them_in_no_collection(shoko_calls) -> None:
    assert asyncio.run(anime_collections.split(_kaguya(), 7, [103])) is None
    assert shoko_calls == []
    assert anime_collections.load_places() == {103: None}


def test_pulling_in_moves_a_series_in_shoko_or_keeps_the_choice_until_it_is_downloaded(shoko_calls) -> None:
    asyncio.run(anime_collections.include(_kaguya(), 7, 300))
    asyncio.run(anime_collections.include(_kaguya(), 7, 200))

    assert shoko_calls == [("PATCH", "/api/v3/Series/3/Move/7", None)]
    assert anime_collections.load_places() == {200: 7}


def test_merging_moves_every_series_of_the_other_collection(shoko_calls) -> None:
    anime_collections.place([555], 9)

    moved = asyncio.run(anime_collections.merge(_kaguya(), 7, 9))

    assert moved == 1
    assert shoko_calls == [("PATCH", "/api/v3/Series/3/Move/7", None)]
    # A choice made for the merged collection follows it.
    assert anime_collections.load_places() == {555: 7}


def test_a_choice_is_handed_to_shoko_once_the_entry_is_downloaded(shoko_calls) -> None:
    """Pulled in while not on disk; Shoko then grouped it by its own rules."""
    anime_collections.place([300], 7)
    anime_collections.place([102], None)

    moved = asyncio.run(anime_collections.apply_places(_kaguya()))

    assert moved == 2
    assert ("PATCH", "/api/v3/Series/3/Move/7", None) in shoko_calls
    assert ("POST", "/api/v3/Group", {"SeriesIDs": [2]}) in shoko_calls
    # Shoko keeps them from here.
    assert anime_collections.load_places() == {}


def test_without_shoko_s_catalogue_there_is_no_collection_to_change(client: TestClient) -> None:
    assert client.get("/api/anime/collections/7/graph").status_code == 404
    assert client.post("/api/anime/collections/7/include", json={"anidb_id": 999}).status_code == 404


def test_the_graph_actions_take_only_entries_of_that_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The request picks among the graph's own entries; anything else is refused."""
    from bankai.config import reset_settings_cache
    from bankai.web import anime_collections as collections_mod
    from bankai.web import shoko_catalog
    from bankai.web.app import create_app

    async def fetch():
        return _kaguya()

    async def nothing_to_apply(catalog):
        return 0

    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("BANKAI_ANIME__ENABLED", "false")
    monkeypatch.setattr(shoko_catalog, "fetch", fetch)
    monkeypatch.setattr(collections_mod, "apply_places", nothing_to_apply)
    reset_settings_cache()
    with TestClient(create_app()) as client:
        graph = client.get("/api/anime/collections/7/graph").json()
        assert graph["name"] == "Kaguya-sama"
        assert {node["anidb_id"] for node in graph["nodes"]} == {101, 102, 103, 200, 300}
        # A member is not a suggestion, and the whole collection is not a split.
        assert client.post("/api/anime/collections/7/include", json={"anidb_id": 101}).status_code == 422
        assert client.post("/api/anime/collections/7/split", json={"anidb_ids": [101, 102, 103]}).status_code == 422
        assert client.post("/api/anime/collections/7/split", json={"anidb_ids": [200]}).status_code == 422
        # Only an entry of the collection can name it; a related one cannot.
        assert client.post("/api/anime/collections/7/title", json={"anidb_id": 200}).status_code == 422
        # Pulling in an entry not on disk is kept here, with no call to Shoko.
        assert client.post("/api/anime/collections/7/include", json={"anidb_id": 200}).status_code == 200
        assert collections_mod.load_places() == {200: 7}
    reset_settings_cache()


def test_tabs_follow_the_sequel_chain_then_the_side_stories() -> None:
    """By air date alone a 2013 OVA came between a show's first and second season."""
    aired = {1: "2013-04", 2: "2017-04", 3: "2018-07", 4: "2019-04", 9: "2013-12", 8: "2023-11"}
    relations = [
        (1, 2, "Sequel"), (2, 3, "Sequel"), (4, 3, "Prequel"),
        (1, 9, "SideStory"), (4, 8, "SideStory"),
    ]

    order = anime_collections.sequel_order([9, 4, 8, 2, 1, 3], relations, lambda aid: aired[aid])

    assert order == [1, 2, 3, 4, 9, 8]


def test_a_tab_is_named_without_the_collection_s_own_name() -> None:
    assert anime_collections.short_label("Attack on Titan Season 2", "Attack on Titan") == "Season 2"
    assert anime_collections.short_label("Attack on Titan: The Final Season", "Attack on Titan") == "The Final Season"
    # Nothing left over: the whole name, not an empty tab.
    assert anime_collections.short_label("Attack on Titan", "Attack on Titan") == "Attack on Titan"
    assert anime_collections.short_label("Junior High", "Attack on Titan") == "Junior High"


def test_a_related_entry_in_another_collection_brings_that_whole_collection() -> None:
    """Seen whole, it reads as another collection, not as one stray entry."""
    catalog = _kaguya()
    catalog.entries[301] = _entry(301, "Kaguya-sama Movie 2", 9, series=4)
    catalog.relations.append((300, 301, "Sequel"))

    nodes = {node["anidb_id"]: node for node in anime_collections.graph(catalog, 7)["nodes"]}

    assert nodes[301]["collection_id"] == 9 and nodes[301]["in_collection"] is False
    edges = anime_collections.graph(catalog, 7)["edges"]
    assert {"from": 300, "to": 301, "label": "sequel", "main_line": True} in edges


def test_a_season_not_downloaded_can_name_the_collection(shoko_calls) -> None:
    """Shoko's group takes its name; bankai keeps which entry it is, for the cover."""
    asyncio.run(anime_collections.set_title_entry(_kaguya(), 7, 103, "Kaguya-sama 3"))

    assert shoko_calls[-1] == (
        "PATCH",
        "/api/v3/Group/7",
        [
            {"op": "replace", "path": "/Name", "value": "Kaguya-sama 3"},
            {"op": "replace", "path": "/HasCustomName", "value": True},
        ],
    )
    assert anime_collections.load_titles() == {7: 103}
    group = _kaguya().groups[7]
    assert anime_collections.title_entry(group, anime_collections.load_titles()) == 103

    # Back to a season on disk: Shoko names the group after it again.
    asyncio.run(anime_collections.set_title_entry(_kaguya(), 7, 102, "Kaguya-sama 2"))

    assert shoko_calls[-1] == (
        "PATCH",
        "/api/v3/Group/7",
        [
            {"op": "replace", "path": "/PreferredSeriesID", "value": 2},
            {"op": "replace", "path": "/HasCustomName", "value": False},
        ],
    )
    assert anime_collections.load_titles() == {}


def test_an_entry_of_another_collection_cannot_name_this_one(shoko_calls) -> None:
    with pytest.raises(ValueError):
        asyncio.run(anime_collections.set_title_entry(_kaguya(), 7, 300, "Movie"))
    assert shoko_calls == []


def test_an_entry_in_no_collection_has_a_graph_of_its_own_to_move_from() -> None:
    """Cut out of every collection, it is still related to them, and can go into one."""
    found = anime_collections.graph(_kaguya(), None, places={103: None}, root_aid=103)

    nodes = {node["anidb_id"]: node for node in found["nodes"]}
    assert found["collection_id"] is None and found["root_aid"] == 103
    assert nodes[103]["in_collection"] and nodes[103]["title_entry"]
    # Its prequel's collection and its sequel's are both offered, whole.
    assert {nodes[102]["collection_id"], nodes[300]["collection_id"]} == {7, 9}
    assert 101 in nodes


def test_an_entry_in_a_collection_is_opened_through_it() -> None:
    with pytest.raises(ValueError):
        anime_collections.graph(_kaguya(), None, root_aid=103)


def test_relations_are_learned_for_entries_shoko_holds_no_series_for(shoko_calls, monkeypatch) -> None:
    """A record Shoko lacks is fetched by it, within a few per run; answers are kept."""
    from bankai.web import shoko, shoko_catalog

    calls: list[tuple] = []

    async def send(method, endpoint, /, *, json_body=None, **params):
        calls.append((method, endpoint))
        if endpoint.endswith("/Relations"):
            if endpoint == "/api/v3/Series/AniDB/500/Relations" and ("POST", "/api/v3/Series/AniDB/500/Refresh") not in calls:
                return None  # no record until Shoko fetched it
            aid = int(endpoint.split("/")[-2])
            return [{"IDs": {"AniDB": aid}, "RelatedIDs": {"AniDB": aid + 1}, "Type": "Sequel"}]
        return True

    monkeypatch.setattr(shoko, "_send", send)
    catalog = _kaguya()

    learned = asyncio.run(shoko_catalog.fill_relations(catalog, {101, 103, 500, 600}))

    # 101 is on disk and 103's links are listed already: neither is asked about.
    assert learned == 2
    assert ("GET", "/api/v3/Series/AniDB/103/Relations") not in calls
    # 600 has a record, 500 had to be fetched by Shoko first.
    assert ("POST", "/api/v3/Series/AniDB/600/Refresh") not in calls
    assert ("POST", "/api/v3/Series/AniDB/500/Refresh") in calls
    assert (500, 501, "Sequel") in catalog.relations and (600, 601, "Sequel") in catalog.relations

    # Kept: the next refresh adds them without asking again.
    calls.clear()
    fresh = _kaguya()
    asyncio.run(shoko_catalog.fill_relations(fresh, {500, 600}))
    assert calls == []
    assert (500, 501, "Sequel") in fresh.relations
