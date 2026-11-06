"""Audio every browser plays: Dolby and DTS tracks made AAC, one file at a time.

Chrome and Firefox decode none of AC3, E-AC3, DTS or TrueHD, so Jellyfin
converts the sound on every play of such a file -- a stream from the server
instead of direct play, slower to start and to seek. Nothing in the house
passes Dolby or DTS on to a receiver.

- A lossy track -- AC3, E-AC3, DTS without the lossless extension -- is
  *replaced* by AAC: as many channels, a bitrate no lower than its own (at
  least 192 kbit/s for 2.0, 384 kbit/s for 5.1), its language, name,
  default and forced flags and its place among the tracks kept.
- A lossless track -- TrueHD (Atmos too), DTS-HD MA, PCM -- is *kept*, and a
  language that has nothing else gets an AAC track made from it (5.1 at
  most), put before it and default when it was: browsers play the default
  track, and picking another makes Jellyfin remux.

The picture is copied as it is (the Encode page decides it), and so is every
other track. Read from ``track_cleanup``'s inventory; written like Tracks
writes: the new file beside the original, its tracks and lengths checked,
the original replaced only if it did not change meanwhile, its modification
time one second on. Files seeding, changed in the last hour, being encoded
or having tracks taken out wait. Off until switched on; files tried by hand
(``try_files``) are done even while it is off.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.web import track_cleanup
from bankai.web.state_lock import StateLock

log = get_logger(__name__)

# Played by Chrome, Firefox and Safari as they are.
PLAYABLE = frozenset({"aac", "mp3", "opus", "flac", "vorbis"})
LOSSY = frozenset({"ac3", "eac3", "dts"})
_LOSSLESS = frozenset({"truehd", "mlp"})
_PART = ".audio.part"
_AAC_PART = ".audio.part.mka"
_RETRY_SECONDS = 24 * 3600.0
_RECENT_KEPT = 200
# The new AAC must start where its source did and end with it.
_START_SLACK = 0.05
_END_SLACK = 0.5

# The layout each channel count is written as. AC3's "5.1(side)" left as it is
# came out of the AAC encoder as six channels of unknown layout; relabelled it
# is the same sound (each channel's level measured alike).
LAYOUTS = {1: "mono", 2: "stereo", 3: "3.0", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}

# Shared with the library worker's process (web.state_lock): its passes write these files too.
_LOCK = StateLock("browser_audio")
# The file being written now, for the Dashboard and the other passes: {path, scope, step, started}.
progress: dict[str, Any] = {}


def current_progress() -> dict[str, Any]:
    """The file being converted, wherever it runs: the library worker's heartbeat when it runs the pass (web.library_worker)."""
    from bankai.web import library_worker

    return library_worker.progress("browser_audio") if library_worker.delegated() else dict(progress)



class AudioError(RuntimeError):
    pass


# -- what a file needs ----------------------------------------------------------


def lossless(track: dict) -> bool:
    codec = str(track.get("codec") or "")
    return codec in _LOSSLESS or codec.startswith("pcm_") or (codec == "dts" and "MA" in str(track.get("profile") or ""))


def lossy(track: dict) -> bool:
    return str(track.get("codec") or "") in LOSSY and not lossless(track)


_SECONDARY = re.compile(r"comment|descri|deskript|kommentar|\bAD\b|sdh", re.IGNORECASE)


def secondary(track: dict) -> bool:
    """A commentary or audio description: no language's main sound."""
    return bool(_SECONDARY.search(str(track.get("name") or "")))


