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
    confidence: float = 0.0
    spread_seconds: float = 0.0
    drift_ratio: float = 1.0


_VIDEO_SUFFIXES = {".mkv", ".mp4", ".webm", ".mov", ".avi", ".m4v"}


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_SUFFIXES


async def estimate_visual_timeline(
    *,
    reference: Path,
    source: Path,
    sample_count: int = 9,
    search_radius_seconds: float = 180.0,
    coarse_step_seconds: float = 15.0,
    fine_step_seconds: float = 0.25,
    max_distance: float = 0.22,
    min_matches: int = 3,
    min_detail: float = 12.0,
) -> VisualTimeline:
    """Estimate source/reference offset using downscaled frame hashes.

    Robustness-first strategy:

    * Sample many candidate timestamps in the HQ reference and keep the
      most *discriminative* frames (high pixel variance) so we don't try to
      match near-black or flat frames that alias everywhere.
    * For each kept frame, locate it in the source with a coarse scan and
      then refine down to a fine step for sub-second precision.
    * Combine the per-frame offsets with a robust **median** (constant
      offset) plus a Theil–Sen **slope** (speed drift), and derive a
      confidence from match count, agreement (spread) and hash quality.
    """
    if not is_video_file(source):
        raise VisualSyncError(f"source is not a video container: {source}")
    ref_dur, source_dur = await asyncio.gather(
        _ffprobe_duration(reference),
        _ffprobe_duration(source),
    )
    ref_frames = await _select_reference_frames(reference, ref_dur, sample_count, min_detail=min_detail)
    if len(ref_frames) < min_matches:
        raise VisualSyncError(f"only {len(ref_frames)} discriminative reference frames found (need {min_matches})")

    matches: list[VisualMatch] = []
    for ref_time, ref_hash in ref_frames:
        match = await _find_best_match(
            source=source,
            source_duration=source_dur,
            reference_time=ref_time,
            reference_hash=ref_hash,
            search_radius_seconds=search_radius_seconds,
            coarse_step_seconds=coarse_step_seconds,
            fine_step_seconds=fine_step_seconds,
        )
        if match is not None and match.distance <= max_distance:
            matches.append(match)

    if len(matches) < min_matches:
        raise VisualSyncError(f"not enough visual matches ({len(matches)}/{min_matches})")

    offsets = [m.source_time - m.reference_time for m in matches]
    median_offset = _median(offsets)
    spread = _median([abs(o - median_offset) for o in offsets])  # MAD
    slope = _theil_sen_slope([m.reference_time for m in matches], [m.source_time for m in matches])
    confidence = _confidence(matches, spread=spread, sample_count=len(ref_frames))
    return VisualTimeline(
        slope=slope,
        offset_seconds=median_offset,
        matches=tuple(matches),
        confidence=confidence,
        spread_seconds=spread,
        drift_ratio=slope,
    )


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


async def _select_reference_frames(reference: Path, duration: float, count: int, *, min_detail: float) -> list[tuple[float, int]]:
    """Pick ``count`` discriminative reference frames (high pixel variance).

    We over-sample candidate timestamps, hash each and measure its detail
    (thumbnail variance), then keep the most detailed frames so matching
    latches onto distinctive scenes rather than flat/near-black frames.
    """
    candidate_count = max(count * 2, count + 3)
    candidates = _sample_times(duration, candidate_count)
    scored: list[tuple[float, float, int]] = []  # (detail, time, hash)
    for t in candidates:
        try:
            frame_hash, detail = await _frame_features(reference, t)
        except VisualSyncError:
            continue
        if detail < min_detail:
            continue
        scored.append((detail, t, frame_hash))
    scored.sort(key=lambda s: s[0], reverse=True)
    kept = scored[:count]
    kept.sort(key=lambda s: s[1])  # back into chronological order
    return [(t, h) for _detail, t, h in kept]


async def _find_best_match(
    *,
    source: Path,
    source_duration: float,
    reference_time: float,
    reference_hash: int,
    search_radius_seconds: float,
    coarse_step_seconds: float,
    fine_step_seconds: float,
) -> VisualMatch | None:
    """Locate ``reference_hash`` in the source: coarse scan then refine."""
    start = max(0.0, reference_time - search_radius_seconds)
    end = min(source_duration, reference_time + search_radius_seconds)
    if end <= start:
        raise VisualSyncError("empty visual search window")

    best = await _scan_window(source, reference_time, reference_hash, start, end, coarse_step_seconds)
    if best is None:
        return None
    # Refine around the coarse best with progressively finer steps.
    step = coarse_step_seconds
    while step > fine_step_seconds:
        step = max(step / 6.0, fine_step_seconds)
        lo = max(0.0, best.source_time - step * 6.0)
        hi = min(source_duration, best.source_time + step * 6.0)
        refined = await _scan_window(source, reference_time, reference_hash, lo, hi, step)
        if refined is not None and refined.distance <= best.distance:
            best = refined
    return best


async def _scan_window(
    source: Path,
    reference_time: float,
    reference_hash: int,
    start: float,
    end: float,
    step: float,
) -> VisualMatch | None:
    best: VisualMatch | None = None
    for source_time in _frange(start, end, step):
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


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _theil_sen_slope(xs: list[float], ys: list[float]) -> float:
    """Robust slope estimate: median of pairwise slopes."""
    slopes: list[float] = []
    n = len(xs)
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[j] - xs[i]
            if abs(dx) < 1e-6:
                continue
            slopes.append((ys[j] - ys[i]) / dx)
    if not slopes:
        return 1.0
    return _median(slopes)


def _confidence(matches: list[VisualMatch], *, spread: float, sample_count: int) -> float:
    """Combine match yield, agreement and hash quality into [0, 1]."""
    if not matches or sample_count <= 0:
        return 0.0
    yield_fraction = min(1.0, len(matches) / sample_count)
    # Tight agreement (small MAD) -> high score; 0.15s or less is excellent,
    # 1.5s+ is poor.
    tightness = max(0.0, min(1.0, 1.0 - (spread - 0.15) / 1.35)) if spread > 0.15 else 1.0
    mean_distance = sum(m.distance for m in matches) / len(matches)
    quality = max(0.0, 1.0 - mean_distance / 0.22)
    return round(yield_fraction * tightness * quality, 4)


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
    frame_hash, _detail = await _frame_features(path, timestamp)
    return frame_hash


async def _frame_features(path: Path, timestamp: float) -> tuple[int, float]:
    """Return ``(average_hash, detail)`` for one frame.

    ``detail`` is the pixel variance of the downscaled thumbnail — a cheap
    proxy for how distinctive the frame is. Flat / near-black frames score
    near zero and are poor matching anchors.
    """
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
    return _average_hash(stdout), _variance(stdout)


def _variance(data: bytes) -> float:
    n = len(data)
    if n == 0:
        return 0.0
    mean = sum(data) / n
    return sum((b - mean) ** 2 for b in data) / n


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
