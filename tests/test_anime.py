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
