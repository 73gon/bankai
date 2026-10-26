"""The libraries' videos encoded again where they are far bigger than anyone can see.

A UHD remux carries about 51 Mbps of picture, a Blu-ray 25-35 Mbps: more than
the eye tells apart, and more than Wi-Fi or a connection from away carries.
Per library -- anime, shows, movies, with the roots and the file inventory of
track_cleanup -- the Encode page lists the files worth encoding again; the
ones ticked there join a queue, and bankai encodes them in its order, one at
a time, on the GPU (see processor.shrink). Nothing is encoded unasked. HEVC
stays HEVC (quality 22 in 4K, 20 below), H.264 stays H.264 (quality 19):
whatever played the original plays the result. Audio, subtitles, chapters,
HDR10 and Dolby Vision (as profile 8.1) are kept. Measured: Game of Thrones in
4K and Ice Age in 1080p came out at half the size, SSIM 0.995 and 0.987.

Listed are files well above what the result would be: a picture of 30 Mbps
and more in 4K, 22 Mbps for 1080p H.264, 15 Mbps for other 1080p, 10 below.
In anime, whose library is HEVC, every episode that is not HEVC is listed,
whatever its bitrate, to become HEVC.
Left out: Dolby Vision profile 5 (not HDR10 underneath), interlaced pictures,
AV1 and VP9 (efficient already). A queued file hard-linked to a torrent still
seeding, or changed in the last hour, waits its turn.

The result replaces the original only once it is checked -- frames, length,
tracks, HDR, Dolby Vision, and the picture compared at three places (SSIM at
least 0.95: that catches a broken encode, it is no judgement of quality) --
when it is at least a fifth smaller, and when the original did not change
meanwhile. Each file is encoded once: the files done are kept with their
size, so a dark episode that stays above the line is not listed again.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.web import track_cleanup

log = get_logger(__name__)

SCOPES = track_cleanup.SCOPES
# What a file's picture is encoded as, by what it is.
_QUALITY_4K = 22
_QUALITY_HEVC = 20
_QUALITY_H264 = 19
# The least bitrate (Mbps) of a picture worth encoding again.
_LINE_4K = 30.0
_LINE_1080_H264 = 22.0
_LINE_1080_HEVC = 15.0
_LINE_SMALL = 10.0
# What the result runs at, roughly, for the estimate of the space freed.
_EXPECTED = {"4k": 22.0, "1080_hevc": 8.0, "1080_h264": 15.0, "small": 5.0}
# What HEVC saves of a non-HEVC anime episode's picture, roughly, for the estimate.
_ANIME_SAVING = 0.45
# A result less than a fifth smaller is not worth what it costs.
_MIN_SAVING = 0.2
_SSIM_FLOOR = 0.95
_SOURCES = {"hevc", "h264", "vc1", "mpeg2video", "mpeg4"}

_LOCK = threading.Lock()
# What encode_next is doing now, for the page: path, step, share done, start.
progress: dict[str, Any] = {}


def _path(name: str) -> Path:
    return bgjobs.jobs_root().parent / name


def _read(name: str) -> dict:
    try:
        raw = json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(name: str, data: dict) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def queue() -> list[dict[str, Any]]:
    """The files to encode, in their order: [{path, scope, added}]."""
    rows = _read("encode_queue.json").get("items")
    return [row for row in rows if isinstance(row, dict) and row.get("path")] if isinstance(rows, list) else []


def _save_queue(rows: list[dict[str, Any]]) -> None:
    _write("encode_queue.json", {"items": rows})


def has_work() -> bool:
    return bool(queue())


def enqueue(paths: list[str]) -> dict[str, Any]:
    """Put files at the end of the queue, in the order given; only files the page lists.

    A file that failed before is tried again: asked for by hand, it does not
    wait out its day.
    """
    inventory = track_cleanup.load_inventory()
    state = _state()
    added, refused = [], []
    with _LOCK:
        rows = queue()
        queued = {row["path"] for row in rows}
        for path in dict.fromkeys(str(raw) for raw in paths):
            entry = inventory.get(path)
            change = assess(path, entry)[0] if entry else None
            if change is None or _encoded_already(path, entry or {}, state["encoded"].get(path)):
                refused.append(path)
                continue
            if path not in queued:
                rows.append({"path": path, "scope": entry.get("scope"), "added": time.time()})
                queued.add(path)
                added.append(path)
        _save_queue(rows)
        if added:
            state = _state()
            for path in added:
                state["failed"].pop(path, None)
            _save_state(state)
    return {"added": len(added), "refused": [Path(path).name for path in refused], "queued": len(rows)}


def dequeue(paths: list[str] | None = None) -> int:
    """Take files out of the queue (all of them with None); the one being encoded goes on."""
    with _LOCK:
        rows = queue()
        drop = set(paths) if paths is not None else {row["path"] for row in rows}
        kept = [row for row in rows if row["path"] not in drop or row["path"] == progress.get("path")]
        _save_queue(kept)
    return len(rows) - len(kept)


def _unqueue(path: str) -> None:
    with _LOCK:
        _save_queue([row for row in queue() if row["path"] != path])


def _state() -> dict[str, Any]:
    raw = _read("encode_state.json")
    for key in ("done", "last", "failed", "skipped", "encoded", "waiting"):
        raw.setdefault(key, {})
    return raw


def _save_state(state: dict[str, Any]) -> None:
    _write("encode_state.json", state)


# -- which files, and as what --------------------------------------------------


def picture_mbps(entry: dict) -> float | None:
    """The picture's bitrate: from the track's own statistics, else most of the file's."""
    video = entry.get("video") or {}
    if video.get("bps"):
        return int(video["bps"]) / 1e6
    if entry.get("bit_rate"):
        return int(entry["bit_rate"]) * 0.9 / 1e6
    return None