# The codec a release group wrote into a track's name: "AMZN_EAC3", "German [Dolby Digital
# Plus 7.1]", "BTM DDP5.1", "English - AC-3 DOLBY SURROUND EX 5.1". Longest first. Atmos
# too: an AAC carries none of its objects.
_CODEC_WORDS = re.compile(
    r"(?<![a-z])(?:"
    r"dts[-\s]?hd(?:\s*(?:master(?:\s*audio)?|ma|hra|high\s*res(?:olution)?(?:\s*audio)?))?"
    r"|dts[:\s-]?x|dts[-\s]?es(?:\s*(?:matrix|discrete))?|dts(?:\s*96/24)?"
    r"|dolby\s+digital\s+plus|dolby\s+digital(?:\s+(?:surround\s+)?ex)?|dolby\s+surround(?:\s+ex)?"
    r"|e-?ac-?3|ac-?3(?:\s+dolby\s+surround(?:\s+ex)?)?|dd\+|ddp|dd"
    r"|(?:dolby\s*)?atmos"
    r")(?![a-z])",
    re.IGNORECASE,
)
_KBPS = re.compile(r"\d{1,3}(?:[ .,]\d{3})*\s*kbps", re.IGNORECASE)


def aac_name(name: str | None, rate: int) -> str | None:
    """A replaced track's name with AAC for its codec: "Deutsch DD 5.1" -> "Deutsch AAC 5.1",
    "AC3 2.0 @ 224 kbps" -> "AAC 2.0 @ 224 kbps" (the AAC's own rate). No codec named: as it was."""
    if not name:
        return name

    def swap(match: re.Match) -> str:
        after = name[match.end():match.end() + 1]
        before = name[match.start() - 1:match.start()] if match.start() else ""
        # "BTM DDP5.1" -> "BTM AAC 5.1", but a release name's "BluRay.DD5.1.x264" -> "BluRay.AAC5.1.x264".
        return "AAC " if after.isdigit() and before != "." else "AAC"

    renamed = _CODEC_WORDS.sub(swap, name)
    if renamed == name:
        return name
    renamed = re.sub(r"AAC(?:\s*[\s_+/&-]\s*AAC)+", "AAC", renamed)  # "E-AC-3 Atmos", "DD+ / Atmos"
    renamed = re.sub(r"\s{2,}", " ", renamed).strip()
    return _KBPS.sub(f"{rate // 1000} kbps", renamed)


