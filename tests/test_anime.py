from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from bankai.processor.anime import _atomic_copy2
from bankai.web import anime
from bankai.web.anime import (
    clean_release_title,
    parse_rss,
    release_episode_info,
    split_filter_terms,
)


def test_atomic_anime_copy_hides_final_media_name_until_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "download" / "Bleach.mkv"
    destination = tmp_path / "library" / "Bleach - S17E41.mkv"
    source.parent.mkdir()
    destination.parent.mkdir()
    source.write_bytes(b"episode")
    original_copy2 = __import__("shutil").copy2

    def observing_copy(src: Path, dst: Path) -> None:
        assert dst.suffix == ".part"
        assert not destination.exists()
        original_copy2(src, dst)

    def no_link(src: Path, dst: Path) -> None:
        raise OSError(18, "Invalid cross-device link")

    # Another volume: no hard link, so it is copied.
    monkeypatch.setattr("bankai.processor.anime.os.link", no_link)
    monkeypatch.setattr("bankai.processor.anime.shutil.copy2", observing_copy)

    _atomic_copy2(source, destination)

    assert destination.read_bytes() == b"episode"
    assert list(destination.parent.glob("*.part")) == []


def test_anime_publish_links_on_the_same_volume_instead_of_copying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "download" / "Bleach.mkv"
    destination = tmp_path / "library" / "Bleach - S17E41.mkv"
    source.parent.mkdir()
    destination.parent.mkdir()
    source.write_bytes(b"episode")

    def no_copy(src: Path, dst: Path) -> None:
        raise AssertionError("a full disk has no room for a copy")

    monkeypatch.setattr("bankai.processor.anime.shutil.copy2", no_copy)

    _atomic_copy2(source, destination)

    assert destination.read_bytes() == b"episode"
    assert destination.stat().st_ino == source.stat().st_ino
    assert list(destination.parent.glob("*.part")) == []


def test_a_published_episode_is_dated_when_it_joined_the_library(tmp_path: Path) -> None:
    """Downloaded weeks ago, published now: new to the library, so "recently added" shows it."""
    import os
    import time

    source = tmp_path / "download" / "Bleach.mkv"
    destination = tmp_path / "library" / "Bleach - S17E41.mkv"
    source.parent.mkdir()
    destination.parent.mkdir()
    source.write_bytes(b"episode")
    weeks_ago = time.time() - 30 * 86400
    os.utime(source, (weeks_ago, weeks_ago))

    _atomic_copy2(source, destination)

    assert destination.stat().st_mtime > time.time() - 60


def test_nyaa_rss_parser_preserves_direct_sources_and_metadata() -> None:
    payload = """<?xml version="1.0" encoding="UTF-8" ?>
    <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel><item>
      <title>[SubsPlease] Sousou no Frieren - 01 (1080p) [ABC123].mkv</title>
      <link>https://nyaa.si/download/1234567.torrent</link>
      <guid isPermaLink="true">https://nyaa.si/view/1234567</guid>
      <pubDate>Fri, 29 Sep 2023 15:00:00 -0000</pubDate>
      <nyaa:seeders>321</nyaa:seeders><nyaa:leechers>8</nyaa:leechers>
      <nyaa:downloads>999</nyaa:downloads><nyaa:infoHash>0123456789ABCDEF0123456789ABCDEF01234567</nyaa:infoHash>
      <nyaa:categoryId>1_2</nyaa:categoryId><nyaa:category>Anime - English-translated</nyaa:category>
      <nyaa:size>1.4 GiB</nyaa:size><nyaa:comments>4</nyaa:comments>
      <nyaa:trusted>Yes</nyaa:trusted><nyaa:remake>No</nyaa:remake>
      <description><![CDATA[<p>321 seeders</p>]]></description>
    </item></channel></rss>"""

    entries = parse_rss(payload)

    assert len(entries) == 1
    assert entries[0].publisher == "SubsPlease"
    assert entries[0].quality == "1080p"
    assert entries[0].seeders == 321
    assert entries[0].trusted is True
    assert entries[0].download_url == "https://nyaa.si/download/1234567.torrent"
    assert entries[0].detail_url == "https://nyaa.si/view/1234567"
    assert entries[0].magnet_uri.startswith(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    )


