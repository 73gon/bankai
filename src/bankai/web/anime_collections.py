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


def sequel_order(aids: list[int], relations: list[tuple[int, int, str]], aired: Any) -> list[int]:
    """A collection's entries in the order to show them.

    The sequel chain first, prequel to sequel -- the longest chain among them,
    or the earliest aired at equal length -- then the rest, side stories,
    recaps, specials, as they aired. By air date alone a 2013 OVA came
    between a show's first and second season. ``aired`` gives each entry a
    sortable date.
    """
    members = set(aids)
    after: dict[int, set[int]] = {aid: set() for aid in members}
    before: dict[int, set[int]] = {aid: set() for aid in members}
    for left, right, kind in relations:
        if left == right or left not in members or right not in members:
            continue
        if kind == "Sequel":
            after[left].add(right)
            before[right].add(left)
        elif kind == "Prequel":
            after[right].add(left)
            before[left].add(right)
    by_date = sorted(members, key=lambda aid: (aired(aid), aid))
    chains: list[list[int]] = []
    seen: set[int] = set()
    for aid in by_date:
        if aid in seen or not (after[aid] or before[aid]):
            continue
        chain, stack = [], [aid]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            chain.append(current)
            stack.extend(after[current] | before[current])
        chains.append(chain)
    spine = min(chains, key=lambda chain: (-len(chain), min(aired(aid) for aid in chain)), default=[])
    ordered: list[int] = []
    waiting = {aid: len(before[aid] & set(spine)) for aid in spine}
    ready = sorted((aid for aid in spine if waiting[aid] == 0), key=lambda aid: (aired(aid), aid))
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for nxt in after[current]:
            if nxt in waiting:
                waiting[nxt] -= 1
                if waiting[nxt] == 0:
                    ready.append(nxt)
                    ready.sort(key=lambda aid: (aired(aid), aid))
    # A loop in AniDB's links would leave entries waiting: they follow by date.
    ordered.extend(aid for aid in by_date if aid in waiting and aid not in ordered)
    return ordered + [aid for aid in by_date if aid not in ordered]


def short_label(title: str, collection: str | None) -> str:
    """An entry's tab name without the collection's own: "Attack on Titan Season 2" -> "Season 2"."""
    if collection and title.casefold().startswith(collection.casefold()):
        rest = title[len(collection):].lstrip(" :-–~,.")
        if rest:
            return rest
    return title


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


def graph(
    catalog: Any,
    group_id: int | None,
    *,
    table: Any = None,
    places: dict[int, int | None] | None = None,
    titles: dict[int, int] | None = None,
    root_aid: int | None = None,
) -> dict:
    """One collection as nodes and edges: its entries, and the ones related to them.

    The collection's own entries are the nodes it is made of. Every entry an
    AniDB relation ties to one of them, but which is not in it, is a
    suggestion: a side story, a recap, a sequel kept in another collection.
    A related entry kept in another collection brings that whole collection
    along, so it is seen for what it is, not as one stray entry.

    Without a collection, ``root_aid`` is an entry not on disk and in none:
    its graph is it and its relations, from which it can be moved into one.
    """
    from bankai.metadata import anidb_art, anilist

    found = assign(catalog, places)
    if group_id is None:
        if root_aid is None or root_aid in catalog.entries or found.of.get(root_aid) is not None:
            raise ValueError("that entry is in a collection; open the collection")
        group = None
        title_aid = root_aid
        members = {root_aid}
    else:
        group = catalog.groups.get(group_id) if catalog is not None else None
        if group is None:
            raise ValueError("Shoko has no such collection")
        title_aid = title_entry(group, titles)
        members = set(found.members(group_id))
    # Both ways: a link can be listed on either end, and an entry Shoko holds
    # no record of has its links listed only on the other.
    related: dict[int, list[tuple[int, str]]] = {}
    for left, right, kind in catalog.relations:
        related.setdefault(left, []).append((right, kind))
        related.setdefault(right, []).append((left, kind))
    suggestions = {
        other for aid in members for other, _kind in related.get(aid, ()) if other not in members
    }
    elsewhere = {found.of[aid] for aid in suggestions if found.of.get(aid) not in (None, group_id)}
    suggestions |= {aid for aid, other in found.of.items() if other in elsewhere}
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
            "title": anilist.name(aid)
            or (entry.title if entry else None)
            or ((anime.english_title or anime.title) if anime else f"AniDB {aid}"),
            "main_title": anime.title if anime else None,
            "type": facts.get("type"),
            "year": int(min(aired)[:4]) if aired else facts.get("year"),
            "first_aired": min(aired) if aired else (str(facts["year"]) if facts.get("year") else None),
            "episodes": len(entry.episodes) if entry and entry.episodes else facts.get("episodes"),
            "on_disk": len(on_disk.get(aid, ())),
            "owned": entry is not None,
            "poster_url": anilist.cover(aid) or (entry.poster_url if entry else None) or anidb_art.cover(aid),
            "in_collection": aid in members,
            "title_entry": aid == title_aid,
            # Where a suggestion is now: another collection, or none.
            "collection_id": other,
            "collection_name": catalog.groups[other].name if other in catalog.groups else None,
            "alone": aid in found.alone,
        }

    nodes = [node(aid) for aid in sorted(members | suggestions)]
    name = group.name if group is not None else next(row["title"] for row in nodes if row["anidb_id"] == root_aid)
    seen: set[tuple] = set()
    edges = []
    for left, right, kind in catalog.relations:
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
        "root_aid": root_aid if group is None else None,
        "name": name,
        "title_entry": title_aid,
        "nodes": nodes,
        "edges": edges,
    }


