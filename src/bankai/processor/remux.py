"""Final remux worker â€” invokes ``mkvmerge`` to combine HQ video + dub.

Pipeline stage 7: take the HQ video file (preserved as-is) and the synced
German audio file, and produce a single MKV with the dub added as an
additional named, language-tagged audio track.

Job payload schema
------------------

``payload`` for a REMUX job::

    {
        "video": "/downloads/movie.mkv",
        "audio": "/work/job-7/synced.aac",
        "out": "/library/Inception (2010) [ger].mkv",
        "language": "ger",
        "track_name": "German (Web-DL)",
        "default_track": false
    }
"""

from __future__ import annotations

import asyncio
import json
import shutil
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


class RemuxError(Exception):
    pass


class RemuxWorker(Worker):
    kind = JobKind.REMUX

    def __init__(self, *, mkvmerge_binary: str = "mkvmerge") -> None:
        if shutil.which(mkvmerge_binary) is None:
            log.warning("%s not found on PATH; remux jobs will fail at runtime", mkvmerge_binary)
        self._bin = mkvmerge_binary

    async def run(self, ctx: WorkerContext) -> dict[str, Any] | None:
        payload = ctx.job.payload
        video = payload.get("video")
        audio = payload.get("audio")
        out = payload.get("out")
        if not (video and audio and out):
            raise PermanentWorkerError("remux job payload requires 'video', 'audio', and 'out'")
        video_p, audio_p, out_p = Path(video), Path(audio), Path(out)
        for label, p in (("video", video_p), ("audio", audio_p)):
            if not p.exists():
                raise PermanentWorkerError(f"{label} not found: {p}")
        out_p.parent.mkdir(parents=True, exist_ok=True)

        settings = get_settings().audio
        language = payload.get("language", settings.language_tag)
        track_name = payload.get("track_name", settings.track_name)
        default_track = bool(payload.get("default_track", settings.default_track))

        cmd = await build_mkvmerge_command(
            video=video_p,
            audio=audio_p,
            out=out_p,
            language=language,
            track_name=track_name,
            default_track=default_track,
            binary=self._bin,
            audio_track_id=await _first_audio_track_id(audio_p, binary=self._bin),
            video_audio_track_ids=await _all_audio_track_ids(video_p, binary=self._bin),
        )
        log.info("[remux] mkvmerge %s", " ".join(cmd[1:]))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode not in (0, 1):  # mkvmerge returns 1 for warnings
            raise WorkerError(
                f"mkvmerge failed (rc={proc.returncode}): "
                f"{stderr.decode(errors='ignore')[:500] or stdout.decode(errors='ignore')[:500]}"
            )

        if not out_p.exists():
            raise WorkerError(f"mkvmerge succeeded but output missing: {out_p}")

        # Verify with mkvmerge -i.
        verified = await verify_output(out_p, binary=self._bin)
        log.info("[remux] verified %s â€” %d tracks", out_p, len(verified.get("tracks", [])))

        assert ctx.job.id is not None
        artifact = ctx.repo.add_artifact(
            Artifact(
                job_id=ctx.job.id,
                kind="final",
                path=out_p,
                size_bytes=out_p.stat().st_size,
                metadata={
                    "language": language,
                    "track_name": track_name,
                    "tracks": len(verified.get("tracks", [])),
                },
            )
        )
        return {"artifact_id": artifact.id, "path": str(out_p)}


async def build_mkvmerge_command(
    *,
    video: Path,
    audio: Path,
    out: Path,
    language: str,
    track_name: str,
    default_track: bool,
    binary: str = "mkvmerge",
    audio_track_id: int = 0,
    video_audio_track_ids: tuple[int, ...] = (),
) -> list[str]:
    """Construct the mkvmerge argv. Pure function â€” easy to test.

    When ``default_track`` is true, the dub gets the default flag and
    every audio track in the source video has its default flag cleared
    via ``--default-track-flag <id>:0`` so players auto-select the dub.
    """
    tid = str(audio_track_id)
    cmd = [binary, "--output", str(out)]
    # Source 0: HQ video. Clear default flags on its existing audio
    # tracks when the dub should be the new default.
    if default_track and video_audio_track_ids:
        for vid_aid in video_audio_track_ids:
            cmd += ["--default-track-flag", f"{vid_aid}:0"]
    cmd.append(str(video))
    # Source 1: dub audio. Strip any video/subs/buttons that may be
    # present in a synced .mp4 container so only the audio remains.
    cmd += [
        "--no-video",
        "--no-subtitles",
        "--no-buttons",
        "--no-chapters",
        "--no-global-tags",
        "--no-track-tags",
        "--language",
        f"{tid}:{language}",
        "--track-name",
        f"{tid}:{track_name}",
        "--default-track-flag",
        f"{tid}:{'1' if default_track else '0'}",
        str(audio),
    ]
    return cmd


async def _first_audio_track_id(path: Path, *, binary: str = "mkvmerge") -> int:
    """Return the source-file track ID of the first audio track in ``path``.

    mkvmerge's per-track flags (``--language T:lang`` etc.) reference the
    track ID in the source container, not the position in the muxed
    output. When the dub source is an MP4 with both video + audio, the
    audio is typically track 1.
    """
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-J",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode not in (0, 1):
        return 0
    try:
        data = json.loads(stdout.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return 0
    for t in data.get("tracks", []):
        if t.get("type") == "audio":
            return int(t.get("id", 0))
    return 0


async def _all_audio_track_ids(path: Path, *, binary: str = "mkvmerge") -> tuple[int, ...]:
    """Return the source-file track IDs of every audio track in ``path``."""
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-J",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode not in (0, 1):
        return ()
    try:
        data = json.loads(stdout.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return ()
    return tuple(int(t["id"]) for t in data.get("tracks", []) if t.get("type") == "audio")


async def verify_output(path: Path, *, binary: str = "mkvmerge") -> dict[str, Any]:
    """Run ``mkvmerge -J <file>`` and parse the JSON identification output."""
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-J",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode not in (0, 1):
        raise RemuxError(f"verify failed: {stderr.decode(errors='ignore')[:500]}")
    try:
        data: dict[str, Any] = json.loads(stdout.decode("utf-8", errors="ignore"))
        return data
    except json.JSONDecodeError as exc:
        raise RemuxError(f"could not parse mkvmerge -J output: {exc}") from exc
