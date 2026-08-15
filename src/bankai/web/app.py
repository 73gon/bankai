"""FastAPI application: JSON API under /api, WebSocket at /ws, static UI.

Reuses the same services and background-job store as the CLI so the web
UI and terminal stay in sync. Built as a single process that also serves
the prebuilt React frontend from :data:`STATIC_DIR`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import mimetypes
import re
import subprocess
import sys
import threading
import time
import uuid
from array import array
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from bankai import __version__
from bankai.config import SelectorSettings, get_settings, reset_settings_cache
from bankai.logging import get_logger
from bankai.processor.extractor import normalize_stream_url
from bankai.queue.models import MediaKind
from bankai.web import anime as anime_mod
from bankai.web import discover as discover_mod
from bankai.web import jobs as webjobs
from bankai.web import media as media_mod
from bankai.web import posters as posters_mod
from bankai.web import review as review_mod

log = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# In-memory cache of downsampled audio waveforms keyed by (path, mtime, stream).
_WAVEFORM_CACHE: dict[tuple, dict] = {}
_WAVEFORM_CACHE_LOCK = threading.Lock()

# Requests from the review preloader and an immediate Play click can target the
# exact same clip.  Stripe the cache locks so only one ffmpeg process builds a
# given key while unrelated clips can still be prepared concurrently.
_CLIP_CACHE_LOCKS = tuple(threading.Lock() for _ in range(64))

# Cap concurrent ffmpeg transcodes. Review scrubbing + many open browser tabs
# used to spawn dozens of ffmpeg at once, exhausting the request threadpool and
# hanging the whole server. Requests that can't get a slot fail fast with 503
# so the client can show a loader and retry instead of blocking everything.
_FFMPEG_SLOTS = threading.BoundedSemaphore(4)
_LAPTOP_VPN_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
_RECENT_RELEASE_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}


def _backfill_review_source_video_fps() -> None:
    """Recover declared German FPS for outputs made before it was persisted."""

    from bankai.cli import bgjobs

    by_output: dict[str, float] = {}
    for job in bgjobs.list_jobs():
        if not job.final_path:
            continue
        fps = bgjobs.source_video_fps(job)
        if fps is None:
            continue
        try:
            by_output[str(Path(job.final_path).resolve())] = fps
        except OSError:
            continue
    if not by_output:
        return
    for entry in media_mod.scan_library():
        state = review_mod.get_state(entry.path)
        if state.source_video_fps is not None:
            continue
        try:
            fps = by_output.get(str(Path(entry.path).resolve()))
        except OSError:
            continue
        if fps is not None:
            review_mod.set_source_video_fps(entry.path, fps)


def _backfill_review_metadata() -> None:
    _backfill_review_source_video_fps()
    _backfill_review_duration_integrity()


def _backfill_review_duration_integrity() -> None:
    """Populate runtime compatibility for review files made by older builds."""
    for entry in media_mod.scan_library():
        state = review_mod.get_state(entry.path)
        if state.duration_compatible is not None:
            continue
        info = media_mod.probe(Path(entry.path))
        if info is None or not info.duration:
            continue
        german = next(
            (
                track
                for track in info.audio_tracks
                if track.is_german and track.duration and track.duration > 0
            ),
            None,
        )
        if german is None or german.duration is None:
            continue
        delta = german.duration - info.duration
        incompatible = abs(delta) > max(300.0, info.duration * 0.08)
        confidence = state.sync_confidence
        if incompatible and confidence is not None:
            confidence = min(confidence, 0.2)
        review_mod.set_sync_review(
            entry.path,
            needs_review=state.needs_sync_review or incompatible,
            confidence=confidence,
            applied_delay_ms=state.auto_delay_ms,
            duration_delta_seconds=delta,
            duration_compatible=not incompatible,
        )


def _laptop_vpn_command(command: str, *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    """Run one fixed NordVPN command on the configured ``laptop`` SSH host."""
    if command not in {"status", "connect"}:
        raise ValueError("unsupported VPN command")
    # Keller already uses this SSH target for remote extraction, including the
    # LocalSystem service account's working key. On development machines the
    # requested ``ssh laptop`` alias remains the fallback.
    target = get_settings().scraper.remote_extract_ssh.strip() or "laptop"
    return subprocess.run(
        [*_LAPTOP_VPN_SSH, target, "nordvpn", command],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _laptop_vpn_status() -> dict:
    try:
        result = _laptop_vpn_command("status")
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "connected": False,
            "status": "unavailable",
            "detail": str(exc)[:500],
        }
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    connected = bool(re.search(r"^\s*status\s*:\s*connected\b", output, re.I | re.M))
    return {
        "connected": connected,
        "status": "connected" if connected else "disconnected",
        "detail": output[:500] or f"nordvpn status exited with code {result.returncode}",
    }


def _stream_site_from_url(url: str) -> str:
    """Return the scraper id for a wrapper URL, or ``unknown`` for a hoster.

    Direct hoster links such as VOE must not be sent through a page scraper;
    the extractor can consume them directly through yt-dlp/Playwright.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source URL must be an http(s) link")
    host = parsed.hostname.lower().rstrip(".")

    def is_host(domain: str) -> bool:
        return host == domain or host.endswith(f".{domain}")

    if is_host("filmpalast.to"):
        return "filmpalast"
    if any(is_host(domain) for domain in ("burningseries.ac", "bs.to", "bs.cine.to")):
        return "burningseries"
    if is_host("aniworld.to"):
        return "aniworld"
    if is_host("kinox.to"):
        return "kinox"
    return "unknown"


