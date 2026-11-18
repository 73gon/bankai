"""Tests for the torrent selector and episode/file matcher."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from bankai.config import QBittorrentSettings, SelectorSettings, reset_settings_cache
from bankai.queue.worker import PermanentWorkerError
from bankai.scraper.base import EpisodeRef
from bankai.torrent import actions as torrent_actions
from bankai.torrent.matcher import (
    find_video_files,
    match_episodes,
    parse_se,
    pick_movie_file,
)
from bankai.torrent.prowlarr import TorrentCandidate
from bankai.torrent.qbittorrent import QBittorrentClient, TorrentStatus, _to_status
from bankai.torrent.selector import TorrentSelector
from bankai.torrent.worker import TorrentWorker, _is_season_pack, episode_search_queries


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


def test_candidate_parses_runtime_from_indexer_metadata_and_title() -> None:
    assert _c("Movie.2024.1080p", raw={"runtime": 129}).runtime_seconds == 129 * 60
    assert _c("Movie.2024.02:09:00.1080p").runtime_seconds == 129 * 60


def test_selector_filters_below_min_seeders() -> None:
    s = TorrentSelector(SelectorSettings(min_seeders=10, preferred_resolutions=["1080p"]))
    chosen = s.select([_c("Movie.1080p.x264-X", seeders=2)])
    assert chosen is None


def test_selector_filters_outside_size_bounds() -> None:
    s = TorrentSelector(SelectorSettings(max_size_gib=2.0, preferred_resolutions=["1080p"]))
    chosen = s.select([_c("Movie.1080p.x264-X", size_gib=5.0)])
    assert chosen is None


def test_selector_accepts_i_robot_release_with_short_title_word() -> None:
    selector = TorrentSelector(SelectorSettings(min_seeders=1, preferred_resolutions=["1080p"]))

    chosen = selector.select([_c("I.Robot.2004.1080p.BluRay.x264-GROUP")], query="I, Robot 2004")

    assert chosen is not None


def test_active_torrent_state_survives_a_stopped_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    torrent_actions.set_active_torrent("job123", "abc123")

    assert torrent_actions.get_active_torrent("job123") == "abc123"
    torrent_actions.clear_active_torrent("job123")
    assert torrent_actions.get_active_torrent("job123") is None


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


def test_selector_prefers_release_near_source_runtime() -> None:
    s = TorrentSelector(
        SelectorSettings(
            preferred_resolutions=["1080p"],
            min_seeders=1,
            max_size_gib=200,
        )
    )
    close = _c("Movie.2024.1080p.130min-WEB", seeders=10)
    busy_but_wrong_cut = _c("Movie.2024.1080p.95min-WEB", seeders=500)

    chosen = s.select([busy_but_wrong_cut, close], target_runtime_seconds=129 * 60)

    assert chosen is not None
    assert chosen.candidate is close


def test_selector_falls_back_to_most_seeders_when_no_runtime_is_close() -> None:
    s = TorrentSelector(
        SelectorSettings(
            preferred_resolutions=["1080p"],
            preferred_sources=["BluRay"],
            min_seeders=1,
            max_size_gib=200,
        )
    )
    preferred_quality = _c("Movie.2024.1080p.BluRay.95min-GRP", seeders=20)
    healthiest = _c("Movie.2024.1080p.WEB-DL.165min-GRP", seeders=200)

    chosen = s.select([preferred_quality, healthiest], target_runtime_seconds=129 * 60)

    assert chosen is not None
    assert chosen.candidate is healthiest


def test_worker_rechecks_current_min_seeders_before_qbit_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _c("Movie.2024.1080p.WEB-DL-GRP", seeders=10)

    class FakeProwlarr:
        async def search(self, *_args: object, **_kwargs: object) -> list[TorrentCandidate]:
            return [candidate]

    class NoQbitCalls:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"qBittorrent must not be called: {name}")

    # Simulate a selector object created before the user raised the web setting
    # from 1 to 20. The final gate must honor the current value.
    stale_selector = TorrentSelector(
        SelectorSettings(min_seeders=1, preferred_resolutions=["1080p"])
    )
    monkeypatch.setenv("BANKAI_SELECTOR__MIN_SEEDERS", "20")
    monkeypatch.delenv("BANKAI_BG_JOB_ID", raising=False)
    reset_settings_cache()
    worker = TorrentWorker(
        prowlarr=FakeProwlarr(),  # type: ignore[arg-type]
        qbit=NoQbitCalls(),  # type: ignore[arg-type]
        selector=stale_selector,
    )
    ctx = SimpleNamespace(
        job=SimpleNamespace(payload={"query": "Movie 2024", "kind": "movie"}),
        cancel_token=None,
    )

    try:
        with pytest.raises(PermanentWorkerError, match="below minimum seeders"):
            asyncio.run(worker.run(ctx))  # type: ignore[arg-type]
    finally:
        reset_settings_cache()


def test_worker_reports_unavailable_indexers_instead_of_missing_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableProwlarr:
        async def search(self, *_args: object, **_kwargs: object) -> list[TorrentCandidate]:
            return []

        async def indexer_unavailable_reason(self) -> str:
            return "All indexers are unavailable due to failures for more than 6 hours"

    class NoQbitCalls:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"qBittorrent must not be called: {name}")

    monkeypatch.delenv("BANKAI_BG_JOB_ID", raising=False)
    worker = TorrentWorker(
        prowlarr=UnavailableProwlarr(),  # type: ignore[arg-type]
        qbit=NoQbitCalls(),  # type: ignore[arg-type]
    )
    ctx = SimpleNamespace(
        job=SimpleNamespace(payload={"query": "Backrooms 2026", "kind": "movie"}),
        cancel_token=None,
    )

    with pytest.raises(PermanentWorkerError, match="torrent indexers unavailable; rerun later"):
        asyncio.run(worker.run(ctx))  # type: ignore[arg-type]


def test_parse_se_variants() -> None:
    assert parse_se("Show.S01E02.1080p.mkv") == (1, 2)
    assert parse_se("Show.s1e1.mkv") == (1, 1)
    assert parse_se("Show.1x10.mkv") == (1, 10)
    assert parse_se("Movie.2010.mkv") is None


def test_parse_se_reads_four_digit_episodes() -> None:
    """A three-digit cap did not fail on One Piece, it lied.

    "S21E1085" came back as episode 108 -- the real episode 108's key -- so
    the library collapsed dozens of episodes onto a handful of wrong numbers
    and reported the survivors.
    """
    assert parse_se("One Piece - S21E1085.mkv") == (21, 1085)
    assert parse_se("One Piece - S21E0999.mkv") == (21, 999)
    assert parse_se("One Piece - 21x1085.mkv") == (21, 1085)
    # Shorter numbers are untouched.
    assert parse_se("One Piece - S21E108.mkv") == (21, 108)


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
    qs = episode_search_queries({"query": "Arcane S01E01", "season": 1, "series_title": "Arcane"})
    assert qs == ["Arcane S01", "Arcane S01E01"]


def test_season_pack_detection_excludes_single_episode_releases() -> None:
    assert _is_season_pack("Arcane.S02.COMPLETE.1080p", 2) is True
    assert _is_season_pack("Arcane Season 2 1080p", 2) is True
    assert _is_season_pack("Arcane.S02E01.1080p", 2) is False
    assert _is_season_pack("Arcane.S01.COMPLETE.1080p", 2) is False


def test_episode_search_queries_derives_series_from_query() -> None:
    qs = episode_search_queries({"query": "Some Show S02E05", "season": 2})
    assert qs[0] == "Some Show S02"
    assert "Some Show S02E05" in qs


def test_episode_search_queries_without_season_is_query_only() -> None:
    qs = episode_search_queries({"query": "Some Show S02E05"})
    assert qs == ["Some Show S02E05"]


def test_qbittorrent_status_includes_dashboard_metrics() -> None:
    status = _to_status(
        {
            "hash": "abc123",
            "name": "Movie",
            "state": "downloading",
            "progress": 0.42,
            "size": 8_000,
            "dlspeed": 2_000,
            "upspeed": 300,
            "eta": 120,
            "num_seeds": 14,
            "num_leechs": 6,
            "num_complete": 383,
            "num_incomplete": 27,
            "added_on": 1_700_000_000,
        }
    )

    assert status.upspeed == 300
    assert status.seeds == 14
    assert status.peers == 6
    assert status.seeds_total == 383
    assert status.peers_total == 27
    assert status.added_on == 1_700_000_000


def test_qbittorrent_poll_retries_transient_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = QBittorrentClient(QBittorrentSettings(poll_interval_seconds=0))
    complete = TorrentStatus(
        hash="abc",
        name="Fight Club",
        state="uploading",
        progress=1.0,
        save_path="/downloads",
        content_path="/downloads/fight.mkv",
        size_bytes=1,
        dlspeed=0,
        eta=0,
    )
    calls = 0

    async def fake_get(_torrent_hash: str) -> TorrentStatus:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadError("connection dropped")
        return complete

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(client, "get", fake_get)
    monkeypatch.setattr("bankai.torrent.qbittorrent.asyncio.sleep", no_sleep)

    try:
        result = asyncio.run(client.wait_until_complete("abc"))
    finally:
        asyncio.run(client.aclose())

    assert result is complete
    assert calls == 2


def test_qbittorrent_active_worker_can_bypass_prefill_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = QBittorrentClient()
    calls: list[tuple[str, dict[str, str]]] = []

    async def logged_in() -> None:
        return None

    async def post(url: str, *, data: dict[str, str]) -> SimpleNamespace:
        calls.append((url, data))
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(client, "_ensure_login", logged_in)
    monkeypatch.setattr(client._client, "post", post)
    try:
        asyncio.run(client.top_priority("abc"))
        asyncio.run(client.force_start("abc", enabled=True))
        asyncio.run(client.force_start("abc", enabled=False))
    finally:
        asyncio.run(client.aclose())

    assert calls == [
        ("/api/v2/torrents/topPrio", {"hashes": "abc"}),
        ("/api/v2/torrents/setForceStart", {"hashes": "abc", "value": "true"}),
        ("/api/v2/torrents/setForceStart", {"hashes": "abc", "value": "false"}),
    ]


def test_qbittorrent_reports_download_disk_free_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = QBittorrentClient()

    async def logged_in() -> None:
        return None

    async def get(url: str) -> SimpleNamespace:
        assert url == "/api/v2/sync/maindata"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"server_state": {"free_space_on_disk": 12345}},
        )

    monkeypatch.setattr(client, "_ensure_login", logged_in)
    monkeypatch.setattr(client._client, "get", get)
    try:
        assert asyncio.run(client.free_space_bytes()) == 12345
    finally:
        asyncio.run(client.aclose())


def test_the_dub_pipeline_finds_its_torrent_in_its_own_category_not_the_anime_one() -> None:
    """The anime automation, while off, pauses every unfinished torrent of its category as backlog."""
    import asyncio

    from bankai.torrent import worker as worker_mod

    asked: list[str | None] = []

    class FakeQbit:
        async def list_torrents(self, *, category=None):
            asked.append(category)
            return [type("T", (), {"hash": "new", "name": "Film.2026.1080p"})()]

    found = asyncio.run(worker_mod.TorrentWorker(qbit=FakeQbit())._locate_added("Film 2026 1080p", before_hashes=set()))
    assert found == "new"
    assert asked == [worker_mod.CATEGORY] and worker_mod.CATEGORY != "bankai"


def test_a_torrent_whose_progress_stops_is_given_up(monkeypatch) -> None:
    """Modern Family S06E17 sat at 0% for hours: no metadata, no seeder."""
    import asyncio

    from bankai.torrent import qbittorrent as qb

    client = qb.QBittorrentClient.__new__(qb.QBittorrentClient)
    client._settings = type("S", (), {"poll_interval_seconds": 0})()
    status = type("T", (), {"progress": 0.2, "state": "stalledDL", "seeds_total": 0})()

    async def get(_hash):
        return status

    from types import SimpleNamespace

    clock = iter(range(0, 100_000, 1000))
    monkeypatch.setattr(qb, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    client.get = get
    with pytest.raises(qb.TorrentStalled, match="no progress"):
        asyncio.run(client.wait_until_complete("h", stall_seconds=3600))


def test_a_sample_is_never_the_film(tmp_path: Path) -> None:
    # A scene release: the film in RAR archives, the sample clip the only video (Nov 2026).
    (tmp_path / "x.rar").write_bytes(b"0" * 100)
    (tmp_path / "the.mandalorian.and.grogu.2026.german.dl.2160p.web.h265-wayne-sample.mkv").write_bytes(b"0" * 50)
    assert pick_movie_file(tmp_path) is None
    (tmp_path / "Sample").mkdir()
    (tmp_path / "Sample" / "clip.mkv").write_bytes(b"0" * 80)
    film = tmp_path / "The.Sample.2020.1080p.mkv"  # a title with the word in it is a film
    film.write_bytes(b"0" * 60)
    assert pick_movie_file(tmp_path) == film


def test_a_release_whose_link_fails_is_taken_from_another_indexer(monkeypatch) -> None:
    # 1337x disabled in Prowlarr: every grab 429, and it gives no info hash (Nov 2026).
    from types import SimpleNamespace

    from bankai.processor import direct as direct_proc

    def row(title, indexer, magnet=None, info_hash=None, seeders=1):
        return SimpleNamespace(title=title, indexer=indexer, magnet_uri=magnet, info_hash=info_hash, seeders=seeders)

    class Prowlarr:
        async def search(self, query, categories=None):
            return [
                row("Prey 2022 1080p WEB", "Knaben", "magnet:?xt=urn:btih:" + "1" * 40, seeders=90),
                row("Prey 2021 1080p NF WEB-DL German-Eng DDP5 1 H 264-TombDoc", "kickasstorrents.to", info_hash="2" * 40, seeders=3),
                row("Prey 2021 1080p NF WEB-DL German Eng DDP5.1 H.264-TombDoc", "1337x"),
            ]

        async def aclose(self):
            pass

    monkeypatch.setattr("bankai.torrent.prowlarr.ProwlarrClient", Prowlarr)
    found = asyncio.run(direct_proc._elsewhere("Prey 2021 1080p NF WEB-DL German Eng DDP5.1 H.264-TombDoc", "movie"))
    assert found == "magnet:?xt=urn:btih:" + "2" * 40
    assert asyncio.run(direct_proc._elsewhere("Nothing Like It 2020 1080p", "movie")) is None
    assert "apikey=***" in direct_proc._plain(RuntimeError("http://x/6/download?apikey=abc123&link=y"))


def test_a_link_in_a_failure_is_not_read_for_its_words() -> None:
    from bankai.web.reasons import classify_reason

    raw = "HTTPStatusError: Client error '429 Too Many Requests' for url 'http://127.0.0.1:9696/6/download?link=YThKvOe1'"
    assert classify_reason(raw)[0] != "extract"


def test_a_download_waits_for_qbittorrent_to_answer_again(monkeypatch) -> None:
    # Nov 17 2026: qBittorrent did not answer for six minutes and 16 downloads failed after ten tries.
    import httpx

    from bankai.torrent import qbittorrent as qb

    client = qb.QBittorrentClient.__new__(qb.QBittorrentClient)
    client._settings = type("S", (), {"poll_interval_seconds": 0})()
    client._logged_in = True
    answers = iter([httpx.ReadTimeout("slow")] * 40 + [None] * 3 + ["done"])
    done = qb.TorrentStatus(hash="h", name="x", state="uploading", progress=1.0, save_path="/d", content_path="/d/x",
                            size_bytes=1, dlspeed=0, eta=0)

    async def get(torrent_hash):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        if answer == "done":
            return done
        return None

    async def nap(seconds):
        return None

    monkeypatch.setattr(client, "get", get)
    monkeypatch.setattr(qb.asyncio, "sleep", nap)
    assert asyncio.run(client.wait_until_complete("h")) is done
