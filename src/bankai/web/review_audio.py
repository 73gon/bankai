"""Each audio track of a large file under review, read once and kept small.

The review studio asks for waveforms of a window and short clips of both
tracks, every time from the file. On a 4K remux (The Odyssey: 88 GB, 70 Mbit/s)
the audio sits between the video's packets: two minutes of it meant reading
over a gigabyte through WSL's mount, 50-70 s a waveform, and the overview's
half-hour chunks could never finish inside the request's minute -- the studio
loaded forever (Nov 2026).

So a file of ``THRESHOLD`` bytes or more is read once, end to end, by one
ffmpeg pass, into a folder on its own drive (``<drive>/bankai/review_audio/
<key>``, keyed by path, size and time): per audio track a mono 8 kHz PCM for
the detailed waveform, a 200 Hz envelope (``abs``) for the overview, and an
AAC 2.0 copy to listen to. Waveforms are then read straight from those files
by offset; clips are cut from the AAC. Smaller files go on as before. One file
is prepared at a time, in a thread; ``status`` says how far.
"""

from __future__ import annotations

import hashlib
import json
import queue
import shutil
import subprocess
import threading
import time
from array import array
from pathlib import Path
from typing import Any

from bankai.logging import get_logger

log = get_logger(__name__)

THRESHOLD = 12 * 1024**3
DETAIL_RATE = 8000
OVERVIEW_RATE = 200
KEEP_SECONDS = 21 * 24 * 3600

_queue: queue.Queue[Path] = queue.Queue()
_queued: set[str] = set()
_progress: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_worker: threading.Thread | None = None


def wanted(path: Path) -> bool:
    try:
        return path.stat().st_size >= THRESHOLD
    except OSError:
        return False


def folder(path: Path) -> Path:
    stat = path.stat()
    parts = path.parts
    drive = Path(*parts[:3]) if len(parts) > 3 and parts[1] == "mnt" else Path(path.anchor)
    key = hashlib.sha1(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]
    return drive / "bankai" / "review_audio" / key


