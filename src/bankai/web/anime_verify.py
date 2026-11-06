"""Whether each anime episode on disk is the episode the library says it is.

Shoko asks AniDB which release a file's hash is; that needs an AniDB login, and a
file Tracks or Encode wrote anew has a hash no release has. This looks at the
episode itself instead, three ways:

- **Picture.** TMDB keeps stills of most episodes, several each. A file's keyframes
  (read once, end to end, scaled to 32x18) are compared with the stills of every
  episode of its show: the right episode's stills are frames of the file, near
  enough exactly (the second-best still of the right episode scores 0.89-1.0, of a
  wrong one at most 0.75). Which TMDB episode a file should be comes from air
  dates -- the AniList entry's start against TMDB's episodes -- since TMDB often
  keeps a show as one long season (Re:Zero's 85 episodes) where AniList has five
  entries, and Fribb's list then names season 1 for all of them.
- **Length.** The file's duration against TMDB's runtime of that episode (else
  AniList's minutes per episode): a recap, an OVA or a double-length premiere
  filed as a plain episode stands out.
- **Count.** Two files on one episode, an episode past the entry's end, two files
  whose pictures are the same episode.

Nothing is moved or renamed here: the library shows each file's verdict, and a
wrong one offers to make it the episode it shows (``/api/anime/library/assign``)
or, when the library has that one already, to delete the copy.

Kept in ``anime_verify.json`` by the file's path below the anime root, with its
size and mtime: a file changed since is checked again. TMDB's episodes and still
lists live in ``anime_metadata/tmdb_episodes.json``, the stills scaled down in
``anime_metadata/tmdb_stills/<show>.json``.
"""

from __future__ import annotations

import base64
import heapq
import json
import math
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

W, H = 32, 18
SIZE = W * H * 3
# A file's episode is the one whose stills it holds: the episode's second-best
# still at least this alike (one still alone, a little more), and a wrong
# episode's that much above the expected one's.
MATCH = 0.86
MARGIN = 0.08
# Called wrong only when nothing else explains it (Nov 2026: of 1066 files, every "wrong" by MATCH and
# MARGIN alone was right -- a next-episode preview or a recap at the start (AoT S03E19 showed S03E20 at
# 0.997), a recap special holding the season's footage (Bakemonogatari, BLUE LOCK), a flashback episode
# (Black Clover 121 -> 144), or stills TMDB has few good ones of): another episode's stills seen
# clearly, its own hardly at all, neither a special, and further off than a preview or a recap reaches.
WRONG_MATCH = 0.9
WRONG_OWN = 0.6
NEIGHBOURS = 2
# Per episode, TMDB's first stills (best voted); more add little and cost a read each.
STILLS_PER_EPISODE = 6
# A show longer than this is compared around the expected episode only.
WHOLE_SHOW = 400
WINDOW = 80
# Length: off by more than this share and more than 3 minutes.
SHORT, LONG = 0.75, 1.35
_TTL = 30 * 86400
_LOCK = threading.RLock()

# What the pass is at, for the Dashboard.
progress: dict[str, Any] = {}


def _state_dir() -> Path:
    return bgjobs.jobs_root().parent


def _store_path() -> Path:
    return _state_dir() / "anime_verify.json"


def _episodes_path() -> Path:
    return _state_dir() / "anime_metadata" / "tmdb_episodes.json"


def _stills_path(tv: int) -> Path:
    return _state_dir() / "anime_metadata" / "tmdb_stills" / f"{int(tv)}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


# -- The store ----------------------------------------------------------------------


def load() -> dict[str, Any]:
    """{"files": {rel: result}, "first": [rel, ...], "confirmed": {rel: {size, mtime, at}}} -- ``first``
    asked for by hand, checked before the rest; ``confirmed`` the right episode by the user's word,
    for as long as the file stays as it was (a check of their own never overrides it)."""
    with _LOCK:
        data = _read_json(_store_path(), {})
    return {
        "files": dict(data.get("files") or {}),
        "first": list(data.get("first") or []),
        "confirmed": dict(data.get("confirmed") or {}),
    }


