"""Audio and subtitle tracks nobody needs, taken out of the library's videos.

The videos carry every language their release had: a UHD disc a dozen
subtitle tracks, an anime batch Arabic, Portuguese and Russian subtitles
beside the German. Per library -- anime, shows, movies, with the roots the
Server page has -- the languages switched off are taken out of every file,
one file at a time, and a file in another container (an MP4) is put into
MKV. Nothing is encoded: mkvmerge copies the tracks kept bit for bit.

1. ``read_tracks`` -- every video's tracks, read with ffprobe (headers only),
   a slice a pass, kept in ``track_inventory.json`` by path and read again
   when a file's size or modification time changes. The page counts the
   languages from it.
2. The rules -- per library the audio and subtitle languages to take out,
   and whether to do it at all, in ``track_rules.json``. Nothing until asked.
3. ``clean_next`` -- the next file a library's rules change, written anew
   beside the original, checked, and put in its place.

A file never loses its last audio track. A file hard-linked to a torrent
still seeding is left until the torrent goes -- written anew, its old bytes
would be held twice -- and so is one changed in the last hour (just
published). The original is replaced only if it did not change while the
copy was written (an HEVC upgrade, a layout move), and it keeps its place
among the recently added: its modification time moves by one second, which
is enough for Jellyfin to read its tracks again.
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
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web.state_lock import StateLock

log = get_logger(__name__)

SCOPES = ("anime", "shows", "movies")
KINDS = ("audio", "subtitles")
# Never taken out, audio or subtitles: German and English everywhere, and
# Japanese in anime, its original language.
PROTECTED = {"anime": frozenset({"ger", "eng", "jpn"}), "shows": frozenset({"ger", "eng"}), "movies": frozenset({"ger", "eng"})}
# Files whose tracks are read per pass: ffprobe reads headers, ~0.2 s a file over 9p.
READ_BATCH = 150
# A file changed this recently may still be being published or moved.
_QUIET_SECONDS = 3600.0
# A file that failed is tried again after this, or as soon as it changes.
_RETRY_SECONDS = 24 * 3600.0
_PART = ".tracks.part"

# Shared with the library worker's process (web.state_lock): its passes write these files too.
_LOCK = StateLock("track_cleanup")
_INVENTORY: tuple[tuple[int, int] | None, dict[str, dict]] | None = None
# What clean_next is doing now, for the page.
progress: dict[str, Any] = {}


def current_progress() -> dict[str, Any]:
    """The file being written anew, wherever it runs: the library worker's heartbeat when it runs the pass (web.library_worker)."""
    from bankai.web import library_worker

    return library_worker.progress("track_clean") if library_worker.delegated() else dict(progress)


# ISO 639: two-letter and terminology codes as the bibliographic ones Matroska writes.
_ALIASES = {
    "en": "eng", "de": "ger", "deu": "ger", "ja": "jpn", "fr": "fre", "fra": "fre", "es": "spa", "it": "ita",
    "pt": "por", "ru": "rus", "ar": "ara", "zh": "chi", "zho": "chi", "ko": "kor", "pl": "pol", "tr": "tur",
    "nl": "dut", "nld": "dut", "sv": "swe", "no": "nor", "nb": "nor", "nob": "nor", "nn": "nor", "nno": "nor",
    "da": "dan", "fi": "fin", "cs": "cze", "ces": "cze", "hu": "hun", "el": "gre", "ell": "gre", "he": "heb",
    "hi": "hin", "th": "tha", "vi": "vie", "id": "ind", "ms": "may", "msa": "may", "uk": "ukr", "ro": "rum",
    "ron": "rum", "bg": "bul", "hr": "hrv", "sr": "srp", "sl": "slv", "sk": "slo", "slk": "slo", "et": "est",
    "lv": "lav", "lt": "lit", "is": "ice", "isl": "ice", "ca": "cat", "eu": "baq", "eus": "baq", "gl": "glg",
    "tl": "tgl", "ta": "tam", "te": "tel", "fa": "per", "fas": "per", "bn": "ben", "ur": "urd", "ml": "mal",
    "kn": "kan", "mr": "mar", "sq": "alb", "sqi": "alb", "mk": "mac", "mkd": "mac", "hy": "arm", "hye": "arm",
    "ka": "geo", "kat": "geo", "cy": "wel", "cym": "wel", "ga": "gle", "la": "lat", "sw": "swa", "af": "afr",
    "az": "aze", "be": "bel", "bs": "bos", "kk": "kaz", "mn": "mon", "ne": "nep", "si": "sin", "km": "khm",
    "lo": "lao", "my": "bur", "mya": "bur", "am": "amh", "zu": "zul", "xh": "xho", "gu": "guj", "pa": "pan",
}
NAMES = {
    "eng": "English", "ger": "German", "jpn": "Japanese", "fre": "French", "spa": "Spanish", "ita": "Italian",
    "por": "Portuguese", "rus": "Russian", "ara": "Arabic", "chi": "Chinese", "kor": "Korean", "pol": "Polish",
    "tur": "Turkish", "dut": "Dutch", "swe": "Swedish", "nor": "Norwegian", "dan": "Danish", "fin": "Finnish",
    "cze": "Czech", "hun": "Hungarian", "gre": "Greek", "heb": "Hebrew", "hin": "Hindi", "tha": "Thai",
    "vie": "Vietnamese", "ind": "Indonesian", "may": "Malay", "ukr": "Ukrainian", "rum": "Romanian",
    "bul": "Bulgarian", "hrv": "Croatian", "srp": "Serbian", "slv": "Slovenian", "slo": "Slovak",
    "est": "Estonian", "lav": "Latvian", "lit": "Lithuanian", "ice": "Icelandic", "cat": "Catalan",
    "baq": "Basque", "glg": "Galician", "fil": "Filipino", "tgl": "Tagalog", "tam": "Tamil", "tel": "Telugu",
    "per": "Persian", "ben": "Bengali", "urd": "Urdu", "mal": "Malayalam", "kan": "Kannada", "mar": "Marathi",
    "alb": "Albanian", "mac": "Macedonian", "arm": "Armenian", "geo": "Georgian", "wel": "Welsh", "gle": "Irish",
    "lat": "Latin", "swa": "Swahili", "afr": "Afrikaans", "aze": "Azerbaijani", "bel": "Belarusian",
    "bos": "Bosnian", "kaz": "Kazakh", "mon": "Mongolian", "nep": "Nepali", "sin": "Sinhala", "khm": "Khmer",
    "lao": "Lao", "bur": "Burmese", "amh": "Amharic", "zul": "Zulu", "xho": "Xhosa", "guj": "Gujarati",
    "pan": "Punjabi", "und": "Unknown", "mul": "Several languages", "zxx": "No language",
}


