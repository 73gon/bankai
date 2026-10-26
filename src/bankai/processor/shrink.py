"""Shrink a video: its picture encoded again, on the GPU, at a quality the eye cannot tell apart.

UHD Blu-ray remuxes carry about 51 Mbps of picture and peak above 100 Mbps:
Game of Thrones was 73 episodes in 1.9 TB, and Wi-Fi could not carry it to a
TV. The picture is encoded again with NVENC HEVC at constant quality 22 --
measured on Game of Thrones, about half the size at SSIM 0.995 -- its peaks
capped at 40 Mbps; everything else is kept as it is: 4K, HDR10, Dolby Vision
(as profile 8.1), every audio track, subtitles, chapters.

The steps leave their files in a work folder, so a run that stops goes on
from the step it got to:

1. ``video.hevc``     the picture, encoded (``encode.json`` once complete)
2. ``rpu.bin``        Dolby Vision's per-frame metadata from the source,
                      converted to profile 8.1 (``dovi_tool -m 2``)
3. ``video.dv.hevc``  the new picture with that metadata put back in
4. the destination    the new picture and every other track of the source
                      (mkvmerge), HDR10's mastering metadata on the track

and the result is checked -- as many frames, as long, every track there --
before it is called done. Dolby Vision profile 5 (streaming releases) is
refused: its picture is not HDR10 underneath, and encoded again its colours
would be wrong.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from bankai.logging import get_logger

log = get_logger(__name__)

# Constant quality: lower is better and bigger. 22 is the measured middle --
# 20 kept 70% of the size, 26 looked softer in dark grain.
DEFAULT_QUALITY = 22
# The peak a home network has to carry, whatever the scene.
_MAXRATE = "40M"
_BUFSIZE = "80M"
# A keyframe every four seconds at 24 fps: seeking stays quick.
_GOP = 96


class ShrinkError(RuntimeError):
    pass


class ShrinkInterrupted(ShrinkError):
    """A step's program was stopped from outside (a restart): no failure, the steps done stay."""


def _stopped(returncode: int, what: str) -> None:
    # A negative return code: killed by a signal, as a service restart does.
    if returncode < 0:
        raise ShrinkInterrupted(f"{what} was stopped")


def dovi_tool_bin() -> str | None:
    """dovi_tool (github.com/quietvoid/dovi_tool), on the PATH or in ~/.local/bin."""
    found = shutil.which("dovi_tool")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "dovi_tool"
    return str(local) if local.is_file() and os.access(local, os.X_OK) else None


def _tool(name: str) -> str:
    found = dovi_tool_bin() if name == "dovi_tool" else shutil.which(name)
    if not found:
        raise ShrinkError(f"{name} is not installed")
    return found


@dataclass
class Source:
    """What of a video the steps need to know."""

    path: Path
    duration: float
    frame_rate: str
    width: int | None = None
    height: int | None = None
    bit_depth: int = 8
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    language: str | None = None
    dv_profile: int | None = None
    # HDR10's static metadata: the mastering display and the content light level.
    mastering: dict[str, float] | None = None
    max_cll: int = 0
    max_fall: int = 0
    hdr10plus: bool = False
    size: int = 0
    streams: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fps(self) -> float:
        return float(Fraction(self.frame_rate))

    @property
    def frames(self) -> int:
        return round(self.duration * self.fps)

    @property
    def hdr(self) -> bool:
        return self.color_transfer in {"smpte2084", "arib-std-b67"}


def _ratio(value: Any) -> float:
    return float(Fraction(str(value))) if value not in (None, "") else 0.0


