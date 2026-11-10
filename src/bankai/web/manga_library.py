"""The manga and light novel libraries on disk: a folder per series, which AniList entry it is, what it holds.

The libraries are OpenPanel's (``manga.library_dir``, ``manga.novels_dir``): a
folder per series, named "Title (Year)", with "Vol 01.cbz" or "Chapter
0001.cbz" inside. Folders bankai makes carry the entry too, "Title (Year) -
118586": OpenPanel reads the id from the name and leaves it out of the shown
title, so it cannot match the folder to the wrong series.

Which entry a folder is, in this order: an id in its name; what OpenPanel
matched it to (its database, read only); AniList's search, taken only when a
title is the folder's name exactly and the year agrees. Kept in
``manga_library.json``; a folder none of these finds is matched by hand.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import anilist
from bankai.metadata import anilist_manga as manga_meta
from bankai.processor.naming import sanitise
from bankai.web import manga_names

log = get_logger(__name__)

KINDS = ("manga", "novel")
# OpenPanel's database where its Windows install keeps it, seen from WSL.
OPENPANEL_DATABASE = Path("/mnt/c/ProgramData/openpanel/openpanel.db")
_FOLDER_ID = re.compile(r"(?<!\d)(\d{5,6})(?!\d)")
_FOLDER_YEAR = re.compile(r"\((\d{4})\)")
# Folders AniList's search could not settle are asked again after this long.
_RETRY_SECONDS = 7 * 86400


def root(kind: str) -> Path:
    settings = get_settings().manga
    return Path(settings.novels_dir if kind == "novel" else settings.library_dir)


def _store_path() -> Path:
    return bgjobs.jobs_root().parent / "manga_library.json"


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw.setdefault("series", {})
    raw.setdefault("unmatched", {})
    return raw


def _write(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _has_books(path: Path) -> bool:
    try:
        with os.scandir(path) as listing:
            return any(entry.is_file() and Path(entry.name).suffix.lower() in manga_names.BOOK_EXTENSIONS for entry in listing)
    except OSError:
        return False


def _subfolders(path: Path | str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as listing:
            return [entry for entry in listing if entry.is_dir() and not entry.name.startswith(".")]
    except OSError:
        return []


def folders(kind: str) -> list[str]:
    """Every series folder of a library, by its path below the root: a folder there, or -- a
    collection's (manga_collections) -- one inside a folder there that holds folders and no book.
    "Tokyo Ghoul (2011)", "Tokyo Ghoul/Tokyo Ghoul -re (2014) - 85611"."""
    found: list[str] = []
    for entry in _subfolders(root(kind)):
        inner = [] if _has_books(Path(entry.path)) else [child.name for child in _subfolders(entry.path)]
        if inner:
            found.extend(f"{entry.name}/{child}" for child in inner)
        else:
            found.append(entry.name)
    return sorted(found)


def leaf(folder: str) -> str:
    """A series folder's own name, without the collection folder it may be in."""
    return folder.rsplit("/", 1)[-1]


def folder_id(name: str) -> int | None:
    """An AniList id in a folder's name, as OpenPanel reads it: five or six digits, not a year."""
    for match in _FOLDER_ID.finditer(leaf(name)):
        number = int(match[1])
        if not 1900 <= number <= 2100:
            return number
    return None


def folder_year(name: str) -> int | None:
    match = _FOLDER_YEAR.search(leaf(name))
    return int(match[1]) if match else None


def folder_title(name: str) -> str:
    """A folder's series name without its year and id: "Tokyo Ghoul - re (2014)" -> "Tokyo Ghoul - re"."""
    cleaned = _FOLDER_YEAR.sub("", leaf(name))
    cleaned = re.sub(r"\s*[-–—]\s*\d{5,6}\s*$", "", cleaned)
    return " ".join(cleaned.split())


# -- Which entry each folder is ---------------------------------------------------


_SERIES: tuple[tuple[int, int], dict[int, dict[str, Any]]] | None = None


def series() -> dict[int, dict[str, Any]]:
    """Every series in the libraries bankai knows the entry of: {aid: {folder, kind, how}}.

    Parsed again only when the file changed: read once per folder, a library build parsed it 543
    times (Nov 2026). The rows are shared; callers read them, writers go through ``_read``.
    """
    global _SERIES
    try:
        stat = _store_path().stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = (0, 0)
    if _SERIES is None or _SERIES[0] != stamp:
        _SERIES = (stamp, {int(key): row for key, row in _read()["series"].items() if str(key).isdigit()})
    return dict(_SERIES[1])


