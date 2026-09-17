from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest

from bankai.backend.transfer import TransferItem, TransferResult
from bankai.config import Settings
from bankai.metadata.tvdb import TVDBEpisode
from bankai.processor import anime as processor
from bankai.web import erai
from bankai.web import jobs as webjobs
from bankai.web.anime import AnimeTVDBMatch, NyaaEntry
from bankai.web.jobs import PendingJob


def entry(
    title: str = "[Erai-raws] Test Show - 01 [1080p][MultiSub]", number: int = 1
) -> NyaaEntry:
    return NyaaEntry(
        id=number,
        title=title,
        download_url=f"https://nyaa.si/download/{number}.torrent",
        detail_url=f"https://nyaa.si/view/{number}",
        magnet_uri=f"magnet:?xt=urn:btih:{number:040x}",
        info_hash=f"{number:040x}",
        category_id="1_2",
        category="Anime",
        size="1 GiB",
        size_bytes=1024**3,
        seeders=10,
        leechers=2,
        downloads=100,
        comments=0,
        trusted=True,
        remake=False,
        published_at="Mon, 01 Jan 2024 00:00:00 +0000",
        publisher="Erai-raws",
        quality="1080p",
    )


@pytest.mark.parametrize(
    "description",
    [
        "Subtitles Info:\nGerman (CR_German) | ASS",
        "English | ASS\nGerman | ASS\nSpanish | ASS",
        "German: SRT",
        "Deutsch | ASS",
        "| **German** | ASS |",
    ],
)
def test_german_evidence_requires_explicit_track(description: str) -> None:
    assert erai.has_explicit_german_subtitles(description)


@pytest.mark.parametrize(
    "description",
    [
        "[MultiSub]",
        "German audio only",
        "No German subtitles",
        "Subtitles Info:\nEnglish | ASS\nGerman: not available",
        "CR_German was removed",
        "English | ASS\nGERMANIUM | ASS",
    ],
)
def test_german_evidence_rejects_unproven_support(description: str) -> None:
    assert not erai.has_explicit_german_subtitles(description)


def listing(item: NyaaEntry) -> str:
    return f"""
    <table><tbody><tr class="success">
      <td>Anime</td><td><a href="/view/{item.id}" title="{item.title}">{item.title}</a></td>
      <td><a href="/download/{item.id}.torrent">torrent</a>
      <a href="{item.magnet_uri}">magnet</a></td>
      <td>1.2 GiB</td><td data-timestamp="1704067200">2024-01-01</td>
      <td>15</td><td>3</td><td>123</td>
    </tr></tbody></table>
    """


def test_historical_listing_preserves_uploader_quality_hash_and_peers() -> None:
    parsed = erai.parse_listing(listing(entry()))[0]
    assert parsed.info_hash == entry().info_hash
    assert parsed.download_url == entry().download_url
    assert parsed.publisher == "Erai-raws"
    assert parsed.trusted
    assert parsed.quality == "1080p"
    assert (parsed.seeders, parsed.leechers, parsed.downloads) == (15, 3, 123)
    assert parsed.published_at == "2024-01-01T00:00:00+00:00"


def test_quality_key_groups_encodes_but_keeps_episode_identity() -> None:
    low = replace(entry(), title="[Erai-raws] Test Show - 01 [720p][MultiSub]", quality="720p")
    high = replace(entry(), title="[Erai-raws] Test Show - 01 [1080p][HEVC][MultiSub]")
    assert erai._release_key(low) == erai._release_key(high)
    assert erai._rank(high) > erai._rank(low)
    assert erai._release_key(entry("[Erai-raws] Test Show - 02 [1080p]")) != erai._release_key(high)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("[Erai-raws] Test Show - 01 [1080p][HEVC][MultiSub]", True),
        ("[Erai-raws] Test Show - 01 [1080p][x265][MultiSub]", True),
        ("[Erai-raws] Test Show - 01 [1080p][H.265][MultiSub]", True),
        ("[Erai-raws] Test Show - 01 [1080p][H265][MultiSub]", True),
        ("[Erai-raws] Test Show - 01 [1080p][h 265][MultiSub]", True),
        ("[Erai-raws] Test Show - 01 [1080p][MultiSub]", False),
        ("[Erai-raws] Test Show - 01 [1080p][x264][MultiSub]", False),
        ("[Erai-raws] Test Show - 01 [1080p][AVC][MultiSub]", False),
        # Substrings of unrelated words must not read as a codec marker.
        ("[Erai-raws] Shevchenko - 01 [1080p][MultiSub]", False),
    ],
)
def test_hevc_detection_accepts_only_explicit_codec_markers(title: str, expected: bool) -> None:
    assert erai._is_hevc(entry(title)) is expected


