"""Stream extraction worker.

Pipeline stage 4: take a ``StreamHandle`` resolved by a scraper backend and
download the audio track to ``work_dir`` as an audio-only file.

Strategy
--------

1. ``yt-dlp`` is the primary extractor. It handles HLS, DASH, hundreds of
   hosters, and is updated frequently. Run with ``-f bestaudio`` to skip
   the video stream entirely.
2. When yt-dlp returns ``DownloadError`` (or the handle's ``hint`` is
   explicitly ``"playwright"``), fall back to launching headless Chromium,
   pressing play, intercepting the resolved ``.m3u8`` / ``.mp4`` URL, and
   handing that back to yt-dlp/ffmpeg for the actual download.
3. The extracted audio file path + codec is recorded as an ``audio``
   :class:`Artifact`.

Job payload schema
------------------

``payload`` for an EXTRACT job::

    {
        "url": "https://filmpalast.to/stream/inception-2010",
        "site": "filmpalast",
        "hint": "ytdlp"        # optional, mirrors StreamHandle.hint
    }
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.queue.models import Artifact, JobKind
from bankai.queue.worker import (
    PermanentWorkerError,
    Worker,
    WorkerContext,
    WorkerError,
)

log = get_logger(__name__)


class ExtractWorker(Worker):
    kind = JobKind.EXTRACT

    def __init__(
        self,
        *,
        ytdlp_runner: YtDlpRunner | None = None,
        playwright_runner: PlaywrightRunner | None = None,
    ) -> None:
        self._ytdlp = ytdlp_runner or YtDlpRunner()
        self._playwright = playwright_runner or PlaywrightRunner()

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        url = ctx.job.payload.get("url")
        if not url:
            raise PermanentWorkerError("extract job payload missing 'url'")
        hint = ctx.job.payload.get("hint", "ytdlp")
        site = ctx.job.payload.get("site", "unknown")
        backend_pref = get_settings().scraper.backend  # "ytdlp" | "playwright" | "auto"

        out_dir = ctx.work_dir / f"job-{ctx.job.id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        try_ytdlp = backend_pref in ("ytdlp", "auto") and hint != "playwright"
        try_playwright = backend_pref in ("playwright", "auto")

        result: ExtractResult | None = None
        last_err: Exception | None = None

        if try_ytdlp:
            try:
                result = await self._ytdlp.extract(url, out_dir)
            except YtDlpError as exc:
                log.warning("[extract] yt-dlp failed for %s: %s", url, exc)
                last_err = exc
                if not try_playwright:
                    raise WorkerError(f"yt-dlp failed: {exc}") from exc

        if result is None and try_playwright:
            log.info(
                "[extract] yt-dlp couldn't handle %s \u2014 switching to Playwright "
                "(opens headless Chromium, captures the real media URL, then "
                "yt-dlp downloads it; this can take several minutes for full movies)",
                url,
            )
            try:
                result = await self._playwright.extract(url, out_dir, ytdlp=self._ytdlp)
            except PlaywrightError as exc:
                log.error("[extract] playwright fallback failed for %s: %s", url, exc)
                raise WorkerError(f"playwright fallback failed: {exc}") from exc

        if result is None:
            raise WorkerError(f"no extractor available (last error: {last_err})")

        assert ctx.job.id is not None
        artifact = ctx.repo.add_artifact(
            Artifact(
                job_id=ctx.job.id,
                kind="audio",
                path=result.path,
                codec=result.codec,
                duration_ms=result.duration_ms,
                size_bytes=result.path.stat().st_size if result.path.exists() else None,
                metadata={"site": site, "source_url": url, "extractor": result.extractor},
            )
        )
        return {
            "artifact_id": artifact.id,
            "path": str(result.path),
            "codec": result.codec,
            "extractor": result.extractor,
        }


# ---- result type -----------------------------------------------------------


class ExtractResult:
    __slots__ = ("codec", "duration_ms", "extractor", "path")

    def __init__(
        self,
        *,
        path: Path,
        codec: str | None,
        duration_ms: int | None,
        extractor: str,
    ) -> None:
        self.path = path
        self.codec = codec
        self.duration_ms = duration_ms
        self.extractor = extractor


# ---- yt-dlp ----------------------------------------------------------------


class YtDlpError(Exception):
    pass


class YtDlpRunner:
    """Thin async wrapper over the ``yt_dlp`` library.

    yt-dlp is sync â€” we run it in a thread to keep the dispatcher
    responsive.
    """

    def __init__(self, ydl_opts: dict[str, Any] | None = None) -> None:
        self._extra_opts = ydl_opts or {}

    async def extract(
        self, url: str, out_dir: Path, *, referer: str | None = None
    ) -> ExtractResult:
        return await asyncio.to_thread(self._extract_sync, url, out_dir, referer)

    def _extract_sync(self, url: str, out_dir: Path, referer: str | None = None) -> ExtractResult:
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as exc:  # pragma: no cover - dep listed in pyproject
            raise YtDlpError(f"yt-dlp not installed: {exc}") from exc

        outtmpl = str(out_dir / "%(title).100s.%(ext)s")
        opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [_yt_dlp_progress_hook()],
            "user_agent": get_settings().scraper.user_agent,
            # Streaming sites often drop HTTP connections mid-transfer.
            # Retry the whole download a few times and per-fragment for HLS.
            "retries": 10,
            "fragment_retries": 10,
            "continuedl": True,
            "concurrent_fragment_downloads": 4,
            "http_chunk_size": 10 * 1024 * 1024,  # 10 MiB chunks survive blips
            # Use ffmpeg as external downloader: it transparently reconnects
            # on RemoteDisconnected and can stream-copy without re-buffering
            # the whole video, which is critical for streaming hosts that
            # cap connections at ~500 MB.
            "external_downloader": "ffmpeg",
            "external_downloader_args": {
                "ffmpeg_i": [
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_on_network_error",
                    "1",
                    "-reconnect_on_http_error",
                    "4xx,5xx",
                    "-reconnect_delay_max",
                    "30",
                    "-rw_timeout",
                    "30000000",
                ],
                "ffmpeg_o": ["-c", "copy"],
            },
        }
        opts.update(self._extra_opts)
        if referer:
            # Many streaming hosts (veev.to, streamtape, etc.) reject
            # direct CDN hits unless a Referer / Origin header from the
            # player page is present. We set them on both yt-dlp itself
            # AND the external ffmpeg downloader (yt-dlp does not always
            # propagate http_headers to external downloaders).
            from urllib.parse import urlparse

            origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
            headers = dict(opts.get("http_headers") or {})
            headers.setdefault("Referer", referer)
            headers.setdefault("Origin", origin)
            opts["http_headers"] = headers
            opts.setdefault("referer", referer)
            ext_args = opts.get("external_downloader_args") or {}
            ffmpeg_i = list(ext_args.get("ffmpeg_i") or [])
            ffmpeg_i = [
                "-headers",
                f"Referer: {referer}\\r\\nOrigin: {origin}\\r\\n",
                *ffmpeg_i,
            ]
            ext_args["ffmpeg_i"] = ffmpeg_i
            opts["external_downloader_args"] = ext_args
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # extract_info may return a playlist dict; we asked for noplaylist
                if isinstance(info, dict) and info.get("entries"):
                    info = info["entries"][0]
                filename = ydl.prepare_filename(info)
        except DownloadError as exc:
            raise YtDlpError(str(exc)) from exc
        path = Path(filename)
        if not path.exists():
            # yt-dlp sometimes post-processes to a different ext.
            candidates = sorted(out_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if not candidates:
                raise YtDlpError(f"yt-dlp finished but no output file in {out_dir}")
            path = candidates[0]
        codec = (info.get("acodec") or info.get("ext")) if isinstance(info, dict) else None
        duration = (
            int(info["duration"] * 1000)
            if isinstance(info, dict) and isinstance(info.get("duration"), (int, float))
            else None
        )
        return ExtractResult(path=path, codec=codec, duration_ms=duration, extractor="ytdlp")


# ---- Playwright ------------------------------------------------------------


class PlaywrightError(Exception):
    pass


class PlaywrightRunner:
    """Headless Chromium fallback that intercepts media URLs.

    Strategy:
        1. Launch Chromium with ``playwright-stealth`` patches.
        2. Navigate to the target URL.
        3. Listen for any response whose ``content-type`` matches video/HLS.
        4. Click any obvious play button.
        5. After a short capture window, hand the captured URL to
           :class:`YtDlpRunner` for the actual download.
    """

    def __init__(
        self,
        *,
        capture_seconds: float = 12.0,
        user_agent: str | None = None,
    ) -> None:
        self._capture_seconds = capture_seconds
        self._user_agent = user_agent

    async def extract(
        self, url: str, out_dir: Path, *, ytdlp: YtDlpRunner | None = None
    ) -> ExtractResult:
        captured = await self._capture(url)
        if not captured:
            raise PlaywrightError(f"no media URL captured at {url}")
        runner = ytdlp or YtDlpRunner()
        # Filmpalast (and most German hosters) play a short pre-roll ad/intro
        # *before* the real feature stream is requested. We see them all in
        # ``captured``; pick the one with the longest duration via a yt-dlp
        # metadata-only probe (cheap; no download).
        chosen = await self._pick_longest(captured, runner)
        log.info(
            "[playwright] selected media URL (longest of %d candidates): %s", len(captured), chosen
        )
        # Try yt-dlp first (handles fancy DRM-free hosters), but fall
        # back to a direct ffmpeg pull if it fails — most CDN 403s happen
        # because yt-dlp's urllib path doesn't replay the player's
        # Referer/Origin reliably, while ffmpeg honours -headers.
        try:
            return await runner.extract(chosen, out_dir, referer=url)
        except YtDlpError as exc:
            log.warning(
                "[playwright] yt-dlp failed (%s) — falling back to direct ffmpeg pull",
                str(exc).splitlines()[0],
            )
            return await _ffmpeg_pull(chosen, out_dir, referer=url)

    async def _pick_longest(self, urls: list[str], runner: YtDlpRunner) -> str:
        if len(urls) == 1:
            return urls[0]

        # Probe in parallel; fall back to the first URL if every probe fails.
        async def probe(href: str) -> tuple[str, float]:
            try:
                dur = await asyncio.to_thread(_probe_duration_seconds, href)
            except Exception as exc:
                log.debug("[playwright] probe failed for %s: %s", href, exc)
                return href, 0.0
            return href, dur

        results = await asyncio.gather(*(probe(u) for u in urls))
        results.sort(key=lambda t: t[1], reverse=True)
        for href, dur in results:
            log.debug("[playwright] candidate dur=%.1fs %s", dur, href)
        # If even the longest is < 60 s, the page probably hasn't yielded
        # the real stream yet \u2014 prefer it anyway, the sync stage will
        # bail out with a clear error if it really is a placeholder.
        return results[0][0] if results else urls[0]

    async def _capture(self, url: str) -> list[str]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise PlaywrightError(f"playwright not installed: {exc}") from exc

        ua = self._user_agent or get_settings().scraper.user_agent
        captured: list[str] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(user_agent=ua)
                page = await ctx.new_page()

                def _on_response(resp: Any) -> None:
                    try:
                        ct = (resp.headers.get("content-type") or "").lower()
                    except Exception:
                        return
                    href = resp.url
                    # Skip placeholders / dummy preview clips that some
                    # streaming sites serve before the real player loads.
                    lower = href.lower()
                    if any(
                        bad in lower
                        for bad in ("blank.mp4", "/dummy", "preview", "trailer", "intro")
                    ):
                        return
                    if (
                        ".m3u8" in href
                        or ".mp4" in href
                        or "video/" in ct
                        or "application/vnd.apple.mpegurl" in ct
                    ):
                        captured.append(href)

                page.on("response", _on_response)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                except Exception as exc:
                    log.warning(
                        "[playwright] page navigation did not finish for %s: %s; "
                        "continuing capture",
                        url,
                        exc,
                    )
                # Best-effort: click anything that looks like a play button.
                for selector in (
                    "button[aria-label*='play' i]",
                    ".plyr__control--overlaid",
                    "button.vjs-big-play-button",
                    "[class*=play]",
                ):
                    try:
                        await page.locator(selector).first.click(timeout=1500)
                        break
                    except Exception:
                        continue
                await asyncio.sleep(self._capture_seconds)
            finally:
                await browser.close()

        # Dedupe while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for href in captured:
            if href in seen:
                continue
            seen.add(href)
            unique.append(href)
        # Prefer HLS manifests over .mp4 fragments.
        manifests = [h for h in unique if h.endswith(".m3u8") or "m3u8" in h]
        return manifests if manifests else unique


def _probe_duration_seconds(url: str) -> float:
    """Cheap metadata-only yt-dlp probe; returns duration or 0.0."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return 0.0
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if isinstance(info, dict):
            d = info.get("duration")
            if isinstance(d, (int, float)):
                return float(d)
    except Exception:
        return 0.0
    return 0.0


