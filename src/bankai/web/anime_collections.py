"""Collections: the AniDB entries of one show, kept together as one library card.

AniDB has an entry per season, or per cour, so a show of four seasons is four
entries -- four cards, four shows in Jellyfin. A collection puts them back
together. It is a Shoko group: Shoko already keeps a show's seasons in one,
and Jellyfin shows them through Shokofin, so the library and the player agree.

What joins a collection on its own is AniDB's main line: sequels and
prequels, followed through entries that are not on disk, so a special filed
between two seasons does not break the chain. Side stories, movies and
recaps stay out unless Shoko already grouped them. Groups as Shoko made them
are kept: the entries placed here are only the ones it does not hold yet.

The relation graph is where that is changed by hand: an entry split off into
a collection of its own, two collections merged, a side story pulled in.
Shoko holds what is on disk, so those moves are Shoko's; for an entry not on
disk yet the choice is kept here, and handed to Shoko once it is.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAIN_LINE = frozenset({"Sequel", "Prequel"})

# How each AniDB relation reads as one edge of the graph: (label, flipped).
# "Prequel" is the same link as "Sequel" read from the other end, and so on;
# flipped ones are turned round so each link is drawn once, in one direction.
_EDGES = {
    "Sequel": ("sequel", False),
    "Prequel": ("sequel", True),
    "SideStory": ("side story", False),
    "MainStory": ("side story", True),
    "Summary": ("summary", False),
    "FullStory": ("summary", True),
    "AlternativeSetting": ("alternative setting", False),
    "AlternativeVersion": ("alternative version", False),
    "SameSetting": ("same setting", False),
    "SharedCharacters": ("shared characters", False),
    "Character": ("shared characters", False),
    "Other": ("other", False),
}
_UNDIRECTED = {"alternative setting", "alternative version", "same setting", "shared characters", "other"}


@dataclass
class Collections:
    # AniDB id -> the Shoko group it belongs to: its own if Shoko holds it,
    # else the group of the nearest held entry along the main line.
    of: dict[int, int] = field(default_factory=dict)
    # Entries taken out of every collection by hand, and so out of the chain.
    alone: set[int] = field(default_factory=set)

    def members(self, group_id: int) -> list[int]:
        return sorted(aid for aid, group in self.of.items() if group == group_id)


def main_line(relations: list[tuple[int, int, str]]) -> dict[int, set[int]]:
    """Sequel and prequel links, both ways: the chain of a show's seasons."""
    graph: dict[int, set[int]] = {}
    for left, right, kind in relations:
        if kind in MAIN_LINE and left != right:
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)
    return graph


# -- Choices for entries Shoko does not hold -----------------------------------


def _places_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "anime_collection_places.json"


def load_places() -> dict[int, int | None]:
    """AniDB id -> the collection chosen for it, or None: in none."""
    try:
        raw = json.loads(_places_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        int(aid): (int(group) if group is not None else None)
        for aid, group in (raw.items() if isinstance(raw, dict) else ())
        if str(aid).isdigit() and (group is None or str(group).isdigit())
    }


def _save_places(places: dict[int, int | None]) -> None:
    path = _places_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({str(aid): group for aid, group in sorted(places.items())}), encoding="utf-8")
    tmp.replace(path)


def place(aids: list[int], group_id: int | None) -> None:
    """Keep entries not on disk in a collection, or (None) in none."""
    places = load_places()
    for aid in aids:
        places[int(aid)] = group_id
    _save_places(places)


def assign(catalog: Any, places: dict[int, int | None] | None = None) -> Collections:
    """Every entry Shoko holds, and every main-line relative of one, by collection.

    Held entries keep the group Shoko put them in. An entry not on disk goes
    where it was placed by hand, if it was; the rest are reached from those
    breadth-first, so an entry between two collections joins the nearer, at
    equal distance the older group (lower id), for an answer that does not
    change from one page load to the next. An entry taken out of every
    collection is out of the chain too: what is reached only through it --
    the prequels behind a prequel cut off -- goes with it.
    """
    found = Collections()
    if catalog is None or not getattr(catalog, "groups", None):
        return found
    for aid in sorted(catalog.entries):
        group = catalog.entries[aid].group_id
        if group is not None:
            found.of[aid] = group
    for aid, group in (places or {}).items():
        if aid in found.of:
            continue  # on disk: Shoko's group says where it is
        if group is None:
            found.alone.add(aid)
        elif group in catalog.groups:
            found.of[aid] = group
    graph = main_line(catalog.relations)
    queue: deque[int] = deque(sorted(found.of, key=lambda aid: (found.of[aid], aid)))
    while queue:
        aid = queue.popleft()
        for other in sorted(graph.get(aid, ())):
            if other not in found.of and other not in found.alone:
                found.of[other] = found.of[aid]
                queue.append(other)
    return found


