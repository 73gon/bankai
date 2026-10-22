"""Episode thumbnails for Jellyfin, from the middle of each episode.

Jellyfin's Screen Grabber takes its frame 10% into an episode -- in a
24-minute one that is 2:24, the cold open or the opening song. A picture
beside the episode named ``<episode>-thumb.jpg`` is a local image, which
Jellyfin takes over anything it grabs itself. So each episode gets one, from
halfway through: well past the opening, well before the ending.

A one-off crawl of the whole library, then one per new or replaced episode,
a slice a minute so it never competes for long with publishing. The layout
moves the picture with its episode (a companion, see anime_layout), and an
upgrade -- a newer file -- gets a new one. Left out while the disk is short
on room, like every other picture bankai writes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from bankai.logging import get_logger
from bankai.web import anime_layout, jellyfin, library_walk

log = get_logger(__name__)

# How far into the episode the frame is taken.
_POINT = 0.5
_WIDTH = 960
_SUFFIX = "-thumb.jpg"

# Episodes known to have a current picture, by path -> the file's mtime then:
# the crawl stats each picture once per process, not on every pass over 9p.
_DONE: dict[str, float] = {}
# Files ffmpeg could not take a frame from, by path -> (size, mtime): not
# tried again until the file changes, or they would use up every pass.
_FAILED: dict[str, tuple[int, float]] = {}


def thumb_path(video: Path) -> Path:
    return video.with_name(video.stem + _SUFFIX)


def _duration(video: Path) -> float | None:
    from bankai.web.media import ffprobe_bin

    binary = ffprobe_bin()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def make_thumb(video: Path) -> bool:
    """Write the episode's picture from halfway through. False when no frame could be taken."""
    from bankai.web.media import ffmpeg_bin

    binary = ffmpeg_bin()
    duration = _duration(video)
    if binary is None or not duration:
        return False
    target = thumb_path(video)
    tmp = target.with_name(f"{target.stem}.{os.getpid()}.tmp.jpg")
    try:
        result = subprocess.run(
            [
                binary, "-v", "error", "-nostdin",
                # Seeking before the input jumps to the nearest keyframe: a read
                # of a few megabytes, not the whole first half of the file.
                "-ss", f"{duration * _POINT:.2f}", "-i", str(video),
                "-frames:v", "1", "-vf", f"scale={_WIDTH}:-2", "-q:v", "3",
                "-y", str(tmp),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            return False
        tmp.replace(target)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        tmp.unlink(missing_ok=True)


def sweep(root: Path, *, limit: int) -> dict[str, int]:
    """Write up to ``limit`` missing or outdated episode pictures under ``root``."""
    if limit <= 0 or not root.exists():
        return {"made": 0, "remaining": 0}
    if not anime_layout.pictures_allowed():
        return {"made": 0, "remaining": 0, "held_for_space": 1}
    made = remaining = failed = 0
    written: list[Path] = []
    for row in library_walk.files([root]):
        key, mtime, size = row["path"], float(row["mtime"]), int(row["size"])
        if _DONE.get(key) == mtime or _FAILED.get(key) == (size, mtime):
            continue
        video = Path(key)
        try:
            # A picture at least as new as its episode is current; an upgrade
            # rewrites the episode, and so gets a new one.
            if thumb_path(video).stat().st_mtime >= mtime:
                _DONE[key] = mtime
                continue
        except OSError:
            pass
        if made + failed >= limit:
            remaining += 1
            continue
        if make_thumb(video):
            _DONE[key] = mtime
            made += 1
            written.append(video)
        else:
            _FAILED[key] = (size, mtime)
            failed += 1
            log.warning("No thumbnail could be taken from %s", video.name)
    # Jellyfin never looks at a new picture of an episode it already has.
    jellyfin.queue(written)
    return {"made": made, "remaining": remaining, "failed": failed}


__all__ = ["make_thumb", "sweep", "thumb_path"]
