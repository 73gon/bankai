"""Shows followed in English: every aired episode downloaded from torrents, German where a release has it.

Search -> a show -> "Torrent" follows it (``mas_follow.json``). The pass ``mas_follow``
compares TMDB's aired episodes with what the library holds in either language
(web.direct.held) and searches Prowlarr for the rest: a season pack when much of a
season is missing, single episodes otherwise, a few searches a pass (Prowlarr's
indexers rate-limit). A release with German is preferred; each file then goes by its
own audio -- German into the German folder, English-only into the English one
(processor.direct). An episode held only in English is looked for in German now and
then, a season at a time; one found replaces it. No review: the release is the file.

Downloads are ``direct-download`` jobs with ``--follow <tmdb id>``, so the jobs say
which episodes are on their way and which releases failed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import statistics
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.web import direct

log = get_logger(__name__)

_LOCK = threading.RLock()
QUALITIES = ("2160p", "1080p", "720p", "480p")
# Prowlarr searches a pass, over every followed show: its indexers rate-limit.
SEARCHES_PER_PASS = 3
# Downloads of followed shows at once; more wait for a later pass.
MAX_ACTIVE = 6
# A search that found nothing is tried again after this long; a German one less often.
RETRY_MISSING = 12 * 3600
RETRY_GERMAN = 3 * 86400
MIN_SEEDERS = 2
# A download starts only with this much room left on its drive afterwards.
RESERVE_BYTES = 20 * 2**30
# Megabytes a minute of picture, for an estimate when the indexers give nothing to go by.
_MB_PER_MINUTE = {"2160p": 120, "1080p": 40, "720p": 20, "480p": 8}
# One pass at a time: Follow, then Save, each started one, and both queued Modern
# Family's S02 before either saw the other's job (Oct 2026).
_PASS = threading.Lock()
# The preview's indexer search, by title: it takes half a minute, and changing the
# quality or opening the dialog again should not wait for it once more.
_RELEASES: dict[str, tuple[float, list[dict], str | None]] = {}
RELEASES_FRESH = 15 * 60

progress: dict[str, Any] = {}


# -- the store ------------------------------------------------------------------


def _path() -> Path:
    return bgjobs.jobs_root().parent / "mas_follow.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("shows", {})
    data.setdefault("searches", {})
    return data


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def followed() -> dict[str, dict[str, Any]]:
    with _LOCK:
        return dict(_load()["shows"])


def get(tmdb_id: int) -> dict[str, Any] | None:
    return followed().get(str(tmdb_id))


def follow(tmdb_id: int, *, title: str, year: int | None, quality: str, roots: dict[str, str], names: list[str]) -> dict:
    if quality not in QUALITIES:
        raise ValueError("quality must be one of " + ", ".join(QUALITIES))
    for language in ("German", "English"):
        if not roots.get(language):
            raise ValueError(f"a folder for {language} is needed")
        direct.target(roots[language], "show")
    with _LOCK:
        data = _load()
        before = data["shows"].get(str(tmdb_id)) or {}
        row = {
            "tmdb_id": tmdb_id,
            "title": title.strip(),
            "year": year,
            "quality": quality,
            "roots": {"German": roots["German"], "English": roots["English"]},
            "names": [name for name in dict.fromkeys(names) if name and name != title],
            "added": before.get("added") or time.time(),
        }
        data["shows"][str(tmdb_id)] = row
        if before and before.get("quality") != quality:
            # Another quality: what was searched for before is worth asking again.
            data["searches"].pop(str(tmdb_id), None)
        _save(data)
    return row


def unfollow(tmdb_id: int) -> bool:
    with _LOCK:
        data = _load()
        gone = data["shows"].pop(str(tmdb_id), None)
        data["searches"].pop(str(tmdb_id), None)
        _save(data)
    return gone is not None


def searches(tmdb_id: int) -> dict[str, dict]:
    with _LOCK:
        return dict(_load()["searches"].get(str(tmdb_id)) or {})


def _note_search(tmdb_id: int, key: str, **fields: Any) -> None:
    with _LOCK:
        data = _load()
        data["searches"].setdefault(str(tmdb_id), {})[key] = {"at": time.time(), **fields}
        _save(data)


def forget_searches(tmdb_id: int) -> None:
    """Look again now, whatever was searched for before ("Look now")."""
    with _LOCK:
        data = _load()
        data["searches"].pop(str(tmdb_id), None)
        _save(data)


def _passing(job: dict) -> bool:
    """Did a download fail for a passing reason -- cut off by a restart, a timeout, a dropped
    connection? Its release is not to blame, and may be picked again."""
    from bankai.web import reasons

    found = reasons.classify_reason(job.get("reason") or "Timed out")
    return bool(found) and found[0] in {"timeout", "network"}


def rejected() -> set[str]:
    """Releases a download passed over (processor.direct: no English subtitles), not to pick again."""
    from bankai.processor.direct import _rejected_path

    try:
        rows = json.loads(_rejected_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(key).casefold() for key in rows} if isinstance(rows, dict) else set()


# -- what is on the way ---------------------------------------------------------


def episode_codes(pairs: list[tuple[int, int]] | set[tuple[int, int]]) -> str:
    return ",".join(f"S{s:02d}E{e:02d}" for s, e in sorted(pairs))


def _codes(raw: str | None) -> set[tuple[int, int]]:
    return {(int(m[1]), int(m[2])) for m in re.finditer(r"S(\d+)E(\d+)", raw or "", re.I)}


def _jobs_for(tmdb_id: int) -> tuple[list[dict], list[dict]]:
    """(active, failed) download jobs of this show: id, release, episodes, season."""
    from bankai.web import jobs as webjobs

    wanted = str(tmdb_id)
    active: list[dict] = []
    failed: list[dict] = []
    rows = [(job.id, job.status, job.args, job.title, job) for job in bgjobs.list_jobs()]
    rows += [(item.id, "queued", item.args, item.title, None) for item in webjobs.list_pending()]
    for ident, status, args, title, job in rows:
        if not args or args[0] != "direct-download" or bgjobs.argument_value(args, "--follow") != wanted:
            continue
        season = bgjobs.argument_value(args, "--season")
        row = {
            "id": ident,
            "status": status,
            "title": title,
            "release": bgjobs.argument_value(args, "--release-title") or "",
            "info_hash": (bgjobs.argument_value(args, "--info-hash") or "").casefold(),
            "episodes": sorted(_codes(bgjobs.argument_value(args, "--episodes"))),
            "season": int(season) if str(season or "").isdigit() else None,
        }
        if status in {"running", "queued", "stopped"}:
            active.append(row)
        elif status == "failed":
            failed.append({**row, "reason": bgjobs.failure_reason(job) if job else None})
    return active, failed


# -- the library against TMDB's episodes ----------------------------------------


def _english_subs(paths: list[Path]) -> bool | None:
    """Do the English copies come with English subtitles? From the track inventory only (None: not read yet)."""
    found = [direct.has_english_subtitles(Path(path), probe=False) for path in paths]
    if any(value is True for value in found):
        return True
    return False if found and all(value is False for value in found) else None


def states(show: dict, info: dict, active: list[dict], *, today: str | None = None) -> list[dict]:
    """Every episode TMDB lists (specials only when on disk) with what the library has of it.

    ``state``: german, english, downloading, missing, upcoming. ``paths`` are its files.
    """
    today = today or date.today().isoformat()
    library = direct.held("show", show["title"], show.get("year"), tuple(show.get("names") or ()))
    coming = {tuple(pair) for job in active for pair in job["episodes"]}
    rows: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for episode in info.get("episodes") or []:
        key = (int(episode["season"]), int(episode["episode"]))
        if key in seen:
            continue
        seen.add(key)
        german, english = library["German"].get(key), library["English"].get(key)
        if key[0] == 0 and not (german or english):
            continue
        aired = episode.get("aired")
        if german:
            state = "german"
        elif key in coming:
            state = "downloading"
        elif english:
            state = "english"
        elif not aired or aired > today:
            state = "upcoming"
        else:
            state = "missing"
        rows.append({
            "season": key[0],
            "episode": key[1],
            "name": episode.get("name"),
            "aired": aired,
            "runtime": episode.get("runtime"),
            "state": state,
            "paths": [str(path) for path in (german or english or [])],
            # English audio needs English subtitles: False sends it to be looked for again.
            "english_subs": _english_subs(english) if state == "english" else None,
        })
    # Files TMDB does not number like this: shown, never searched for.
    for language in ("German", "English"):
        for key, paths in library[language].items():
            if key not in seen:
                seen.add(key)
                rows.append({
                    "season": key[0], "episode": key[1], "name": None, "aired": None, "runtime": None,
                    "state": language.casefold(), "paths": [str(path) for path in paths], "extra": True,
                })
    rows.sort(key=lambda row: (row["season"], row["episode"]))
    return rows


def plan(rows: list[dict], searched: dict[str, dict], *, now: float) -> list[dict]:
    """The searches due, missing episodes before German ones.

    A season with much of it missing is one search for its pack; a season whose pack
    search found nothing, or with a few missing, is searched episode by episode.
    """
    units: list[dict] = []
    seasons = sorted({row["season"] for row in rows if row["season"] > 0})
    for season in seasons:
        own = [row for row in rows if row["season"] == season and not row.get("extra")]
        aired = [row for row in own if row["state"] != "upcoming"]
        # An English copy without English subtitles is looked for again, like a missing episode.
        missing = [
            (season, row["episode"]) for row in own
            if row["state"] == "missing" or (row["state"] == "english" and row.get("english_subs") is False)
        ]
        english = [(season, row["episode"]) for row in own if row["state"] == "english"]
        pack_key = f"S{season:02d}"
        pack_failed = searched.get(pack_key, {}).get("found") is False
        if missing:
            if len(missing) >= max(3, len(aired) // 2) and not pack_failed:
                units.append({"key": pack_key, "season": season, "episode": None, "want": missing + english,
                              "german_only": False, "retry": RETRY_MISSING})
            else:
                units.extend(
                    {"key": f"S{season:02d}E{episode:02d}", "season": season, "episode": episode,
                     "want": [(season, episode)], "german_only": False, "retry": RETRY_MISSING}
                    for _season, episode in missing
                )
        elif english and not any(row["state"] == "downloading" for row in own):
            units.append({"key": f"S{season:02d}:de", "season": season, "episode": None, "want": english,
                          "german_only": True, "retry": RETRY_GERMAN})
    due = [unit for unit in units if now - float(searched.get(unit["key"], {}).get("at") or 0) >= unit["retry"]]
    due.sort(key=lambda unit: (unit["german_only"], unit["season"], unit["episode"] or 0))
    return due


# -- picking a release ----------------------------------------------------------

_COUNTRY = ("us", "uk", "au", "nz", "ca")


def _title_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\b(19|20)\d\d\b", "", name.casefold()))


def same_title(release_title: str, names: list[str]) -> bool:
    """Is the release's title one of the show's names ("The Office US" for "The Office" too)?"""
    found = _title_key(release_title)
    for name in names:
        wanted = _title_key(name)
        if not wanted:
            continue
        if found == wanted or any(found == wanted + country or wanted == found + country for country in _COUNTRY):
            return True
    return False


def quality_rank(found: str | None, wanted: str) -> int | None:
    """How near a release's resolution is to the one wanted; None for one above it."""
    order = list(reversed(QUALITIES))  # 480p .. 2160p
    if found not in order:
        return 0
    gap = order.index(wanted) - order.index(found)
    return None if gap < 0 else 3 - min(gap, 3)


