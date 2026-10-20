"""Seasons arranged by hand, files matched one by one, a show removed without blacklisting."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bankai.config import reset_settings_cache
from bankai.metadata import anilist
from bankai.web import anime_collections as collections
from bankai.web import anime_files, anime_layout, erai
from bankai.web.app import create_app

S1, S2, FINAL, CH1, CH2 = 16498, 20958, 131681, 146984, 162314
AOT = "Attack on Titan"


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    anime = tmp_path / "anime"
    anime.mkdir()
    (tmp_path / "staging" / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(anime))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(tmp_path / "staging"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("BANKAI_ANIME__ENABLED", "false")
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    reset_settings_cache()
    yield SimpleNamespace(anime=anime, tmp=tmp_path)
    reset_settings_cache()


@pytest.fixture
def titan(anilist_data):
    anilist_data.add(S1, "Shingeki no Kyojin", english=AOT, episodes=2, start="2013-04-07",
                     relations=[(S2, "SEQUEL")])
    anilist_data.add(S2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", episodes=2,
                     start="2017-04-01", relations=[(S1, "PREQUEL"), (FINAL, "SEQUEL")],
                     streaming={n: f"S1 episode {n}" for n in range(1, 26)})
    anilist_data.add(FINAL, "Shingeki no Kyojin: The Final Season Part 2", english="Attack on Titan Final Season Part 2",
                     episodes=2, start="2022-01-10", relations=[(S2, "PREQUEL"), (CH1, "SEQUEL", "SPECIAL")])
    anilist_data.add(CH1, "Shingeki no Kyojin: The Final Season - Kanketsu-hen Zenpen", format="SPECIAL",
                     english="Attack on Titan Final Season THE FINAL CHAPTERS Special 1", episodes=1,
                     start="2023-03-04", relations=[(FINAL, "PREQUEL"), (CH2, "SEQUEL", "SPECIAL")])
    anilist_data.add(CH2, "Shingeki no Kyojin: The Final Season - Kanketsu-hen Kouhen", format="SPECIAL",
                     english="Attack on Titan Final Season THE FINAL CHAPTERS Special 2", episodes=1,
                     start="2023-11-05", relations=[(CH1, "PREQUEL")])
    with collections.editing() as store:
        collections.join([S1], store)
    return anilist_data


def _row():
    return collections.load().collections[1]


def test_without_an_arrangement_the_final_chapters_are_specials(titan) -> None:
    plan = collections.plan(_row())

    assert plan.arrangement() == {"seasons": [[S1], [S2], [FINAL]], "specials": [CH1, CH2], "custom": False}


def test_the_final_chapters_appended_to_the_last_season_continue_its_numbering(titan) -> None:
    with collections.editing() as store:
        collections.arrange(store, 1, [[S1], [S2], [FINAL, CH1, CH2]], [])

    plan = collections.plan(_row())

    assert plan.place(CH1, 1) == (3, 3) and plan.place(CH2, 1) == (3, 4)
    assert plan.arrangement()["custom"] is True
    assert plan.first_of(3) == FINAL


def test_seasons_can_be_reordered(titan) -> None:
    with collections.editing() as store:
        collections.arrange(store, 1, [[S2], [S1], [FINAL]], [CH2, CH1])

    plan = collections.plan(_row())

    assert (plan.place(S2, 1), plan.place(S1, 1)) == ((1, 1), (2, 1))
    assert plan.place(CH2, 1) == (0, 1) and plan.place(CH1, 1) == (0, 2)


def test_an_arrangement_must_place_every_entry_once(titan) -> None:
    with collections.editing() as store, pytest.raises(ValueError, match="exactly once"):
        collections.arrange(store, 1, [[S1], [S2]], [CH1, CH2])


def test_a_reset_goes_back_to_the_sequel_chain(titan) -> None:
    with collections.editing() as store:
        collections.arrange(store, 1, [[S2], [S1], [FINAL]], [CH1, CH2])
        collections.arrange(store, 1, None, None)

    assert collections.plan(_row()).arrangement()["seasons"] == [[S1], [S2], [FINAL]]


def test_a_sequel_listed_after_the_arrangement_is_a_season_after_it(titan, anilist_data) -> None:
    with collections.editing() as store:
        collections.arrange(store, 1, [[S1, S2]], [FINAL, CH1, CH2])
        store.collections[1].members.append(999)
    anilist_data.add(999, "Shingeki no Kyojin 5", episodes=2, start="2030-01-01", relations=[(CH2, "PREQUEL")])

    plan = collections.plan(_row(), episodes_on_disk=lambda aid: 0)

    assert plan.seasons[S2] == 1 and plan.offsets[S2] == 2
    assert plan.seasons.get(999) in (None, 2)


def test_appending_renames_the_files_into_the_season(titan, roots) -> None:
    rel = f"{AOT}/Season 00/{AOT} - S00E01.mkv"
    (roots.anime / rel).parent.mkdir(parents=True)
    (roots.anime / rel).write_text("video")
    with anime_files.editing() as rows:
        rows[rel] = [(CH1, 1)]
    with collections.editing() as store:
        collections.arrange(store, 1, [[S1], [S2], [FINAL, CH1, CH2]], [])

    asyncio.run(anime_layout.relayout({1}))

    assert (roots.anime / AOT / "Season 03" / f"{AOT} - S03E03.mkv").is_file()


def test_streaming_titles_numbered_for_another_season_are_not_this_entrys(titan) -> None:
    # Season 2's page listed Season 1's 25 episodes: none of them is Season 2's.
    assert [title for _n, title, _aired in anilist.episodes(S2)] == [None, None]


def test_streaming_titles_numbered_on_from_the_season_before_are_renumbered() -> None:
    assert anilist._own_numbers({"26": "a", "27": "b"}, 2) == {1: "a", 2: "b"}
    assert anilist._own_numbers({"1": "a", "2": "b"}, 12) == {1: "a", 2: "b"}


# -- The endpoints -------------------------------------------------------------------------


@pytest.fixture
def client(roots, titan):
    with TestClient(create_app()) as value:
        yield value


def _file(roots, rel: str) -> Path:
    path = roots.anime / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("video")
    return path


def test_the_arrangement_is_saved_and_the_files_follow(client, roots) -> None:
    _file(roots, f"{AOT}/Season 00/{AOT} - S00E01.mkv")
    with anime_files.editing() as rows:
        rows[f"{AOT}/Season 00/{AOT} - S00E01.mkv"] = [(CH1, 1)]

    graph = client.get("/api/anime/collections/1/graph").json()
    assert graph["arrangement"]["specials"] == [CH1, CH2]
    body = client.post(
        "/api/anime/collections/1/arrange", json={"seasons": [[S1], [S2], [FINAL, CH1, CH2]], "specials": []}
    ).json()

    assert body["result"]["seasons"][-1] == [FINAL, CH1, CH2] and body["moved"] == 1
    bad = client.post("/api/anime/collections/1/arrange", json={"seasons": [[S1]], "specials": []})
    assert bad.status_code == 422


def test_single_files_of_an_unmatched_folder_are_matched_one_by_one(client, roots) -> None:
    paths = [str(_file(roots, f"OADs/OAD - 0{n}.mkv")) for n in (1, 2, 3)]
    client.get("/api/anime/library", params={"rescan": True})

    first = client.post(
        "/api/anime/library/assign", json={"key": "OADs", "paths": paths[:2], "anilist_id": CH1, "episodes": [1, 2]}
    ).json()

    assert first["assigned"] == 2
    index = anime_files.load()
    assert sorted(pairs for pairs in index.values()) == [[(CH1, 1)], [(CH1, 2)]]
    # The third stays unmatched until it is matched too.
    shows = {row["key"]: row for row in client.get("/api/anime/library", params={"rescan": True}).json()["shows"]}
    assert shows["OADs"]["episode_count"] == 1


def test_a_matched_file_can_be_unmatched(client, roots) -> None:
    rel = f"{AOT}/Season 01/{AOT} - S01E01.mkv"
    path = _file(roots, rel)
    with anime_files.editing() as rows:
        rows[rel] = [(S1, 1)]
    client.get("/api/anime/library", params={"rescan": True})

    body = client.post("/api/anime/library/assign", json={"key": AOT, "paths": [str(path)], "anilist_id": None}).json()

    assert body["unmatched"] == 1 and anime_files.load() == {}


def test_a_file_not_on_the_card_is_refused(client, roots) -> None:
    client.get("/api/anime/library", params={"rescan": True})
    response = client.post(
        "/api/anime/library/assign", json={"key": AOT, "paths": [str(roots.tmp / "x.mkv")], "anilist_id": S1}
    )
    assert response.status_code in (404, 422)


def test_removing_without_blacklisting_deletes_and_unfollows_only(client, roots, monkeypatch) -> None:
    _file(roots, f"{AOT}/Season 01/{AOT} - S01E01.mkv")
    with anime_files.editing() as rows:
        rows[f"{AOT}/Season 01/{AOT} - S01E01.mkv"] = [(S1, 1)]
    state = erai._default_state()
    state["anime"] = {str(S1): {"anilist_id": S1}}
    erai._save_state(state)
    called = []
    monkeypatch.setattr(erai, "blacklist_show", lambda **kwargs: called.append(kwargs))
    client.get("/api/anime/library", params={"rescan": True})

    body = client.post("/api/anime/library/remove", json={"key": AOT, "blacklist": False}).json()

    assert called == [] and body.get("deleted_files", 1) >= 1
    assert not (roots.anime / AOT).exists()
    assert str(S1) not in erai._load_state().get("anime", {})
    assert not erai._load_policies()