def language(raw: object) -> str:
    """A track's language as one code: "de", "deu", "ger" and "de-DE" are all "ger"; none is "und"."""
    code = str(raw or "").strip().casefold().split("-")[0]
    code = _ALIASES.get(code, code)
    return code if re.fullmatch(r"[a-z]{3}", code) else "und"


def language_name(code: str) -> str:
    return NAMES.get(code, code)


def roots(scope: str) -> list[str]:
    """A library's roots, as the Server page has them; a root listed for two belongs to the first."""
    settings = get_settings()
    lists = {
        "anime": [settings.transfer.anime_shows_dir, *settings.web.server_anime_dirs],
        "shows": [settings.transfer.shows_dir, *settings.web.server_show_dirs],
        "movies": [settings.transfer.movies_dir, *settings.web.server_movie_dirs],
    }
    claimed: set[str] = set()
    for name in SCOPES:
        own = [root for root in dict.fromkeys(str(Path(raw)) for raw in lists[name]) if root not in claimed]
        claimed.update(own)
        if name == scope:
            return own
    raise ValueError(f"no such library: {scope}")


# -- stores -------------------------------------------------------------------


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


def _stamp(name: str) -> tuple[int, int] | None:
    try:
        stat = _path(name).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def load_inventory() -> dict[str, dict]:
    """Path -> {scope, size, mtime, tracks}; held in memory while the file is unchanged."""
    global _INVENTORY
    stamp = _stamp("track_inventory.json")
    if _INVENTORY is None or _INVENTORY[0] != stamp:
        _INVENTORY = (stamp, _read("track_inventory.json"))
    return _INVENTORY[1]


def follow_renames(renamed: dict[str, str]) -> int:
    """Files moved by the anime layout keep what was read of their tracks: not read again."""
    with _LOCK:
        rows = dict(load_inventory())
        moved = [old for old in renamed if old in rows]
        for old in moved:
            rows[renamed[old]] = rows.pop(old)
        if moved:
            _save_inventory(rows)
    return len(moved)


def _save_inventory(rows: dict[str, dict]) -> None:
    global _INVENTORY
    _write("track_inventory.json", rows)
    _INVENTORY = (_stamp("track_inventory.json"), rows)


def rules() -> dict[str, dict[str, Any]]:
    """Per library: whether to clean it, and the audio and subtitle languages to take out."""
    raw = _read("track_rules.json")
    out = {}
    for scope in SCOPES:
        row = raw.get(scope) if isinstance(raw.get(scope), dict) else {}
        out[scope] = {
            "enabled": bool(row.get("enabled")),
            **{
                kind: sorted({language(code) for code in row.get(kind) or []} - PROTECTED[scope])
                for kind in KINDS
            },
        }
    return out


def set_rules(scope: str, *, enabled: bool | None = None, audio: list[str] | None = None, subtitles: list[str] | None = None) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError(f"no such library: {scope}")
    with _LOCK:
        current = rules()
        row = current[scope]
        if enabled is not None:
            row["enabled"] = bool(enabled)
        for kind, codes in (("audio", audio), ("subtitles", subtitles)):
            if codes is not None:
                # A code that is no language is left out, not read as "und";
                # a language always kept cannot be switched off.
                row[kind] = sorted(
                    {language(code) for code in codes if language(code) != "und" or str(code).strip().casefold() == "und"}
                    - PROTECTED[scope]
                )
        _write("track_rules.json", current)
    return row


def _state() -> dict[str, Any]:
    raw = _read("track_cleanup.json")
    raw.setdefault("done", {})
    raw.setdefault("last", {})
    raw.setdefault("failed", {})
    raw.setdefault("recent", [])
    return raw