def ready(path: Path) -> dict[str, Any] | None:
    """The finished cache's facts (streams, duration), or None."""
    try:
        done = json.loads((folder(path) / "done.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return done if isinstance(done, dict) else None


def status(path: Path) -> dict[str, Any]:
    if ready(path):
        return {"ready": True}
    with _lock:
        current = dict(_progress.get(str(path)) or {})
        waiting = str(path) in _queued
    return {"ready": False, "preparing": bool(current) or waiting, **current}


def ensure(path: Path) -> dict[str, Any]:
    """Queue the file's preparation unless it is done or under way; its status."""
    global _worker
    if not wanted(path) or ready(path):
        return status(path)
    with _lock:
        if str(path) not in _queued:
            _queued.add(str(path))
            _queue.put(path)
            _progress.setdefault(str(path), {"percent": 0.0, "step": "Waiting"})
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="review-audio", daemon=True)
            _worker.start()
    return status(path)


def _audio_streams(path: Path) -> tuple[list[int], float]:
    from bankai.web import media as media_mod

    probe = media_mod.ffprobe_bin() or "ffprobe"
    done = subprocess.run(
        [probe, "-v", "error", "-show_entries", "stream=index,codec_type:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    data = json.loads(done.stdout or "{}")
    streams = [int(row["index"]) for row in data.get("streams") or [] if row.get("codec_type") == "audio"]
    return streams, float((data.get("format") or {}).get("duration") or 0)


def _prepare(path: Path) -> None:
    from bankai.web import media as media_mod

    target = folder(path)
    streams, duration = _audio_streams(path)
    if not streams:
        raise RuntimeError("no audio track")
    part = target.with_name(target.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    ffmpeg = media_mod.ffmpeg_bin() or "ffmpeg"
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(path)]
    for index in streams:
        cmd += ["-map", f"0:{index}", "-ac", "1", "-ar", str(DETAIL_RATE), "-f", "s16le", str(part / f"{index}.detail.s16")]
        cmd += [
            "-map", f"0:{index}", "-af", "aformat=channel_layouts=mono,aeval=abs(val(0))", "-ac", "1",
            "-ar", str(OVERVIEW_RATE), "-f", "s16le", str(part / f"{index}.overview.s16"),
        ]
        cmd += ["-map", f"0:{index}", "-ac", "2", "-c:a", "aac", "-b:a", "160k", "-f", "mp4", str(part / f"{index}.m4a")]
    started = time.time()
    errors = part / "ffmpeg.log"
    # stderr into a file: a pipe nobody reads would stall a long pass once it filled.
    with open(errors, "w", encoding="utf-8") as log_file, subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=log_file, text=True, encoding="utf-8", errors="replace",
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit() and duration:
                done = min(1.0, int(value) / 1e6 / duration)
                with _lock:
                    _progress[str(path)] = {
                        "percent": round(100 * done, 1),
                        "step": "Reading the file's audio once",
                        "eta": round((time.time() - started) / done * (1 - done)) if done > 0.01 else None,
                    }
    if proc.returncode != 0:
        error = errors.read_text(encoding="utf-8", errors="replace")[-300:]
        shutil.rmtree(part, ignore_errors=True)
        raise RuntimeError(f"ffmpeg failed: {error.strip()}")
    errors.unlink(missing_ok=True)
    (part / "done.json").write_text(json.dumps({"streams": streams, "duration": duration, "made": time.time()}), encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    part.rename(target)
    log.info("[review] audio of %s ready for review in %d s", path.name, round(time.time() - started))


def _tidy(root: Path) -> None:
    """Caches untouched for three weeks go: their files are approved, moved or changed."""
    now = time.time()
    for entry in root.glob("*"):
        try:
            if now - entry.stat().st_mtime > KEEP_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def _run() -> None:
    while True:
        try:
            path = _queue.get(timeout=60)
        except queue.Empty:
            return
        try:
            if not ready(path):
                log.info("[review] reading the audio of %s once (%.0f GB)", path.name, path.stat().st_size / 1e9)
                _prepare(path)
            _tidy(folder(path).parent)
        except Exception as exc:  # noqa: BLE001 -- the next file goes on
            log.warning("[review] could not prepare the audio of %s: %s", path, exc)
            with _lock:
                _progress[str(path)] = {"percent": 0.0, "step": "Failed", "error": str(exc)}
        finally:
            with _lock:
                _queued.discard(str(path))
                if (_progress.get(str(path)) or {}).get("step") != "Failed":
                    _progress.pop(str(path), None)


def samples(path: Path, stream: int, start: float, dur: float, *, detailed: bool) -> array | None:
    """The cached PCM of a window, or None when the cache does not have this track."""
    base = folder(path)
    raw = base / f"{stream}.{'detail' if detailed else 'overview'}.s16"
    rate = DETAIL_RATE if detailed else OVERVIEW_RATE
    try:
        with open(raw, "rb") as handle:
            handle.seek(int(start * rate) * 2)
            data = handle.read(int(dur * rate) * 2)
    except OSError:
        return None
    out = array("h")
    out.frombytes(data[: len(data) - len(data) % 2])
    import sys

    if sys.byteorder != "little":
        out.byteswap()
    try:
        base.touch()
    except OSError:
        pass
    return out


def listening_copy(path: Path, stream: int) -> Path | None:
    copy = folder(path) / f"{stream}.m4a"
    return copy if copy.exists() else None


def prepare_waiting() -> int:
    """Queue every large file waiting for review whose audio is not read yet, so it is ready
    before the studio opens it (a scheduler pass). The number queued."""
    from bankai.web import review as review_mod

    queued = 0
    for key, state in review_mod.all_states().items():
        if state.stage != "review":
            continue
        path = Path(key)
        if wanted(path) and not ready(path):
            ensure(path)
            queued += 1
    return queued