def test_nyaa_detail_preserves_markdown_line_breaks() -> None:
    class Response:
        text = """<div id="torrent-description">### Release\n\n| Episode | 41 |\n| --- | --- |</div>"""

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Client:
        @staticmethod
        async def get(_url: str) -> Response:
            return Response()

    anime._DETAIL_CACHE.clear()
    description, _, _ = asyncio.run(
        anime._detail_url(Client(), "https://nyaa.si/view/markdown-test")
    )

    assert "### Release" in description
    assert "\n" in description
    assert "| Episode | 41 |" in description


def test_anime_filter_terms_are_or_tokens() -> None:
    assert split_filter_terms("German, GER; Deutsch\nDual Audio") == [
        "german",
        "ger",
        "deutsch",
        "dual audio",
    ]


def test_anime_filters_are_case_insensitive() -> None:
    entry = parse_rss(
        """<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel><item>
        <title>[ToonsHub] BLEACH S02E01 German 1080p</title>
        <link>https://nyaa.si/download/1.torrent</link><guid>https://nyaa.si/view/1</guid>
        <nyaa:infoHash>0123456789abcdef0123456789abcdef01234567</nyaa:infoHash>
        <nyaa:seeders>10</nyaa:seeders><nyaa:size>1 GiB</nyaa:size>
        <description>DEUTSCH audio</description>
        </item></channel></rss>"""
    )[0]

    assert anime._matches_filters(
        entry,
        quality="1080P",
        publisher="toonshub",
        title_terms=split_filter_terms("bleach, german"),
        description_terms=split_filter_terms("deutsch"),
        min_seeders=0,
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("[ToonsHub] BLEACH Thousand-Year Blood War S01E41 1080p", (1, 41)),
        ("[Lazier] Bleach Thousand-Year Blood War - 41 (WEB 1080p)", (None, 41)),
        ("Frieren 2nd Season - 10 [1080p]", (2, 10)),
    ],
)
def test_release_episode_info_extracts_common_nyaa_notation(
    title: str, expected: tuple[int | None, int | None]
) -> None:
    assert release_episode_info(title) == expected


def test_release_title_is_cleaned_to_the_show_s_own_name() -> None:
    assert clean_release_title("[SubsPlease] Sousou no Frieren - 27 (1080p) [ABC].mkv") == (
        "Sousou no Frieren"
    )
    assert (
        clean_release_title("[Erai-raws] Sousou no Frieren 2nd Season - 10 [1080p CR WEB-DL]")
        == "Sousou no Frieren"
    )


@pytest.fixture()
def frieren(anilist_data):
    """AniList's title index holding Frieren: a 28-episode season with a sequel."""
    anilist_data.add(154587, "Sousou no Frieren", english="Frieren: Beyond Journey's End", episodes=28,
                     start="2023-09-29", relations=[(182255, "SEQUEL")])
    anilist_data.add(182255, "Sousou no Frieren 2nd Season", episodes=10, start="2026-01-16",
                     relations=[(154587, "PREQUEL")])
    return anilist_data.index()


def _rss_row(title: str):
    return parse_rss(
        f"""<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel><item>
        <title>{title}</title>
        <link>https://nyaa.si/download/1.torrent</link><guid>https://nyaa.si/view/1</guid>
        <nyaa:infoHash>0123456789abcdef0123456789abcdef01234567</nyaa:infoHash>
        <nyaa:seeders>10</nyaa:seeders><nyaa:downloads>20</nyaa:downloads>
        <nyaa:size>1 GiB</nyaa:size>
        </item></channel></rss>"""
    )[0]


def test_a_release_is_identified_on_anilist_from_its_title_alone(frieren) -> None:
    """The title index is on disk: no request is made to tell what a release is."""
    match = anime.anilist_match(
        "[SubsPlease] Sousou no Frieren - 27 (1080p) [ABC].mkv", table=frieren, mappings={}
    )

    assert match == {
        "anilist_id": 154587,
        "title": "Sousou no Frieren",
        "name": "Frieren: Beyond Journey's End",
        "episode": 27,
        "cover": None,
        "format": "TV",
        "year": 2023,
    }


