"""Move what the TVDB era left in the anime state over to AniDB, once.

Anime is identified on AniDB only. What was recorded before -- episodes keyed
"<tvdb>|<season>|<episode>", tracked shows keyed by TVDB id, jobs queued with
``--tvdb-id`` -- is translated through Anime-Lists, which records where each
AniDB entry starts within its TVDB season. That is an offline file; TVDB
itself is never asked. Whatever Anime-Lists cannot place is reported and left
as it was, inert.

Run with the automation stopped: ``bankai anime migrate-anidb`` (``--dry-run``
to only report). Every file it changes is copied aside first.
"""

from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.metadata import anidb as anidb_mod

# Flags of an anime-download job that the AniDB route sets itself, with a value.
_IDENTITY_FLAGS = {"--tvdb-id", "--season", "--year", "--english-title", "--episode", "--anidb-id", "--anidb-title"}
# The fields a TVDB choice put on a saved mapping.
_TVDB_MAPPING_FIELDS = {
    "tvdb_id", "kind", "english_title", "japanese_title", "year", "poster_url", "aliases",
    "status", "season", "episode_offset",
}


def tvdb_episode_to_anidb(table: Any, tvdb_id: int, season: int, episode: int) -> tuple[Any, int] | None:
    """The AniDB entry and episode a TVDB season episode is, by Anime-Lists.

    A TVDB season split in cours is several AniDB entries, each starting at its
    offset: the episode belongs to the last one starting before it.
    """
    if table is None or season < 1 or episode < 1:
        return None
    parts = [
        entry
        for entry in table.by_tvdb.get(int(tvdb_id), [])
        if (entry.tvdb_season or "").isdigit() and int(entry.tvdb_season) == season
    ]
    starting = [entry for entry in parts if entry.tvdb_offset < episode]
    if not starting:
        return None
    entry = max(starting, key=lambda item: (item.tvdb_offset, item.aid))
    return entry, episode - entry.tvdb_offset


def _strip(args: list[str], flags: set[str]) -> list[str]:
    out: list[str] = []
    skip = False
    for value in args:
        if skip:
            skip = False
            continue
        if value in flags:
            skip = True
            continue
        out.append(value)
    return out


def _with_entry(args: list[str], entry: Any, episode: int) -> list[str]:
    return [
        *_strip(args, _IDENTITY_FLAGS),
        "--anidb-id",
        str(entry.aid),
        "--anidb-title",
        entry.title,
        "--english-title",
        entry.english_title or entry.title,
        "--episode",
        str(episode),
    ]


def anidb_job_args(args: list[str], table: Any) -> list[str] | None:
    """An anime-download job's arguments with its TVDB identity turned into AniDB.

    Unchanged when there is nothing to turn; None when a show episode cannot
    be placed on AniDB.
    """
    if not args or args[0] != "anime-download" or "--tvdb-id" not in args:
        return args
    if "--anidb-id" in args or bgjobs.argument_value(args, "--kind") != "show":
        return _strip(args, {"--tvdb-id", "--season"})
    tvdb_id = bgjobs.argument_value(args, "--tvdb-id") or ""
    season = bgjobs.argument_value(args, "--season") or ""
    episode = bgjobs.argument_value(args, "--episode") or ""
    if not (tvdb_id.isdigit() and season.isdigit() and episode.isdigit()):
        return None
    placed = tvdb_episode_to_anidb(table, int(tvdb_id), int(season), int(episode))
    if placed is None:
        return None
    return _with_entry(args, *placed)


def _anime_record(entry: Any) -> dict[str, Any]:
    from bankai.processor.naming import sanitise

    return {**entry.as_dict(), "folder": sanitise(entry.title)}


def _canonical_to_anidb(table: Any, canonical: str) -> tuple[Any, int] | None:
    parts = str(canonical).split("|")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tvdb_episode_to_anidb(table, int(parts[0]), int(parts[1]), int(parts[2]))


def migrate_state(state: dict[str, Any], table: Any, report: Counter) -> None:
    """Episodes, releases and tracked shows, in place."""
    anime = state.setdefault("anime", {})
    canonical = state.setdefault("canonical", {})
    tracked: dict[str, set[int]] = {}
    for key in list(canonical):
        placed = _canonical_to_anidb(table, key)
        if placed is None:
            if str(key).count("|") == 2 and not str(key).startswith("anidb:"):
                report["episodes left on TVDB keys (Anime-Lists cannot place them)"] += 1
            continue
        entry, episode = placed
        new = f"anidb:{entry.aid}|{episode}"
        row = canonical.pop(key)
        held = canonical.get(new)
        if held is None or int(row.get("resolution", 0)) > int(held.get("resolution", 0)):
            canonical[new] = row
        anime.setdefault(str(entry.aid), _anime_record(entry))
        tracked.setdefault(str(key).split("|", 1)[0], set()).add(entry.aid)
        report["episodes moved to AniDB keys"] += 1

    for release in (state.get("releases") or {}).values():
        old = str(release.get("canonical") or "")
        placed = _canonical_to_anidb(table, old)
        if placed is not None:
            entry, episode = placed
            release["canonical"] = f"anidb:{entry.aid}|{episode}"
            release["anidb_id"], release["episode"] = entry.aid, episode
            report["releases moved to AniDB keys"] += 1
        release.pop("tvdb_id", None)
        args = release.get("args")
        if isinstance(args, list) and "--tvdb-id" in args:
            converted = anidb_job_args(args, table)
            if converted is None:
                report["release arguments left on TVDB"] += 1
            else:
                release["args"] = converted
                report["release arguments moved to AniDB"] += 1
        entry_row = release.get("entry")
        if isinstance(entry_row, dict) and "tvdb" in entry_row:
            entry_row.pop("tvdb", None)

    # Tracked shows become their AniDB entries: the ones their episodes were,
    # else whatever their name is on AniDB.
    for tvdb_id, record in (state.pop("series", None) or {}).items():
        if tracked.get(str(tvdb_id)):
            report["tracked shows moved to AniDB"] += 1
            continue
        names = [record.get("english_title"), record.get("japanese_title"), *(record.get("aliases") or [])]
        found = None
        for name in names:
            if name:
                found = anidb_mod.resolve_in(table, str(name)).anime
                if found is not None:
                    break
        if found is None:
            report["tracked shows not found on AniDB"] += 1
            continue
        anime.setdefault(str(found.aid), _anime_record(found))
        report["tracked shows moved to AniDB"] += 1


