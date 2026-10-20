"""Collections: the AniList entries of one show, kept together as one show.

AniList has an entry per season, or per cour, so a show of four seasons is
four entries. A collection puts them back together: one card in the library,
one folder on disk, one show in Jellyfin. bankai keeps them itself, in
``anime_collections.json``.

What joins a collection on its own is AniList's main line: sequels and
prequels, followed through entries not on disk, so a movie between two
seasons does not break the chain. Side stories, recaps and spin-offs stay out
until pulled in by hand in the relation graph, which is also where an entry
is split off into a collection of its own or two collections are merged.

On disk (see anime_layout) the main line is Season 1, 2, 3... in sequel order,
and every other entry of the collection -- the movie between two seasons,
the side story pulled in -- is a block of Season 00 specials.
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bankai.metadata import anilist, offline

MAIN_LINE = frozenset({"SEQUEL", "PREQUEL"})

# How each AniList relation reads as one edge of the graph: (label, flipped).
# "PREQUEL" is the same link as "SEQUEL" read from the other end, and so on;
# flipped ones are turned round so each link is drawn once, in one direction.
_EDGES = {
    "SEQUEL": ("sequel", False),
    "PREQUEL": ("sequel", True),
    "SIDE_STORY": ("side story", False),
    "PARENT": ("side story", True),
    "SUMMARY": ("summary", False),
    "SUMMARY_OF": ("summary", True),
    "ALTERNATIVE": ("alternative", False),
    "SPIN_OFF": ("spin-off", False),
    "SPIN_OFF_OF": ("spin-off", True),
    "CHARACTER": ("shared characters", False),
    "COMPILATION": ("compilation", False),
    "CONTAINS": ("compilation", True),
    "OTHER": ("other", False),
}
# Not anime-to-anime in any sense the library cares about.
_DROPPED = frozenset({"SOURCE", "ADAPTATION"})
_UNDIRECTED = frozenset({"alternative", "shared characters", "other"})
_MIRROR = {
    "SEQUEL": "PREQUEL",
    "PREQUEL": "SEQUEL",
    "SIDE_STORY": "PARENT",
    "PARENT": "SIDE_STORY",
    "CONTAINS": "COMPILATION",
    "COMPILATION": "CONTAINS",
    # AniList has no name of its own for these read from the other end: a
    # kind of bankai's, so the copy read from there is drawn as the same edge.
    "SUMMARY": "SUMMARY_OF",
    "SPIN_OFF": "SPIN_OFF_OF",
}


# -- The store -----------------------------------------------------------------


@dataclass
class Collection:
    id: int
    members: list[int] = field(default_factory=list)
    # The entry it is named and pictured after, and whose sequel chain is its seasons.
    title: int | None = None
    # A name given by hand; else the title entry's AniList name.
    name: str | None = None
    # The order its Season 00 blocks were given in, so a newcomer does not
    # renumber the specials already on disk.
    specials: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"members": self.members, "title": self.title, "name": self.name, "specials": self.specials}


@dataclass
class Store:
    collections: dict[int, Collection] = field(default_factory=dict)
    # Entries taken out of every collection by hand: never pulled in again on their own.
    alone: set[int] = field(default_factory=set)
    next: int = 1

    def of(self) -> dict[int, int]:
        """AniList id -> its collection."""
        return {aid: cid for cid, row in self.collections.items() for aid in row.members}

    def collection_of(self, aid: int) -> Collection | None:
        for row in self.collections.values():
            if aid in row.members:
                return row
        return None

    def create(self, members: list[int], *, title: int | None = None) -> Collection:
        cid = self.next
        self.next += 1
        row = Collection(cid, list(dict.fromkeys(members)), title if title is not None else (members[0] if members else None))
        self.collections[cid] = row
        return row

    def take(self, aid: int) -> None:
        """Take an entry out of whatever collection holds it; an emptied collection goes."""
        for cid, row in list(self.collections.items()):
            if aid in row.members:
                row.members.remove(aid)
                if aid in row.specials:
                    row.specials.remove(aid)
                if not row.members:
                    del self.collections[cid]
                elif row.title == aid:
                    row.title = _first_season(row.members)


def _path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "anime_collections.json"


def _lock():
    from bankai.web.erai import _STATE_LOCK

    return _STATE_LOCK


def _read() -> Store:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Store()
    store = Store(alone={int(a) for a in raw.get("alone") or [] if str(a).isdigit()}, next=int(raw.get("next") or 1))
    for cid, row in (raw.get("collections") or {}).items():
        if not str(cid).isdigit() or not isinstance(row, dict):
            continue
        members = [int(a) for a in row.get("members") or [] if str(a).isdigit()]
        if not members:
            continue
        title = row.get("title")
        store.collections[int(cid)] = Collection(
            int(cid),
            members,
            int(title) if str(title or "").isdigit() and int(title) in members else members[0],
            str(row["name"]) if row.get("name") else None,
            [int(a) for a in row.get("specials") or [] if str(a).isdigit()],
        )
    store.next = max([store.next, *(cid + 1 for cid in store.collections)])
    return store


def _write(store: Store) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    data = {
        "next": store.next,
        "collections": {str(cid): row.as_dict() for cid, row in sorted(store.collections.items())},
        "alone": sorted(store.alone),
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load() -> Store:
    with _lock():
        return _read()


@contextmanager
def editing() -> Iterator[Store]:
    """The store to change in place, saved when the block ends without error."""
    with _lock():
        store = _read()
        yield store
        _write(store)


# -- Relations -------------------------------------------------------------------


_TABLES: tuple[Any, list[tuple[int, int, str]], dict[int, set[int]], dict[int, list[tuple[int, str]]]] | None = None


def links() -> list[tuple[int, int, str]]:
    """(entry, related entry, AniList relation) as AniList lists them, from either end.

    A relation is listed on both of its ends, but only the ends fetched are
    known; each is also read from the other end, turned round, so an entry
    whose own record is not fetched yet is still linked. Kept until the
    AniList cache changes: every collection's plan asks for it.
    """
    return _tables()[0]


def _tables() -> tuple[list[tuple[int, int, str]], dict[int, set[int]], dict[int, list[tuple[int, str]]]]:
    global _TABLES
    rows = anilist.media_all()
    if _TABLES is None or _TABLES[0] is not rows:
        found: set[tuple[int, int, str]] = set()
        for aid, row in rows.items():
            for other, kind, _form in row.get("relations") or []:
                if kind in _DROPPED or other == aid:
                    continue
                found.add((aid, int(other), str(kind)))
                found.add((int(other), aid, _MIRROR.get(str(kind), str(kind))))
        relations = sorted(found)
        _TABLES = (rows, relations, main_line(relations), _neighbours(relations))
    return _TABLES[1], _TABLES[2], _TABLES[3]


def _line(relations: list[tuple[int, int, str]] | None) -> dict[int, set[int]]:
    """The main-line graph of these relations; of AniList's as kept, by default."""
    return _tables()[1] if relations is None else main_line(relations)


