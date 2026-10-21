"""The maintenance pass: entries join their collections, and changed collections are laid out again."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankai.config import reset_settings_cache
from bankai.web import anime_collections as collections
from bankai.web import anime_files, anime_layout, anime_maintenance, erai

S1, S2, S3 = 16498, 20958, 99147
KNY1 = 101922
AOT = "Attack on Titan"
KNY = "Demon Slayer Kimetsu no Yaiba"


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    anime = tmp_path / "anime"
    staging = tmp_path / "staging"
    anime.mkdir()
    (staging / "Shows").mkdir(parents=True)
    monkeypatch.setenv("BANKAI_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", str(anime))
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(staging))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    # Which files are in the library itself, as the held library tree would say.
    published: set[str] = set()
    monkeypatch.setattr(anime_maintenance, "_published", lambda: set(published))
    # Every layout run, by the collections it was asked for.
    laid_out: list[set[int]] = []
    relayout = anime_layout.relayout

    async def spy(cids=None, **kwargs):
        laid_out.append(set(cids or ()))
        return await relayout(cids, **kwargs)

    monkeypatch.setattr(anime_layout, "relayout", spy)
    reset_settings_cache()
    yield SimpleNamespace(anime=anime, published=published, laid_out=laid_out)
    reset_settings_cache()


@pytest.fixture
def titan(anilist_data):
    anilist_data.add(S1, "Shingeki no Kyojin", english=AOT, episodes=25, start="2013-04-07",
                     relations=[(S2, "SEQUEL")], streaming={1: "To You, in 2000 Years"})
    anilist_data.add(S2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", episodes=12,
                     start="2017-04-01", relations=[(S1, "PREQUEL")])
    anilist_data.add(KNY1, "Kimetsu no Yaiba", english="Demon Slayer: Kimetsu no Yaiba", episodes=26,
                     start="2019-04-06")
    return anilist_data


def _announce_season_3(anilist_data) -> None:
    anilist_data.add(S2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", episodes=12,
                     start="2017-04-01", relations=[(S1, "PREQUEL"), (S3, "SEQUEL")])
    anilist_data.add(S3, "Shingeki no Kyojin Season 3", english="Attack on Titan Season 3", episodes=12,
                     start="2018-07-23", relations=[(S2, "PREQUEL")])


def _follow(*aids: int) -> None:
    state = erai._default_state()
    state["anime"] = {str(aid): {"anilist_id": aid} for aid in aids}
    erai._save_state(state)


def _on_disk(root: Path, rows: dict[str, list[tuple[int, int]]]) -> None:
    for rel in rows:
        path = root.joinpath(*Path(rel).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("video", encoding="utf-8")
    with anime_files.editing() as index:
        index.update(rows)


def _place(folder: str, season: int, number: int) -> str:
    return f"{folder}/Season {season:02d}/{folder} - S{season:02d}E{number:02d}.mkv"


def _run(**kwargs) -> dict:
    return asyncio.run(anime_maintenance.run(**kwargs))


# -- Joining ---------------------------------------------------------------------------


def test_a_followed_entry_joins_its_shows_collection(titan, library) -> None:
    _follow(S2)

    result = _run(layout=False)

    assert (result["collections_grown"], result["fetched"], result["offline_renewed"]) == (1, 0, False)
    row = collections.load().collection_of(S2)
    assert row is not None and sorted(row.members) == [S1, S2]
    assert row.title == S1


def test_an_entry_with_files_on_disk_joins_its_shows_collection(titan, library) -> None:
    _on_disk(library.anime, {"Loose/Loose - 01.mkv": [(KNY1, 1)]})

    assert _run(layout=False)["collections_grown"] == 1
    assert collections.load().collection_of(KNY1).members == [KNY1]


def test_a_blacklisted_followed_entry_joins_nothing(titan, library) -> None:
    _follow(S2)
    erai._save_policies({f"anilist:{S2}": {"mode": "blacklisted", "anilist_id": S2}})

    assert _run(layout=False)["collections_grown"] == 0
    assert collections.load().collections == {}


def test_a_newly_listed_sequel_joins_its_shows_collection(titan, library) -> None:
    with collections.editing() as store:
        collections.join([S1], store)
    assert _run(layout=False)["collections_grown"] == 0

    _announce_season_3(titan)

    assert _run(layout=False)["collections_grown"] == 1
    assert sorted(collections.load().collections[1].members) == [S1, S2, S3]
    assert _run(layout=False)["collections_grown"] == 0


# -- Laying out what changed ------------------------------------------------------------------


def _two_shows(library) -> None:
    _on_disk(library.anime, {
        "Shingeki no Kyojin/Shingeki no Kyojin - 01.mkv": [(S1, 1)],
        "Kimetsu/Kimetsu - 01.mkv": [(KNY1, 1)],
    })


def test_every_collection_is_laid_out_on_the_first_pass_and_none_again_unchanged(titan, library) -> None:
    _two_shows(library)

    first = _run()

    assert (first["collections_grown"], first["laid_out"], first["moved"]) == (2, 2, 2)
    assert library.laid_out == [{1, 2}]
    assert (library.anime / _place(AOT, 1, 1)).is_file() and (library.anime / _place(KNY, 1, 1)).is_file()
    assert (library.anime / AOT / "tvshow.nfo").is_file()

    second = _run()

    assert "laid_out" not in second
    assert library.laid_out == [{1, 2}]


def test_a_changed_collection_is_laid_out_again_and_an_unchanged_one_is_not(titan, library) -> None:
    _two_shows(library)
    _run()
    with collections.editing() as store:
        collections.set_name(store, 1, "Shingeki")

    result = _run()

    assert (result["laid_out"], result["moved"]) == (1, 1)
    assert library.laid_out[-1] == {1}
    assert (library.anime / _place("Shingeki", 1, 1)).is_file()
    assert (library.anime / _place(KNY, 1, 1)).is_file()
    assert "laid_out" not in _run()


def test_a_sequel_joining_lays_its_collection_out_again(titan, library) -> None:
    _two_shows(library)
    _run()

    _announce_season_3(titan)
    result = _run()

    assert (result["collections_grown"], result["laid_out"]) == (1, 1)
    assert library.laid_out[-1] == {1}


def test_a_file_published_into_the_library_lays_its_collection_out_again(titan, library) -> None:
    """Its NFO is written once Transfer has moved it out of staging."""
    _two_shows(library)
    _run()

    library.published.add(_place(KNY, 1, 1))
    result = _run()

    assert result["laid_out"] == 1
    assert library.laid_out[-1] == {2}


def test_new_episode_titles_from_anilist_lay_a_collection_out_again(titan, library) -> None:
    _two_shows(library)
    _run()
    nfo = (library.anime / _place(AOT, 1, 1)).with_suffix(".nfo")
    assert "To You, in 2000 Years" in nfo.read_text(encoding="utf-8")

    titan.add(S1, "Shingeki no Kyojin", english=AOT, episodes=25, start="2013-04-07",
              relations=[(S2, "SEQUEL")], streaming={1: "To You, 2,000 Years From Now"})
    result = _run()

    assert (result["laid_out"], result["nfo_written"]) == (1, 1)
    assert "To You, 2,000 Years From Now" in nfo.read_text(encoding="utf-8")


def test_a_fingerprint_changes_with_what_the_layout_is_made_of_not_with_when_it_was_fetched(titan, library) -> None:
    with collections.editing() as store:
        collections.join([S1, KNY1], store)
    rows = {"a/a - 01.mkv": [(S1, 1)], "k/k - 01.mkv": [(KNY1, 1)]}

    def fingerprints(published: set[str] = frozenset()) -> dict[str, str]:
        return anime_maintenance.fingerprints(anime_layout.Layout.build(collections.load(), rows), set(published))

    before = fingerprints()
    titan.add(KNY1, "Kimetsu no Yaiba", english="Demon Slayer: Kimetsu no Yaiba", episodes=26, start="2019-04-06")
    assert fingerprints() == before

    titan.add(KNY1, "Kimetsu no Yaiba", english="Demon Slayer", episodes=26, start="2019-04-06")
    after = fingerprints()
    assert (after["1"], after["2"] != before["2"]) == (before["1"], True)
    assert fingerprints({"a/a - 01.mkv"})["1"] != after["1"]


def test_room_for_pictures_again_lays_every_collection_out_anew(titan, library, monkeypatch) -> None:
    """Laid out while the disk was full, a collection got no posters; once there is room it gets them."""
    with collections.editing() as store:
        collections.join([S1, KNY1], store)
    rows = {"a/a - 01.mkv": [(S1, 1)], "k/k - 01.mkv": [(KNY1, 1)]}
    layout = anime_layout.Layout.build(collections.load(), rows)

    monkeypatch.setattr(anime_layout, "pictures_allowed", lambda: False)
    full = anime_maintenance.fingerprints(layout, set())
    monkeypatch.setattr(anime_layout, "pictures_allowed", lambda: True)
    room = anime_maintenance.fingerprints(layout, set())

    assert all(full[cid] != room[cid] for cid in full)


def test_a_season_folder_left_without_episodes_goes_rather_than_reading_specials(tmp_path) -> None:
    show = tmp_path / "Attack on Titan"
    kept, emptied, foreign = show / "Season 01", show / "Season 05", show / "Season 06"
    for folder in (kept, emptied, foreign):
        folder.mkdir(parents=True)
    (kept / "Attack on Titan - S01E01.mkv").write_bytes(b"v")
    (kept / "season.nfo").write_text("<season/>", encoding="utf-8")
    (emptied / "season.nfo").write_text("<season><title>Specials</title></season>", encoding="utf-8")
    (emptied / "poster.jpg").write_bytes(b"p")
    (foreign / "notes.txt").write_text("mine", encoding="utf-8")

    assert anime_layout._clear_empty_season(emptied) is True
    assert not emptied.exists()
    # Episodes in it, or a file bankai did not write: left as it is.
    assert anime_layout._clear_empty_season(kept) is False
    assert anime_layout._clear_empty_season(foreign) is False
    assert (kept / "season.nfo").exists() and (foreign / "notes.txt").exists()


def test_the_dashboard_sees_how_far_the_poster_download_is(monkeypatch) -> None:
    from bankai.web import jobs

    monkeypatch.setattr(jobs, "_PASSES", {"anime_metadata": {"running": True, "last_run": 1.0, "task": None}})
    monkeypatch.setattr(anime_layout, "progress", {"step": "Downloading posters and artwork", "done": 3, "total": 40})

    rows = {row["name"]: row for row in jobs.scheduled_passes()}

    assert rows["anime_metadata"]["progress"] == {"step": "Downloading posters and artwork", "done": 3, "total": 40}
    assert rows["library_walk"]["progress"] is None