def _save(data: dict[str, Any]) -> None:
    with _LOCK:
        _write_json(_store_path(), data)


_RESULTS: tuple[tuple[int, int], dict[str, Any]] | None = None


def _cached() -> dict[str, Any]:
    """The store, read again only when it changed (the library reads it per show)."""
    global _RESULTS
    try:
        stat = _store_path().stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {"files": {}, "first": [], "confirmed": {}}
    if _RESULTS is None or _RESULTS[0] != stamp:
        _RESULTS = (stamp, load())
    return _RESULTS[1]


def results() -> dict[str, dict]:
    return _cached()["files"]


def confirmations() -> dict[str, dict]:
    return _cached()["confirmed"]


def confirm(rels: list[str], *, right: bool = True) -> int:
    """The user looked: these files are the episodes they are filed as (``right``), or that word taken back."""
    from bankai.web import anime_files

    changed = 0
    with _LOCK:
        data = load()
        for rel in dict.fromkeys(rels):
            if not right:
                changed += data["confirmed"].pop(rel, None) is not None
                continue
            try:
                stat = (anime_files.anime_root() / rel).stat()
            except OSError:
                continue
            data["confirmed"][rel] = {"size": stat.st_size, "mtime": stat.st_mtime, "at": time.time()}
            changed += 1
        if changed:
            _save(data)
    return changed


def ask_first(rels: list[str]) -> int:
    """These files checked next, whatever their results say (a show's "Check again")."""
    with _LOCK:
        data = load()
        first = [rel for rel in data["first"] if rel not in rels]
        data["first"] = list(dict.fromkeys(rels)) + first
        _save(data)
    return len(rels)


def follow_renames(renamed: dict[str, str]) -> None:
    """Results carried along when the layout renames files (keys below the anime root)."""
    if not renamed:
        return
    with _LOCK:
        data = load()
        moved = False
        for old, new in renamed.items():
            for kept in (data["files"], data["confirmed"]):
                if old in kept:
                    kept[new] = kept.pop(old)
                    moved = True
        if moved:
            _save(data)


# -- TMDB ---------------------------------------------------------------------------


def _client() -> httpx.Client:
    from bankai.metadata import tmdb

    headers, _ = tmdb._auth()
    return httpx.Client(timeout=30, headers=headers, follow_redirects=True)


def _params() -> dict[str, str]:
    from bankai.metadata import tmdb

    return tmdb._auth()[1]


def show_episodes(tv: int, *, http: httpx.Client | None = None) -> list[dict]:
    """A TMDB show's episodes, every season, in order: {season, episode, name, air_date, runtime, stills?}.

    Kept a month; ``stills`` (the still paths) is filled episode by episode as checks need them."""
    from bankai.metadata import tmdb

    with _LOCK:
        kept = _read_json(_episodes_path(), {})
    row = kept.get(f"tv:{tv}")
    if row and time.time() - float(row.get("at") or 0) < _TTL:
        return row["episodes"]
    if not tmdb.configured():
        return row["episodes"] if row else []
    own = http is None
    http = http or _client()
    try:
        details = http.get(f"{tmdb._API}/tv/{tv}", params=_params())
        if details.status_code == 404:
            episodes: list[dict] = []
        else:
            details.raise_for_status()
            old = {(e["season"], e["episode"]): e for e in (row or {}).get("episodes") or []}
            episodes = []
            for season in details.json().get("seasons") or []:
                number = season.get("season_number")
                if not isinstance(number, int) or not season.get("episode_count"):
                    continue
                answer = http.get(f"{tmdb._API}/tv/{tv}/season/{number}", params=_params())
                if answer.status_code == 404:
                    continue
                answer.raise_for_status()
                for item in answer.json().get("episodes") or []:
                    key = (number, int(item.get("episode_number") or 0))
                    episode = {
                        "season": number,
                        "episode": key[1],
                        "name": item.get("name") or "",
                        "air_date": item.get("air_date") or None,
                        "runtime": item.get("runtime") or None,
                        "still": item.get("still_path") or None,
                    }
                    if "stills" in old.get(key, {}):
                        episode["stills"] = old[key]["stills"]
                    episodes.append(episode)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("TMDB episodes of tv:%s: %s", tv, exc)
        return row["episodes"] if row else []
    finally:
        if own:
            http.close()
    episodes.sort(key=lambda e: (e["season"] == 0, e["season"], e["episode"]))
    with _LOCK:
        kept = _read_json(_episodes_path(), {})
        kept[f"tv:{tv}"] = {"at": time.time(), "episodes": episodes}
        _write_json(_episodes_path(), kept)
    return episodes