# -- The relation graph of one collection --------------------------------------


def graph(catalog: Any, group_id: int, *, table: Any = None, places: dict[int, int | None] | None = None) -> dict:
    """One collection as nodes and edges: its entries, and the ones related to them.

    The collection's own entries are the nodes it is made of. Every entry an
    AniDB relation ties to one of them, but which is not in it, is a
    suggestion: a side story, a recap, a sequel kept in another collection.
    """
    from bankai.metadata import anidb_art

    found = assign(catalog, places)
    group = catalog.groups.get(group_id) if catalog is not None else None
    if group is None:
        raise ValueError("Shoko has no such collection")
    members = set(found.members(group_id))
    related: dict[int, list[tuple[int, str]]] = {}
    for left, right, kind in catalog.relations:
        related.setdefault(left, []).append((right, kind))
        related.setdefault(right, [])
    suggestions = {
        other for aid in members for other, _kind in related.get(aid, ()) if other not in members
    }
    on_disk: dict[int, set[int]] = {}
    for links in catalog.files.values():
        for aid, number in links:
            on_disk.setdefault(aid, set()).add(number)
    known = getattr(table, "anime", {}) if table is not None else {}

    def node(aid: int) -> dict:
        entry = catalog.entries.get(aid)
        anime = known.get(aid)
        facts = anidb_art.facts(aid)
        aired = [ep.aired for ep in (entry.episodes if entry else ()) if ep.aired]
        other = found.of.get(aid)
        return {
            "anidb_id": aid,
            "title": (entry.title if entry else None)
            or ((anime.english_title or anime.title) if anime else f"AniDB {aid}"),
            "main_title": anime.title if anime else None,
            "type": facts.get("type"),
            "year": int(min(aired)[:4]) if aired else facts.get("year"),
            "first_aired": min(aired) if aired else (str(facts["year"]) if facts.get("year") else None),
            "episodes": len(entry.episodes) if entry and entry.episodes else facts.get("episodes"),
            "on_disk": len(on_disk.get(aid, ())),
            "owned": entry is not None,
            "poster_url": (entry.poster_url if entry else None) or anidb_art.cover(aid),
            "in_collection": aid in members,
            "title_entry": aid == group.main_aid,
            # Where a suggestion is now: another collection, or none.
            "collection_id": other,
            "collection_name": catalog.groups[other].name if other in catalog.groups else None,
            "alone": aid in found.alone,
        }

    nodes = [node(aid) for aid in sorted(members | suggestions)]
    seen: set[tuple] = set()
    edges = []
    for left, right, kind in catalog.relations:
        if left not in members and right not in members:
            continue
        if left not in members | suggestions or right not in members | suggestions:
            continue
        label, flipped = _EDGES.get(kind, (kind.lower(), False))
        source, target = (right, left) if flipped else (left, right)
        key = (label, *sorted((source, target))) if label in _UNDIRECTED else (label, source, target)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"from": source, "to": target, "label": label, "main_line": kind in MAIN_LINE})
    return {
        "collection_id": group_id,
        "name": group.name,
        "title_entry": group.main_aid,
        "nodes": nodes,
        "edges": edges,
    }


# -- Changing a collection in Shoko --------------------------------------------
#
# Every change is made to Shoko's group, which is what Jellyfin shows. Before
# each change every group and which series it holds is written down, so a
# change that turns out wrong can be put back by hand.

_SNAPSHOT_KEEP = 50


def _snapshot_dir() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "shoko_groups"


async def snapshot() -> Path:
    """Write down every Shoko group and its series; returns the file."""
    from bankai.web import shoko

    groups = await shoko._get("/api/v3/Group", pageSize=0, includeEmpty="true")
    series = await shoko._get("/api/v3/Series", pageSize=0)
    rows = groups.get("List", groups) if isinstance(groups, dict) else groups
    members = series.get("List", series) if isinstance(series, dict) else series
    record = {
        "taken_at": time.time(),
        "groups": [
            {"ids": row.get("IDs"), "name": row.get("Name"), "has_custom_name": row.get("HasCustomName")}
            for row in rows or []
        ],
        "series": [
            {"ids": row.get("IDs"), "name": row.get("Name")} for row in members or []
        ],
        "places": {str(aid): group for aid, group in load_places().items()},
    }
    folder = _snapshot_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("groups-%Y%m%d-%H%M%S.json")
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    for old in sorted(folder.glob("groups-*.json"))[:-_SNAPSHOT_KEEP]:
        old.unlink(missing_ok=True)
    return path


