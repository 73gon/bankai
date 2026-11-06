"""What a magnet link is, before it replaces anything: the torrent's name, size and files, read from
the swarm by qBittorrent (``torrents/fetchMetadata``, qBittorrent 5.2: nothing is added or
downloaded), what bankai already knows of it (Erai-raws' listing, a release it had), and which of
the ticked library files each of its files would take the place of.

The matching is the replace's own: anime by the torrent file's episode number against the ticked
file's SxxEyy (``erai.replace_from_magnet`` maps the torrent's episode N onto SxxE<N>); Movies &
Shows by SxxEyy on both sides; a film by the torrent's largest video.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path, PurePosixPath
from typing import Any

from bankai.logging import get_logger

log = get_logger(__name__)

VIDEO_SUFFIXES = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts"})


async def metadata(magnet: str, *, wait: float = 12.0) -> dict[str, Any] | None:
    """The torrent's metadata as qBittorrent fetched it, or None while the swarm has not answered."""
    from bankai.torrent.qbittorrent import QBittorrentClient

    deadline = time.monotonic() + wait
    async with QBittorrentClient() as client:
        await client._ensure_login()
        while True:
            response = await client._client.post("/api/v2/torrents/fetchMetadata", data={"source": magnet})
            if response.status_code == 200:
                return response.json()
            if response.status_code != 202:
                raise RuntimeError(f"qBittorrent answered {response.status_code}: {response.text[:200]}")
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(1.5)


def files_of(meta: dict[str, Any]) -> list[dict[str, Any]]:
    info = meta.get("info") or {}
    files = info.get("files") or ([{"path": info.get("name") or "", "length": info.get("length") or 0}] if info.get("name") else [])
    return [{"path": str(row.get("path") or ""), "size": int(row.get("length") or 0)} for row in files]


def known(info_hash: str) -> dict[str, Any] | None:
    """What bankai knows of this torrent already: Erai-raws' listing (subtitles, resolution), a release it had."""
    from bankai.web import erai, erai_site

    out: dict[str, Any] = {}
    listed = erai_site.lookup(info_hash)
    if listed:
        subs = list(listed.get("subs") or [])
        out["erai"] = {
            "name": listed.get("name") or listed.get("title"),
            "subtitles": [erai_site.LANGUAGES.get(code, code) for code in subs],
            "german": erai_site.GERMAN in subs,
            "resolution": listed.get("res") or None,
            "published": listed.get("published") or None,
        }
    release = (erai._load_state().get("releases") or {}).get(info_hash)
    if release:
        out["release"] = {"status": release.get("status"), "title": release.get("display_title") or release.get("title")}
    return out or None


def plan(files: list[dict[str, Any]], paths: list[str], scope: str) -> list[dict[str, Any]]:
    """Each of the torrent's files with the ticked file it would replace (``replaces``), or why it stays out."""
    from bankai.processor.anime import episode_number
    from bankai.torrent.matcher import parse_se
    from bankai.web import direct

    rows = [{**row, "name": PurePosixPath(row["path"]).name} for row in files]
    videos = [row for row in rows if PurePosixPath(row["path"]).suffix.casefold() in VIDEO_SUFFIXES]
    if scope == "movie":
        largest = max(videos, key=lambda row: row["size"], default=None)
        for row in rows:
            row["replaces"] = Path(paths[0]).name if row is largest and paths else None
            if row is not largest:
                row["why"] = "not a video" if row not in videos else "not the film's largest video"
        return rows
    wanted: dict[Any, str] = {}
    for raw in paths:
        name = Path(raw).name
        if scope == "anime":
            found = parse_se(name)
            if found:
                # A file of two episodes ("S01E01-E02") holds both numbers.
                span = direct.file_episode(name)
                last = span[2] if span and span[2] else found[1]
                for number in range(found[1], last + 1):
                    wanted[number] = name
        else:
            span = direct.file_episode(name)
            if span:
                for number in range(span[1], (span[2] or span[1]) + 1):
                    wanted[(span[0], number)] = name
    for row in rows:
        if row not in videos:
            row.update(replaces=None, why="not a video")
            continue
        if scope == "anime":
            key: Any = episode_number(row["name"])
            row["episode"] = key
        else:
            span = direct.file_episode(row["name"])
            key = (span[0], span[1]) if span else None
            row["episode"] = f"S{span[0]:02d}E{span[1]:02d}" if span else None
        row["replaces"] = wanted.get(key) if key is not None else None
        if row["replaces"] is None:
            row["why"] = "no episode number in its name" if key is None else "not one of the ticked episodes"
    return rows


async def preview(magnet: str, paths: list[str], scope: str) -> dict[str, Any]:
    from bankai.web import erai

    info_hash, name = erai.magnet_parts(magnet)
    out: dict[str, Any] = {"info_hash": info_hash, "name": name, "known": await asyncio.to_thread(known, info_hash)}
    meta = await metadata(magnet.strip())
    if meta is None:
        return {**out, "pending": True}
    files = files_of(meta)
    rows = await asyncio.to_thread(plan, files, paths, scope)
    # What would replace something first, in episode order; then the rest by name.
    rows.sort(key=lambda row: (row.get("replaces") is None, str(row.get("episode") or "").zfill(6), row["name"]))
    covered = {row["replaces"] for row in rows if row.get("replaces")}
    return {
        **out,
        "pending": False,
        "name": (meta.get("info") or {}).get("name") or name,
        "size": sum(row["size"] for row in files),
        "private": bool((meta.get("info") or {}).get("private")),
        "trackers": len(meta.get("trackers") or []),
        "files": rows,
        "ticked": len(paths),
        "covered": len(covered),
        "uncovered": [Path(raw).name for raw in paths if Path(raw).name not in covered],
    }
