"""Show-level Anime library presentation and cached TVDB artwork identity."""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from contextlib import suppress
from dataclasses import asdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from bankai.cli import bgjobs
from bankai.processor.anime import _tvdb_episode_map
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


async def episode_roster(tvdb_id: int) -> list:
    key = f"episodes:{tvdb_id}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    rows = await _tvdb_episode_map(tvdb_id)
    _CACHE[key] = (time.time(), rows)
    return rows


def merge_episodes(files: list[dict], roster: list, *, ended: bool) -> dict:
    """Count unique regular episodes, using final files as downloaded evidence."""
    by_number = {}
    others = []
    for row in files:
        key = (row["season_number"], row["episode"])
        if None in key:
            others.append(row)
        elif key not in by_number or by_number[key]["staged"]:
            by_number[key] = row
    today = date.today().isoformat()
    for item in roster:
        key = (item.season, item.episode)
        if item.season < 1 or item.episode < 1:
            continue
        future = bool(item.aired and item.aired[:10] > today)
        tba = future or (not item.aired and not ended)
        if key in by_number:
            by_number[key] = {
                **by_number[key],
                "episode_title": item.name,
                "aired": item.aired,
                "tba": False,
                "missing": False,
            }
        else:
            by_number[key] = {
                "path": "",
                "rel_path": "",
                "name": item.name or "TBA",
                "episode_title": item.name,
                "series": "",
                "season": f"Season {item.season:02d}",
                "season_number": item.season,
                "episode": item.episode,
                "size": 0,
                "mtime": 0,
                "staged": False,
                "stage": "missing",
                "transfer_status": "idle",
                "aired": item.aired,
                "tba": tba,
                "missing": True,
            }
    regular = [row for (season, _), row in by_number.items() if season > 0]
    downloaded = sum(not row.get("missing", False) and not row["staged"] for row in regular)
    outstanding = [row for row in regular if row.get("missing", False) or row["staged"]]
    future_only = outstanding and all(row.get("tba", False) for row in outstanding)
    state = (
        "empty"
        if downloaded == 0
        else "upcoming"
        if future_only
        else "partial"
        if outstanding
        else "complete"
    )
    # Do not imply full TVDB completeness when metadata is unavailable.
    if not roster and downloaded:
        state = "unknown"
    return {
        "episodes": sorted(
            [*by_number.values(), *others],
            key=lambda row: (row["season_number"] or 0, row["episode"] or 0, row["name"]),
        ),
        "downloaded_count": downloaded,
        "total_count": len(regular),
        "completion_state": state,
        "finished": ended and state == "complete",
        "metadata_available": bool(roster),
    }


async def search_episode(tvdb_id: int, season: int, episode: int, query: str | None = None) -> dict:
    """Search scene names in reverse, then verify every result's TVDB target."""
    from dataclasses import replace

    import httpx

    from bankai.metadata import anime_mapping

    match = await anime.series_metadata(tvdb_id)
    roster = await episode_roster(tvdb_id)
    if not any((row.season, row.episode) == (season, episode) for row in roster):
        raise ValueError("Episode does not exist in TVDB ordering")
    terms = [query] if query else []
    if not terms:
        for title in await anime_mapping.related_titles(tvdb_id):
            parts = [
                part for part in await anime_mapping.anidb_parts(title) if part.tvdb_id == tvdb_id
            ]
            for part in parts:
                for number in range(1, len(roster) + 1):
                    if anime_mapping.map_part(part, number, roster) == (season, episode):
                        terms.append(f'"{title}" "- {number:02d}"')
                        break
        target = next(row for row in roster if (row.season, row.episode) == (season, episode))
        number = target.absolute_number or episode
        terms.extend(
            f'"{title}" "- {number:02d}"'
            for title in [match.english_title, match.japanese_title]
            if title
        )
    terms = list(dict.fromkeys(terms))[:6]
    state = await asyncio.to_thread(erai._load_state)
    indexed = [
        erai._entry_from_dict(row)
        for index in [state["backfill"], *state["series_catalogs"].values()]
        for field in ["catalog_1080", "catalog_720"]
        for row in index.get(field, {}).values()
    ]
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": anime.get_settings().scraper.user_agent},
    ) as client:
        batches = await asyncio.gather(
            *(anime._fetch_rss(client, term, "1_2", 0) for term in terms), return_exceptions=True
        )
        by_hash = {row.info_hash: row for row in indexed}
        for batch in batches:
            if isinstance(batch, list):
                by_hash.update({row.info_hash: row for row in batch})
        items = []
        # Avoid TVDB lookups for unrelated catalogue titles.
        aliases = {
            anime_mapping.normalise(value)
            for value in [
                match.english_title,
                match.japanese_title or "",
                *match.aliases,
                *(await anime_mapping.related_titles(tvdb_id)),
            ]
        }
        for entry in by_hash.values():
            clean = anime.clean_release_title(entry.title)
            if anime_mapping.normalise(clean) not in aliases and not query:
                continue
            _, number = anime.release_episode_info(entry.title)
            if number is None:
                continue
            resolved, identity, _ = await erai._resolve(entry, episodes=roster)
            if (
                not resolved
                or resolved.tvdb_id != tvdb_id
                or not identity
                or (identity.season, identity.episode) != (season, episode)
            ):
                continue
            items.append(
                anime.entry_to_dict(replace(entry, tvdb=match, season=season, episode=episode))
            )
    return {
        "items": sorted(
            items, key=lambda row: (-erai._resolution(erai._entry_from_dict(row)), -row["seeders"])
        ),
        "queries": terms,
        "match": asdict(match),
    }


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
    tracked = (await asyncio.to_thread(erai._load_state)).get("series", {})
    names = {_name(title) for title in groups}
    for record in tracked.values():
        title = record.get("english_title")
        if title and _name(title) not in names:
            groups[title] = []
            names.add(_name(title))
    slots = asyncio.Semaphore(6)

    async def build(title: str, files: list[dict]) -> dict:
        tvdb_id = ids.get(_name(title)) or await asyncio.to_thread(_nfo_id, root / title)
        async with slots:
            metadata = await show_metadata(title, tvdb_id)
            tvdb_id = metadata.get("tvdb_id") or tvdb_id
            roster = []
            if tvdb_id and discover.is_configured():
                with suppress(Exception):
                    roster = await episode_roster(tvdb_id)
        merged = merge_episodes(
            files, roster, ended=str(metadata.get("status", "")).casefold() == "ended"
        )
        return {
            "key": title,
            "title": metadata.get("english_title") or title,
            "tvdb_id": tvdb_id,
            "year": metadata.get("year"),
            "poster_url": metadata.get("poster_url"),
            "episode_count": len(files),
            "season_count": len(
                {
                    row["season_number"]
                    for row in merged["episodes"]
                    if row["season_number"] is not None
                }
            ),
            "size": sum(row["size"] for row in files),
            "staged_count": sum(row["staged"] for row in files),
            **merged,
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
