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
import os
import shutil
import subprocess
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
        want_video = bool(ctx.job.payload.get("want_video", False))
        max_height = ctx.job.payload.get("max_height")

        out_dir = ctx.work_dir / f"job-{ctx.job.id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        remote = get_settings().scraper.remote_extract_ssh.strip()
        if remote:
            # Delegate to a host with a real display (voe-style hosters need one).
            result = await self._extract_remote(url, site=site, hint=hint, want_video=want_video, max_height=max_height)
        else:
            result = await extract_url(
                url,
                out_dir,
                site=site,
                hint=hint,
                want_video=want_video,
                max_height=max_height,
                ytdlp=self._ytdlp,
                playwright=self._playwright,
            )

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
            "has_video": result.has_video,
        }

    async def _extract_remote(
        self,
        url: str,
        *,
        site: str,
        hint: str,
        want_video: bool,
        max_height: int | None,
    ) -> ExtractResult:
        """Run the extraction on a remote host (which has a real display) via
        SSH, then read the produced audio back through a local mount/share."""
        import hashlib
        import json as _json

        s = get_settings().scraper
        rdir = s.remote_extract_dir.rstrip("/") or "/tmp/bankai-extract"
        ldir = s.remote_extract_local.rstrip("/")
        if not ldir:
            raise PermanentWorkerError("scraper.remote_extract_local not configured")
        prefix = s.remote_extract_cmd.strip() or "bankai"
        tag = hashlib.sha1(url.encode()).hexdigest()[:12]
        remote_out = f"{rdir}/{tag}"
        inner = [prefix, "extract-audio", _shq(url), "--site", _shq(site), "--out-dir", _shq(remote_out), "--json"]
        if want_video:
            inner.append("--want-video")
        if max_height:
            inner += ["--max-height", str(int(max_height))]
        remote_cmd = " ".join(inner)
        ssh_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            s.remote_extract_ssh,
            remote_cmd,
        ]
        log.info("[extract] delegating to %s (remote display): %s", s.remote_extract_ssh, remote_cmd)
        proc = await asyncio.create_subprocess_exec(*ssh_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        out_s = out.decode(errors="replace")
        if proc.returncode != 0:
            raise WorkerError(f"remote extract failed (rc={proc.returncode}): {err.decode(errors='replace')[-600:]}")
        payload = None
        for line in reversed(out_s.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    payload = _json.loads(line)
                    break
                except ValueError:
                    continue
        if not payload or not payload.get("path"):
            raise WorkerError(f"remote extract: no JSON result (stdout tail: {out_s[-400:]})")
        remote_path = str(payload["path"])
        local_path = ldir + remote_path[len(rdir) :] if remote_path.startswith(rdir) else remote_path
        p = Path(local_path.replace("\\", "/"))
        for _ in range(30):  # wait for the file to appear over the share
            if p.exists():
                break
            await asyncio.sleep(1)
        if not p.exists():
            raise WorkerError(f"remote extract produced {remote_path} but local {p} is not visible")
        return ExtractResult(
            path=p,
            codec=payload.get("codec"),
            duration_ms=payload.get("duration_ms"),
            extractor="remote:" + str(payload.get("extractor", "?")),
            has_video=bool(payload.get("has_video", False)),
        )


# ---- result type -----------------------------------------------------------


class ExtractResult:
    __slots__ = ("codec", "duration_ms", "extractor", "has_video", "path")

    def __init__(
        self,
        *,
        path: Path,
        codec: str | None,
        duration_ms: int | None,
        extractor: str,
        has_video: bool = False,
    ) -> None:
        self.path = path
        self.codec = codec
        self.duration_ms = duration_ms
        self.extractor = extractor
        self.has_video = has_video


def _shq(s: str) -> str:
    """POSIX single-quote a string for embedding in a remote SSH command."""
    return "'" + str(s).replace("'", "'\\''") + "'"


async def extract_url(
    url: str,
    out_dir: Path,
    *,
    site: str = "unknown",
    hint: str = "ytdlp",
    want_video: bool = False,
    max_height: int | None = None,
    ytdlp: YtDlpRunner | None = None,
    playwright: PlaywrightRunner | None = None,
) -> ExtractResult:
    """Extract the audio (yt-dlp first, Playwright fallback) for one stream URL.

    Self-contained (no DB/context) so it can back both the pipeline worker and
    the ``bankai extract`` CLI used for remote delegation.
    """
    ytdlp = ytdlp or YtDlpRunner()
    playwright = playwright or PlaywrightRunner()
    backend_pref = get_settings().scraper.backend  # "ytdlp" | "playwright" | "auto"
    try_ytdlp = backend_pref in ("ytdlp", "auto") and hint != "playwright"
    try_playwright = backend_pref in ("playwright", "auto")

    result: ExtractResult | None = None
    last_err: Exception | None = None

    if try_ytdlp:
        try:
            result = await ytdlp.extract(url, out_dir, want_video=want_video, max_height=max_height)
        except YtDlpError as exc:
            log.warning("[extract] yt-dlp failed for %s: %s", url, exc)
            last_err = exc
            if not try_playwright:
                raise WorkerError(f"yt-dlp failed: {exc}") from exc

    if result is None and try_playwright:
        log.info(
            "[extract] yt-dlp couldn't handle %s \u2014 switching to Playwright "
            "(opens Chromium, captures the real media URL, then yt-dlp downloads "
            "it; this can take several minutes for full movies)",
            url,
        )
        try:
            result = await playwright.extract(url, out_dir, ytdlp=ytdlp, want_video=want_video, max_height=max_height)
        except PlaywrightError as exc:
            log.error("[extract] playwright fallback failed for %s: %s", url, exc)
            raise WorkerError(f"playwright fallback failed: {exc}") from exc

    if result is None:
        raise WorkerError(f"no extractor available (last error: {last_err})")
    return result


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
        self,
        url: str,
        out_dir: Path,
        *,
        referer: str | None = None,
        want_video: bool = False,
        max_height: int | None = None,
    ) -> ExtractResult:
        return await asyncio.to_thread(self._extract_sync, url, out_dir, referer, want_video, max_height)

    def _extract_sync(
        self,
        url: str,
        out_dir: Path,
        referer: str | None = None,
        want_video: bool = False,
        max_height: int | None = None,
    ) -> ExtractResult:
        try:
            from yt_dlp import YoutubeDL
            from yt_dlp.utils import DownloadError
        except ImportError as exc:  # pragma: no cover - dep listed in pyproject
            raise YtDlpError(f"yt-dlp not installed: {exc}") from exc

        outtmpl = str(out_dir / "%(title).100s.%(ext)s")
        # For visual sync we need the picture too. Cap the video height so
        # the throwaway match copy stays small (it only feeds tiny hash
        # thumbnails); keep full-quality audio for the dub.
        if want_video:
            if max_height and max_height > 0:
                fmt = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"
            else:
                fmt = "bestvideo+bestaudio/best"
        else:
            fmt = "bestaudio/best"
        opts: dict[str, Any] = {
            "format": fmt,
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
        duration = int(info["duration"] * 1000) if isinstance(info, dict) and isinstance(info.get("duration"), (int, float)) else None
        vcodec = info.get("vcodec") if isinstance(info, dict) else None
        has_video = bool(vcodec and vcodec != "none")
        return ExtractResult(
            path=path,
            codec=codec,
            duration_ms=duration,
            extractor="ytdlp",
            has_video=has_video,
        )


# ---- Playwright ------------------------------------------------------------


class PlaywrightError(Exception):
    pass


# Anti-automation hardening. Many hosters (voe in particular) refuse to load
# the real stream when they detect a headless / automated browser: they keep
# serving the interstitial and never fire the JS redirect to the player. We
# defeat this by running a *headful* Chromium under a virtual X display
# (Xvfb) plus a small init script that hides the obvious automation tells.
_STEALTH_INIT_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
    "window.chrome={runtime:{}};"
)
_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--autoplay-policy=no-user-gesture-required",
]
# Substrings that mark a voe (or similar) "dead link" 404 body, so we can
# fail fast and let the pipeline try the next mirror instead of burning the
# full capture window on a link that will never play.
_DEAD_PAGE_MARKERS = ("not recognized", "404 - not found", "file not found")