def test_non_hevc_release_is_filtered_before_any_network_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The codec gate must run before the Nyaa detail fetch.

    A detail lookup per rejected release would mean one request for every AVC
    upload Erai-raws publishes, which is most of the feed.
    """
    state = erai._default_state()
    avc = entry("[Erai-raws] Test Show - 01 [1080p][MultiSub]")

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a filtered release must not be looked up")

    monkeypatch.setattr(erai.anime_mod, "_detail_url", fail)
    monkeypatch.setattr(erai, "get_settings", lambda: Settings())

    assert asyncio.run(erai._consider(state, avc, object())) is False
    record = state["releases"][avc.info_hash]
    assert record["status"] == "filtered"
    assert "HEVC" in record["reason"]
    # A filtered release is a policy decision, not a retryable hold.
    assert not [item for item in state["held"] if item.get("info_hash") == avc.info_hash]


def test_backfill_indexes_hevc_only_and_asks_nyaa_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hevc = entry("[Erai-raws] Test Show - 01 [1080p][HEVC][MultiSub]")
    avc = entry("[Erai-raws] Other Show - 01 [1080p][MultiSub]", 2)
    responses = iter(["<table/>", listing(hevc) + listing(avc), "<table/>"])

    class Client:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str) -> httpx.Response:
            self.urls.append(url)
            return httpx.Response(200, text=next(responses), request=httpx.Request("GET", url))

    async def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(erai, "get_settings", lambda: Settings())
    monkeypatch.setattr(erai.asyncio, "sleep", no_sleep)
    state = erai._default_state()
    client = Client()
    asyncio.run(erai._crawl_backfill(state, client))

    indexed = [row["title"] for row in state["backfill"]["catalog_1080"].values()]
    assert indexed == [hevc.title]
    # Narrowing the query itself keeps the crawl from paging through AVC results.
    assert all("HEVC" in url for url in client.urls)


def test_ordered_candidates_drop_non_hevc_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """The back-catalogue sweep must not resurrect AVC episodes.

    RSS is gated at ingestion, but ordering re-reads the series catalogue,
    so the codec policy has to hold on that path too.
    """
    settings = Settings(anime={"backfill_enabled": False, "backfill_request_delay_seconds": 1})
    state = erai._default_state()
    newest = entry("[Erai-raws] Test Show - 12 [1080p][HEVC]", 12)
    hevc = entry("[Erai-raws] Test Show - 01 [1080p][HEVC]", 1)
    avc = entry("[Erai-raws] Test Show - 02 [1080p]", 2)

    class Client:
        async def get(self, url):
            html = listing(hevc) + listing(avc) + listing(newest) if "1080p" in url else ""
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

    async def no_wait(seconds):
        pass

    async def no_parts(title):
        return []

    monkeypatch.setattr(erai, "get_settings", lambda: settings)
    monkeypatch.setattr(erai.asyncio, "sleep", no_wait)
    monkeypatch.setattr(erai.anime_mapping, "anidb_parts", no_parts)
    result = asyncio.run(
        erai._ordered_candidates(state, {erai._release_key(newest): newest}, Client())
    )
    episodes = [erai.anime_mod.release_episode_info(row.title)[1] for row in result]
    assert 1 in episodes  # the HEVC back-catalogue episode is swept in
    assert 2 not in episodes  # the AVC one never is


def test_backfill_finishes_all_high_quality_passes_before_720_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high = entry("[Erai-raws] Test Show - 01 [1080p][HEVC][MultiSub]")
    low_duplicate = replace(
        high, id=2, title="[Erai-raws] Test Show - 01 [720p][HEVC]", quality="720p"
    )
    fallback = replace(
        entry(number=3), title="[Erai-raws] Other Show - 01 [720p][HEVC]", quality="720p"
    )
    responses = iter(["<table/>", listing(high), listing(low_duplicate) + listing(fallback)])

    class Client:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str) -> httpx.Response:
            self.urls.append(url)
            return httpx.Response(200, text=next(responses), request=httpx.Request("GET", url))

    async def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(erai, "get_settings", lambda: Settings())
    monkeypatch.setattr(erai.asyncio, "sleep", no_sleep)
    state = erai._default_state()
    client = Client()
    asyncio.run(erai._crawl_backfill(state, client))
    assert state["backfill"]["complete"]
    assert len(state["backfill"]["catalog_1080"]) == 1
    assert len(state["backfill"]["catalog_720"]) == 1
    assert "Other Show" in next(iter(state["backfill"]["catalog_720"].values()))["title"]
    assert "q=2160p" in client.urls[0]
    assert "q=1080p" in client.urls[1]
    assert "q=720p" in client.urls[2]


def test_reserve_stops_network_and_new_downloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(anime={"enabled": True}, metadata={"tvdb_api_key": "test"})
    monkeypatch.setattr(erai, "get_settings", lambda: settings)
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    monkeypatch.setattr(erai, "free_space_gib", lambda: 100.0)

    async def forbidden(_client: object) -> list:
        pytest.fail("RSS must not be queried when the storage reserve is reached")

    monkeypatch.setattr(erai, "_fetch_rss", forbidden)
    result = asyncio.run(erai.run_cycle())
    assert result["paused"]
    assert result["last_enqueued"] == 0
    assert "reserve" in result["pause_reason"]


def test_tvdb_ambiguity_is_held_instead_of_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def candidates(_query: str, **_kwargs: object) -> list[AnimeTVDBMatch]:
        return [AnimeTVDBMatch(1, "show", "Test Show"), AnimeTVDBMatch(2, "show", "Test Show")]

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    match, identity, error = asyncio.run(erai._resolve(entry()))
    assert match is None and identity is None
    assert error == "TVDB match is ambiguous"


def test_tvdb_fabricated_episode_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def candidates(_query: str, **_kwargs: object) -> list[AnimeTVDBMatch]:
        return [AnimeTVDBMatch(1, "show", "Test Show")]

    async def episodes(_id: int) -> list[TVDBEpisode]:
        return [TVDBEpisode(1, 1, 1, "First")]

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(processor, "_tvdb_episode_map", episodes)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    match, identity, error = asyncio.run(erai._resolve(entry("[Erai-raws] Test Show - 99 [1080p]")))
    assert match is None and identity is None
    assert "TVDB ordering" in error


def test_manual_mapping_is_durable_and_unblocks_future_episodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    erai.save_mapping(entry().title, AnimeTVDBMatch(123, "show", "Canonical Show", year=2024))
    assert erai._load_mappings()[erai._mapping_key(entry().title)]["tvdb_id"] == 123
    next_episode = entry("[Erai-raws] Test Show - 02 [1080p]")
    assert erai._mapping_key(next_episode.title) == erai._mapping_key(entry().title)


def test_state_merges_backfill_defaults_for_older_ledgers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "erai.json"
    path.write_text(json.dumps({"backfill": {"phase": "1080", "page": 2}}))
    monkeypatch.setattr(erai, "_state_path", lambda: path)
    state = erai._load_state()
    assert state["backfill"]["page"] == 2
    assert state["backfill"]["catalog_720"] == {}


def test_pending_anime_and_normal_queues_are_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    anime = webjobs.PendingJob("anime", "show", "Anime", ["anime-download"], created_at=1)
    movie = webjobs.PendingJob("movie", "movie", "Movie", ["run"], created_at=2)
    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [anime, movie])
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: [])
    normal = webjobs.snapshot()
    separate = webjobs.anime_snapshot()
    assert [row["id"] for row in normal] == ["movie"]
    assert [row["id"] for row in separate] == ["anime"]
    assert separate[0]["queue_total"] == 1


def test_anime_snapshot_checks_shared_storage_reserve_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = [
        webjobs.PendingJob(
            str(index),
            "show",
            f"Anime {index}",
            ["anime-download", "--require-german-subtitles"],
            created_at=index,
        )
        for index in range(50)
    ]
    checks = 0

    def storage_ready(_args: list[str] | None) -> bool:
        nonlocal checks
        checks += 1
        return False

    monkeypatch.setattr(webjobs, "reconcile", lambda: 0)
    monkeypatch.setattr(webjobs, "_load_pending", lambda: pending)
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: [])
    monkeypatch.setattr(webjobs, "_anime_storage_ready", storage_ready)
    rows = webjobs.anime_snapshot()
    assert len(rows) == 50
    assert checks == 1
    assert {row["step_label"] for row in rows} == {"Waiting for Anime storage reserve"}


def test_reserve_keeps_auto_anime_pending_without_blocking_movies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anime = webjobs.PendingJob(
        "anime", "show", "Anime", ["anime-download", "--require-german-subtitles"]
    )
    movie = webjobs.PendingJob("movie", "movie", "Movie", ["run"])
    saved: list[webjobs.PendingJob] = []
    spawned: list[str] = []
    monkeypatch.setattr(webjobs, "get_settings", lambda: Settings(web={"max_concurrent_jobs": 1}))
    monkeypatch.setattr(webjobs, "_load_pending", lambda: [anime, movie])
    monkeypatch.setattr(webjobs, "_save_pending", lambda items: saved.extend(items))
    monkeypatch.setattr(webjobs.bgjobs, "list_jobs", lambda: [])
    monkeypatch.setattr(webjobs.bgjobs, "spawn", lambda **kwargs: spawned.append(kwargs["title"]))
    monkeypatch.setattr(erai, "free_space_gib", lambda: 99.0)
    assert webjobs.reconcile() == 1
    assert spawned == ["Movie"]
    assert [item.id for item in saved] == ["anime"]


@pytest.mark.parametrize(
    "stream,expected",
    [
        ({"codec_type": "subtitle", "tags": {"language": "deu"}}, True),
        ({"codec_type": "subtitle", "tags": {"title": "CR_German"}}, True),
        ({"codec_type": "audio", "tags": {"language": "ger"}}, False),
        ({"codec_type": "subtitle", "tags": {"title": "Audio Description"}}, False),
        ({"codec_type": "subtitle", "tags": {"language": "eng"}}, False),
    ],
)
def test_post_download_german_check_only_accepts_subtitles(
    stream: dict,
    expected: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"video")
    monkeypatch.setattr("bankai.web.media.ffprobe_bin", lambda: "ffprobe")
    monkeypatch.setattr(
        processor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"streams": [stream]}),
            stderr="",
        ),
    )
    assert processor._has_german_subtitles(source) is expected


def test_autonomous_anime_transfers_video_and_german_sidecar_without_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    download = tmp_path / "downloads"
    download.mkdir()
    source = download / "Test Show - 01.mkv"
    source.write_bytes(b"video")
    source.with_suffix(".de.ass").write_text("German subtitles")
    target = tmp_path / "shows_anime" / "Test Show" / "Season 01" / "Test Show - S01E01.mkv"
    settings = Settings(
        output={"directory": tmp_path / "staging"},
        paths={"cleanup_after_success": False},
        transfer={"anime_shows_dir": target.parent.parent.parent},
    )
    monkeypatch.setattr(processor, "get_settings", lambda: settings)
    monkeypatch.setattr(processor, "_has_german_subtitles", lambda _path: True)
    monkeypatch.delenv("BANKAI_BG_JOB_ID", raising=False)

    async def episodes(_id: int) -> list[TVDBEpisode]:
        return [TVDBEpisode(1, 1, 1, "First")]

    async def locate(*_args: object, **_kwargs: object) -> str:
        return entry().info_hash

    class Qbit:
        async def top_priority(self, torrent_hash: str) -> None:
            assert torrent_hash == entry().info_hash

        async def login(self) -> None:
            pass

        async def list_torrents(self, **_kwargs: object) -> list:
            return []

        async def add(self, **_kwargs: object) -> None:
            pass

        async def resume(self, _hash: str) -> None:
            pass

        async def force_start(self, torrent_hash: str, *, enabled: bool) -> None:
            assert torrent_hash == entry().info_hash

        async def aclose(self) -> None:
            pass

        async def wait_until_complete(self, _hash: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                content_path=str(source), save_path=str(download), name=source.name
            )

    def transfer(paths: list[Path], **_kwargs: object) -> TransferResult:
        assert target.with_suffix(".de.ass").exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(paths[0], target)
        return TransferResult(transferred=[TransferItem(paths[0], target, "anime")])

    monkeypatch.setattr(processor, "QBittorrentClient", Qbit)
    monkeypatch.setattr(processor, "_locate_torrent", locate)
    monkeypatch.setattr(processor, "_tvdb_episode_map", episodes)
    monkeypatch.setattr(
        "bankai.backend.transfer.plan_transfer",
        lambda paths, **kwargs: [TransferItem(paths[0], target, "anime")],
    )
    monkeypatch.setattr("bankai.backend.transfer.transfer_with_rsync", transfer)
    monkeypatch.setattr(processor.review, "set_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(processor.review, "set_sources", lambda *args, **kwargs: None)
    monkeypatch.setattr(processor.review, "set_transfer", lambda *args, **kwargs: None)

    result = asyncio.run(
        processor.download_anime(
            release_title=entry().title,
            torrent_url=entry().download_url,
            detail_url=entry().detail_url,
            magnet_uri=entry().magnet_uri,
            info_hash=entry().info_hash,
            media_kind="show",
            tvdb_id=123,
            english_title="Test Show",
            year=2024,
            season_override=1,
            episode_override=1,
            require_german_subtitles=True,
        )
    )
    assert result["final_path"] == str(target)
    assert target.read_bytes() == b"video"
    assert target.with_suffix(".de.ass").read_text() == "German subtitles"


@pytest.mark.parametrize(("cleanup_torrent", "should_remove"), [(True, True), (False, False)])
def test_prefilled_anime_torrent_cleanup_respects_automation_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_torrent: bool,
    should_remove: bool,
) -> None:
    """Discovery adds the torrent long before the job runs.

    Cleanup used to be skipped for any torrent already present in
    qBittorrent, which is every prefilled release, so finished downloads
    piled up until the download volume hit the reserve and discovery
    stopped admitting work.
    """
    download = tmp_path / "downloads"
    download.mkdir()
    source = download / "Test Show - 01.mkv"
    source.write_bytes(b"video")
    target = tmp_path / "shows_anime" / "Test Show" / "Season 01" / "Test Show - S01E01.mkv"
    settings = Settings(
        output={"directory": tmp_path / "staging"},
        paths={"cleanup_after_success": True},
        transfer={"anime_shows_dir": target.parent.parent.parent},
    )
    monkeypatch.setattr(processor, "get_settings", lambda: settings)
    monkeypatch.delenv("BANKAI_BG_JOB_ID", raising=False)
    removed: list[tuple[str, bool]] = []

    async def episodes(_id: int) -> list[TVDBEpisode]:
        return [TVDBEpisode(1, 1, 1, "First")]

    async def locate(*_args: object, **_kwargs: object) -> str:
        return entry().info_hash

    class Qbit:
        async def login(self) -> None:
            pass

        async def list_torrents(self, **_kwargs: object) -> list:
            # The discovery prefill already queued this exact release.
            return [SimpleNamespace(hash=entry().info_hash, progress=1.0, size_bytes=1)]

        async def add(self, **_kwargs: object) -> None:
            pass

        async def top_priority(self, _hash: str) -> None:
            pass

        async def resume(self, _hash: str) -> None:
            pass

        async def force_start(self, _hash: str, *, enabled: bool) -> None:
            pass

        async def remove(self, torrent_hash: str, *, delete_files: bool = False) -> None:
            removed.append((torrent_hash, delete_files))

        async def aclose(self) -> None:
            pass

        async def wait_until_complete(self, _hash: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                content_path=str(source), save_path=str(download), name=source.name
            )

    monkeypatch.setattr(processor, "QBittorrentClient", Qbit)
    monkeypatch.setattr(processor, "_locate_torrent", locate)
    monkeypatch.setattr(processor, "_tvdb_episode_map", episodes)
    monkeypatch.setattr(processor.review, "set_stage", lambda *args, **kwargs: None)
    monkeypatch.setattr(processor.review, "set_sources", lambda *args, **kwargs: None)

    asyncio.run(
        processor.download_anime(
            release_title=entry().title,
            torrent_url=entry().download_url,
            detail_url=entry().detail_url,
            magnet_uri=entry().magnet_uri,
            info_hash=entry().info_hash,
            media_kind="show",
            tvdb_id=123,
            english_title="Test Show",
            year=2024,
            season_override=1,
            episode_override=1,
            cleanup_torrent=cleanup_torrent,
        )
    )
    assert removed == ([(entry().info_hash, True)] if should_remove else [])


def test_cleanup_sweep_removes_only_verified_completed_automation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = tmp_path / "published.mkv"
    published.write_bytes(b"published")
    hashes = {
        "verified": f"{1:040x}",
        "manual": f"{2:040x}",
        "missing": f"{3:040x}",
        "failed": f"{4:040x}",
        "incomplete": f"{5:040x}",
    }

    def job(name: str, *, marker: bool = True, status: str = "done", final: Path = published):
        args = ["anime-download", "--info-hash", hashes[name]]
        if marker:
            args.append("--require-german-subtitles")
        return SimpleNamespace(status=status, args=args, final_path=str(final))

    monkeypatch.setattr(
        erai.bgjobs,
        "list_jobs",
        lambda: [
            job("verified"),
            job("manual", marker=False),
            job("missing", final=tmp_path / "missing.mkv"),
            job("failed", status="failed"),
            job("incomplete"),
        ],
    )
    torrents = [
        SimpleNamespace(hash=value, name=name, progress=0.5 if name == "incomplete" else 1.0)
        for name, value in hashes.items()
    ]
    removed: list[tuple[str, bool]] = []

    class Qbit:
        async def remove(self, info_hash: str, *, delete_files: bool) -> None:
            removed.append((info_hash, delete_files))

    count = asyncio.run(erai._cleanup_verified_torrents(Qbit(), torrents))
    assert count == 1
    assert removed == [(hashes["verified"], True)]


def test_capped_search_splits_complementary_branches_without_claiming_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = entry("[Erai-raws] Alpha Show - 01 [1080p]")
    second = entry("[Erai-raws] Beta Show - 01 [1080p]", 2)
    capped = (
        listing(first)
        + listing(second)
        + '<div class="pagination-page-info">Displaying results 1-75 out of 1000 results.</div>'
    )
    responses = iter([capped, "<table/>", "<table/>"])

    class Client:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def get(self, url: str) -> httpx.Response:
            self.urls.append(url)
            return httpx.Response(200, text=next(responses), request=httpx.Request("GET", url))

    async def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(erai.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(erai, "get_settings", lambda: Settings())
    state = erai._default_state()
    client = Client()
    asyncio.run(erai._crawl_backfill(state, client))
    assert state["backfill"]["queries_split"] == 1
    assert not state["backfill"]["complete"]
    assert state["backfill"]["catalog_1080"] == {}
    from urllib.parse import parse_qs, urlparse

    positive = parse_qs(urlparse(client.urls[1]).query)["q"][0]
    negative = parse_qs(urlparse(client.urls[2]).query)["q"][0]
    assert positive.replace(' "', ' -"') == negative


def test_listing_ignores_comment_link_titles() -> None:
    html = listing(entry()).replace(
        '<td><a href="/view/1"',
        '<td><a href="/view/1#comments" title="1 comment">1</a><a href="/view/1"',
    )
    parsed = erai.parse_listing(html)
    assert parsed[0].title == entry().title


def test_automatic_tvdb_match_does_not_accept_extra_title_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def candidates(_query: str, **_kwargs: object) -> list[AnimeTVDBMatch]:
        return [AnimeTVDBMatch(1, "show", "Test Show: A Different Series")]

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    match, identity, error = asyncio.run(erai._resolve(entry()))
    assert match is None and identity is None
    assert error == "No confident TVDB match"


def test_series_name_cannot_prove_sidecar_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "German Story - 01.mkv"
    source.write_bytes(b"video")
    source.with_suffix(".eng.ass").write_text("English subtitles")
    monkeypatch.setattr("bankai.web.media.ffprobe_bin", lambda: "ffprobe")
    monkeypatch.setattr(
        processor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"streams":[]}', stderr=""),
    )
    assert not processor._has_german_subtitles(source)
    source.with_suffix(".de.ass").write_text("German subtitles")
    assert processor._has_german_subtitles(source)


def test_backfill_follows_actual_nyaa_next_page_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = erai._default_state()
    queries = []
    html = (
        listing(entry("[Erai-raws] Test Show - 01 [2160p]"))
        + '<div class="pagination-page-info">Displaying results 1-75 out of 150 results.</div>'
        + '<ul class="pagination"><li class="next"><a href="/?p=2">Next</a></li></ul>'
    )

    class Client:
        async def get(self, url: str) -> SimpleNamespace:
            queries.append(url)
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

    async def no_wait(_seconds: float) -> None:
        pass

    monkeypatch.setattr(erai.asyncio, "sleep", no_wait)
    asyncio.run(erai._crawl_backfill(state, Client()))
    assert state["backfill"]["phase"] == "2160"
    assert state["backfill"]["frontier"][0]["page"] == 4
    assert not state["backfill"]["complete"]
    assert len(queries) == 3


def test_one_season_resolution_does_not_need_xem(monkeypatch: pytest.MonkeyPatch) -> None:
    async def candidates(*args, **kwargs):
        return [AnimeTVDBMatch(1, "show", "Test Show")]

    async def episodes(_id):
        return [TVDBEpisode(1, 1), TVDBEpisode(1, 2)]

    async def forbidden(*args):
        raise AssertionError("one-season shows do not need mapping")

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(processor, "_tvdb_episode_map", episodes)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai.anime_mapping, "mapped_episode", forbidden)
    match, identity, error = asyncio.run(erai._resolve(entry()))
    assert match is not None and not error and (identity.season, identity.episode) == (1, 1)


def test_named_bleach_part_uses_published_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    title = "Bleach: Sennen Kessen Hen - Ketsubetsu Tan"

    async def candidates(*args, **kwargs):
        return [AnimeTVDBMatch(74796, "show", "Bleach", aliases=(title,))]

    async def episodes(_id):
        return [TVDBEpisode(1, 1), TVDBEpisode(17, 14)]

    async def mapped(*args):
        return (17, 14), True

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(processor, "_tvdb_episode_map", episodes)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai.anime_mapping, "mapped_episode", mapped)
    match, identity, error = asyncio.run(
        erai._resolve(entry("[Erai-raws] " + title + " - 01 [1080p]"))
    )
    assert match is not None and not error and (identity.season, identity.episode) == (17, 14)


def test_named_multiseason_alias_without_mapping_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    async def candidates(*args, **kwargs):
        return [AnimeTVDBMatch(74796, "show", "Bleach", aliases=("Bleach Calamity",))]

    async def episodes(_id):
        return [TVDBEpisode(1, 1, 1), TVDBEpisode(17, 41, 407)]

    async def mapped(*args):
        return None, False

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(processor, "_tvdb_episode_map", episodes)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai.anime_mapping, "mapped_episode", mapped)
    match, identity, error = asyncio.run(
        erai._resolve(entry("[Erai-raws] Bleach Calamity - 01 [1080p]"))
    )
    assert match is None and identity is None and "no season was guessed" in error


def test_rss_reads_configured_nyaa_url(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = Settings(anime={"rss_url": "https://nyaa.si/?u=Erai-raws&page=rss&c=1_2"})
    seen = []

    class Client:
        async def get(self, url):
            seen.append(url)
            return SimpleNamespace(text="<rss><channel /></rss>", raise_for_status=lambda: None)

    monkeypatch.setattr(erai, "get_settings", lambda: policy)
    assert asyncio.run(erai._fetch_rss(Client())) == []
    assert seen == [policy.anime.rss_url]


def test_series_ordering_indexes_older_episodes_even_without_global_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(anime={"backfill_enabled": False, "backfill_request_delay_seconds": 1})
    state = erai._default_state()
    newest = entry("[Erai-raws] Test Show - 12 [1080p][HEVC]", 12)
    first = entry("[Erai-raws] Test Show - 01 [1080p][HEVC]", 1)

    class Client:
        async def get(self, url):
            html = listing(first) + listing(newest) if "1080p" in url else ""
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

    async def no_wait(seconds):
        pass

    async def no_parts(title):
        return []

    monkeypatch.setattr(erai, "get_settings", lambda: settings)
    monkeypatch.setattr(erai.asyncio, "sleep", no_wait)
    monkeypatch.setattr(erai.anime_mapping, "anidb_parts", no_parts)
    result = asyncio.run(
        erai._ordered_candidates(state, {erai._release_key(newest): newest}, Client())
    )
    assert [erai.anime_mod.release_episode_info(row.title)[1] for row in result] == [1, 12]


def test_named_parts_wait_for_parent_catalogue_then_follow_tvdb_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xml.etree import ElementTree as ET

    settings = Settings(anime={"backfill_enabled": False, "backfill_request_delay_seconds": 1})
    state = erai._default_state()
    earlier = entry("[Erai-raws] Bleach Ketsubetsu - 01 [1080p][HEVC]", 1)
    latest = entry("[Erai-raws] Bleach Kashin - 01 [1080p][HEVC]", 2)

    async def parts(title):
        if title not in {"Bleach Ketsubetsu", "Bleach Kashin"}:
            return []
        offset = 13 if title == "Bleach Ketsubetsu" else 40
        record = ET.fromstring(f'<anime defaulttvdbseason="17" episodeoffset="{offset}" />')
        return [erai.anime_mapping.Part(74796, offset, record)]

    async def titles(tvdb_id):
        return ["Bleach Ketsubetsu", "Bleach Kashin"]

    class Client:
        async def get(self, url):
            html = ""
            if "1080p" in url:
                html = listing(earlier if "Ketsubetsu" in url else latest)
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(erai, "get_settings", lambda: settings)
    monkeypatch.setattr(erai.asyncio, "sleep", no_wait)
    monkeypatch.setattr(erai.anime_mapping, "anidb_parts", parts)
    monkeypatch.setattr(erai.anime_mapping, "related_titles", titles)
    fresh = {erai._release_key(latest): latest}
    assert asyncio.run(erai._ordered_candidates(state, fresh, Client())) == []
    result = asyncio.run(erai._ordered_candidates(state, fresh, Client()))
    assert [row.id for row in result] == [earlier.id, latest.id]


def test_finished_index_stays_ready_when_phase_advancer_is_called_again() -> None:
    index = erai._default_state()["backfill"]
    index.update({"phase": "1080", "high_only": True, "frontier": []})
    erai._advance_backfill_phase(index)
    assert index["complete"] and index["phase"] == "ready"
    erai._advance_backfill_phase(index)
    assert index["phase"] == "ready" and not index["frontier"]


def test_empty_series_search_cannot_jump_to_latest_rss_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = erai._default_state()
    newest = entry("[Erai-raws] Example - 12 [1080p]", 12)

    class Client:
        async def get(self, url):
            return SimpleNamespace(text="", raise_for_status=lambda: None)

    async def no_parts(title):
        return []

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(erai.anime_mapping, "anidb_parts", no_parts)
    monkeypatch.setattr(erai.asyncio, "sleep", no_wait)
    result = asyncio.run(
        erai._ordered_candidates(state, {erai._release_key(newest): newest}, Client())
    )
    assert result == []
    index = state["series_catalogs"]["example"]
    assert not index["complete"] and "latest episode is blocked" in index["error"]


def test_punctuation_mismatch_rebuilds_even_when_latest_episode_already_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xml.etree import ElementTree as ET

    state = erai._default_state()
    latest = entry("[Erai-raws] Test Show - 12 [1080p][HEVC]", 12)
    first = entry("[Erai-raws] Test Show - 01 [1080p][HEVC]", 1)
    state["releases"][latest.info_hash] = {"status": "running"}
    state["series_catalogs"]["tvdb:1"] = {
        **erai._default_state()["backfill"],
        "complete": True,
        "high_only": True,
        "parent_tvdb_id": 1,
        "title_queries": ["Test Show."],
        "title_query": "Test Show.",
        "title_query_index": 0,
        "frontier": [],
    }

    async def parts(title):
        return [erai.anime_mapping.Part(1, 1, ET.fromstring('<anime defaulttvdbseason="1"/>'))]

    class Client:
        async def get(self, url):
            assert "Show." not in url
            html = listing(first) + listing(latest) if "1080p" in url else ""
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(erai.anime_mapping, "anidb_parts", parts)
    monkeypatch.setattr(erai.asyncio, "sleep", no_wait)
    result = asyncio.run(
        erai._ordered_candidates(state, {erai._release_key(latest): latest}, Client())
    )
    assert [row.id for row in result] == [1, 12]
    assert state["series_catalogs"]["tvdb:1"]["title_queries"] == ["Test Show"]


@pytest.mark.parametrize(
    "description",
    [
        "Subtitles Info:<br>**German** (CR) | ASS<br>English | ASS",
        "Subtitles Info:\n0: [German](https://example.com) (CR) | ASS",
        "Subtitles Info:\nTrack 3: Deutsch | SRT",
    ],
)
def test_german_subtitle_html_links_and_numbered_rows(description):
    assert erai.has_explicit_german_subtitles(description)


def test_german_aac_audio_row_is_not_subtitle_evidence():
    assert not erai.has_explicit_german_subtitles(
        "Audio Info:\nGerman | AAC\nSubtitles Info:\nEnglish | ASS"
    )


def test_part_two_uses_verified_split_cour_boundary(monkeypatch):
    from datetime import date, timedelta

    match = AnimeTVDBMatch(464693, "show", "Yoroi-Shinden Samurai Troopers")
    rows = [
        TVDBEpisode(
            1,
            n,
            n,
            f"Episode {n}",
            aired=(
                date(2026, 1, 6) + timedelta(days=(n - 1) * 7 + (90 if n > 12 else 0))
            ).isoformat(),
        )
        for n in range(1, 25)
    ]

    async def candidates(*args, **kwargs):
        return [match]

    async def mapping(*args):
        return None, False

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(erai.anime_mapping, "mapped_episode", mapping)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    selected, identity, error = asyncio.run(
        erai._resolve(
            entry("[Erai-raws] Yoroi-Shinden Samurai Troopers Part 2 - 11 [1080p][MultiSub]"),
            episodes=rows,
        )
    )
    assert selected == match and error is None
    assert (identity.season, identity.episode) == (1, 23)


def test_part_two_uses_equal_tvdb_cour_when_air_dates_have_no_boundary(monkeypatch):

    match = AnimeTVDBMatch(1, "show", "Test Show")

    async def candidates(*args, **kwargs):
        return [match]

    async def mapping(*args):
        return None, False

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", candidates)
    monkeypatch.setattr(erai.anime_mapping, "mapped_episode", mapping)
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    selected, identity, error = asyncio.run(
        erai._resolve(
            entry("[Erai-raws] Test Show Part 2 - 11 [1080p]"),
            episodes=[TVDBEpisode(1, n) for n in range(1, 25)],
        )
    )
    assert selected == match and error is None
    assert (identity.season, identity.episode) == (1, 23)


def test_saved_one_piece_selection_bypasses_ambiguous_search(monkeypatch, tmp_path):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    erai.save_mapping(
        "[Erai-raws] One Piece - 1173 [1080p][MultiSub]",
        AnimeTVDBMatch(81797, "show", "One Piece", year=1999),
    )

    async def ambiguous(*args, **kwargs):
        raise AssertionError("A saved source identity must bypass fuzzy/provider selection")

    async def no_mapping(*args):
        return None, False

    monkeypatch.setattr(erai.anime_mod, "tvdb_candidates", ambiguous)
    monkeypatch.setattr(erai.anime_mapping, "mapped_episode", no_mapping)
    selected, identity, error = asyncio.run(
        erai._resolve(
            entry("[Erai-raws] One Piece - 1174 [1080p][MultiSub]"),
            episodes=[TVDBEpisode(22, 90, 1174), TVDBEpisode(1, 1, 1)],
        )
    )
    assert selected.tvdb_id == 81797 and error is None
    assert (identity.season, identity.episode) == (22, 90)
    assert (
        erai._mapping_key("[Erai-raws] One Piece Film Red - 01 [1080p]")
        not in erai._load_mappings()
    )


def test_backlog_submission_reserves_space_before_qbit_add(monkeypatch):
    state = erai._default_state()
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})

    async def detail(*args):
        return "German | ASS", None, "Erai-raws"

    async def resolve(*args):
        return AnimeTVDBMatch(1, "show", "Test Show"), processor.EpisodeIdentity(1, 1), None

    monkeypatch.setattr(erai.anime_mod, "_detail_url", detail)
    monkeypatch.setattr(erai, "_resolve", resolve)
    monkeypatch.setattr(
        "bankai.backend.transfer._existing_show_folder", lambda *args, **kwargs: None
    )

    class Qbit:
        async def add(self, **kwargs):
            raise AssertionError("Insufficient reserved space must prevent submission")

    async def run():
        token = erai._ADMISSION.set({"qbit": Qbit(), "remaining": 100})
        try:
            async with httpx.AsyncClient() as client:
                assert not await erai._consider(state, entry(), client)
        finally:
            erai._ADMISSION.reset(token)

    asyncio.run(run())
    assert not state["canonical"]


def test_completed_prefill_torrents_are_not_double_counted_against_free_space():
    state = erai._default_state()
    state["releases"]["partial"] = {"size_bytes": 1000}
    torrents = [
        SimpleNamespace(hash="done", size_bytes=5000, progress=1.0),
        SimpleNamespace(hash="partial", size_bytes=0, progress=0.25),
    ]

    assert erai._reserved_torrent_bytes(state, torrents) == 750


def test_review_groups_every_held_release_beyond_recent_display_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    state = erai._default_state()
    for number in range(1, 502):
        item = entry(f"[Erai-raws] Test Show - {number:03d} [1080p][MultiSub]", number)
        erai._hold(state, item, "No confident TVDB match")
    erai._save_state(state)

    rows = erai.review_items()
    assert len(state["held"]) == 500
    assert len(rows) == 1
    assert rows[0]["release_count"] == 501


def test_retry_request_targets_all_holds_without_overwriting_cycle_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    monkeypatch.setattr(erai, "free_space_gib", lambda: 1000.0)
    state = erai._default_state()
    held = entry()
    erai._hold(state, held, "Release is not a trusted, original Nyaa upload")
    state["releases"]["running"] = {"status": "running", "title": "Active"}
    state["canonical"]["active"] = {"job_id": "running"}
    erai._save_state(state)
    before = erai._state_path().read_bytes()

    async def run() -> None:
        async def waiting(**kwargs: object) -> dict:
            await asyncio.Event().wait()
            return {}

        monkeypatch.setattr(erai, "run_cycle", waiting)
        monkeypatch.setattr(erai, "_RETRY_TASK", None)
        result = erai.retry_held()
        assert result["requested"] == result["retry_pending"] == 1
        assert erai._state_path().read_bytes() == before
        assert set(erai._load_retry_requests()) == {held.info_hash}
        assert erai._needs_consideration(state, held)
        # An active cycle's later save must not erase the button request.
        state["last_enqueued"] = 9
        erai._save_state(state)
        assert held.info_hash in erai._load_retry_requests()
        await erai.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("result_status", ["existing", "held"])
def test_retry_checks_old_catalog_release_and_consumes_one_attempt(
    result_status: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    settings = Settings(
        anime={"enabled": True, "backfill_request_delay_seconds": 1},
        metadata={"tvdb_api_key": "test"},
    )
    monkeypatch.setattr(erai, "get_settings", lambda: settings)
    monkeypatch.setattr(erai, "free_space_gib", lambda: 1000.0)
    held = entry()
    state = erai._default_state()
    erai._hold(state, held, "TVDB match is ambiguous")
    # Simulate a legacy hold whose original entry only exists in its catalogue.
    state["releases"][held.info_hash].pop("entry")
    state["backfill"]["catalog_1080"]["show|1"] = erai._entry_dict(held)
    erai._save_state(state)
    erai._save_retry_requests({held.info_hash: {"title": held.title}})
    checked = []

    async def forbidden(*args: object, **kwargs: object) -> list:
        pytest.fail("Retry-only checks must not require the current RSS or global crawl")

    async def consider(current: dict, candidate: NyaaEntry, _client: object) -> bool:
        assert erai._needs_consideration(current, candidate)
        checked.append(candidate.info_hash)
        if result_status == "held":
            erai._hold(current, candidate, "German subtitles are still absent")
        else:
            current["releases"][candidate.info_hash] = {"status": "existing"}
        return False

    monkeypatch.setattr(erai, "_fetch_rss", forbidden)
    monkeypatch.setattr(erai, "_crawl_backfill", forbidden)
    monkeypatch.setattr(erai, "_consider", consider)
    result = asyncio.run(erai.run_cycle(retries_only=True))
    assert checked == [held.info_hash]
    assert result["retry_pending"] == 0
    assert bool(result["held"]) == (result_status == "held")
    assert not erai._needs_consideration(erai._load_state(), held)


def test_retry_recovery_requires_exact_hash_and_preserves_trust_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    monkeypatch.setattr(
        erai, "get_settings", lambda: Settings(anime={"backfill_request_delay_seconds": 1})
    )
    held = replace(entry(), trusted=False)
    other = entry(number=2)
    state = erai._default_state()
    erai._hold(state, held, "Untrusted release")
    state["releases"][held.info_hash].pop("entry")
    erai._save_retry_requests({held.info_hash: {"title": held.title}})
    html_text = listing(held).replace('class="success"', 'class=""') + listing(other)

    async def run() -> list:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text=html_text))
        ) as client:
            return await erai._retry_candidates(state, client)

    found = asyncio.run(run())
    assert [item.info_hash for item in found] == [held.info_hash]
    assert not found[0].trusted


def test_part_suffix_is_not_part_of_series_policy_identity():
    first = "[Erai-raws] Test Show Part 2 - 01 [1080p]"
    later = "[Erai-raws] Test Show Part 2 - 11 [1080p]"
    plain = "[Erai-raws] Test Show - 03 [1080p]"
    assert erai._mapping_key(first) == erai._mapping_key(later) == erai._mapping_key(plain)


def test_blacklisted_series_is_never_considered(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    item = entry()
    erai._save_policies(
        {
            erai._mapping_key(item.title): {
                "mode": "blacklisted",
                "source_title": "Test Show",
                "updated_at": 1,
            }
        }
    )
    assert not erai._needs_consideration(erai._default_state(), item)


@pytest.mark.asyncio
async def test_allow_german_is_series_wide_and_schedules_every_held_release(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    state = erai._default_state()
    one = entry(number=1)
    two = entry("[Erai-raws] Test Show - 02 [1080p][MultiSub]", 2)
    erai._hold(state, one, "Nyaa description does not explicitly list German subtitles")
    erai._hold(state, two, "Nyaa description does not explicitly list German subtitles")
    erai._save_state(state)

    async def no_cycle(**kwargs):
        return {}

    monkeypatch.setattr(erai, "run_cycle", no_cycle)
    result = await erai.review_action(one.info_hash, "allow_german")
    assert result["requested"] == 2
    assert erai._series_policy(two.title)["mode"] == "german_allowed"
    assert set(erai._load_retry_requests()) == {one.info_hash, two.info_hash}
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_blacklist_removes_holds_and_can_be_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai.json")
    state = erai._default_state()
    item = entry()
    erai._hold(state, item, "No confident TVDB match")
    erai._save_state(state)

    async def no_cycle(**kwargs):
        return {}

    monkeypatch.setattr(erai, "run_cycle", no_cycle)
    await erai.review_action(item.info_hash, "blacklist")
    assert erai.review_items() == []
    assert erai.blacklist_items()[0]["source_title"] == "Test Show"
    result = erai.remove_blacklist(erai._mapping_key(item.title))
    assert result["requested"] == 1
    assert not erai.blacklist_items()
    await asyncio.sleep(0)


class _Torrent:
    def __init__(self, info_hash, progress=0.0, state="queuedDL"):
        self.hash = info_hash
        self.progress = progress
        self.state = state
        self.size_bytes = 1024**3


class _RecordingQbit:
    """Stands in for qBittorrent; records what the reconciler asks of it."""

    instances: ClassVar[list] = []

    def __init__(self, torrents=(), fail_add=False):
        self.torrents = list(torrents)
        self.removed: list[tuple[str, bool]] = []
        self.added: list[dict] = []
        self.fail_add = fail_add
        _RecordingQbit.instances.append(self)

    async def login(self):
        pass

    async def list_torrents(self, **_kwargs):
        return list(self.torrents)

    async def add(self, **kwargs):
        if self.fail_add:
            raise RuntimeError("qBittorrent refused the magnet")
        self.added.append(kwargs)

    async def remove(self, info_hash, *, delete_files=False):
        self.removed.append((info_hash, delete_files))

    async def aclose(self):
        pass


def _release(info_hash, status="queued", **extra):
    row = {
        "status": status,
        "title": "[Erai-raws] Test Show - 01 [1080p][HEVC]",
        "display_title": "Test Show S01E01",
        "args": [
            "anime-download",
            "--info-hash",
            info_hash,
            "--magnet-uri",
            "magnet:?xt=urn:btih:" + info_hash,
            "--tvdb-id",
            "123",
        ],
        "updated_at": 1.0,
    }
    row.update(extra)
    return row


def _run_reconcile(monkeypatch, tmp_path, releases, torrents, jobs=None, limit=2, **qbit_kwargs):
    """Drive one reconciliation pass over an isolated state file."""
    state = erai._default_state()
    state["releases"] = releases
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    saved: dict = {}
    monkeypatch.setattr(erai, "_save_state", lambda value: saved.update({"state": value}))
    monkeypatch.setattr(
        erai,
        "get_settings",
        lambda: Settings(anime={"enabled": True, "max_concurrent_transfers": limit}),
    )
    monkeypatch.setattr(erai.updates, "maintenance_active", lambda: False)

    qbit = _RecordingQbit(torrents, **qbit_kwargs)
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", lambda *a, **k: qbit)

    spawned: list[dict] = []

    class _Spawned:
        def __init__(self, job_id):
            self.id = job_id

    def spawn(*, kind, title, args):
        spawned.append({"kind": kind, "title": title, "args": args})
        return _Spawned(f"job{len(spawned)}")

    monkeypatch.setattr("bankai.cli.bgjobs.spawn", spawn)
    monkeypatch.setattr("bankai.cli.bgjobs.get_job", lambda job_id: (jobs or {}).get(job_id))

    counts = asyncio.run(erai.reconcile_releases())
    return state, counts, qbit, spawned


def test_reconciler_tracks_download_state_without_spawning_a_worker(monkeypatch, tmp_path):
    """Waiting on a download must cost no worker slot at all."""
    releases = {"a" * 40: _release("a" * 40), "b" * 40: _release("b" * 40)}
    torrents = [
        _Torrent("a" * 40, progress=0.0, state="queuedDL"),
        _Torrent("b" * 40, progress=0.4, state="downloading"),
    ]
    state, counts, _qbit, spawned = _run_reconcile(monkeypatch, tmp_path, releases, torrents)
    assert state["releases"]["a" * 40]["status"] == "queued"
    assert state["releases"]["b" * 40]["status"] == "downloading"
    assert spawned == []
    assert counts.get("downloading") == 1 and counts.get("queued") == 1


def test_completed_download_starts_publishing_within_the_transfer_limit(monkeypatch, tmp_path):
    """Any number may download; only `max_concurrent_transfers` may publish."""
    releases = {
        f"{index}".rjust(40, "0"): _release(f"{index}".rjust(40, "0")) for index in range(1, 5)
    }
    torrents = [_Torrent(h, progress=1.0, state="queuedUP") for h in releases]
    state, counts, _qbit, spawned = _run_reconcile(
        monkeypatch, tmp_path, releases, torrents, limit=2
    )
    assert len(spawned) == 2
    # The two that missed out are finished downloading, not still downloading.
    assert counts.get("complete") == 2
    publishing = [r for r in state["releases"].values() if r["status"] == "transferring"]
    assert len(publishing) == 2
    assert all(row["args"][0] == "anime-download" for row in spawned)


def test_queuedup_counts_as_complete(monkeypatch, tmp_path):
    """qBittorrent parks a finished torrent in the seeding queue."""
    info_hash = "c" * 40
    releases = {info_hash: _release(info_hash)}
    # progress is reported as 0 but the UP suffix means the download finished.
    torrents = [_Torrent(info_hash, progress=0.0, state="queuedUP")]
    _state, _counts, _qbit, spawned = _run_reconcile(monkeypatch, tmp_path, releases, torrents)
    assert len(spawned) == 1


def test_missing_torrent_is_re_added_rather_than_stranded(monkeypatch, tmp_path):
    """Nothing else re-adds it: discovery skips recorded releases."""
    info_hash = "d" * 40
    releases = {info_hash: _release(info_hash)}
    state, counts, qbit, spawned = _run_reconcile(monkeypatch, tmp_path, releases, [])
    assert len(qbit.added) == 1
    assert qbit.added[0]["magnet"].endswith(info_hash)
    assert state["releases"][info_hash]["status"] == "queued"
    assert counts.get("readded") == 1
    assert spawned == []


def test_release_is_held_when_it_cannot_be_re_added(monkeypatch, tmp_path):
    info_hash = "e" * 40
    releases = {info_hash: _release(info_hash)}
    state, _counts, _qbit, _spawned = _run_reconcile(
        monkeypatch, tmp_path, releases, [], fail_add=True
    )
    assert state["releases"][info_hash]["status"] == "held"


def test_published_release_has_its_torrent_removed_then_reads_done(monkeypatch, tmp_path):
    """A torrent is deleted only after its publishing job succeeded."""
    info_hash = "f" * 40
    releases = {info_hash: _release(info_hash, status="transferring", job_id="job1")}
    torrents = [_Torrent(info_hash, progress=1.0, state="queuedUP")]
    jobs = {"job1": SimpleNamespace(id="job1", status="done")}
    state, _counts, qbit, _spawned = _run_reconcile(
        monkeypatch, tmp_path, releases, torrents, jobs=jobs
    )
    assert qbit.removed == [(info_hash, True)]
    assert state["releases"][info_hash]["status"] == "done"


def test_a_failed_publish_never_deletes_the_torrent(monkeypatch, tmp_path):
    """Losing the download would make the failure unrecoverable."""
    info_hash = "1" * 40
    releases = {info_hash: _release(info_hash, status="transferring", job_id="job1")}
    torrents = [_Torrent(info_hash, progress=1.0, state="queuedUP")]
    jobs = {"job1": SimpleNamespace(id="job1", status="failed")}
    state, _counts, qbit, _spawned = _run_reconcile(
        monkeypatch, tmp_path, releases, torrents, jobs=jobs
    )
    assert qbit.removed == []
    assert state["releases"][info_hash]["status"] == "failed"


def test_running_publish_is_left_alone(monkeypatch, tmp_path):
    info_hash = "2" * 40
    releases = {info_hash: _release(info_hash, status="transferring", job_id="job1")}
    torrents = [_Torrent(info_hash, progress=1.0, state="queuedUP")]
    jobs = {"job1": SimpleNamespace(id="job1", status="running")}
    state, _counts, qbit, spawned = _run_reconcile(
        monkeypatch, tmp_path, releases, torrents, jobs=jobs
    )
    assert spawned == [] and qbit.removed == []
    assert state["releases"][info_hash]["status"] == "transferring"


def test_errored_torrent_is_held(monkeypatch, tmp_path):
    info_hash = "3" * 40
    releases = {info_hash: _release(info_hash)}
    torrents = [_Torrent(info_hash, progress=0.2, state="error")]
    state, _counts, _qbit, _spawned = _run_reconcile(monkeypatch, tmp_path, releases, torrents)
    assert state["releases"][info_hash]["status"] == "held"


def test_queue_rows_expose_the_backlog_that_has_no_job(monkeypatch):
    state = erai._default_state()
    state["releases"] = {
        "a" * 40: _release("a" * 40, status="queued"),
        "b" * 40: _release("b" * 40, status="downloading"),
        "c" * 40: _release("c" * 40, status="transferring", job_id="job1"),
        "d" * 40: _release("d" * 40, status="done"),
    }
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    rows = erai.release_queue_rows()
    # Only releases without a worker of their own; the rest come from bgjobs.
    assert {row["phase"] for row in rows} == {"queued", "downloading"}
    assert all(row["tvdb_id"] == "123" for row in rows)
    assert all(row["pending"] for row in rows)


def _pending(job_id, info_hash, title="Test Show S01E01"):
    from bankai.web.jobs import PendingJob

    return PendingJob(
        id=job_id,
        kind="show",
        title=title,
        args=[
            "anime-download",
            "--release-title",
            "[Erai-raws] Test Show - 01 [1080p][HEVC]",
            "--info-hash",
            info_hash,
            "--magnet-uri",
            "magnet:?xt=urn:btih:" + info_hash,
        ],
    )


def test_migration_retires_placeholders_and_adopts_untracked_ones(monkeypatch):
    """A pending job whose release is untracked is that episode's only record."""
    tracked, untracked, foreign = "a" * 40, "b" * 40, "c" * 40
    state = erai._default_state()
    state["releases"] = {tracked: _release(tracked, status="queued")}
    state["releases"][tracked].pop("args")  # an older record, written before args were stored
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)

    pending = [
        _pending("j1", tracked),
        _pending("j2", untracked, title="Other Show S01E02"),
        PendingJob(id="j3", kind="movie", title="A Movie", args=["run", "--url", "x"]),
    ]
    cancelled: list[str] = []
    monkeypatch.setattr("bankai.web.jobs.list_pending", lambda: list(pending))
    monkeypatch.setattr(
        "bankai.web.jobs.cancel_pending", lambda job_id: cancelled.append(job_id) or True
    )

    result = erai.retire_download_pendings()
    assert result == {"retired": 2, "adopted": 1, "kept": 1}
    assert cancelled == ["j1", "j2"]
    # The untracked release is now tracked rather than lost with its job.
    assert state["releases"][untracked]["status"] == "queued"
    assert state["releases"][untracked]["args"][0] == "anime-download"
    # The older record gains the arguments publishing will need.
    assert state["releases"][tracked]["args"][0] == "anime-download"
    assert foreign not in state["releases"]
    # A non-anime pending job is none of this migration's business.
    assert "j3" not in cancelled