def unmatched() -> list[dict[str, Any]]:
    """Folders no entry is known for yet: matched by hand."""
    known = {(row.get("kind"), row.get("folder")) for row in series().values()}
    return [
        {"kind": kind, "folder": name, "title": folder_title(name), "year": folder_year(name)}
        for kind in KINDS
        for name in folders(kind)
        if (kind, name) not in known
    ]


def folder_of(anilist_id: int) -> Path | None:
    row = series().get(int(anilist_id))
    if not row:
        return None
    path = root(row.get("kind") or "manga") / row["folder"]
    return path if path.is_dir() else None


def _remember(anilist_id: int, kind: str, folder: str, how: str) -> None:
    data = _read()
    # One folder is one entry: a folder matched again leaves its old entry.
    for key, row in list(data["series"].items()):
        if row.get("kind") == kind and row.get("folder") == folder and key != str(anilist_id):
            data["series"].pop(key)
    old = data["series"].get(str(anilist_id)) or {}
    data["series"][str(anilist_id)] = {"folder": folder, "kind": kind, "how": how, "at": time.time()}
    if old.get("folder") == folder and old.get("moved"):
        data["series"][str(anilist_id)]["moved"] = old["moved"]
    data["unmatched"].pop(f"{kind}/{folder}", None)
    _write(data)


def match(kind: str, folder: str, anilist_id: int) -> None:
    """A folder matched by hand."""
    if kind not in KINDS or folder not in folders(kind):
        raise ValueError(f"no such folder: {folder}")
    _remember(int(anilist_id), kind, folder, "hand")


def _openpanel_matches() -> dict[str, int]:
    """OpenPanel's own folder -> AniList id, from its database (read only); {} without one."""
    path = OPENPANEL_DATABASE
    if not path.is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as db:
            rows = db.execute("SELECT name, anilist_id FROM series WHERE anilist_id IS NOT NULL").fetchall()
    except sqlite3.Error as exc:
        log.info("[manga] OpenPanel's database could not be read: %s", exc)
        return {}
    return {str(name): int(aid) for name, aid in rows if aid}


async def adopt(*, max_searches: int = 10) -> dict[str, int]:
    """Learn which entry each folder is that bankai does not know yet; returns counts."""
    data = _read()
    known = {(row.get("kind"), row.get("folder")) for row in data["series"].values()}
    openpanel = _openpanel_matches()
    found = searched = 0
    now = time.time()
    for kind in KINDS:
        for name in folders(kind):
            if (kind, name) in known:
                continue
            how, aid = "folder", folder_id(name)
            # OpenPanel names a series after the folder holding its books.
            if aid is None and leaf(name) in openpanel:
                how, aid = "openpanel", openpanel[leaf(name)]
            if aid is None:
                tried = data["unmatched"].get(f"{kind}/{name}") or {}
                if searched >= max_searches or now - float(tried.get("tried") or 0) < _RETRY_SECONDS:
                    continue
                searched += 1
                how, aid = "search", await _search(kind, name)
                if aid is None:
                    data = _read()
                    data["unmatched"][f"{kind}/{name}"] = {"tried": now}
                    _write(data)
                    continue
            taken = data["series"].get(str(aid))
            if taken and (taken.get("kind"), taken.get("folder")) != (kind, name) and (root(taken.get("kind") or "manga") / taken["folder"]).is_dir():
                # Two folders of one series: the first keeps it, this one is matched by hand.
                data = _read()
                data["unmatched"][f"{kind}/{name}"] = {"tried": now, "same_as": taken["folder"]}
                _write(data)
                continue
            _remember(aid, kind, name, how)
            data = _read()
            found += 1
    if found:
        await manga_meta.ensure(list(series()), refresh=False)
    return {"adopted": found, "searched": searched}


async def _search(kind: str, name: str) -> int | None:
    """AniList's entry for a folder, when one of its titles is the folder's name and the year agrees."""
    title, year = folder_title(name), folder_year(name)
    try:
        choices = await manga_meta.search(title, limit=10, kind=kind)
    except Exception as exc:
        log.info("[manga] AniList could not be searched for %s: %s", name, exc)
        return None
    wanted = anilist.normalise(title)
    for choice in choices:
        names = {anilist.normalise(str(value)) for value in (choice.get("english"), choice.get("romaji")) if value}
        if wanted in names and (year is None or choice.get("year") in (None, year)):
            return int(choice["anilist_id"])
    return None