async def _ffmpeg_pull(url: str, out_dir: Path, *, referer: str) -> ExtractResult:
    """Direct ffmpeg pull with Referer/Origin headers.

    Used when yt-dlp fails on a CDN URL captured by Playwright. ffmpeg
    honours ``-headers`` natively and reliably replays the player's
    request signature.
    """
    from urllib.parse import urlparse

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "playwright-direct.mp4"
    origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
    headers = (
        f"Referer: {referer}\r\n"
        f"Origin: {origin}\r\n"
        f"User-Agent: {get_settings().scraper.user_agent}\r\n"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_on_network_error",
        "1",
        "-reconnect_on_http_error",
        "4xx,5xx",
        "-reconnect_delay_max",
        "30",
        "-rw_timeout",
        "30000000",
        "-headers",
        headers,
        "-i",
        url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(out_path),
    ]
    log.info(
        "[playwright] ffmpeg pull: %s",
        " ".join([*cmd[:8], "...", "-i", url, "...", str(out_path)]),
    )
    log.info("BANKAI_PROGRESS stage=stream pct=0.0 status=ffmpeg")
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not out_path.exists():
        tail = (stderr or b"").decode("utf-8", "replace")
        tail = "\n".join(tail.splitlines()[-10:])
        raise YtDlpError(f"ffmpeg pull failed (exit {proc.returncode}):\n{tail}")
    log.info("BANKAI_PROGRESS stage=stream pct=100.0 status=finished")
    return ExtractResult(
        path=out_path,
        codec=None,
        duration_ms=None,
        extractor="ffmpeg",
    )