def test_discovery_records_the_release_before_adding_the_torrent(monkeypatch):
    """The old order could add a torrent that no record ever pointed at."""
    order: list[str] = []
    state = erai._default_state()
    item = entry("[Erai-raws] Test Show - 01 [1080p][HEVC][MultiSub]")

    class Qbit:
        async def add(self, **_kwargs):
            order.append("qbit.add")
            # The release must already be written down by this point.
            assert item.info_hash in state["releases"]

    async def detail(_client, _url):
        return ("Subtitles Info:\nGerman (CR_German) | ASS", item.magnet_uri, "Erai-raws")

    async def resolve(_entry):
        return (
            AnimeTVDBMatch(1, "show", "Test Show", year=2024),
            SimpleNamespace(season=1, episode=1),
            None,
        )

    monkeypatch.setattr(erai, "get_settings", lambda: Settings(anime={"enabled": True}))
    monkeypatch.setattr(erai.anime_mod, "_detail_url", detail)
    monkeypatch.setattr(erai, "_resolve", resolve)
    monkeypatch.setattr(erai, "_existing_show_folder_lookup", None, raising=False)
    monkeypatch.setattr("bankai.backend.transfer._existing_show_folder", lambda *a, **k: None)
    token = erai._ADMISSION.set({"qbit": Qbit(), "remaining": 10 * 1024**3})

    def spawn(**_kwargs):
        raise AssertionError("discovery must not spawn a worker for a download")

    monkeypatch.setattr("bankai.cli.bgjobs.spawn", spawn)
    try:
        assert asyncio.run(erai._consider(state, item, object())) is True
    finally:
        erai._ADMISSION.reset(token)

    assert order == ["qbit.add"]
    record = state["releases"][item.info_hash]
    assert record["status"] == "queued"
    assert record["display_title"] == "Test Show S01E01"
    # The arguments are kept so publishing never re-resolves against TVDB.
    assert record["args"][0] == "anime-download"