def assess(path: str, entry: dict) -> tuple[dict[str, Any] | None, str | None]:
    """What a file would be encoded as, or why it is left as it is (None: nothing to say)."""
    video = entry.get("video")
    if not video or entry.get("unreadable") or Path(path).suffix.casefold() != ".mkv":
        return None, None
    codec = str(video.get("codec") or "")
    if codec not in _SOURCES:
        return None, None
    width = int(video.get("width") or 0)
    if entry.get("scope") == "anime" and codec != "hevc":
        return _anime_to_hevc(entry, video, width)
    # A 10-bit picture is HEVC either way: H.264 has no 10-bit NVENC, and 10-bit H.264 plays nowhere HEVC does not.
    target = "hevc" if codec == "hevc" or int(video.get("bit_depth") or 8) > 8 else "h264"
    if width >= 3200:
        line, size_class, quality = _LINE_4K, "4k", _QUALITY_4K if target == "hevc" else _QUALITY_H264
    elif width >= 1800:
        line = _LINE_1080_H264 if target == "h264" else _LINE_1080_HEVC
        size_class, quality = ("1080_h264", _QUALITY_H264) if target == "h264" else ("1080_hevc", _QUALITY_HEVC)
    else:
        line, size_class, quality = _LINE_SMALL, "small", _QUALITY_HEVC if target == "hevc" else _QUALITY_H264
    mbps = picture_mbps(entry)
    if mbps is None or mbps < line:
        return None, None
    if video.get("dv") == 5:
        return None, "Dolby Vision profile 5"
    if video.get("interlaced"):
        return None, "interlaced picture"
    duration = float(entry.get("duration") or 0)
    estimate = max(0.0, mbps - min(mbps, _EXPECTED[size_class])) * 1e6 / 8 * duration
    return {"codec": target, "quality": quality, "mbps": round(mbps, 1), "estimate": int(estimate)}, None