def _yt_dlp_progress_hook() -> Any:
    last = {"time": 0.0, "pct": -1.0}

    def hook(data: dict[str, Any]) -> None:
        status = str(data.get("status") or "")
        if status == "finished":
            log.info("BANKAI_PROGRESS stage=stream pct=100.0 status=finished")
            return
        if status != "downloading":
            return
        downloaded = _as_float(data.get("downloaded_bytes"))
        total = _as_float(data.get("total_bytes") or data.get("total_bytes_estimate"))
        pct = (downloaded / total * 100.0) if downloaded is not None and total else None
        now = time.monotonic()
        if pct is not None and pct < 100:
            if now - last["time"] < 5 and abs(pct - last["pct"]) < 2:
                return
            last["time"] = now
            last["pct"] = pct
            log.info(
                "BANKAI_PROGRESS stage=stream pct=%.1f speed=%s eta=%s downloaded=%s total=%s",
                pct,
                _as_int(data.get("speed")),
                _as_int(data.get("eta")),
                _as_int(downloaded),
                _as_int(total),
            )
        else:
            if now - last["time"] < 10:
                return
            last["time"] = now
            log.info(
                "BANKAI_PROGRESS stage=stream pct=unknown speed=%s eta=%s downloaded=%s",
                _as_int(data.get("speed")),
                _as_int(data.get("eta")),
                _as_int(downloaded),
            )

    return hook


def _as_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _as_int(value: object) -> int | str:
    if isinstance(value, int | float):
        return int(value)
    return "unknown"