def _legacy_release(status="queued"):
    """A record as older builds wrote them: no stored arguments."""
    return {
        "status": status,
        "title": "[Erai-raws] Test Show - 11 [1080p][HEVC]",
        "canonical": "402642|1|11",
        "updated_at": 1.0,
    }


def _state_with_series():
    state = erai._default_state()
    state["series"] = {
        "402642": {"tvdb_id": 402642, "english_title": "Test Show", "year": 2024}
    }
    return state


def test_legacy_record_without_arguments_is_rebuilt_and_published(monkeypatch, tmp_path):
    """Finished downloads sat untouched because the record carried no arguments."""
    info_hash = "a" * 40
    state = _state_with_series()
    state["releases"] = {info_hash: _legacy_release()}

    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(
        erai, "get_settings", lambda: Settings(anime={"enabled": True, "max_concurrent_transfers": 2})
    )
    monkeypatch.setattr(erai.updates, "maintenance_active", lambda: False)
    qbit = _RecordingQbit([_Torrent(info_hash, progress=1.0, state="queuedUP")])
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", lambda *a, **k: qbit)
    spawned: list[dict] = []
    monkeypatch.setattr(
        "bankai.cli.bgjobs.spawn",
        lambda **kwargs: spawned.append(kwargs) or SimpleNamespace(id="job1"),
    )
    monkeypatch.setattr("bankai.cli.bgjobs.get_job", lambda job_id: None)

    asyncio.run(erai.reconcile_releases())

    assert state["releases"][info_hash]["status"] == "transferring"
    args = spawned[0]["args"]
    assert args[0] == "anime-download"
    assert args[args.index("--tvdb-id") + 1] == "402642"
    assert args[args.index("--season") + 1] == "1"
    assert args[args.index("--episode") + 1] == "11"
    assert args[args.index("--english-title") + 1] == "Test Show"
    assert args[args.index("--year") + 1] == "2024"
    # The CLI refuses anything that is not a nyaa.si source.
    from bankai.web.anime import is_nyaa_url

    assert is_nyaa_url(args[args.index("--torrent-url") + 1])
    assert is_nyaa_url(args[args.index("--detail-url") + 1])
    assert args[args.index("--magnet-uri") + 1].endswith(info_hash)


