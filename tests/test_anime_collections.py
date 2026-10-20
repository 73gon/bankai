"""Collections: the AniList entries of one show, kept together by bankai."""

from __future__ import annotations

import json

import pytest

from bankai.web import anime_collections as collections
from bankai.web.anime_collections import Store

# Attack on Titan: four TV entries in one sequel chain, and an OVA beside the first.
S1, S2, S3, S3P2, OVA, MOVIE = 16498, 20958, 99147, 104578, 18397, 20291
# Demon Slayer: a movie between two TV seasons.
KNY1, MUGEN, KNY2 = 101922, 112151, 142329


@pytest.fixture
def titan(anilist_data):
    anilist_data.add(
        S1, "Shingeki no Kyojin", english="Attack on Titan", episodes=25, start="2013-04-07",
        relations=[(S2, "SEQUEL"), (OVA, "SIDE_STORY", "OVA"), (MOVIE, "SIDE_STORY", "MOVIE")],
    )
    anilist_data.add(
        OVA, "Shingeki no Kyojin OVA", english="Attack on Titan OVA", format="OVA", episodes=8,
        start="2013-12-09", relations=[(S1, "PARENT")],
    )
    anilist_data.add(
        MOVIE, "Shingeki no Kyojin Movie 1: Guren no Yumiya", english="Attack on Titan: Crimson Bow and Arrow",
        format="MOVIE", episodes=1, start="2014-11-22", relations=[(S1, "PARENT")],
    )
    anilist_data.add(
        S2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", episodes=12, start="2017-04-01",
        relations=[(S1, "PREQUEL"), (S3, "SEQUEL")],
    )
    anilist_data.add(
        S3, "Shingeki no Kyojin Season 3", english="Attack on Titan Season 3", episodes=12, start="2018-07-23",
        relations=[(S2, "PREQUEL"), (S3P2, "SEQUEL")],
    )
    anilist_data.add(
        S3P2, "Shingeki no Kyojin Season 3 Part 2", english="Attack on Titan Season 3 Part 2", episodes=10,
        start="2019-04-29", relations=[(S3, "PREQUEL")],
    )
    return anilist_data


@pytest.fixture
def demon_slayer(anilist_data):
    anilist_data.add(
        KNY1, "Kimetsu no Yaiba", english="Demon Slayer: Kimetsu no Yaiba", episodes=26, start="2019-04-06",
        relations=[(MUGEN, "SEQUEL", "MOVIE")],
    )
    anilist_data.add(
        MUGEN, "Kimetsu no Yaiba Movie: Mugen Ressha-hen", english="Demon Slayer: Mugen Train", format="MOVIE",
        episodes=1, start="2020-10-16", relations=[(KNY1, "PREQUEL"), (KNY2, "SEQUEL", "TV")],
    )
    anilist_data.add(
        KNY2, "Kimetsu no Yaiba: Yuukaku-hen", english="Demon Slayer: Entertainment District Arc", episodes=11,
        start="2021-12-05", relations=[(MUGEN, "PREQUEL")],
    )
    return anilist_data


def _titan_collection(store: Store) -> collections.Collection:
    collections.join([S1], store)
    row = store.collection_of(S1)
    assert row is not None
    return row


# -- Joining -------------------------------------------------------------------------


def test_an_entry_joins_with_its_whole_sequel_chain(titan) -> None:
    store = Store()

    changed = collections.join([S1], store)

    assert changed == {1}
    row = store.collections[1]
    assert sorted(row.members) == sorted([S1, S2, S3, S3P2])
    assert row.title == S1
    # Side stories and recaps stay out until pulled in by hand.
    assert OVA not in row.members and MOVIE not in row.members


def test_a_later_season_joining_first_still_names_the_collection_after_the_first(titan) -> None:
    store = Store()

    collections.join([S3], store)

    row = store.collection_of(S3)
    assert row is not None and row.title == S1
    assert sorted(row.members) == sorted([S1, S2, S3, S3P2])


def test_an_entry_joins_the_collection_its_prequel_is_in(titan) -> None:
    store = Store()
    store.create([S1])

    changed = collections.join([S3], store)

    assert changed == {1}
    assert sorted(store.collections[1].members) == sorted([S1, S2, S3, S3P2])
    assert len(store.collections) == 1