def _remember_stills(tv: int, found: dict[tuple[int, int], list[str]]) -> None:
    if not found:
        return
    with _LOCK:
        kept = _read_json(_episodes_path(), {})
        row = kept.get(f"tv:{tv}")
        if not row:
            return
        for episode in row["episodes"]:
            key = (episode["season"], episode["episode"])
            if key in found:
                episode["stills"] = found[key]
        _write_json(_episodes_path(), kept)


def _decode(data: bytes) -> bytes | None:
    """A picture scaled as the frames are: 32x18 RGB."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "-", "-vf", f"scale={W}:{H}:flags=area,format=rgb24", "-f", "rawvideo", "-"],
            input=data, capture_output=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out[:SIZE] if len(out) >= SIZE else None


def stills(tv: int, episodes: list[dict], *, http: httpx.Client | None = None) -> dict[tuple[int, int], list[bytes]]:
    """These episodes' stills, scaled down: (season, episode) -> [32x18 RGB]. Fetched once, kept."""
    from bankai.metadata import tmdb

    path = _stills_path(tv)
    with _LOCK:
        kept: dict[str, str] = _read_json(path, {})
    own = http is None
    found: dict[tuple[int, int], list[str]] = {}
    added = False
    out: dict[tuple[int, int], list[bytes]] = {}
    try:
        for episode in episodes:
            key = (episode["season"], episode["episode"])
            listed = episode.get("stills")
            if listed is None:
                if not tmdb.configured():
                    listed = [episode["still"]] if episode.get("still") else []
                else:
                    http = http or _client()
                    try:
                        answer = http.get(
                            f"{tmdb._API}/tv/{tv}/season/{key[0]}/episode/{key[1]}/images", params=_params()
                        )
                        listed = [] if answer.status_code == 404 else [
                            item["file_path"] for item in answer.raise_for_status().json().get("stills") or []
                            if item.get("file_path")
                        ][:STILLS_PER_EPISODE]
                    except (httpx.HTTPError, ValueError) as exc:
                        log.warning("TMDB stills of tv:%s %s: %s", tv, key, exc)
                        continue
                    if not listed and episode.get("still"):
                        listed = [episode["still"]]
                    found[key] = listed
                    episode["stills"] = listed
            pictures = []
            for still in listed:
                if still not in kept:
                    http = http or _client()
                    try:
                        answer = http.get(tmdb.url(still, "w300"))
                        answer.raise_for_status()
                    except httpx.HTTPError:
                        continue
                    raw = _decode(answer.content)
                    if raw is None:
                        continue
                    kept[still] = base64.b64encode(raw).decode()
                    added = True
                pictures.append(base64.b64decode(kept[still]))
            if pictures:
                out[key] = pictures
    finally:
        if own and http is not None:
            http.close()
    _remember_stills(tv, found)
    if added:
        with _LOCK:
            _write_json(path, {**_read_json(path, {}), **kept})
    return out


# -- Pictures -----------------------------------------------------------------------


def _vector(raw: bytes) -> tuple[list[float], int]:
    """A 32x18 picture as compared: its values centred and scaled to length one, and a
    144-bit hash of its 16x9 brightness (above the mean or not) to find likely frames fast."""
    mean = sum(raw) / len(raw)
    centred = [value - mean for value in raw]
    length = math.sqrt(sum(value * value for value in centred)) or 1.0
    vector = [value / length for value in centred]
    grey = []
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            total = 0
            for dy in (0, 1):
                for dx in (0, 1):
                    at = ((y + dy) * W + x + dx) * 3
                    total += raw[at] * 3 + raw[at + 1] * 6 + raw[at + 2]
            grey.append(total)
    middle = sum(grey) / len(grey)
    bits = 0
    for value in grey:
        bits = (bits << 1) | (value > middle)
    return vector, bits