# -- What a series holds ---------------------------------------------------------


# A folder's books by its mtime: listing a folder over 9p costs a tenth of a
# second, telling whether it changed a millisecond.
_STATS: dict[str, tuple[int, dict[str, Any]]] = {}


def stats(anilist_id: int) -> dict[str, Any]:
    """A series' folder: its volumes and chapters by its files' names, its books (and their names) and their size."""
    empty: dict[str, Any] = {"volumes": set(), "chapters": set(), "books": 0, "size": 0, "names": []}
    folder = folder_of(anilist_id)
    if folder is None:
        return empty
    try:
        stamp = folder.stat().st_mtime_ns
        kept = _STATS.get(str(folder))
        if kept is not None and kept[0] == stamp:
            return {**kept[1], "volumes": set(kept[1]["volumes"]), "chapters": set(kept[1]["chapters"])}
        # One stat a file: on the 9p mount each costs ~2 ms.
        with os.scandir(folder) as listing:
            entries = [entry for entry in listing if entry.is_file() and Path(entry.name).suffix.lower() in manga_names.BOOK_EXTENSIONS]
        found = {**empty, "volumes": set(), "chapters": set(), "books": len(entries), "size": sum(entry.stat().st_size for entry in entries),
                 "names": sorted(entry.name for entry in entries)}
    except OSError:
        return empty
    for entry in entries:
        number = manga_names.file_number(entry.name)
        if number:
            found["volumes" if number[0] == "volume" else "chapters"].add(number[1])
    _STATS[str(folder)] = (stamp, found)
    return {**found, "volumes": set(found["volumes"]), "chapters": set(found["chapters"])}


def forget(folder: Path) -> None:
    """A folder bankai just changed: its books read again next time (``stats``, ``manga_contents.holdings``)."""
    from bankai.web import manga_contents

    _STATS.pop(str(folder), None)
    manga_contents.forget(folder)


def owned(anilist_id: int) -> tuple[set[float], set[float]]:
    """The volumes and chapters a series' folder holds, by its files' names."""
    found = stats(anilist_id)
    return found["volumes"], found["chapters"]


def files(anilist_id: int) -> list[dict[str, Any]]:
    folder = folder_of(anilist_id)
    if folder is None:
        return []
    rows = []
    try:
        with os.scandir(folder) as listing:
            for entry in sorted(listing, key=lambda item: item.name):
                if entry.is_file() and Path(entry.name).suffix.lower() in manga_names.BOOK_EXTENSIONS:
                    rows.append({"name": entry.name, "size": entry.stat().st_size})
    except OSError:
        return rows
    return rows


def new_folder_name(anilist_id: int) -> str:
    """A new series' folder: "Title (Year) - id", the title as OpenPanel's folders write it."""
    entry = manga_meta.media(anilist_id)
    title = manga_meta.name(anilist_id) or f"AniList {anilist_id}"
    title = title.replace(": ", " - ").replace(":", " -")
    base = sanitise(title, fallback=f"AniList {anilist_id}")
    year = entry.get("year")
    return f"{base} ({year}) - {anilist_id}" if year else f"{base} - {anilist_id}"


def ensure_folder(anilist_id: int, kind: str) -> Path:
    """The series' folder, made (and remembered) when it has none yet: inside its collection's
    folder (manga_collections) when another entry of it is on disk -- which then moves in too."""
    from bankai.web import manga_collections

    existing = folder_of(anilist_id)
    if existing is not None:
        return existing
    name = new_folder_name(anilist_id)
    collection = manga_collections.load().collection_of(int(anilist_id))
    others = [aid for aid in (collection.members if collection else []) if aid != int(anilist_id) and aid in series()]
    if collection is not None and others:
        name = f"{manga_collections.folder_name(collection)}/{name}"
    path = root(kind) / name
    path.mkdir(parents=True, exist_ok=True)
    _remember(int(anilist_id), kind, name, "bankai")
    if others:
        # Now two of the collection are on disk: the others move into its folder too (journaled).
        from bankai.web import manga_layout

        try:
            manga_layout.apply(only=set(others), check=False)
        except OSError as exc:
            log.warning("[manga] %s's collection could not be gathered: %s", name, exc)
    return path


