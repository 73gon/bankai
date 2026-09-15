"""Cached AniDB titles, Anime-Lists episode offsets, and TheXEM scene maps.

Title identity and episode identity are separate. An exact AniDB title selects
its own series/part; its mapping then selects a TVDB episode, never an ordinal
guess into a similarly named parent series.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from bankai.cli import bgjobs

_TITLES_URL = "https://anidb.net/api/anime-titles.xml.gz"
_LIST_URL = "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/anime-list.xml"
_XEM = "https://thexem.info/map/"
_CACHE: dict[str, tuple[float, Any]] = {}
_LOCK = asyncio.Lock()
_TTL = 86400
_TITLE_INDEX: tuple[tuple[int, int], dict[str, list[Part]]] | None = None


def normalise(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold().replace("_", " "), re.UNICODE))


async def _resource(name: str, url: str, *, params: dict | None = None) -> Any:
    hit = _CACHE.get(name)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    async with _LOCK:
        hit = _CACHE.get(name)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        path = bgjobs.jobs_root().parent / "anime_metadata" / name
        raw = None
        if path.exists():
            raw = path.read_bytes()
        if raw is None or time.time() - path.stat().st_mtime >= _TTL:
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    incoming = response.content
                    # Validate before replacing the last usable cache.
                    if name.endswith(".json"):
                        payload = json.loads(incoming)
                        if payload.get("result") != "success":
                            raise ValueError("TheXEM returned no mapping")
                    else:
                        ET.fromstring(
                            gzip.decompress(incoming) if name.endswith(".gz") else incoming
                        )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(".part")
                    temporary.write_bytes(incoming)
                    temporary.replace(path)
                    raw = incoming
            except (httpx.HTTPError, ValueError, ET.ParseError, OSError):
                if raw is None:
                    _CACHE[name] = (time.time(), None)
                    return None
        if name.endswith(".json"):
            value = json.loads(raw).get("data")
        else:
            value = ET.fromstring(gzip.decompress(raw) if name.endswith(".gz") else raw)
        _CACHE[name] = (time.time(), value)
        return value


@dataclass(frozen=True)
class Part:
    tvdb_id: int
    anidb_id: int
    record: ET.Element


async def anidb_parts(title: str) -> list[Part]:
    names, records = await asyncio.gather(
        _resource("anime-titles.xml.gz", _TITLES_URL),
        _resource("anime-list.xml", _LIST_URL),
    )
    if records is None:
        return []
    global _TITLE_INDEX
    revision = (id(names), id(records))
    if _TITLE_INDEX is None or _TITLE_INDEX[0] != revision:
        records_by_id = {
            record.get("anidbid"): record
            for record in records
            if (record.get("tvdbid") or "").isdigit()
        }
        index: dict[str, list[Part]] = {}
        for aid, record in records_by_id.items():
            part = Part(int(record.get("tvdbid")), int(aid), record)
            index.setdefault(normalise(record.findtext("name", "")), []).append(part)
        for anime in names if names is not None else []:
            record = records_by_id.get(anime.get("aid"))
            if record is None:
                continue
            part = Part(int(record.get("tvdbid")), int(record.get("anidbid")), record)
            for node in anime.findall("title"):
                key = normalise(node.text or "")
                if part not in index.setdefault(key, []):
                    index[key].append(part)
        _TITLE_INDEX = (revision, index)
    return _TITLE_INDEX[1].get(normalise(title), [])


async def related_titles(tvdb_id: int) -> list[str]:
    records = await _resource("anime-list.xml", _LIST_URL)
    if records is None:
        return []
    regular = [
        record
        for record in records
        if record.get("tvdbid") == str(tvdb_id)
        and (
            record.get("defaulttvdbseason") == "a"
            or (
                (record.get("defaulttvdbseason") or "").isdigit()
                and int(record.get("defaulttvdbseason")) > 0
            )
        )
    ]
    regular.sort(
        key=lambda record: (
            0 if record.get("defaulttvdbseason") == "a" else int(record.get("defaulttvdbseason")),
            int(record.get("episodeoffset") or 0),
        )
    )
    return list(
        dict.fromkeys(record.findtext("name") for record in regular if record.findtext("name"))
    )


async def scene_names(title: str) -> list[tuple[int, int]]:
    names = await _resource(
        "xem_names.json",
        _XEM + "allNames",
        params={"origin": "tvdb", "seasonNumbers": "true"},
    )
    result = set()
    for tvdb_id, aliases in (names or {}).items():
        for entry in aliases:
            for alias, season in entry.items():
                if normalise(alias) == normalise(title) and str(season).lstrip("-").isdigit():
                    result.add((int(tvdb_id), int(season)))
    return sorted(result)


async def mapped_series_ids(title: str) -> list[int]:
    parts = await anidb_parts(title)
    if parts:
        return sorted({part.tvdb_id for part in parts})
    return sorted({tvdb_id for tvdb_id, _ in await scene_names(title)})


def map_part(part: Part, number: int, episodes: list) -> tuple[int, int] | None:
    targets = set()
    matched = False
    for rule in part.record.findall("./mapping-list/mapping"):
        if rule.get("anidbseason") != "1" or not (rule.get("tvdbseason") or "").isdigit():
            continue
        season = int(rule.get("tvdbseason"))
        for source, target in re.findall(r"(?:^|;)(\d+)-([\d+]+)(?=;|$)", rule.text or ""):
            if int(source) == number:
                matched = True
                # Split episodes require multi-episode import support; fail closed.
                if "+" in target or int(target) <= 0:
                    return None
                targets.add((season, int(target)))
        start, end = rule.get("start"), rule.get("end")
        if start and end and int(start) <= number <= int(end):
            matched = True
            targets.add((season, number + int(rule.get("offset") or 0)))
    if not matched:
        season = part.record.get("defaulttvdbseason", "")
        offset = int(part.record.get("episodeoffset") or 0)
        if season == "a":
            targets.update(
                (item.season, item.episode)
                for item in episodes
                if item.absolute_number == number + offset and item.season > 0
            )
        elif season.isdigit() and int(season) > 0:
            targets.add((int(season), number + offset))
    if len(targets) != 1:
        return None
    target = next(iter(targets))
    return target if any((item.season, item.episode) == target for item in episodes) else None


async def mapped_episode(
    title: str,
    number: int,
    tvdb_id: int,
    episodes: list,
) -> tuple[tuple[int, int] | None, bool]:
    """Return target and whether a published title/part mapping exists."""
    parts = [part for part in await anidb_parts(title) if part.tvdb_id == tvdb_id]
    if parts:
        targets = {map_part(part, number, episodes) for part in parts}
        return (next(iter(targets)) if len(targets) == 1 else None), True
    names = [season for mapped_id, season in await scene_names(title) if mapped_id == tvdb_id]
    if not names:
        return None, False
    rows = await _resource(
        f"xem_{tvdb_id}.json",
        _XEM + "all",
        params={"origin": "tvdb", "id": tvdb_id},
    )
    targets = set()
    for row in rows or []:
        scene, target = row.get("scene") or {}, row.get("tvdb") or {}
        if (int(scene.get("season", -1)) in names and int(scene.get("episode", -1)) == number) or (
            -1 in names and int(scene.get("absolute", -1)) == number
        ):
            targets.add((int(target.get("season", 0)), int(target.get("episode", 0))))
    if len(targets) != 1:
        return None, True
    target = next(iter(targets))
    return (
        target if any((item.season, item.episode) == target for item in episodes) else None
    ), True