def test_an_entry_already_in_a_collection_changes_nothing(titan) -> None:
    store = Store()
    _titan_collection(store)

    assert collections.join([S2], store) == set()


def test_an_entry_kept_out_by_hand_stops_the_chain(titan) -> None:
    store = Store(alone={S2})

    assert collections.chain(S1, store) == [S1]
    collections.join([S1], store)
    assert store.collections[1].members == [S1]


def test_an_entry_kept_out_by_hand_joins_when_asked_for_itself(titan) -> None:
    store = Store(alone={S1})

    collections.join([S1], store)

    assert S1 not in store.alone
    assert store.collection_of(S1) is not None


def test_an_entry_in_another_collection_stops_the_chain(titan) -> None:
    store = Store()
    store.create([S3])

    assert collections.chain(S1, store) == [S1, S2]


def test_at_equal_distance_the_older_collection_is_joined(titan) -> None:
    store = Store()
    store.create([S1])
    store.create([S3])

    assert collections.nearest(S2, store) == 1


def test_a_chain_running_through_several_shows_is_named_after_the_entry_that_joined(anilist_data) -> None:
    """PreCure's series follow one another as sequels; the first is not what the later ones are called."""
    anilist_data.add(1, "Futari wa Pretty Cure", episodes=49, start="2004-02-01", relations=[(2, "SEQUEL")])
    anilist_data.add(2, "Futari wa Pretty Cure Max Heart", episodes=47, start="2005-02-06",
                     relations=[(1, "PREQUEL"), (3, "SEQUEL")])
    anilist_data.add(3, "Yes! Precure 5", episodes=49, start="2007-02-04", relations=[(2, "PREQUEL")])
    store = Store()

    collections.join([3], store)

    row = store.collection_of(3)
    assert sorted(row.members) == [1, 2, 3]
    assert row.title == 3
    assert collections.name(row) == "Yes! Precure 5"


def test_a_collection_is_named_after_the_first_season_of_the_show_that_joined(anilist_data) -> None:
    """"Mr. Osomatsu", not its 3rd season -- and not the 1988 "Osomatsu-kun" the chain starts from."""
    anilist_data.add(1, "Osomatsu-kun", episodes=86, start="1988-02-13", relations=[(2, "SEQUEL")])
    anilist_data.add(2, "Osomatsu-san", english="Mr. Osomatsu", episodes=25, start="2015-10-06",
                     relations=[(1, "PREQUEL"), (3, "SEQUEL")])
    anilist_data.add(3, "Osomatsu-san 2nd Season", english="Mr. Osomatsu 2nd Season", episodes=25,
                     start="2017-10-03", relations=[(2, "PREQUEL"), (4, "SEQUEL")])
    anilist_data.add(4, "Osomatsu-san 3rd Season", english="Mr. Osomatsu 3rd Season", episodes=25,
                     start="2020-10-13", relations=[(3, "PREQUEL")])
    store = Store()

    collections.join([4], store)

    row = store.collection_of(4)
    assert sorted(row.members) == [1, 2, 3, 4]
    assert (row.title, collections.name(row)) == (2, "Mr. Osomatsu")
    assert collections.first_of_show([1, 2, 3, 4], 1) == 1
    # Nothing of the same show to go by: the entry itself.
    assert collections.first_of_show([1], 4) == 4


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Dorohedoro Season 2", "dorohedoro"),
        ("Shingeki no Kyojin Season 3 Part 2", "shingeki no kyojin"),
        ("Attack on Titan: Final Season", "attack on titan"),
        ("Hunter x Hunter (2011)", "hunter x hunter"),
        ("Overlord IV", "overlord"),
        ("Kaguya-sama wa Kokurasetai 2nd Season", "kaguya sama wa kokurasetai"),
        ("86", "86"),
    ],
)
def test_a_titles_stem_is_the_show_without_its_season(title, expected) -> None:
    assert collections.stem(title) == expected


def test_two_entries_are_one_show_when_one_name_begins_the_other(titan, anilist_data) -> None:
    anilist_data.add(5, "Yes! Precure 5", episodes=49, start="2007-02-04")

    assert collections.same_show(S1, S3P2)
    assert collections.same_show(S1, OVA)
    assert not collections.same_show(S1, 5)


def test_an_entry_with_no_sequels_or_prequels_is_a_collection_of_its_own(titan) -> None:
    store = Store()

    collections.join([OVA], store)

    assert store.collection_of(OVA).members == [OVA]