def _ffprobe(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [_tool("ffprobe"), "-v", "error", *args, "-of", "json"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise ShrinkError(f"ffprobe failed: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout or "{}")


def inspect(path: Path) -> Source:
    """The video's picture as the steps read it: frame rate, colours, HDR10 and Dolby Vision."""
    data = _ffprobe(["-show_format", "-show_streams", str(path)])
    streams = data.get("streams") or []
    video = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise ShrinkError(f"{path.name} has no video")
    dv_profile = None
    for item in video.get("side_data_list") or []:
        if item.get("side_data_type") == "DOVI configuration record":
            dv_profile = int(item.get("dv_profile") or 0) or None
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ShrinkError(f"{path.name} has no duration")
    pix_fmt = str(video.get("pix_fmt") or "")
    source = Source(
        path=path,
        duration=duration,
        frame_rate=str(video.get("r_frame_rate") or video.get("avg_frame_rate") or "24000/1001"),
        width=video.get("width"),
        height=video.get("height"),
        bit_depth=10 if "10" in pix_fmt or "12" in pix_fmt else 8,
        color_primaries=video.get("color_primaries"),
        color_transfer=video.get("color_transfer"),
        color_space=video.get("color_space"),
        language=(video.get("tags") or {}).get("language"),
        dv_profile=dv_profile,
        size=int((data.get("format") or {}).get("size") or 0),
        streams=streams,
    )
    # The static HDR metadata rides on the frames.
    frames = _ffprobe(
        ["-select_streams", "v:0", "-read_intervals", "%+#1", "-show_frames", "-show_entries", "frame=side_data_list", str(path)]
    )
    for frame in frames.get("frames") or []:
        for item in frame.get("side_data_list") or []:
            kind = item.get("side_data_type")
            if kind == "Mastering display metadata":
                source.mastering = {
                    key: _ratio(item.get(key))
                    for key in (
                        "red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y",
                        "white_point_x", "white_point_y", "min_luminance", "max_luminance",
                    )
                }
            elif kind == "Content light level metadata":
                source.max_cll = int(item.get("max_content") or 0)
                source.max_fall = int(item.get("max_average") or 0)
            elif kind == "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)":
                source.hdr10plus = True
    return source


def encode_command(
    source: Source,
    output: Path,
    *,
    quality: int = DEFAULT_QUALITY,
    codec: str = "hevc",
    progress: str = "pipe:1",
) -> list[str]:
    """ffmpeg encoding the source's picture alone into a raw stream on the GPU: HEVC or H.264.

    Frames pass through one for one (Dolby Vision's metadata is put back per
    frame); the colours are signalled as the source's. H.264 is for sources
    that were H.264: devices that play the original play the result.
    """
    if codec not in {"hevc", "h264"}:
        raise ShrinkError(f"no such codec: {codec}")
    command = [
        _tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-progress", progress,
        # Decoded on the GPU and kept there; a codec it cannot decode falls back to the CPU.
        "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
        "-i", str(source.path),
        "-map", "0:v:0", "-fps_mode", "passthrough",
        "-c:v", f"{codec}_nvenc", "-preset", "p7", "-tune", "hq", "-multipass", "fullres",
        "-rc", "vbr", "-cq", str(int(quality)), "-b:v", "0", "-maxrate", _MAXRATE, "-bufsize", _BUFSIZE,
    ]
    if codec == "hevc":
        command += ["-profile:v", "main10" if source.bit_depth > 8 else "main", "-bf", "4"]
    else:
        command += ["-profile:v", "high", "-bf", "3"]
    command += [
        "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "32",
        "-b_ref_mode", "middle", "-g", str(_GOP),
    ]
    for flag, value in (
        ("-color_primaries", source.color_primaries),
        ("-color_trc", source.color_transfer),
        ("-colorspace", source.color_space),
    ):
        if value and value != "unknown":
            command += [flag, value]
    if source.hdr:
        command += ["-color_range", "tv"]
    return [*command, "-f", codec, str(output)]


def _run_encode(
    command: list[str], *, total_frames: int, on_progress: Callable[[float], None] | None = None
) -> int:
    """Run the encode, reporting its progress; returns how many frames it wrote."""
    frames = 0
    # ffmpeg's errors go to a file: a pipe nobody reads while the progress is
    # read could fill and stall it.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errors, subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=errors, encoding="utf-8", errors="replace"
    ) as process:
        assert process.stdout is not None
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key == "frame" and value.isdigit():
                frames = int(value)
            elif key == "progress" and total_frames:
                fraction = min(1.0, frames / total_frames)
                log.info("BANKAI_PROGRESS stage=encode pct=%.1f frames=%d", fraction * 100, frames)
                if on_progress is not None:
                    on_progress(fraction)
        returncode = process.wait()
        _stopped(returncode, "the encode")
        if returncode != 0:
            errors.seek(0)
            raise ShrinkError(f"the encode failed: {errors.read().strip()[-500:]}")
    return frames


def _rpu_commands(source: Source, rpu: Path) -> tuple[list[str], list[str]]:
    """The source's picture as a raw stream, piped to dovi_tool, which keeps its RPU as profile 8.1."""
    stream = [
        _tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(source.path), "-map", "0:v:0", "-c:v", "copy", "-bsf:v", "hevc_mp4toannexb", "-f", "hevc", "-",
    ]
    extract = [_tool("dovi_tool"), "-m", "2", "extract-rpu", "-", "-o", str(rpu)]
    return stream, extract


def _extract_rpu(source: Source, rpu: Path) -> None:
    stream, extract = _rpu_commands(source, rpu)
    with subprocess.Popen(stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as reader:
        result = subprocess.run(extract, stdin=reader.stdout, capture_output=True, check=False)
        if reader.stdout:
            reader.stdout.close()
        reader.wait()
    _stopped(min(result.returncode, reader.returncode or 0), "reading the Dolby Vision metadata")
    if result.returncode != 0 or not rpu.is_file():
        raise ShrinkError("Dolby Vision metadata could not be read: " + result.stderr.decode("utf-8", "replace")[-300:])


def _inject_rpu(video: Path, rpu: Path, output: Path) -> None:
    result = subprocess.run(
        [_tool("dovi_tool"), "inject-rpu", "-i", str(video), "--rpu-in", str(rpu), "-o", str(output)],
        capture_output=True,
        check=False,
    )
    _stopped(result.returncode, "putting the Dolby Vision metadata back")
    if result.returncode != 0 or not output.is_file():
        raise ShrinkError("Dolby Vision metadata could not be put back: " + result.stderr.decode("utf-8", "replace")[-300:])


def _decimal(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def mux_command(source: Source, video: Path, output: Path, *, drop_audio: list[int] = ()) -> list[str]:  # type: ignore[assignment]
    """mkvmerge: the new picture first, then every other track of the source but the audio ``drop_audio`` (mkvmerge ids)."""
    track = ["--language", f"0:{source.language or 'und'}", "--default-duration", f"0:{source.frame_rate}p"]
    codes = {
        # Matroska's numbers, as ISO/IEC 23091-4 has them.
        "primaries": {"bt709": 1, "bt2020": 9},
        "transfer": {"bt709": 1, "smpte2084": 16, "arib-std-b67": 18},
        "matrix": {"bt709": 1, "bt2020nc": 9, "bt2020c": 10},
    }
    for option, table, value in (
        ("--colour-primaries", codes["primaries"], source.color_primaries),
        ("--colour-transfer-characteristics", codes["transfer"], source.color_transfer),
        ("--colour-matrix-coefficients", codes["matrix"], source.color_space),
    ):
        if value in table:
            track += [option, f"0:{table[value]}"]
    if source.hdr:
        track += ["--colour-range", "0:1"]
    if source.mastering:
        m = source.mastering
        track += [
            "--chromaticity-coordinates",
            "0:" + ",".join(_decimal(m[key]) for key in ("red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y")),
            "--white-colour-coordinates", f"0:{_decimal(m['white_point_x'])},{_decimal(m['white_point_y'])}",
            "--max-luminance", f"0:{_decimal(m['max_luminance'])}",
            "--min-luminance", f"0:{_decimal(m['min_luminance'])}",
        ]
    if source.max_cll:
        track += ["--max-content-light", f"0:{source.max_cll}"]
    if source.max_fall:
        track += ["--max-frame-light", f"0:{source.max_fall}"]
    rest = ["--no-video"]
    if drop_audio:
        # A second lossless copy of the same mix (House of the Dragon: DTS-HD MA beside TrueHD Atmos).
        rest += ["--audio-tracks", "!" + ",".join(str(track_id) for track_id in drop_audio)]
    return [_tool("mkvmerge"), "-q", "-o", str(output), *track, str(video), *rest, str(source.path)]


def _stream_count(streams: list[dict[str, Any]], kind: str) -> int:
    return sum(
        1
        for stream in streams
        if stream.get("codec_type") == kind and not (stream.get("disposition") or {}).get("attached_pic")
    )


def verify(source: Source, output: Path, *, dropped_audio: int = 0, dolby_vision: bool = True) -> Source:
    """The result, checked: as long, every track there, and Dolby Vision where the source had it."""
    result = inspect(output)
    frame = 1 / source.fps
    if abs(result.duration - source.duration) > max(1.0, 2 * frame):
        raise ShrinkError(f"the result runs {result.duration:.2f}s, the source {source.duration:.2f}s")
    for kind, missing in (("audio", dropped_audio), ("subtitle", 0)):
        expected = _stream_count(source.streams, kind) - missing
        if _stream_count(result.streams, kind) != expected:
            raise ShrinkError(f"the result has {_stream_count(result.streams, kind)} {kind} tracks, not {expected}")
    if dolby_vision and source.dv_profile and result.dv_profile != 8:
        raise ShrinkError("the result lost its Dolby Vision")
    if source.hdr and result.color_transfer != source.color_transfer:
        raise ShrinkError("the result lost its HDR signalling")
    return result


def _settings(source: Source, quality: int, codec: str = "hevc") -> dict[str, Any]:
    return {"codec": codec, "quality": int(quality), "maxrate": _MAXRATE, "gop": _GOP, "source_size": source.size, "version": 2}


_WORK_FILES = ("video.hevc", "video.h264", "rpu.bin", "video.dv.hevc")


def shrink(
    source_path: Path,
    destination: Path,
    *,
    work: Path,
    quality: int = DEFAULT_QUALITY,
    codec: str = "hevc",
    drop_audio: list[int] = (),  # type: ignore[assignment]
    dolby_vision: bool = True,
    on_progress: Callable[[str, float | None], None] | None = None,
) -> dict[str, Any]:
    """Encode ``source_path``'s picture again into ``destination``, every other track kept.

    ``work`` keeps the steps' files; a step already done there with the same
    settings is not done again, and other settings start it over. The source
    is never touched. ``dolby_vision`` False leaves Dolby Vision out (HDR10
    alone). ``on_progress(step, fraction)`` hears where it is: "encode" with
    the share done, then "dolby_vision" and "mux".
    """
    def report(step: str, fraction: float | None = None) -> None:
        if on_progress is not None:
            on_progress(step, fraction)

    source = inspect(source_path)
    if source.dv_profile == 5:
        raise ShrinkError("Dolby Vision profile 5 is not HDR10 underneath: encoded again, its colours would be wrong")
    if source.hdr10plus:
        log.warning("[shrink] %s carries HDR10+, which is not kept", source_path.name)
    work.mkdir(parents=True, exist_ok=True)
    settings = _settings(source, quality, codec)
    marker = work / "encode.json"
    done = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
    if done.get("settings") != settings:
        for name in _WORK_FILES:
            (work / name).unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        done = {}
    video = work / f"video.{codec}"
    keep_dv = bool(source.dv_profile) and dolby_vision and codec == "hevc"
    picture = work / "video.dv.hevc" if keep_dv else video
    if not picture.is_file():
        if not video.is_file() or "frames" not in done:
            free = shutil.disk_usage(work).free
            if free < source.size:
                raise ShrinkError(f"{free / 2**30:.0f} GiB free in {work}: the steps need up to the source's size")
            log.info('BANKAI_STAGE step=1 total=3 key=encode label="Encode the picture"')
            report("encode", 0.0)
            frames = _run_encode(
                encode_command(source, video, quality=quality, codec=codec),
                total_frames=source.frames,
                on_progress=lambda fraction: report("encode", fraction),
            )
            done = {"settings": settings, "frames": frames}
            marker.write_text(json.dumps(done), encoding="utf-8")
        frames = int(done.get("frames") or 0)
        if abs(frames - source.frames) > 2:
            raise ShrinkError(f"the encode wrote {frames} frames of {source.frames}")
        if keep_dv:
            log.info('BANKAI_STAGE step=2 total=3 key=dolby_vision label="Dolby Vision"')
            report("dolby_vision")
            rpu = work / "rpu.bin"
            if not rpu.is_file():
                partial = work / "rpu.part.bin"
                _extract_rpu(source, partial)
                partial.replace(rpu)
            partial = work / "video.dv.part.hevc"
            _inject_rpu(video, rpu, partial)
            partial.replace(picture)
            # Only the picture with Dolby Vision is needed from here: room for the mux.
            video.unlink(missing_ok=True)
    frames = int(done.get("frames") or 0)
    if abs(frames - source.frames) > 2:
        raise ShrinkError(f"the encode wrote {frames} frames of {source.frames}")
    log.info('BANKAI_STAGE step=3 total=3 key=mux label="Put the tracks together"')
    report("mux")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.part")
    result = subprocess.run(
        mux_command(source, picture, partial, drop_audio=list(drop_audio)),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    # mkvmerge exits 1 for warnings, with the file written.
    if result.returncode not in (0, 1) or not partial.is_file():
        partial.unlink(missing_ok=True)
        _stopped(result.returncode, "mkvmerge")
        raise ShrinkError("mkvmerge failed: " + (result.stdout + result.stderr).strip()[-300:])
    try:
        checked = verify(source, partial, dropped_audio=len(drop_audio), dolby_vision=keep_dv)
    except ShrinkError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)
    size = destination.stat().st_size
    log.info("[shrink] %s: %.1f GiB -> %.1f GiB", source_path.name, source.size / 2**30, size / 2**30)
    return {
        "source": str(source_path),
        "destination": str(destination),
        "source_size": source.size,
        "size": size,
        "saved": source.size - size,
        "frames": frames,
        "codec": codec,
        "dolby_vision": checked.dv_profile,
        "hdr": checked.color_transfer,
    }


def quality_check(source_path: Path, output: Path, *, points: tuple[float, ...] = (0.15, 0.5, 0.85), seconds: float = 10.0) -> list[dict[str, float]]:
    """SSIM and PSNR of the result against the source at a few places, frames paired by their order."""
    source = inspect(source_path)
    rate = source.frame_rate
    count = max(1, round(seconds * source.fps))
    rows = []
    for point in points:
        start = f"{source.duration * point:.3f}"
        graph = (
            f"[0:v]setpts=N/({rate})/TB,trim=end_frame={count},split[a1][a2];"
            f"[1:v]setpts=N/({rate})/TB,trim=end_frame={count},split[b1][b2];"
            "[a1][b1]ssim;[a2][b2]psnr"
        )
        result = subprocess.run(
            [
                _tool("ffmpeg"), "-hide_banner", "-nostdin", "-nostats",
                "-ss", start, "-i", str(output), "-ss", start, "-i", str(source_path),
                "-lavfi", graph, "-f", "null", "-",
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check=False,
        )
        ssim = psnr = 0.0
        for line in result.stderr.splitlines():
            if "SSIM" in line and "All:" in line:
                ssim = float(line.split("All:")[1].split()[0])
            if "PSNR" in line and "y:" in line:
                psnr = float(line.split("y:")[1].split()[0])
        rows.append({"at": float(start), "ssim": ssim, "psnr_y": psnr})
    return rows


__all__ = [
    "DEFAULT_QUALITY",
    "ShrinkError",
    "ShrinkInterrupted",
    "Source",
    "dovi_tool_bin",
    "encode_command",
    "inspect",
    "mux_command",
    "quality_check",
    "shrink",
    "verify",
]