_RANGE = re.compile(r"\bS\d{1,2}\s*-\s*S?\d{1,2}\b|\bseasons?\s*\d+\s*-\s*\d+|complete\s+series", re.I)


def pick(rows: list[dict], *, unit: dict, names: list[str], quality: str, skip: set[str]) -> dict | None:
    """The best release for a search: right show and season, German first, then fit, quality, seeders."""
    best: tuple[tuple, dict] | None = None
    for row in rows:
        if row["id"] in skip or (row.get("info_hash") or "").casefold() in skip or row["seeders"] < MIN_SEEDERS:
            continue
        if not same_title(row["guess"]["title"], names) or _RANGE.search(row["title"]):
            continue
        numbers = direct.file_episode(row["title"])
        season, episode = unit["season"], unit["episode"]
        if episode is None:
            if numbers is not None or row["guess"]["season"] != season:
                continue
            fit = 2
        elif numbers and numbers[0] == season and numbers[1] <= episode <= (numbers[2] or numbers[1]):
            fit = 2
        elif numbers is None and row["guess"]["season"] == season:
            fit = 1  # the season's pack, for one episode of it
        else:
            continue
        german = "German" in row["languages"]
        if unit["german_only"] and not german:
            continue
        rank = quality_rank(row["resolution"], quality)
        if rank is None:
            continue
        if episode is None and not german and rank < 3:
            # An English pack below the quality chosen: the season's episodes alone, at that
            # quality, are better (Modern Family S08 came as a 720p pack, 1080p only singly).
            continue
        score = (german, fit, rank, min(row["seeders"], 50) // 10, row["seeders"])
        if best is None or score > best[0]:
            best = (score, row)
    return best[1] if best else None


# -- downloading ----------------------------------------------------------------


def job_args(show: dict, row: dict, *, season: int, want: list[tuple[int, int]] | set[tuple[int, int]]) -> list[str]:
    args = [
        "direct-download",
        "--release-title", row["title"],
        "--kind", "show",
        "--title", show["title"],
        "--root-german", show["roots"]["German"],
        "--root-english", show["roots"]["English"],
        "--follow", str(show["tmdb_id"]),
        "--season", str(season),
        "--episodes", episode_codes(want),
    ]
    for name in show.get("names") or []:
        args.extend(["--also-named", name])
    for option, value in (
        ("--download-url", row.get("download_url")),
        ("--magnet-uri", row.get("magnet_uri")),
        ("--info-hash", row.get("info_hash")),
        ("--info-url", row.get("info_url")),
        ("--year", show.get("year")),
    ):
        if value:
            args.extend([option, str(value)])
    return args


def _room_for(show: dict, size: int) -> bool:
    """Is there room on the drives for this download, with a reserve left?"""
    for root in {show["roots"]["German"], show["roots"]["English"]}:
        try:
            if shutil.disk_usage(root).free - size < RESERVE_BYTES:
                return False
        except OSError:
            return False
    return True


def enqueue(show: dict, row: dict, *, season: int, want: list[tuple[int, int]] | set[tuple[int, int]]) -> dict:
    from bankai.web import jobs as webjobs

    label = f"S{season:02d}" if len(want) != 1 else episode_codes(want)
    return webjobs.enqueue(
        kind="show",
        title=f"{show['title']} · {label} · {row['title']}",
        args=job_args(show, row, season=season, want=want),
    )


def _season_want(rows: list[dict], season: int) -> list[tuple[int, int]]:
    """What a pack of this season may fill: its episodes missing or held only in English."""
    return [(row["season"], row["episode"]) for row in rows
            if row["season"] == season and row["state"] in {"missing", "english"} and not row.get("extra")]


async def _info(tmdb_id: int) -> dict:
    from bankai.web.anime_library import flush_persistent_cache, show_info

    info = await show_info(tmdb_id)
    await asyncio.to_thread(flush_persistent_cache)
    return info


async def run(*, budget: int = SEARCHES_PER_PASS, only: int | None = None) -> dict:
    """One pass: the searches due, over every followed show (or one), at most ``budget`` of them."""
    await asyncio.to_thread(_PASS.acquire)
    try:
        return await _run(budget=budget, only=only)
    finally:
        _PASS.release()


async def _run(*, budget: int, only: int | None) -> dict:
    from bankai.processor import direct as direct_download

    shows = [show for key, show in followed().items() if only is None or key == str(only)]
    result: dict[str, Any] = {"followed": len(shows), "searched": 0, "queued": 0, "found_nothing": 0}
    if only is None:
        try:
            swept = await direct_download.sweep()
        except Exception as exc:  # qBittorrent away: the searches still run
            log.warning("[follow] sweeping finished torrents failed: %s", exc)
            swept = []
        if swept:
            result["swept"] = len(swept)
            log.info("[follow] removed %d finished torrent(s) no job reads", len(swept))
    if not shows:
        return result
    active_total = 0
    work: list[tuple[dict, list[dict], set[str], dict]] = []
    for show in shows:
        info = await _info(int(show["tmdb_id"]))
        if not info:
            continue
        active, failed = await asyncio.to_thread(_jobs_for, int(show["tmdb_id"]))
        active_total += len(active)
        rows = await asyncio.to_thread(states, show, info, active)
        skip = {job["info_hash"] for job in failed if job["info_hash"] and not _passing(job)} | rejected()
        for unit in plan(rows, searches(int(show["tmdb_id"])), now=time.time()):
            work.append((show, rows, skip, unit))
    for show, rows, skip, unit in work:
        if result["searched"] >= budget or active_total >= MAX_ACTIVE:
            break
        query = f"{show['title']} S{unit['season']:02d}" + (f"E{unit['episode']:02d}" if unit["episode"] else "")
        if unit["german_only"]:
            query += " German"
        progress.update(step="search", series=show["title"], query=query)
        try:
            found = await direct.search(query, kind="show", min_seeders=MIN_SEEDERS)
        except RuntimeError as exc:
            result["error"] = str(exc)
            break
        result["searched"] += 1
        found = {**found, "items": [direct.found(row["id"]) or row for row in found["items"]]}
        names = [show["title"], *(show.get("names") or [])]
        row = pick(found["items"], unit=unit, names=names, quality=show["quality"], skip=skip)
        tmdb_id = int(show["tmdb_id"])
        if row is None:
            result["found_nothing"] += 1
            _note_search(tmdb_id, unit["key"], found=False, query=query, results=len(found["items"]))
            continue
        want = unit["want"] if unit["episode"] else (
            _season_want(rows, unit["season"]) if direct.file_episode(row["title"]) is None else unit["want"]
        )
        if not _room_for(show, int(row["size_bytes"] or 0)):
            _note_search(tmdb_id, unit["key"], found=True, query=query, release=row["title"], held="not enough room")
            result["held_for_space"] = True
            continue
        queued = await asyncio.to_thread(enqueue, show, row, season=unit["season"], want=want)
        _note_search(tmdb_id, unit["key"], found=True, query=query, release=row["title"], job=queued.get("id"))
        if queued.get("status") in {"running", "queued"}:
            result["queued"] += 1
            active_total += 1
            log.info("[follow] %s: %s for %s", show["title"], row["title"], unit["key"])
    progress.clear()
    return result


# -- what following would take --------------------------------------------------


def _per_episode_sizes(rows: list[dict], *, names: list[str], quality: str, aired: dict[int, int]) -> list[float]:
    sizes: list[float] = []
    for row in rows:
        if not row.get("size_bytes") or not same_title(row["guess"]["title"], names) or _RANGE.search(row["title"]):
            continue
        if row["resolution"] not in {quality, None}:
            continue
        numbers = direct.file_episode(row["title"])
        if numbers:
            sizes.append(row["size_bytes"] / ((numbers[2] or numbers[1]) - numbers[1] + 1))
        elif row["guess"]["season"] and aired.get(row["guess"]["season"]):
            sizes.append(row["size_bytes"] / aired[row["guess"]["season"]])
    return sizes


def estimate(rows: list[dict], wanted: list[dict], *, names: list[str], quality: str, runtime: int | None) -> dict:
    """Bytes the wanted episodes would take: from the releases on the indexers, else from their running time."""
    aired: dict[int, int] = {}
    for row in wanted:
        aired[row["season"]] = aired.get(row["season"], 0) + 1
    sizes = _per_episode_sizes(rows, names=names, quality=quality, aired=aired)
    if len(sizes) >= 3:
        per = statistics.median(sizes)
        return {"bytes": int(per * len(wanted)), "per_episode": int(per), "basis": "releases", "samples": len(sizes)}
    minutes = sum(int(row.get("runtime") or runtime or 30) for row in wanted)
    per_minute = _MB_PER_MINUTE.get(quality, 40) * 2**20
    return {
        "bytes": int(minutes * per_minute),
        "per_episode": int(minutes * per_minute / max(1, len(wanted))),
        "basis": "runtime",
        "samples": len(sizes),
    }


def _season_rows(rows: list[dict]) -> list[dict]:
    seasons: dict[int, dict] = {}
    for row in rows:
        if row.get("extra"):
            continue
        entry = seasons.setdefault(row["season"], {"season": row["season"], "episodes": 0, "aired": 0,
                                                   "german": 0, "english": 0, "downloading": 0, "missing": 0})
        entry["episodes"] += 1
        entry["aired"] += row["state"] != "upcoming"
        if row["state"] in entry:
            entry[row["state"]] += 1
    return [seasons[number] for number in sorted(seasons)]


async def _releases(title: str) -> tuple[list[dict], str | None]:
    """What the indexers have under a show's title, kept a quarter of an hour."""
    held = _RELEASES.get(title)
    if held and time.time() - held[0] < RELEASES_FRESH:
        return held[1], held[2]
    try:
        found = await direct.search(title, kind="show", min_seeders=1)
    except RuntimeError as exc:
        return [], str(exc)
    releases = [direct.found(row["id"]) or row for row in found["items"]]
    _RELEASES[title] = (time.time(), releases, found.get("problem"))
    return releases, found.get("problem")


async def preview(tmdb_id: int, *, quality: str = "1080p") -> dict:
    """What following a show means: its seasons and episodes, what the library has, the size to download."""
    if quality not in QUALITIES:
        raise ValueError("quality must be one of " + ", ".join(QUALITIES))
    info = await _info(tmdb_id)
    if not info:
        raise LookupError("TMDB has no such show (or cannot be reached)")
    stored = get(tmdb_id)
    show = stored or {
        "tmdb_id": tmdb_id, "title": info["name"], "year": info.get("year"),
        "names": [info["german"]] if info.get("german") else [],
    }
    active, _failed = await asyncio.to_thread(_jobs_for, tmdb_id)
    rows = await asyncio.to_thread(states, show, info, active)
    wanted = [row for row in rows if row["state"] == "missing" and not row.get("extra")]
    names = [show["title"], *(show.get("names") or [])]
    releases, problem = await _releases(show["title"])
    size = estimate(releases, wanted, names=names, quality=quality, runtime=info.get("runtime"))
    regular = [row for row in rows if row["season"] > 0 and not row.get("extra")]
    defaults = direct.default_roots("show")
    return {
        "tmdb_id": tmdb_id,
        "title": info["name"],
        "german_title": info.get("german"),
        "year": info.get("year"),
        "status": info.get("status"),
        "poster_url": info.get("poster_url"),
        "seasons": [row for row in _season_rows(rows) if row["season"] > 0],
        "counts": {
            "episodes": len(regular),
            "aired": sum(row["state"] != "upcoming" for row in regular),
            "german": sum(row["state"] == "german" for row in regular),
            "english": sum(row["state"] == "english" for row in regular),
            "downloading": sum(row["state"] == "downloading" for row in regular),
            "missing": len(wanted),
        },
        "estimate": size,
        "quality": quality,
        "followed": stored,
        "roots": (stored or {}).get("roots") or defaults,
        "targets": [row for row in direct.targets() if row["library"] == "shows"],
        "problem": problem,
    }


def _qualities(rows: list[dict]) -> None:
    """Each episode's quality (2160p, 1080p, ...), from the track inventory's reading of its file."""
    from bankai.web import track_cleanup
    from bankai.web.mas_library import quality

    inventory = track_cleanup.load_inventory()
    for row in rows:
        row["quality"] = quality((inventory.get(row["paths"][0]) or {}).get("video")) if row["paths"] else None


async def detail(tmdb_id: int) -> dict:
    """A followed show for its drawer: every episode with its state, the searches made, the downloads under way."""
    show = get(tmdb_id)
    if show is None:
        raise LookupError("that show is not followed")
    info = await _info(tmdb_id)
    active, failed = await asyncio.to_thread(_jobs_for, tmdb_id)
    rows = await asyncio.to_thread(states, show, info, active) if info else []
    await asyncio.to_thread(_qualities, rows)
    return {
        "show": show,
        "info": {key: info.get(key) for key in ("name", "german", "year", "status", "poster_url", "seasons")},
        "episodes": rows,
        "seasons": _season_rows(rows),
        "searches": searches(tmdb_id),
        "active": active,
        "failed": failed[:10],
    }


async def overview() -> list[dict]:
    """Every followed show with its counts, for the library's cards."""
    out: list[dict] = []
    for key, show in followed().items():
        info = await _info(int(key))
        active, _failed = await asyncio.to_thread(_jobs_for, int(key))
        rows = await asyncio.to_thread(states, show, info, active) if info else []
        regular = [row for row in rows if row["season"] > 0 and not row.get("extra")]
        out.append({
            **show,
            "poster_url": info.get("poster_url"),
            "status": info.get("status"),
            "counts": {state: sum(row["state"] == state for row in regular)
                       for state in ("german", "english", "downloading", "missing", "upcoming")},
            "episodes": len(regular),
        })
    return sorted(out, key=lambda row: row["title"].casefold())


async def movie_releases(tmdb_id: int, *, quality: str = "1080p") -> dict:
    """A film's releases, the best first (German, then quality, then seeders), and what the library has of it."""
    from bankai.web.discover import title_details

    if quality not in QUALITIES:
        raise ValueError("quality must be one of " + ", ".join(QUALITIES))
    details = await title_details(tmdb_id, kind="movie")
    if not details.english:
        raise LookupError("TMDB has no such film (or cannot be reached)")
    title, year = details.english, details.worldwide_year
    names = [title, *([details.german] if details.german else [])]
    found = await direct.search(f"{title} {year}" if year else title, kind="movie", min_seeders=1)
    scored: list[tuple[tuple, dict]] = []
    for shown in found["items"]:
        row = direct.found(shown["id"]) or shown
        if not same_title(row["guess"]["title"], names):
            continue
        if year and row["guess"]["year"] not in {None, year - 1, year, year + 1}:
            continue
        rank = quality_rank(row["resolution"], quality)
        german = "German" in row["languages"]
        scored.append(((rank is not None, german, rank or 0, min(row["seeders"], 50) // 10, row["seeders"]), shown))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    items = [{**shown, "german": score[1], "fits": score[0]} for score, shown in scored]
    best = next((row for row in items if row["fits"] and row["seeders"] >= MIN_SEEDERS), None)
    library = await asyncio.to_thread(direct.held, "movie", title, year, tuple(names[1:]))
    return {
        "tmdb_id": tmdb_id,
        "title": title,
        "german_title": details.german,
        "year": year,
        "items": items,
        "best": best["id"] if best else None,
        "held": {language: [str(path) for paths in files.values() for path in paths] for language, files in library.items()},
        "roots": direct.default_roots("movie"),
        "found": found["found"],
        "problem": found.get("problem"),
    }


__all__ = [
    "detail", "follow", "followed", "movie_releases", "overview", "pick", "plan", "preview", "run", "states", "unfollow",
]