def _neighbours(relations: list[tuple[int, int, str]]) -> dict[int, list[tuple[int, str]]]:
    near: dict[int, list[tuple[int, str]]] = {}
    for left, right, kind in relations:
        near.setdefault(left, []).append((right, kind))
    return near


def main_line(relations: list[tuple[int, int, str]]) -> dict[int, set[int]]:
    """Sequel and prequel links, both ways: the chain of a show's seasons."""
    graph: dict[int, set[int]] = {}
    for left, right, kind in relations:
        if kind in MAIN_LINE and left != right:
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)
    return graph


def start(aid: int) -> str:
    """When an entry began airing, sortable; unknown last."""
    row = anilist.media(aid)
    if row.get("start"):
        return str(row["start"])
    year = anilist.year(aid)
    return f"{year:04d}" if year else "9999"


_SEASON_TAIL = re.compile(
    r"\s*\(\d{4}\)$|\s+(?:(?:Season|Part|Cour)\s*\d+|\d+(?:st|nd|rd|th)\s+Season|Final\s+Season|[IVX]+|\d+)$",
    re.IGNORECASE,
)


def stem(title: str) -> str:
    """A title without what tells its seasons apart: "Dorohedoro Season 2" -> "dorohedoro"."""
    value = anilist.normalise(title)
    previous = None
    while previous != value:
        previous = value
        value = anilist.normalise(_SEASON_TAIL.sub("", value))
    return value


