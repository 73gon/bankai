"""Tests for the torrent selector and episode/file matcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bankai.config import SelectorSettings
from bankai.scraper.base import EpisodeRef
from bankai.torrent.matcher import (
    find_video_files,
    match_episodes,
    parse_se,
    pick_movie_file,
)
from bankai.torrent.prowlarr import TorrentCandidate
from bankai.torrent.selector import TorrentSelector
from bankai.torrent.worker import episode_search_queries


def _c(
    title: str,
    *,
    seeders: int = 50,
    size_gib: float = 5.0,
    **extra: Any,
) -> TorrentCandidate:
    return TorrentCandidate(
        title=title,
        indexer="test",
        indexer_id=1,
        download_url="magnet:?xt=urn:btih:" + ("0" * 40),
        info_url=None,
        magnet_uri="magnet:?xt=urn:btih:" + ("0" * 40),
        info_hash=None,
        size_bytes=int(size_gib * (1024**3)),
        seeders=seeders,
        leechers=0,
        publish_date=None,
        **extra,
    )


def test_candidate_parses_release_attrs() -> None:
    c = _c("The.Matrix.1999.1080p.BluRay.x265-FraMeSToR")
    assert c.resolution == "1080p"
    assert c.codec == "x265"
    assert c.source == "BluRay"
    assert c.release_group == "FraMeSToR"


def test_selector_filters_below_min_seeders() -> None:
    s = TorrentSelector(SelectorSettings(min_seeders=10, preferred_resolutions=["1080p"]))
    chosen = s.select([_c("Movie.1080p.x264-X", seeders=2)])
    assert chosen is None


def test_selector_filters_outside_size_bounds() -> None:
    s = TorrentSelector(SelectorSettings(max_size_gib=2.0, preferred_resolutions=["1080p"]))
    chosen = s.select([_c("Movie.1080p.x264-X", size_gib=5.0)])
    assert chosen is None


def test_selector_prefers_higher_resolution() -> None:
    s = TorrentSelector(
        SelectorSettings(
            preferred_resolutions=["2160p", "1080p"],
            preferred_codecs=["x265"],
            preferred_sources=["BluRay"],
            min_seeders=1,
            max_size_gib=200,
        )
    )
    a = _c("Movie.1080p.BluRay.x265-X", seeders=10)
    b = _c("Movie.2160p.BluRay.x265-X", seeders=10)
    chosen = s.select([a, b])
    assert chosen is not None
    assert chosen.candidate is b


def test_selector_release_group_breaks_tie() -> None:
    s = TorrentSelector(
        SelectorSettings(
            preferred_resolutions=["1080p"],
            preferred_codecs=["x265"],
            preferred_sources=["BluRay"],
            preferred_groups=["FraMeSToR", "DON"],
            min_seeders=1,
        )
    )
    a = _c("Movie.1080p.BluRay.x265-FraMeSToR", seeders=10)
    b = _c("Movie.1080p.BluRay.x265-Random", seeders=200)  # more seeders, no group
    chosen = s.select([a, b])
    assert chosen is not None
    assert chosen.candidate is a


def test_parse_se_variants() -> None:
    assert parse_se("Show.S01E02.1080p.mkv") == (1, 2)
    assert parse_se("Show.s1e1.mkv") == (1, 1)
    assert parse_se("Show.1x10.mkv") == (1, 10)
    assert parse_se("Movie.2010.mkv") is None


def test_pick_movie_file_returns_largest(tmp_path: Path) -> None:
    (tmp_path / "small.mkv").write_bytes(b"x" * 100)
    big = tmp_path / "movie.mkv"
    big.write_bytes(b"x" * 10_000)
    (tmp_path / "sample.mp4").write_bytes(b"x" * 500)
    assert pick_movie_file(tmp_path) == big


def test_match_episodes_pairs_files(tmp_path: Path) -> None:
    (tmp_path / "Show.S01E01.mkv").write_bytes(b"a")
    (tmp_path / "Show.S01E02.mkv").write_bytes(b"b")
    (tmp_path / "Show.S01E03.mkv").write_bytes(b"c")
    refs = [
        EpisodeRef(site="x", series_title="Show", season=1, episode=1, title="", url=""),
        EpisodeRef(site="x", series_title="Show", season=1, episode=3, title="", url=""),
    ]
    matched = match_episodes(tmp_path, refs)
    assert len(matched) == 2
    assert matched[0].path.name == "Show.S01E01.mkv"
    assert matched[1].path.name == "Show.S01E03.mkv"


def test_find_video_files_filters_extensions(tmp_path: Path) -> None:
    (tmp_path / "a.mkv").write_bytes(b"")
    (tmp_path / "b.mp4").write_bytes(b"")
    (tmp_path / "c.txt").write_bytes(b"")
    files = find_video_files(tmp_path)
    assert {f.name for f in files} == {"a.mkv", "b.mp4"}


def test_episode_search_queries_prefers_season_pack() -> None:
    qs = episode_search_queries(
        {"query": "Arcane S01E01", "season": 1, "series_title": "Arcane"}
    )
    assert qs == ["Arcane S01", "Arcane S01E01"]


def test_episode_search_queries_derives_series_from_query() -> None:
    qs = episode_search_queries({"query": "Some Show S02E05", "season": 2})
    assert qs[0] == "Some Show S02"
    assert "Some Show S02E05" in qs


def test_episode_search_queries_without_season_is_query_only() -> None:
    qs = episode_search_queries({"query": "Some Show S02E05"})
    assert qs == ["Some Show S02E05"]
