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


def folders(kind: str) -> list[str]:
    try:
        return sorted(entry.name for entry in root(kind).iterdir() if entry.is_dir() and not entry.name.startswith("."))
    except OSError:
        return []


def folder_id(name: str) -> int | None:
    """An AniList id in a folder's name, as OpenPanel reads it: five or six digits, not a year."""
    for match in _FOLDER_ID.finditer(name):
        number = int(match[1])
        if not 1900 <= number <= 2100:
            return number
    return None


def folder_year(name: str) -> int | None:
    match = _FOLDER_YEAR.search(name)
    return int(match[1]) if match else None


def folder_title(name: str) -> str:
    """A folder's series name without its year and id: "Tokyo Ghoul - re (2014)" -> "Tokyo Ghoul - re"."""
    cleaned = _FOLDER_YEAR.sub("", name)
    cleaned = re.sub(r"\s*[-–—]\s*\d{5,6}\s*$", "", cleaned)
    return " ".join(cleaned.split())


# -- Which entry each folder is ---------------------------------------------------


def series() -> dict[int, dict[str, Any]]:
    """Every series in the libraries bankai knows the entry of: {aid: {folder, kind, how}}."""
    return {int(key): row for key, row in _read()["series"].items() if str(key).isdigit()}


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
            if aid is None and name in openpanel:
                how, aid = "openpanel", openpanel[name]
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
    """A series' folder: its volumes and chapters by its files' names, its books and their size."""
    empty: dict[str, Any] = {"volumes": set(), "chapters": set(), "books": 0, "size": 0}
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
        found = {**empty, "volumes": set(), "chapters": set(), "books": len(entries), "size": sum(entry.stat().st_size for entry in entries)}
    except OSError:
        return empty
    for entry in entries:
        number = manga_names.file_number(entry.name)
        if number:
            found["volumes" if number[0] == "volume" else "chapters"].add(number[1])
    _STATS[str(folder)] = (stamp, found)
    return {**found, "volumes": set(found["volumes"]), "chapters": set(found["chapters"])}


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
    """The series' folder, made (and remembered) when it has none yet."""
    existing = folder_of(anilist_id)
    if existing is not None:
        return existing
    name = new_folder_name(anilist_id)
    path = root(kind) / name
    path.mkdir(parents=True, exist_ok=True)
    _remember(int(anilist_id), kind, name, "bankai")
    return path


def move(from_id: int, names: list[str], to_id: int, renames: dict[str, str] | None = None) -> dict[str, Any]:
    """Books of one series' folder that are another series' (Jujutsu Kaisen's "Vol 00" is
    Jujutsu Kaisen 0): into that series' folder, made when it has none, each under its
    new name where one is given. Every name is checked before anything moves."""
    source = folder_of(from_id)
    if source is None:
        raise ValueError("the series has no folder")
    if int(from_id) == int(to_id):
        raise ValueError("the files are in that series already")
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
    log.info("[manga] %d file(s) from %s into %s", len(plan), source.name, target.name)
    return {"moved": len(plan), "folder": target.name, "names": targets}


def moved_away(anilist_id: int) -> set[str]:
    """The books moved from a series' folder to another series': not filed into it again."""
    return set((series().get(int(anilist_id)) or {}).get("moved") or [])