def same_show(first: int, other: int) -> bool:
    """Whether two entries are seasons of one show by their names, not just linked on AniList.

    A sequel chain can run through several shows -- PreCure's series follow
    one another -- and the first of them is not what the later ones are called.
    """
    def names(aid: int) -> list[str]:
        kept = anilist.media(aid)
        return [name for name in (kept.get("english"), kept.get("romaji"), offline.entry(aid).get("title")) if name]

    for left in names(first):
        for right in names(other):
            a, b = stem(left), stem(right)
            if a and b and (b.startswith(a) or a.startswith(b)):
                return True
    return False


def first_of_show(aids: list[int], title: int) -> int:
    """The earliest season among ``aids`` that is the same show as ``title``, by name: what to name a collection after.

    "Mr. Osomatsu", not "Mr. Osomatsu 4th season" -- but not the 1988
    "Osomatsu-kun" its sequel chain starts from.
    """
    seasons = [aid for aid in aids if anilist.kind(aid) in {"TV", "ONA"} and (aid == title or same_show(aid, title))]
    return min(seasons, key=lambda aid: (start(aid), aid)) if seasons else title


def _first_season(aids: list[int]) -> int | None:
    seasons = [aid for aid in aids if anilist.kind(aid) in {"TV", "ONA"}]
    pool = seasons or list(aids)
    return min(pool, key=lambda aid: (start(aid), aid)) if pool else None


def sequel_order(aids: list[int], relations: list[tuple[int, int, str]], aired: Callable[[int], Any] = start) -> list[int]:
    """A collection's entries in the order to show them.

    The sequel chain first, prequel to sequel -- the longest chain among them,
    or the earliest aired at equal length -- then the rest, side stories,
    recaps, specials, as they aired. ``aired`` gives each entry a sortable date.
    """
    members = set(aids)
    after: dict[int, set[int]] = {aid: set() for aid in members}
    before: dict[int, set[int]] = {aid: set() for aid in members}
    for left, right, kind in relations:
        if left == right or left not in members or right not in members:
            continue
        if kind == "SEQUEL":
            after[left].add(right)
            before[right].add(left)
        elif kind == "PREQUEL":
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
    # A loop in AniList's links would leave entries waiting: they follow by date.
    ordered.extend(aid for aid in by_date if aid in waiting and aid not in ordered)
    return ordered + [aid for aid in by_date if aid not in ordered]


def short_label(title: str, collection: str | None) -> str:
    """An entry's tab name without the collection's own: "Attack on Titan Season 2" -> "Season 2"."""
    if collection and title.casefold().startswith(collection.casefold()):
        rest = title[len(collection):].lstrip(" :-–~,.")
        if rest:
            return rest
    return title


# -- Joining -----------------------------------------------------------------------


def chain(aid: int, store: Store, *, relations: list[tuple[int, int, str]] | None = None) -> list[int]:
    """An entry and its sequels and prequels, as far as no other collection holds them.

    Walked through entries not on disk, so a movie between two seasons does
    not break it. Entries kept out of every collection by hand stop it.
    """
    graph = _line(relations)
    held = store.of()
    found = [aid]
    queue: deque[int] = deque([aid])
    while queue:
        current = queue.popleft()
        for other in sorted(graph.get(current, ())):
            if other in found or other in store.alone or other in held:
                continue
            found.append(other)
            queue.append(other)
    return found