def test_unrebuildable_record_is_held_instead_of_skipped_forever(monkeypatch, tmp_path):
    info_hash = "b" * 40
    state = erai._default_state()  # no series, so nothing to rebuild from
    state["releases"] = {info_hash: _legacy_release()}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(
        erai, "get_settings", lambda: Settings(anime={"enabled": True})
    )
    monkeypatch.setattr(erai.updates, "maintenance_active", lambda: False)
    qbit = _RecordingQbit([_Torrent(info_hash, progress=1.0, state="queuedUP")])
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", lambda *a, **k: qbit)
    monkeypatch.setattr("bankai.cli.bgjobs.get_job", lambda job_id: None)
    monkeypatch.setattr(
        "bankai.cli.bgjobs.spawn",
        lambda **kwargs: pytest.fail("must not publish without arguments"),
    )

    asyncio.run(erai.reconcile_releases())
    record = state["releases"][info_hash]
    assert record["status"] == "held"
    assert "arguments" in record["reason"].lower()


def test_legacy_running_status_is_re_driven(monkeypatch, tmp_path):
    """Those records point at workers from the old model that no longer exist."""
    info_hash = "c" * 40
    state = _state_with_series()
    state["releases"] = {info_hash: _legacy_release(status="running")}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(
        erai, "get_settings", lambda: Settings(anime={"enabled": True, "max_concurrent_transfers": 2})
    )
    monkeypatch.setattr(erai.updates, "maintenance_active", lambda: False)
    qbit = _RecordingQbit([_Torrent(info_hash, progress=0.5, state="downloading")])
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", lambda *a, **k: qbit)
    monkeypatch.setattr("bankai.cli.bgjobs.get_job", lambda job_id: None)
    monkeypatch.setattr("bankai.cli.bgjobs.spawn", lambda **kwargs: SimpleNamespace(id="job1"))

    asyncio.run(erai.reconcile_releases())
    assert state["releases"][info_hash]["status"] == "downloading"