def frames(path: Path, *, enough: Callable[[bytes], bool] | None = None) -> list[bytes]:
    """A file's keyframes at 32x18 RGB. Read end to end through a pipe in large blocks:
    ffmpeg's own small reads over 9p took three times as long. ``enough`` is shown each
    frame as it comes; once it says so, the rest of the file is not read."""
    ffmpeg = [
        "ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", "pipe:", "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", f"scale={W}:{H}:flags=area,format=rgb24", "-fps_mode", "passthrough", "-f", "rawvideo", "-",
    ]
    command = f"dd if={shlex.quote(str(path))} bs=8M status=none | {shlex.join(ffmpeg)}"
    out: list[bytes] = []
    try:
        process = subprocess.Popen(
            ["bash", "-c", command], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True
        )
    except OSError:
        return []
    try:
        assert process.stdout is not None
        while True:
            raw = process.stdout.read(SIZE)
            if len(raw) < SIZE:
                break
            out.append(raw)
            if enough is not None and enough(raw):
                break
    finally:
        if process.poll() is None:
            # The whole pipeline: dd would go on reading the file.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        process.wait()
    return out


class Seen:
    """Stops a file's reading once two of the expected episode's stills are among its frames."""

    def __init__(self, raws: list[bytes]) -> None:
        self.stills = [_vector(raw)[0] for raw in raws]
        self.best = [-1.0] * len(self.stills)

    def __call__(self, raw: bytes) -> bool:
        vector = _vector(raw)[0]
        for at, still in enumerate(self.stills):
            self.best[at] = max(self.best[at], _dot(vector, still))
        return len(self.stills) > 1 and sum(score >= MATCH + 0.04 for score in self.best) >= 2


def _dot(left: list[float], right: list[float]) -> float:
    return sum(map(float.__mul__, left, right))


def scores(file_frames: list[bytes], pictures: dict[Any, list[bytes]], *, near: int = 8) -> dict[Any, float]:
    """Per episode, how alike its stills are to the file's frames: each still against the
    frames its hash finds nearest, the episode by its second-best still (a single still a
    little less), since one still alone may be a frame many episodes share."""
    framed = [_vector(raw) for raw in file_frames]
    if not framed:
        return {}
    out: dict[Any, float] = {}
    for key, raws in pictures.items():
        best: list[float] = []
        for raw in raws:
            vector, bits = _vector(raw)
            nearest = heapq.nsmallest(near, framed, key=lambda frame: (frame[1] ^ bits).bit_count())
            best.append(max(_dot(vector, frame[0]) for frame in nearest))
        best.sort(reverse=True)
        out[key] = best[1] if len(best) > 1 else best[0] - 0.05
    return out


# -- Where an entry sits in its TMDB show ------------------------------------------