def nearest(aid: int, store: Store, *, relations: list[tuple[int, int, str]] | None = None) -> int | None:
    """The collection an entry joins by itself: the nearest along its sequels and prequels.

    At equal distance the older collection (lower id), for an answer that does
    not change from one call to the next.
    """
    graph = _line(relations)
    held = store.of()
    if aid in held:
        return held[aid]
    seen = {aid}
    frontier = [aid]
    while frontier:
        hits = sorted({held[other] for current in frontier for other in graph.get(current, ()) if other in held})
        if hits:
            return hits[0]
        following = []
        for current in frontier:
            for other in sorted(graph.get(current, ())):
                if other not in seen and other not in store.alone:
                    seen.add(other)
                    following.append(other)
        frontier = following
    return None


def join(aids: list[int], store: Store, *, relations: list[tuple[int, int, str]] | None = None) -> set[int]:
    """Put entries that are in no collection into the one they belong to; returns the collections changed.

    The nearest collection along sequels and prequels, else a new one; either
    way with the rest of the chain not held anywhere yet, as its missing seasons.
    """
    changed: set[int] = set()
    for aid in aids:
        aid = int(aid)
        if store.collection_of(aid) is not None:
            continue
        store.alone.discard(aid)
        found = chain(aid, store, relations=relations)
        target = nearest(aid, store, relations=relations)
        if target is None:
            row = store.create(found, title=first_of_show(found, aid))
        else:
            row = store.collections[target]
            row.members.extend(other for other in found if other not in row.members)
        changed.add(row.id)
    return changed


def extend(store: Store, *, relations: list[tuple[int, int, str]] | None = None) -> set[int]:
    """Add to each collection the sequels and prequels AniList has listed since; returns the ones grown."""
    changed: set[int] = set()
    for cid in sorted(store.collections):
        row = store.collections[cid]
        for aid in list(row.members):
            for other in chain(aid, store, relations=relations):
                if other not in row.members:
                    row.members.append(other)
                    changed.add(cid)
    return changed


# -- Seasons on disk -----------------------------------------------------------------


SEASON_FORMATS = frozenset({"TV", "ONA"})


@dataclass
class Plan:
    """Where each entry of a collection goes: a season of its own, or a Season 00 block."""

    seasons: dict[int, int] = field(default_factory=dict)   # AniList id -> season number
    specials: dict[int, int] = field(default_factory=dict)  # AniList id -> first S00 episode - 1

    def place(self, aid: int, episode: int) -> tuple[int, int] | None:
        if aid in self.seasons:
            return self.seasons[aid], episode
        if aid in self.specials:
            return 0, self.specials[aid] + episode
        return None

    def order(self) -> list[int]:
        """The entries as tabs: seasons in order, then the specials."""
        return [*sorted(self.seasons, key=self.seasons.__getitem__), *sorted(self.specials, key=self.specials.__getitem__)]


def seasons(row: Collection, *, relations: list[tuple[int, int, str]] | None = None, store: Store | None = None) -> list[int]:
    """The collection's main line: the TV seasons in its title entry's sequel chain, as they aired.

    The chain may run through any of its members, and through entries no
    collection holds: a movie between two seasons, left out, still links them.
    """
    graph = _line(relations)
    members = set(row.members)
    held = (store or Store()).of()
    title = row.title if row.title in members else _first_season(row.members)
    if title is None:
        return []
    walk = {title}
    queue: deque[int] = deque([title])
    while queue:
        current = queue.popleft()
        for other in graph.get(current, ()):
            if other in walk:
                continue
            if other in members or held.get(other) in (None, row.id):
                walk.add(other)
                queue.append(other)
    # In the order they aired: the way a show's seasons are counted, and a
    # prequel made later goes after the seasons already on disk, not before.
    line = sorted(
        (aid for aid in walk if aid in members and anilist.kind(aid) in SEASON_FORMATS),
        key=lambda aid: (start(aid), aid),
    )
    # A collection of a movie, or OVAs only: its title entry is its one season.
    return line or [title]


