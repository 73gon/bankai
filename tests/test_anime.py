from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from bankai.metadata.tvdb import TVDBEpisode
from bankai.processor.anime import episode_identity
from bankai.web import anime
from bankai.web.anime import (
    clean_release_title,
    parse_rss,
    release_episode_info,
    split_filter_terms,
)


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


def test_release_title_is_cleaned_for_tvdb_lookup() -> None:
    assert clean_release_title("[SubsPlease] Sousou no Frieren - 27 (1080p) [ABC].mkv") == (
        "Sousou no Frieren"
    )
    assert (
        clean_release_title("[Erai-raws] Sousou no Frieren 2nd Season - 10 [1080p CR WEB-DL]")
        == "Sousou no Frieren"
    )


def test_short_anime_query_does_not_match_a_word_inside_an_unrelated_title() -> None:
    correct = anime.AnimeTVDBMatch(
        tvdb_id=424536,
        kind="show",
        english_title="Frieren: Beyond Journey's End",
        aliases=("Sousou no Frieren",),
    )
    unrelated = anime.AnimeTVDBMatch(
        tvdb_id=256256,
        kind="movie",
        english_title="Young Ones Are Even Cold in the Summer",
        japanese_title="Kleine frieren auch im Sommer",
    )
    spelling_lookalike = anime.AnimeTVDBMatch(
        tvdb_id=391612,
        kind="show",
        english_title="Labyrinth of Peace",
        japanese_title="Frieden",
    )

    assert anime._match_score("Frieren", correct) > 0.9
    assert anime._match_score("Sousou no Frieren", correct) > 0.9
    assert anime._match_score("Frieren", unrelated) < 0.55
    assert anime._match_score("Frieren", spelling_lookalike) < 0.75


def test_episode_identity_maps_absolute_anime_number_to_tvdb() -> None:
    episodes = [
        TVDBEpisode(season=1, episode=28, absolute_number=28, name="The Height of Magic"),
        TVDBEpisode(season=2, episode=1, absolute_number=29, name="A New Journey"),
    ]

    identity = episode_identity(
        "[Group] Frieren - 29 [1080p].mkv",
        release_title="[Group] Frieren Season 2",
        tvdb_episodes=episodes,
    )

    assert identity is not None
    assert (identity.season, identity.episode, identity.title) == (2, 1, "A New Journey")


def test_episode_identity_uses_tvdb_absolute_order_without_season_hint() -> None:
    episodes = [
        TVDBEpisode(season=1, episode=28, absolute_number=28, name="The Height of Magic"),
        TVDBEpisode(season=2, episode=1, absolute_number=29, name="A New Journey"),
    ]

    identity = episode_identity(
        "[Group] Frieren - 29 [1080p].mkv",
        release_title="[Group] Frieren",
        tvdb_episodes=episodes,
    )

    assert identity is not None
    assert (identity.season, identity.episode, identity.title) == (2, 1, "A New Journey")


def test_nyaa_page_keeps_every_release_when_only_leading_rows_are_enriched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = parse_rss(
        """<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel><item>
        <title>[Group] Anime - 01 [1080p]</title>
        <link>https://nyaa.si/download/1.torrent</link><guid>https://nyaa.si/view/1</guid>
        <nyaa:infoHash>0123456789abcdef0123456789abcdef01234567</nyaa:infoHash>
        <nyaa:seeders>10</nyaa:seeders><nyaa:downloads>20</nyaa:downloads>
        <nyaa:size>1 GiB</nyaa:size>
        </item></channel></rss>"""
    )[0]
    rows = [
        replace(
            sample,
            id=index,
            info_hash=f"{index:040x}",
            detail_url=f"https://nyaa.si/view/{index}",
        )
        for index in range(1, 76)
    ]

    async def fake_fetch(*args: object, **kwargs: object) -> list[anime.NyaaEntry]:
        return rows

    async def fake_enrich(
        entries: list[anime.NyaaEntry], matches: list[anime.AnimeTVDBMatch]
    ) -> list[anime.NyaaEntry]:
        assert len(entries) == 20
        return entries

    monkeypatch.setattr(anime, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(anime, "_enrich_tvdb", fake_enrich)

    page = asyncio.run(anime.search(""))

    assert len(page.items) == 75
    assert page.has_next is True


def test_nonempty_anime_search_enriches_every_visible_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = parse_rss(
        """<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel><item>
        <title>[Group] Bleach - 01 [1080p]</title>
        <link>https://nyaa.si/download/1.torrent</link><guid>https://nyaa.si/view/1</guid>
        <nyaa:infoHash>0123456789abcdef0123456789abcdef01234567</nyaa:infoHash>
        <nyaa:seeders>10</nyaa:seeders><nyaa:downloads>20</nyaa:downloads>
        <nyaa:size>1 GiB</nyaa:size>
        </item></channel></rss>"""
    )[0]
    rows = [replace(sample, id=index, info_hash=f"{index:040x}") for index in range(1, 36)]

    async def fake_tvdb(*args: object, **kwargs: object) -> list[anime.AnimeTVDBMatch]:
        return []

    async def fake_fetch(*args: object, **kwargs: object) -> list[anime.NyaaEntry]:
        return rows

    async def fake_enrich(
        entries: list[anime.NyaaEntry], matches: list[anime.AnimeTVDBMatch]
    ) -> list[anime.NyaaEntry]:
        assert len(entries) == len(rows)
        return entries

    monkeypatch.setattr(anime, "tvdb_candidates", fake_tvdb)
    monkeypatch.setattr(anime, "_fetch_rss", fake_fetch)
    monkeypatch.setattr(anime, "_enrich_tvdb", fake_enrich)

    page = asyncio.run(anime.search("Bleach"))

    assert len(page.items) == len(rows)