def _days(date: str | None) -> int | None:
    if not date:
        return None
    try:
        return int(time.mktime(time.strptime(date[:10], "%Y-%m-%d")) // 86400)
    except ValueError:
        return None


def placement(aid: int, episodes: list[dict]) -> tuple[list[dict], int] | None:
    """The TMDB episodes an AniList entry's are counted along, and where its episode 1 is.

    Fribb's season when it names one (else the regular seasons); within it the
    episode that aired nearest the entry's start (within ten days), else its first."""
    from bankai.metadata import anilist, animelists

    season = animelists.ids(aid).get("tmdb_season")
    line = [e for e in episodes if e["season"] == season] if isinstance(season, int) else []
    if not line:
        line = [e for e in episodes if e["season"] > 0]
        season = None
    if not line:
        return None
    start = _days(anilist.media(aid).get("start"))
    if start is not None:
        dated = [(abs(day - start), index) for index, e in enumerate(line) if (day := _days(e["air_date"])) is not None]
        near = min(dated, default=None)
        if near is not None and near[0] <= 10:
            return line, near[1]
    return (line, 0) if season is not None else None


def expected(aid: int, number: int, episodes: list[dict]) -> dict | None:
    placed = placement(aid, episodes)
    if placed is None:
        return None
    line, first = placed
    index = first + number - 1
    return line[index] if 0 <= index < len(line) else None


def label(episode: dict) -> str:
    return f"S{episode['season']:02d}E{episode['episode']:02d}"


def owner(episode: dict, aids: list[int], episodes: list[dict]) -> tuple[int, int] | None:
    """Which entry's episode (of these, e.g. a collection's) a TMDB episode is, by placement."""
    from bankai.metadata import anilist

    for aid in aids:
        placed = placement(aid, episodes)
        if placed is None:
            continue
        line, first = placed
        if episode not in line:
            continue
        index = line.index(episode) - first
        count = anilist.media(aid).get("episodes")
        if index >= 0 and (not count or index < int(count)):
            return aid, index + 1
    return None


# -- One file -----------------------------------------------------------------------


def _duration(path: Path) -> float | None:
    from bankai.web import track_cleanup

    row = track_cleanup.load_inventory().get(str(path)) or {}
    if row.get("duration"):
        try:
            stat = path.stat()
            if int(row.get("size") or 0) == stat.st_size:
                return float(row["duration"])
        except OSError:
            return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        ).stdout.strip()
        return float(out) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _tmdb_show(aid: int) -> int | None:
    from bankai.metadata import animelists

    tv = animelists.ids(aid).get("tmdb_tv")
    return int(tv) if isinstance(tv, int) else None


def _family(aid: int) -> list[int]:
    """The entries a file of this one might really be: its collection's, else itself."""
    from bankai.web import anime_collections

    row = anime_collections.load().collection_of(aid)
    return list(dict.fromkeys([aid, *(row.members if row else [])]))


def check(rel: str, pairs: list[tuple[int, int]], *, http: httpx.Client | None = None) -> dict[str, Any]:
    """One file looked at: its length, and its picture where TMDB has stills of its show."""
    from bankai.metadata import anilist
    from bankai.web import anime_files

    path = anime_files.anime_root() / rel
    stat = path.stat()
    aid, number = pairs[0]
    result: dict[str, Any] = {
        "anilist_id": aid,
        "episode": number,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "checked_at": time.time(),
    }
    tv = _tmdb_show(aid)
    episodes = show_episodes(tv, http=http) if tv else []
    wanted = [expected(a, n, episodes) for a, n in pairs] if episodes else []
    # Length.
    seconds = _duration(path)
    minutes = 0.0
    if wanted and all(e and e.get("runtime") for e in wanted):
        minutes, source = float(sum(e["runtime"] for e in wanted)), "TMDB"  # type: ignore[index]
    elif anilist.media(aid).get("duration"):
        minutes, source = float(anilist.media(aid)["duration"]) * len(pairs), "AniList"
    if seconds and minutes:
        ratio = seconds / (minutes * 60)
        result["length"] = {
            "seconds": round(seconds), "expected_minutes": minutes, "source": source,
            "ok": not ((ratio < SHORT or ratio > LONG) and abs(seconds - minutes * 60) > 180),
        }
    # Picture.
    target = wanted[0] if wanted else None
    if not episodes:
        result["picture"] = {"state": "none", "why": "TMDB has no episodes of this show" if tv else "no TMDB show for this entry"}
    elif target is None:
        result["picture"] = {"state": "none", "why": "not placed among TMDB's episodes"}
    else:
        line, first = placement(aid, episodes) or (episodes, 0)
        if len(episodes) <= WHOLE_SHOW:
            candidates = episodes
        else:
            at = line.index(target)
            candidates = line[max(0, at - WINDOW): at + WINDOW]
        pictures = stills(tv, candidates, http=http)  # type: ignore[arg-type]
        key = (target["season"], target["episode"])
        seen = Seen(pictures[key]) if key in pictures else None
        file_frames = frames(path, enough=seen) if pictures else []
        alike = scores(file_frames, pictures) if file_frames else {}
        own = alike.get(key)
        best_key = max(alike, key=alike.__getitem__) if alike else None
        best = next((e for e in candidates if (e["season"], e["episode"]) == best_key), None)
        picture: dict[str, Any] = {
            "tmdb": tv, "expected": label(target), "expected_name": target["name"],
            "score": round(own, 3) if own is not None else None, "frames": len(file_frames),
        }
        if best is not None:
            picture.update(match=label(best), match_name=best["name"], match_score=round(alike[best_key], 3))
            shown = owner(best, _family(aid), episodes)
            if shown:
                picture["is"] = list(shown)
        if key not in pictures:
            picture.update(state="none", why="TMDB has no stills of this episode")
        elif not file_frames:
            picture.update(state="none", why="the file's frames could not be read")
        elif own is not None and own >= MATCH:
            picture["state"] = "ok"
        elif best is not None and best_key != key and alike[best_key] >= MATCH and alike[best_key] - (own or 0) >= MARGIN:
            picture["state"] = "wrong"
        else:
            picture["state"] = "unsure"
        result["picture"] = picture
    result["verdict"] = verdict(result)
    return result