def bitrate(channels: int | None, source: int | None, *, from_lossless: bool) -> int:
    """The AAC's bits per second: never under the source's, nor under 192k for 2.0 or 384k for 5.1."""
    channels = max(1, int(channels or 2))
    if from_lossless:
        return 128_000 if channels == 1 else 256_000 if channels == 2 else 448_000
    floor = 96_000 if channels == 1 else 192_000 if channels == 2 else 384_000 if channels <= 6 else 512_000
    wanted = max(floor, -(-int(source or 0) // 32_000) * 32_000)
    # The encoder's ceiling is ~288k a channel; past 256k nothing is heard (a 1.5 Mbit DTS 2.0).
    return min(wanted, 256_000 * channels)


def out_channels(track: dict) -> int:
    channels = int(track.get("channels") or 2)
    return min(channels, 6) if lossless(track) else channels


def plan(path: str, entry: dict) -> dict[str, Any] | None:
    """Which audio tracks (by position among the audio, from 0) get replaced, and which get an AAC beside them.

    A language whose main sound -- commentaries and audio descriptions aside --
    is lossless alone gets an AAC from it (its default track, else its first).
    ``default``: a lossless default track whose language has AAC (or gets it
    from a replaced track) gives the default flag to that track -- browsers
    play the default, and a default TrueHD with an AC3 beside it played no AC3.
    None when the file needs nothing, or is no MKV (Tracks puts it into MKV).
    ``unsure``: DTS whose profile was not read yet, counted lossy for now.
    """
    if not path.casefold().endswith(".mkv"):
        return None
    audio = [track for track in entry.get("tracks") or [] if track.get("kind") == "audio"]
    replace = [index for index, track in enumerate(audio) if lossy(track)]
    add = []
    for lang in dict.fromkeys(track.get("lang") or "und" for track in audio):
        mine = [index for index, track in enumerate(audio) if (track.get("lang") or "und") == lang]
        main = [index for index in mine if not secondary(audio[index])] or mine
        if all(lossless(audio[index]) for index in main):
            # The default one when there is one: that is what plays.
            add.append(next((index for index in main if audio[index].get("default")), main[0]))
    default = None
    first = next((index for index, track in enumerate(audio) if track.get("default")), None)
    if first is not None and lossless(audio[first]) and first not in add:
        lang = audio[first].get("lang") or "und"
        default = next(
            (
                index for index, track in enumerate(audio)
                if (track.get("lang") or "und") == lang and not secondary(track) and not lossless(track)
                and (index in replace or str(track.get("codec") or "") in PLAYABLE)
            ),
            None,
        )
    if not replace and not add and default is None:
        return None
    duration = float(entry.get("duration") or 0)
    added = 0
    for index in replace:
        track = audio[index]
        new = bitrate(track.get("channels"), track.get("bit_rate"), from_lossless=False)
        added += (new - int(track.get("bit_rate") or new)) * duration / 8
    for index in add:
        added += bitrate(out_channels(audio[index]), None, from_lossless=True) * duration / 8
    return {
        "replace": replace,
        "add": sorted(add),
        "default": default,
        "unsure": any(audio[index].get("codec") == "dts" and "profile" not in audio[index] for index in replace),
        "bytes": int(added),
    }


# -- state ------------------------------------------------------------------------


def _path() -> Path:
    return bgjobs.jobs_root().parent / "browser_audio.json"


def _state() -> dict[str, Any]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("enabled", False)
    raw.setdefault("tried", [])
    raw.setdefault("done", {})
    raw.setdefault("failed", {})
    raw.setdefault("recent", [])
    return raw


def _save(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def set_enabled(on: bool) -> bool:
    with _LOCK:
        state = _state()
        state["enabled"] = bool(on)
        _save(state)
    return bool(on)


def enabled() -> bool:
    """Is there anything to do at all: switched on, or files tried by hand."""
    state = _state()
    return bool(state["enabled"] or state["tried"])


def try_files(paths: list[str]) -> dict[str, int]:
    """Files to do next, even while switched off -- a few to listen to first."""
    inventory = track_cleanup.load_inventory()
    taken = [path for path in dict.fromkeys(map(str, paths)) if path in inventory and plan(path, inventory[path])]
    with _LOCK:
        state = _state()
        state["tried"] = list(dict.fromkeys([*state["tried"], *taken]))
        for path in taken:
            state["failed"].pop(path, None)
        _save(state)
    return {"queued": len(taken), "skipped": len(paths) - len(taken)}


def candidates(scope: str | None = None) -> list[tuple[str, str, dict]]:
    """(path, library, plan) for every file that needs AAC: tried ones first, then most bytes added. Failed ones wait a day."""
    state = _state()
    tried = {path: number for number, path in enumerate(state["tried"])}
    now = time.time()
    out = []
    for path, entry in track_cleanup.load_inventory().items():
        library = entry.get("scope")
        if scope is not None and library != scope:
            continue
        change = plan(path, entry)
        if change is None:
            continue
        miss = state["failed"].get(path)
        if miss and miss.get("size") == entry.get("size") and now - float(miss.get("at") or 0) < _RETRY_SECONDS:
            continue
        out.append((path, str(library), change))
    out.sort(key=lambda item: (tried.get(item[0], len(tried)), -item[2]["bytes"], item[0]))
    return out


def preview(scope: str) -> dict[str, int]:
    rows = candidates(scope)
    return {
        "files": len(rows),
        "replace": sum(len(change["replace"]) for _p, _s, change in rows),
        "add": sum(len(change["add"]) for _p, _s, change in rows),
        "add_files": sum(1 for _p, _s, change in rows if change["add"]),
        "default": sum(1 for _p, _s, change in rows if change["default"] is not None),
        "unsure": sum(1 for _p, _s, change in rows if change["unsure"]),
        "bytes": sum(change["bytes"] for _p, _s, change in rows),
    }


# -- writing a file anew ----------------------------------------------------------


def _fail(path: Path, scope: str, error: str, *, size: int | None) -> dict[str, Any]:
    log.warning("[browser audio] %s: %s", path.name, error)
    with _LOCK:
        state = _state()
        state["failed"][str(path)] = {"error": error, "at": time.time(), "size": size, "scope": scope}
        state["tried"] = [row for row in state["tried"] if row != str(path)]
        _save(state)
    return {"path": str(path), "scope": scope, "error": error}


def _run(command: list[str], step: str) -> subprocess.CompletedProcess:
    progress["step"] = step
    return subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=6 * 3600, check=False)


def _stream_times(path: Path) -> list[dict[str, float | None]]:
    """Each audio stream's first timestamp and its own length (mkvmerge's DURATION tag), as ffprobe reads them."""
    from bankai.web.media import ffprobe_bin

    result = subprocess.run(
        [
            ffprobe_bin() or "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=start_time,duration,channels,channel_layout:stream_tags", "-of", "json", str(path),
        ],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120, check=False,
    )
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except ValueError:
        streams = []
    out = []
    for stream in streams:
        tags = {str(key).casefold(): value for key, value in (stream.get("tags") or {}).items()}
        length = stream.get("duration") or next((v for k, v in tags.items() if k == "duration" or k.startswith("duration-")), None)
        out.append({
            "start": _seconds(stream.get("start_time")),
            "length": _seconds(length),
            "channels": stream.get("channels"),
            "layout": stream.get("channel_layout"),
        })
    return out


def _seconds(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    text = str(value)
    if ":" in text:
        try:
            hours, minutes, seconds = text.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _same_codec(probed: dict, track: dict) -> bool:
    """ffprobe's "eac3", "truehd", "pcm_s24le" against mkvmerge's "E-AC-3", "TrueHD Atmos", "PCM"."""
    letters = "".join(ch for ch in str(track.get("codec") or "").casefold() if ch.isalnum())
    return str(probed.get("codec") or "?").split("_")[0] in letters


def _flags(properties: dict, new_id: int) -> list[str]:
    """The source track's language, commentary/original/visual-impaired flags, for the new one."""
    out = []
    lang = properties.get("language_ietf") or properties.get("language")
    if lang and lang != "und":
        out += ["--language", f"{new_id}:{lang}"]
    for key, option in (
        ("flag_commentary", "--commentary-flag"),
        ("flag_original", "--original-flag"),
        ("flag_visual_impaired", "--visual-impaired-flag"),
    ):
        if properties.get(key):
            out += [option, f"{new_id}:1"]
    return out


def convert_file(path: Path, scope: str) -> dict[str, Any]:
    """Write one file anew with AAC for its Dolby and DTS tracks, and put it in its place."""
    from bankai.web.media import ffmpeg_bin, mkvmerge_bin

    before = path.stat()
    found = track_cleanup.inspect_file(path)
    if found is None:
        return _fail(path, scope, "ffprobe cannot read it", size=before.st_size)
    change = plan(str(path), {"tracks": found["tracks"], "duration": found.get("duration")})
    if change is None:
        track_cleanup.note(path, scope)
        _untry(str(path))
        return {"path": str(path), "scope": scope, "skipped": "every track plays in browsers"}
    audio_probe = [track for track in found["tracks"] if track["kind"] == "audio"]
    try:
        info = track_cleanup._mkvmerge_json(path)
    except track_cleanup.CleanupError as exc:
        return _fail(path, scope, str(exc), size=before.st_size)
    tracks = info.get("tracks") or []
    audio = [track for track in tracks if track.get("type") == "audio"]
    if len(audio) != len(audio_probe) or not all(
        _same_codec(audio_probe[index], audio[index]) for index in change["replace"] + change["add"]
    ):
        # ffmpeg maps by ffprobe's order, mkvmerge by its ids: they must be the same tracks.
        seen = [str(track.get("codec")) for track in audio]
        return _fail(path, scope, f"mkvmerge sees the audio as {seen}, ffprobe as {[t['codec'] for t in audio_probe]}", size=before.st_size)
    if shutil.disk_usage(path.parent).free < before.st_size + 2**30:
        return _fail(path, scope, "not enough room on the disk for a second copy", size=before.st_size)

    # The AAC tracks, in the order they are written: (audio position, replaces it?).
    jobs = sorted([(index, True) for index in change["replace"]] + [(index, False) for index in change["add"]])
    aac = path.with_name(f".{path.stem}{_AAC_PART}")
    part = path.with_name(f".{path.name}{_PART}")
    # -copyts: each track keeps the time it starts at (a dub can start 40 ms late).
    encode = [ffmpeg_bin() or "ffmpeg", "-v", "error", "-nostdin", "-y", "-copyts", "-i", str(path)]
    for number, (index, replaces) in enumerate(jobs):
        source = audio_probe[index]
        channels = int(source.get("channels") or 2) if replaces else out_channels(source)
        rate = bitrate(channels, source.get("bit_rate"), from_lossless=not replaces)
        layout = LAYOUTS.get(channels, "5.1")
        encode += ["-map", f"0:a:{index}", f"-filter:a:{number}", f"aformat=channel_layouts={layout}", f"-c:a:{number}", "aac", f"-b:a:{number}", str(rate)]
    encode += ["-map_metadata", "-1", "-map_chapters", "-1", str(aac)]

    command = [mkvmerge_bin() or "mkvmerge", "-q", "-o", str(part)]
    replaced_ids = {audio[index]["id"] for index, replaces in jobs if replaces}
    kept = [track["id"] for track in audio if track["id"] not in replaced_ids]
    command += ["--audio-tracks", ",".join(map(str, kept))] if kept else ["--no-audio"]
    for index, replaces in jobs:
        # An AAC beside a lossless default track takes its place as the default.
        if not replaces and (audio[index].get("properties") or {}).get("default_track"):
            command += ["--default-track-flag", f"{audio[index]['id']}:0"]
    moved = change.get("default")
    if moved is not None:
        for track in audio:
            if (track.get("properties") or {}).get("default_track"):
                command += ["--default-track-flag", f"{track['id']}:0"]
        if moved not in change["replace"]:
            command += ["--default-track-flag", f"{audio[moved]['id']}:1"]
    command.append(str(path))
    for number, (index, replaces) in enumerate(jobs):
        properties = audio[index].get("properties") or {}
        source = audio_probe[index]
        rate = bitrate(int(source.get("channels") or 2) if replaces else out_channels(source), source.get("bit_rate"), from_lossless=not replaces)
        name = aac_name(properties.get("track_name"), rate) if replaces else f"AAC {'5.1' if out_channels(source) == 6 else '2.0' if out_channels(source) == 2 else out_channels(source)}"
        command += _flags(properties, number)
        if name:
            command += ["--track-name", f"{number}:{name}"]
        command += ["--default-track-flag", f"{number}:{1 if properties.get('default_track') or index == moved else 0}"]
        command += ["--forced-display-flag", f"{number}:{1 if properties.get('forced_track') else 0}"]
    if jobs:
        command.append(str(aac))
    # Every track where it was: a replacement in its source's place, an added one just before its source.
    order = []
    new_of = {index: number for number, (index, _r) in enumerate(jobs)}
    by_id = {track["id"]: position for position, track in enumerate(audio)}
    for track in tracks:
        position = by_id.get(track["id"]) if track.get("type") == "audio" else None
        if position is not None and position in new_of:
            order.append(f"1:{new_of[position]}")
            if position in change["add"]:
                order.append(f"0:{track['id']}")
        else:
            order.append(f"0:{track['id']}")
    command += ["--track-order", ",".join(order)]

    with _LOCK:
        state = _state()
        state["current"] = {"path": str(path), "parts": [str(aac), str(part)], "scope": scope, "started": time.time()}
        _save(state)
    progress.clear()
    progress.update(path=str(path), scope=scope, started=time.time(), size=before.st_size, step="encoding")
    try:
        # Only the default flag to move: nothing to encode.
        for step, run in ((("encoding", encode),) if jobs else ()) + (("writing", command),):
            result = _run(run, step)
            if result.returncode < 0:
                aac.unlink(missing_ok=True)
                part.unlink(missing_ok=True)
                log.info("[browser audio] %s was stopped; it is taken again later", path.name)
                return {"path": str(path), "scope": scope, "interrupted": True}
            # mkvmerge exits 1 for warnings, the file written.
            if result.returncode not in ((0, 1) if step == "writing" else (0,)):
                tool = "mkvmerge" if step == "writing" else "ffmpeg"
                raise AudioError(f"{tool} failed: " + (result.stdout + result.stderr).strip()[-300:])
        progress["step"] = "checking"
        _check(path, part, aac, info, jobs, audio_probe)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise AudioError("the original changed while it was being written anew")
        part.replace(path)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    except (AudioError, track_cleanup.CleanupError, subprocess.TimeoutExpired) as exc:
        return _fail(path, scope, str(exc), size=before.st_size)
    finally:
        aac.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        progress.clear()
        with _LOCK:
            state = _state()
            state.pop("current", None)
            _save(state)
    size = path.stat().st_size
    track_cleanup.note(path, scope)
    from bankai.web import jellyfin

    # Written anew, its tracks moved: Jellyfin reads them again and forgets subtitles cut by the old numbers.
    jellyfin.queue([path], reprobe=True)
    done = {
        "path": str(path),
        "scope": scope,
        "replaced": len(change["replace"]),
        "added": len(change["add"]),
        "default_moved": change.get("default") is not None,
        "grown": size - before.st_size,
        "at": time.time(),
    }
    with _LOCK:
        state = _state()
        totals = state["done"].setdefault(scope, {"files": 0, "bytes": 0})
        totals["files"] += 1
        totals["bytes"] += size - before.st_size
        state["recent"] = [*state["recent"], done][-_RECENT_KEPT:]
        state["failed"].pop(str(path), None)
        state["tried"] = [row for row in state["tried"] if row != str(path)]
        _save(state)
    log.info(
        "[browser audio] %s: %d track(s) made AAC, %d AAC added, %+.0f MiB",
        path.name, done["replaced"], done["added"], done["grown"] / 2**20,
    )
    return done


def _check(path: Path, part: Path, aac: Path, info: dict, jobs: list[tuple[int, bool]], audio_probe: list[dict]) -> None:
    """The new file holds every track it should, as long as the original, each AAC starting and ending with its source."""
    written = track_cleanup._mkvmerge_json(part)
    expected = track_cleanup._counts(info)
    expected["audio"] = expected.get("audio", 0) + sum(1 for _index, replaces in jobs if not replaces)
    if track_cleanup._counts(written) != expected:
        raise AudioError(f"the new file has {track_cleanup._counts(written)} tracks, not {expected}")
    codecs = [str(track.get("codec") or "") for track in written.get("tracks") or [] if track.get("type") == "audio"]
    if sum("AAC" in codec for codec in codecs) < len(jobs):
        raise AudioError(f"the new file's audio is {codecs}")
    old, new = track_cleanup._duration(info), track_cleanup._duration(written)
    if old and new and abs(old - new) > 1.0:
        raise AudioError(f"the new file runs {new:.1f}s, the original {old:.1f}s")
    sources, made = _stream_times(path), (_stream_times(aac) if jobs else [])
    if len(made) != len(jobs):
        raise AudioError(f"{len(made)} AAC tracks were made, not {len(jobs)}")
    for (index, replaces), new_times in zip(jobs, made):
        source = sources[index] if index < len(sources) else {}
        name = audio_probe[index].get("lang") or "und"
        channels = int(audio_probe[index].get("channels") or 2) if replaces else out_channels(audio_probe[index])
        if new_times.get("channels") != channels or new_times.get("layout") != LAYOUTS.get(channels, "5.1"):
            raise AudioError(f"the {name} AAC has {new_times.get('channels')} channels laid out as {new_times.get('layout')}")
        if source.get("start") is not None and new_times.get("start") is not None:
            if abs(float(source["start"]) - float(new_times["start"])) > _START_SLACK:
                raise AudioError(f"the {name} AAC starts at {new_times['start']:.3f}s, its source at {source['start']:.3f}s")
        if source.get("length") and new_times.get("length"):
            if abs(float(source["length"]) - float(new_times["length"])) > _END_SLACK:
                raise AudioError(f"the {name} AAC runs {new_times['length']:.1f}s, its source {source['length']:.1f}s")


def _untry(path: str) -> None:
    with _LOCK:
        state = _state()
        if path in state["tried"]:
            state["tried"].remove(path)
            _save(state)


def recover() -> None:
    """Copies a restart cut off are thrown away; their original was never touched."""
    with _LOCK:
        state = _state()
        current = state.pop("current", None)
        if current is None:
            return
        _save(state)
    for part in current.get("parts") or []:
        Path(part).unlink(missing_ok=True)
    log.info("[browser audio] removed the unfinished copy of %s", Path(current["path"]).name)


def convert_next() -> dict[str, Any] | None:
    """Do the next file: one tried by hand, or with the pass on any; None when there is none to do now."""
    from bankai.web import encode_library

    state = _state()
    tried = set(state["tried"])
    waiting: dict[str, int] = {}
    for path, scope, _change in candidates():
        if not state["enabled"] and path not in tried:
            continue
        reason = track_cleanup.held(Path(path))
        if reason is None and path == encode_library.current_progress().get("path"):
            reason = "being encoded"
        if reason is None and path == track_cleanup.current_progress().get("path"):
            reason = "having tracks taken out"
        if reason:
            waiting[reason] = waiting.get(reason, 0) + 1
            continue
        _set_waiting({})
        return convert_file(Path(path), scope)
    _set_waiting(waiting)
    return None


def _set_waiting(waiting: dict[str, int]) -> None:
    with _LOCK:
        state = _state()
        if state.get("waiting", {}) != waiting:
            state["waiting"] = waiting
            _save(state)


def files(scope: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """The files it would write anew, with what each gets: for picking a few to try."""
    inventory = track_cleanup.load_inventory()
    tried = set(_state()["tried"])
    out = []
    for path, library, change in candidates(scope)[:limit]:
        audio = [track for track in inventory[path].get("tracks") or [] if track.get("kind") == "audio"]
        rows = []
        for index, track in enumerate(audio):
            action = "replace" if index in change["replace"] else "add" if index in change["add"] else "keep"
            # Default afterwards: its own flag, unless it moves to (or from) it.
            becomes_default = index == change["default"] if change["default"] is not None else bool(track.get("default"))
            channels = int(track.get("channels") or 0) or None
            target = channels if action == "replace" else out_channels(track) if action == "add" else None
            rows.append({
                "lang": track.get("lang"),
                "name": track.get("name"),
                "codec": track.get("codec"),
                "profile": track.get("profile"),
                "channels": channels,
                "bit_rate": track.get("bit_rate"),
                "default": bool(track.get("default")),
                "becomes_default": becomes_default,
                "action": action,
                "new_name": aac_name(track.get("name"), bitrate(target, track.get("bit_rate"), from_lossless=False)) if action == "replace" else None,
                "aac_bit_rate": None if action == "keep" else bitrate(target, track.get("bit_rate"), from_lossless=action == "add"),
            })
        out.append({"path": path, "scope": library, "tried": path in tried, "bytes": change["bytes"], "unsure": change["unsure"], "tracks": rows})
    return out


def overview() -> dict[str, Any]:
    state = _state()
    return {
        "enabled": state["enabled"],
        "tried": state["tried"],
        "scopes": {scope: preview(scope) for scope in track_cleanup.SCOPES},
        "done": state["done"],
        "recent": state["recent"][-20:],
        "failed": [{"path": path, **row} for path, row in state["failed"].items()][-20:],
        "waiting": state.get("waiting") or {},
        "current": current_progress() or None,
    }


__all__ = [
    "PLAYABLE",
    "bitrate",
    "candidates",
    "convert_file",
    "convert_next",
    "enabled",
    "files",
    "lossless",
    "lossy",
    "overview",
    "plan",
    "preview",
    "recover",
    "set_enabled",
    "try_files",
]
