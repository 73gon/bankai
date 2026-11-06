"""The manga libraries on disk, laid out by collection, every book under the library's own name.

A collection's entries (manga_collections) go into one folder named after it, each into
its own folder inside, "Title (Year) - id": "Tokyo Ghoul/Tokyo Ghoul -re (2014) - 85611" --
once two or more of them are on disk; a collection of which the library has one entry (the
others only followed) leaves it where it is.
OpenPanel takes the folder holding the books as the series and reads the entry's id from
its name, so each entry stays its own series there. A series in no collection keeps its
folder as it is (one taken out of a collection goes back up beside the others).

Books get the names downloads are filed under: "Vol 01.cbz", "Chapter 0138.cbz", and
"Vol 01-02.cbz" for one holding two volumes. A book whose name gives no number, or whose
new name the folder has already, stays as it is.

Nothing moves on its own: ``plan`` says what would, ``apply`` does it. Each run is journaled
in ``manga_layout_moves/`` -- every move in order, as it happens -- and ``undo`` puts a run
back. OpenPanel scans afterwards; it finds moved books by their pages and keeps them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.web import manga_collections, manga_library, manga_names

log = get_logger(__name__)


def _journals() -> Path:
    return bgjobs.jobs_root().parent / "manga_layout_moves"


def standard_name(filename: str) -> str:
    """A book's library name: "Vol 01.cbz", "Chapter 0138.cbz", "Vol 01-02.cbz" for one of two
    volumes; its own when its name gives no number."""
    suffix = Path(filename).suffix
    number = manga_names.file_number(filename)
    if number:
        return manga_names.target_name(number[0], number[1], suffix)
    release = manga_names.parse(Path(filename).stem)
    for label, span, other, width in (("Vol", release.volumes, release.chapters, 2), ("Chapter", release.chapters, release.volumes, 4)):
        if span and not other and span[0] != span[1] and span[0] == int(span[0]) and span[1] == int(span[1]):
            return f"{label} {int(span[0]):0{width}d}-{int(span[1]):0{width}d}{manga_names.library_extension(suffix)}"
    return filename


def collections() -> manga_collections.Store:
    """The collections as they stand, with the entries the library has or follows joined in."""
    return manga_collections.refresh(manga_collections.present())


def gathered(row: manga_collections.Collection, on_disk: set[int]) -> bool:
    """Whether a collection has a folder of its own: two or more of its entries are on disk."""
    return len(set(row.members) & on_disk) >= 2


def plan(store: manga_collections.Store | None = None, *, only: set[int] | None = None) -> dict[str, Any]:
    """What ``apply`` would do: {"collections": [...], "folders": [moves], "files": [renames], "skipped": [...]};
    with ``only``, for those entries alone. Paths are below their library's root."""
    store = store or collections()
    rows = {aid: row for aid, row in manga_library.series().items() if only is None or aid in only}
    on_disk = set(manga_library.series())
    folders: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    shown: list[dict[str, Any]] = []
    placed: dict[int, str] = {}
    for cid, row in sorted(store.collections.items()):
        name = manga_collections.folder_name(row)
        shown.append({"id": cid, "name": manga_collections.name(row), "folder": name if gathered(row, on_disk) else None,
                      "members": row.members, "on_disk": [aid for aid in row.members if aid in on_disk]})
        if not gathered(row, on_disk):
            continue
        for aid in row.members:
            if aid in rows:
                placed[aid] = f"{name}/{manga_library.new_folder_name(aid)}"
    for aid, current in sorted(rows.items(), key=lambda item: item[1]["folder"]):
        kind = current.get("kind") or "manga"
        source = current["folder"]
        # In a collection: inside its folder; out of one again: back up beside the others.
        target = placed.get(aid) or (manga_library.leaf(source) if "/" in source else source)
        if target == source or not (manga_library.root(kind) / source).is_dir():
            continue
        if (manga_library.root(kind) / target).exists():
            skipped.append({"anilist_id": aid, "kind": kind, "from": source, "to": target, "why": "a folder of that name is there already"})
            continue
        folders.append({"anilist_id": aid, "kind": kind, "from": source, "to": target})
    moved = {move["anilist_id"]: move["to"] for move in folders}
    files: list[dict[str, Any]] = []
    for aid, current in sorted(rows.items(), key=lambda item: item[1]["folder"]):
        kind = current.get("kind") or "manga"
        # Its books' names as the library keeps them (read again only when the folder changed).
        names = manga_library.stats(aid).get("names") or []
        taken = set(names)
        for name in names:
            target = standard_name(name)
            if target == name:
                continue
            if target in taken:
                skipped.append({"anilist_id": aid, "kind": kind, "folder": moved.get(aid, current["folder"]), "from": name, "to": target, "why": "the folder has a book of that name"})
                continue
            taken.add(target)
            files.append({"anilist_id": aid, "kind": kind, "folder": moved.get(aid, current["folder"]), "from": name, "to": target})
    return {"collections": shown, "folders": folders, "files": files, "skipped": skipped}


