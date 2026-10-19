"""Cached AniDB titles and Anime-Lists, the two files AniDB identity is read from.

Anime-Lists records, for each AniDB entry, where it sits among the seasons of
the show it belongs to. That is how a sequel named by season number is found,
how the entries of one show are ordered, and how files filed by season are
read as AniDB episodes. It is an offline file; nothing here asks TVDB.
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