def test_an_episode_past_the_season_s_end_is_the_sequel_s(frieren) -> None:
    match = anime.anilist_match("[SubsPlease] Sousou no Frieren - 30 (1080p).mkv", table=frieren, mappings={})

    assert (match["anilist_id"], match["episode"]) == (182255, 2)


@pytest.mark.parametrize(
    "title",
    [
        # Re:Zero's second season batch was taken for the first season and replaced it (Nov 2026).
        "[Erai-raws] Sousou no Frieren 2nd Season - 01 ~ 10 [1080p][HEVC][BATCH][Multiple Subtitle]",
        "[Group] Sousou no Frieren S2 - 01 ~ 10 (1080p)",
        "[Group] Sousou no Frieren S2 - 03 (1080p)",
    ],
)
def test_a_later_season_s_batch_is_that_season_not_the_first(frieren, title) -> None:
    match = anime.anilist_match(title, table=frieren, mappings={})

    assert match is not None and match["anilist_id"] == 182255


def test_a_first_season_batch_stays_the_first_season(frieren) -> None:
    match = anime.anilist_match("[Erai-raws] Sousou no Frieren - 01 ~ 28 [1080p][Multiple Subtitle]", table=frieren, mappings={})

    assert (match["anilist_id"], match["episode"]) == (154587, 1)


def test_a_name_anilist_does_not_know_is_left_for_the_user_to_place(frieren) -> None:
    assert anime.anilist_match("[Group] Not An Anime - 01 [1080p]", table=frieren, mappings={}) is None


def test_the_user_s_choice_for_a_release_name_wins_over_the_title(frieren) -> None:
    from bankai.web import erai

    title = "[Erai-raws] Frieren Beyond - 03 [1080p]"
    mappings = {erai._mapping_key(title): {"anilist_id": 154587, "anilist_episode_offset": 28}}

    match = anime.anilist_match(title, table=frieren, mappings=mappings)

    assert match is not None
    # Taken as chosen: the offset is the user's, not settled any further.
    assert (match["anilist_id"], match["episode"]) == (154587, 31)


def test_every_release_on_a_nyaa_page_is_identified(monkeypatch: pytest.MonkeyPatch, frieren) -> None:
    """Identifying costs nothing, so no row is left without a try, browsing included."""
    from bankai.web import erai

    sample = _rss_row("[Group] Sousou no Frieren - 01 [1080p]")
    rows = [
        replace(sample, id=index, info_hash=f"{index:040x}", detail_url=f"https://nyaa.si/view/{index}")
        for index in range(1, 76)
    ]

    async def fake_fetch(*args: object, **kwargs: object) -> list[anime.NyaaEntry]:
        return rows

    monkeypatch.setattr(anime, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})

    page = asyncio.run(anime.search(""))

    assert len(page.items) == 75
    assert page.has_next is True
    assert all(item.anilist and item.anilist["anilist_id"] == 154587 for item in page.items)
    # What the API sends: the match under "anilist".
    assert anime.entry_to_dict(page.items[0])["anilist"]["name"] == "Frieren: Beyond Journey's End"


def test_a_search_also_asks_nyaa_under_the_anime_s_other_anilist_names(
    monkeypatch: pytest.MonkeyPatch, frieren
) -> None:
    """People type the English name; Erai publishes under the romaji one."""
    from bankai.web import erai

    asked: list[str] = []

    async def fake_fetch(client, term, category, page, *, user=None) -> list[anime.NyaaEntry]:
        asked.append(term)
        return [_rss_row("[Erai-raws] Sousou no Frieren - 02 [1080p]")]

    monkeypatch.setattr(anime, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})

    page = asyncio.run(anime.search("Frieren: Beyond Journey's End"))

    assert sorted(asked) == sorted(["Frieren: Beyond Journey's End", "Sousou no Frieren"])
    assert page.aliases == ["Frieren: Beyond Journey's End", "Sousou no Frieren"]
    assert page.items[0].anilist["episode"] == 2
    # What was asked is shown under the filters, one address per search.
    assert sorted(page.sent) == sorted(
        anime.feed_url(term, "1_0", 0) for term in ["Frieren: Beyond Journey's End", "Sousou no Frieren"]
    )
    assert page.sent[0].startswith("https://nyaa.si/?page=rss&q=")
    assert anime.entry_to_dict(page.items[0])["source"] == "nyaa"


