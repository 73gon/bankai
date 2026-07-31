"""Tests for the extract worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.db.state import StateRepository
from bankai.processor.extractor import (
    ExtractResult,
    ExtractWorker,
    PlaywrightError,
    PlaywrightRunner,
    YtDlpError,
    YtDlpRunner,
    _is_vincdn_stream_url,
    _vinovo_stream_from_api,
    normalize_stream_url,
)
from bankai.queue.models import Job, JobKind
from bankai.queue.worker import Dispatcher, run_until_idle


class _FakeYtDlp(YtDlpRunner):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._fail = fail

    async def extract(  # type: ignore[override]
        self,
        url: str,
        out_dir: Path,
        *,
        referer: str | None = None,
        want_video: bool = False,
        max_height: int | None = None,
    ) -> ExtractResult:
        self.calls.append(url)
        if self._fail:
            raise YtDlpError("synthetic failure")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "audio.aac"
        path.write_bytes(b"fake-aac-bytes")
        return ExtractResult(path=path, codec="aac", duration_ms=120_000, extractor="ytdlp")


class _FakePlaywright(PlaywrightRunner):
    def __init__(self, *, captured_url: str | None = "http://stream/x.m3u8") -> None:
        super().__init__()
        self._captured = captured_url

    async def extract(
        self,
        url: str,
        out_dir: Path,
        *,
        ytdlp: YtDlpRunner | None = None,
        want_video: bool = False,
        max_height: int | None = None,
    ) -> ExtractResult:
        if self._captured is None:
            raise PlaywrightError("no capture")
        runner = ytdlp or YtDlpRunner()
        return await runner.extract(self._captured, out_dir)


def test_normalize_stream_url_turns_signed_vincdn_url_into_player_page() -> None:
    assert normalize_stream_url(
        "https://fs-11b55d.vincdn.net/stream/x230j40na6411v/token/1785529632"
    ) == "https://vinovo.to/e/x230j40na6411v"
    assert normalize_stream_url(
        "https://vinovo.to/d/x230j40na6411v"
    ) == "https://vinovo.to/e/x230j40na6411v"
    assert normalize_stream_url(
        "https://www.vinovo.to/e/x230j40na6411v?old=token"
    ) == "https://vinovo.to/e/x230j40na6411v"
    assert normalize_stream_url("https://voe.sx/e/stable") == "https://voe.sx/e/stable"


def test_vinovo_api_result_builds_extensionless_signed_stream_url() -> None:
    url = _vinovo_stream_from_api(
        "https://vinovo.to/api/file/url/x230j40na6411v",
        {"status": "ok", "token": "x230j40na6411v%2Ftoken%2F1785529632"},
        base_url="https://fs-11b55d.vincdn.net",
    )

    assert url == (
        "https://fs-11b55d.vincdn.net/stream/"
        "x230j40na6411v/token/1785529632"
    )
    assert _is_vincdn_stream_url(
        "https://fs-11b55d.vincdn.net/stream/x230j40na6411v/token/1785529632"
    )


def test_vinovo_legacy_result_field_remains_supported() -> None:
    assert _vinovo_stream_from_api(
        "https://vinovo.to/api/file/url/x230j40na6411v",
        {"status": "success", "result": "x230j40na6411v/old/1785529632"},
        base_url="https://fs-11b55d.vincdn.net",
    ) == "https://fs-11b55d.vincdn.net/stream/x230j40na6411v/old/1785529632"


def test_vinovo_api_failure_does_not_invent_a_stream_url() -> None:
    assert _vinovo_stream_from_api(
        "https://vinovo.to/api/file/url/x230j40na6411v",
        {"status": "fail", "message": ""},
        base_url="https://fs-11b55d.vincdn.net",
    ) is None


@pytest.mark.asyncio
async def test_extract_worker_happy_path(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite3"
    fake = _FakeYtDlp()
    worker = ExtractWorker(ytdlp_runner=fake, playwright_runner=_FakePlaywright())
    disp = Dispatcher(
        db_path=db,
        work_dir=tmp_path / "work",
        workers={JobKind.EXTRACT: worker},
        poll_interval=0.02,
    )
    with StateRepository(db) as repo:
        repo.create_job(
            Job(
                kind=JobKind.EXTRACT,
                payload={"url": "http://example/movie", "site": "filmpalast"},
            )
        )
    import asyncio as _a

    task = _a.create_task(disp.run())
    try:
        await run_until_idle(disp, timeout=5.0)
    finally:
        await disp.stop()
        await task
    assert fake.calls == ["http://example/movie"]
    with StateRepository(db) as repo:
        (job,) = repo.list_jobs()
        arts = repo.list_artifacts(job.id)  # type: ignore[arg-type]
    assert job.status.value == "done"
    assert len(arts) == 1
    assert arts[0].codec == "aac"
    assert arts[0].path.exists()


@pytest.mark.asyncio
async def test_extract_worker_falls_back_to_playwright(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite3"
    failing = _FakeYtDlp(fail=True)
    pw = _FakePlaywright(captured_url="http://stream/y.mp4")
    # Inner ytdlp used by playwright must NOT fail; supply a fresh fake.
    inner = _FakeYtDlp()
    worker = ExtractWorker(ytdlp_runner=failing, playwright_runner=pw)
    # Replace the playwright runner's downstream ytdlp via monkeypatch:
    original = pw.extract

    async def _patched(
        url: str,
        out_dir: Path,
        *,
        ytdlp: YtDlpRunner | None = None,
        want_video: bool = False,
        max_height: int | None = None,
    ) -> ExtractResult:
        return await original(url, out_dir, ytdlp=inner)

    pw.extract = _patched  # type: ignore[method-assign]

    disp = Dispatcher(
        db_path=db,
        work_dir=tmp_path / "work",
        workers={JobKind.EXTRACT: worker},
        poll_interval=0.02,
    )
    with StateRepository(db) as repo:
        repo.create_job(
            Job(
                kind=JobKind.EXTRACT,
                payload={"url": "http://example/movie", "site": "filmpalast"},
                max_attempts=1,
            )
        )
    import asyncio as _a

    task = _a.create_task(disp.run())
    try:
        await run_until_idle(disp, timeout=5.0)
    finally:
        await disp.stop()
        await task
    with StateRepository(db) as repo:
        (job,) = repo.list_jobs()
    assert job.status.value == "done"
    assert inner.calls == ["http://stream/y.mp4"]
