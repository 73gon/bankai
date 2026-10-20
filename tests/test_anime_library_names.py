"""Names in the library: how a show is told apart, and what each tab of a card is called."""

from __future__ import annotations

import time

import pytest

from bankai.web.anime_library import _name, tab_labels


# Every pair below appeared twice in the library: once as the folder holding
# the episodes, once as an empty card built from the tracked title.
@pytest.mark.parametrize(
    "folder_name,title",
    [
        # sanitise() drops "?" and ":" from folder names; the title keeps them.
        (
            "Heroine Saint No, I'm an All-Works Maid (And Proud of It)! (2026)",
            "Heroine? Saint? No, I'm an All-Works Maid (And Proud of It)!",
        ),
        ("Jaadugar A Witch in Mongolia (2026)", "Jaadugar: A Witch in Mongolia"),
        (
            "Magical Girl Lyrical Nanoha EXCEEDS Gun Blaze Vengeance",
            "Magical Girl Lyrical Nanoha EXCEEDS: Gun Blaze Vengeance",
        ),
        # Folders left behind by the double-year bug still belong to the series.
        ("LIAR GAME (2026) (2026)", "LIAR GAME (2026)"),
        (
            "Love Unseen Beneath the Clear Night Sky (2026) (2026)",
            "Love Unseen Beneath the Clear Night Sky (2026)",
        ),
    ],
)
def test_a_folder_and_its_title_are_the_same_show(folder_name, title):
    assert _name(folder_name) == _name(title)


@pytest.mark.parametrize(
    "source_title,expected",
    [
        # A season is named per season; TVDB holds one series.
        ("Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e S3", "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"),
        ("Some Show Season 2", "Some Show"),
        ("Some Show 3rd Season", "Some Show"),
        ("Some Show S04", "Some Show"),
        ("Some Show (2024)", "Some Show"),
        # A number that is part of the name stays.
        ("Mob Psycho 100", "Mob Psycho 100"),
        ("Steins;Gate 0", "Steins;Gate 0"),
        ("86", "86"),
    ],
)
def test_the_season_suffix_comes_off_before_the_tvdb_search(source_title, expected):
    """Left on, it went into the query and the name comparison, so nothing
    matched and the Movies & Shows card was left with no cover."""
    from bankai.web.anime_library import _search_title

    assert _search_title(source_title) == expected


@pytest.fixture()
def metadata_store(monkeypatch):
    """show_metadata against a TVDB answered from ``provider``, saving to a dict."""
    from bankai.web import anime_library, discover

    monkeypatch.setattr(anime_library, "_CACHE", {})
    monkeypatch.setattr(anime_library, "_PERSISTENT_CACHE", {})
    monkeypatch.setattr(anime_library, "_REFRESHING", set())
    monkeypatch.setattr(anime_library, "flush_persistent_cache", lambda: None)
    monkeypatch.setattr(discover, "is_configured", lambda: True)
    provider = {"answer": [], "calls": 0}

    async def search(query, **kwargs):
        provider["calls"] += 1
        if isinstance(provider["answer"], Exception):
            raise provider["answer"]
        return provider["answer"]

    monkeypatch.setattr(discover, "search", search)
    return provider


def test_an_outage_is_not_cached_as_an_answer(metadata_store):
    """A failure held the show at its folder name with no cover for a full day."""
    import asyncio

    from bankai.web import anime_library

    metadata_store["answer"] = RuntimeError("TVDB is down")

    assert asyncio.run(anime_library.show_metadata("Nothing Matches This")) == {}
    assert anime_library._PERSISTENT_CACHE == {}


def test_no_such_title_is_remembered_so_a_restart_does_not_ask_again(metadata_store):
    """Every unmatched film was searched for again on every restart.

    That was ~360 provider round trips before the Movies & Shows page could
    answer: over two minutes.
    """
    import asyncio

    from bankai.web import anime_library

    assert asyncio.run(anime_library.show_metadata("Nothing Matches This", kind="movie")) == {}
    # Remembered as a miss, never as a match.
    assert [key.split(":")[0] for key in anime_library._PERSISTENT_CACHE] == ["miss"]

    anime_library._CACHE.clear()  # a restart: memory gone, the file kept
    assert asyncio.run(anime_library.show_metadata("Nothing Matches This", kind="movie")) == {}
    assert metadata_store["calls"] == 1


