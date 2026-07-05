"""Audio sync worker (alass / manual offset).

Pipeline stage 6: align an extracted dub audio file against an HQ video
file (the reference), producing a synced audio file. Modes:

* ``auto`` â€” invoke ``alass-cli`` to fingerprint speech/silence and apply
  the detected offset.
* ``manual`` â€” apply a user-supplied offset (in seconds) via ffmpeg.
* ``skip`` â€” copy the input through unchanged.

Job payload schema
------------------

``payload`` for a SYNC job::

    {
        "audio": "/work/job-7/audio.aac",
        "reference": "/downloads/movie.mkv",
        "offset_seconds": -1.24       # optional; forces manual mode
    }
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class SyncResult:
    path: Path
    offset_seconds: float
    method: str  # "alass" | "manual" | "skip" | "passthrough" | "atempo"


# Common framerate-conversion ratios that show up in dub releases.
# Audio extracted from a 25fps PAL stream is ~4.27% faster than the
# matching 23.976fps NTSC/BluRay video; we counter with the inverse.
_KNOWN_TEMPO_RATIOS = {
    "pal_to_ndf": 23.976 / 25.0,  # 0.95904 â€” slow audio down
    "ndf_to_pal": 25.0 / 23.976,  # 1.04270 â€” speed audio up
    "pal_to_film": 24.0 / 25.0,  # 0.96000
    "film_to_pal": 25.0 / 24.0,  # 1.04167
}


async def _ffprobe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise SyncError(f"ffprobe failed: {stderr.decode(errors='ignore')[:200]}")
    try:
        return float(stdout.decode().strip())
    except ValueError as exc:
        raise SyncError(f"ffprobe returned non-numeric duration: {stdout!r}") from exc


def _classify_ratio(audio_dur: float, video_dur: float, *, tol: float = 0.003) -> str | None:
    """Return the name of a known tempo ratio if audio/video matches one."""
    if audio_dur <= 0 or video_dur <= 0:
        return None
    ratio = video_dur / audio_dur
    for name, target in _KNOWN_TEMPO_RATIOS.items():
        if abs(ratio - target) <= tol:
            return name
    return None


class SyncError(Exception):
    pass


class PlaceholderAudioError(PermanentWorkerError):
    """Extracted audio is too short to be the real feature stream."""

    def __init__(self, *, audio_duration: float, video_duration: float) -> None:
        self.audio_duration = audio_duration
        self.video_duration = video_duration
        super().__init__(
            f"audio duration ({audio_duration:.1f}s) is much shorter than "
            f"video ({video_duration:.1f}s); extract likely captured a placeholder. "
            "Re-run with a different URL or hint."
        )


class SyncWorker(Worker):
    kind = JobKind.SYNC

    def __init__(self, *, runner: AlassRunner | None = None) -> None:
        self._runner = runner or AlassRunner()

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        payload = ctx.job.payload
        audio = payload.get("audio")
        reference = payload.get("reference")
        if not audio:
            raise PermanentWorkerError("sync job payload missing 'audio'")
        audio_path = Path(audio)
        if not audio_path.exists():
            raise PermanentWorkerError(f"audio not found: {audio_path}")

        out_dir = ctx.work_dir / f"job-{ctx.job.id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"synced{audio_path.suffix}"

        settings = get_settings().sync
        manual_offset = payload.get("offset_seconds")
        explicit_tempo = payload.get("tempo")
        mode = settings.mode
        if manual_offset is not None:
            mode = "manual"
        log.info("[sync] mode=%s threshold=%.2fs", mode, settings.threshold_seconds)

        try:
            if explicit_tempo is not None and mode not in ("skip",):
                # Visual sync detected a speed drift and asked us to correct
                # it directly; re-encode the audio at the given tempo factor.
                log.info("[sync] applying explicit tempo=%.5f", float(explicit_tempo))
                result = await self._apply_tempo(audio_path, out_path, float(explicit_tempo))
            elif mode == "skip":
                shutil.copyfile(audio_path, out_path)
                result = SyncResult(path=out_path, offset_seconds=0.0, method="skip")
            elif mode == "manual":
                if manual_offset is None:
                    raise PermanentWorkerError("sync mode=manual requires offset_seconds")
                result = await self._apply_offset(
                    audio_path, out_path, float(manual_offset), method="manual"
                )
            else:  # auto
                if not reference:
                    raise PermanentWorkerError("sync mode=auto requires 'reference'")
                ref_path = Path(reference)
                if not ref_path.exists():
                    raise PermanentWorkerError(f"reference not found: {ref_path}")
                # Duration-based heuristic: compare audio vs video length.
                audio_dur = await _ffprobe_duration(audio_path)
                video_dur = await _ffprobe_duration(ref_path)
                delta = audio_dur - video_dur
                log.info(
                    "[sync] audio=%.3fs video=%.3fs delta=%+.3fs",
                    audio_dur,
                    video_dur,
                    delta,
                )
                # Sanity check: if the extracted audio is dramatically shorter
                # than the reference video, the extractor most likely captured
                # a placeholder/ad/preview rather than the feature stream.
                if video_dur > 60 and audio_dur < 0.5 * video_dur:
                    raise PlaceholderAudioError(
                        audio_duration=audio_dur,
                        video_duration=video_dur,
                    )
                length_tol = max(settings.threshold_seconds, 2.0)
                if abs(delta) <= length_tol:
                    log.info("[sync] durations match within %.1fs; passthrough", length_tol)
                    shutil.copyfile(audio_path, out_path)
                    result = SyncResult(path=out_path, offset_seconds=0.0, method="passthrough")
                else:
                    ratio_name = _classify_ratio(audio_dur, video_dur)
                    if ratio_name is None:
                        log.warning(
                            "[sync] duration mismatch %.3fs but no known fps ratio; "
                            "passthrough (manual offset may be required)",
                            delta,
                        )
                        shutil.copyfile(audio_path, out_path)
                        result = SyncResult(path=out_path, offset_seconds=0.0, method="passthrough")
                    else:
                        tempo = video_dur / audio_dur
                        log.info(
                            "[sync] applying fps correction (%s, tempo=%.5f)",
                            ratio_name,
                            tempo,
                        )
                        result = await self._apply_tempo(audio_path, out_path, tempo)
        except SyncError as exc:
            raise WorkerError(f"sync failed: {exc}") from exc

        assert ctx.job.id is not None
        artifact = ctx.repo.add_artifact(
            Artifact(
                job_id=ctx.job.id,
                kind="audio",
                path=result.path,
                size_bytes=result.path.stat().st_size if result.path.exists() else None,
                metadata={
                    "offset_seconds": result.offset_seconds,
                    "method": result.method,
                    "source_audio": str(audio_path),
                },
            )
        )
        return {
            "artifact_id": artifact.id,
            "path": str(result.path),
            "offset_seconds": result.offset_seconds,
            "method": result.method,
        }

    async def _apply_offset(
        self, src: Path, dst: Path, offset: float, *, method: str
    ) -> SyncResult:
        # ffmpeg: ``-itsoffset`` shifts the input timeline. Positive values
        # delay the audio (audio starts later than video).
        cmd = [
            "ffmpeg",
            "-y",
            "-itsoffset",
            f"{offset:.6f}",
            "-i",
            str(src),
            "-c:a",
            "copy",
            str(dst),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise SyncError(f"ffmpeg failed: {stderr.decode(errors='ignore')[:500]}")
        return SyncResult(path=dst, offset_seconds=offset, method=method)

    async def _apply_tempo(self, src: Path, dst: Path, tempo: float) -> SyncResult:
        """Re-encode audio at a different speed via ffmpeg's atempo filter.

        ``tempo`` is the playback speed factor (>1 = faster). atempo
        accepts values in [0.5, 100.0]; for our typical PAL/NDF cases the
        ratio is in [0.95, 1.05] so a single filter pass suffices.
        """
        # atempo "copy" is not allowed â€” must re-encode. Use AAC by default.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-filter:a",
            f"atempo={tempo:.6f}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(dst),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise SyncError(f"ffmpeg atempo failed: {stderr.decode(errors='ignore')[:500]}")
        # Report the equivalent end-to-end offset (positive = audio shortened).
        return SyncResult(path=dst, offset_seconds=0.0, method="atempo")


class AlassRunner:
    """Wraps ``alass-cli`` to derive a numeric offset.

    Strategy: alass writes the corrected subtitle, but for raw audio we
    only need the offset. We use ``alass`` against the reference's
    extracted audio + the target audio in a "discover" pass, then parse
    the offset from stdout.

    NOTE: ``alass`` is primarily a subtitle synchronizer. For audio we
    fingerprint via its audio mode (``--encoding`` etc.). A future
    revision can swap this for ``ffmpeg-normalize`` + cross-correlation
    if alass proves brittle for pure-audio workflows.
    """

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or get_settings().sync.alass_binary

    async def detect_offset(self, *, reference: Path, target: Path) -> float:
        cmd = [self._binary, str(reference), str(target), "-"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise SyncError(f"alass failed: {stderr.decode(errors='ignore')[:500]}")
        return _parse_alass_offset(stdout.decode(errors="ignore") + stderr.decode(errors="ignore"))


def _parse_alass_offset(text: str) -> float:
    """Find ``offset â€¦ <n>s`` in alass output and return the seconds value."""
    import re

    # alass prints lines like: "Detected offset: -1.234s"
    m = re.search(r"offset[^-\d]*(-?\d+(?:\.\d+)?)\s*s", text, re.IGNORECASE)
    if not m:
        raise SyncError(f"could not parse alass offset; output:\n{text[:500]}")
    return float(m.group(1))
