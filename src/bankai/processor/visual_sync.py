"""Visual timeline matching between a source video and the HQ reference.

This is a lightweight fallback for cases where the German source is a full
video container rather than audio-only. It samples a few frames from the HQ
reference, searches near the same timestamps in the source, and estimates a
linear timeline mapping:

    source_time = slope * reference_time + offset

Only simple offset mappings are applied automatically by the pipeline. Speed
or cut-heavy mappings are reported for logs and future refinement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


class VisualSyncError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class VisualMatch:
    reference_time: float
    source_time: float
    distance: float


@dataclass(frozen=True, slots=True)
class VisualTimeline:
    slope: float
    offset_seconds: float
    matches: tuple[VisualMatch, ...]


_VIDEO_SUFFIXES = {".mkv", ".mp4", ".webm", ".mov", ".avi", ".m4v"}


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_SUFFIXES


async def estimate_visual_timeline(
    *,
    reference: Path,
    source: Path,
    sample_count: int = 3,
    search_radius_seconds: float = 180.0,
    search_step_seconds: float = 20.0,
    max_distance: float = 0.22,
    min_matches: int = 2,
) -> VisualTimeline:
    """Estimate source/reference offset using downscaled frame hashes."""
    if not is_video_file(source):
        raise VisualSyncError(f"source is not a video container: {source}")
    ref_dur, source_dur = await asyncio.gather(
        _ffprobe_duration(reference),
        _ffprobe_duration(source),
    )
    times = _sample_times(ref_dur, sample_count)
    matches: list[VisualMatch] = []
    for ref_time in times:
        ref_hash = await _frame_hash(reference, ref_time)
        match = await _find_best_match(
            source=source,
            source_duration=source_dur,
            reference_time=ref_time,
            reference_hash=ref_hash,
            search_radius_seconds=search_radius_seconds,
            search_step_seconds=search_step_seconds,
        )
        if match.distance <= max_distance:
            matches.append(match)

    if len(matches) < min_matches:
        raise VisualSyncError(f"not enough visual matches ({len(matches)}/{min_matches})")
    slope, offset = _fit_timeline(matches)
    return VisualTimeline(slope=slope, offset_seconds=offset, matches=tuple(matches))


def _sample_times(duration: float, count: int) -> list[float]:
    if duration <= 0:
        raise VisualSyncError("reference duration is not positive")
    count = max(1, count)
    start = min(max(duration * 0.15, 60.0), duration * 0.4)
    end = max(min(duration * 0.85, duration - 60.0), duration * 0.6)
    if count == 1 or end <= start:
        return [duration * 0.5]
    step = (end - start) / (count - 1)
    return [start + i * step for i in range(count)]


async def _find_best_match(
    *,
    source: Path,
    source_duration: float,
    reference_time: float,
    reference_hash: int,
    search_radius_seconds: float,
    search_step_seconds: float,
) -> VisualMatch:
    start = max(0.0, reference_time - search_radius_seconds)
    end = min(source_duration, reference_time + search_radius_seconds)
    if end <= start:
        raise VisualSyncError("empty visual search window")

    best: VisualMatch | None = None
    for source_time in _frange(start, end, search_step_seconds):
        try:
            source_hash = await _frame_hash(source, source_time)
        except VisualSyncError:
            continue
        distance = _hash_distance(reference_hash, source_hash)
        if best is None or distance < best.distance:
            best = VisualMatch(
                reference_time=reference_time,
                source_time=source_time,
                distance=distance,
            )
    if best is None:
        raise VisualSyncError("no source frames could be hashed")
    return best


def _fit_timeline(matches: list[VisualMatch]) -> tuple[float, float]:
    if len(matches) == 1:
        match = matches[0]
        return 1.0, match.source_time - match.reference_time
    xs = [m.reference_time for m in matches]
    ys = [m.source_time for m in matches]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 1.0, mean_y - mean_x
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denom
    offset = mean_y - slope * mean_x
    return slope, offset


async def _ffprobe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise VisualSyncError(f"ffprobe failed: {stderr.decode(errors='ignore')[:200]}")
    try:
        return float(stdout.decode().strip())
    except ValueError as exc:
        raise VisualSyncError(f"ffprobe returned non-numeric duration: {stdout!r}") from exc


async def _frame_hash(path: Path, timestamp: float) -> int:
    width = 32
    height = 18
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:{height},format=gray",
        "-f",
        "rawvideo",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or len(stdout) != width * height:
        detail = stderr.decode(errors="ignore")[:200]
        raise VisualSyncError(f"could not hash frame at {timestamp:.3f}s: {detail}")
    return _average_hash(stdout)


def _average_hash(data: bytes) -> int:
    mean = sum(data) / len(data)
    value = 0
    for byte in data:
        value = (value << 1) | int(byte >= mean)
    return value


def _hash_distance(left: int, right: int, *, bits: int = 32 * 18) -> float:
    return (left ^ right).bit_count() / bits


def _frange(start: float, end: float, step: float) -> list[float]:
    step = max(step, 0.001)
    values: list[float] = []
    current = start
    while current <= end:
        values.append(current)
        current += step
    return values