def _se(text: object) -> tuple[int, int] | None:
    found = re.fullmatch(r"S(\d+)E(\d+)", str(text or ""))
    return (int(found[1]), int(found[2])) if found else None


def certainly_wrong(picture: dict[str, Any]) -> bool:
    """Whether a picture leaning to another episode leaves no other explanation (see WRONG_MATCH)."""
    if picture.get("state") != "wrong":
        return False
    own, other = _se(picture.get("expected")), _se(picture.get("match"))
    if own is None or other is None or 0 in (own[0], other[0]):
        return False
    if own[0] == other[0] and abs(own[1] - other[1]) <= NEIGHBOURS:
        return False
    return float(picture.get("match_score") or 0) >= WRONG_MATCH and float(picture.get("score") or 0) <= WRONG_OWN


def verdict(result: dict[str, Any]) -> str:
    """verified: its picture is the episode; wrong: its picture is certainly another (``certainly_wrong``);
    plausible: no picture to compare, its length fits; unsure: anything else -- not shown, only a
    certain answer is worth the user's look."""
    picture = (result.get("picture") or {}).get("state")
    length = result.get("length")
    if picture == "ok":
        return "verified"
    if certainly_wrong(result.get("picture") or {}):
        return "wrong"
    if picture == "none" and length and length["ok"]:
        return "plausible"
    return "unsure"


# -- Count, across files ------------------------------------------------------------


def issues(rows: dict[str, list[tuple[int, int]]], found: dict[str, dict]) -> dict[str, list[str]]:
    """Per file, what is wrong beside others, from the file index alone (facts, not a picture's guess):
    two files on one episode, an episode past its entry's end. A file whose picture is certainly
    another episode the library has shows that as "a second copy of" in the drawer."""
    from bankai.metadata import anilist

    out: dict[str, list[str]] = {}
    by_episode: dict[tuple[int, int], list[str]] = {}
    for rel, pairs in rows.items():
        for aid, number in pairs:
            by_episode.setdefault((aid, number), []).append(rel)
            count = anilist.media(aid).get("episodes")
            if count and number > int(count) and anilist.media(aid).get("status") == "FINISHED":
                out.setdefault(rel, []).append(f"episode {number} of an entry with {count}")
    for rels in by_episode.values():
        if len(rels) > 1:
            for rel in rels:
                out.setdefault(rel, []).append(f"{len(rels)} files are this episode")
    return out