async def set_title(group_id: int, series_id: int) -> dict:
    """Name and picture a collection after one of its series, as Shoko's preferred series.

    Returns the group as Shoko has it afterwards.
    """
    from bankai.web import shoko

    await snapshot()
    result = await shoko._send(
        "PATCH",
        f"/api/v3/Group/{int(group_id)}",
        json_body=[{"op": "replace", "path": "/PreferredSeriesID", "value": int(series_id)}],
    )
    if result is None:
        raise ValueError("Shoko has no such group")
    return await shoko._get(f"/api/v3/Group/{int(group_id)}")


def _owned(catalog: Any, aids: list[int]) -> tuple[list[Any], list[int]]:
    held = [catalog.entries[aid] for aid in aids if aid in catalog.entries]
    return held, [aid for aid in aids if aid not in catalog.entries]


async def split(catalog: Any, group_id: int, aids: list[int]) -> int | None:
    """Take entries out of a collection into one of their own; returns its Shoko group.

    The ones on disk become a new Shoko group together; the ones not on disk
    go with them, or -- split off on their own -- out of every collection.
    """
    from bankai.web import shoko

    held, rest = _owned(catalog, aids)
    if any(entry.group_id != group_id for entry in held):
        raise ValueError("an entry is not in this collection")
    new_group = None
    if held:
        await snapshot()
        created = await shoko._send(
            "POST", "/api/v3/Group", json_body={"SeriesIDs": [entry.series_id for entry in held]}
        )
        new_group = ((created or {}).get("IDs") or {}).get("ID") if isinstance(created, dict) else None
        if not isinstance(new_group, int):
            raise ValueError("Shoko did not create the collection")
    if rest:
        place(rest, new_group)
    return new_group


async def include(catalog: Any, group_id: int, aid: int) -> None:
    """Pull one entry into a collection: moved there in Shoko, or kept there until it is."""
    from bankai.web import shoko

    entry = catalog.entries.get(aid)
    if entry is None:
        place([aid], group_id)
        return
    if entry.group_id == group_id:
        return
    await snapshot()
    if await shoko._send("PATCH", f"/api/v3/Series/{entry.series_id}/Move/{int(group_id)}") is None:
        raise ValueError("Shoko has no such series or collection")


async def merge(catalog: Any, group_id: int, other_id: int) -> int:
    """Every entry of another collection into this one; returns how many moved in Shoko."""
    from bankai.web import shoko

    if other_id == group_id or other_id not in catalog.groups:
        raise ValueError("there is no other collection to merge")
    moving = [entry for entry in catalog.entries.values() if entry.group_id == other_id]
    if moving:
        await snapshot()
    for entry in moving:
        await shoko._send("PATCH", f"/api/v3/Series/{entry.series_id}/Move/{int(group_id)}")
    places = load_places()
    kept = {aid: group for aid, group in places.items() if group == other_id}
    if kept:
        place(list(kept), group_id)
    return len(moving)


async def apply_places(catalog: Any) -> int:
    """Hand Shoko the choices made for entries that have since come onto disk.

    An entry placed by hand while it was not downloaded is, once it is, in
    whatever group Shoko's own rules put it. The choice stands: it is moved
    where it was placed, or into a group of its own, and then forgotten,
    since Shoko keeps it from there. Returns how many were moved.
    """
    from bankai.web import shoko

    places = load_places()
    due = {aid: group for aid, group in places.items() if aid in catalog.entries}
    if not due:
        return 0
    moved = 0
    snapped = False
    sizes: dict[int | None, int] = {}
    for held in catalog.entries.values():
        sizes[held.group_id] = sizes.get(held.group_id, 0) + 1
    for aid, group in sorted(due.items()):
        entry = catalog.entries[aid]
        wanted = group if group in catalog.groups else None
        settled = entry.group_id == wanted if wanted is not None else sizes.get(entry.group_id) == 1
        if settled or entry.series_id is None:
            continue
        if not snapped:
            await snapshot()
            snapped = True
        if wanted is not None:
            await shoko._send("PATCH", f"/api/v3/Series/{entry.series_id}/Move/{int(wanted)}")
        else:
            await shoko._send("POST", "/api/v3/Group", json_body={"SeriesIDs": [entry.series_id]})
        moved += 1
    _save_places({aid: group for aid, group in places.items() if aid not in due})
    return moved
