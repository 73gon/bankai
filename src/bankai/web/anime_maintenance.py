"""Keeping the anime library's AniList data and layout current: a scheduler pass.

Every few minutes, a little at a time:

- the anime-offline-database, when its copy is a week old;
- AniList's records of every entry the library shows -- the members of each
  collection, the entries on disk, the entries the automation follows -- and
  of what they are related to, each refreshed weekly, a few batches a pass,
  so hundreds of entries are never asked for in one burst;
- entries on disk or followed that are in no collection yet joining one, and
  sequels AniList has announced since joining their show's collection;
- every collection whose layout changed since the last pass laid out again:
  renamed into place, NFO files written. What changed is told by a
  fingerprint of what the layout is made from, so a pass with nothing new
  costs a few hashes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import anilist, offline
from bankai.web import anime_collections, anime_files, anime_layout

log = get_logger(__name__)

_LOCK = asyncio.Lock()


def _fingerprints_path() -> Path:
    return bgjobs.jobs_root().parent / "anime_layout_fingerprints.json"


def _load_fingerprints() -> dict[str, str]:
    try:
        raw = json.loads(_fingerprints_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_fingerprints(rows: dict[str, str]) -> None:
    path = _fingerprints_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    tmp.replace(path)


def fingerprints(layout: anime_layout.Layout, published: set[str]) -> dict[str, str]:
    """What each collection's layout is made of, hashed: its folder, places, names, AniList data, files.

    And whether the disk has room for pictures: a collection laid out while it
    had none got no posters, so once there is room again it is laid out anew.
    """
    pictures = anime_layout.pictures_allowed()
    by_collection: dict[int, list[str]] = {}
    for rel, pairs in layout.rows.items():
        cid = layout.of.get(pairs[0][0])
        if cid is not None:
            by_collection.setdefault(cid, []).append(rel)
    result = {}
    for cid, row in layout.store.collections.items():
        plan = layout.plans[cid]
        media = {
            str(aid): {key: value for key, value in anilist.media(aid).items() if key != "fetched_at"}
            for aid in row.members
        }
        files = sorted((rel, rel in published) for rel in by_collection.get(cid, []))
        material = json.dumps(
            [layout.folders.get(cid), row.as_dict(), plan.seasons, plan.offsets, plan.specials, media, files, pictures],
            sort_keys=True,
            default=str,
        )
        result[str(cid)] = hashlib.sha1(material.encode("utf-8")).hexdigest()
    return result


def _published() -> set[str]:
    """The keys of the files in the library itself (not staging), from the held tree."""
    from bankai.web import library_walk

    root = Path(get_settings().transfer.anime_shows_dir)
    try:
        rows = library_walk.files([root])
    except OSError:
        return set()
    return {key for row in rows if (key := anime_files.key(row["path"]))}


def _followed() -> set[int]:
    from bankai.web import erai

    state = erai._load_state()
    blocked = erai._policy_anilist_ids(erai._load_policies())
    return {int(aid) for aid in state.get("anime") or {} if str(aid).isdigit()} - blocked


async def run(*, max_batches: int = 4, layout: bool = True) -> dict[str, Any]:
    """One pass; returns what it did."""
    if _LOCK.locked():
        return {"skipped": "a pass is running"}
    async with _LOCK:
        renewed = await offline.refresh()
        store = await asyncio.to_thread(anime_collections.load)
        rows = await asyncio.to_thread(anime_files.load)
        followed = await asyncio.to_thread(_followed)
        on_disk = {aid for pairs in rows.values() for aid, _episode in pairs}
        wanted = set(store.of()) | on_disk | followed
        fetched = await anilist.ensure(wanted, max_batches=max_batches)
        near = {other for aid in wanted for other, _kind, _form in anilist.relations(aid)} - wanted
        fetched += await anilist.ensure(near, refresh=False, max_batches=2)

        def regroup() -> set[int]:
            with anime_collections.editing() as current:
                loose = sorted((on_disk | followed) - set(current.of()))
                changed = anime_collections.join(loose, current) | anime_collections.extend(current)
                for cid in changed:
                    if cid in current.collections:
                        anime_collections.settle_specials(current.collections[cid])
                return changed

        grown = await asyncio.to_thread(regroup)
        result: dict[str, Any] = {
            "offline_renewed": renewed,
            "fetched": fetched,
            "collections_grown": len(grown),
        }
        if not layout:
            return result
        store = await asyncio.to_thread(anime_collections.load)
        rows = await asyncio.to_thread(anime_files.load)
        built = await asyncio.to_thread(anime_layout.Layout.build, store, rows)
        published = await asyncio.to_thread(_published)
        now = await asyncio.to_thread(fingerprints, built, published)
        before = await asyncio.to_thread(_load_fingerprints)
        due = {int(cid) for cid, value in now.items() if before.get(cid) != value}
        if due:
            outcome = await anime_layout.relayout(due, label="maintenance")
            result.update(
                laid_out=len(due), moved=outcome["moved"], nfo_written=outcome["nfo_written"],
                pictures=outcome["pictures"],
            )
            if outcome["moved"]:
                log.info("anime layout: %d files moved into place", outcome["moved"])
            # Taken again: the layout itself moved files and wrote what they are.
            store = await asyncio.to_thread(anime_collections.load)
            rows = await asyncio.to_thread(anime_files.load)
            built = await asyncio.to_thread(anime_layout.Layout.build, store, rows)
            now = await asyncio.to_thread(fingerprints, built, published)
        await asyncio.to_thread(_save_fingerprints, now)
        return result


__all__ = ["fingerprints", "run"]