# The files written anew kept for the Dashboard's Recently added ("Remuxed").
_RECENT_KEPT = 300


def remuxed() -> dict[str, dict[str, Any]]:
    """The files tracks were taken out of lately, by path: when and what (newest kept)."""
    with _LOCK:
        rows = _state()["recent"]
    return {str(row["path"]): row for row in rows if isinstance(row, dict) and row.get("path")}


def _save_state(state: dict[str, Any]) -> None:
    _write("track_cleanup.json", state)


# -- reading the tracks ---------------------------------------------------------


def inspect_file(path: Path) -> dict[str, Any] | None:
    """A file as ffprobe reads its headers: its audio and subtitle tracks, its video, its length.

    The video -- codec, size, bit depth, bitrate, HDR, Dolby Vision profile,
    interlacing -- is what the encoder (see encode_library) decides by.
    None when the file cannot be read.
    """
    from bankai.web.media import ffprobe_bin

    binary = ffprobe_bin()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [
                binary, "-v", "error",
                "-show_entries",
                "stream=index,codec_type,codec_name,profile,channels,bit_rate,width,height,pix_fmt,field_order,color_transfer"
                ":stream_tags:stream_disposition=default,forced,attached_pic"
                ":stream_side_data=side_data_type,dv_profile:format=duration,bit_rate",
                "-of", "json", str(path),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return None
    tracks = []
    video = None
    for stream in data.get("streams") or []:
        tags = {str(key).casefold(): value for key, value in (stream.get("tags") or {}).items()}
        disposition = stream.get("disposition") or {}
        if stream.get("codec_type") == "video" and video is None and not disposition.get("attached_pic"):
            pix_fmt = str(stream.get("pix_fmt") or "")
            bps = next((value for key, value in tags.items() if key == "bps" or key.startswith("bps-")), None)
            video = {
                "codec": stream.get("codec_name"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "bit_depth": 10 if re.search(r"(10|12)(le|be)?$", pix_fmt) else 8,
                "interlaced": str(stream.get("field_order") or "") in {"tt", "bb", "tb", "bt"},
                "hdr": stream.get("color_transfer") in {"smpte2084", "arib-std-b67"},
                "dv": next(
                    (
                        int(item.get("dv_profile") or 0) or None
                        for item in stream.get("side_data_list") or []
                        if item.get("side_data_type") == "DOVI configuration record"
                    ),
                    None,
                ),
                "bps": int(bps) if str(bps or "").isdigit() else None,
            }
            continue
        kind = {"audio": "audio", "subtitle": "subtitles"}.get(str(stream.get("codec_type")))
        if kind is None:
            continue
        size = next((value for key, value in tags.items() if key.startswith("number_of_bytes")), None)
        row = {
            "kind": kind,
            "lang": language(tags.get("language")),
            "name": tags.get("title") or None,
            "codec": stream.get("codec_name"),
            "default": bool(disposition.get("default")),
            "forced": bool(disposition.get("forced")),
            "bytes": int(size) if str(size or "").isdigit() else None,
        }
        if kind == "audio":
            # What browser_audio decides by: DTS-HD MA is "dts" too, told apart by its profile.
            bps = stream.get("bit_rate") or next((value for key, value in tags.items() if key == "bps" or key.startswith("bps-")), None)
            row["profile"] = stream.get("profile") or None
            row["channels"] = stream.get("channels")
            row["bit_rate"] = int(bps) if str(bps or "").isdigit() else None
        tracks.append(row)
    form = data.get("format") or {}
    try:
        duration = float(form.get("duration") or 0) or None
    except ValueError:
        duration = None
    rate = form.get("bit_rate")
    return {"tracks": tracks, "video": video, "duration": duration, "bit_rate": int(rate) if str(rate or "").isdigit() else None}


def probe(path: Path) -> list[dict] | None:
    """A file's audio and subtitle tracks, as ffprobe reads its headers; None when it cannot."""
    found = inspect_file(path)
    return None if found is None else found["tracks"]


def _entry(scope: str, size: int, mtime: float, found: dict[str, Any] | None) -> dict[str, Any]:
    entry = {
        "scope": scope,
        "size": size,
        "mtime": mtime,
        "tracks": (found or {}).get("tracks") or [],
        "video": (found or {}).get("video"),
        "duration": (found or {}).get("duration"),
        "bit_rate": (found or {}).get("bit_rate"),
    }
    if found is None:
        entry["unreadable"] = True
    return entry


def _listed() -> dict[str, tuple[str, dict]]:
    """Every video of the three libraries: path -> (library, its row from the held tree)."""
    from bankai.web import library_walk

    out: dict[str, tuple[str, dict]] = {}
    for scope in SCOPES:
        for row in library_walk.files(roots(scope)):
            out.setdefault(row["path"], (scope, row))
    return out


def read_tracks(*, limit: int = READ_BATCH) -> dict[str, int]:
    """Read the tracks of up to ``limit`` files not read yet or changed since; forget files gone."""
    listed = _listed()
    inventory = load_inventory()
    todo = sorted(
        path
        for path, (scope, row) in listed.items()
        if (entry := inventory.get(path)) is None
        or entry.get("size") != row["size"]
        or entry.get("mtime") != row["mtime"]
        or entry.get("scope") != scope
        # Read before the video was kept: read again for it.
        or "video" not in entry
        # Audio read before its profile, channels and bitrate were kept (browser_audio: DTS-HD MA
        # is "dts" too, and an AAC's bitrate follows its source's).
        or any(track.get("kind") == "audio" and "channels" not in track for track in entry.get("tracks") or [])
    )
    read: dict[str, dict] = {}
    from bankai.web import stopping

    for path in todo[:limit]:
        if stopping.requested():
            break
        scope, row = listed[path]
        read[path] = _entry(scope, row["size"], row["mtime"], inspect_file(Path(path)))
    with _LOCK:
        inventory = dict(load_inventory())
        gone = [path for path in inventory if path not in listed]
        for path in gone:
            del inventory[path]
        inventory.update(read)
        if read or gone:
            _save_inventory(inventory)
    return {"read": len(read), "remaining": max(0, len(todo) - len(read)), "files": len(listed)}


def note(path: Path, scope: str) -> None:
    """Read one file's tracks again now, after it was written anew."""
    try:
        stat = path.stat()
    except OSError:
        return
    entry = _entry(scope, stat.st_size, stat.st_mtime, inspect_file(path))
    with _LOCK:
        inventory = dict(load_inventory())
        inventory[str(path)] = entry
        _save_inventory(inventory)


# -- what the rules change ------------------------------------------------------


# -- tracks picked by hand to be taken out (Tracks -> Unknown -> Remove) -------------


def removals() -> dict[str, dict[str, list[int]]]:
    """Path -> {"audio": [...], "subtitles": [...]}: tracks by their place among the file's of that kind."""
    rows = _read("track_removals.json")
    return {path: row for path, row in rows.items() if isinstance(row, dict)}


def remove_tracks(items: list[dict[str, Any]]) -> dict[str, int]:
    """Queue tracks to be taken out: ``items`` {path, kind, position}. The cleanup writes the file anew
    without them -- one at a time, checked as always -- even in a library whose cleanup is off."""
    inventory = load_inventory()
    with _LOCK:
        rows = removals()
        queued = 0
        for item in items:
            path, kind = str(item.get("path") or ""), str(item.get("kind") or "")
            if path not in inventory or kind not in KINDS or not str(item.get("position") or "").isdigit():
                raise ValueError("each track is a library file, a kind (audio, subtitles) and a position")
            row = rows.setdefault(path, {"audio": [], "subtitles": []})
            position = int(item["position"])
            if position not in row[kind]:
                row[kind] = sorted([*row[kind], position])
                queued += 1
        _write("track_removals.json", rows)
        # Asked for by hand: a file that failed before is tried at once, not after its day's wait.
        state = _state()
        if any(state["failed"].pop(str(item.get("path")), None) for item in items):
            _save_state(state)
    return {"queued": queued, "files": len({str(item.get("path")) for item in items})}


def _forget_removal(path: str) -> None:
    with _LOCK:
        rows = removals()
        if rows.pop(path, None) is not None:
            _write("track_removals.json", rows)


def _picked(path: str, kind: str, picked: dict[str, dict[str, list[int]]] | None = None) -> set[int]:
    return set(((removals() if picked is None else picked).get(path) or {}).get(kind) or [])


def plan(path: str, entry: dict, rule: dict, picked: dict[str, dict[str, list[int]]] | None = None) -> dict[str, Any] | None:
    """What a library's rules take out of one file (and the tracks picked by hand), and whether it
    changes container; None for nothing. ``picked``: ``removals()``, read once by a caller going
    through every file (read per file, it was 13,000 reads of the same JSON for one page)."""
    tracks = entry.get("tracks") or []
    audio = [track for track in tracks if track.get("kind") == "audio"]
    subtitles = [track for track in tracks if track.get("kind") == "subtitles"]
    picked_audio, picked_subs = _picked(path, "audio", picked), _picked(path, "subtitles", picked)
    drop_audio = [track for index, track in enumerate(audio, 1) if track.get("lang") in rule["audio"] or index in picked_audio]
    if len(drop_audio) == len(audio):
        drop_audio = []  # never the last audio track
    drop_subs = [track for index, track in enumerate(subtitles, 1) if track.get("lang") in rule["subtitles"] or index in picked_subs]
    convert = Path(path).suffix.casefold() != ".mkv"
    if entry.get("unreadable") or not (drop_audio or drop_subs or convert):
        return None
    return {
        "audio": len(drop_audio),
        "subtitles": len(drop_subs),
        "convert": convert,
        "bytes": sum(track.get("bytes") or 0 for track in drop_audio + drop_subs),
    }


def languages(scope: str) -> dict[str, list[dict[str, Any]]]:
    """Each audio and subtitle language in a library: in how many files, how many tracks, how much."""
    found: dict[str, dict[str, dict]] = {kind: {} for kind in KINDS}
    for entry in load_inventory().values():
        if entry.get("scope") != scope:
            continue
        seen: set[tuple[str, str]] = set()
        for track in entry.get("tracks") or []:
            kind, code = track.get("kind"), track.get("lang") or "und"
            if kind not in found:
                continue
            row = found[kind].setdefault(code, {"code": code, "name": language_name(code), "files": 0, "tracks": 0, "bytes": 0})
            row["tracks"] += 1
            row["bytes"] += track.get("bytes") or 0
            if (kind, code) not in seen:
                seen.add((kind, code))
                row["files"] += 1
    return {kind: sorted(rows.values(), key=lambda row: (-row["files"], row["name"])) for kind, rows in found.items()}


# -- tracks that name no language, told by hand ------------------------------------
#
# A track tagged "und" (or nothing) is "Unknown" in the language tables, in Jellyfin and ZonTV
# alike. The Tracks page lists them by title, with each track's own name -- what the players show
# too -- and the language picked for one is written into the file's header with mkvpropedit:
# no copy, seconds even for 50 GB.


def _title(path: str, scope: str) -> tuple[str, str]:
    """A file's title (its show's or film's folder, else its name) and its path below the root."""
    for root in roots(scope):
        try:
            relative = Path(path).relative_to(root)
        except ValueError:
            continue
        parts = relative.parts
        return (parts[0] if len(parts) > 1 else Path(path).stem), str(relative)
    return Path(path).parent.name, Path(path).name


def _unknown(entry: dict, kind: str, code: str = "und") -> list[dict[str, Any]]:
    """A file's tracks of this kind in one language (by default: naming none), with their place among
    the kind's tracks."""
    out = []
    position = 0
    for track in entry.get("tracks") or []:
        if track.get("kind") != kind:
            continue
        position += 1
        if (track.get("lang") or "und") == code:
            out.append({**track, "position": position})
    return out


def unknown_titles(scope: str, kind: str, code: str = "und") -> list[dict[str, Any]]:
    """The titles of a library with tracks of this kind in this language (by default: naming none):
    how many files and tracks. Any language opens this way on the Tracks page."""
    found: dict[str, dict[str, Any]] = {}
    for path, entry in load_inventory().items():
        if entry.get("scope") != scope:
            continue
        tracks = _unknown(entry, kind, code)
        if not tracks:
            continue
        title, _relative = _title(path, scope)
        row = found.setdefault(title, {"title": title, "files": 0, "tracks": 0, "names": set()})
        row["files"] += 1
        row["tracks"] += len(tracks)
        row["names"].update(str(track.get("name") or "") for track in tracks)
    return sorted(
        ({**row, "names": sorted(name for name in row["names"] if name)[:6]} for row in found.values()),
        key=lambda row: (-row["files"], row["title"].casefold()),
    )


def unknown_tracks(scope: str, kind: str, title: str, code: str = "und") -> list[dict[str, Any]]:
    """One title's files with tracks of this kind in this language (by default: naming none), each
    such track listed."""
    files = []
    for path, entry in load_inventory().items():
        if entry.get("scope") != scope:
            continue
        name, relative = _title(path, scope)
        tracks = _unknown(entry, kind, code)
        if name != title or not tracks:
            continue
        files.append({
            "path": path,
            "relative": relative,
            "mkv": path.casefold().endswith(".mkv"),
            "tracks": [
                {key: track.get(key) for key in ("position", "name", "codec", "default", "forced", "bytes")}
                for track in tracks
            ],
        })
    return sorted(files, key=lambda row: row["relative"].casefold())


# A language being written into many files in the background: {done, total}.
progress_language: dict[str, int] = {}


def set_language(items: list[dict[str, Any]], code: str) -> dict[str, Any]:
    """Write a language into tracks: ``items`` are {path, kind, position} (the n-th track of the kind).

    MKV only (mkvpropedit edits the header in place); a file still seeding in qBittorrent waits, as
    the cleanup has it -- an edit would break the torrent's pieces. The mtime is the old one + 1 s:
    Jellyfin reads the file again, recency order stays.
    """
    from bankai.web import jellyfin

    lang = language(code)
    if lang == "und":
        raise ValueError("pick a language (a code such as ger, eng, jpn)")
    by_path: dict[str, list[tuple[str, int]]] = {}
    for item in items:
        kind = str(item.get("kind") or "")
        if kind not in KINDS or not str(item.get("position") or "").isdigit():
            raise ValueError("each track is a kind (audio, subtitles) and a position")
        by_path.setdefault(str(item.get("path") or ""), []).append((kind, int(item["position"])))
    binary = shutil.which("mkvpropedit")
    if binary is None:
        raise RuntimeError("mkvpropedit is not installed")
    inventory = load_inventory()
    done, failed = [], []
    written: dict[str, dict[str, Any]] = {}
    for path, tracks in by_path.items():
        file = Path(path)
        entry = inventory.get(path)
        if entry is None or not file.is_file():
            failed.append({"path": path, "error": "not in the library any more"})
            continue
        if file.suffix.casefold() != ".mkv":
            failed.append({"path": path, "error": "not an MKV: the cleanup puts it into one first"})
            continue
        before = file.stat()
        if before.st_nlink > 1:
            failed.append({"path": path, "error": "still seeding in qBittorrent"})
            continue
        args = [binary, path]
        for kind, position in tracks:
            args += ["--edit", f"track:{'a' if kind == 'audio' else 's'}{position}", "--set", f"language={lang}"]
        try:
            result = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            failed.append({"path": path, "error": str(exc)})
            continue
        if result.returncode not in (0, 1):  # 1: done with warnings
            failed.append({"path": path, "error": (result.stdout or result.stderr).strip()[-300:]})
            continue
        os.utime(file, (before.st_atime, before.st_mtime + 1))
        after = file.stat()
        # What was written is known: the record follows without reading the file again (500
        # Naruto Shippuden episodes took ten minutes that way, the inventory saved after each).
        tracks_now = [dict(track) for track in entry.get("tracks") or []]
        places = {kind: 0 for kind in KINDS}
        chosen = set(tracks)
        for track in tracks_now:
            kind = track.get("kind")
            if kind in places:
                places[kind] += 1
                if (kind, places[kind]) in chosen:
                    track["lang"] = lang
        written[path] = {**entry, "tracks": tracks_now, "size": after.st_size, "mtime": after.st_mtime}
        done.append(path)
        progress_language.update(done=len(done), total=len(by_path))
    if written:
        with _LOCK:
            rows = dict(load_inventory())
            rows.update(written)
            _save_inventory(rows)
        jellyfin.queue([Path(path) for path in written], reprobe=True)
    progress_language.clear()
    log.info("[tracks] %s written into %d track(s) of %d file(s)", lang, sum(len(by_path[p]) for p in done), len(done))
    return {"language": lang, "name": language_name(lang), "files": len(done), "failed": failed}


def held(path: Path) -> str | None:
    """Why a file waits for now, if it does."""
    try:
        stat = path.stat()
    except OSError:
        return "gone"
    if stat.st_nlink > 1:
        return "still seeding"  # a second name: the torrent's
    if time.time() - stat.st_mtime < _QUIET_SECONDS:
        return "changed in the last hour"
    return None


def candidates(scope: str | None = None) -> list[tuple[str, str, dict]]:
    """The files the rules change, most space first: (path, library, plan). Failed ones wait a day."""
    every = rules()
    failed = _state()["failed"]
    picked = removals()
    now = time.time()
    out = []
    for path, entry in load_inventory().items():
        library = entry.get("scope")
        if library not in every or (scope is not None and library != scope):
            continue
        change = plan(path, entry, every[library], picked)
        if change is None:
            continue
        miss = failed.get(path)
        if miss and miss.get("size") == entry.get("size") and now - float(miss.get("at") or 0) < _RETRY_SECONDS:
            continue
        out.append((path, library, change))
    out.sort(key=lambda item: (-item[2]["bytes"], item[0]))
    return out


def preview(scope: str) -> dict[str, int]:
    """What the library's rules would change: files, tracks, bytes (of the tracks that say their size)."""
    rows = candidates(scope)
    return {
        "files": len(rows),
        "tracks": sum(change["audio"] + change["subtitles"] for _path, _scope, change in rows),
        "bytes": sum(change["bytes"] for _path, _scope, change in rows),
        "convert": sum(1 for _path, _scope, change in rows if change["convert"]),
    }


# -- writing a file anew --------------------------------------------------------


class CleanupError(RuntimeError):
    pass


def _mkvmerge_json(path: Path) -> dict:
    from bankai.web.media import mkvmerge_bin

    binary = mkvmerge_bin()
    if binary is None:
        raise CleanupError("mkvmerge is not installed")
    result = subprocess.run(
        [binary, "-J", str(path)], capture_output=True, encoding="utf-8", errors="replace", timeout=300, check=False
    )
    if result.returncode != 0:
        raise CleanupError(f"mkvmerge cannot read {path.name}: {result.stdout.strip()[-300:]}")
    return json.loads(result.stdout or "{}")


def _lang(track: dict) -> str:
    properties = track.get("properties") or {}
    return language(properties.get("language") or properties.get("language_ietf"))


def _counts(info: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for track in info.get("tracks") or []:
        counts[track.get("type")] = counts.get(track.get("type"), 0) + 1
    return counts


def _duration(info: dict) -> float | None:
    raw = (info.get("container") or {}).get("properties", {}).get("duration")
    return int(raw) / 1e9 if raw else None


def _seconds(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _picture_end(path: Path, start: float) -> float | None:
    """Where the picture ends, in seconds: its frames read from ``start`` to the end."""
    from bankai.web.media import ffprobe_bin

    command = [
        ffprobe_bin() or "ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", f"{start:.3f}%",
        "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    ends = []
    for line in result.stdout.splitlines():
        pts, _, duration = line.partition(",")
        at = _seconds(pts)
        if at is not None:
            ends.append(at + (_seconds(duration) or 0.0))
    return max(ends, default=None)


def _same_picture(original: Path, new: Path, near: float) -> bool:
    """Whether both pictures end alike. A track taken out can have run past the
    picture -- a subtitle whose last line comes late -- and the container gets
    shorter with it; the picture must not."""
    start = max(0.0, near - 30.0)
    old, new_end = _picture_end(original, start), _picture_end(new, start)
    return old is not None and new_end is not None and abs(old - new_end) <= 0.5


def _fail(path: Path, scope: str, error: str, *, size: int | None) -> dict[str, Any]:
    log.warning("[tracks] %s: %s", path.name, error)
    with _LOCK:
        state = _state()
        state["failed"][str(path)] = {"error": error, "at": time.time(), "size": size, "scope": scope}
        _save_state(state)
    return {"path": str(path), "scope": scope, "error": error}


def clean_file(path: Path, scope: str) -> dict[str, Any]:
    """Write one file anew without the tracks its library's rules take out, MKV, and put it in its place."""
    from bankai.web.media import mkvmerge_bin

    rule = rules()[scope]
    before = path.stat()
    info = _mkvmerge_json(path)
    tracks = info.get("tracks") or []
    audio = [track for track in tracks if track.get("type") == "audio"]
    subs = [track for track in tracks if track.get("type") == "subtitles"]
    # The library's languages switched off when its cleanup is on; tracks picked by hand always.
    switched_off = rule if rule["enabled"] else {"audio": [], "subtitles": []}
    picked_audio, picked_subs = _picked(str(path), "audio"), _picked(str(path), "subtitles")
    drop_audio = [track["id"] for index, track in enumerate(audio, 1) if _lang(track) in switched_off["audio"] or index in picked_audio]
    if len(drop_audio) == len(audio):
        drop_audio = []
    drop_subs = [track["id"] for index, track in enumerate(subs, 1) if _lang(track) in switched_off["subtitles"] or index in picked_subs]
    convert = path.suffix.casefold() != ".mkv"
    if not (drop_audio or drop_subs or convert):
        note(path, scope)
        _forget_removal(str(path))
        return {"path": str(path), "scope": scope, "skipped": "nothing to take out"}
    target = path.with_suffix(".mkv") if convert else path
    if convert:
        if target.exists():
            return _fail(path, scope, f"{target.name} is already there", size=before.st_size)
        # mkvmerge leaves out what it cannot carry: every track ffprobe sees must be one it sees.
        seen = probe(path) or []
        if len(seen) > len(audio) + len(subs):
            return _fail(path, scope, "it has tracks mkvmerge cannot carry into MKV", size=before.st_size)
    if shutil.disk_usage(path.parent).free < before.st_size + 2**30:
        return _fail(path, scope, "not enough room on the disk for a second copy", size=before.st_size)
    keep_audio = [track["id"] for track in audio if track["id"] not in drop_audio]
    keep_subs = [track["id"] for track in subs if track["id"] not in drop_subs]
    part = target.with_name(f".{target.name}{_PART}")
    command = [mkvmerge_bin() or "mkvmerge", "-q", "-o", str(part)]
    if drop_audio:
        command += ["--audio-tracks", ",".join(str(track_id) for track_id in keep_audio)]
    if drop_subs:
        command += ["--subtitle-tracks", ",".join(str(track_id) for track_id in keep_subs)] if keep_subs else ["--no-subtitles"]
    defaults = {track["id"] for track in audio if (track.get("properties") or {}).get("default_track")}
    if defaults & set(drop_audio) and not defaults & set(keep_audio):
        # The default audio went: the first one left plays instead.
        command += ["--default-track-flag", f"{keep_audio[0]}:1"]
    command.append(str(path))
    with _LOCK:
        state = _state()
        state["current"] = {"path": str(path), "part": str(part), "scope": scope, "started": time.time()}
        _save_state(state)
    progress.clear()
    progress.update(path=str(path), scope=scope, started=time.time(), size=before.st_size)
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=4 * 3600, check=False)
        if result.returncode < 0:
            # Stopped from outside (a restart): no failure; the file is taken again.
            part.unlink(missing_ok=True)
            log.info("[tracks] %s was stopped; it is taken again later", path.name)
            return {"path": str(path), "scope": scope, "interrupted": True}
        # mkvmerge exits 1 for warnings, the file written.
        if result.returncode not in (0, 1) or not part.is_file():
            raise CleanupError("mkvmerge failed: " + (result.stdout + result.stderr).strip()[-300:])
        written = _mkvmerge_json(part)
        expected = _counts(info)
        expected["audio"] = len(keep_audio)
        expected["subtitles"] = len(keep_subs)
        expected = {kind: count for kind, count in expected.items() if count}
        if _counts(written) != expected:
            raise CleanupError(f"the new file has {_counts(written)} tracks, not {expected}")
        old, new = _duration(info), _duration(written)
        if old and new and abs(old - new) > 1.0 and not _same_picture(path, part, min(old, new)):
            raise CleanupError(f"the new file runs {new:.1f}s, the original {old:.1f}s")
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise CleanupError("the original changed while it was being written anew")
        part.replace(target)
        # Its place among the recently added kept; a second later, so Jellyfin reads its tracks again.
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        if convert:
            path.unlink(missing_ok=True)
            _renamed(path, target, scope)
    except CleanupError as exc:
        part.unlink(missing_ok=True)
        return _fail(path, scope, str(exc), size=before.st_size)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        progress.clear()
        with _LOCK:
            state = _state()
            state.pop("current", None)
            _save_state(state)
    size = target.stat().st_size
    note(target, scope)
    from bankai.web import jellyfin

    # Written anew: Jellyfin reads its tracks again, in every library.
    jellyfin.queue([target], reprobe=True)
    saved = before.st_size - size
    done = {
        "path": str(target),
        "scope": scope,
        "audio": len(drop_audio),
        "subtitles": len(drop_subs),
        "converted": convert,
        "saved": saved,
        "at": time.time(),
    }
    with _LOCK:
        state = _state()
        totals = state["done"].setdefault(scope, {"files": 0, "bytes": 0})
        totals["files"] += 1
        totals["bytes"] += max(0, saved)
        state["last"][scope] = done
        state["recent"] = [*state["recent"], done][-_RECENT_KEPT:]
        state["failed"].pop(str(path), None)
        _save_state(state)
    _forget_removal(str(path))
    log.info(
        "[tracks] %s: %d audio, %d subtitle track(s) taken out%s, %.0f MiB freed",
        target.name, len(drop_audio), len(drop_subs), ", now MKV" if convert else "", saved / 2**20,
    )
    return done


def _renamed(old: Path, new: Path, scope: str) -> None:
    """A file put into MKV has a new name: the anime file index follows it, and the inventory."""
    with _LOCK:
        inventory = dict(load_inventory())
        inventory.pop(str(old), None)
        _save_inventory(inventory)
    if scope != "anime":
        return
    from bankai.web import anime_files

    rel_old, rel_new = anime_files.key(old), anime_files.key(new)
    if rel_old and rel_new:
        with anime_files.editing() as rows:
            if rel_old in rows:
                rows[rel_new] = rows.pop(rel_old)


def recover() -> None:
    """A copy cut off by a restart is thrown away; its original was never touched."""
    with _LOCK:
        state = _state()
        current = state.pop("current", None)
        if current is None:
            return
        _save_state(state)
    if current.get("part"):
        Path(current["part"]).unlink(missing_ok=True)
        log.info("[tracks] removed the unfinished copy of %s", Path(current["path"]).name)


def clean_next() -> dict[str, Any] | None:
    """Clean the next file the enabled libraries' rules change; None when there is none to do now.

    Files that wait -- still seeding, just changed -- are counted per library
    for the page when nothing could be done.
    """
    from bankai.web import browser_audio, encode_library

    every = rules()
    waiting: dict[str, dict[str, int]] = {}
    busy = encode_library.current_progress().get("path")
    picked = removals()
    for path, scope, _change in candidates():
        if not every[scope]["enabled"] and path not in picked:
            continue
        reason = held(Path(path))
        if reason is None and path == busy:
            reason = "being encoded"
        if reason is None and path == browser_audio.current_progress().get("path"):
            reason = "getting browser audio"
        if reason:
            counts = waiting.setdefault(scope, {})
            counts[reason] = counts.get(reason, 0) + 1
            continue
        _set_waiting({})
        return clean_file(Path(path), scope)
    _set_waiting(waiting)
    return None


def _set_waiting(waiting: dict[str, dict[str, int]]) -> None:
    with _LOCK:
        state = _state()
        if state.get("waiting", {}) != waiting:
            state["waiting"] = waiting
            _save_state(state)


def enabled() -> bool:
    """Is there cleaning to do at all: a library's cleanup on, or tracks picked by hand to take out."""
    return any(row["enabled"] for row in rules().values()) or bool(removals())


def overview() -> dict[str, Any]:
    """The page: each library's languages, rules, what they would change and what was done."""
    state = _state()
    inventory = load_inventory()
    listed_by_scope: dict[str, int] = {}
    for entry in inventory.values():
        listed_by_scope[entry.get("scope")] = listed_by_scope.get(entry.get("scope"), 0) + 1
    every = rules()
    current = current_progress() or None
    from bankai.web import library_walk

    out = {}
    for scope in SCOPES:
        out[scope] = {
            "rules": every[scope],
            "protected": sorted(PROTECTED[scope]),
            "languages": languages(scope),
            "read": listed_by_scope.get(scope, 0),
            # From the held tree, in memory: how many there are to read.
            "files": len(library_walk.files(roots(scope))),
            "preview": preview(scope),
            # Why files the rules change wait, as the last pass that found nothing to do saw it.
            "waiting": (state.get("waiting") or {}).get(scope) or {},
            "done": state["done"].get(scope, {"files": 0, "bytes": 0}),
            "last": state["last"].get(scope),
            "failed": [
                {"path": path, "error": row.get("error"), "at": row.get("at")}
                for path, row in state["failed"].items()
                if row.get("scope") == scope
            ][-20:],
            "current": current if current and current.get("scope") == scope else None,
        }
    return {"scopes": out, "names": NAMES}


__all__ = [
    "KINDS",
    "NAMES",
    "PROTECTED",
    "SCOPES",
    "candidates",
    "clean_file",
    "clean_next",
    "enabled",
    "held",
    "inspect_file",
    "language",
    "languages",
    "load_inventory",
    "note",
    "overview",
    "plan",
    "preview",
    "probe",
    "read_tracks",
    "recover",
    "roots",
    "rules",
    "set_rules",
]
