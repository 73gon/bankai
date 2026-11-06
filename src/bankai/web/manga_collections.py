"""Manga collections: the AniList entries of one story, kept together as the anime library's are.

AniList has an entry per part or sequel -- JoJo's Bizarre Adventure is eight, Tokyo
Ghoul two -- and a collection puts them back together: one row in the library, and
on disk one folder with a folder per entry inside it (``manga_layout``). OpenPanel
takes the folder holding the books as the series, so each entry stays its own series
there, under the collection's folder.

What joins on its own is AniList's main line among entries of one kind (manga with
manga, light novels with light novels: they live in different libraries): sequels and
prequels, followed through entries bankai neither has nor follows, so a part nobody
downloaded does not break the chain. A collection holds the entries of that line the
library has or follows; one of a single entry is none. Anything else -- a side story,
a spin-off -- joins by hand from the relation graph, which is also where an entry is
taken out again; one taken out never joins on its own again (``alone``).

Kept by bankai in ``manga_collections.json``.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from bankai.metadata import anilist_manga as manga_meta
from bankai.processor.naming import sanitise

MAIN_LINE = frozenset({"SEQUEL", "PREQUEL"})
_LOCK = threading.RLock()


@dataclass
class Collection:
    id: int
    members: list[int]
    # A name of the user's own; else the members' shared words (``name``).
    custom: str | None = None

    def as_dict(self) -> dict:
        row: dict = {"members": self.members}
        if self.custom:
            row["name"] = self.custom
        return row


@dataclass
class Store:
    collections: dict[int, Collection] = field(default_factory=dict)
    alone: set[int] = field(default_factory=set)
    next: int = 1

    def of(self) -> dict[int, int]:
        """Entry -> its collection's id."""
        return {aid: cid for cid, row in self.collections.items() for aid in row.members}

    def collection_of(self, aid: int) -> Collection | None:
        cid = self.of().get(int(aid))
        return self.collections.get(cid) if cid is not None else None

    def create(self, members: list[int]) -> Collection:
        row = Collection(self.next, list(dict.fromkeys(members)))
        self.collections[row.id] = row
        self.next += 1
        return row

    def take(self, aid: int) -> None:
        """An entry out of whichever collection holds it; one left with a single entry is none."""
        for cid, row in list(self.collections.items()):
            if aid in row.members:
                row.members.remove(aid)
                if len(row.members) < 2:
                    del self.collections[cid]


def _path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "manga_collections.json"


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
        if len(members) >= 2:
            store.collections[int(cid)] = Collection(int(cid), members, str(row["name"]) if row.get("name") else None)
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
    with _LOCK:
        return _read()


@contextmanager
def editing() -> Iterator[Store]:
    """The store to change in place, saved when the block ends without error."""
    with _LOCK:
        store = _read()
        yield store
        _write(store)


# -- Joining ----------------------------------------------------------------------


def main_line(aid: int) -> set[int]:
    """Every entry AniList's sequels and prequels reach from one, among entries of its kind --
    through those bankai neither has nor follows, as far as AniList's cache knows them."""
    kind = manga_meta.kind_of(manga_meta.media(aid).get("format"))
    seen: set[int] = set()
    stack = [int(aid)]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for relation in manga_meta.media(current).get("relations") or []:
            other, how = int(relation[0]), relation[1]
            kind_of_other = relation[3] if len(relation) > 3 else None
            if how in MAIN_LINE and (len(relation) < 3 or relation[2] in (None, "MANGA")) and manga_meta.kind_of(kind_of_other) == kind:
                stack.append(other)
    return seen


