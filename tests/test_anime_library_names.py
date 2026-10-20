"""Show identity used to group the anime library."""

from __future__ import annotations

import time
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import pytest

from bankai.web.anime_library import _name


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


def _table(*entries):
    """An AniDB title index of (aid, main title, English title or None)."""
    from bankai.metadata import anidb

    nodes = "".join(
        f'<anime aid="{aid}"><title xml:lang="x-jat" type="main">{escape(main)}</title>'
        + (f'<title xml:lang="en" type="official">{escape(english)}</title>' if english else "")
        + "</anime>"
        for aid, main, english in entries
    )
    return anidb.build_index(ET.fromstring(f"<animetitles>{nodes}</animetitles>"), None)


def _run_library(
    monkeypatch,
    folders,
    *,
    titles=(),
    taken=None,
    policies=None,
    links=None,
    catalog=None,
    only_key=None,
    reads=None,
    places=None,
):
    """group_shows over a library holding ``folders`` -> {folder: [episode, ...]}.

    ``titles`` stands in for AniDB's title list: (aid, main title, English
    title or None). ``taken`` is the release state's ``anime``, the AniDB
    entries the automation took; ``policies`` the user's blacklist; ``links``
    the folders the user linked to an entry, {folder: aid}.
    """
    import asyncio
    from pathlib import Path

    from bankai.metadata import anidb, anidb_art
    from bankai.web import anime_library, erai

    table = _table(*titles) if titles else None

    async def index():
        return table

    state = erai._default_state()
    state["anime"] = dict(taken or {})

    def load_state():
        if reads is not None:
            reads.append(1)
        return state

    monkeypatch.setattr(anidb, "index", index)
    monkeypatch.setattr(erai, "_load_state", load_state)
    monkeypatch.setattr(erai, "_load_policies", lambda: dict(policies or {}))
    monkeypatch.setattr(
        anime_library,
        "load_prefs",
        lambda: {f"folder:{folder}": {"anidb_id": aid} for folder, aid in (links or {}).items()},
    )
    monkeypatch.setattr(anime_library, "probed_codecs", lambda files: {})
    monkeypatch.setattr(anime_library, "german_dubbed_episodes", lambda files: set())
    monkeypatch.setattr(anidb_art, "facts", lambda aid: {})
    monkeypatch.setattr(anidb_art, "cover", lambda aid: None)
    from bankai.web import anime_collections

    monkeypatch.setattr(anime_collections, "load_places", lambda: dict(places or {}))

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
        anime_library.group_shows(entries, Path("/library"), only_key=only_key, catalog=catalog)
    )


ROMAJI = "Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e"
ENGLISH = "Classroom of the Elite"
CLASSROOM = [(12761, ROMAJI, ENGLISH)]


def test_the_release_state_is_read_once_for_the_whole_library(monkeypatch):
    """It is a fourteen megabyte file; reading it per show cost twenty seconds."""
    reads: list[int] = []
    shows = _run_library(
        monkeypatch,
        {f"Show {n}": [f"Show {n} - S01E01.mkv"] for n in range(1, 21)},
        reads=reads,
    )

    assert len(shows) == 20
    # One read, for the cards of entries with nothing on disk, whatever the
    # library holds.
    assert len(reads) == 1


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


def test_two_names_for_one_anime_are_one_card(monkeypatch):
    """A romaji folder beside its English one shares no spelling at all.

    Nothing about the two names can be compared; the AniDB entry both
    folders' names belong to is what says they are the same anime.
    """
    shows = _run_library(
        monkeypatch,
        {
            ROMAJI: ["ep - S01E01.mkv"],
            ENGLISH: ["ep - S01E02.mkv"],
        },
        titles=CLASSROOM,
    )
    assert len(shows) == 1
    assert shows[0]["title"] == ENGLISH
    assert shows[0]["anidb_id"] == 12761
    assert shows[0]["numbering"] == "anidb"
    assert sorted(shows[0]["folders"]) == sorted([ROMAJI, ENGLISH])
    assert shows[0]["episode_count"] == 2


def test_an_entry_the_automation_took_does_not_duplicate_the_folder_it_is_in(monkeypatch):
    """The empty card for a taken entry has to land on its own folder."""
    shows = _run_library(
        monkeypatch,
        {ROMAJI: ["ep - S01E01.mkv"]},
        titles=CLASSROOM,
        taken={"12761": {"title": ROMAJI, "english_title": ENGLISH}},
    )
    assert len(shows) == 1
    assert shows[0]["title"] == ENGLISH
    assert shows[0]["folders"] == [ROMAJI]


def test_shows_without_an_anidb_entry_are_not_merged_on_a_guess(monkeypatch):
    """With no entry there is nothing better than the name; separate is correct."""
    shows = _run_library(
        monkeypatch,
        {
            "Grand Blue": ["a - S01E01.mkv"],
            "Grand Blue Dreaming": ["b - S01E01.mkv"],
        },
    )
    assert len(shows) == 2
    # Their files, as named: no AniDB entry, no tabs of one.
    assert {show["numbering"] for show in shows} == {"files"}
    assert [show["anidb_id"] for show in shows] == [None, None]
    assert [show["season_anidb"] for show in shows] == [{}, {}]