def test_a_sequel_announced_since_joins_its_shows_collection(anilist_data) -> None:
    anilist_data.add(1, "Show", episodes=12, start="2020-01-01", relations=[(2, "SEQUEL")])
    anilist_data.add(2, "Show 2", episodes=12, start="2021-01-01", relations=[(1, "PREQUEL")])
    store = Store()
    collections.join([1], store)

    anilist_data.add(2, "Show 2", episodes=12, start="2021-01-01", relations=[(1, "PREQUEL"), (3, "SEQUEL")])
    anilist_data.add(3, "Show 3", episodes=12, start="2022-01-01", relations=[(2, "PREQUEL")])

    assert collections.extend(store) == {1}
    assert store.collections[1].members == [1, 2, 3]
    assert collections.extend(store) == set()


# -- Seasons and Season 00 ---------------------------------------------------------------


def test_the_main_line_is_its_seasons_in_sequel_order(titan) -> None:
    store = Store()
    row = store.create([S3P2, OVA, S1, S3, S2], title=S1)

    placing = collections.plan(row)

    assert placing.seasons == {S1: 1, S2: 2, S3: 3, S3P2: 4}
    assert placing.specials == {OVA: 0}
    assert placing.order() == [S1, S2, S3, S3P2, OVA]
    assert placing.place(S3P2, 5) == (4, 5)
    assert placing.place(OVA, 3) == (0, 3)
    assert placing.place(12345, 1) is None


def test_a_prequel_made_later_is_the_season_after_the_ones_already_there(anilist_data) -> None:
    """Seasons are counted as they aired, not by AniList's story order."""
    anilist_data.add(1, "Show", episodes=12, start="2015-01-01", relations=[(2, "PREQUEL")])
    anilist_data.add(2, "Show: Origins", episodes=12, start="2018-01-01", relations=[(1, "SEQUEL")])
    store = Store()
    collections.join([1], store)

    assert collections.plan(store.collection_of(1)).seasons == {1: 1, 2: 2}


def test_a_movie_between_two_seasons_is_a_special(demon_slayer) -> None:
    store = Store()
    collections.join([KNY1], store)
    row = store.collection_of(KNY1)

    placing = collections.plan(row)

    assert sorted(row.members) == sorted([KNY1, MUGEN, KNY2])
    assert placing.seasons == {KNY1: 1, KNY2: 2}
    assert placing.specials == {MUGEN: 0}


def test_a_movie_left_out_of_the_collection_still_links_the_seasons_around_it(demon_slayer) -> None:
    store = Store(alone={MUGEN})
    row = store.create([KNY1, KNY2], title=KNY1)

    assert collections.plan(row, store=store).seasons == {KNY1: 1, KNY2: 2}


def test_season_00_blocks_follow_air_date_and_are_as_long_as_their_entries(titan) -> None:
    store = Store()
    row = store.create([S1, MOVIE, OVA], title=S1)

    assert collections.plan(row).specials == {OVA: 0, MOVIE: 8}
    # More on disk than AniList lists: the block is as long as what is there.
    assert collections.plan(row, episodes_on_disk=lambda aid: 10 if aid == OVA else 0).specials == {OVA: 0, MOVIE: 10}


def test_a_special_of_unknown_length_takes_one_place(anilist_data) -> None:
    anilist_data.add(1, "Show", episodes=12, start="2020-01-01")
    anilist_data.add(2, "Show Special", format="SPECIAL", episodes=None, start="2020-06-01")
    anilist_data.add(3, "Show Movie", format="MOVIE", episodes=1, start="2021-01-01")
    store = Store()
    row = store.create([1, 2, 3], title=1)

    assert collections.plan(row).specials == {2: 0, 3: 1}


def test_a_newcomer_special_goes_after_the_specials_already_given(titan) -> None:
    store = Store()
    row = store.create([S1, MOVIE], title=S1)
    collections.settle_specials(row)
    assert row.specials == [MOVIE]

    # The OVA aired before the movie, but the movie's place is already on disk.
    collections.include(store, row.id, OVA)

    assert collections.plan(row).specials == {MOVIE: 0, OVA: 1}
    collections.settle_specials(row)
    assert row.specials == [MOVIE, OVA]