def test_finished_download_waiting_for_a_slot_reads_complete(monkeypatch, tmp_path):
    """It stopped downloading the moment qBittorrent finished, slot or no slot."""
    hashes = [f"{index}".rjust(40, "0") for index in range(1, 4)]
    releases = {h: _release(h, status="downloading") for h in hashes}
    torrents = [_Torrent(h, progress=1.0, state="queuedUP") for h in hashes]
    state, counts, _qbit, spawned = _run_reconcile(
        monkeypatch, tmp_path, releases, torrents, limit=1
    )
    statuses = sorted(row["status"] for row in state["releases"].values())
    # One got the single slot; the other two are done downloading and waiting.
    assert statuses == ["complete", "complete", "transferring"]
    assert len(spawned) == 1
    assert counts.get("complete") == 2
    # None of them may still claim to be downloading.
    assert "downloading" not in statuses


def test_complete_releases_are_visible_in_the_queue(monkeypatch):
    state = erai._default_state()
    state["releases"] = {"a" * 40: _release("a" * 40, status="complete")}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    row = erai.release_queue_rows()[0]
    assert row["phase"] == "complete"
    assert row["step_label"] == "Downloaded, waiting to publish"
    assert row["overall_percent"] == 100.0


def _library(tmp_path, show="Test Show", episodes=3):
    root = tmp_path / "shows_anime"
    season = root / show / "Season 01"
    season.mkdir(parents=True)
    for number in range(1, episodes + 1):
        (season / f"{show} - S01E{number:02d}.mkv").write_bytes(b"video" * 100)
    return root