# -- A collection's title entry ------------------------------------------------
#
# Shoko names and pictures a group after its preferred series, which has to be
# one it holds. A season not downloaded yet can still be the title: Shoko's
# group then gets its name as a custom name, and bankai keeps which entry it
# is, for the cover on the card.


def _titles_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "anime_collection_titles.json"


def load_titles() -> dict[int, int]:
    """Shoko group -> the entry not on disk chosen to name it."""
    try:
        raw = json.loads(_titles_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        int(group): int(aid)
        for group, aid in (raw.items() if isinstance(raw, dict) else ())
        if str(group).isdigit() and str(aid).isdigit()
    }


def _save_titles(titles: dict[int, int]) -> None:
    path = _titles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({str(group): aid for group, aid in sorted(titles.items())}), encoding="utf-8")
    tmp.replace(path)


def title_entry(group: Any, titles: dict[int, int] | None) -> int | None:
    """The entry a collection is named and pictured after."""
    return (titles or {}).get(group.id) or group.main_aid


# -- Collection names from AniList ---------------------------------------------
#
# A Shoko group is named after its main series as AniDB or TMDB has it; the
# names people know are AniList's. So each group is named after its title
# entry as AniList has it, and Jellyfin shows that. A group named by hand in
# Shoko is left as it is: only the ones Shoko named, or bankai did, are kept
# in step.


def _names_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "anime_collection_names.json"


def load_names() -> dict[int, str]:
    """Shoko group -> the name bankai gave it."""
    try:
        raw = json.loads(_names_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {int(group): str(name) for group, name in (raw.items() if isinstance(raw, dict) else ()) if str(group).isdigit()}


def _save_names(names: dict[int, str]) -> None:
    path = _names_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({str(group): name for group, name in sorted(names.items())}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


async def sync_names(catalog: Any) -> int:
    """Name every collection after its title entry as AniList has it; returns how many changed.

    The catalogue's groups are updated in place, so the page shows the new
    names without reading Shoko again.
    """
    from bankai.metadata import anilist
    from bankai.web import shoko
    from bankai.web.shoko_catalog import Group

    if catalog is None or not catalog.groups:
        return 0
    named = load_names()
    titles = load_titles()
    changes: list[tuple[int, str]] = []
    for group_id, group in catalog.groups.items():
        aid = title_entry(group, titles)
        wanted = anilist.name(aid) if aid else None
        if not wanted or wanted == group.name:
            continue
        if group.has_custom_name and named.get(group_id) != group.name:
            continue  # named by hand in Shoko: the user's, not ours
        changes.append((group_id, wanted))
    if not changes:
        return 0
    await snapshot()
    done = 0
    for group_id, wanted in changes:
        result = await shoko._send(
            "PATCH",
            f"/api/v3/Group/{group_id}",
            json_body=[
                {"op": "replace", "path": "/Name", "value": wanted},
                {"op": "replace", "path": "/HasCustomName", "value": True},
            ],
        )
        if result is None:
            continue
        named[group_id] = wanted
        group = catalog.groups[group_id]
        catalog.groups[group_id] = Group(group.id, wanted, group.main_aid, True)
        done += 1
    _save_names(named)
    return done


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
    patch = [{"op": "replace", "path": "/PreferredSeriesID", "value": int(series_id)}]
    titles = load_titles()
    # A name given for an entry not on disk is taken back: the series names it again.
    if titles.pop(int(group_id), None) is not None:
        patch.append({"op": "replace", "path": "/HasCustomName", "value": False})
    result = await shoko._send("PATCH", f"/api/v3/Group/{int(group_id)}", json_body=patch)
    if result is None:
        raise ValueError("Shoko has no such group")
    _save_titles(titles)
    return await shoko._get(f"/api/v3/Group/{int(group_id)}")


async def set_title_entry(catalog: Any, group_id: int, aid: int, name: str) -> dict:
    """Name a collection after any of its entries; one not on disk by its name only."""
    from bankai.web import shoko

    entry = catalog.entries.get(int(aid))
    if entry is not None and entry.series_id is not None:
        if entry.group_id != group_id:
            raise ValueError("that entry is in another collection")
        return await set_title(group_id, entry.series_id)
    from bankai.metadata import anilist

    name = anilist.name(aid) or name
    await snapshot()
    result = await shoko._send(
        "PATCH",
        f"/api/v3/Group/{int(group_id)}",
        json_body=[
            {"op": "replace", "path": "/Name", "value": name},
            {"op": "replace", "path": "/HasCustomName", "value": True},
        ],
    )
    if result is None:
        raise ValueError("Shoko has no such group")
    titles = load_titles()
    titles[int(group_id)] = int(aid)
    _save_titles(titles)
    named = load_names()
    named[int(group_id)] = name
    _save_names(named)
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