def move(from_id: int, names: list[str], to_id: int, renames: dict[str, str] | None = None) -> dict[str, Any]:
    """Books of one series' folder that are another series' (Jujutsu Kaisen's "Vol 00" is
    Jujutsu Kaisen 0): into that series' folder, made when it has none, each under its
    new name where one is given. The same series renumbers them in place ("Chapter 0168.1"
    -> "Chapter 0001"), names swapped among them too. Every name is checked before anything moves."""
    source = folder_of(from_id)
    if source is None:
        raise ValueError("the series has no folder")
    same = int(from_id) == int(to_id)
    renames = renames or {}
    plan: list[tuple[Path, str]] = []
    for name in dict.fromkeys(names):
        path = source / name
        if Path(name).name != name or not path.is_file():
            raise ValueError(f"no such file: {name}")
        new = sanitise(str(renames.get(name) or name).strip(), fallback=name)
        if Path(new).suffix.lower() != path.suffix.lower():
            new += path.suffix
        plan.append((path, new))
    targets = [new for _path, new in plan]
    if len(set(targets)) != len(targets):
        raise ValueError("two files would get the same name")
    if same:
        return _renumber(source, plan)
    kind = manga_meta.kind_of(manga_meta.media(to_id).get("format"))
    target = ensure_folder(int(to_id), kind)
    taken = [new for new in targets if (target / new).exists()]
    if taken:
        raise ValueError(f"{target.name} has {', '.join(taken)} already")
    for path, new in plan:
        try:
            path.replace(target / new)
        except OSError:
            # Another drive: copied, then the original removed.
            shutil.move(str(path), str(target / new))
    # What left a series stays out of it when a download of it holds the same book again.
    data = _read()
    for aid, add, drop in ((from_id, [path.name for path, _new in plan], []), (to_id, [], targets)):
        row = data["series"].get(str(aid))
        if row is not None:
            row["moved"] = sorted({*row.get("moved", []), *add} - set(drop))
            if not row["moved"]:
                row.pop("moved")
    _write(data)
    forget(source)
    forget(target)
    log.info("[manga] %d file(s) from %s into %s", len(plan), source.name, target.name)
    return {"moved": len(plan), "folder": target.name, "names": targets}


def _renumber(folder: Path, plan: list[tuple[Path, str]]) -> dict[str, Any]:
    """Books renamed inside their folder: through a name of their own first, so two may swap."""
    plan = [(path, new) for path, new in plan if path.name != new]
    leaving = {path.name for path, _new in plan}
    taken = [new for _path, new in plan if (folder / new).exists() and new not in leaving]
    if taken:
        raise ValueError(f"{folder.name} has {', '.join(taken)} already")
    passing = []
    for index, (path, new) in enumerate(plan):
        between = folder / f".{index}.{os.getpid()}.renumber{path.suffix}"
        path.replace(between)
        passing.append((between, new))
    for between, new in passing:
        between.replace(folder / new)
    forget(folder)
    log.info("[manga] %d file(s) renumbered in %s", len(plan), folder.name)
    return {"moved": len(plan), "folder": folder.name, "names": [new for _path, new in plan]}


def remove(anilist_id: int) -> dict[str, Any]:
    """A series off the disk: its folder with every book in it, and the folder forgotten. Only the
    folder bankai knows as the series', and only a series folder of its library (``folders``); a
    collection's folder left empty goes with it."""
    aid = int(anilist_id)
    row = series().get(aid)
    folder = folder_of(aid)
    count = size = 0
    if row is not None and folder is not None:
        target = folder.resolve()
        base = root(row.get("kind") or "manga").resolve()
        if target.parent != base and target.parent.parent != base:
            raise ValueError(f"{folder} is not a series folder of the library")
        for path in target.rglob("*"):
            if path.is_file():
                count += 1
                size += path.stat().st_size
        shutil.rmtree(target)
        log.info("[manga] %s deleted: %d file(s)", target.name, count)
        if target.parent != base and not any(target.parent.iterdir()):
            target.parent.rmdir()
    if row is not None:
        data = _read()
        data["series"].pop(str(aid), None)
        _write(data)
    return {"deleted_files": count, "deleted_bytes": size, "folder": (row or {}).get("folder")}


def relocate(anilist_id: int, folder: str) -> None:
    """A series' folder moved (manga_layout): remembered at its new path below the root."""
    data = _read()
    row = data["series"].get(str(int(anilist_id)))
    if row is not None:
        row["folder"] = folder
        _write(data)


def moved_away(anilist_id: int) -> set[str]:
    """The books moved from a series' folder to another series': not filed into it again."""
    return set((series().get(int(anilist_id)) or {}).get("moved") or [])
