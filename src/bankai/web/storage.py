"""Where the disk space goes.

Every area bankai knows of -- the three libraries, staging, qBittorrent's
download folder, Windows' recycle bin on a library drive and bankai's own
data -- is walked and broken down by kind of file. What a volume holds beyond
them is shown as "Other", so the parts add up to the drive.

A file can sit in two areas at once: a published anime episode is a hard
link to the torrent's file, the same bytes under two names. Each file is
counted once, in the first area it is met in (the libraries come first), and
the later area reports those bytes as shared instead.

The walk takes seconds over the Windows drive mount, so it runs in the
background and its last result is kept on disk; the page reads that.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from bankai.config import get_settings

log = logging.getLogger(__name__)

# A scan older than this is redone when the page asks.
STALE_SECONDS = 30 * 60

_VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".webm", ".mov", ".wmv", ".m2ts"}
_AUDIO = {".mka", ".aac", ".ac3", ".eac3", ".dts", ".flac", ".mp3", ".opus", ".wav"}
_SUBTITLES = {".ass", ".ssa", ".srt", ".sub", ".idx", ".sup", ".vtt"}
_ARTWORK = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tbn"}
_METADATA = {".nfo", ".xml", ".json", ".txt"}
_PARTIAL = {".part", ".!qb", ".parts"}

_LOCK = threading.Lock()
_SCANNING = False


def _kind(path: str) -> str:
    """What a file is, for the breakdown of an area."""
    suffix = Path(path).suffix.casefold()
    if suffix in _VIDEO:
        return "Video"
    if suffix in _AUDIO:
        return "Audio"
    if suffix in _SUBTITLES:
        return "Subtitles"
    if suffix in _ARTWORK:
        return "Posters & artwork"
    if suffix in _METADATA:
        return "Metadata (NFO)"
    if suffix in _PARTIAL:
        return "Unfinished copies"
    return "Other files"


def _cache_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "storage_usage.json"


def _volume_root(path: Path) -> Path:
    """The mount a path is on: the highest parent on the same device."""
    path = path.resolve()
    device = path.stat().st_dev
    while path.parent != path and path.parent.stat().st_dev == device:
        path = path.parent
    return path


def _walk(root: Path, seen: set[tuple[int, int]], parts_of=None) -> dict[str, Any]:
    """Sizes under ``root``: by part, files met before counted as shared."""
    parts: dict[str, list[int]] = {}
    total = files = shared = 0
    stack = [str(root)]
    while stack:
        top = stack.pop()
        try:
            entries = list(os.scandir(top))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            files += 1
            inode = (st.st_dev, st.st_ino)
            if st.st_nlink > 1 and inode in seen:
                shared += st.st_size
                continue
            if st.st_nlink > 1:
                seen.add(inode)
            total += st.st_size
            part = parts_of(entry.path) if parts_of else _kind(entry.path)
            bucket = parts.setdefault(part, [0, 0])
            bucket[0] += st.st_size
            bucket[1] += 1
    return {
        "bytes": total,
        "files": files,
        "shared_bytes": shared,
        "parts": sorted(
            ({"label": label, "bytes": size, "files": count} for label, (size, count) in parts.items()),
            key=lambda part: -part["bytes"],
        ),
    }


def _state_part(root: Path):
    """bankai's data by what it is for: the top-level folder or file, backups together."""

    names = {
        "clips": "Review clips",
        "waveforms": "Waveforms",
        "jobs": "Job logs",
        "jobs_archive": "Job logs (archived)",
        "anime_metadata": "AniList & anime metadata",
        "anime_layout_moves": "Layout move journal",
        "anilist_migration_backup": "Migration backups",
        "torrent_actions": "Torrent actions",
        "shoko_groups": "Shoko groups (old)",
    }

    def part(path: str) -> str:
        relative = Path(path).relative_to(root)
        top = relative.parts[0]
        if ".bak" in top:
            return "Backups of state files"
        if len(relative.parts) > 1:
            return names.get(top, top)
        return "State files"

    return part


def _top_folder(root: Path):
    """An area by its top-level folders: the install's venv, its code, its frontend."""

    def part(path: str) -> str:
        relative = Path(path).relative_to(root)
        return relative.parts[0] if len(relative.parts) > 1 else "Files at the top"

    return part


def _torrents() -> dict[str, Any] | None:
    """qBittorrent's torrents of bankai's category, by how far they got."""
    from bankai.torrent.qbittorrent import QBittorrentClient
    from bankai.web import erai

    async def fetch():
        client = QBittorrentClient()
        try:
            await client.login()
            return await client.list_torrents(category=get_settings().qbittorrent.category)
        finally:
            await client.aclose()

    try:
        torrents = asyncio.run(fetch())
    except Exception as exc:
        log.warning("disk usage: qBittorrent not readable: %s", exc)
        return None
    try:
        releases = {key.casefold(): value for key, value in erai._load_state()["releases"].items()}
    except Exception:
        releases = {}
    groups: dict[str, list[int]] = {}
    root = Path(get_settings().paths.downloads_dir).resolve()

    def add(label: str, size: int, downloaded: int) -> None:
        bucket = groups.setdefault(label, [0, 0, 0])
        bucket[0] += size
        bucket[1] += 1
        bucket[2] += downloaded

    for torrent in torrents:
        have = int(torrent.size_bytes * torrent.progress)
        # What its files take on the disk: an unfinished download's files are
        # full size from the start over the Windows drive mount, whatever has arrived.
        taken = _on_disk(torrent.content_path, root, fallback=have)
        if torrent.progress < 1.0:
            add("Still downloading", taken, have)
            continue
        status = str((releases.get(torrent.hash.casefold()) or {}).get("status") or "")
        if status == "failed":
            label = "Finished, publishing failed"
        elif status == "held":
            label = "Finished, held back"
        elif status in {"queued", "downloading", "complete", "transferring"}:
            label = "Finished, waiting to be published"
        elif status:
            label = "Finished and published, still seeding"
        else:
            label = "Finished, not tracked by bankai"
        add(label, taken, have)
    return {
        "count": len(torrents),
        "groups": sorted(
            (
                {"label": label, "bytes": size, "torrents": count, "downloaded": downloaded}
                for label, (size, count, downloaded) in groups.items()
            ),
            key=lambda group: -group["bytes"],
        ),
    }