def _anime_to_hevc(entry: dict, video: dict, width: int) -> tuple[dict[str, Any] | None, str | None]:
    """An anime episode that is not HEVC becomes HEVC, whatever its bitrate.

    The anime library is HEVC but for these; HEVC carries the same picture in
    about half of H.264's bits. A result less than a fifth smaller is thrown
    away all the same, so a small episode keeps its H.264.
    """
    if video.get("interlaced"):
        return None, "interlaced picture"
    mbps = picture_mbps(entry)
    duration = float(entry.get("duration") or 0)
    bits = mbps * 1e6 / 8 * duration if mbps and duration else float(entry.get("size") or 0)
    change = {
        "codec": "hevc",
        "quality": _QUALITY_4K if width >= 3200 else _QUALITY_HEVC,
        "mbps": round(mbps or 0.0, 1),
        "estimate": int(bits * _ANIME_SAVING),
    }
    return change, None


def candidates(scope: str | None = None) -> tuple[list[tuple[str, str, dict]], dict[str, dict[str, int]]]:
    """The files worth encoding, most space freed first, and per library why others are left out.

    A file that failed is listed with its error, to be queued again.
    """
    state = _state()
    out = []
    left: dict[str, dict[str, int]] = {}
    for path, entry in track_cleanup.load_inventory().items():
        library = entry.get("scope")
        if library not in SCOPES or (scope is not None and library != scope):
            continue
        if _encoded_already(path, entry, state["encoded"].get(path)):
            continue
        change, reason = assess(path, entry)
        skipped = state["skipped"].get(path)
        if skipped and skipped.get("size") == entry.get("size"):
            reason, change = skipped.get("reason"), None
        if reason:
            counts = left.setdefault(library, {})
            counts[reason] = counts.get(reason, 0) + 1
        if change is None:
            continue
        miss = state["failed"].get(path)
        failed = miss.get("error") if miss and miss.get("size") == entry.get("size") else None
        source = (entry.get("video") or {}).get("codec")
        out.append((path, library, {**change, "size": entry.get("size") or 0, "failed": failed, "source": source}))
    out.sort(key=lambda item: (-item[2]["estimate"], item[0]))
    return out, left


def _encoded_already(path: str, entry: dict, size: int | None) -> bool:
    """Encoded by bankai, and not replaced since: by the inventory's size, or the disk's.

    The disk's too: right after the result is put in place the held library
    tree may still list the old size for a minute, and the inventory with it.
    """
    if size is None:
        return False
    if entry.get("size") == size:
        return True
    try:
        return Path(path).stat().st_size == size
    except OSError:
        return False


def work_dir(path: Path) -> Path:
    """Where a file's steps are kept: on its own drive, so the result is put in place by a rename."""
    parts = path.parts
    drive = Path(*parts[:3]) if len(parts) > 3 and parts[1] == "mnt" else Path(path.anchor)
    return drive / "bankai" / "encode" / hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


# -- encoding one file ----------------------------------------------------------


def _remember(kind: str, path: Path, scope: str, row: dict[str, Any]) -> None:
    with _LOCK:
        state = _state()
        state[kind][str(path)] = {**row, "scope": scope, "at": time.time()}
        _save_state(state)


