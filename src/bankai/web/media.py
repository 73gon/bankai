"""Media inspection and manipulation helpers for the web UI.

These wrap ``ffprobe``, ``ffmpeg`` and ``mkvmerge`` to support the
Library review workflow: inspecting audio tracks, streaming/transcoding
for in-browser preview, and re-applying an audio delay (repack) so the
user can fix German dub sync before approving a transfer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

_VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts"}
_GERMAN_TAGS = {"ger", "deu", "de", "german", "deutsch"}


@dataclass(frozen=True, slots=True)
class AudioTrack:
    index: int  # ffprobe stream index
    order: int  # 0-based order among audio streams (mkvmerge track-ish)
    language: str | None
    title: str | None
    codec: str | None
    channels: int | None
    default: bool
    is_german: bool


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: str
    size: int
    duration: float | None
    video_codec: str | None
    width: int | None
    height: int | None
    audio_tracks: list[AudioTrack]
    has_german: bool
    browser_playable: bool


def _which(name: str) -> str | None:
    return shutil.which(name)


def ffprobe_bin() -> str | None:
    return _which("ffprobe")


def ffmpeg_bin() -> str | None:
    return _which("ffmpeg")


def mkvmerge_bin() -> str | None:
    return _which("mkvmerge")


_PROBE_CACHE: dict[str, tuple[float, float, MediaInfo]] = {}


def probe(path: Path, *, use_cache: bool = True) -> MediaInfo | None:
    """Return :class:`MediaInfo` for ``path`` (cached by mtime+size)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = str(p.resolve())
    if use_cache:
        cached = _PROBE_CACHE.get(key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
    info = _probe_uncached(p, st.st_size)
    if info is not None:
        _PROBE_CACHE[key] = (st.st_mtime, st.st_size, info)
    return info


def _probe_uncached(path: Path, size: int) -> MediaInfo | None:
    bin_ = ffprobe_bin()
    if bin_ is None:
        # ffprobe missing: return a minimal record so the UI still lists files.
        return MediaInfo(
            path=str(path),
            size=size,
            duration=None,
            video_codec=None,
            width=None,
            height=None,
            audio_tracks=[],
            has_german=False,
            browser_playable=path.suffix.lower() in {".mp4", ".m4v", ".webm"},
        )
    cmd = [
        bin_,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("ffprobe failed for %s: %s", path, exc)
        return None
    if out.returncode != 0:
        log.warning("ffprobe rc=%s for %s: %s", out.returncode, path, out.stderr.strip())
        return None
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return _parse_probe(path, size, data)


def _parse_probe(path: Path, size: int, data: dict) -> MediaInfo:
    fmt = data.get("format") or {}
    duration = _to_float(fmt.get("duration"))
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    tracks: list[AudioTrack] = []
    audio_order = 0
    for stream in data.get("streams") or []:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_codec is None:
            video_codec = stream.get("codec_name")
            width = _to_int(stream.get("width"))
            height = _to_int(stream.get("height"))
        elif codec_type == "audio":
            tags = stream.get("tags") or {}
            lang = (tags.get("language") or "").strip().lower() or None
            title = tags.get("title")
            disp = stream.get("disposition") or {}
            is_de = _is_german(lang, title)
            tracks.append(
                AudioTrack(
                    index=_to_int(stream.get("index")) or 0,
                    order=audio_order,
                    language=lang,
                    title=title,
                    codec=stream.get("codec_name"),
                    channels=_to_int(stream.get("channels")),
                    default=bool(disp.get("default")),
                    is_german=is_de,
                )
            )
            audio_order += 1
    has_german = any(t.is_german for t in tracks)
    return MediaInfo(
        path=str(path),
        size=size,
        duration=duration,
        video_codec=video_codec,
        width=width,
        height=height,
        audio_tracks=tracks,
        has_german=has_german,
        browser_playable=_browser_playable(path, video_codec),
    )


def _browser_playable(path: Path, video_codec: str | None) -> bool:
    """Heuristic: can a typical browser play this directly via <video>?

    Note: Matroska (.mkv) is **not** a browser-playable container even when
    it holds h264/aac — browsers only natively demux mp4/webm. So an MKV
    always goes through the remux/transcode endpoint.
    """
    if path.suffix.lower() not in {".mp4", ".m4v", ".webm"}:
        return False
    if video_codec is None:
        return False
    # h264/vp9/av1 are broadly supported; HEVC/h265 is not in most browsers.
    return video_codec.lower() in {"h264", "avc1", "vp8", "vp9", "av1"}


def _is_german(lang: str | None, title: str | None) -> bool:
    if lang and lang in _GERMAN_TAGS:
        return True
    if title and any(tag in title.lower() for tag in _GERMAN_TAGS):
        return True
    return False


def _to_float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Library scanning
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    kind: str  # "movie" | "episode"
    path: str
    rel_path: str
    name: str
    size: int
    mtime: float
    series: str | None = None
    season: int | None = None


def scan_library(library: Path | None = None) -> list[LibraryEntry]:
    settings = get_settings()
    root = Path(library or settings.output.directory)
    entries: list[LibraryEntry] = []
    movies_dir = root / "Movies"
    shows_dir = root / "Shows"
    if movies_dir.is_dir():
        for f in sorted(movies_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in _VIDEO_EXTS:
                entries.append(_movie_entry(root, f))
    if shows_dir.is_dir():
        for f in sorted(shows_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in _VIDEO_EXTS:
                entries.append(_episode_entry(root, shows_dir, f))
    return entries


def _movie_entry(root: Path, f: Path) -> LibraryEntry:
    st = f.stat()
    return LibraryEntry(
        kind="movie",
        path=str(f),
        rel_path=str(f.relative_to(root)),
        name=f.stem,
        size=st.st_size,
        mtime=st.st_mtime,
    )


def _episode_entry(root: Path, shows_dir: Path, f: Path) -> LibraryEntry:
    st = f.stat()
    rel = f.relative_to(shows_dir)
    series = rel.parts[0] if len(rel.parts) >= 1 else None
    season = None
    for part in rel.parts:
        low = part.lower()
        if low.startswith("season"):
            digits = "".join(ch for ch in part if ch.isdigit())
            season = int(digits) if digits else None
    return LibraryEntry(
        kind="episode",
        path=str(f),
        rel_path=str(f.relative_to(root)),
        name=f.stem,
        size=st.st_size,
        mtime=st.st_mtime,
        series=series,
        season=season,
    )


# --------------------------------------------------------------------------
# Media-server contents scan (the "Server" page)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServerTitle:
    name: str
    kind: str  # "movie" | "show"
    present: bool
    location: str | None = None


_SERVER_CACHE: dict[str, tuple[float, list[ServerTitle]]] = {}


def scan_server(kind: str, *, use_cache: bool = True) -> list[ServerTitle]:
    settings = get_settings()
    dirs = (
        settings.web.server_movie_dirs if kind == "movie" else settings.web.server_show_dirs
    )
    cache_key = kind
    ttl = settings.web.cache_ttl_seconds
    if use_cache:
        cached = _SERVER_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
    seen: dict[str, ServerTitle] = {}
    for d in dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        for child in sorted(p.iterdir()):
            if child.is_dir():
                name = child.name
            elif child.is_file() and child.suffix.lower() in _VIDEO_EXTS:
                # Many movies live as a bare ``Title (Year).mkv`` rather than
                # inside a folder — surface those too.
                name = child.stem
            else:
                continue
            key = name.casefold()
            if key not in seen:
                seen[key] = ServerTitle(
                    name=name,
                    kind="movie" if kind == "movie" else "show",
                    present=True,
                    location=str(child),
                )
    titles = sorted(seen.values(), key=lambda t: t.name.casefold())
    _SERVER_CACHE[cache_key] = (time.time(), titles)
    return titles


def invalidate_server_cache() -> None:
    _SERVER_CACHE.clear()


@dataclass(frozen=True, slots=True)
class ServerEpisode:
    name: str
    path: str
    size: int


@dataclass(frozen=True, slots=True)
class ServerSeason:
    name: str
    season: int | None
    episodes: list[ServerEpisode]


def _server_episode(f: Path) -> ServerEpisode:
    try:
        size = f.stat().st_size
    except OSError:
        size = 0
    return ServerEpisode(name=f.stem, path=str(f), size=size)


def _season_number(name: str) -> int | None:
    digits = "".join(ch for ch in name if ch.isdigit())
    return int(digits) if digits else None


def scan_server_show(show_dir: Path) -> list[ServerSeason]:
    """Drill into a single show directory and return its seasons + the
    episode files present in each. Bare episode files that sit directly in
    the show folder (no Season subfolder) are grouped under "Episodes"."""
    p = Path(show_dir)
    if not p.is_dir():
        return []
    seasons: list[ServerSeason] = []
    loose: list[ServerEpisode] = []
    for child in sorted(p.iterdir(), key=lambda c: c.name.casefold()):
        if child.is_dir():
            eps = [
                _server_episode(f)
                for f in sorted(child.rglob("*"), key=lambda c: c.name.casefold())
                if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
            ]
            seasons.append(
                ServerSeason(name=child.name, season=_season_number(child.name), episodes=eps)
            )
        elif child.is_file() and child.suffix.lower() in _VIDEO_EXTS:
            loose.append(_server_episode(child))
    if loose:
        seasons.append(ServerSeason(name="Episodes", season=None, episodes=loose))
    return seasons


# --------------------------------------------------------------------------
# Repack: re-apply audio delay with mkvmerge (no re-encode)
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RepackResult:
    ok: bool
    message: str
    delay_ms: int = 0
    log: list[str] = field(default_factory=list)


def repack_audio_delay(
    path: Path,
    *,
    delay_ms: int,
    german_only: bool = True,
    track_index: int | None = None,
) -> RepackResult:
    """Re-apply ``delay_ms`` to an audio track and overwrite ``path``.

    Uses ``mkvmerge --sync <track>:<delay>`` which remuxes without
    re-encoding (fast). When ``track_index`` is given, the delay is
    applied to exactly that track (by ffprobe stream index) — used by the
    per-track nudge in the review player to fix e.g. an offset English
    track. Otherwise it falls back to the German track(s).
    """
    p = Path(path)
    if not p.is_file():
        return RepackResult(False, f"file not found: {p}")
    bin_ = mkvmerge_bin()
    if bin_ is None:
        return RepackResult(False, "mkvmerge not found")
    info = probe(p, use_cache=False)
    if info is None:
        return RepackResult(False, "could not probe file")
    if track_index is not None:
        targets = [t for t in info.audio_tracks if t.index == track_index]
        if not targets:
            return RepackResult(False, f"no audio track with index {track_index}")
        set_default = False
    else:
        targets = [t for t in info.audio_tracks if (t.is_german or not german_only)]
        if german_only and not targets:
            return RepackResult(False, "no German audio track found to delay")
        set_default = True

    tmp = p.with_suffix(p.suffix + ".repack.mkv")
    cmd: list[str] = [bin_, "-o", str(tmp)]
    # Apply sync to each target audio track. mkvmerge --sync uses the
    # track id within the source file, which equals the ffprobe stream
    # index for single-file inputs.
    for t in targets:
        cmd.extend(["--sync", f"{t.index}:{delay_ms}"])
    # Keep the German track default when nudging German; leave defaults
    # untouched for an explicit per-track nudge.
    if set_default and targets:
        cmd.extend(["--default-track", f"{targets[0].index}:yes"])
    cmd.append(str(p))

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except (subprocess.SubprocessError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        return RepackResult(False, f"mkvmerge failed: {exc}")
    log_lines = (out.stdout or "").splitlines() + (out.stderr or "").splitlines()
    # mkvmerge returns 1 for warnings (still produces output), 2 for errors.
    if out.returncode >= 2 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return RepackResult(False, f"mkvmerge rc={out.returncode}", log=log_lines[-20:])
    try:
        tmp.replace(p)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return RepackResult(False, f"could not overwrite original: {exc}")
    # Invalidate probe cache for this path.
    _PROBE_CACHE.pop(str(p.resolve()), None)
    where = f"track #{track_index}" if track_index is not None else "German audio"
    return RepackResult(
        True,
        f"applied {delay_ms} ms delay to {where}",
        delay_ms=delay_ms,
        log=log_lines[-20:],
    )