def plan(row: Collection, *, episodes_on_disk: Callable[[int], int] = lambda aid: 0,
         relations: list[tuple[int, int, str]] | None = None, store: Store | None = None) -> Plan:
    """Each member's place on disk.

    The main line is Season 1..n. Everything else is a block of Season 00, as
    long as the entry has episodes -- or as many as are on disk, if more --
    in the order the specials were given (``row.specials``), newcomers after
    them by air date.
    """
    line = seasons(row, relations=relations, store=store)
    result = Plan(seasons={aid: number for number, aid in enumerate(line, start=1)})
    rest = [aid for aid in row.members if aid not in result.seasons]
    ordered = [aid for aid in row.specials if aid in rest]
    ordered += sorted((aid for aid in rest if aid not in ordered), key=lambda aid: (start(aid), aid))
    offset = 0
    for aid in ordered:
        result.specials[aid] = offset
        offset += max(anilist.episode_count(aid) or 0, episodes_on_disk(aid), 1)
    return result


def settle_specials(row: Collection) -> None:
    """Fix the Season 00 order as it is now, newcomers included."""
    current = plan(row)
    row.specials = sorted(current.specials, key=current.specials.__getitem__)


# -- Names ---------------------------------------------------------------------------


_FIRST_SEASON = re.compile(r"\s+(?:Season\s*1|1st\s+Season)$", re.IGNORECASE)


def name(row: Collection) -> str:
    """How a collection is called: its own name, else its title entry's AniList name.

    Without a "Season 1" some first seasons carry: the collection is the show.
    """
    if row.name:
        return row.name
    title = row.title if row.title is not None else (row.members[0] if row.members else None)
    found = anilist.name(title) or offline.entry(title).get("title") or f"AniList {title}"
    return _FIRST_SEASON.sub("", found) or found


def entry_name(aid: int) -> str:
    return anilist.name(aid) or offline.entry(aid).get("title") or f"AniList {aid}"


# -- Changes by hand -----------------------------------------------------------------


def split(store: Store, cid: int, aids: list[int]) -> int:
    """Take entries out of a collection into one of their own; returns its id."""
    row = store.collections.get(cid)
    if row is None:
        raise ValueError("there is no such collection")
    moving = [int(aid) for aid in aids if int(aid) in row.members]
    if not moving:
        raise ValueError("none of these entries is in this collection")
    if len(moving) == len(row.members):
        raise ValueError("that would leave the collection empty")
    for aid in moving:
        store.take(aid)
    return store.create(moving, title=_first_season(moving)).id


def exclude(store: Store, cid: int, aids: list[int], *, with_files: set[int]) -> int | None:
    """Take entries out of a collection and out of every other.

    One with files on disk cannot be in none -- it has to be somewhere -- so
    those go to a collection of their own instead; its id is returned.
    """
    row = store.collections.get(cid)
    if row is None:
        raise ValueError("there is no such collection")
    moving = [int(aid) for aid in aids if int(aid) in row.members]
    if len(moving) == len(row.members) and set(moving) & with_files:
        raise ValueError("that would leave the collection empty")
    kept = [aid for aid in moving if aid in with_files]
    for aid in moving:
        store.take(aid)
        if aid not in with_files:
            store.alone.add(aid)
    return store.create(kept, title=_first_season(kept)).id if kept else None


def include(store: Store, cid: int, aid: int) -> None:
    """Pull one entry into a collection, from wherever it was."""
    if cid not in store.collections:
        raise ValueError("there is no such collection")
    aid = int(aid)
    if aid in store.collections[cid].members:
        return
    store.take(aid)
    store.alone.discard(aid)
    if cid not in store.collections:
        raise ValueError("there is no such collection")
    store.collections[cid].members.append(aid)


def merge(store: Store, cid: int, other: int) -> int:
    """Every entry of another collection into this one; returns how many moved."""
    if other == cid or other not in store.collections or cid not in store.collections:
        raise ValueError("there is no other collection to merge")
    moving = store.collections.pop(other)
    row = store.collections[cid]
    row.members.extend(aid for aid in moving.members if aid not in row.members)
    return len(moving.members)


def set_title(store: Store, cid: int, aid: int) -> None:
    row = store.collections.get(cid)
    if row is None or int(aid) not in row.members:
        raise ValueError("that entry is not in this collection")
    row.title = int(aid)