def test_without_an_order_given_the_specials_go_by_air_date(titan) -> None:
    store = Store()
    row = store.create([S1, MOVIE, OVA], title=S1)

    collections.settle_specials(row)

    assert row.specials == [OVA, MOVIE]


def test_the_sequel_chain_comes_first_and_the_rest_by_air_date() -> None:
    relations = [(1, 2, "SEQUEL"), (3, 4, "SEQUEL"), (5, 4, "PREQUEL")]
    aired = {1: "2001", 2: "2002", 3: "2010", 4: "2011", 5: "2012", 6: "2000"}

    order = collections.sequel_order([1, 2, 3, 4, 5, 6], relations, aired=aired.__getitem__)

    # The longest chain is the spine, prequel to sequel; the rest as they aired.
    assert order == [3, 4, 5, 6, 1, 2]


def test_a_tab_is_named_without_the_collections_own_name() -> None:
    assert collections.short_label("Attack on Titan Season 2", "Attack on Titan") == "Season 2"
    assert collections.short_label("Attack on Titan: Junior High", "attack on titan") == "Junior High"
    assert collections.short_label("Attack on Titan", "Attack on Titan") == "Attack on Titan"
    assert collections.short_label("Kimi no Na wa.", "Attack on Titan") == "Kimi no Na wa."
    assert collections.short_label("Attack on Titan Season 2", None) == "Attack on Titan Season 2"


# -- Changes by hand ---------------------------------------------------------------------


def test_split_takes_entries_into_a_collection_of_their_own(titan) -> None:
    store = Store()
    row = _titan_collection(store)

    new = collections.split(store, row.id, [S3P2, S3])

    assert sorted(store.collections[new].members) == sorted([S3, S3P2])
    assert store.collections[new].title == S3
    assert sorted(row.members) == sorted([S1, S2])
    # Held elsewhere now: the sequel chain does not pull them back.
    assert collections.extend(store) == set()


@pytest.mark.parametrize(
    "aids,message",
    [([OVA], "none of these"), ([S1, S2, S3, S3P2], "empty")],
)
def test_split_refuses_what_is_not_there_or_would_leave_nothing(titan, aids, message) -> None:
    store = Store()
    row = _titan_collection(store)

    with pytest.raises(ValueError, match=message):
        collections.split(store, row.id, aids)
    with pytest.raises(ValueError):
        collections.split(store, 99, [S1])


def test_an_entry_excluded_without_files_is_kept_out_of_every_collection(titan) -> None:
    store = Store()
    row = _titan_collection(store)

    assert collections.exclude(store, row.id, [S3P2], with_files=set()) is None

    assert S3P2 in store.alone
    assert S3P2 not in row.members
    assert collections.extend(store) == set()


def test_an_entry_excluded_with_files_goes_to_a_collection_of_its_own(titan) -> None:
    store = Store()
    row = _titan_collection(store)

    new = collections.exclude(store, row.id, [S3P2], with_files={S3P2})

    assert new is not None and store.collections[new].members == [S3P2]
    assert S3P2 not in store.alone
    with pytest.raises(ValueError, match="empty"):
        collections.exclude(store, new, [S3P2], with_files={S3P2})


def test_include_pulls_an_entry_from_another_collection(titan) -> None:
    store = Store()
    row = _titan_collection(store)
    other = store.create([OVA])

    collections.include(store, row.id, OVA)

    assert OVA in row.members
    # Emptied, the other collection is gone.
    assert other.id not in store.collections


def test_include_takes_an_entry_kept_out_by_hand_back_in(titan) -> None:
    store = Store(alone={MOVIE})
    row = _titan_collection(store)

    collections.include(store, row.id, MOVIE)
    collections.include(store, row.id, MOVIE)

    assert row.members.count(MOVIE) == 1
    assert MOVIE not in store.alone
    with pytest.raises(ValueError):
        collections.include(store, 99, MOVIE)


def test_merge_moves_every_entry_of_the_other_collection(titan) -> None:
    store = Store()
    row = _titan_collection(store)
    other = collections.split(store, row.id, [S3, S3P2])

    assert collections.merge(store, row.id, other) == 2

    assert other not in store.collections
    assert sorted(row.members) == sorted([S1, S2, S3, S3P2])
    with pytest.raises(ValueError):
        collections.merge(store, row.id, row.id)
    with pytest.raises(ValueError):
        collections.merge(store, row.id, 99)