def test_an_expired_answer_is_served_while_it_refreshes(metadata_store):
    """Expiry used to block the page on the provider, once a day per card."""
    import asyncio

    from bankai.web import anime_library

    key = "metadata:v2:all:show:frieren"
    anime_library._PERSISTENT_CACHE[key] = {
        "saved_at": time.time() - anime_library._PERSISTENT_TTL_SECONDS - 60,
        "value": {"english_title": "Frieren", "tvdb_id": 424536},
    }

    async def load():
        first = await anime_library.show_metadata("Frieren")
        # Let the background refresh run before the loop closes.
        await asyncio.gather(*anime_library._BACKGROUND)
        return first

    assert asyncio.run(load())["tvdb_id"] == 424536
    assert metadata_store["calls"] == 1


def test_genuinely_different_shows_stay_apart():
    assert _name("Grand Blue") != _name("Grand Blue Dreaming")
    assert _name("Show A (2024)") != _name("Show B (2024)")


def test_a_single_year_is_still_stripped():
    assert _name("Test Show (2024)") == _name("Test Show")


def test_a_name_that_is_only_punctuation_does_not_collapse_to_nothing():
    """sanitise() would otherwise fall back and merge unrelated shows."""
    assert _name("???") != _name("***")


def test_merge_episodes_marks_each_episode_with_its_codec():
    from bankai.metadata.tvdb import TVDBEpisode
    from bankai.web.anime_library import merge_episodes

    files = [
        {"path": "1", "season_number": 1, "episode": 1, "name": "1", "staged": False},
        {"path": "2", "season_number": 1, "episode": 2, "name": "2", "staged": False},
    ]
    roster = [
        TVDBEpisode(1, 1, aired="2020-01-01"),
        TVDBEpisode(1, 2, aired="2020-01-08"),
        TVDBEpisode(1, 3, aired="2020-01-15"),
    ]
    merged = merge_episodes(
        files, roster, ended=True, codecs={(1, 1): "hevc", (1, 2): "avc"}
    )
    by_number = {row["episode"]: row for row in merged["episodes"]}
    assert by_number[1]["codec"] == "hevc"
    assert by_number[2]["codec"] == "avc"
    # An episode that is not downloaded has no encode to report.
    assert by_number[3]["codec"] is None


# -- Tabs: one per AniList entry of a collection ------------------------------------------


def test_a_tab_is_named_without_the_collections_own_name(anilist_data):
    anilist_data.add(1, "Shingeki no Kyojin", english="Attack on Titan", start="2013-04-07")
    anilist_data.add(2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2", start="2017-04-01")
    anilist_data.add(3, "Shingeki no Kyojin OVA", english="Attack on Titan: Lost Girls", format="OVA")

    assert tab_labels([1, 2, 3], "Attack on Titan") == {1: "Attack on Titan", 2: "Season 2", 3: "Lost Girls"}


def test_without_a_collection_a_tab_is_its_entrys_whole_name(anilist_data):
    anilist_data.add(2, "Shingeki no Kyojin Season 2", english="Attack on Titan Season 2")
    anilist_data.add(4, "Kimi no Na wa.")

    assert tab_labels([2, 4], None) == {2: "Attack on Titan Season 2", 4: "Kimi no Na wa."}


def test_two_tabs_of_one_name_are_told_apart_by_what_they_are(anilist_data):
    anilist_data.add(1, "Shingeki no Kyojin", english="Attack on Titan", start="2013-04-07")
    anilist_data.add(2, "Shingeki no Kyojin OVA", english="Attack on Titan", format="OVA", start="2013-12-09")
    anilist_data.add(3, "Shingeki no Kyojin Movie", english="Attack on Titan", format="MOVIE", start="2013-12-09")

    assert tab_labels([1, 2, 3], "Attack on Titan") == {
        1: "Attack on Titan (TV, 2013)",
        2: "Attack on Titan (OVA, 2013)",
        3: "Attack on Titan (Movie, 2013)",
    }


def test_two_tabs_of_one_name_and_kind_are_told_apart_by_year(anilist_data):
    anilist_data.add(136, "Hunter x Hunter", start="1999-10-16")
    anilist_data.add(11061, "Hunter x Hunter (2011)", english="Hunter x Hunter", start="2011-10-02")
    anilist_data.add(5, "Fullmetal Alchemist", start="2003-10-04")

    assert tab_labels([136, 11061, 5], None) == {
        136: "Hunter x Hunter (TV, 1999)",
        11061: "Hunter x Hunter (TV, 2011)",
        5: "Fullmetal Alchemist",
    }


def test_two_tabs_nothing_is_known_of_are_told_apart_by_their_ids(anilist_data):
    anilist_data.add(7, "Same Name", format=None, start=None, fetched=False)
    anilist_data.add(8, "Same Name", format=None, start=None, fetched=False)

    assert tab_labels([7, 8], None) == {7: "Same Name (7)", 8: "Same Name (8)"}