def encode_file(path: Path, scope: str, change: dict[str, Any]) -> dict[str, Any]:
    """Encode one file's picture again and, checked, put the result in its place."""
    from bankai.processor import shrink

    before = path.stat()
    work = work_dir(path)
    output = work / "out.mkv"
    started = time.time()
    progress.clear()
    progress.update(path=str(path), scope=scope, step="encode", fraction=0.0, started=started, size=before.st_size, codec=change["codec"])

    def report(step: str, fraction: float | None) -> None:
        progress.update(step=step, fraction=fraction)

    with _LOCK:
        state = _state()
        state["current"] = {"path": str(path), "scope": scope, "work": str(work), "started": started}
        _save_state(state)
    try:
        result = shrink.shrink(path, output, work=work, quality=change["quality"], codec=change["codec"], on_progress=report)
        report("check", None)
        windows = shrink.quality_check(path, output, seconds=5.0)
        worst = min((row["ssim"] for row in windows), default=0.0)
        if worst < _SSIM_FLOOR:
            raise shrink.ShrinkError(f"the new picture differs from the original (SSIM {worst:.3f})")
        size = output.stat().st_size
        if size > before.st_size * (1 - _MIN_SAVING):
            saving = 1 - size / before.st_size
            shutil.rmtree(work, ignore_errors=True)
            _remember("skipped", path, scope, {"reason": "not worth it", "size": before.st_size, "saving": round(saving, 3)})
            log.info("[encode] %s: only %.0f%% smaller, left as it was", path.name, saving * 100)
            return {"path": str(path), "scope": scope, "skipped": f"only {saving:.0%} smaller"}
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise shrink.ShrinkError("the original changed while it was encoded")
        output.replace(path)
        # Its place among the recently added kept; a second later, so Jellyfin reads it again.
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    except shrink.ShrinkInterrupted:
        # A restart: no failure, and the steps done stay to go on from.
        for name in ("out.mkv", ".out.mkv.part"):
            (work / name).unlink(missing_ok=True)
        log.info("[encode] %s was stopped; it goes on from its last finished step", path.name)
        return {"path": str(path), "scope": scope, "interrupted": True}
    except shrink.ShrinkError as exc:
        shutil.rmtree(work, ignore_errors=True)
        _remember("failed", path, scope, {"error": str(exc), "size": before.st_size})
        log.warning("[encode] %s: %s", path.name, exc)
        return {"path": str(path), "scope": scope, "error": str(exc)}
    finally:
        progress.clear()
        with _LOCK:
            state = _state()
            state.pop("current", None)
            _save_state(state)
    shutil.rmtree(work, ignore_errors=True)
    track_cleanup.note(path, scope)
    if scope == "anime":
        from bankai.web import jellyfin

        jellyfin.queue([path])
    saved = before.st_size - size
    done = {
        "path": str(path),
        "scope": scope,
        "codec": change["codec"],
        "before": before.st_size,
        "after": size,
        "saved": saved,
        "ssim": round(worst, 4),
        "dolby_vision": result.get("dolby_vision"),
        "seconds": round(time.time() - started),
        "at": time.time(),
    }
    with _LOCK:
        state = _state()
        state["encoded"][str(path)] = size
        totals = state["done"].setdefault(scope, {"files": 0, "bytes": 0})
        totals["files"] += 1
        totals["bytes"] += max(0, saved)
        state["last"][scope] = done
        state["failed"].pop(str(path), None)
        _save_state(state)
    log.info("[encode] %s: %.1f GiB -> %.1f GiB (SSIM %.3f)", path.name, before.st_size / 2**30, size / 2**30, worst)
    return done


def encode_next() -> dict[str, Any] | None:
    """Encode the first file of the queue that can be encoded now; None when none can.

    A queued file no longer worth it (encoded meanwhile, replaced, gone) leaves
    the queue; one that waits -- still seeding, just changed, having tracks
    taken out -- keeps its place, and the next one goes first.
    """
    inventory = track_cleanup.load_inventory()
    state = _state()
    waiting: dict[str, int] = {}
    busy = track_cleanup.progress.get("path")
    for row in queue():
        path = row["path"]
        entry = inventory.get(path)
        change = assess(path, entry)[0] if entry else None
        if change is None or _encoded_already(path, entry or {}, state["encoded"].get(path)):
            _unqueue(path)
            continue
        reason = track_cleanup.held(Path(path))
        if reason is None and path == busy:
            reason = "having tracks taken out"
        if reason:
            waiting[path] = reason
            continue
        _set_waiting({})
        result = encode_file(Path(path), entry.get("scope") or row.get("scope") or "shows", change)
        if not result.get("interrupted"):
            _unqueue(path)
        return result
    _set_waiting(waiting)
    return None


def _set_waiting(waiting: dict[str, str]) -> None:
    with _LOCK:
        state = _state()
        if state.get("waiting") != waiting:
            state["waiting"] = waiting
            _save_state(state)