def test_the_title_entry_can_be_any_member(titan) -> None:
    store = Store()
    row = _titan_collection(store)

    collections.set_title(store, row.id, S2)

    assert collections.name(row) == "Attack on Titan Season 2"
    with pytest.raises(ValueError):
        collections.set_title(store, row.id, OVA)


def test_a_name_given_by_hand_wins_until_it_is_cleared(titan) -> None:
    store = Store()
    row = _titan_collection(store)

    collections.set_name(store, row.id, "  Shingeki  ")
    assert collections.name(row) == "Shingeki"

    collections.set_name(store, row.id, "   ")
    assert row.name is None
    collections.set_name(store, row.id, None)
    assert collections.name(row) == "Attack on Titan"
    with pytest.raises(ValueError):
        collections.set_name(store, 99, "x")


def test_a_collection_is_named_in_english_else_romaji_else_by_its_offline_title(anilist_data) -> None:
    anilist_data.add(1, "Sousou no Frieren", english="Frieren: Beyond Journey's End")
    anilist_data.add(2, "Akame ga Kill!")
    anilist_data.add(3, "Not Fetched Yet", fetched=False)
    store = Store()

    names = [collections.name(store.create([aid])) for aid in (1, 2, 3, 4)]

    assert names == ["Frieren: Beyond Journey's End", "Akame ga Kill!", "Not Fetched Yet", "AniList 4"]


def test_a_collection_is_the_show_not_its_first_season(anilist_data) -> None:
    anilist_data.add(1, "Dorohedoro", english="Dorohedoro Season 1")
    anilist_data.add(2, "Some Show 1st Season")
    anilist_data.add(3, "Other Show Season 10")
    store = Store()

    names = [collections.name(store.create([aid])) for aid in (1, 2, 3)]

    assert names == ["Dorohedoro", "Some Show", "Other Show Season 10"]
    # The entry itself keeps its name: it is a tab of the collection.
    assert collections.entry_name(1) == "Dorohedoro Season 1"


def test_taking_the_title_entry_out_gives_the_title_to_the_first_season(titan) -> None:
    store = Store()
    row = store.create([S1, OVA, S2], title=S1)

    store.take(S1)
    assert row.title == S2

    store.take(S2)
    store.take(OVA)
    assert row.id not in store.collections


# -- The store on disk ---------------------------------------------------------------------


def test_the_collections_are_saved_and_read_back(titan) -> None:
    with collections.editing() as store:
        collections.join([S1], store)
        collections.set_name(store, 1, "Shingeki")
        collections.settle_specials(store.collections[1])
        store.alone.add(OVA)

    loaded = collections.load()

    assert loaded.collections[1].members == store.collections[1].members
    assert loaded.collections[1].name == "Shingeki"
    assert loaded.collections[1].title == S1
    assert loaded.alone == {OVA}
    assert loaded.next == 2


def test_a_change_that_fails_halfway_is_not_saved(titan) -> None:
    with pytest.raises(RuntimeError), collections.editing() as store:
        collections.join([S1], store)
        raise RuntimeError("halfway")

    assert collections.load().collections == {}


def test_a_damaged_store_keeps_what_can_be_read() -> None:
    path = collections._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "next": 1,
                "collections": {
                    "3": {"members": [S2, S1, "x"], "title": OVA, "name": ""},
                    "4": {"members": []},
                    "junk": {"members": [S3]},
                },
                "alone": [str(MOVIE), "junk"],
            }
        ),
        encoding="utf-8",
    )

    store = collections.load()

    assert list(store.collections) == [3]
    # A title that is not among the members falls back to the first member.
    assert (store.collections[3].members, store.collections[3].title, store.collections[3].name) == ([S2, S1], S2, None)
    assert store.alone == {MOVIE}
    assert store.next == 4

    path.write_text("not json", encoding="utf-8")
    assert collections.load().collections == {}


# -- The relation graph ----------------------------------------------------------------------


def _edges(graph: dict) -> set[tuple]:
    return {(edge["from"], edge["to"], edge["label"], edge["main_line"]) for edge in graph["edges"]}