async def _verify_filmpalast_source(url: str) -> int:
    """Return the supported mirror count for a Filmpalast wrapper URL."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not (host == "filmpalast.to" or host.endswith(".filmpalast.to")):
        return 0
    from bankai.scraper.backends.filmpalast import FilmpalastBackend

    backend = FilmpalastBackend()
    try:
        return len(await backend.resolve_live_streams(url))
    finally:
        await backend.aclose()


def _waveform_envelope(
    samples: Any,
    bins: int,
    *,
    smoothing_radius: int = 1,
) -> bytearray:
    """Build a stable dBFS loudness envelope from full-band PCM samples.

    Each bin uses winsorised RMS: the loudest two percent of samples are capped
    before energy is calculated, so a click or decode glitch cannot create a
    large bar for an otherwise quiet moment. Values are mapped against a fixed
    -72 dBFS noise floor. Crucially, there is no per-window normalisation, so a
    quiet scene remains visually quiet and comparable with a loud scene.
    """
    n = len(samples)
    if n == 0 or bins <= 0:
        return bytearray()
    count = min(bins, n)
    powers: list[float] = []
    for index in range(count):
        lo = index * n // count
        hi = max(lo + 1, (index + 1) * n // count)
        chunk = samples[lo:hi]
        if not chunk:
            continue
        absolute = sorted(abs(int(sample)) for sample in chunk)
        cap = absolute[min(len(absolute) - 1, int((len(absolute) - 1) * 0.98))]
        powers.append(sum(min(abs(int(sample)), cap) ** 2 for sample in chunk) / len(chunk))

    if not any(power > 0 for power in powers):
        return bytearray(len(powers))
    # Overview lanes benefit from a short integration window. Detailed review
    # lanes pass radius=0 so transient edges remain available for alignment.
    smoothed: list[float] = []
    for index in range(len(powers)):
        nearby = powers[
            max(0, index - smoothing_radius) :
            min(len(powers), index + smoothing_radius + 1)
        ]
        smoothed.append(sum(nearby) / len(nearby))
    floor_db = -72.0
    values = bytearray()
    for power in smoothed:
        if power <= 0:
            values.append(0)
            continue
        rms = math.sqrt(power) / 32768.0
        dbfs = 20.0 * math.log10(max(rms, 1e-9))
        values.append(max(0, min(127, round((dbfs - floor_db) * 127 / -floor_db))))
    return values


_R128_VALUE_RE = re.compile(r"lavfi\.r128\.M=(-?\d+(?:\.\d+)?)")


def _ebur128_envelope(output: str, bins: int) -> bytearray:
    """Map FFmpeg's EBU R128 momentary-loudness frames to display bars.

    R128's 400 ms integration window models perceived loudness much better
    than raw PCM peaks.  We keep a fixed LUFS scale so quiet passages remain
    visibly quiet instead of being normalised to fill the canvas.
    """
    loudness = [float(match.group(1)) for match in _R128_VALUE_RE.finditer(output)]
    if not loudness or bins <= 0:
        return bytearray()
    count = min(bins, len(loudness))
    values = bytearray()
    floor_lufs, ceiling_lufs = -70.0, -5.0
    for index in range(count):
        lo = index * len(loudness) // count
        hi = max(lo + 1, (index + 1) * len(loudness) // count)
        # Average in the energy domain, not directly in decibels.
        energy = sum(10.0 ** (value / 10.0) for value in loudness[lo:hi]) / (hi - lo)
        lufs = 10.0 * math.log10(max(energy, 1e-12))
        scaled = (lufs - floor_lufs) / (ceiling_lufs - floor_lufs)
        values.append(max(0, min(127, round(scaled * 127))))
    return values


@contextmanager
def _ffmpeg_slot():
    if not _FFMPEG_SLOTS.acquire(timeout=1.0):
        raise HTTPException(status_code=503, detail="transcoder busy")
    try:
        yield
    finally:
        _FFMPEG_SLOTS.release()


def _clips_dir() -> Path:
    from bankai.web.review import _state_root

    d = _state_root() / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _waveforms_dir() -> Path:
    from bankai.web.review import _state_root

    directory = _state_root() / "waveforms"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _waveform_cache_path(key: tuple) -> Path:
    digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()
    return _waveforms_dir() / f"{digest}.json"


def _waveform_cache_get(key: tuple) -> dict | None:
    with _WAVEFORM_CACHE_LOCK:
        hit = _WAVEFORM_CACHE.get(key)
    if hit is not None:
        return hit
    path = _waveform_cache_path(key)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    with _WAVEFORM_CACHE_LOCK:
        _WAVEFORM_CACHE[key] = value
    return value


def _waveform_cache_put(key: tuple, value: dict) -> None:
    with _WAVEFORM_CACHE_LOCK:
        _WAVEFORM_CACHE[key] = value
        while len(_WAVEFORM_CACHE) > 512:
            _WAVEFORM_CACHE.pop(next(iter(_WAVEFORM_CACHE)))
    path = _waveform_cache_path(key)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _clip_cache_path(key: str, ext: str) -> Path:
    import hashlib

    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return _clips_dir() / f"{digest}.{ext}"


def _cached_clip(key: str, ext: str, build) -> Path:
    """Return a disk-cached media clip, building it once via ``build(tmp)``.

    Clips (short audio/video windows for the review tool) are cached so
    replaying/scrubbing a section is instant and doesn't re-transcode.
    """
    out = _clip_cache_path(key, ext)
    lock = _CLIP_CACHE_LOCKS[hash(key) % len(_CLIP_CACHE_LOCKS)]
    with lock:
        if out.exists() and out.stat().st_size > 0:
            return out
        tmp = out.with_name(out.name + ".tmp")
        build(tmp)
        if not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="clip build failed")
        tmp.replace(out)
        return out


def _video_clip_cache_key(
    path: Path,
    *,
    mtime_ns: int,
    start: float,
    dur: float,
    height: int,
    audio: int | None,
) -> str:
    return f"{path}|{mtime_ns}|vid3|{round(start, 2)}|{round(dur, 2)}|{height}|{audio}"


def _audio_clip_cache_key(
    path: Path,
    *,
    mtime_ns: int,
    stream: int,
    start: float,
    dur: float,
    lead: float,
    rate: float,
) -> str:
    return (
        f"{path}|{mtime_ns}|aud3|{stream}|{round(start, 3)}|{round(dur, 3)}"
        f"|{round(lead, 3)}|{round(rate, 6)}"
    )


def _norm_title(s: str) -> str:
    """Loosely normalise a title/filename so a queue job and its finished
    library file collapse to the same key (one row per movie)."""
    s = s.lower()
    s = re.sub(r"\.[a-z0-9]{2,4}$", "", s)  # extension
    s = re.sub(r"\(\d{4}\)", "", s)  # (2016)
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)  # bare year
    s = re.sub(r"[^a-z0-9]+", "", s)  # punctuation / whitespace
    return s


def _title_membership_keys(title: str) -> set[str]:
    """Return conservative aliases used only for catalog membership checks.

    Media folders sometimes insert a sequel number that the canonical TVDB
    title omits (``Maleficent 2 - Mistress of Evil`` versus
    ``Maleficent: Mistress of Evil``). Ignore such a standalone number only
    when at least three other title words remain, so short numbered sequels
    such as ``Frozen 2`` never collapse into the original.
    """

    keys = {_norm_title(title)}
    value = title.lower()
    value = re.sub(r"\.[a-z0-9]{2,4}$", "", value)
    value = re.sub(r"\(?\b(?:19|20)\d{2}\b\)?", "", value)
    tokens = re.findall(r"[a-z0-9]+", value)
    sequel_numbers = {
        token for token in tokens if token.isdigit() and 1 <= int(token) <= 20
    }
    words = [token for token in tokens if token not in sequel_numbers]
    if sequel_numbers and len(words) >= 3:
        keys.add("".join(words))
    return {key for key in keys if key}


def _set_cli_option(args: list[str], option: str, value: str) -> list[str]:
    """Return argv with one canonical ``option value`` pair."""
    updated: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == option:
            skip_next = True
            continue
        if arg.startswith(f"{option}="):
            continue
        updated.append(arg)
    updated.extend([option, value])
    return updated


def _extract_year(s: str) -> int | None:
    """Pull a 4-digit year out of a title/filename, if present."""
    m = re.search(r"\((\d{4})\)", s) or re.search(r"\b(19|20)\d{2}\b", s)
    if not m:
        return None
    try:
        return int(m.group(0).strip("()"))
    except ValueError:
        return None


def _validate_media_title(raw: str) -> str:
    """Return a filesystem-safe movie/episode stem for Windows and Linux."""
    title = raw.strip()
    if not title or title in {".", ".."}:
        raise ValueError("title cannot be empty")
    if len(title) > 180:
        raise ValueError("title must be 180 characters or fewer")
    if any(ord(char) < 32 or char in '<>:"/\\|?*' for char in title):
        raise ValueError('title contains an invalid filename character')
    if title.endswith((".", " ")):
        raise ValueError("title cannot end with a dot or space")
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if title.split(".", 1)[0].upper() in reserved:
        raise ValueError("title is reserved by Windows")
    return title


def _clean_title(s: str) -> str:
    """Human title without the file extension, ``(year)`` or release cruft."""
    s = re.sub(r"\.[a-z0-9]{2,4}$", "", s, flags=re.I)  # extension
    s = re.sub(r"\s*\(\d{4}\)\s*", " ", s)  # (2016)
    s = re.sub(r"\s*\((?:unknown)\)\s*", " ", s, flags=re.I)
    return s.strip()


def _job_priority(job: dict) -> tuple[int, float]:
    """Rank jobs of the same title so the most relevant one wins.

    Active (running/queued) beats any finished attempt; among finished attempts
    the most recent one wins — so a fresh *failed* re-run is shown as Failed
    rather than being masked by an older successful run.
    """
    active = 1 if (job.get("pending") or job.get("status") == "running") else 0
    return (active, float(job.get("started_at") or 0))


class MovieQueueRequest(BaseModel):
    title: str
    german: str | None = None
    url: str | None = None
    site: str = "filmpalast"
    year: int | None = None


class CustomEpisodeRequest(BaseModel):
    episode: int
    url: str
    title: str | None = None


class ShowQueueRequest(BaseModel):
    show: str
    season: int
    episodes: list[int] | None = None
    site: str | None = None
    custom_episodes: list[CustomEpisodeRequest] | None = None


class AnimeDownloadRequest(BaseModel):
    release_title: str
    torrent_url: str
    detail_url: str
    magnet_uri: str
    info_hash: str
    tvdb_id: int
    kind: str
    english_title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None


class QueuePriorityRequest(BaseModel):
    position: int


class SourceRetryRequest(BaseModel):
    url: str


class DelayRequest(BaseModel):
    path: str
    delay_ms: int
    # Optional drift correction: time-stretch the German track by this factor
    # (atempo, pitch-preserving) before applying the constant delay. 1.0 / None
    # means "no stretch" (constant-delay repack only).
    atempo: float | None = None
    track_index: int | None = None


class ApproveRequest(BaseModel):
    path: str
    delay_ms: int | None = None
    atempo: float | None = None
    track_index: int | None = None


class TorrentCandidateRequest(BaseModel):
    id: str | None = None
    title: str
    indexer: str = ""
    indexer_id: int | None = None
    download_url: str
    info_url: str | None = None
    magnet_uri: str | None = None
    info_hash: str | None = None
    size_bytes: int = 0
    seeders: int = 0
    leechers: int = 0
    publish_date: str | None = None
    runtime_seconds: float | None = None
    eligible: bool = False


class ReplaceTorrentRequest(BaseModel):
    path: str
    query: str
    target_runtime_seconds: float | None = None
    candidate: TorrentCandidateRequest | None = None
    magnet_uri: str | None = None
    kind: str = "movie"
    series_title: str | None = None
    season: int | None = None
    episode: int | None = None


class PathRequest(BaseModel):
    path: str


class PathsRequest(BaseModel):
    paths: list[str]


class TorrentChoiceRequest(BaseModel):
    candidate_id: str | None = None
    candidate: TorrentCandidateRequest | None = None
    magnet_uri: str | None = None
    title: str | None = None


class ServerDirRequest(BaseModel):
    kind: str  # "movie" | "show"
    path: str = ""


class ServerRenameRequest(BaseModel):
    kind: str
    path: str
    title: str


class SettingRequest(BaseModel):
    key: str
    value: Any


# Settings keys the web UI is allowed to edit (safe subset).
SAFE_SETTING_KEYS: set[str] = {
    "metadata.tvdb_api_key",
    "metadata.tvdb_pin",
    "metadata.tvdb_enabled",
    "notifications.webhook_url",
    "notifications.on_success",
    "notifications.on_failure",
    "output.directory",
    "output.skip_existing",
    "transfer.root",
    "transfer.movies_dir",
    "transfer.shows_dir",
    "scraper.interactive_pick",
    "selector.max_size_gib",
    "selector.min_seeders",
    "selector.min_size_gib",
    "selector.preferred_resolutions",
    "web.port",
    "web.host",
    "web.max_concurrent_jobs",
    "web.transcode_fallback",
}


def _validate_setting_value(key: str, value: Any) -> Any:
    """Coerce and validate a web setting before it reaches config.toml."""
    if not key.startswith("selector."):
        return value

    field = key.removeprefix("selector.")
    data = get_settings().selector.model_dump()
    if field == "preferred_resolutions":
        if not isinstance(value, list) or not value:
            raise ValueError("preferred quality must be 1080p or 2160p")
        preferred = str(value[0]).lower()
        if preferred not in {"1080p", "2160p"}:
            raise ValueError("preferred quality must be 1080p or 2160p")
        other = "1080p" if preferred == "2160p" else "2160p"
        value = [preferred, other]
    data[field] = value
    try:
        validated = SelectorSettings.model_validate(data)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid value") if exc.errors() else "invalid value"
        raise ValueError(str(message)) from exc
    return getattr(validated, field)


def _is_secret_key(key: str) -> bool:
    return any(part in key.casefold() for part in ("password", "api_key", "pin", "webhook"))


def create_app() -> Any:
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import (
        FileResponse,
        HTMLResponse,
        Response,
        StreamingResponse,
    )
    from fastapi.staticfiles import StaticFiles

    @asynccontextmanager
    async def _lifespan(_app: Any):
        # Sync endpoints (media probing/transcoding, library scans) run in the
        # anyio threadpool. Give it headroom so a burst of review clips or many
        # open tabs can't starve lightweight endpoints like /api/health.
        migration_task: asyncio.Task | None = None
        try:
            import anyio.to_thread

            anyio.to_thread.current_default_thread_limiter().total_tokens = 96
            migration_task = asyncio.create_task(
                anyio.to_thread.run_sync(_backfill_review_metadata)
            )
        except Exception:
            pass
        try:
            yield
        finally:
            from bankai.web import availability as availability_mod

            if migration_task is not None:
                with suppress(Exception):
                    await migration_task
            await availability_mod.shutdown()

    app = FastAPI(
        title="bankai",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Queue/library membership is used by both catalogue pages.  Computing it
    # used to parse every historical job log for every Search/Discover request.
    # Keep a short-lived immutable snapshot instead; queue rows themselves
    # continue to update at their normal cadence.
    catalog_cache: dict[str, tuple[float, tuple[set[str], set[str], set[str]]]] = {}
    catalog_cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Path safety
    # ------------------------------------------------------------------
    def _library_root() -> Path:
        return Path(get_settings().output.directory).resolve()

    def _safe_path(raw: str) -> Path:
        p = _safe_library_output(raw)
        if not p.exists():
            raise HTTPException(status_code=404, detail="file not found")
        return p

    def _safe_library_output(raw: str) -> Path:
        p = Path(raw).resolve()
        root = _library_root()
        try:
            p.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="path outside library") from exc
        return p

    # ------------------------------------------------------------------
    # Health / meta
    # ------------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        s = get_settings()
        return {
            "status": "ok",
            "version": __version__,
            "library": str(s.output.directory),
            "ffprobe": media_mod.ffprobe_bin() is not None,
            "ffmpeg": media_mod.ffmpeg_bin() is not None,
            "mkvmerge": media_mod.mkvmerge_bin() is not None,
            "tvdb_configured": discover_mod.is_configured(),
        }

    # ------------------------------------------------------------------
    # Discover
    # ------------------------------------------------------------------
    def _server_have(kind: str) -> set[str]:
        """Normalised names already present on the server library (both the
        German-dub target and, if configured, other server dirs)."""
        have: set[str] = set()
        try:
            for t in media_mod.scan_server(kind):
                have.update(_title_membership_keys(t.name))
        except Exception:
            pass
        return have

    def _active_titles() -> set[str]:
        """Normalised titles currently in the queue (running/queued/done) so
        Discover doesn't re-offer something you're already downloading."""
        names: set[str] = set()
        try:
            titles = webjobs.catalog_titles()
            # Keep compatibility with empty/test registries whose display
            # snapshot may be supplied independently.
            if not titles:
                titles = {j.get("title", "") for j in webjobs.snapshot()}
            for title in titles:
                names.update(_title_membership_keys(title))
        except Exception:
            pass
        return names

    def _catalog_membership(kind: str) -> tuple[set[str], set[str], set[str]]:
        """Return ``(server, active, staged)`` title sets with a short TTL."""

        now = time.monotonic()
        with catalog_cache_lock:
            cached = catalog_cache.get(kind)
            if cached and now - cached[0] < 3.0:
                return tuple(set(values) for values in cached[1])  # type: ignore[return-value]
        have = _server_have(kind)
        active = _active_titles()
        staged: set[str] = set()
        try:
            for entry in media_mod.scan_library():
                if kind == "movie" and entry.kind == "movie":
                    staged.update(_title_membership_keys(entry.name))
                elif kind == "show" and entry.kind == "episode":
                    staged.update(_title_membership_keys(entry.series or entry.name))
        except Exception:
            pass
        value = (have, active, staged)
        with catalog_cache_lock:
            catalog_cache[kind] = (now, tuple(set(values) for values in value))
        return value

    def _added_titles(kind: str) -> set[str]:
        """Normalised titles already queued, staged, or on the media server."""
        have, active, staged = _catalog_membership(kind)
        return have | active | staged

    def _library_titles(kind: str) -> set[str]:
        """Normalised titles staged in bankai's local review library."""
        have, _active, staged = _catalog_membership(kind)
        return have | staged

    def _filter_discover(items: list, kind: str, membership=None) -> list:
        """Hide upcoming (not-yet-released) titles, anything already on the
        server, and anything already in the queue, so Discover only shows
        things worth downloading now."""
        have, active, _staged = membership or _catalog_membership(kind)
        out = []
        for it in items:
            if not discover_mod.is_released(it):
                continue
            nt = _norm_title(it.name)
            if nt and (nt in have or nt in active):
                continue
            out.append(it)
        return out

    def _apply_availability(items: list, kind: str) -> list[dict]:
        """Drop titles confirmed to have no working filmpalast mirror, backfill
        from deeper in the pool, schedule background checks for every unchecked
        pool item, and mark survivors with availability for the UI checkmark."""
        if kind != "movie":
            return [discover_mod.to_dict(i) for i in items]
        from bankai.web import availability as avail

        out: list[dict] = []
        for it in items:
            st = avail.get_status(it.name, year=it.year)
            # Schedule a check for anything unresolved -- including items beyond
            # the first `cap` so they're ready to backfill on the next refresh.
            if st is None or st["status"] == "unknown":
                avail.schedule(it.name, year=it.year)
            if st and st["status"] == "unavailable":
                continue  # hide it; a later pool item takes the slot
            d = discover_mod.to_dict(it)
            d["available"] = bool(st and st["status"] == "available")
            d["checked"] = bool(st and st["status"] in ("available", "unavailable"))
            if st and st.get("url"):
                d["filmpalast_url"] = st["url"]
            out.append(d)
        return out

    async def _discover_visible_page(
        kind: str,
        *,
        page: int,
        page_size: int,
        membership: tuple[set[str], set[str], set[str]],
    ) -> tuple[list[dict], int | None, bool]:
        """Paginate the entries that survive membership and mirror checks."""
        logical_start = page * page_size
        logical_target = logical_start + page_size + 1
        raw_page = 0
        visible: list[dict] = []
        seen: set[tuple] = set()
        provider_total: int | None = None
        provider_has_next = True
        while len(visible) < logical_target and provider_has_next:
            result = await discover_mod.browse_page(
                kind, page=raw_page, page_size=100
            )
            if provider_total is None:
                provider_total = result.total
            filtered = _filter_discover(result.items, kind, membership)
            for item in _apply_availability(filtered, kind):
                identity = (
                    item.get("tvdb_id"),
                    _norm_title(str(item.get("name") or "")),
                    item.get("year"),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                visible.append(item)
            provider_has_next = result.has_next
            raw_page += 1

        items = visible[logical_start : logical_start + page_size]
        has_next = len(visible) > logical_start + page_size or provider_has_next
        return items, provider_total, has_next

    @app.get("/api/discover/trending")
    async def discover_trending(
        kind: str = Query("movie"),
        page: int = Query(0, ge=0),
        page_size: int = Query(50, ge=10, le=100),
    ) -> dict:
        k = "movie" if kind == "movie" else "show"
        import anyio.to_thread

        membership = await anyio.to_thread.run_sync(_catalog_membership, k)
        items, total, has_next = await _discover_visible_page(
            k,
            page=page,
            page_size=page_size,
            membership=membership,
        )
        return {
            "configured": discover_mod.is_configured(),
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_next": has_next,
        }

    @app.get("/api/discover/search")
    async def discover_search(
        q: str = Query(...),
        kind: str = Query("movie"),
        search_by: str = Query("title", alias="by"),
        page: int = Query(0, ge=0),
        page_size: int = Query(50, ge=10, le=100),
    ) -> dict:
        k = "movie" if kind == "movie" else "show"
        mode = search_by.strip().casefold()
        if mode not in {"title", "person", "studio"}:
            raise HTTPException(status_code=400, detail="by must be title, person, or studio")
        if k != "movie" and mode != "title":
            raise HTTPException(
                status_code=400, detail="person and studio search are only available for movies"
            )
        paged = await discover_mod.search_page(
            q, kind=k, page=page, page_size=page_size, search_by=mode
        )
        # An explicit search should surface everything that matches. We only
        # hide clearly-unreleased titles; the discover mirror/on-server/queued
        # filters would otherwise hide exactly what the user searched for.
        items = [i for i in paged.items if discover_mod.is_released(i)]
        import anyio.to_thread

        have, active, staged = await anyio.to_thread.run_sync(_catalog_membership, k)
        added = have | active | staged
        in_library = have | staged
        results: list[dict] = []
        for item in items:
            result = discover_mod.to_dict(item)
            key = _norm_title(item.name)
            result["added"] = bool(key and key in added)
            result["in_library"] = bool(key and key in in_library)
            results.append(result)
        return {
            "configured": discover_mod.is_configured(),
            "items": results,
            "page": paged.page,
            "page_size": paged.page_size,
            "total": paged.total,
            "has_next": paged.has_next,
        }

    @app.get("/api/discover/poster")
    async def discover_poster(url: str = Query(...)) -> Response:
        import httpx

        if not url.startswith("https://") and not url.startswith("http://"):
            raise HTTPException(status_code=400, detail="invalid url")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(url)
                r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="poster fetch failed") from exc
        return Response(
            content=r.content,
            media_type=r.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/api/discover/german")
    async def discover_german(id: int = Query(...), kind: str = Query("movie")) -> dict:
        k = "movie" if kind == "movie" else "show"
        details = await discover_mod.title_details(id, kind=k)
        return {
            "tvdb_id": id,
            "kind": k,
            "english": details.english,
            "german": details.german,
            "year": details.worldwide_year,
            "release_date": details.worldwide_release_date,
        }

    # ------------------------------------------------------------------
    # Search (stream sources)
    # ------------------------------------------------------------------
    @app.get("/api/search")
    async def search_sources(
        q: str = Query(...),
        kind: str = Query("movie"),
        site: str | None = Query(None),
        limit: int = Query(15, ge=1, le=50),
    ) -> dict:
        from bankai.backend import search_stream_sources

        normalized_kind = kind.strip().casefold()
        if normalized_kind not in {"all", "movie", "episode", "show"}:
            raise HTTPException(
                status_code=400,
                detail="kind must be all, movie, episode, or show",
            )
        media_kind = (
            None
            if normalized_kind == "all"
            else MediaKind.MOVIE
            if normalized_kind == "movie"
            else MediaKind.EPISODE
        )
        results = await search_stream_sources(q, site=site, limit=limit, kind=media_kind)
        filmpalast_results = [result for result in results if result.site == "filmpalast"]
        if filmpalast_results:
            from bankai.scraper.backends.filmpalast import FilmpalastBackend

            backend = FilmpalastBackend()
            try:
                enriched = await backend.enrich_search_results(filmpalast_results)
            finally:
                await backend.aclose()
            by_url = {result.url: result for result in enriched}
            results = [by_url.get(result.url, result) for result in results]
        return {
            "results": [
                {
                    "site": r.site,
                    "title": r.title,
                    "year": r.year,
                    "kind": str(r.kind),
                    "url": r.url,
                    "release_name": r.release_name,
                    "poster_url": r.poster_url,
                    "runtime_minutes": int(r.raw["runtime_minutes"])
                    if r.raw.get("runtime_minutes", "").isdigit()
                    else None,
                }
                for r in results
            ]
        }

    @app.get("/api/filmpalast/detail")
    async def filmpalast_detail(url: str = Query(...)) -> dict:
        try:
            site = _stream_site_from_url(url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if site != "filmpalast":
            raise HTTPException(status_code=422, detail="a Filmpalast title URL is required")

        from bankai.scraper.backends.filmpalast import FilmpalastBackend, is_supported_hoster

        backend = FilmpalastBackend()
        try:
            details = await backend.title_details(url)
        except Exception as exc:
            log.warning("Filmpalast detail fetch failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="The Filmpalast title details could not be loaded. Please try again.",
            ) from exc
        finally:
            await backend.aclose()

        return {
            "title": details.title,
            "url": details.url,
            "kind": str(details.kind),
            "year": details.year,
            "poster_url": details.poster_url,
            "release_name": details.release_name,
            "runtime_minutes": details.runtime_minutes,
            "mirrors": [
                {
                    "url": mirror.url,
                    "host": (urlparse(mirror.url).hostname or mirror.url).removeprefix("www."),
                    "hint": mirror.hint,
                    "supported": is_supported_hoster(mirror.url),
                }
                for mirror in details.mirrors
            ],
            "episodes": [
                {
                    "season": episode.season,
                    "episode": episode.episode,
                    "title": episode.title or None,
                    "url": episode.url,
                }
                for episode in details.episodes
            ],
        }

    @app.get("/api/releases/recent")
    async def recent_releases(
        page: int = Query(0, ge=0),
        feed: str = Query("new"),
    ) -> dict:
        """Return three Filmpalast listing pages as one Bankai page."""
        normalized_feed = feed.strip().casefold()
        if normalized_feed not in {"new", "movies", "shows", "top"}:
            raise HTTPException(status_code=400, detail="feed must be new, movies, shows, or top")
        cache_key = (normalized_feed, page)
        cached = _RECENT_RELEASE_CACHE.get(cache_key)
        if cached is not None and time.monotonic() - cached[0] < 300:
            return cached[1]

        from bankai.scraper.backends.filmpalast import FilmpalastBackend

        backend = FilmpalastBackend()
        try:
            results, has_next, source_start, source_end = await backend.recent(
                page, feed=normalized_feed
            )
        except Exception as exc:
            log.warning("Filmpalast recent-release fetch failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Filmpalast's recent releases could not be loaded. Please try again.",
            ) from exc
        finally:
            await backend.aclose()

        payload = {
            "items": [
                {
                    "site": result.site,
                    "title": result.title,
                    "url": result.url,
                    "kind": str(result.kind),
                    "year": result.year,
                    "poster_url": result.poster_url,
                    "release_name": result.release_name,
                    "runtime_minutes": int(result.raw["runtime_minutes"])
                    if result.raw.get("runtime_minutes", "").isdigit()
                    else None,
                }
                for result in results
            ],
            "page": page,
            "feed": normalized_feed,
            "source_page_start": source_start,
            "source_page_end": source_end,
            "has_next": has_next,
        }
        _RECENT_RELEASE_CACHE[cache_key] = (time.monotonic(), payload)
        return payload

    @app.get("/api/torrents/search")
    async def torrent_search(
        q: str = Query(...),
        runtime_seconds: float | None = Query(None, gt=0),
        kind: str = Query("movie"),
        series_title: str | None = Query(None),
        season: int | None = Query(None, ge=1),
        episode: int | None = Query(None, ge=1),
        min_seeders: int | None = Query(None, ge=0),
        max_seeders: int | None = Query(None, ge=0),
        min_size_gib: float | None = Query(None, ge=0),
        max_size_gib: float | None = Query(None, gt=0),
    ) -> dict:
        """Candidates for the review replacement picker."""
        from bankai.torrent.actions import candidate_id, candidate_to_dict
        from bankai.torrent.prowlarr import ProwlarrClient
        from bankai.torrent.selector import TorrentSelector
        from bankai.torrent.worker import _CAT_MOVIES, _CAT_TV, episode_search_queries

        if kind not in {"movie", "episode"}:
            raise HTTPException(status_code=422, detail="kind must be movie or episode")
        if max_seeders is not None and min_seeders is not None and max_seeders < min_seeders:
            raise HTTPException(status_code=422, detail="maximum seeders cannot be below minimum")
        base_policy = get_settings().selector
        policy_data = base_policy.model_dump()
        if min_seeders is not None:
            policy_data["min_seeders"] = min_seeders
        if min_size_gib is not None:
            policy_data["min_size_gib"] = min_size_gib
        if max_size_gib is not None:
            policy_data["max_size_gib"] = max_size_gib
        try:
            policy = SelectorSettings.model_validate(policy_data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc.errors()[0]["msg"])) from exc

        payload = {
            "query": q,
            "kind": kind,
            "series_title": series_title,
            "season": season,
            "episode": episode,
        }

        queries = episode_search_queries(payload) if kind == "episode" else [q]
        categories = _CAT_TV if kind == "episode" else _CAT_MOVIES

        client = ProwlarrClient()
        try:
            by_id = {}
            relevant = []
            for search_query in queries:
                candidates = await client.search(search_query, categories=categories)
                for candidate in TorrentSelector(policy).relevant(candidates, query=search_query):
                    ident = candidate_id(candidate)
                    if ident not in by_id:
                        by_id[ident] = candidate
                        relevant.append(candidate)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Prowlarr search failed: {exc}") from exc
        finally:
            await client.aclose()
        selector = TorrentSelector(policy)
        policy_candidates = [
            candidate
            for candidate in relevant
            if max_seeders is None or candidate.seeders <= max_seeders
        ]
        ranked = selector.rank(
            policy_candidates,
            query=None,
            target_runtime_seconds=runtime_seconds,
        )
        eligible = {candidate_id(item.candidate) for item in ranked}
        order = {candidate_id(item.candidate): index for index, item in enumerate(ranked)}
        relevant.sort(
            key=lambda item: (
                candidate_id(item) not in eligible,
                order.get(candidate_id(item), 10_000),
                -item.seeders,
            )
        )
        return {
            "query": q,
            "target_runtime_seconds": runtime_seconds,
            "policy": {
                "min_seeders": policy.min_seeders,
                "max_seeders": max_seeders,
                "min_size_gib": policy.min_size_gib,
                "max_size_gib": policy.max_size_gib,
            },
            "candidates": [
                candidate_to_dict(item, eligible=candidate_id(item) in eligible)
                for item in relevant
            ],
        }

    # ------------------------------------------------------------------
    # Anime (Nyaa-only direct downloads)
    # ------------------------------------------------------------------
    @app.get("/api/anime")
    async def anime_search(
        q: str = Query(""),
        category: str = Query("1_0"),
        page: int = Query(0, ge=0),
        quality: str | None = Query(None),
        publisher: str | None = Query(None),
        title_filters: str | None = Query(None),
        description_filters: str | None = Query(None),
        min_seeders: int = Query(0, ge=0),
    ) -> dict:
        try:
            result = await anime_mod.search(
                q,
                category=category,
                page=page,
                quality=quality,
                publisher=publisher,
                title_filters=title_filters,
                description_filters=description_filters,
                min_seeders=min_seeders,
            )
        except Exception as exc:
            log.warning("Nyaa search failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"Nyaa search failed: {exc}") from exc
        return {
            "configured": discover_mod.is_configured(),
            "items": [anime_mod.entry_to_dict(item) for item in result.items],
            "page": result.page,
            "has_next": result.has_next,
            "aliases": result.aliases,
        }

    @app.get("/api/anime/tvdb")
    async def anime_tvdb(q: str = Query(..., min_length=2)) -> dict:
        matches = await anime_mod.tvdb_candidates(q, limit=12)
        return {
            "configured": discover_mod.is_configured(),
            "items": [anime_mod.tvdb_to_dict(item) for item in matches],
        }

    @app.get("/api/vpn/status")
    def vpn_status() -> dict:
        return _laptop_vpn_status()

    @app.post("/api/vpn/connect")
    def vpn_connect() -> dict:
        try:
            result = _laptop_vpn_command("connect", timeout=75)
        except (OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=502, detail=f"VPN connection failed: {exc}") from exc
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "NordVPN connect failed").strip()
            raise HTTPException(status_code=502, detail=output[:500])
        return _laptop_vpn_status()

    @app.get("/api/anime/detail")
    async def anime_detail(url: str = Query(...)) -> dict:
        try:
            description, magnet, publisher = await anime_mod.detail(url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            log.warning("Nyaa detail lookup failed for %s: %s", url, exc)
            raise HTTPException(status_code=502, detail="Nyaa description could not be loaded") from exc
        return {"description": description, "magnet_uri": magnet, "publisher": publisher}

    @app.post("/api/anime/download")
    def anime_download(req: AnimeDownloadRequest) -> dict:
        if not req.release_title.strip() or not req.english_title.strip():
            raise HTTPException(status_code=422, detail="release and English titles are required")
        if req.tvdb_id <= 0:
            raise HTTPException(status_code=422, detail="a valid TVDB entry is required")
        if req.kind not in {"show", "movie"}:
            raise HTTPException(status_code=422, detail="kind must be show or movie")
        if req.season is not None and req.season < 1:
            raise HTTPException(status_code=422, detail="season must be positive")
        if req.episode is not None and req.episode < 1:
            raise HTTPException(status_code=422, detail="episode must be positive")
        if req.kind == "movie" and (req.season is not None or req.episode is not None):
            raise HTTPException(status_code=422, detail="movies cannot have season or episode overrides")
        if not anime_mod.is_nyaa_url(req.torrent_url) or not anime_mod.is_nyaa_url(req.detail_url):
            raise HTTPException(
                status_code=422, detail="anime downloads only accept nyaa.si sources"
            )
        if not re.fullmatch(r"[0-9a-fA-F]{40}", req.info_hash.strip()):
            raise HTTPException(status_code=422, detail="invalid Nyaa info hash")
        if not req.magnet_uri.casefold().startswith(
            f"magnet:?xt=urn:btih:{req.info_hash.casefold()}"
        ):
            raise HTTPException(status_code=422, detail="magnet does not match the Nyaa torrent")
        args = [
            "anime-download",
            "--release-title",
            req.release_title.strip(),
            "--torrent-url",
            req.torrent_url.strip(),
            "--detail-url",
            req.detail_url.strip(),
            "--magnet-uri",
            req.magnet_uri.strip(),
            "--info-hash",
            req.info_hash.casefold(),
            "--kind",
            req.kind,
            "--tvdb-id",
            str(req.tvdb_id),
            "--english-title",
            req.english_title.strip(),
        ]
        if req.year is not None:
            args.extend(["--year", str(req.year)])
        if req.season is not None:
            args.extend(["--season", str(req.season)])
        if req.episode is not None:
            args.extend(["--episode", str(req.episode)])
        detected_season, detected_episode = anime_mod.release_episode_info(req.release_title)
        season = req.season if req.season is not None else detected_season
        episode = req.episode if req.episode is not None else detected_episode
        suffix = ""
        if season is not None and episode is not None:
            suffix = f" S{season:02d}E{episode:02d}"
        elif episode is not None:
            suffix = f" E{episode:02d}"
        elif season is not None:
            suffix = f" S{season:02d}"
        return webjobs.enqueue(
            kind=req.kind,
            title=f"{req.english_title.strip()}{suffix}",
            args=args,
        )

    @app.get("/api/torrent-actions/{job_id}")
    def torrent_action_get(job_id: str) -> dict:
        from bankai.torrent import actions as torrent_actions

        request = torrent_actions.get_request(job_id)
        if request is None:
            raise HTTPException(status_code=404, detail="torrent action not found")
        return request

    @app.post("/api/torrent-actions/{job_id}")
    def torrent_action_choose(job_id: str, req: TorrentChoiceRequest) -> dict:
        from bankai.torrent import actions as torrent_actions

        if req.candidate is not None:
            selected = torrent_actions.choose_candidate(
                job_id,
                req.candidate.model_dump(),
            )
        elif req.magnet_uri:
            magnet = req.magnet_uri.strip()
            if not magnet.casefold().startswith("magnet:?xt=urn:btih:"):
                raise HTTPException(
                    status_code=422, detail="a valid BitTorrent magnet link is required"
                )
            selected = torrent_actions.choose_magnet(job_id, magnet_uri=magnet, title=req.title)
        elif req.candidate_id:
            selected = torrent_actions.choose(job_id, req.candidate_id)
        else:
            raise HTTPException(status_code=422, detail="candidate_id or magnet_uri is required")
        if selected is None:
            raise HTTPException(status_code=404, detail="torrent candidate not found")
        return {"selected": selected, "status": "resuming"}

    @app.get("/api/series/episodes")
    async def series_episodes(
        show: str = Query(...),
        season: int = Query(...),
        site: str | None = Query(None),
    ) -> dict:
        from bankai.backend import list_series_episodes

        result = await list_series_episodes(show, season=season, site=site)
        if result is None:
            return {"found": False, "site": None, "episodes": []}
        return {
            "found": True,
            "site": result.site,
            "query": result.query,
            "episodes": [
                {"season": e.season, "episode": e.episode, "title": e.title, "url": e.url}
                for e in sorted(result.episodes, key=lambda e: e.episode)
            ],
        }

    # ------------------------------------------------------------------
    # Queue / jobs
    # ------------------------------------------------------------------
    @app.get("/api/queue")
    def queue_list() -> dict:
        return {"jobs": webjobs.snapshot()}

    @app.post("/api/queue/movie")
    async def queue_movie(req: MovieQueueRequest) -> dict:
        from bankai.backend import BatchMovie, build_movie_args

        # The year MUST come from the exact title the user selected (its TVDB
        # entry) or an explicit user prompt. We deliberately do NOT fuzzy-search
        # TVDB by name to guess a year -- that once resolved "Obsession 2026" to
        # a different "Obsession (2019)" and downloaded the wrong movie.
        year = req.year
        if year is None:
            raise HTTPException(status_code=422, detail="year_required")
        source_url = normalize_stream_url(req.url) if req.url else None
        site = req.site
        if source_url:
            try:
                site = _stream_site_from_url(source_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if source_url and site == "filmpalast":
            try:
                mirror_count = await _verify_filmpalast_source(source_url)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="The German source could not be checked. Please try again.",
                ) from exc
            if mirror_count == 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This Filmpalast page no longer has a supported German stream mirror. "
                        "Choose another result or paste a direct German mirror link."
                    ),
                )
        movie = BatchMovie(
            title=req.title,
            german_title=req.german,
            url=source_url,
            year=year,
        )
        args = build_movie_args(movie, site=site)
        return webjobs.enqueue(kind="movie", title=f"{req.title} ({year})", args=args)

    @app.post("/api/queue/show")
    async def queue_show(req: ShowQueueRequest) -> dict:
        from bankai.backend import list_series_episodes

        if req.custom_episodes is not None:
            if not req.custom_episodes:
                raise HTTPException(status_code=400, detail="no custom episodes supplied")
            seen: set[int] = set()
            custom: list[tuple[CustomEpisodeRequest, str, str]] = []
            for ep in req.custom_episodes:
                if ep.episode < 1:
                    raise HTTPException(status_code=400, detail="episode numbers must be positive")
                if ep.episode in seen:
                    raise HTTPException(status_code=400, detail=f"duplicate episode {ep.episode}")
                seen.add(ep.episode)
                source_url = normalize_stream_url(ep.url)
                try:
                    site = _stream_site_from_url(source_url)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                custom.append((ep, site, source_url))

            queued: list[dict] = []
            torrent_group = uuid.uuid4().hex
            torrent_group_size = len(custom)
            for ep, site, source_url in sorted(custom, key=lambda item: item[0].episode):
                q = f"{req.show.strip()} S{req.season:02d}E{ep.episode:02d}"
                args = [
                    "run",
                    q,
                    "--url",
                    source_url,
                    "--site",
                    site,
                    "--kind",
                    "episode",
                    "--season",
                    str(req.season),
                    "--episode",
                    str(ep.episode),
                    "--series-title",
                    req.show.strip(),
                    "--torrent-group",
                    torrent_group,
                    "--torrent-group-size",
                    str(torrent_group_size),
                    "--torrent-group-member",
                    f"S{req.season:02d}E{ep.episode:02d}",
                    "--auto",
                ]
                if ep.title:
                    args.extend(["--episode-title", ep.title.strip()])
                queued.append(webjobs.enqueue(kind="show", title=q, args=args))
            return {"queued": queued, "count": len(queued)}

        result = await list_series_episodes(req.show, season=req.season, site=req.site)
        if result is None:
            raise HTTPException(status_code=404, detail="no episodes found")
        episodes = sorted(result.episodes, key=lambda e: e.episode)
        if req.episodes:
            wanted = set(req.episodes)
            episodes = [e for e in episodes if e.episode in wanted]
        if not episodes:
            raise HTTPException(status_code=400, detail="no matching episodes")
        queued: list[dict] = []
        torrent_group = uuid.uuid4().hex
        torrent_group_size = len(episodes)
        for ep in episodes:
            q = f"{req.show} S{req.season:02d}E{ep.episode:02d}"
            args = [
                "run",
                q,
                "--url",
                ep.url,
                "--site",
                result.site,
                "--kind",
                "episode",
                "--season",
                str(req.season),
                "--episode",
                str(ep.episode),
                "--series-title",
                req.show,
                "--torrent-group",
                torrent_group,
                "--torrent-group-size",
                str(torrent_group_size),
                "--torrent-group-member",
                f"S{req.season:02d}E{ep.episode:02d}",
                "--auto",
            ]
            if ep.title:
                args.extend(["--episode-title", ep.title])
            queued.append(webjobs.enqueue(kind="show", title=q, args=args))
        return {"queued": queued, "count": len(queued)}

    @app.post("/api/queue/{job_id}/cancel")
    def queue_cancel(job_id: str) -> dict:
        from bankai.cli import bgjobs

        if webjobs.cancel_pending(job_id):
            return {"cancelled": True, "pending": True}
        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        ok = job.cancel()
        return {"cancelled": ok, "pending": False}

    @app.post("/api/queue/{job_id}/stop")
    def queue_stop(job_id: str) -> dict:
        job = webjobs.stop_job(job_id)
        if job is None:
            raise HTTPException(status_code=409, detail="only running jobs can be stopped")
        return {"stopped": True, "id": job.id}

    @app.post("/api/queue/{job_id}/continue")
    def queue_continue(job_id: str) -> dict:
        try:
            job = webjobs.continue_job(job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=409, detail="job is not stopped")
        return {"continued": True, "id": job.id, "status": job.status}

    @app.post("/api/queue/{job_id}/force")
    def queue_force(job_id: str) -> dict:
        try:
            job = webjobs.force_start_pending(job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=409, detail="job is no longer queued")
        return {"started": True, "id": job.id, "status": job.status}

    @app.post("/api/queue/{job_id}/priority")
    def queue_priority(job_id: str, req: QueuePriorityRequest) -> dict:
        if req.position < 1:
            raise HTTPException(status_code=422, detail="position must be at least 1")
        position = webjobs.reorder_pending(job_id, req.position)
        if position is None:
            raise HTTPException(status_code=409, detail="job is no longer queued")
        return {"id": job_id, "position": position}

    @app.post("/api/queue/{job_id}/retry")
    def queue_retry(job_id: str) -> dict:
        from bankai.cli import bgjobs

        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return webjobs.enqueue(kind=job.kind, title=job.title, args=job.args)

    @app.post("/api/queue/{job_id}/retry-with-source")
    def queue_retry_with_source(job_id: str, req: SourceRetryRequest) -> dict:
        """Retry a failed movie with a user-supplied German mirror URL."""
        from bankai.cli import bgjobs

        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.kind != "movie":
            raise HTTPException(status_code=409, detail="source links can only replace movie sources")
        if job.status not in {"failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="only failed or cancelled movies can be retried")
        source_url = normalize_stream_url(req.url)
        try:
            site = _stream_site_from_url(source_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        args = _set_cli_option(list(job.args), "--url", source_url)
        args = _set_cli_option(args, "--site", site)
        return webjobs.enqueue(kind=job.kind, title=job.title, args=args)

    @app.delete("/api/queue/{job_id}")
    def queue_delete(job_id: str) -> dict:
        from bankai.cli import bgjobs

        if webjobs.cancel_pending(job_id):
            return {"deleted": True, "pending": True}
        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status == "running":
            job.cancel()
        ok = job.delete()
        if not ok:
            raise HTTPException(status_code=500, detail="job could not be removed from disk")
        return {"deleted": ok, "pending": False}

    @app.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str, lines: int = Query(200, ge=1, le=5000)) -> dict:
        from bankai.cli import bgjobs

        job = bgjobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"id": job.id, "status": job.status, "log": bgjobs.tail(job, lines=lines)}

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------
    @app.get("/api/library")
    def library_list() -> dict:
        entries = media_mod.scan_library()
        transfers = webjobs.transfer_states()
        repacks = webjobs.repack_states()
        out = []
        for e in entries:
            state = review_mod.get_state(e.path)
            if state.stage == "repacking" and state.repack_status in {"done", "failed"}:
                state = review_mod.set_repack(
                    e.path,
                    state.repack_status,
                    percent=state.repack_percent,
                    kind=state.repack_kind,
                    note=state.note,
                )
            # Reconcile the detached transfer job into the per-entry status so
            # the library shows transfer progress as a column (not a queue job).
            try:
                tkey = str(Path(e.path).resolve())
            except OSError:
                tkey = e.path
            tinfo = transfers.get(tkey)
            if tinfo and tinfo["status"] != state.transfer_status:
                state = review_mod.set_transfer(
                    e.path, tinfo["status"], percent=tinfo.get("percent")
                )
            rinfo = repacks.get(tkey)
            if rinfo and rinfo["status"] != state.repack_status:
                state = review_mod.set_repack(
                    e.path,
                    rinfo["status"],
                    percent=rinfo.get("percent"),
                    kind=rinfo.get("kind"),
                    note=rinfo.get("reason"),
                )
            out.append(
                {
                    "kind": e.kind,
                    "path": e.path,
                    "rel_path": e.rel_path,
                    "name": e.name,
                    "size": e.size,
                    "mtime": e.mtime,
                    "series": e.series,
                    "season": e.season,
                    "stage": state.stage,
                    "delay_ms": state.delay_ms,
                    "needs_sync_review": state.needs_sync_review,
                    "sync_confidence": state.sync_confidence,
                    "sync_user_approved": state.sync_user_approved,
                    "duration_delta_seconds": state.duration_delta_seconds,
                    "duration_compatible": state.duration_compatible,
                    "auto_delay_ms": state.auto_delay_ms,
                    "transfer_status": state.transfer_status,
                    "transfer_percent": (
                        tinfo.get("percent", state.transfer_percent)
                        if tinfo
                        else state.transfer_percent
                    ),
                    "repack_status": state.repack_status,
                    "repack_percent": (
                        rinfo.get("percent", state.repack_percent)
                        if rinfo
                        else state.repack_percent
                    ),
                    "repack_kind": state.repack_kind,
                    "german_source_url": state.german_source_url,
                    "torrent_source_url": state.torrent_source_url,
                    "torrent_source_title": state.torrent_source_title,
                }
            )
        return {"entries": out, "library": str(get_settings().output.directory)}

    titles_cache_lock = threading.Lock()
    titles_cache_revision: tuple | None = None
    titles_cache_value: dict | None = None

    def _titles_revision(entries: list) -> tuple:
        """Cheap exact-enough revision used to coalesce dashboard refreshes."""

        files: list[Path] = [
            webjobs._pending_path(),
            review_mod._store_path(),
            posters_mod._store(),
        ]
        root = webjobs.bgjobs.jobs_root()
        files.extend(root.glob("*/meta.json"))
        files.extend(root.glob("*/log"))
        count = 0
        total_size = 0
        revision_xor = 0
        for file in files:
            try:
                stat = file.stat()
            except OSError:
                continue
            count += 1
            total_size += stat.st_size
            revision_xor ^= stat.st_mtime_ns ^ stat.st_size
        library = tuple((entry.path, entry.size, entry.mtime) for entry in entries)
        return (library, count, total_size, revision_xor)

    def _build_titles(entries: list) -> dict:
        """Unified one-row-per-title view merging the library and the queue.

        Each movie/episode appears exactly once: a title being downloaded
        shows its job status; once finished it becomes its library row. Extra
        state (sync, transfer, download progress) is carried as columns, never
        as extra rows.
        """
        # All three views share the same reconciled registry snapshot.  This
        # prevents a single request from scanning every historical job four or
        # more times and gives the response a consistent point-in-time view.
        with webjobs.dashboard_read():
            transfers = webjobs.transfer_states()
            repacks = webjobs.repack_states()
            jobs = webjobs.snapshot()  # already excludes transfer jobs
        review_states = review_mod.all_states()
        poster_cache = posters_mod.all_cached()
        # Tombstones for files the user explicitly deleted (stage="deleted"),
        # keyed by resolved path — used to relabel their finished job row.
        deleted_paths = {k for k, st in review_states.items() if st.stage == "deleted"}
        # During the tiny atomic-replacement window a repacked file may be
        # absent from a directory scan. Reserve its path/title so the original
        # pipeline job never flashes up as a separate temporary row.
        repacking_paths = {
            k
            for k, st in review_states.items()
            if st.stage == "repacking" or st.repack_status == "repacking"
        }
        repacking_norms = {_norm_title(_clean_title(Path(k).name)) for k in repacking_paths}
        # Map normalised title -> newest job, so a finished library row can
        # still surface its download log (expandable row) and be re-run.
        job_by_norm: dict[str, dict] = {}
        created_by_norm: dict[str, float] = {}
        for j in jobs:
            nt = _norm_title(j.get("title", ""))
            started_at = float(j.get("started_at") or 0)
            if started_at and (nt not in created_by_norm or started_at < created_by_norm[nt]):
                created_by_norm[nt] = started_at
            cur = job_by_norm.get(nt)
            if cur is None or (j.get("started_at") or 0) >= (cur.get("started_at") or 0):
                job_by_norm[nt] = j
        rows: list[dict] = []
        lib_paths: set[str] = set()
        lib_norms: set[str] = set()
        for e in entries:
            try:
                rp = str(Path(e.path).resolve())
            except OSError:
                rp = e.path
            state = review_states.get(rp, review_mod.ReviewState(path=e.path))
            if state.stage == "repacking" and state.repack_status in {"done", "failed"}:
                state = review_mod.set_repack(
                    e.path,
                    state.repack_status,
                    percent=state.repack_percent,
                    kind=state.repack_kind,
                    note=state.note,
                )
            tinfo = transfers.get(rp)
            if tinfo and tinfo["status"] != state.transfer_status:
                state = review_mod.set_transfer(
                    e.path, tinfo["status"], percent=tinfo.get("percent")
                )
            rinfo = repacks.get(rp)
            if rinfo and rinfo["status"] != state.repack_status:
                state = review_mod.set_repack(
                    e.path,
                    rinfo["status"],
                    percent=rinfo.get("percent"),
                    kind=rinfo.get("kind"),
                    note=rinfo.get("reason"),
                )
            lib_paths.add(rp)
            norm = _norm_title(e.name)
            lib_norms.add(norm)
            clean = _clean_title(e.name)
            poster_key = ("show:" if e.kind == "episode" else "movie:") + (
                _norm_title(e.series or clean) if e.kind == "episode" else norm
            )
            posters_mod.ensure(
                poster_key,
                e.series or clean,
                "series" if e.kind == "episode" else "movie",
                known=poster_cache,
            )
            poster_entry = poster_cache.get(poster_key) or {}
            related = job_by_norm.get(norm)
            related_is_newer = bool(
                related and float(related.get("started_at") or 0) > float(e.mtime or 0)
            )
            observed_created_at = min(e.created_at, created_by_norm.get(norm, e.created_at))
            if state.created_at is None:
                state = review_mod.ensure_created_at(e.path, observed_created_at)
            created_at = state.created_at or observed_created_at
            updated_at = max(
                e.mtime,
                state.updated_at or 0,
                (related.get("finished_at") or related.get("started_at") or 0) if related else 0,
            )
            rows.append(
                {
                    "row_kind": "library",
                    "id": e.path,
                    "title": clean,
                    "kind": e.kind,
                    "year": _extract_year(e.name) or poster_entry.get("year"),
                    "poster": poster_entry.get("url"),
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "done_at": updated_at,
                    "path": e.path,
                    "rel_path": e.rel_path,
                    "name": e.name,
                    "size": e.size,
                    "mtime": e.mtime,
                    "series": e.series,
                    "season": e.season,
                    "stage": "review" if state.stage == "deleted" else state.stage,
                    "reason": (
                        rinfo.get("reason")
                        if rinfo and rinfo.get("status") == "failed"
                        else related.get("reason")
                        if related_is_newer and related
                        else None
                    ),
                    "reason_detail": (
                        rinfo.get("reason")
                        if rinfo and rinfo.get("status") == "failed"
                        else related.get("reason_detail")
                        if related_is_newer and related
                        else None
                    ),
                    "delay_ms": state.delay_ms,
                    "needs_sync_review": state.needs_sync_review,
                    "sync_confidence": state.sync_confidence,
                    "sync_user_approved": state.sync_user_approved,
                    "duration_delta_seconds": state.duration_delta_seconds,
                    "duration_compatible": state.duration_compatible,
                    "auto_delay_ms": state.auto_delay_ms,
                    "transfer_status": state.transfer_status,
                    "transfer_percent": (
                        tinfo.get("percent", state.transfer_percent)
                        if tinfo
                        else state.transfer_percent
                    ),
                    "repack_status": state.repack_status,
                    "repack_percent": (
                        rinfo.get("percent", state.repack_percent)
                        if rinfo
                        else state.repack_percent
                    ),
                    "repack_kind": state.repack_kind,
                    "repack_label": rinfo.get("label") if rinfo else None,
                    "job_id": related["id"] if related else None,
                    "job_status": related.get("status") if related_is_newer and related else None,
                    "step_label": related.get("step_label")
                    if related_is_newer and related
                    else None,
                    "overall_percent": related.get("overall_percent")
                    if related_is_newer and related
                    else None,
                    "total_steps": related.get("total_steps")
                    if related_is_newer and related
                    else None,
                    "pending": bool(related.get("pending"))
                    if related_is_newer and related
                    else False,
                    "action_required": bool(related.get("action_required"))
                    if related_is_newer and related
                    else False,
                    "queue_position": related.get("queue_position")
                    if related_is_newer and related
                    else None,
                    "queue_total": related.get("queue_total")
                    if related_is_newer and related
                    else None,
                    "german_source_url": state.german_source_url
                    or (related.get("german_source_url") if related else None),
                    "torrent_source_url": state.torrent_source_url
                    or (related.get("torrent_source_url") if related else None),
                    "torrent_source_title": state.torrent_source_title
                    or (related.get("torrent_source_title") if related else None),
                }
            )
        # Collapse queue jobs that have no finished library file yet into one
        # row per title (prefer the most relevant attempt).
        best_by_norm: dict[str, dict] = {}
        for j in jobs:
            fp = j.get("final_path")
            if fp:
                try:
                    rfp = str(Path(fp).resolve())
                except OSError:
                    rfp = fp
                if rfp in lib_paths or rfp in repacking_paths:
                    continue  # already represented by its library row
            nt = _norm_title(j.get("title", ""))
            if nt in lib_norms or nt in repacking_norms:
                continue  # a library file already covers this title
            cur = best_by_norm.get(nt)
            if cur is None or _job_priority(j) >= _job_priority(cur):
                best_by_norm[nt] = j
        for j in best_by_norm.values():
            clean = _clean_title(j.get("title", ""))
            is_ep = j.get("kind") == "show"
            poster_key = ("show:" if is_ep else "movie:") + _norm_title(clean)
            posters_mod.ensure(
                poster_key,
                clean,
                "series" if is_ep else "movie",
                known=poster_cache,
            )
            poster_entry = poster_cache.get(poster_key) or {}
            fp = j.get("final_path")
            try:
                is_deleted = bool(fp) and str(Path(fp).resolve()) in deleted_paths
            except OSError:
                is_deleted = fp in deleted_paths
            rows.append(
                {
                    "row_kind": "job",
                    "id": j["id"],
                    "title": clean,
                    "kind": "episode" if is_ep else "movie",
                    "year": _extract_year(j.get("title", ""))
                    or poster_entry.get("year"),
                    "poster": poster_entry.get("url"),
                    "created_at": j.get("started_at"),
                    "updated_at": j.get("updated_at")
                    or j.get("finished_at")
                    or j.get("started_at"),
                    "done_at": j.get("finished_at") or j.get("started_at"),
                    "path": j.get("final_path"),
                    "rel_path": None,
                    "name": j.get("title", ""),
                    "size": None,
                    "mtime": j.get("started_at"),
                    "series": None,
                    "season": None,
                    "stage": "deleted" if is_deleted else None,
                    "reason": j.get("reason"),
                    "reason_code": j.get("reason_code"),
                    "reason_detail": j.get("reason_detail"),
                    "delay_ms": 0,
                    "needs_sync_review": False,
                    "sync_confidence": None,
                    "sync_user_approved": False,
                    "duration_delta_seconds": None,
                    "duration_compatible": None,
                    "auto_delay_ms": 0,
                    "transfer_status": "idle",
                    "transfer_percent": 0.0,
                    "repack_status": "idle",
                    "repack_percent": 0.0,
                    "repack_kind": None,
                    "repack_label": None,
                    "job_id": j["id"],
                    "job_status": "deleted" if is_deleted else j.get("status"),
                    "step_label": j.get("step_label"),
                    "overall_percent": j.get("overall_percent"),
                    "total_steps": j.get("total_steps"),
                    "pending": j.get("pending", False),
                    "action_required": j.get("action_required", False),
                    "queue_position": j.get("queue_position"),
                    "queue_total": j.get("queue_total"),
                    "german_source_url": j.get("german_source_url"),
                    "torrent_source_url": j.get("torrent_source_url"),
                    "torrent_source_title": j.get("torrent_source_title"),
                }
            )
        return {"rows": rows, "library": str(get_settings().output.directory)}

    @app.get("/api/titles")
    def titles_list() -> dict:
        nonlocal titles_cache_revision, titles_cache_value

        entries = media_mod.scan_library()
        revision = _titles_revision(entries)
        with titles_cache_lock:
            if titles_cache_value is not None and revision == titles_cache_revision:
                return titles_cache_value
            value = _build_titles(entries)
            titles_cache_revision = revision
            titles_cache_value = value
            return value

    @app.post("/api/titles/redo")
    def titles_redo(req: PathRequest) -> dict:
        """Re-run the pipeline for a title, reusing its original source args.

        ``req.path`` may be a library file path OR a plain title. We find the
        most recent movie/show job with a matching normalised title and
        re-enqueue its exact args (same german title + hoster URL).
        """
        from bankai.cli import bgjobs

        raw = req.path
        want = _norm_title(Path(raw).name if ("/" in raw or "\\" in raw) else raw)
        best = None
        for j in bgjobs.list_jobs():
            if j.kind not in ("movie", "show"):
                continue
            if _norm_title(j.title) != want:
                continue
            if best is None or (j.started_at or 0) >= (best.started_at or 0):
                best = j
        if best is None or not best.args:
            raise HTTPException(status_code=404, detail="no previous run found to redo")
        args = list(best.args)
        if not args or args[0] != "run":
            raise HTTPException(
                status_code=409,
                detail="the previous run cannot be restarted as a full pipeline",
            )
        # A Redo is always a fresh end-to-end pipeline.  Older or manually
        # created job metadata may contain experimental resume/skip flags;
        # never carry those into a user-requested full restart.
        shortcut_flags = {
            "--resume",
            "--reuse-extract",
            "--reuse-torrent",
            "--skip-extract",
            "--skip-torrent",
            "--skip-sync",
        }
        shortcut_options = {"--from-stage", "--start-at"}
        fresh_args: list[str] = []
        skip_value = False
        for arg in args:
            if skip_value:
                skip_value = False
                continue
            if arg in shortcut_flags:
                continue
            if arg in shortcut_options:
                skip_value = True
                continue
            if any(arg.startswith(f"{option}=") for option in shortcut_options):
                continue
            fresh_args.append(arg)
        args = fresh_args
        output: Path | None = None
        if "/" in raw or "\\" in raw:
            output = _safe_library_output(raw)
        elif best.final_path:
            output = _safe_library_output(best.final_path)
        if output is not None:
            # An explicit output bypasses the pipeline's normal
            # skip-existing guard. RemuxWorker writes and verifies a sibling
            # working file, then atomically replaces this path only on success.
            args = _set_cli_option(args, "--out", str(output))
        job = webjobs.enqueue(kind=best.kind, title=best.title, args=args)
        return {
            "redo": job,
            "title": best.title,
            "fresh": True,
            "stages": ["extract", "torrent", "sync", "remux"],
        }

    @app.get("/api/qbittorrent/torrents")
    async def qbittorrent_torrents() -> dict:
        """Return every torrent visible to the configured qBittorrent user."""
        from bankai.torrent.qbittorrent import QBittorrentClient

        try:
            async with QBittorrentClient() as client:
                torrents = await client.list_torrents()
        except Exception as exc:
            log.warning("qBittorrent listing failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="qBittorrent could not be reached. Check its connection settings.",
            ) from exc
        return {
            "items": [
                {
                    "hash": torrent.hash,
                    "name": torrent.name,
                    "state": torrent.state,
                    "progress": torrent.progress,
                    "size_bytes": torrent.size_bytes,
                    "seeds": torrent.seeds,
                    "peers": torrent.peers,
                    "seeds_total": torrent.seeds_total,
                    "peers_total": torrent.peers_total,
                    "dlspeed": torrent.dlspeed,
                    "upspeed": torrent.upspeed,
                    "eta": torrent.eta,
                    "added_on": torrent.added_on,
                }
                for torrent in torrents
            ]
        }

    @app.get("/api/media/info")
    def media_info(path: str = Query(...)) -> dict:
        p = _safe_path(path)
        info = media_mod.probe(p)
        if info is None:
            raise HTTPException(status_code=422, detail="could not probe file")
        state = review_mod.get_state(str(p))
        return {
            "path": info.path,
            "size": info.size,
            "duration": info.duration,
            "video_codec": info.video_codec,
            "width": info.width,
            "height": info.height,
            "video_fps": info.video_fps,
            "has_german": info.has_german,
            "browser_playable": info.browser_playable,
            "stage": state.stage,
            "delay_ms": state.delay_ms,
            "needs_sync_review": state.needs_sync_review,
            "sync_confidence": state.sync_confidence,
            "sync_user_approved": state.sync_user_approved,
            "duration_delta_seconds": state.duration_delta_seconds,
            "duration_compatible": state.duration_compatible,
            "auto_delay_ms": state.auto_delay_ms,
            "source_fps": state.source_fps,
            "source_video_fps": state.source_video_fps,
            "reference_fps": state.reference_fps,
            "drift_ratio": state.drift_ratio,
            "german_source_url": state.german_source_url,
            "torrent_source_url": state.torrent_source_url,
            "torrent_source_title": state.torrent_source_title,
            "audio_tracks": [
                {
                    "index": t.index,
                    "order": t.order,
                    "language": t.language,
                    "title": t.title,
                    "codec": t.codec,
                    "channels": t.channels,
                    "default": t.default,
                    "is_german": t.is_german,
                    "sample_rate": t.sample_rate,
                    "duration": t.duration,
                }
                for t in info.audio_tracks
            ],
        }

    @app.delete("/api/library/file")
    def library_delete(req: PathRequest) -> dict:
        p = _safe_path(req.path)
        try:
            p.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        # Keep a tombstone (stage="deleted") so the title shows as "Deleted"
        # rather than reverting to its finished job's "Done" row.
        review_mod.set_stage(str(p), "deleted")
        # Remove the now-empty movie/show folder(s) left behind, up to (but not
        # including) the library root, so no dead folders linger.
        try:
            root = _library_root().resolve()
            cur = p.parent.resolve()
            while cur != root and root in cur.parents and not any(cur.iterdir()):
                cur.rmdir()
                cur = cur.parent
        except OSError:
            pass
        return {"deleted": True, "path": str(p)}

    # ------------------------------------------------------------------
    # Streaming + transcode preview
    # ------------------------------------------------------------------
    @app.get("/api/media/stream")
    def media_stream(request: Request, path: str = Query(...)) -> Response:
        p = _safe_path(path)
        file_size = p.stat().st_size
        content_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        range_header = request.headers.get("range")
        if range_header is None:
            return FileResponse(p, media_type=content_type)
        start, end = _parse_range(range_header, file_size)
        length = end - start + 1

        def iter_file():
            with p.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1024 * 256, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            iter_file(), status_code=206, headers=headers, media_type=content_type
        )

    @app.get("/api/media/transcode")
    def media_transcode(
        path: str = Query(...),
        t: float = Query(0.0, ge=0.0),
        audio: int = Query(0, ge=0),
    ) -> Response:
        p = _safe_path(path)
        ffmpeg = media_mod.ffmpeg_bin()
        if ffmpeg is None:
            raise HTTPException(status_code=501, detail="ffmpeg not available")
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if t > 0:
            cmd += ["-ss", str(t)]
        cmd += [
            "-i",
            str(p),
            "-map",
            "0:v:0",
            "-map",
            f"0:a:{audio}?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
        import subprocess

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def stream():
            try:
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(1024 * 256)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.kill()

        return StreamingResponse(stream(), media_type="video/mp4")

    # ------------------------------------------------------------------
    # Audio alignment (waveform review)
    # ------------------------------------------------------------------
    @app.get("/api/media/waveform")
    def media_waveform(
        path: str = Query(...),
        stream: int = Query(..., ge=0),
        start: float = Query(0.0, ge=0.0),
        dur: float = Query(30.0, gt=0.0, le=1800.0),
        bins: int = Query(1000, ge=50, le=4000),
    ) -> dict:
        """Adaptive loudness envelope for one audio-track window.

        Only the requested ``[start, start+dur]`` slice is decoded, so this
        stays fast even on weak hardware (a full-movie decode would take
        minutes). Close zoom levels use full-band PCM RMS for millisecond-scale
        detail. Wide navigation overviews use aggressively downsampled PCM so
        an entire movie does not require an expensive real-time loudness pass.
        Returns base64 uint8 peaks; the client maps pixels to peaks using
        ``start``/``dur``.
        """
        import base64
        import subprocess

        p = _safe_path(path)
        try:
            st = p.stat()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        detailed = dur <= 180.0
        key = ("adaptive-v4", str(p), st.st_mtime_ns, stream, round(start, 2), round(dur, 2), bins)
        hit = _waveform_cache_get(key)
        if hit is not None:
            return hit
        ffmpeg = media_mod.ffmpeg_bin()
        if ffmpeg is None:
            raise HTTPException(status_code=501, detail="ffmpeg not available")
        sample_rate = 48000 if detailed else 200
        cmd = [
            ffmpeg,
            "-v",
            "error",
            "-nostats",
            "-ss",
            str(start),
            "-t",
            str(dur),
            "-i",
            str(p),
            "-map",
            f"0:{stream}",
            "-vn",
            *(
                []
                if detailed
                else ["-af", "aformat=channel_layouts=mono,aeval=abs(val(0))"]
            ),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "pipe:1",
        ]
        try:
            with _ffmpeg_slot():
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=60,
                )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="waveform decode timed out") from exc
        if proc.returncode != 0:
            raise HTTPException(status_code=422, detail="could not decode audio track")
        samples = array("h")
        samples.frombytes(proc.stdout[: len(proc.stdout) - (len(proc.stdout) % 2)])
        if sys.byteorder != "little":  # ffmpeg's s16le output is fixed
            samples.byteswap()
        peaks = _waveform_envelope(
            samples,
            bins,
            smoothing_radius=0 if detailed else 1,
        )
        out = {
            "start": start,
            "dur": dur,
            "bins": len(peaks),
            "peaks": base64.b64encode(bytes(peaks)).decode("ascii"),
            "detail": "pcm" if detailed else "pcm-overview",
        }
        _waveform_cache_put(key, out)
        return out

    @app.get("/api/media/audioclip")
    def media_audioclip(
        path: str = Query(...),
        stream: int = Query(..., ge=0),
        start: float = Query(0.0, ge=0.0),
        dur: float = Query(10.0, gt=0.0, le=120.0),
        lead: float = Query(0.0, ge=0.0, le=120.0),
        rate: float = Query(1.0, ge=0.5, le=2.0),
    ) -> Response:
        """Return a drift-aware, disk-cached MP3 clip for exact A/B playback."""
        import subprocess

        p = _safe_path(path)
        ffmpeg = media_mod.ffmpeg_bin()
        if ffmpeg is None:
            raise HTTPException(status_code=501, detail="ffmpeg not available")
        st = p.stat()

        def build(out: Path) -> None:
            audible = max(0.05, dur - lead)
            source_dur = audible * rate + 0.25
            filters: list[str] = []
            if abs(rate - 1.0) > 1e-5:
                filters.append(f"atempo={rate:.8f}")
            if lead > 1e-4:
                filters.append(f"adelay={round(lead * 1000)}:all=1")
            filters.append(f"apad=whole_dur={dur:.6f}")
            with _ffmpeg_slot():
                subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        str(start),
                        "-t",
                        str(source_dur),
                        "-i",
                        str(p),
                        "-map",
                        f"0:{stream}",
                        "-ac",
                        "2",
                        "-af",
                        ",".join(filters),
                        "-t",
                        str(dur),
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        "-f",
                        "mp3",
                        "-y",
                        str(out),
                    ],
                    timeout=120,
                    check=False,
                )

        key = _audio_clip_cache_key(
            p,
            mtime_ns=st.st_mtime_ns,
            stream=stream,
            start=start,
            dur=dur,
            lead=lead,
            rate=rate,
        )
        clip = _cached_clip(key, "mp3", build)
        return FileResponse(clip, media_type="audio/mpeg")

    @app.get("/api/media/audioclip/cache")
    def media_audioclip_cache(
        path: str = Query(...),
        stream: int = Query(..., ge=0),
        segment: float = Query(30.0, ge=1.0, le=60.0),
        delay_ms: int = Query(0),
        rate: float = Query(1.0, ge=0.5, le=2.0),
    ) -> dict:
        """Return reference-time ranges whose German preview is disk-cached.

        The review player aligns German preview clips to the same reusable
        reference-time segment grid as video. This read-only inspection uses
        that exact affine delay/rate mapping and never starts ffmpeg.
        """
        p = _safe_path(path)
        info = media_mod.probe(p)
        if info is None or info.duration is None or info.duration <= 0:
            return {"ranges": []}
        total_duration = float(info.duration)
        st = p.stat()
        delay = delay_ms / 1000.0
        ranges: list[dict[str, float]] = []
        segment_count = max(1, math.ceil(total_duration / segment))
        for index in range(segment_count):
            reference_start = index * segment
            span = max(
                1.0,
                min(120.0, total_duration - reference_start, segment * 2),
            )
            source_start = (reference_start - delay) * rate
            lead = min(span, -source_start / rate) if source_start < 0 else 0.0
            key = _audio_clip_cache_key(
                p,
                mtime_ns=st.st_mtime_ns,
                stream=stream,
                start=max(0.0, source_start),
                dur=span,
                lead=lead,
                rate=rate,
            )
            cached = _clip_cache_path(key, "mp3")
            if cached.exists() and cached.stat().st_size > 0:
                ranges.append(
                    {
                        "start": reference_start,
                        "end": reference_start + span,
                    }
                )
        return {"ranges": ranges}

    @app.get("/api/media/videoclip/cache")
    def media_videoclip_cache(
        path: str = Query(...),
        segment: float = Query(30.0, ge=1.0, le=60.0),
        height: int = Query(480, ge=180, le=1080),
        audio: int | None = Query(None, ge=0),
    ) -> dict:
        """Return the reference-time ranges already cached on disk.

        This is a read-only cache inspection: it never starts ffmpeg. The
        segment grid mirrors the review player's preview URL construction so
        every returned range can play without waiting for a new transcode.
        """
        p = _safe_path(path)
        info = media_mod.probe(p)
        if info is None or info.duration is None or info.duration <= 0:
            return {"ranges": []}
        total_duration = float(info.duration)
        st = p.stat()
        ranges: list[dict[str, float]] = []
        segment_count = max(1, math.ceil(total_duration / segment))
        for index in range(segment_count):
            start = index * segment
            span = max(
                1.0,
                min(120.0, total_duration - start, segment * 2),
            )
            key = _video_clip_cache_key(
                p,
                mtime_ns=st.st_mtime_ns,
                start=start,
                dur=span,
                height=height,
                audio=audio,
            )
            cached = _clip_cache_path(key, "mp4")
            if cached.exists() and cached.stat().st_size > 0:
                ranges.append({"start": start, "end": start + span})
        return {"ranges": ranges}

    @app.get("/api/media/videoclip")
    def media_videoclip(
        path: str = Query(...),
        start: float = Query(0.0, ge=0.0),
        dur: float = Query(30.0, gt=0.0, le=120.0),
        height: int = Query(480, ge=180, le=1080),
        audio: int | None = Query(None, ge=0),
    ) -> Response:
        """Return a short, disk-cached H.264 video clip of the visible window.

        The reference audio is muxed into this same clip when requested, so
        the browser plays the HQ picture and English track from one media
        clock. This avoids false sync offsets caused by separately starting an
        audio element and a video element.
        """
        import subprocess

        p = _safe_path(path)
        ffmpeg = media_mod.ffmpeg_bin()
        if ffmpeg is None:
            raise HTTPException(status_code=501, detail="ffmpeg not available")
        st = p.stat()

        def build(out: Path) -> None:
            audio_args = (
                ["-map", f"0:{audio}", "-c:a", "aac", "-b:a", "128k"]
                if audio is not None
                else ["-an"]
            )
            with _ffmpeg_slot():
                subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        str(start),
                        "-t",
                        str(dur),
                        "-i",
                        str(p),
                        "-map",
                        "0:v:0",
                        *audio_args,
                        "-vf",
                        f"scale=-2:{height}",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-tune",
                        "zerolatency",
                        "-crf",
                        "30",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        "-f",
                        "mp4",
                        "-y",
                        str(out),
                    ],
                    timeout=300,
                    check=False,
                )

        key = _video_clip_cache_key(
            p,
            mtime_ns=st.st_mtime_ns,
            start=start,
            dur=dur,
            height=height,
            audio=audio,
        )
        clip = _cached_clip(key, "mp4", build)
        return FileResponse(clip, media_type="video/mp4")

    # ------------------------------------------------------------------
    # Review / approval workflow
    # ------------------------------------------------------------------
    @app.post("/api/review/delay")
    def review_delay(req: DelayRequest) -> dict:
        p = _safe_path(req.path)
        state = review_mod.set_delay(str(p), req.delay_ms)
        return review_mod.to_dict(state)

    @app.post("/api/review/repack")
    def review_repack(req: DelayRequest) -> dict:
        p = _safe_path(req.path)
        if req.atempo is not None and abs(req.atempo - 1.0) > 1e-4:
            result = media_mod.repack_audio_drift(
                p,
                delay_ms=req.delay_ms,
                atempo=req.atempo,
                track_index=req.track_index,
            )
        else:
            result = media_mod.repack_audio_delay(p, delay_ms=req.delay_ms)
        if not result.ok:
            raise HTTPException(status_code=422, detail=result.message)
        review_mod.set_delay(str(p), req.delay_ms)
        return {"ok": True, "message": result.message, "delay_ms": result.delay_ms}

    @app.post("/api/review/approve")
    def review_approve(req: ApproveRequest) -> dict:
        p = _safe_path(req.path)
        current = review_mod.get_state(str(p))
        delay_ms = current.delay_ms if req.delay_ms is None else req.delay_ms
        needs_repack = delay_ms != current.delay_ms or (
            req.atempo is not None and abs(req.atempo - 1.0) > 1e-4
        )
        if needs_repack:
            args = ["review-repack", str(p), "--delay-ms", str(delay_ms)]
            if req.atempo is not None:
                args.extend(["--atempo", str(req.atempo)])
            if req.track_index is not None:
                args.extend(["--track-index", str(req.track_index)])
            job = webjobs.enqueue(kind="repack", title=f"Repack {p.name}", args=args)
            review_mod.set_sync_user_approved(str(p))
            state = review_mod.set_repack(str(p), "repacking", percent=0.0, kind="audio")
            return {**review_mod.to_dict(state), "background": True, "job": job}
        review_mod.set_sync_user_approved(str(p))
        state = review_mod.set_stage(str(p), "approved")
        return {**review_mod.to_dict(state), "background": False}

    @app.post("/api/review/replace-torrent")
    def review_replace_torrent(req: ReplaceTorrentRequest) -> dict:
        p = _safe_path(req.path)
        query = req.query.strip()
        if not query:
            raise HTTPException(status_code=422, detail="torrent query required")
        if req.kind not in {"movie", "episode"}:
            raise HTTPException(status_code=422, detail="kind must be movie or episode")
        args = ["review-replace-torrent", str(p), "--query", query]
        args.extend(["--kind", req.kind])
        if req.series_title:
            args.extend(["--series-title", req.series_title.strip()])
        if req.season is not None:
            args.extend(["--season", str(req.season)])
        if req.episode is not None:
            args.extend(["--episode", str(req.episode)])
        if req.target_runtime_seconds is not None:
            args.extend(["--target-runtime-seconds", str(req.target_runtime_seconds)])
        candidate = req.candidate
        if req.magnet_uri:
            magnet = req.magnet_uri.strip()
            if not magnet.casefold().startswith("magnet:?xt=urn:btih:"):
                raise HTTPException(
                    status_code=422, detail="a valid BitTorrent magnet link is required"
                )
            candidate = TorrentCandidateRequest(
                title=query,
                indexer="Manual magnet",
                download_url=magnet,
                magnet_uri=magnet,
            )
        if candidate is not None:
            args.extend(["--candidate-json", candidate.model_dump_json()])
        job = webjobs.enqueue(
            kind="torrent_replace",
            title=f"Replace torrent {p.name}",
            args=args,
        )
        review_mod.set_sync_user_approved(str(p), False)
        state = review_mod.set_repack(str(p), "repacking", percent=0.0, kind="torrent")
        return {**review_mod.to_dict(state), "background": True, "job": job}

    @app.post("/api/review/approve-batch")
    def review_approve_batch(req: PathsRequest) -> dict:
        approved: list[dict] = []
        errors: list[dict] = []
        for raw in req.paths:
            try:
                p = _safe_path(raw)
            except HTTPException as exc:
                errors.append({"path": raw, "detail": str(exc.detail)})
                continue
            review_mod.set_sync_user_approved(str(p))
            state = review_mod.set_stage(str(p), "approved")
            approved.append(review_mod.to_dict(state))
        return {"approved": approved, "count": len(approved), "errors": errors}

    @app.post("/api/review/transfer")
    def review_transfer(req: PathRequest) -> dict:
        p = _safe_path(req.path)
        state = review_mod.get_state(str(p))
        if state.stage not in {"approved", "transferred"}:
            raise HTTPException(status_code=409, detail="file must be approved before transfer")
        kind = "show" if "Shows" in p.parts else "movie"
        args = ["transfer-run", str(p), "--kind", kind]
        job = webjobs.enqueue(kind="transfer", title=f"Transfer {p.name}", args=args)
        review_mod.set_transfer(str(p), "transferring", percent=0.0)
        return {"transfer": job}

    @app.post("/api/review/transfer-batch")
    def review_transfer_batch(req: PathsRequest) -> dict:
        """Transfer every approved path (or all approved files when omitted)."""
        targets: list[Path] = []
        errors: list[dict] = []
        if req.paths:
            for raw in req.paths:
                try:
                    targets.append(_safe_path(raw))
                except HTTPException as exc:
                    errors.append({"path": raw, "detail": str(exc.detail)})
        else:
            for path_str, state in review_mod.all_states().items():
                if state.stage == "approved":
                    try:
                        targets.append(_safe_path(path_str))
                    except HTTPException:
                        continue
        jobs: list[dict] = []
        skipped: list[dict] = []
        for p in targets:
            state = review_mod.get_state(str(p))
            if state.stage not in {"approved", "transferred"}:
                skipped.append({"path": str(p), "stage": state.stage})
                continue
            kind = "show" if "Shows" in p.parts else "movie"
            args = ["transfer-run", str(p), "--kind", kind]
            jobs.append(webjobs.enqueue(kind="transfer", title=f"Transfer {p.name}", args=args))
            review_mod.set_transfer(str(p), "transferring", percent=0.0)
        return {"transferred": jobs, "count": len(jobs), "skipped": skipped, "errors": errors}

    # ------------------------------------------------------------------
    # Server page (media-server contents)
    # ------------------------------------------------------------------
    @app.get("/api/server/contents")
    def server_contents(rescan: bool = Query(False)) -> dict:
        if rescan:
            media_mod.invalidate_server_cache()
        movies = media_mod.scan_server("movie", use_cache=not rescan)
        shows = media_mod.scan_server("show", use_cache=not rescan)
        return {
            "movies": [
                {
                    "name": t.name,
                    "present": t.present,
                    "location": t.location,
                    "directory": t.directory,
                }
                for t in movies
            ],
            "shows": [
                {
                    "name": t.name,
                    "present": t.present,
                    "location": t.location,
                    "directory": t.directory,
                }
                for t in shows
            ],
        }

    @app.get("/api/server/show")
    def server_show(path: str = Query(...)) -> dict:
        s = get_settings()
        target = Path(path).resolve()
        allowed = [Path(d).resolve() for d in s.web.server_show_dirs]
        if not any(target == a or a in target.parents for a in allowed):
            raise HTTPException(status_code=403, detail="path not under a configured show dir")
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="not a directory")
        seasons = media_mod.scan_server_show(target)
        return {
            "path": str(target),
            "seasons": [
                {
                    "name": se.name,
                    "season": se.season,
                    "episodes": [
                        {"name": e.name, "path": e.path, "size": e.size} for e in se.episodes
                    ],
                }
                for se in seasons
            ],
        }

    @app.post("/api/server/rename")
    def server_rename(req: ServerRenameRequest) -> dict:
        """Rename a media-server movie or one episode within configured roots."""
        if req.kind not in {"movie", "episode"}:
            raise HTTPException(status_code=422, detail="kind must be movie or episode")
        try:
            title = _validate_media_title(req.title)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        settings = get_settings()
        roots = settings.web.server_movie_dirs if req.kind == "movie" else settings.web.server_show_dirs
        allowed = [Path(root).resolve() for root in roots]
        target = Path(req.path).resolve()
        if not any(root in target.parents for root in allowed):
            raise HTTPException(status_code=403, detail="path is outside configured server directories")
        if not target.exists():
            raise HTTPException(status_code=404, detail="server item not found")

        renamed_pairs: list[tuple[Path, Path]] = []
        folder_moved = False
        old_folder = target if target.is_dir() else None
        final_path = target
        try:
            if req.kind == "episode":
                if not target.is_file():
                    raise HTTPException(status_code=422, detail="episode path is not a file")
                destination = target.with_name(f"{title}{target.suffix}")
                if destination.exists() and destination != target:
                    raise HTTPException(status_code=409, detail="an episode with that title already exists")
                if destination != target:
                    target.rename(destination)
                    renamed_pairs.append((target, destination))
                final_path = destination
            elif target.is_file():
                destination = target.with_name(f"{title}{target.suffix}")
                if destination.exists() and destination != target:
                    raise HTTPException(status_code=409, detail="a movie with that title already exists")
                if destination != target:
                    target.rename(destination)
                    renamed_pairs.append((target, destination))
                final_path = destination
            else:
                destination = target.parent / title
                if destination.exists() and destination != target:
                    raise HTTPException(status_code=409, detail="a movie folder with that title already exists")
                # Rename matching video/sidecar basenames before the folder so
                # the movie directory and its contents stay consistently named.
                for child in list(target.iterdir()):
                    if not child.is_file() or child.stem != target.name:
                        continue
                    renamed = child.with_name(f"{title}{child.suffix}")
                    if renamed.exists() and renamed != child:
                        raise HTTPException(status_code=409, detail=f"{renamed.name} already exists")
                    if renamed != child:
                        child.rename(renamed)
                        renamed_pairs.append((child, renamed))
                if destination != target:
                    target.rename(destination)
                    folder_moved = True
                final_path = destination
        except HTTPException:
            for original, renamed in reversed(renamed_pairs):
                if renamed.exists() and not original.exists():
                    renamed.rename(original)
            raise
        except OSError as exc:
            try:
                if folder_moved and old_folder is not None and final_path.exists():
                    final_path.rename(old_folder)
                for original, renamed in reversed(renamed_pairs):
                    current = old_folder / renamed.name if old_folder is not None else renamed
                    if current.exists() and not original.exists():
                        current.rename(original)
            except OSError:
                log.exception("Could not roll back server rename %s", target)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        media_mod.invalidate_server_cache()
        return {
            "renamed": final_path != target,
            "kind": req.kind,
            "path": str(final_path),
            "name": title,
            "folder_renamed": folder_moved,
        }

    @app.get("/api/server/dirs")
    def server_dirs() -> dict:
        s = get_settings()
        return {
            "movie_dirs": [str(p) for p in s.web.server_movie_dirs],
            "show_dirs": [str(p) for p in s.web.server_show_dirs],
        }

    @app.post("/api/server/dirs")
    def server_dirs_add(req: ServerDirRequest) -> dict:
        if req.kind not in {"movie", "show"}:
            raise HTTPException(status_code=400, detail="kind must be movie or show")
        path = req.path.strip()
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        key = "web.server_movie_dirs" if req.kind == "movie" else "web.server_show_dirs"
        s = get_settings()
        current = [
            str(p)
            for p in (s.web.server_movie_dirs if req.kind == "movie" else s.web.server_show_dirs)
        ]
        if path not in current:
            current.append(path)
        from bankai.cli.main import _set_config_value

        _set_config_value(key, current)
        reset_settings_cache()
        media_mod.invalidate_server_cache()
        return {"kind": req.kind, "dirs": current}

    @app.delete("/api/server/dirs")
    def server_dirs_remove(req: ServerDirRequest) -> dict:
        if req.kind not in {"movie", "show"}:
            raise HTTPException(status_code=400, detail="kind must be movie or show")
        key = "web.server_movie_dirs" if req.kind == "movie" else "web.server_show_dirs"
        s = get_settings()
        current = [
            str(p)
            for p in (s.web.server_movie_dirs if req.kind == "movie" else s.web.server_show_dirs)
        ]
        current = [p for p in current if p != req.path.strip()]
        from bankai.cli.main import _set_config_value

        _set_config_value(key, current)
        reset_settings_cache()
        media_mod.invalidate_server_cache()
        return {"kind": req.kind, "dirs": current}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    @app.get("/api/settings")
    def settings_get() -> dict:
        s = get_settings()
        data = s.model_dump(mode="json")
        out: list[dict] = []
        for key in sorted(SAFE_SETTING_KEYS):
            parts = key.split(".")
            cur: Any = data
            for part in parts:
                cur = cur.get(part) if isinstance(cur, dict) else None
            secret = _is_secret_key(key)
            out.append(
                {
                    "key": key,
                    "value": cur,
                    "secret": secret,
                    "is_set": bool(cur),
                }
            )
        return {"settings": out}

    @app.post("/api/settings")
    def settings_set(req: SettingRequest) -> dict:
        if req.key not in SAFE_SETTING_KEYS:
            raise HTTPException(status_code=403, detail="key not editable via web")
        from bankai.cli.main import _set_config_value

        try:
            value = _validate_setting_value(req.key, req.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        path, _written = _set_config_value(req.key, value)
        reset_settings_cache()
        return {"key": req.key, "saved": True, "path": str(path)}

    # ------------------------------------------------------------------
    # WebSocket: live queue + job log stream
    # ------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        from bankai.cli import bgjobs

        follow_job: str | None = None
        try:
            while True:
                # Non-blocking receive for control messages (e.g. follow a job).
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.01)
                    if isinstance(msg, dict) and "follow" in msg:
                        follow_job = msg.get("follow") or None
                except (TimeoutError, ValueError):
                    pass
                payload: dict[str, Any] = {"type": "queue", "jobs": webjobs.snapshot()}
                if follow_job:
                    job = bgjobs.get_job(follow_job)
                    if job is not None:
                        payload["log"] = {"id": job.id, "tail": bgjobs.tail(job, lines=200)}
                await ws.send_json(payload)
                await asyncio.sleep(1.5)
        except WebSocketDisconnect:
            return
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("websocket closed: %s", exc)
            return

    # ------------------------------------------------------------------
    # Static frontend (prebuilt React) — mounted last so /api wins.
    # ------------------------------------------------------------------
    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").exists():
        assets = STATIC_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def index() -> Any:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> Any:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")
    else:

        @app.get("/")
        def placeholder() -> Any:
            return HTMLResponse(
                f"<h1>bankai web</h1><p>Frontend assets not built yet. Run the Vite build and place output in <code>{STATIC_DIR}</code>.</p>"
            )

    return app


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse a single ``bytes=start-end`` range header."""
    try:
        units, _, rng = range_header.partition("=")
        if units.strip() != "bytes":
            return 0, file_size - 1
        start_s, _, end_s = rng.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        start = max(0, start)
        end = min(end, file_size - 1)
        if start > end:
            start, end = 0, file_size - 1
        return start, end
    except (ValueError, AttributeError):
        return 0, file_size - 1
