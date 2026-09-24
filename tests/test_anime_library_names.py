"""Show identity used to group the anime library."""

from __future__ import annotations

import time

import pytest

from bankai.web.anime_library import _name


# Every pair below appeared twice in the library: once as the folder holding
# the episodes, once as an empty card built from the tracked TVDB title.
@pytest.mark.parametrize(
    "folder_name,tvdb_title",
    [
        # sanitise() drops "?" and ":" from folder names; the TVDB title keeps them.
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
def test_a_folder_and_its_tvdb_title_are_the_same_show(folder_name, tvdb_title):
    assert _name(folder_name) == _name(tvdb_title)


@pytest.mark.parametrize(
    "source_title,expected",
    [
        # Erai names a source show per season; TVDB holds one series.
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
    """Left on, it went into the query and the alias comparison, so nothing
    matched and the card kept its romaji name with no cover."""
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

    async def candidates(query, **kwargs):
        provider["calls"] += 1
        if isinstance(provider["answer"], Exception):
            raise provider["answer"]
        return provider["answer"]

    monkeypatch.setattr(anime_library.anime, "tvdb_candidates", candidates)
    return provider


def test_an_outage_is_not_cached_as_an_answer(metadata_store):
    """A failure held the show at its romaji name with no cover for a full day."""
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

    key = "metadata:v2:show:frieren"
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


def test_episode_codecs_are_read_from_the_release_that_produced_them(monkeypatch):
    """Probing thousands of files on a slow library disk is not worth it."""
    from bankai.web import anime_library, erai

    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {"status": "done", "title": "[Erai-raws] Show - 01 [1080p][HEVC][ENG]"},
        "b" * 40: {"status": "done", "title": "[Erai-raws] Show - 02 [1080p CR WEB-DL AVC AAC]"},
        "c" * 40: {"status": "done", "title": "[Erai-raws] Other - 01 [1080p][HEVC]"},
    }
    state["canonical"] = {
        "555|1|1": {"info_hash": "a" * 40},
        "555|1|2": {"info_hash": "b" * 40},
        "999|1|1": {"info_hash": "c" * 40},
    }
    monkeypatch.setattr(erai, "_load_state", lambda: state)

    codecs = anime_library.episode_codecs(555)
    assert codecs == {(1, 1): "hevc", (1, 2): "avc"}
    # A different series is not mixed in.
    assert anime_library.episode_codecs(999) == {(1, 1): "hevc"}
    assert anime_library.episode_codecs(None) == {}


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


def test_the_release_state_is_read_once_for_the_whole_library(monkeypatch):
    """It is a fourteen megabyte file; reading it per show cost twenty seconds."""
    import asyncio
    from pathlib import Path

    from bankai.web import anime_library, discover, erai

    reads = []
    state = erai._default_state()
    state["releases"] = {
        f"{n:040x}": {"status": "done", "title": f"[Erai-raws] Show {n} - 01 [1080p][HEVC]"}
        for n in range(1, 21)
    }
    state["canonical"] = {
        f"{n}|1|1": {"info_hash": f"{n:040x}"} for n in range(1, 21)
    }

    def load():
        reads.append(1)
        return state

    monkeypatch.setattr(erai, "_load_state", load)
    monkeypatch.setattr(anime_library, "known_ids", lambda: {})
    monkeypatch.setattr(discover, "is_configured", lambda: False)
    monkeypatch.setattr(anime_library, "_nfo_id", lambda path: None)
    monkeypatch.setattr(anime_library, "probed_codecs", lambda files: {})
    monkeypatch.setattr(anime_library, "german_dubbed_episodes", lambda files: set())

    async def metadata(title, tvdb_id=None):
        return {"english_title": title, "tvdb_id": tvdb_id}

    monkeypatch.setattr(anime_library, "show_metadata", metadata)

    entries = [
        {
            "path": f"/library/Show {n}/Season 01/ep.mkv",
            "rel_path": f"Show {n}/Season 01/ep.mkv",
            "name": f"Show {n} - S01E01.mkv",
            "series": f"Show {n}",
            "season": "Season 01",
            "size": 1,
            "mtime": 0,
            "staged": False,
            "stage": "final",
            "transfer_status": "idle",
        }
        for n in range(1, 21)
    ]
    shows = asyncio.run(anime_library.group_shows(entries, Path("/library")))

    assert len(shows) == 20
    # One read for the codec index, whatever the library holds.
    assert len(reads) == 1
def _run_library(monkeypatch, folders, *, resolve=None, tracked=(), only_key=None):
    """group_shows over a library holding ``folders`` -> {folder: [episode, ...]}.

    ``resolve`` stands in for TVDB: {title: (tvdb_id, english_title)}.
    """
    import asyncio
    from pathlib import Path

    from bankai.web import anime_library, discover, erai

    state = erai._default_state()
    state["series"] = {
        str(index): {"english_title": title, "tvdb_id": None}
        for index, title in enumerate(tracked, start=1)
    }
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(anime_library, "known_ids", lambda: {})
    monkeypatch.setattr(discover, "is_configured", lambda: False)
    monkeypatch.setattr(anime_library, "_nfo_id", lambda path: None)
    monkeypatch.setattr(anime_library, "probed_codecs", lambda files: {})
    monkeypatch.setattr(anime_library, "german_dubbed_episodes", lambda files: set())

    async def metadata(title, tvdb_id=None):
        if resolve and title in resolve:
            found, english = resolve[title]
            return {"english_title": english, "tvdb_id": found}
        return {"english_title": title, "tvdb_id": tvdb_id}

    monkeypatch.setattr(anime_library, "show_metadata", metadata)

    entries = [
        {
            "path": f"/library/{folder}/Season 01/{name}",
            "rel_path": f"{folder}/Season 01/{name}",
            "name": name,
            "series": folder,
            "season": "Season 01",
            "size": 1,
            "mtime": 0,
            "staged": False,
            "stage": "final",
            "transfer_status": "idle",
        }
        for folder, names in folders.items()
        for name in names
    ]
    return asyncio.run(
        anime_library.group_shows(entries, Path("/library"), only_key=only_key)
    )


def test_one_show_in_two_folders_is_one_card(monkeypatch):
    """The folder name was the group key, so a second folder was a second card.

    Normalising the name only ever decided whether to add an empty card for a
    tracked title; it never merged two folders that both held episodes.
    """
    shows = _run_library(
        monkeypatch,
        {
            "Show": ["Show - S01E01.mkv"],
            "Show (2024)": ["Show - S01E02.mkv"],
        },
    )
    assert len(shows) == 1
    assert sorted(shows[0]["folders"]) == ["Show", "Show (2024)"]
    # Both folders' episodes end up on the one card.
    assert shows[0]["episode_count"] == 2


def test_two_names_for_one_series_are_one_card(monkeypatch):
    """A romaji folder beside its English one shares no spelling at all.

    Nothing about the two names can be compared; the TVDB id is what says
    they are the same series.
    """
    romaji = "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"
    english = "Classroom of the Elite"
    shows = _run_library(
        monkeypatch,
        {
            romaji: ["ep - S01E01.mkv"],
            english: ["ep - S02E01.mkv"],
        },
        resolve={romaji: (337912, english), english: (337912, english)},
    )
    assert len(shows) == 1
    assert shows[0]["title"] == english
    assert shows[0]["tvdb_id"] == 337912
    assert sorted(shows[0]["folders"]) == sorted([romaji, english])
    assert shows[0]["episode_count"] == 2


def test_a_tracked_title_does_not_duplicate_the_folder_it_belongs_to(monkeypatch):
    """The empty card for a tracked show has to land on its own folder."""
    romaji = "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e S3"
    english = "Classroom of the Elite"
    shows = _run_library(
        monkeypatch,
        {romaji: ["ep - S03E01.mkv"]},
        resolve={romaji: (337912, english), english: (337912, english)},
        tracked=[english],
    )
    assert len(shows) == 1
    assert shows[0]["title"] == english


def test_shows_without_a_tvdb_id_are_not_merged_on_a_guess(monkeypatch):
    """With no id there is nothing better than the name; separate is correct."""
    shows = _run_library(
        monkeypatch,
        {
            "Grand Blue": ["a - S01E01.mkv"],
            "Grand Blue Dreaming": ["b - S01E01.mkv"],
        },
    )
    assert len(shows) == 2


def test_asking_for_one_merged_show_returns_its_folders(monkeypatch):
    """The card's key is the English title, which need not be a folder name."""
    romaji = "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"
    english = "Classroom of the Elite"
    folders = {romaji: ["ep - S01E01.mkv"], "Unrelated Show": ["u - S01E01.mkv"]}
    resolve = {romaji: (337912, english)}

    by_key = _run_library(monkeypatch, folders, resolve=resolve, only_key=english)
    assert len(by_key) == 1
    assert by_key[0]["folders"] == [romaji]

    # The folder name still works, so a link made before the merge survives.
    by_folder = _run_library(monkeypatch, folders, resolve=resolve, only_key=romaji)
    assert len(by_folder) == 1
    assert by_folder[0]["title"] == english
@pytest.mark.parametrize(
    "label,value,expected",
    [
        ("romaji", "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e", True),
        ("punctuation", "Fate/stay night: Unlimited Blade Works", True),
        ("accented latin", "Pok\u00e9mon Kanojo", True),
        # A blocklist of the scripts I thought of let this one straight
        # through, and the drawer captioned Demon Slayer in Arabic.
        ("arabic", "\u0642\u0627\u062a\u0644 \u0627\u0644\u0634\u064a\u0627\u0637\u064a\u0646", False),
        ("kana and kanji", "\u30a2\u30ab\u30e1\u304c\u65ac\u308b!", False),
        ("cyrillic", "\u041a\u043b\u0438\u043d\u043e\u043a", False),
        ("greek", "\u0394\u03b1\u03af\u03bc\u03bf\u03bd\u03b1\u03c2", False),
        ("digits alone", "2017", False),
        ("empty", "", False),
    ],
)
def test_a_second_name_must_be_in_letters_you_can_type(label, value, expected):
    """The name is there to be matched against a release title.

    A script you cannot type does not do that job, whichever script it is.
    """
    from bankai.web.anime_library import _is_romaji

    assert _is_romaji(value) is expected


def test_the_romaji_name_comes_from_the_release_lists_not_tvdb_aliases(monkeypatch):
    """TVDB aliases are translations into every language, in no useful order.

    Picking from them gave Demon Slayer its Arabic name and Classroom of the
    Elite the mouthful naming one specific season. The AniDB list is what
    release groups publish under, and what bankai already searches Nyaa with.
    """
    import asyncio

    from bankai.web import anime_library

    async def related(tvdb_id):
        assert tvdb_id == 1234
        return ["\u30a2\u30ab\u30e1\u304c\u65ac\u308b!", "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"]

    monkeypatch.setattr(anime_library.anime_mapping, "related_titles", related)
    # The Japanese entry is skipped; the first typeable one is taken.
    assert asyncio.run(anime_library.romaji_name(1234)) == (
        "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"
    )
    assert asyncio.run(anime_library.romaji_name(None)) == ""


def test_a_second_name_that_repeats_the_title_is_not_printed(monkeypatch):
    """Akame ga Kill! is already the romaji; printing it twice says nothing.

    Compared through the same normalisation the library groups by, so a
    stray exclamation mark or a trailing year does not sneak a second line
    back in.
    """
    import asyncio

    from bankai.web import anime_library

    async def related(tvdb_id):
        return ["Akame ga Kill!"]

    monkeypatch.setattr(anime_library.anime_mapping, "related_titles", related)
    run = asyncio.run
    assert run(anime_library.second_name(280329, "Akame ga Kill!")) == ""
    assert run(anime_library.second_name(280329, "Akame ga Kill")) == ""
    # A genuinely different name is still worth showing.
    assert run(anime_library.second_name(280329, "Red Eyes Sword")) == "Akame ga Kill!"


def test_the_series_name_beats_the_name_of_one_of_its_seasons(monkeypatch):
    """A card stands for a series, so its second name has to as well.

    Preferring the firsthand Erai name sounded right and was not: Erai
    publishes each season separately, so the Bleach card was labelled with
    one late arc and Classroom of the Elite with its fourth season.
    """
    import asyncio

    from bankai.web import anime_library

    async def related(tvdb_id):
        return ["Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"]

    monkeypatch.setattr(anime_library.anime_mapping, "related_titles", related)
    assert asyncio.run(
        anime_library.second_name(
            329822, "Classroom of the Elite", "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e S3"
        )
    ) == "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"


def test_the_erai_name_is_used_when_the_release_list_has_nothing(monkeypatch):
    import asyncio

    from bankai.web import anime_library

    async def related(tvdb_id):
        return []

    monkeypatch.setattr(anime_library.anime_mapping, "related_titles", related)
    assert asyncio.run(
        anime_library.second_name(1, "Some Show", "Betsu no Namae")
    ) == "Betsu no Namae"


def test_the_erai_name_for_a_series_is_its_shortest_not_its_first():
    """Bleach was labelled with whichever arc came first out of a dict."""
    from bankai.web import anime_library, erai

    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {"title": "[Erai-raws] Bleach: Sennen Kessen Hen - Kashin Tan - 03 [1080p]"},
        "b" * 40: {"title": "[Erai-raws] Bleach - 210 [1080p]"},
        "c" * 40: {"title": "[Erai-raws] Bleach: Sennen Kessen Hen - 01 [1080p]"},
    }
    # The late arc is indexed first, as it was on the real server.
    state["canonical"] = {
        "74796|7|3": {"info_hash": "a" * 40},
        "74796|1|210": {"info_hash": "b" * 40},
        "74796|5|1": {"info_hash": "c" * 40},
    }
    assert anime_library.erai_source_titles(state) == {"74796": "Bleach"}


def test_a_show_with_no_release_list_entry_gets_no_second_name(monkeypatch):
    """Better than captioning it with something unusable."""
    import asyncio

    from bankai.web import anime_library

    async def related(tvdb_id):
        return ["\u30a2\u30ab\u30e1\u304c\u65ac\u308b!"]

    monkeypatch.setattr(anime_library.anime_mapping, "related_titles", related)
    assert asyncio.run(anime_library.romaji_name(4321)) == ""


def test_the_romaji_lookup_survives_the_mapping_list_being_unreachable(monkeypatch):
    """The library must still render when AniDB cannot be fetched."""
    import asyncio

    from bankai.web import anime_library

    async def boom(tvdb_id):
        raise RuntimeError("anime-list.xml unreachable")

    monkeypatch.setattr(anime_library.anime_mapping, "related_titles", boom)
    assert asyncio.run(anime_library.romaji_name(1234)) == ""


def test_erai_name_beats_a_tvdb_alias(monkeypatch):
    """What Erai actually published is better evidence than an alias list."""
    from bankai.web import anime_library, erai

    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {
            "status": "done",
            "title": "[Erai-raws] Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e S3 - 05 [1080p]",
        }
    }
    state["canonical"] = {"337912|3|5": {"info_hash": "a" * 40}}

    titles = anime_library.erai_source_titles(state)
    # The season marker is kept: it is part of how Erai names the show, and
    # the review page shows the same string.
    assert titles == {"337912": "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e S3"}