def recover() -> None:
    """An encode a restart cut off: its unfinished result goes; the steps done stay, to go on from."""
    with _LOCK:
        state = _state()
        current = state.pop("current", None)
        if current is None:
            return
        _save_state(state)
    work = Path(current.get("work") or "")
    if work.name:
        for name in ("out.mkv", ".out.mkv.part"):
            (work / name).unlink(missing_ok=True)
    log.info("[encode] %s was cut off; it goes on from its last finished step", Path(current["path"]).name)


def _group(path: str, scope: str) -> str:
    """The show a file is of -- its folder below the library's root; none for a film."""
    if scope == "movies":
        return ""
    for root in track_cleanup.roots(scope):
        prefix = root.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix):].split("/", 1)[0]
    return ""


def _current() -> dict[str, Any] | None:
    current = dict(progress) if progress else None
    if current and current.get("step") == "encode" and current.get("fraction"):
        elapsed = time.time() - float(current["started"])
        current["eta"] = round(elapsed / current["fraction"] - elapsed)
    return current


def overview() -> dict[str, Any]:
    """The page: the queue and the file being encoded, and per library the files worth encoding."""
    from bankai.web import library_walk

    state = _state()
    rows, left = candidates()
    queued = queue()
    position = {row["path"]: index for index, row in enumerate(queued, 1)}
    listed = {path: change for path, _library, change in rows}
    read: dict[str, int] = {}
    for entry in track_cleanup.load_inventory().values():
        if "video" in entry:
            read[entry.get("scope")] = read.get(entry.get("scope"), 0) + 1
    scopes = {}
    for scope in SCOPES:
        mine = [(path, change) for path, library, change in rows if library == scope]
        scopes[scope] = {
            # Files whose video is read, of all: the list fills as the inventory goes.
            "read": read.get(scope, 0),
            "files": len(library_walk.files(track_cleanup.roots(scope))),
            "totals": {
                "files": len(mine),
                "bytes": sum(change["size"] for _path, change in mine),
                "saving": sum(change["estimate"] for _path, change in mine),
            },
            "candidates": [
                {
                    "path": path,
                    "group": _group(path, scope),
                    "size": change["size"],
                    "mbps": change["mbps"],
                    "codec": change["codec"],
                    "source": change.get("source"),
                    "estimate": change["estimate"],
                    "queued": position.get(path),
                    "failed": change["failed"],
                }
                for path, change in sorted(mine, key=lambda item: item[0].casefold())
            ],
            "left": left.get(scope, {}),
            "done": state["done"].get(scope, {"files": 0, "bytes": 0}),
        }
    waiting = state.get("waiting") or {}
    totals = {"files": 0, "bytes": 0}
    for row in state["done"].values():
        totals["files"] += int(row.get("files") or 0)
        totals["bytes"] += int(row.get("bytes") or 0)
    lasts = [row for row in state["last"].values() if isinstance(row, dict)]
    return {
        "queue": [
            {
                "path": row["path"],
                "scope": row.get("scope"),
                "size": (listed.get(row["path"]) or {}).get("size"),
                "estimate": (listed.get(row["path"]) or {}).get("estimate"),
                "codec": (listed.get(row["path"]) or {}).get("codec"),
                "waiting": waiting.get(row["path"]) if isinstance(waiting, dict) else None,
            }
            for row in queued
        ],
        "current": _current(),
        "done": totals,
        "last": max(lasts, key=lambda row: row.get("at") or 0) if lasts else None,
        "failed": [
            {"path": path, "error": row.get("error"), "at": row.get("at")} for path, row in state["failed"].items()
        ][-20:],
        "scopes": scopes,
    }


def lane() -> dict[str, Any]:
    """The Dashboard's Encoding lane: the file being encoded and how many wait."""
    return {"current": _current(), "queued": max(0, len(queue()) - (1 if progress.get("path") else 0))}


__all__ = [
    "SCOPES",
    "assess",
    "candidates",
    "dequeue",
    "encode_file",
    "encode_next",
    "enqueue",
    "has_work",
    "lane",
    "overview",
    "picture_mbps",
    "progress",
    "queue",
    "recover",
    "work_dir",
]