def test_asking_for_one_merged_show_returns_its_folders(monkeypatch):
    """The card is asked for back by its key, one of its folders' names.

    Any other folder merged into it answers too, so a link made before the
    merge survives.
    """
    folders = {ROMAJI: ["ep - S01E01.mkv"], ENGLISH: ["ep - S01E02.mkv"], "Unrelated Show": ["u - S01E01.mkv"]}

    key = next(
        show["key"] for show in _run_library(monkeypatch, folders, titles=CLASSROOM) if show["anidb_id"] == 12761
    )
    by_key = _run_library(monkeypatch, folders, titles=CLASSROOM, only_key=key)
    assert len(by_key) == 1
    assert sorted(by_key[0]["folders"]) == sorted([ROMAJI, ENGLISH])

    other = ENGLISH if key == ROMAJI else ROMAJI
    by_folder = _run_library(monkeypatch, folders, titles=CLASSROOM, only_key=other)
    assert len(by_folder) == 1
    assert by_folder[0]["title"] == ENGLISH


def test_entries_the_automation_took_with_nothing_on_disk_still_get_a_card(monkeypatch):
    """Each from the release state's AniDB entries, unless a folder holds it or
    it was blacklisted -- which is how one removed from the library leaves it.

    Its key is its AniDB name, and never another card's: the page asks for a
    card back by its key. Two entries can share an English title, and an
    entry can share its name with a folder that is another entry.
    """
    titles = [
        (17617, "Sousou no Frieren", "Frieren: Beyond Journey's End"),
        (18886, "Sousou no Frieren (2026)", "Frieren: Beyond Journey's End"),
        (17246, "Kusuriya no Hitorigoto", "The Apothecary Diaries"),
        (17470, "Oshi no Ko", None),
        (1102, "Hunter x Hunter", None),
        (8521, "Hunter x Hunter (2011)", "Hunter x Hunter"),
    ]
    taken = {
        "17246": {"title": "Kusuriya no Hitorigoto"},  # on disk
        "17470": {"title": "Oshi no Ko"},  # blacklisted
        "17617": {"title": "Sousou no Frieren"},
        "18886": {"title": "Sousou no Frieren (2026)"},
        "8521": {"title": "Hunter x Hunter (2011)"},
    }
    library = dict(
        folders={
            "Kusuriya no Hitorigoto": ["Kusuriya no Hitorigoto - S01E01.mkv"],
            "Hunter x Hunter": ["Hunter x Hunter - S01E01.mkv"],
        },
        titles=titles,
        taken=taken,
        policies={"anidb:17470": {"mode": "blacklisted", "anidb_id": 17470}},
        # Two entries are called "Hunter x Hunter"; the folder is the 1999 one.
        links={"Hunter x Hunter": 1102},
    )

    shows = _run_library(monkeypatch, **library)
    by_key = {show["key"]: show for show in shows}

    assert set(by_key) == {
        "Kusuriya no Hitorigoto",
        "Hunter x Hunter",
        "Frieren: Beyond Journey's End",
        # Its English title is taken; its own AniDB title is not.
        "Sousou no Frieren (2026)",
        # Every name it has is the folder's card's, the year aside.
        "Hunter x Hunter (AniDB 8521)",
    }
    assert len(shows) == len(by_key)
    placeholders = [show for show in shows if not show["folders"]]
    assert sorted(show["anidb_id"] for show in placeholders) == [8521, 17617, 18886]
    assert all(show["episode_count"] == 0 and show["numbering"] == "anidb" for show in placeholders)
    assert by_key["Hunter x Hunter"]["anidb_id"] == 1102

    # Each key asks for exactly one card back.
    assert [show["anidb_id"] for show in _run_library(monkeypatch, **library, only_key="Hunter x Hunter")] == [1102]


def test_the_anidb_title_is_shown_under_the_name_only_when_it_says_something_else(monkeypatch):
    """Akame ga Kill! is already the name on the card; printing it twice says nothing.

    Compared through the same normalisation the library groups by, so a
    stray exclamation mark does not sneak a second line back in. A card shown
    by its English title keeps the AniDB one, which is what release groups
    publish under.
    """
    from bankai.web import shoko_catalog

    catalog = shoko_catalog.Catalog(
        entries={8074: shoko_catalog.Entry(aid=8074, title="Akame ga Kill", poster_url=None, episodes=())}
    )
    shows = _run_library(
        monkeypatch,
        {
            "Akame ga Kill": ["Akame ga Kill - S01E01.mkv"],
            "Sousou no Frieren": ["Sousou no Frieren - S01E01.mkv"],
        },
        titles=[(8074, "Akame ga Kill!", None), (17617, "Sousou no Frieren", "Frieren: Beyond Journey's End")],
        catalog=catalog,
    )
    by_id = {show["anidb_id"]: show for show in shows}

    assert by_id[8074]["title"] == "Akame ga Kill"
    assert by_id[8074]["source_title"] == ""
    assert by_id[17617]["title"] == "Frieren: Beyond Journey's End"
    assert by_id[17617]["source_title"] == "Sousou no Frieren"