class _VirtualDisplay:
    """Start an Xvfb virtual display so Chromium can run headful on a
    headless server. No-op when a display already exists or Xvfb is absent.
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.display: str | None = None
        self._prev: str | None = None

    def start(self) -> bool:
        if os.environ.get("DISPLAY"):
            self.display = os.environ["DISPLAY"]
            return True
        if not shutil.which("Xvfb"):
            return False
        for n in range(99, 110):
            disp = f":{n}"
            lock = Path(f"/tmp/.X{n}-lock")
            if lock.exists():
                continue
            try:
                proc = subprocess.Popen(
                    ["Xvfb", disp, "-screen", "0", "1280x720x24", "-nolisten", "tcp", "-ac"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                return False
            # Wait for the X server to become ready (lock file appears and
            # the process stays alive) instead of guessing a fixed delay.
            ready = False
            for _ in range(30):  # up to ~3s
                if proc.poll() is not None:
                    break
                if lock.exists():
                    ready = True
                    break
                time.sleep(0.1)
            if ready:
                # Small extra settle so the socket is fully accepting.
                time.sleep(0.3)
                self.proc = proc
                self.display = disp
                self._prev = os.environ.get("DISPLAY")
                os.environ["DISPLAY"] = disp
                log.info("[playwright] started Xvfb on %s for headful capture", disp)
                return True
            try:
                proc.kill()
            except Exception:
                pass
        return False

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            self.proc = None
        if self._prev is not None:
            os.environ["DISPLAY"] = self._prev
        elif self.display and os.environ.get("DISPLAY") == self.display and self.proc is None:
            os.environ.pop("DISPLAY", None)


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
        self,
        url: str,
        out_dir: Path,
        *,
        ytdlp: YtDlpRunner | None = None,
        want_video: bool = False,
        max_height: int | None = None,
    ) -> ExtractResult:
        captured, final_url = await self._capture(url)
        if not captured:
            raise PlaywrightError(f"no media URL captured at {url}")
        runner = ytdlp or YtDlpRunner()
        # Filmpalast (and most German hosters) play a short pre-roll ad/intro
        # *before* the real feature stream is requested. We see them all in
        # ``captured``; pick the one with the longest duration via a yt-dlp
        # metadata-only probe (cheap; no download).
        chosen = await self._pick_longest(captured, runner)
        log.info("[playwright] selected media URL (longest of %d candidates): %s", len(captured), chosen)
        # Use the final (post-redirect) player URL as the Referer/Origin
        # source — for hosters like voe the manifest CDN validates against
        # the player domain we actually landed on, not the link we started
        # from.
        referer = final_url or url
        # Try yt-dlp first (handles fancy DRM-free hosters), but fall
        # back to a direct ffmpeg pull if it fails — most CDN 403s happen
        # because yt-dlp's urllib path doesn't replay the player's
        # Referer/Origin reliably, while ffmpeg honours -headers.
        try:
            return await runner.extract(
                chosen,
                out_dir,
                referer=referer,
                want_video=want_video,
                max_height=max_height,
            )
        except YtDlpError as exc:
            log.warning(
                "[playwright] yt-dlp failed (%s) — falling back to direct ffmpeg pull",
                str(exc).splitlines()[0],
            )
            return await _ffmpeg_pull(chosen, out_dir, referer=referer)

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

    async def _capture(self, url: str) -> tuple[list[str], str]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover
            raise PlaywrightError(f"playwright not installed: {exc}") from exc

        ua = self._user_agent or get_settings().scraper.user_agent
        captured: list[str] = []
        got_manifest = asyncio.Event()
        final_url = url

        def _consider(href: str, ct: str = "") -> None:
            lower = href.lower()
            if any(bad in lower for bad in ("blank.mp4", "/dummy", "preview", "trailer", "intro")):
                return
            if ".m3u8" in lower or ".mp4" in lower or "video/" in ct or "application/vnd.apple.mpegurl" in ct:
                captured.append(href)
                if ".m3u8" in lower or "mpegurl" in ct:
                    got_manifest.set()

        # Start the virtual display BEFORE the Playwright driver launches so
        # the browser subprocess inherits DISPLAY (setting it afterwards is
        # too late — the driver has already captured its environment).
        display = _VirtualDisplay()
        headful = display.start()
        try:
            async with async_playwright() as pw:
                if headful:
                    browser = await pw.chromium.launch(headless=False, args=_CHROMIUM_ARGS, env={**os.environ})
                else:
                    # No display available — fall back to headless. Anti-bot
                    # hosters (voe) may not yield a stream this way, but
                    # yt-dlp hosters and simpler players still work.
                    log.warning("[playwright] no virtual display available (Xvfb missing); running headless \u2014 voe-style hosters may not load")
                    browser = await pw.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
                try:
                    ctx = await browser.new_context(
                        user_agent=ua,
                        viewport={"width": 1280, "height": 720},
                        locale="en-US",
                    )
                    await ctx.add_init_script(_STEALTH_INIT_JS)
                    page = await ctx.new_page()

                    def _on_response(resp: Any) -> None:
                        try:
                            ct = (resp.headers.get("content-type") or "").lower()
                        except Exception:
                            return
                        _consider(resp.url, ct)

                    # Also watch requests directly: some hosters fetch the
                    # manifest via XHR whose *response* headers we may miss.
                    def _on_request(req: Any) -> None:
                        try:
                            _consider(req.url)
                        except Exception:
                            return

                    def _attach(target: Any) -> None:
                        target.on("response", _on_response)
                        target.on("request", _on_request)

                    _attach(page)
                    # Some sites (e.g. bs.to) open the player in a popup/new
                    # tab — watch those too so we still capture the stream.
                    ctx.on("page", _attach)

                    # Voe (and similar hosters) serve an interstitial that
                    # JS-redirects to the real player on another domain. Wait
                    # for the redirect + player to settle instead of assuming
                    # the first page is the player.
                    resp = None
                    try:
                        resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                    except Exception as exc:
                        log.warning(
                            "[playwright] navigation did not finish for %s: %s; continuing",
                            url,
                            exc,
                        )
                    # Detect a dead hoster link (e.g. voe 404 "file not
                    # found") and bail fast so the pipeline tries the next
                    # mirror.
                    if resp is not None and resp.status == 404 and page.url == url:
                        try:
                            body = (await page.content()).lower()
                        except Exception:
                            body = ""
                        if any(m in body for m in _DEAD_PAGE_MARKERS):
                            raise PlaywrightError(f"hoster link is dead (404 not-found page): {url}")
                    # Give a JS redirect a chance to fire and the player load.
                    final_url = page.url
                    if final_url != url:
                        log.info("[playwright] followed redirect: %s -> %s", url, final_url)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8_000)
                    except Exception:
                        pass
                    final_url = page.url

                    # Press play in the main frame and any child iframes (voe
                    # nests its player in an iframe).
                    await self._click_play_everywhere(page)

                    # Adaptive wait: poll until a manifest is captured or the
                    # capture window elapses, re-pressing play periodically.
                    deadline = asyncio.get_event_loop().time() + max(self._capture_seconds, 20.0)
                    while asyncio.get_event_loop().time() < deadline:
                        if got_manifest.is_set():
                            # Let a couple more variants arrive, then stop.
                            await asyncio.sleep(1.5)
                            break
                        await asyncio.sleep(1.0)
                        await self._click_play_everywhere(page, quiet=True)
                finally:
                    await browser.close()
        finally:
            display.stop()

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
        return (manifests if manifests else unique), final_url

    @staticmethod
    async def _click_play_everywhere(page: Any, *, quiet: bool = False) -> None:
        """Press anything that looks like a play button, in every frame and
        every open tab/popup of the browser context."""
        selectors = (
            ".hoster-player",  # bs.to "Hier klicken" player
            "button[aria-label*='play' i]",
            ".plyr__control--overlaid",
            "button.vjs-big-play-button",
            ".jw-icon-display",
            "#player",
            ".play-button",
            ".play",
            "[class*=play]",
            "video",
        )
        pages = [page]
        try:
            ctx = page.context
            pages = list(ctx.pages) or [page]
        except Exception:
            pages = [page]
        for pg in pages:
            frames = [pg, *getattr(pg, "frames", [])]
            for frame in frames:
                for selector in selectors:
                    try:
                        await frame.locator(selector).first.click(timeout=1000)
                        break
                    except Exception:
                        continue
        # Last resort: click the middle of the viewport to dismiss overlays.
        if not quiet:
            try:
                await page.mouse.click(640, 360)
            except Exception:
                pass


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
    headers = f"Referer: {referer}\r\nOrigin: {origin}\r\nUser-Agent: {get_settings().scraper.user_agent}\r\n"
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
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
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