def annotate(episodes: list[dict]) -> dict[str, int]:
    """The library's episode rows of one show with their verdicts (``verify``), and its counts."""
    from bankai.web import anime_files

    found = results()
    sure = confirmations()
    rows: dict[str, list[tuple[int, int]]] = {}
    for row in episodes:
        rel = anime_files.key(row["path"]) if row.get("path") and not row.get("missing") else None
        if rel and row.get("anilist_id") is not None and row.get("anilist_episode") is not None:
            rows[rel] = [(int(row["anilist_id"]), int(row["anilist_episode"]))]
    extra = issues(rows, found)
    counts = {"verify_checked": 0, "verify_wrong": 0, "verify_unsure": 0, "verify_files": len(rows)}
    for row in episodes:
        rel = anime_files.key(row["path"]) if row.get("path") and not row.get("missing") else None
        if rel not in rows:
            continue
        result = found.get(rel)
        current = bool(result) and _same(result, row.get("size"), row.get("mtime"))
        confirmed = rel in sure and _same(sure[rel], row.get("size"), row.get("mtime"))
        if not current and not confirmed and not extra.get(rel):
            continue
        shown = dict(result) if current else {"verdict": "unchecked"}
        if current:
            # Today's rules over the verdict saved with the result.
            shown["verdict"] = verdict(result)
        notes = extra.get(rel) or []
        if confirmed:
            # The user's word over the check's: its findings stay readable, the verdict is theirs.
            shown.update(verdict="confirmed", confirmed_at=sure[rel].get("at"))
            if notes:
                shown["issues"] = notes
            row["verify"] = shown
            counts["verify_checked"] += 1
            continue
        if notes:
            shown["issues"] = notes
        row["verify"] = shown
        if current:
            counts["verify_checked"] += 1
        counts["verify_wrong"] += shown["verdict"] == "wrong"
        counts["verify_unsure"] += shown["verdict"] == "unsure"
    return counts


def _same(result: dict, size: object, mtime: object) -> bool:
    """A result for the file as it is: same size and time (the library's walk has both)."""
    try:
        return int(result.get("size") or -1) == int(size) and abs(float(result.get("mtime") or 0) - float(mtime)) < 2  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _current(rel: str, result: dict) -> bool:
    from bankai.web import anime_files

    try:
        stat = (anime_files.anime_root() / rel).stat()
    except OSError:
        return False
    return _same(result, stat.st_size, stat.st_mtime)


# -- The pass -----------------------------------------------------------------------


def due() -> list[str]:
    """Files to check, in order: asked for by hand, never checked, changed since."""
    from bankai.web import anime_files

    data = load()
    index = anime_files.load()
    order = [rel for rel in data["first"] if rel in index]
    asked = set(order)
    found = data["files"]
    # Confirmed by the user: no check needed while the file is as it was.
    sure = {rel for rel, row in data["confirmed"].items() if _current(rel, row)}
    fresh = [rel for rel in sorted(index) if rel not in asked and rel not in found and rel not in sure]
    stale = [rel for rel in sorted(index) if rel not in asked and rel in found and rel not in sure and not _current(rel, found[rel])]
    return order + fresh + stale


def run(*, seconds: float = 240.0) -> dict[str, int]:
    """Check files for about this long, one after another. Files the encode or the track
    cleanup is writing anew wait (a result for a file about to change is wasted)."""
    from bankai.web import anime_files

    todo = due()
    index = anime_files.load()
    checked = wrong = failed = 0
    started = time.monotonic()
    with _client() as http:
        for rel in todo:
            if time.monotonic() - started > seconds:
                break
            path = anime_files.anime_root() / rel
            try:
                if not path.is_file() or time.time() - path.stat().st_mtime < 3600:
                    continue
            except OSError:
                continue
            progress.update(file=rel, at=time.time())
            try:
                result = check(rel, [tuple(pair) for pair in index[rel]], http=http)  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 - one bad file never stops the pass
                log.warning("verify %s: %s", rel, exc)
                failed += 1
                result = {"verdict": "unsure", "error": str(exc), "checked_at": time.time()}
                try:
                    stat = path.stat()
                    result.update(size=stat.st_size, mtime=stat.st_mtime)
                except OSError:
                    continue
            with _LOCK:
                data = load()
                data["files"][rel] = result
                data["first"] = [other for other in data["first"] if other != rel]
                _save(data)
            checked += 1
            wrong += result.get("verdict") == "wrong"
            if result.get("verdict") == "wrong":
                picture = result.get("picture") or {}
                log.info("[verify] %s shows %s (%s), not %s", rel, picture.get("match"), picture.get("match_name"), picture.get("expected"))
    progress.clear()
    return {"checked": checked, "wrong": wrong, "failed": failed, "remaining": max(0, len(todo) - checked)}
