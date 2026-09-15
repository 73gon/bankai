"""Show-level Anime library presentation and cached TVDB artwork identity."""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from bankai.cli import bgjobs
from bankai.torrent.matcher import parse_se
from bankai.web import anime, discover, erai

_CACHE: dict[str, tuple[float, dict]] = {}


def _name(value: str) -> str:
    return re.sub(r"\s*[\[(]\d{4}[\])]\s*$", "", value).strip().casefold()


async def show_metadata(title: str, tvdb_id: int | None = None) -> dict:
    key = f"id:{tvdb_id}" if tvdb_id else _name(title)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    metadata = {}
    if discover.is_configured():
        try:
            if tvdb_id:
                metadata = asdict(await anime.series_metadata(tvdb_id))
            else:
                candidates = await anime.tvdb_candidates(re.sub(r"\s*\(\d{4}\)$", "", title))
                exact = [
                    item
                    for item in candidates
                    if item.kind == "show"
                    and _name(title)
                    in {
                        _name(item.english_title),
                        _name(item.japanese_title or ""),
                        *(_name(alias) for alias in item.aliases),
                    }
                ]
                if len(exact) == 1:
                    metadata = asdict(exact[0])
        except Exception:
            pass  # Library browsing remains available during provider outages.
    _CACHE[key] = (time.time(), metadata)
    return metadata


def known_ids() -> dict[str, int]:
    state = erai._load_state()
    ids = {}
    for record in [*state.get("series", {}).values(), *erai._load_mappings().values()]:
        if record.get("english_title") and record.get("tvdb_id"):
            ids[_name(record["english_title"])] = int(record["tvdb_id"])
    for job in bgjobs.list_jobs():
        if not job.args or job.args[0] != "anime-download":
            continue
        title = bgjobs.argument_value(job.args, "--english-title")
        tvdb_id = bgjobs.argument_value(job.args, "--tvdb-id")
        if title and tvdb_id and tvdb_id.isdigit():
            ids[_name(title)] = int(tvdb_id)
    return ids


def _nfo_id(folder: Path) -> int | None:
    path = folder / "tvshow.nfo"
    try:
        tree = ET.fromstring(path.read_bytes())
        raw = tree.findtext("tvdbid")
        if not raw:
            raw = next(
                (node.text for node in tree.findall("uniqueid") if node.get("type") == "tvdb"),
                None,
            )
        return int(raw) if raw and raw.isdigit() else None
    except (OSError, ET.ParseError):
        return None


async def group_shows(entries: list[dict], root: Path) -> list[dict]:
    groups = defaultdict(list)
    for entry in entries:
        identity = parse_se(entry["name"])
        groups[entry["series"]].append(
            {
                **entry,
                "season_number": identity[0] if identity else None,
                "episode": identity[1] if identity else None,
            }
        )
    ids = await asyncio.to_thread(known_ids)
    slots = asyncio.Semaphore(6)

    async def build(title: str, episodes: list[dict]) -> dict:
        tvdb_id = ids.get(_name(title)) or await asyncio.to_thread(_nfo_id, root / title)
        async with slots:
            metadata = await show_metadata(title, tvdb_id)
        episodes.sort(key=lambda row: (row["season_number"] or 0, row["episode"] or 0, row["name"]))
        return {
            "key": title,
            "title": metadata.get("english_title") or title,
            "tvdb_id": metadata.get("tvdb_id") or tvdb_id,
            "year": metadata.get("year"),
            "poster_url": metadata.get("poster_url"),
            "episode_count": len(episodes),
            "season_count": len(
                {row["season_number"] for row in episodes if row["season_number"] is not None}
            ),
            "size": sum(row["size"] for row in episodes),
            "staged_count": sum(row["staged"] for row in episodes),
            "episodes": episodes,
        }

    shows = await asyncio.gather(*(build(title, episodes) for title, episodes in groups.items()))
    return sorted(shows, key=lambda row: row["title"].casefold())


async def queue_covers(rows: list[dict]) -> list[dict]:
    slots = asyncio.Semaphore(6)

    async def enrich(row: dict) -> None:
        raw = str(row.get("tvdb_id") or "")
        if not raw.isdigit():
            return
        async with slots:
            metadata = await show_metadata(row["title"], int(raw))
        row["poster_url"] = metadata.get("poster_url")
        row["series_title"] = metadata.get("english_title")

    await asyncio.gather(*(enrich(row) for row in rows))
    return rows
