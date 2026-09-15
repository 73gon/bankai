from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bankai.backend.transfer import TransferItem, TransferResult
from bankai.config import Settings
from bankai.metadata.tvdb import TVDBEpisode
from bankai.processor import anime as processor
from bankai.web import erai
from bankai.web import jobs as webjobs
from bankai.web.anime import AnimeTVDBMatch, NyaaEntry


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


def test_backfill_finishes_all_high_quality_passes_before_720_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high = entry()
    low_duplicate = replace(high, id=2, title="[Erai-raws] Test Show - 01 [720p]", quality="720p")
    fallback = replace(entry(number=3), title="[Erai-raws] Other Show - 01 [720p]", quality="720p")
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
        async def login(self) -> None:
            pass

        async def list_torrents(self, **_kwargs: object) -> list:
            return []

        async def add(self, **_kwargs: object) -> None:
            pass

        async def resume(self, _hash: str) -> None:
            pass

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
    newest = entry("[Erai-raws] Test Show - 12 [1080p]", 12)
    first = entry("[Erai-raws] Test Show - 01 [1080p]", 1)

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
    earlier = entry("[Erai-raws] Bleach Ketsubetsu - 01 [1080p]", 1)
    latest = entry("[Erai-raws] Bleach Kashin - 01 [1080p]", 2)

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
    latest = entry("[Erai-raws] Test Show - 12 [1080p]", 12)
    first = entry("[Erai-raws] Test Show - 01 [1080p]", 1)
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


def test_part_two_without_boundary_is_held(monkeypatch):
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
    assert selected is None and identity is None and "continuation boundary" in error


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