def _purge_settings(tmp_path):
    return Settings(
        anime={"enabled": True},
        output={"directory": tmp_path / "staging"},
        transfer={"anime_shows_dir": tmp_path / "shows_anime"},
    )


def test_blacklisting_without_delete_keeps_the_episodes(tmp_path, monkeypatch):
    """Two separate actions: stopping a show must not destroy what it has."""
    root = _library(tmp_path)
    monkeypatch.setattr(erai, "get_settings", lambda: _purge_settings(tmp_path))
    monkeypatch.setattr(erai, "_load_state", lambda: erai._default_state())
    monkeypatch.setattr(erai, "_load_policies", lambda: {})
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})

    result = asyncio.run(erai.purge_series("k", english_title="Test Show", delete_files=False))
    assert result["deleted_files"] == 0
    assert list((root / "Test Show" / "Season 01").glob("*.mkv"))


def test_purge_deletes_the_show_folder_and_reports_what_it_freed(tmp_path, monkeypatch):
    root = _library(tmp_path, episodes=3)
    monkeypatch.setattr(erai, "get_settings", lambda: _purge_settings(tmp_path))
    monkeypatch.setattr(erai, "_load_state", lambda: erai._default_state())
    monkeypatch.setattr(erai, "_load_policies", lambda: {})
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})

    result = asyncio.run(erai.purge_series("k", english_title="Test Show", delete_files=True))
    assert result["deleted_files"] == 3
    assert result["freed_bytes"] == 3 * 500
    assert not (root / "Test Show").exists()
    # The library root itself is never the thing being removed.
    assert root.exists()