def test_erai_suffix_deconstruction_keeps_numbers_inside_the_title() -> None:
    title = "[Erai-raws] Anime Name - 2 - 01v2 [1080p][MultiSub][ABCDEF12].mkv"
    assert anime.deconstruct_release(title) == ("Anime Name - 2", 1)
    assert anime.clean_release_title(title) == "Anime Name - 2"
    assert anime.release_episode_info(title) == (None, 1)


def test_a_scene_style_season_pack_is_read_by_its_season_not_the_bracket():
    title = "My Hero Academia S02 1080p NF WEB-DL MULTi AAC2.0 H 264-VARYG (Boku no Hero Academia 4, Multi-Audio, Multi-Subs)"
    assert anime.season_pack_name(title) == "My Hero Academia Season 2"
    assert anime.season_pack_name("[Grp] Some.Show.S01.1080p.BluRay") == "Some Show Season 1"
    assert anime.season_pack_name("Frieren.S01E05.1080p.WEB") is None
    assert anime.season_pack_name("[Erai-raws] Sousou no Frieren - 05 [1080p]") is None


def test_a_publisher_is_asked_of_nyaa_and_matched_without_spaces(monkeypatch):
    """PHT Mini publishes as "[PHTMini]" and is no uploader account; the newest uploads hold few of its releases."""
    import asyncio

    from bankai.web import anime as anime_web

    asked = []

    def entry(title, info_hash):
        return anime_web.NyaaEntry(
            id=1, title=title, download_url="", detail_url="", magnet_uri="", info_hash=info_hash, category_id="1_2",
            category="", size="1 GiB", size_bytes=2**30, seeders=5, leechers=0, downloads=0, comments=0, trusted=False,
            remake=False, published_at=None, publisher=anime_web._publisher(title), quality="1080p",
        )

    async def fake_rss(client, query, category, page, *, user=None):
        asked.append((query, user))
        if user:
            return []
        if "PHTMini" in query:
            return [entry("[PHTMini] Chainsaw Man - S01 (BD 1080p) (German/Deutsch)", "a" * 40)]
        return [entry("[SubsPlease] Chainsaw Man - 01 (1080p)", "b" * 40)]

    async def no_index():
        return None

    monkeypatch.setattr(anime_web, "_fetch_rss", fake_rss)
    monkeypatch.setattr("bankai.metadata.anilist.index", no_index)
    page = asyncio.run(anime_web.search("", publisher="PHT Mini"))
    assert [item.title[:9] for item in page.items] == ["[PHTMini]"]
    assert ("PHT Mini", None) in asked and ("PHTMini", None) in asked and ("", "PHT Mini") in asked


def test_the_publishers_setting_takes_a_list_or_one_per_line():
    from bankai.web.app import _validate_setting_value

    assert _validate_setting_value("anime.publishers", "Ironclad\nPHT Mini\n\nIronclad, Erai-raws") == ["Ironclad", "PHT Mini", "Erai-raws"]
    assert _validate_setting_value("anime.publishers", ["A", " B "]) == ["A", "B"]


def test_a_nyaa_listing_page_is_read_as_the_feed_is():
    """Nyaa's feed ignores the page: from page 2 on the web listing is read."""
    from bankai.web import anime as anime_web

    page = """<table class="torrent-list"><tbody><tr class="success">
      <td><a href="/?c=1_2" title="Anime - English-translated"></a></td>
      <td colspan="2"><a href="/view/9#comments" class="comments">1</a><a href="/view/9" title="[Ironclad] Show - S01E02 [WEB.1080p.AV1]">x</a></td>
      <td><a href="/download/9.torrent"></a><a href="magnet:?xt=urn:btih:BB59A98AB3A9BFF821567F88208C445739B0FC2D&amp;dn=x"></a></td>
      <td>296.5 MiB</td><td data-timestamp="1789244333">2026-09-12</td><td>45</td><td>0</td><td>1641</td></tr></tbody></table>"""
    (entry,) = anime_web.parse_listing(page)
    assert (entry.id, entry.publisher, entry.category_id, entry.seeders, entry.downloads) == (9, "Ironclad", "1_2", 45, 1641)
    assert entry.info_hash == "bb59a98ab3a9bff821567f88208c445739b0fc2d" and entry.trusted and not entry.remake
    assert entry.download_url == "https://nyaa.si/download/9.torrent" and entry.quality == "1080p"