def _on_disk(content_path: str, root: Path, *, fallback: int) -> int:
    """The bytes a torrent's files take, by its content path; ``fallback`` when it has none of its own."""
    try:
        path = Path(content_path).resolve() if content_path else None
        # A torrent still fetching its metadata names the download folder itself.
        if path is None or path == root or not path.exists():
            return fallback
        if path.is_file():
            return path.stat().st_size
        return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())
    except OSError:
        return fallback


def _install_root() -> Path:
    """The checkout bankai runs from (``src/bankai`` two levels down), its venv inside."""
    import bankai

    package = Path(bankai.__file__).resolve().parent
    return package.parents[1] if package.parent.name == "src" else package


def scan() -> dict[str, Any]:
    """Walk every area and add them up per volume. Takes a while: run it in the background."""
    from bankai.cli import bgjobs

    settings = get_settings()
    started = time.time()
    state_root = bgjobs.jobs_root().parent
    # Libraries first: a file linked into one of them is counted there, and shared elsewhere.
    candidates: list[tuple[str, str, Path, Any]] = [
        ("anime", "Anime library", settings.transfer.anime_shows_dir, None),
        ("movies", "Movies", settings.transfer.movies_dir, None),
        ("shows", "Shows", settings.transfer.shows_dir, None),
        ("staging", "Staging (waiting for review or transfer)", settings.output.directory, None),
        ("downloads", "qBittorrent downloads", settings.paths.downloads_dir, None),
        ("state", "bankai data", state_root, _state_part(state_root)),
        ("work", "bankai work folder", settings.paths.work_dir, None),
        ("install", "bankai install (code and Python)", _install_root(), _top_folder(_install_root())),
    ]
    areas: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    walked: list[Path] = []
    volumes: dict[str, dict[str, Any]] = {}
    for key, label, path, parts_of in candidates:
        path = Path(path)
        try:
            if not path.is_dir():
                continue
            resolved = path.resolve()
            # One folder inside another (staging inside the work folder) is counted once.
            if any(resolved == done or resolved.is_relative_to(done) for done in walked):
                continue
            volume = _volume_root(resolved)
        except OSError:
            continue
        walked.append(resolved)
        area = {"key": key, "label": label, "path": str(resolved), "volume": str(volume)}
        area.update(_walk(resolved, seen, parts_of))
        areas.append(area)
        volumes.setdefault(str(volume), {"path": str(volume)})
    # The recycle bin of each Windows drive a library is on: deleted files still taking room.
    for volume in list(volumes):
        bin_path = Path(volume) / "$RECYCLE.BIN"
        if bin_path.is_dir():
            area = {"key": "recycle:" + volume, "label": "Windows recycle bin", "path": str(bin_path), "volume": volume}
            area.update(_walk(bin_path, seen))
            area["parts"] = []
            areas.append(area)
    for volume, info in volumes.items():
        usage = shutil.disk_usage(volume)
        counted = sum(area["bytes"] for area in areas if area["volume"] == volume)
        info.update(
            total=usage.total,
            used=usage.used,
            free=usage.free,
            other=max(0, usage.used - counted),
        )
    downloads = next((area for area in areas if area["key"] == "downloads"), None)
    if downloads is not None:
        torrents = _torrents()
        if torrents is not None:
            # What the folder holds beyond bankai's torrents: other categories, or files of removed ones.
            claimed = sum(group["bytes"] for group in torrents["groups"])
            rest = downloads["bytes"] + downloads["shared_bytes"] - claimed
            if rest > 0:
                torrents["groups"].append({"label": "Files no bankai torrent claims", "bytes": rest, "torrents": 0})
        downloads["torrents"] = torrents
    return {
        "scanned_at": time.time(),
        "duration_seconds": round(time.time() - started, 1),
        "volumes": sorted(volumes.values(), key=lambda info: -info["total"]),
        "areas": areas,
    }


def _run() -> None:
    global _SCANNING
    try:
        result = scan()
        path = _cache_path()
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(result), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        log.exception("disk usage scan failed")
    finally:
        with _LOCK:
            _SCANNING = False


def refresh() -> bool:
    """Start a scan unless one is running. Returns whether one was started."""
    global _SCANNING
    with _LOCK:
        if _SCANNING:
            return False
        _SCANNING = True
    threading.Thread(target=_run, name="bankai-disk-usage", daemon=True).start()
    return True


def snapshot() -> dict[str, Any]:
    """The last scan, and whether a new one is on its way; a stale or missing one is started."""
    try:
        result = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {"scanned_at": None, "volumes": [], "areas": []}
    if not result.get("scanned_at") or time.time() - float(result["scanned_at"]) > STALE_SECONDS:
        refresh()
    with _LOCK:
        result["scanning"] = _SCANNING
    return result