def test_purge_refuses_paths_outside_the_library(tmp_path, monkeypatch):
    """The show folder is found by name, so containment is the real guard."""
    _library(tmp_path)
    outside = tmp_path / "not_the_library"
    outside.mkdir()
    (outside / "precious.mkv").write_bytes(b"keep me")
    monkeypatch.setattr(erai, "get_settings", lambda: _purge_settings(tmp_path))
    monkeypatch.setattr(erai, "_load_state", lambda: erai._default_state())
    monkeypatch.setattr(erai, "_load_policies", lambda: {})
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    # Pretend the lookup resolved to somewhere it should never reach.
    monkeypatch.setattr(erai, "series_files", lambda title: [outside])

    result = asyncio.run(erai.purge_series("k", english_title="Test Show", delete_files=True))
    assert result["deleted_files"] == 0
    assert (outside / "precious.mkv").exists()


def test_purge_removes_the_series_torrents_with_their_data(tmp_path, monkeypatch):
    """A rejected episode must not keep downloading onto the same full disk."""
    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {"status": "blacklisted", "title": "[Erai-raws] Test Show - 01 [1080p][HEVC]"},
        "b" * 40: {"status": "queued", "title": "[Erai-raws] Other Show - 01 [1080p][HEVC]"},
    }
    monkeypatch.setattr(erai, "get_settings", lambda: _purge_settings(tmp_path))
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(
        erai, "_load_policies", lambda: {"test show": {"mode": "blacklisted", "tvdb_id": "1"}}
    )
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    qbit = _RecordingQbit([])
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", lambda *a, **k: qbit)

    result = asyncio.run(erai.purge_series("test show", english_title="", delete_files=False))
    assert result["removed_torrents"] == 1
    assert qbit.removed == [("a" * 40, True)]


def test_blacklist_matches_every_season_of_the_same_series():
    """The reported bug: one season blacklisted, the other left in review."""
    policies = {
        "yozakura san chi no daisakusen 2nd season": {
            "mode": "blacklisted",
            "tvdb_id": "417912",
        }
    }
    mappings = {"yozakura san chi no daisakusen": {"tvdb_id": "417912"}}
    season_two = {"title": "[Erai-raws] Yozakura-san Chi no Daisakusen 2nd Season - 01 [1080p]"}
    season_one = {"title": "[Erai-raws] Yozakura-san Chi no Daisakusen - 08 [1080p]"}
    japanese = {"title": "[Erai-raws] Totally Different Name - 03 [1080p]", "canonical": "417912|1|3"}
    unrelated = {"title": "[Erai-raws] Some Other Show - 01 [1080p]"}

    for release in (season_two, season_one, japanese):
        assert erai._is_blacklisted_release(release, policies=policies, mappings=mappings)
    assert not erai._is_blacklisted_release(unrelated, policies=policies, mappings=mappings)


def _argless(info_hash, status="queued", **extra):
    """A record as older builds wrote them: identity, but no stored magnet."""
    row = {
        "status": status,
        "title": "[Erai-raws] Test Show - 11 [1080p][HEVC]",
        "canonical": "402642|1|11",
        "updated_at": 1.0,
    }
    row.update(extra)
    return row


def test_missing_torrent_is_re_added_from_its_info_hash_alone(monkeypatch, tmp_path):
    """Two hundred releases were held for wanting a magnet they never needed."""
    info_hash = "a" * 40
    state, counts, qbit, _spawned = _run_reconcile(
        monkeypatch, tmp_path, {info_hash: _argless(info_hash)}, []
    )
    assert counts.get("readded") == 1
    assert state["releases"][info_hash]["status"] == "queued"
    assert qbit.added[0]["magnet"] == "magnet:?xt=urn:btih:" + info_hash


def test_stale_re_add_holds_are_let_back_into_the_pipeline(monkeypatch, tmp_path):
    """Nothing was ever wrong with them, and held releases are never revisited."""
    info_hash = "b" * 40
    releases = {
        info_hash: _argless(
            info_hash,
            status="held",
            reason="Torrent could not be re-added to qBittorrent",
        )
    }
    torrents = [_Torrent(info_hash, progress=0.3, state="downloading")]
    state, _counts, _qbit, _spawned = _run_reconcile(monkeypatch, tmp_path, releases, torrents)
    assert state["releases"][info_hash]["status"] == "downloading"
    assert "reason" not in state["releases"][info_hash]


def test_a_real_hold_is_left_alone(monkeypatch, tmp_path):
    info_hash = "c" * 40
    releases = {
        info_hash: _argless(
            info_hash, status="held", reason="Nyaa description does not explicitly list German"
        )
    }
    state, _counts, _qbit, _spawned = _run_reconcile(monkeypatch, tmp_path, releases, [])
    assert state["releases"][info_hash]["status"] == "held"


def test_an_episode_already_in_the_library_is_not_downloaded_again(monkeypatch, tmp_path):
    """Its torrent is gone because it was published before the reconciler existed."""
    info_hash = "d" * 40
    season = tmp_path / "shows_anime" / "Test Show" / "Season 01"
    season.mkdir(parents=True)
    (season / "Test Show - S01E11.mkv").write_bytes(b"video")

    state = _state_with_series()
    state["releases"] = {info_hash: _argless(info_hash)}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(
        erai,
        "get_settings",
        lambda: Settings(
            anime={"enabled": True},
            transfer={"anime_shows_dir": tmp_path / "shows_anime"},
        ),
    )
    monkeypatch.setattr(erai.updates, "maintenance_active", lambda: False)
    qbit = _RecordingQbit([])
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", lambda *a, **k: qbit)
    monkeypatch.setattr("bankai.cli.bgjobs.get_job", lambda job_id: None)

    asyncio.run(erai.reconcile_releases())
    assert state["releases"][info_hash]["status"] == "done"
    assert qbit.added == []


@pytest.mark.parametrize(
    "title,expected",
    [
        # The real shape of an Erai-raws HEVC release: languages in the title.
        (
            "[Erai-raws] Nige Jouzu no Wakagimi - 01 [1080p][HEVC][Multiple Subtitle] "
            "[ENG][POR-BR][SPA-LA][SPA][ARA][FRE][GER][ITA][RUS]",
            True,
        ),
        ("[Erai-raws] Show - 01 [1080p][HEVC][Multiple Subtitle][ENG][DEU]", True),
        ("[Erai-raws] Show - 01 [1080p][HEVC][German]", True),
        ("[Erai-raws] Show - 01 [1080p][HEVC][GER-DE]", True),
        # No German among the tags.
        ("[Erai-raws] Show - 01 [1080p][HEVC][Multiple Subtitle][ENG][FRE][ITA]", False),
        ("[Erai-raws] Show - 01 [1080p CR WEB-DL AVC AAC][MultiSub][350457AB]", False),
        # "ger" inside a word is not a language tag.
        ("[Erai-raws] Danger Zone - 01 [1080p][HEVC][ENG]", False),
        ("[Erai-raws] Gerhard no Bouken - 01 [1080p][HEVC][ENG]", False),
    ],
)
def test_german_language_tags_are_read_from_the_title(title, expected):
    assert erai.title_lists_german_subtitles(title) is expected


def test_a_title_tagged_german_is_not_held(monkeypatch):
    """150 HEVC episodes were held while stating [GER] in their own name."""
    state = erai._default_state()
    item = entry(
        "[Erai-raws] Test Show - 01 [1080p][HEVC][Multiple Subtitle][ENG][FRE][GER]"
    )

    async def detail(_client, _url):
        # The HEVC releases say nothing about subtitles in the description.
        return ("Some description with no subtitle section", item.magnet_uri, "Erai-raws")

    async def resolve(_entry):
        return (
            AnimeTVDBMatch(1, "show", "Test Show", year=2024),
            SimpleNamespace(season=1, episode=1),
            None,
        )

    monkeypatch.setattr(erai, "get_settings", lambda: Settings(anime={"enabled": True}))
    monkeypatch.setattr(erai.anime_mod, "_detail_url", detail)
    monkeypatch.setattr(erai, "_resolve", resolve)
    monkeypatch.setattr("bankai.backend.transfer._existing_show_folder", lambda *a, **k: None)

    assert asyncio.run(erai._consider(state, item, object())) is True
    assert state["releases"][item.info_hash]["status"] == "queued"


def test_existing_german_tagged_holds_are_reopened(monkeypatch):
    state = erai._default_state()
    state["releases"] = {
        "a" * 40: {
            "status": "held",
            "reason": "Nyaa description does not explicitly list German subtitles",
            "title": "[Erai-raws] Show - 01 [1080p][HEVC][Multiple Subtitle][ENG][GER]",
            "retry_after": 9e9,
        },
        "b" * 40: {
            "status": "held",
            "reason": "Nyaa description does not explicitly list German subtitles",
            "title": "[Erai-raws] Show - 02 [1080p][HEVC][Multiple Subtitle][ENG][FRE]",
            "retry_after": 9e9,
        },
        "c" * 40: {
            "status": "held",
            "reason": "No confident TVDB match",
            "title": "[Erai-raws] Show - 03 [1080p][HEVC][ENG][GER]",
            "retry_after": 9e9,
        },
    }
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)

    assert erai.release_german_tagged_holds() == 1
    assert state["releases"]["a" * 40]["retry_after"] == 0
    # Not tagged German, and a hold for an unrelated reason: both left alone.
    assert state["releases"]["b" * 40]["retry_after"] == 9e9
    assert state["releases"]["c" * 40]["retry_after"] == 9e9