def set_name(store: Store, cid: int, value: str | None) -> None:
    row = store.collections.get(cid)
    if row is None:
        raise ValueError("there is no such collection")
    row.name = (value or "").strip() or None


# -- The relation graph of one collection --------------------------------------------


def graph(
    store: Store,
    cid: int | None,
    *,
    root_aid: int | None = None,
    episodes_on_disk: Callable[[int], int] = lambda aid: 0,
) -> dict:
    """One collection as nodes and edges: its entries, and the ones related to them.

    The collection's own entries are the nodes it is made of. Every entry an
    AniList relation ties to one of them, but which is not in it, is a
    suggestion: a side story, a recap, a sequel kept in another collection.
    A related entry kept in another collection brings that whole collection
    along, so it is seen for what it is, not as one stray entry.

    Without a collection, ``root_aid`` is an entry in none: its graph is it
    and its relations, from which it can be moved into one.
    """
    held = store.of()
    if cid is None:
        if root_aid is None or root_aid in held:
            raise ValueError("that entry is in a collection; open the collection")
        row = None
        title_aid = root_aid
        members = {root_aid}
    else:
        row = store.collections.get(cid)
        if row is None:
            raise ValueError("there is no such collection")
        title_aid = row.title
        members = set(row.members)
    _relations, _graph, near = _tables()
    suggestions = {
        other for aid in members for other, _kind in near.get(aid, ()) if other not in members
    }
    elsewhere = {held[aid] for aid in suggestions if held.get(aid) not in (None, cid)}
    suggestions |= {aid for aid, other in held.items() if other in elsewhere}
    placing = plan(row, episodes_on_disk=episodes_on_disk, store=store) if row else Plan()

    def node(aid: int) -> dict:
        other = held.get(aid)
        kept = anilist.media(aid)
        place = placing.seasons.get(aid)
        return {
            "anilist_id": aid,
            "title": entry_name(aid),
            "main_title": kept.get("romaji") or offline.entry(aid).get("title"),
            "type": anilist.kind(aid),
            "year": anilist.year(aid),
            "first_aired": kept.get("start") or (str(anilist.year(aid)) if anilist.year(aid) else None),
            "episodes": anilist.episode_count(aid),
            "on_disk": episodes_on_disk(aid),
            "owned": episodes_on_disk(aid) > 0,
            "poster_url": anilist.cover(aid),
            "in_collection": aid in members,
            "title_entry": aid == title_aid,
            "season": (f"Season {place}" if place else "Specials") if aid in members and row else None,
            # Where a suggestion is now: another collection, or none.
            "collection_id": other,
            "collection_name": name(store.collections[other]) if other in store.collections else None,
            "alone": aid in store.alone,
        }

    every = members | suggestions
    nodes = [node(aid) for aid in sorted(every)]
    seen: set[tuple] = set()
    edges = []
    for left, right, kind in sorted((aid, other, kind) for aid in every for other, kind in near.get(aid, ()) if other in every):
        label, flipped = _EDGES.get(kind, (kind.lower().replace("_", " "), False))
        source, target = (right, left) if flipped else (left, right)
        edge_key = (label, *sorted((source, target))) if label in _UNDIRECTED else (label, source, target)
        if edge_key in seen:
            continue
        seen.add(edge_key)
        edges.append({"from": source, "to": target, "label": label, "main_line": kind in MAIN_LINE})
    return {
        "collection_id": cid,
        "root_aid": root_aid if row is None else None,
        "name": name(row) if row is not None else entry_name(root_aid),
        "title_entry": title_aid,
        "nodes": nodes,
        "edges": edges,
    }


__all__ = [
    "MAIN_LINE",
    "Collection",
    "Plan",
    "Store",
    "chain",
    "editing",
    "entry_name",
    "exclude",
    "extend",
    "graph",
    "include",
    "join",
    "links",
    "load",
    "main_line",
    "merge",
    "name",
    "nearest",
    "plan",
    "seasons",
    "sequel_order",
    "set_name",
    "set_title",
    "settle_specials",
    "short_label",
    "split",
    "start",
]