def migrate_mappings(mappings: dict[str, Any], table: Any, report: Counter) -> None:
    for key, row in list(mappings.items()):
        if not row.get("tvdb_id"):
            continue
        if not str(row.get("anidb_id") or "").isdigit():
            season = row.get("season")
            offset = int(row.get("episode_offset") or 0)
            placed = None
            if str(season or "").isdigit():
                placed = tvdb_episode_to_anidb(table, int(row["tvdb_id"]), int(season), 1 + offset)
            if placed is None:
                regular = [
                    entry
                    for entry in table.by_tvdb.get(int(row["tvdb_id"]), [])
                    if (entry.tvdb_season or "").isdigit() and int(entry.tvdb_season) >= 1
                ]
                placed = (regular[0], 1 + offset) if len(regular) == 1 else None
            if placed is None:
                del mappings[key]
                report["saved choices dropped (no AniDB entry)"] += 1
                continue
            entry, first = placed
            row.update(anidb_id=entry.aid, anidb_title=entry.title, anidb_episode_offset=first - 1)
        mappings[key] = {name: value for name, value in row.items() if name not in _TVDB_MAPPING_FIELDS}
        report["saved choices moved to AniDB"] += 1


def migrate_policies(policies: dict[str, Any], table: Any, report: Counter) -> None:
    from bankai.web.erai import _entry_policy

    for row in list(policies.values()):
        tvdb_id = row.pop("tvdb_id", None)
        if not tvdb_id or str(row.get("anidb_id") or "").isdigit() or row.get("mode") != "blacklisted":
            continue
        # The whole TVDB show was blocked: each of its AniDB seasons is.
        for entry in table.by_tvdb.get(int(tvdb_id), []) if str(tvdb_id).isdigit() else []:
            if (entry.tvdb_season or "").isdigit() and int(entry.tvdb_season) >= 1:
                policies.setdefault(f"anidb:{entry.aid}", _entry_policy(entry, origin="library"))
                report["blacklist entries added for TVDB-wide decisions"] += 1


def migrate_prefs(prefs: dict[str, Any], report: Counter) -> None:
    for key in list(prefs):
        if key.startswith(("tvdb:", "key:")):
            del prefs[key]
            report["numbering choices dropped"] += 1


def _backup(path: Path, stamp: str) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak-anidb-{stamp}"))


def _write(path: Path, value: Any) -> None:
    tmp = path.with_suffix(".migrate.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def migrate(*, dry_run: bool = False) -> dict[str, int]:
    """The whole move; returns what it did, by kind."""
    from bankai.web import anime_library, erai, jobs

    table = await anidb_mod.index()
    if table is None:
        raise RuntimeError("AniDB titles are unavailable; nothing was changed")
    report: Counter = Counter()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with erai._STATE_LOCK:
        state = erai._load_state()
        mappings = erai._load_mappings()
        policies = erai._load_policies()
        prefs = anime_library.load_prefs()
        pending = jobs._load_pending()
        migrate_state(state, table, report)
        migrate_mappings(mappings, table, report)
        migrate_policies(policies, table, report)
        migrate_prefs(prefs, report)
        for job in pending:
            if job.args and job.args[0] == "anime-download" and "--tvdb-id" in job.args:
                converted = anidb_job_args(job.args, table)
                if converted is None:
                    report["queued jobs left on TVDB"] += 1
                else:
                    job.args = converted
                    report["queued jobs moved to AniDB"] += 1
        if dry_run:
            return dict(report)
        paths = {
            "state": erai._state_path(),
            "mappings": erai._state_path().with_name("erai_mappings.json"),
            "policies": erai._policy_path(),
            "prefs": anime_library._prefs_path(),
            "pending": jobs._pending_path(),
        }
        for path in paths.values():
            _backup(path, stamp)
        erai._save_state(state)
        _write(paths["mappings"], mappings)
        erai._save_policies(policies)
        _write(paths["prefs"], prefs)
        jobs._save_pending(pending)
    for kind, count in erai.reclassify_tvdb_holds(table).items():
        report[f"TVDB-era holds read again on AniDB: {kind}"] += count
    return dict(report)
