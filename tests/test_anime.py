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

    monkeypatch.setattr("bankai.processor.anime.shutil.copy2", observing_copy)

    _atomic_copy2(source, destination)

    assert destination.read_bytes() == b"episode"
    assert list(destination.parent.glob("*.part")) == []


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


def _frieren_index():
    from xml.etree import ElementTree as ET

    from bankai.metadata import anidb

    return anidb.build_index(
        ET.fromstring(
            """<animetitles>
  <anime aid="17617">
    <title xml:lang="x-jat" type="main">Sousou no Frieren</title>
    <title xml:lang="en" type="official">Frieren: Beyond Journey's End</title>
  </anime>
</animetitles>"""
        ),
        ET.fromstring("<anime-list/>"),
    )


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


def test_a_release_is_identified_on_anidb_from_its_title_alone() -> None:
    """The title index is on disk: no request is made to tell what a release is."""
    match = anime.anidb_match(
        "[SubsPlease] Sousou no Frieren - 27 (1080p) [ABC].mkv", table=_frieren_index(), mappings={}
    )

    assert match is not None
    assert (match["anidb_id"], match["episode"]) == (17617, 27)
    assert match["english_title"] == "Frieren: Beyond Journey's End"


def test_a_name_anidb_does_not_know_is_left_for_the_user_to_place() -> None:
    assert anime.anidb_match("[Group] Not An Anime - 01 [1080p]", table=_frieren_index(), mappings={}) is None


def test_the_user_s_choice_for_a_release_name_wins_over_the_title() -> None:
    from bankai.web import erai

    title = "[Erai-raws] Frieren Beyond - 03 [1080p]"
    mappings = {erai._mapping_key(title): {"anidb_id": 17617, "anidb_episode_offset": 28}}

    match = anime.anidb_match(title, table=_frieren_index(), mappings=mappings)

    assert match is not None
    assert (match["anidb_id"], match["episode"]) == (17617, 31)


def test_every_release_on_a_nyaa_page_is_identified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Identifying costs nothing, so no row is left without a try, browsing included."""
    from bankai.metadata import anidb
    from bankai.web import erai

    sample = _rss_row("[Group] Sousou no Frieren - 01 [1080p]")
    rows = [
        replace(sample, id=index, info_hash=f"{index:040x}", detail_url=f"https://nyaa.si/view/{index}")
        for index in range(1, 76)
    ]
    table = _frieren_index()

    async def fake_fetch(*args: object, **kwargs: object) -> list[anime.NyaaEntry]:
        return rows

    async def index():
        return table

    monkeypatch.setattr(anime, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(anidb, "index", index)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})

    page = asyncio.run(anime.search(""))

    assert len(page.items) == 75
    assert page.has_next is True
    assert all(item.anidb and item.anidb["anidb_id"] == 17617 for item in page.items)


def test_a_search_also_asks_nyaa_under_the_anime_s_other_anidb_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """People type the English name; Erai publishes under the romaji one."""
    from bankai.metadata import anidb
    from bankai.web import erai

    asked: list[str] = []
    table = _frieren_index()

    async def fake_fetch(client, term, category, page) -> list[anime.NyaaEntry]:
        asked.append(term)
        return [_rss_row("[Erai-raws] Sousou no Frieren - 02 [1080p]")]

    async def index():
        return table

    monkeypatch.setattr(anime, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(anidb, "index", index)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})

    page = asyncio.run(anime.search("Frieren: Beyond Journey's End"))

    assert sorted(asked) == sorted(["Frieren: Beyond Journey's End", "Sousou no Frieren"])
    assert page.aliases == ["Frieren: Beyond Journey's End", "Sousou no Frieren"]
    assert page.items[0].anidb["episode"] == 2


def test_erai_suffix_deconstruction_keeps_numbers_inside_the_title() -> None:
    title = "[Erai-raws] Anime Name - 2 - 01v2 [1080p][MultiSub][ABCDEF12].mkv"
    assert anime.deconstruct_release(title) == ("Anime Name - 2", 1)
    assert anime.clean_release_title(title) == "Anime Name - 2"
    assert anime.release_episode_info(title) == (None, 1)
