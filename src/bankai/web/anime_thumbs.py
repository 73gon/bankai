"""Episode thumbnails for Jellyfin, from well inside each episode.

Jellyfin's Screen Grabber takes its frame 10% into an episode -- in a
24-minute one that is 2:24, the cold open or the opening song. A picture
beside the episode named ``<episode>-thumb.jpg`` is a local image, which
Jellyfin takes over anything it grabs itself. So each episode gets one, from
where the anime settings say: a share of its length (halfway, by default:
well past the opening, well before the ending) or a fixed time into it.

Each picture is remembered with the setting it was taken at; change the
setting, or ask for every picture again, and they are all taken anew, a
slice a pass like the first crawl.

A one-off crawl of the whole library, then one per new or replaced episode,
a slice a minute so it never competes for long with publishing. The layout
moves the picture with its episode (a companion, see anime_layout), and an
upgrade -- a newer file -- gets a new one. Left out while the disk is short
on room, like every other picture bankai writes.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web import anime_layout, jellyfin, library_walk

log = get_logger(__name__)

_SUFFIX = "-thumb.jpg"
# What every picture taken before the setting existed was taken at: halfway, 960 wide.
_LEGACY_SPEC = "percent:50:960"
_GENERATION = "_generation"

# Episodes known to have a current picture, by path -> (the file's mtime, the
# setting) then: the crawl stats each picture once per process, not on every
# pass over 9p.
_DONE: dict[str, tuple[float, str]] = {}
# Files ffmpeg could not take a frame from, by path -> (size, mtime): not
# tried again until the file changes, or they would use up every pass.
_FAILED: dict[str, tuple[int, float]] = {}


def thumb_path(video: Path) -> Path:
    return video.with_name(video.stem + _SUFFIX)


def _specs_path() -> Path:
    return bgjobs.jobs_root().parent / "anime_thumb_specs.json"


def _load_specs() -> dict:
    """Each picture's setting by its episode's path, and how many times every picture was asked for again."""
    try:
        raw = json.loads(_specs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_specs(rows: dict) -> None:
    path = _specs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    tmp.replace(path)


def current_spec(specs: dict | None = None) -> str:
    """The setting as one string; a picture taken at another is taken again."""
    anime = get_settings().anime
    if anime.thumb_mode == "time":
        spec = f"time:{anime.thumb_time_seconds}:{anime.thumb_width}"
    else:
        spec = f"percent:{anime.thumb_percent:g}:{anime.thumb_width}"
    generation = int((specs if specs is not None else _load_specs()).get(_GENERATION) or 0)
    return f"{spec}#{generation}" if generation else spec


def regenerate() -> None:
    """Take every episode's picture again, from the next passes on."""
    specs = _load_specs()
    specs[_GENERATION] = int(specs.get(_GENERATION) or 0) + 1
    _save_specs(specs)
    _DONE.clear()
    _FAILED.clear()


def position(duration: float) -> float:
    """Where in an episode this long the frame is taken, in seconds."""
    anime = get_settings().anime
    share = duration * anime.thumb_percent / 100
    if anime.thumb_mode == "time":
        # An episode shorter than the time asked for (a short, a recap) is taken at the share.
        return float(anime.thumb_time_seconds) if anime.thumb_time_seconds < duration * 0.95 else share
    return share


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


def grab_frame(video: Path, target: Path, *, at: float, width: int) -> bool:
    """Write one frame of the video, ``at`` seconds in and ``width`` wide, to ``target`` (a JPEG)."""
    from bankai.web.media import ffmpeg_bin

    binary = ffmpeg_bin()
    if binary is None:
        return False
    tmp = target.with_name(f"{target.stem}.{os.getpid()}.tmp.jpg")
    try:
        result = subprocess.run(
            [
                binary, "-v", "error", "-nostdin",
                # Seeking before the input jumps to the nearest keyframe: a read
                # of a few megabytes, not the whole first half of the file.
                "-ss", f"{at:.2f}", "-i", str(video),
                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3",
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


def make_thumb(video: Path) -> bool:
    """Write the episode's picture from where the setting says. False when no frame could be taken."""
    duration = _duration(video)
    if not duration:
        return False
    return grab_frame(video, thumb_path(video), at=position(duration), width=get_settings().anime.thumb_width)


def preview(
    video: Path, target: Path, *, mode: str, percent: float, time_seconds: int, width: int
) -> float | None:
    """A frame as a setting not saved yet would take it, for the settings page. Seconds in, or None."""
    duration = _duration(video)
    if not duration:
        return None
    share = duration * percent / 100
    at = float(time_seconds) if mode == "time" and time_seconds < duration * 0.95 else share
    return at if grab_frame(video, target, at=at, width=width) else None


def sweep(root: Path, *, limit: int) -> dict[str, int]:
    """Write up to ``limit`` missing or outdated episode pictures under ``root``."""
    if limit <= 0 or not root.exists():
        return {"made": 0, "remaining": 0}
    if not get_settings().anime.thumbs_enabled:
        return {"made": 0, "remaining": 0, "disabled": 1}
    if not anime_layout.pictures_allowed():
        return {"made": 0, "remaining": 0, "held_for_space": 1}
    specs = _load_specs()
    spec = current_spec(specs)
    made = remaining = failed = 0
    written: list[Path] = []
    for row in library_walk.files([root]):
        key, mtime, size = row["path"], float(row["mtime"]), int(row["size"])
        if _DONE.get(key) == (mtime, spec) or _FAILED.get(key) == (size, mtime):
            continue
        video = Path(key)
        try:
            # A picture at least as new as its episode, taken at this setting,
            # is current; an upgrade rewrites the episode, and so gets a new one.
            if thumb_path(video).stat().st_mtime >= mtime and specs.get(key, _LEGACY_SPEC) == spec:
                _DONE[key] = (mtime, spec)
                continue
        except OSError:
            pass
        if made + failed >= limit:
            remaining += 1
            continue
        if make_thumb(video):
            _DONE[key] = (mtime, spec)
            made += 1
            written.append(video)
        else:
            _FAILED[key] = (size, mtime)
            failed += 1
            log.warning("No thumbnail could be taken from %s", video.name)
    if written:
        # Read again first: a regenerate asked for meanwhile is kept.
        fresh = _load_specs()
        if current_spec(fresh) == spec:
            fresh.update({str(video): spec for video in written})
            _save_specs(fresh)
    # Jellyfin never looks at a new picture of an episode it already has.
    jellyfin.queue(written)
    return {"made": made, "remaining": remaining, "failed": failed}


__all__ = ["current_spec", "grab_frame", "make_thumb", "position", "preview", "regenerate", "sweep", "thumb_path"]