def build(store: Store, present: Iterable[int]) -> set[int]:
    """Collections for the entries the library has or follows (``present``): each joins the
    collection of an entry on its main line, or one is made of those of it present -- two or more.
    Entries taken out by hand stay out. Returns the ids of the collections changed."""
    present = {int(aid) for aid in present}
    changed: set[int] = set()
    of = store.of()
    for aid in sorted(present):
        if aid in of or aid in store.alone:
            continue
        line = (main_line(aid) & present) - store.alone
        joined = next((of[other] for other in sorted(line) if other in of), None)
        if joined is not None:
            row = store.collections[joined]
            row.members.append(aid)
            changed.add(joined)
        elif len(line) >= 2:
            row = store.create(sorted(line, key=_order_key))
            changed.add(row.id)
        else:
            continue
        of = store.of()
    for cid in changed:
        row = store.collections[cid]
        row.members = sorted(dict.fromkeys(row.members), key=_order_key)
    return changed


def refresh(present: Iterable[int]) -> Store:
    """The collections with the entries the library has or follows joined in (``build``), saved
    when that changed something: the library's rows and the drawer's tabs read them."""
    with _LOCK:
        store = _read()
        if build(store, present):
            _write(store)
        return store


def present() -> set[int]:
    """The entries the library has or follows."""
    from bankai.web import manga_library, manga_series

    return set(manga_library.series()) | set(manga_series.followed())


def include(store: Store, cid: int, aid: int) -> None:
    """An entry into a collection by hand: out of any other, and joining on its own again."""
    row = store.collections.get(int(cid))
    if row is None:
        raise ValueError(f"no collection {cid}")
    store.take(int(aid))
    row = store.collections.get(int(cid))
    if row is None:
        raise ValueError(f"no collection {cid}")
    row.members = sorted(dict.fromkeys([*row.members, int(aid)]), key=_order_key)
    store.alone.discard(int(aid))


def exclude(store: Store, aid: int) -> None:
    """An entry out of its collection by hand: it never joins one on its own again."""
    store.take(int(aid))
    store.alone.add(int(aid))


def together(store: Store, aids: list[int]) -> Collection:
    """Entries into one collection by hand (an entry and another not in one yet)."""
    for aid in aids:
        store.take(int(aid))
        store.alone.discard(int(aid))
    return store.create(sorted(dict.fromkeys(int(aid) for aid in aids), key=_order_key))


def split(store: Store, aids: list[int]) -> Collection | None:
    """Entries split off by hand: one on its own (``exclude``), several into a new collection together."""
    if len(aids) == 1:
        exclude(store, aids[0])
        return None
    return together(store, aids)


def merge(store: Store, cid: int, other: int) -> None:
    """A whole collection merged into another by hand."""
    row, gone = store.collections.get(int(cid)), store.collections.get(int(other))
    if row is None or gone is None or row is gone:
        raise ValueError("no such collections")
    row.members = sorted(dict.fromkeys([*row.members, *gone.members]), key=_order_key)
    store.alone.difference_update(gone.members)
    del store.collections[gone.id]


def _order_key(aid: int) -> tuple:
    entry = manga_meta.media(aid)
    return (int(entry.get("year") or 9999), manga_meta.name(aid).casefold(), int(aid))


# -- Names ------------------------------------------------------------------------


def _words(title: str) -> list[str]:
    """A title's words, for comparing: "JoJo’s" and "JoJo's" are one word."""
    return re.findall(r"[\w']+", title.replace("’", "'").replace("‘", "'"))


def name(row: Collection) -> str:
    """A collection's name: the user's own, else the words all its entries' names start with --
    "JoJo's Bizarre Adventure", "Tokyo Ghoul" -- else its first entry's name."""
    if row.custom:
        return row.custom
    names = [manga_meta.name(aid) for aid in row.members]
    shared: list[str] = []
    for words in zip(*(_words(title) for title in names)):
        if len({word.casefold() for word in words}) != 1:
            break
        shared.append(words[0])
    # A dangling joiner ("Tokyo Ghoul -", "Part") is no name's end.
    while shared and shared[-1].casefold() in {"part", "the", "of", "and", "season", "vol", "volume"}:
        shared.pop()
    return " ".join(shared) if shared else (names[0] if names else f"Collection {row.id}")


def folder_name(row: Collection) -> str:
    """The collection's folder: its name as a folder can carry it."""
    title = name(row).replace(": ", " - ").replace(":", " -")
    return sanitise(title, fallback=f"Collection {row.id}")