@pytest.fixture()
def shows(anilist_data):
    """A show with a later season split in two parts, a special, and a remake named by its year."""
    anilist_data.add(1, "Shingeki no Kyojin", english="Attack on Titan", episodes=25, start="2013-04-07",
                     relations=[(2, "SEQUEL"), (9, "SIDE_STORY")])
    anilist_data.add(2, "Shingeki no Kyojin Season 2", episodes=12, start="2017-04-01",
                     relations=[(1, "PREQUEL"), (3, "SEQUEL")])
    anilist_data.add(3, "Shingeki no Kyojin Season 3", episodes=12, start="2018-07-23",
                     relations=[(2, "PREQUEL"), (4, "SEQUEL")])
    anilist_data.add(4, "Shingeki no Kyojin Season 3 Part 2", episodes=10, start="2019-04-29",
                     relations=[(3, "PREQUEL"), (5, "SEQUEL")])
    anilist_data.add(5, "Shingeki no Kyojin: The Final Season", episodes=16, start="2020-12-07",
                     relations=[(4, "PREQUEL")])
    anilist_data.add(9, "Shingeki no Kyojin OAD", format="OVA", episodes=8, start="2013-12-09",
                     relations=[(1, "PARENT")])
    anilist_data.add(20, "Hunter x Hunter", episodes=62, start="1999-10-16")
    anilist_data.add(21, "Hunter x Hunter (2011)", episodes=148, start="2011-10-02")
    anilist_data.add(30, "Honzuki no Gekokujou", english="Ascendance of a Bookworm", episodes=14, start="2019-10-03")
    return anilist_data.index()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Other uploaders than Erai: each read as no title at all before (Nov 2026).
        ("[FrixySubs] Shingeki no Kyojin - S02E05v2 [1080p CR WEB-DL H.264 AAC] [Napisy PL].mkv", (2, 5)),
        ("[Judas] Shingeki no Kyojin (Attack on Titan) - S02E07 [1080p][HEVC x265 10bit][Multi-Subs] (Weekly)", (2, 7)),
        ("Attack on Titan S02E06 1080p CR WEB-DL AAC2.0 H.264-VARYG (Shingeki no Kyojin, Multi-Subs)", (2, 6)),
        ("[FuyuPOP] Shingeki no Kyojin - Ep03 (1080p x265 AAC WEB)", (1, 3)),
        ("[ALS] Attack on Titan 08 (1080p BD AAC x264)", (1, 8)),
        ("[AMT] Attack on Titan 01-25 (1080p BD x264 10bit FLAC)", (1, 1)),
        ("[Knight-Subs] Attack on Titan - E26 - Title (1080p CR WEB-DL AAC2.0 H.264).mkv", (2, 1)),
        ("[GJM] Ascendance of a Bookworm (Honzuki no Gekokujou) - 05 (S01E05) (WEB 1080p) [73F346F2].mkv", (30, 5)),
        ("[Group] Some Show S02 / 進撃の巨人 | Attack on Titan Season 2 - 04 [1080p]", (2, 4)),
        ("[Moxie] Attack on Titan Season 2 (BD Remux 1080p x264 8-bit FLAC) [Dual Audio] | Shingeki no Kyojin 2", (2, None)),
        # A season's second part is no season of its own; counted as one, S04 was Season 3 Part 2.
        ("[PHTM] Attack on Titan - S04 (BD 1080p x265 Opus 2.0) | Shingeki no Kyojin (Season 4)", (5, None)),
        # A number past a later season's end counts from the show's first episode.
        ("[ANi] Attack on Titan / 進擊的巨人 Season 2 - 30 [1080P][Baha][WEB-DL]", (2, 5)),
        # A remake named by its year; the bare name is the first.
        ("[sam] Hunter x Hunter (2011) (027-038) (BD 1080p HEVC x265 10-bit FLAC) [Dual-Audio]", (21, 27)),
        ("[VEGETA] Hunter X Hunter (1999) - 01-38", (20, 1)),
        # A special: its own entry, never the show's episode of that number.
        ("[PHTM] Attack on Titan - S00E03 (BD 1080p) | Shingeki no Kyojin OAD", (9, None)),
        ("[Fuchs] Attack on Titan - S00E05 (BD 1080p HEVC Opus 2.0) [Dual-Audio]", None),
    ],
)
def test_release_names_of_every_shape_find_their_entry(shows, title, expected) -> None:
    match = anime.anilist_match(title, table=shows, mappings={})

    assert (match and (match["anilist_id"], match["episode"])) == expected