def test_the_graph_is_the_collection_and_the_entries_related_to_it(titan) -> None:
    store = Store()
    row = _titan_collection(store)

    graph = collections.graph(store, row.id, episodes_on_disk=lambda aid: 3 if aid == S1 else 0)

    nodes = {node["anilist_id"]: node for node in graph["nodes"]}
    assert set(nodes) == {S1, S2, S3, S3P2, OVA, MOVIE}
    assert [nodes[aid]["season"] for aid in (S1, S2, S3, S3P2)] == ["Season 1", "Season 2", "Season 3", "Season 4"]
    assert nodes[OVA]["in_collection"] is False and nodes[OVA]["season"] is None
    assert nodes[S1]["title_entry"] is True and nodes[S2]["title_entry"] is False
    assert (nodes[S1]["on_disk"], nodes[S1]["owned"], nodes[S2]["owned"]) == (3, True, False)
    assert (nodes[OVA]["type"], nodes[OVA]["episodes"], nodes[OVA]["first_aired"]) == ("OVA", 8, "2013-12-09")
    assert nodes[S2]["main_title"] == "Shingeki no Kyojin Season 2"
    assert (graph["collection_id"], graph["name"], graph["title_entry"], graph["root_aid"]) == (
        row.id, "Attack on Titan", S1, None
    )
    # Each link once, in one direction, whichever end it was listed on.
    assert _edges(graph) == {
        (S1, S2, "sequel", True),
        (S2, S3, "sequel", True),
        (S3, S3P2, "sequel", True),
        (S1, OVA, "side story", False),
        (S1, MOVIE, "side story", False),
    }
    assert len(graph["edges"]) == 5


def test_a_relation_listed_on_one_end_only_is_drawn_once(anilist_data) -> None:
    """A summary or spin-off read from its other end is the same link, not a second one back."""
    anilist_data.add(1, "Show", relations=[(2, "SUMMARY", "MOVIE"), (3, "SPIN_OFF"), (4, "ALTERNATIVE")])
    anilist_data.add(2, "Show Recap", format="MOVIE", episodes=1)
    anilist_data.add(3, "Show Spin-off")
    anilist_data.add(4, "Show Remake")
    store = Store()
    store.create([1])

    graph = collections.graph(store, 1)

    assert _edges(graph) == {
        (1, 2, "summary", False),
        (1, 3, "spin-off", False),
        (1, 4, "alternative", False),
    }
    assert {node["anilist_id"] for node in collections.graph(Store(), None, root_aid=2)["nodes"]} == {1, 2}


def test_a_member_off_the_main_line_is_a_special_in_the_graph(titan) -> None:
    store = Store()
    row = _titan_collection(store)
    collections.include(store, row.id, OVA)

    nodes = {node["anilist_id"]: node for node in collections.graph(store, row.id)["nodes"]}

    assert nodes[OVA]["season"] == "Specials" and nodes[OVA]["in_collection"] is True


def test_a_related_entry_in_another_collection_brings_that_whole_collection(titan) -> None:
    store = Store()
    row = _titan_collection(store)
    other = collections.split(store, row.id, [S3, S3P2])

    nodes = {node["anilist_id"]: node for node in collections.graph(store, row.id)["nodes"]}

    # S3P2 is related to nothing in this collection, but its collection is.
    assert nodes[S3P2]["collection_id"] == other
    assert nodes[S3P2]["collection_name"] == "Attack on Titan Season 3"
    assert nodes[S3P2]["in_collection"] is False


def test_an_entry_kept_out_by_hand_is_marked_so_in_the_graph(titan) -> None:
    store = Store()
    row = _titan_collection(store)
    collections.exclude(store, row.id, [S3P2], with_files=set())

    nodes = {node["anilist_id"]: node for node in collections.graph(store, row.id)["nodes"]}

    assert nodes[S3P2]["alone"] is True and nodes[S3P2]["collection_id"] is None


def test_an_entry_in_no_collection_has_a_graph_of_its_own(titan) -> None:
    store = Store()

    graph = collections.graph(store, None, root_aid=OVA)

    assert {node["anilist_id"] for node in graph["nodes"]} == {OVA, S1}
    assert (graph["collection_id"], graph["root_aid"], graph["name"], graph["title_entry"]) == (
        None, OVA, "Attack on Titan OVA", OVA
    )
    assert _edges(graph) == {(S1, OVA, "side story", False)}


def test_an_entry_in_a_collection_is_opened_through_it(titan) -> None:
    store = Store()
    _titan_collection(store)

    with pytest.raises(ValueError):
        collections.graph(store, None, root_aid=S2)
    with pytest.raises(ValueError):
        collections.graph(store, 99)