def busy() -> str | None:
    """Why nothing may move now: a download being filed into a folder."""
    from bankai.web import manga_releases

    if any(row.get("status") == manga_releases.PUBLISHING for row in manga_releases.releases().values()):
        return "a download is being filed into the library: try again in a minute"
    return None


def apply(*, only: set[int] | None = None, check: bool = True) -> dict[str, Any]:
    """Collections saved, then the folders moved and the books renamed as ``plan`` says, each move
    journaled as it happens; with ``only``, for those entries alone. Returns the plan carried out,
    with its journal's name."""
    reason = busy() if check else None
    if reason:
        raise ValueError(reason)
    todo = plan(collections(), only=only)
    if not todo["folders"] and not todo["files"]:
        return {**todo, "journal": None}
    journal = _journals() / time.strftime("%Y%m%d-%H%M%S.json")
    journal.parent.mkdir(parents=True, exist_ok=True)
    done: list[dict[str, Any]] = []

    def record() -> None:
        journal.write_text(json.dumps({"at": time.time(), "moves": done}, ensure_ascii=False, indent=1), encoding="utf-8")

    record()
    for move in todo["folders"]:
        base = manga_library.root(move["kind"])
        source, target = base / move["from"], base / move["to"]
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        manga_library.relocate(move["anilist_id"], move["to"])
        done.append({"what": "folder", **move})
        record()
        _drop_if_empty(source.parent, base)
    for rename in todo["files"]:
        folder = manga_library.root(rename["kind"]) / rename["folder"]
        (folder / rename["from"]).rename(folder / rename["to"])
        manga_library.forget(folder)
        done.append({"what": "file", **rename})
        record()
    log.info("[manga] layout: %d folder(s) moved, %d book(s) renamed (%s)", len(todo["folders"]), len(todo["files"]), journal.name)
    return {**todo, "journal": journal.name}


def _drop_if_empty(folder: Path, base: Path) -> None:
    """A collection's folder left with nothing in it goes; never the library's root."""
    if folder != base and folder.parent == base:
        try:
            folder.rmdir()
        except OSError:
            pass


def journals() -> list[dict[str, Any]]:
    """The runs, newest first: {name, at, moves, undone}."""
    rows = []
    for path in sorted(_journals().glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({"name": path.name, "at": data.get("at"), "moves": len(data.get("moves") or []), "undone": bool(data.get("undone"))})
    return rows


def undo(name: str) -> dict[str, Any]:
    """One run put back: its moves undone last first, each folder remembered where it was."""
    path = _journals() / Path(name).name
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("undone"):
        raise ValueError("that run is undone already")
    reason = busy()
    if reason:
        raise ValueError(reason)
    undone = 0
    for move in reversed(data.get("moves") or []):
        base = manga_library.root(move["kind"])
        if move["what"] == "file":
            folder = base / move["folder"]
            if (folder / move["to"]).exists() and not (folder / move["from"]).exists():
                (folder / move["to"]).rename(folder / move["from"])
                undone += 1
        else:
            source, target = base / move["to"], base / move["from"]
            if source.is_dir() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                manga_library.relocate(move["anilist_id"], move["from"])
                _drop_if_empty(source.parent, base)
                undone += 1
    data["undone"] = time.time()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("[manga] layout %s undone: %d move(s)", path.name, undone)
    return {"undone": undone}