def test_a_title_s_own_number_is_not_taken_for_the_episode() -> None:
    readings = anime.release_readings("[GSH] Mob Psycho 100 (BD 1080p HEVC FLAC)")

    assert readings[0][0] == "Mob Psycho 100"
    assert anime.release_readings("[Group] Cyberpunk Edgerunners 2 - 06 [1080p]")[0][:3] == ("Cyberpunk Edgerunners 2", None, 6)


def test_a_choice_for_a_name_is_not_taken_for_a_release_uploaded_before_its_entry_began(anilist_data) -> None:
    """Erai names a new season as plainly as the first: the choice made for the new season's
    episodes took the first season's batch, uploaded two years earlier, for the new season."""
    from datetime import datetime, UTC

    from bankai.web import erai

    anilist_data.add(120120, "Tokyo Revengers", episodes=24, start="2021-04-11")
    anilist_data.add(178083, "Tokyo Revengers: Santen Sensou-hen", episodes=12, start="2026-10-03")
    table = anilist_data.index()
    title = "[Erai-raws] Tokyo Revengers - 01 ~ 24 [1080p][Multiple Subtitle]"
    mappings = {erai._mapping_key(title): {"anilist_id": 178083}}

    def uploaded(day: str) -> float:
        return datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()

    assert erai._release_anilist_id(title, mappings=mappings, table=table, published=uploaded("2024-08-08")) == 120120
    assert erai._release_anilist_id(title, mappings=mappings, table=table, published=uploaded("2026-10-04")) == 178083
    assert erai._release_anilist_id(title, mappings=mappings, table=table) == 178083


def test_a_streaming_list_two_seasons_carry_is_the_newest_season_s(anilist_data) -> None:
    """AniList gave Dr. STONE's first season its fourth's streaming list: both tabs read "Ryusui vs. Senku"."""
    from bankai.metadata import anilist

    names = {1: "Ryusui vs. Senku", 2: "Science Journey", 3: "Light Trap in the Darkness"}
    anilist_data.add(105333, "Dr. STONE", episodes=24, start="2019-07-05", streaming=names)
    anilist_data.add(172019, "Dr. STONE SCIENCE FUTURE", episodes=12, start="2025-01-09", streaming=names)
    anilist_data.add(5, "Dr. STONE Movie", format="MOVIE", episodes=1, start="2026-01-01", streaming=names)
    anilist_data.index()

    assert anilist.episodes(172019)[0][1] == "Ryusui vs. Senku"
    assert anilist.episodes(105333)[0][1] is None  # MyAnimeList's title, once fetched


def test_nyaa_asking_to_wait_is_waited_for_then_said_plainly(monkeypatch) -> None:
    import asyncio

    import httpx

    answers = iter([429, 200])
    rss = '<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel></channel></rss>'

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(answers, 429)
        return httpx.Response(code, headers={"Retry-After": "1"}, text=rss if code == 200 else "")

    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(anime.asyncio, "sleep", sleep)

    async def go(limit_all: bool):
        nonlocal answers
        answers = iter([429, 429, 429] if limit_all else [429, 200])
        async with httpx.AsyncClient(base_url="https://nyaa.si", transport=httpx.MockTransport(handler)) as client:
            return await anime._fetch_rss(client, "Frieren", "1_0", 0)

    assert asyncio.run(go(False)) == [] and slept == [1.0]
    with pytest.raises(anime.RateLimited, match="try again"):
        asyncio.run(go(True))
