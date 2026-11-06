"""Which chapters a CBZ holds, read from the names of its pages, and so which a series' folder holds.

Digital releases name every page after its chapter: "One Piece - c0036 (v005) -
p000.jpg", "Bleach v01/Chapter 001 - Death & Strawberry/001.jpg", "v01c01/000.jpg",
"001/01.jpg". A ZIP's list of names sits at its end, so reading it costs one
small read, not the archive. Kept per file in ``manga_contents.json`` by size
and mtime: a library of a few thousand volumes is read once. ``holdings`` puts
a folder's books together: which chapters it holds, in which book, and how
sure that is.
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
import json
import os
import re
import threading
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.web import manga_names

_CHAPTER_PATTERNS = (
    # "c0036", "c016x6" (an extra of chapter 16), "c138.5"; not a "[c2c]" tag
    re.compile(r"(?<![A-Za-z0-9])c(\d{1,4}(?:\.\d+)?)(?:x\d+)?(?![0-9.A-Za-z])", re.IGNORECASE),
    # "v01c01", "v01 c01"
    re.compile(r"v\d{1,3}[\s._-]*c(\d{1,4}(?:\.\d+)?)(?![0-9])", re.IGNORECASE),
    # "Chapter 001 - Death & Strawberry", "Ch.12", "Chap 5"
    re.compile(r"(?:^|[/\\\s_(\[-])(?:chapter|chap|ch)[\s._-]*(\d{1,4}(?:\.\d+)?)(?![0-9])", re.IGNORECASE),
)
_VOLUME_PATTERNS = (
    re.compile(r"\(v(\d{1,3})\)", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])v(\d{1,3})(?=[\s._-]*c\d|[\s_)\]/-]|$)", re.IGNORECASE),
)
_PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".jxl", ".bmp"}
_LOCK = threading.Lock()
_CACHE: dict[str, list] | None = None
# A folder's holdings by (folder, its mtime, complete, whole_series): a book added, removed or
# renamed changes the folder's mtime. On the 9p mount each file's stat costs ~2 ms.
_HOLDINGS: dict[tuple[str, int, bool, int], Holdings] = {}
# A gap wider than this among the chapters a volume's pages name parts its own from numbers
# that are not (another numbering, a slip); a narrower one is pages named for the chapter
# before. Seen in the library: holes of 1-4 chapters, strays 34 and 166 away.
_STRAY_GAP = 10


def page_number(name: str) -> tuple[float | None, int | None]:
    """A page's (chapter, volume), each None when its name does not say."""
    chapter = volume = None
    for pattern in _CHAPTER_PATTERNS:
        if match := pattern.search(name):
            chapter = float(match[1])
            break
    if chapter is None:
        # "001/01.jpg": a folder that is a bare number is a chapter.
        for part in name.replace("\\", "/").split("/")[:-1]:
            if part.isdigit() and len(part) <= 4 and not 1900 <= int(part) <= 2100:
                chapter = float(part)
                break
    for pattern in _VOLUME_PATTERNS:
        if match := pattern.search(name):
            volume = int(match[1])
            break
    return chapter, volume


def read(path: Path) -> list[tuple[float, int | None]]:
    """The (chapter, volume) pairs an archive's pages name, in chapter order; [] when they name none."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
    except (OSError, zipfile.BadZipFile, ValueError):
        return []
    found: dict[float, Counter] = {}
    for name in names:
        if Path(name).suffix.lower() not in _PICTURES:
            continue
        chapter, volume = page_number(name)
        if chapter is not None:
            found.setdefault(chapter, Counter())[volume] += 1
    return [(chapter, votes.most_common(1)[0][0]) for chapter, votes in sorted(found.items())]


def own_chapters(named: set[int]) -> list[int]:
    """The chapters a volume holds by its pages' names: the longest stretch of them without a
    wide gap, every chapter from its first to its last. A hole in it is pages named for the
    chapter before (The Promised Neverland's v15 names 126-129 "c125"); a number apart is
    another numbering (JoJo Part 5's first volume opens with Part 4's last chapters,
    172-174) or a slip ("c197" among 155-163 in Tokyo Ghoul:re's v15)."""
    if not named:
        return []
    ordered = sorted(named)
    stretches = [[ordered[0]]]
    for chapter in ordered[1:]:
        if chapter - stretches[-1][-1] > _STRAY_GAP:
            stretches.append([])
        stretches[-1].append(chapter)
    own = max(stretches, key=len)
    return list(range(own[0], own[-1] + 1))


def _store() -> Path:
    return bgjobs.jobs_root().parent / "manga_contents.json"


def _load() -> dict[str, list]:
    global _CACHE
    if _CACHE is None:
        try:
            raw = json.loads(_store().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        _CACHE = raw if isinstance(raw, dict) else {}
    return _CACHE


def _save() -> None:
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_load(), separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def chapters(paths: list[Path], *, budget: int | None = None) -> dict[str, list[tuple[float, int | None]]]:
    """Each archive's (chapter, volume) pairs, from the cache where the file did not change.

    ``budget`` caps how many archives are read anew (the rest are left out);
    None reads all.
    """
    result: dict[str, list[tuple[float, int | None]]] = {}
    read_now = 0
    with _LOCK:
        cache = _load()
        for path in paths:
            if path.suffix.lower() not in (".cbz", ".zip"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            kept = cache.get(str(path))
            if kept and kept[0] == stat.st_size and kept[1] == stat.st_mtime_ns:
                result[str(path)] = [(float(chapter), volume) for chapter, volume in kept[2]]
                continue
            if budget is not None and read_now >= budget:
                continue
            pairs = read(path)
            read_now += 1
            cache[str(path)] = [stat.st_size, stat.st_mtime_ns, pairs]
            result[str(path)] = pairs
        if read_now:
            _save()
    return result


@dataclasses.dataclass(frozen=True)
class Holdings:
    """Which chapters a series' folder holds, and in which of its books. Shared: read only."""

    # Each book's ("volume" | "chapter", number) from its name; None when it gives none.
    numbers: dict[str, tuple[str, float] | None]
    # Each archive's (chapter, volume) pairs, from its pages' names.
    pages: dict[str, list[tuple[float, int | None]]]
    # The first book of each whole volume number, with the whole chapters its pages name.
    volume_files: dict[int, tuple[str, list[int]]]
    # The first book of each whole chapter number, with the volume its pages name.
    chapter_files: dict[int, tuple[str, int | None]]
    # Chapter -> {kind: "volume" | "chapter", via: its book, volume, guessed, sure}.
    covered: dict[int, dict[str, Any]]
    # A finished series whose every volume the folder has.
    complete: bool = False

    @functools.cached_property
    def _sure(self) -> list[int]:
        return sorted(chapter for chapter, info in self.covered.items() if info["sure"])

    def lacks(self, chapter: int) -> bool:
        """Whether the folder lacks a chapter: no book holds it for sure (named, or where
        nothing else can be). A complete series lacks only the holes between the chapters
        its books name -- the rest is in volumes whose pages do not say."""
        if self.covered.get(chapter, {}).get("sure"):
            return False
        if self.complete:
            return bool(self._sure) and self._sure[0] < chapter < self._sure[-1]
        return True

    def volumes(self) -> set[float]:
        """Every volume number a book's name gives."""
        return {number[1] for number in self.numbers.values() if number and number[0] == "volume"}


def _books(folder: Path) -> list[Path]:
    try:
        with os.scandir(folder) as entries:
            found = [Path(entry.path) for entry in entries if entry.is_file() and Path(entry.name).suffix.lower() in manga_names.BOOK_EXTENSIONS]
    except OSError:
        return []
    return sorted(found, key=lambda path: path.name)


def forget(folder: Path) -> None:
    """A folder bankai just changed: read again next time, whatever its time says -- a book filed and
    the chapter files it replaced gone within one tick of the clock leave the folder's time as it was."""
    for key in [key for key in _HOLDINGS if key[0] == str(folder)]:
        _HOLDINGS.pop(key, None)


def holdings(folder: Path | None, *, complete: bool, whole_series: int) -> Holdings:
    """Which chapters a series' folder holds (manga_views.breakdown shows them).

    A volume holds the chapters its pages name (``own_chapters``), a chapter
    file its own chapter; two volumes one after the other hold every chapter
    between them. Volumes whose pages name none share the chapters between the
    volumes around them by volume number (a volume not on disk leaves its share
    missing); after the last, the rest of a finished series (``complete``,
    ``whole_series`` chapters long), else as many as the others average, or up
    to the chapter files after -- guessed, and sure only where every volume of
    the stretch is on disk and it ends at a volume whose pages say, or at the
    series' end.
    """
    try:
        key = (str(folder), folder.stat().st_mtime_ns, complete, whole_series) if folder else None
    except OSError:
        key = None
    if key and key in _HOLDINGS:
        return _HOLDINGS[key]
    books = _books(folder) if folder else []
    numbers = {path.name: manga_names.file_number(path.name) for path in books}
    found = chapters(books)
    pages = {path.name: found.get(str(path), []) for path in books}
    # The first book of each whole number; a second, and the rest, stand apart.
    volume_files: dict[int, tuple[str, list[int]]] = {}
    chapter_files: dict[int, tuple[str, int | None]] = {}
    for name, number in numbers.items():
        if not number or number[1] != int(number[1]):
            continue
        whole = int(number[1])
        if number[0] == "volume" and whole not in volume_files:
            volume_files[whole] = (name, own_chapters({int(chapter) for chapter, _volume in pages[name] if chapter == int(chapter)}))
        elif number[0] == "chapter" and whole not in chapter_files:
            said = [volume for _chapter, volume in pages[name] if volume is not None]
            chapter_files[whole] = (name, Counter(said).most_common(1)[0][0] if said else None)

    covered: dict[int, dict[str, Any]] = {}
    read = sorted((volume, name, named) for volume, (name, named) in volume_files.items() if volume)
    for volume, name, named in read:
        for chapter in named:
            covered.setdefault(chapter, {"kind": "volume", "via": name, "volume": volume, "sure": True})
    # A chapter between two volumes one after the other is in one of them under another's
    # number (Jujutsu Kaisen's v22 names 191-199 "c192"-"c200"): the first, worked out.
    for (volume, name, named), (following, _name, after) in itertools.pairwise(read):
        if named and after and following == volume + 1:
            for chapter in range(named[-1] + 1, after[0]):
                covered.setdefault(chapter, {"kind": "volume", "via": name, "volume": volume, "guessed": True, "sure": True})
    # Volumes whose pages name no chapter, a run of them at a time: the chapters between the
    # volumes around them, shared out by volume number. After the last: the rest of a
    # finished series, else as many as the others average, or up to the chapter files after.
    known = [(volume, named) for volume, _name, named in read if named]
    average = sum(len(named) for _volume, named in known) / len(known) if known else 0
    runs: list[list[tuple[int, str]]] = [[]]
    for volume, name, named in read:
        if not named:
            runs[-1].append((volume, name))
        elif runs[-1]:
            runs.append([])
    for run in filter(None, runs):
        below = [(volume, named) for volume, named in known if volume < run[0][0]]
        above = [(volume, named) for volume, named in known if volume > run[-1][0]]
        floor = max((volume for volume, _named in below), default=0)
        begin = max((max(named) for _volume, named in below), default=0) + 1
        if above:
            ceiling = min(volume for volume, _named in above) - 1
            end = min(min(named) for _volume, named in above) - 1
        else:
            ceiling = run[-1][0]
            after = [chapter for chapter in chapter_files if chapter >= begin]
            if complete and whole_series >= begin:
                end = whole_series
            elif known:
                end = begin + round(average * (ceiling - floor)) - 1
            else:
                end = min(after, default=begin) - 1
            if after:
                end = min(end, min(after) - 1)
        count, slots = end - begin + 1, ceiling - floor
        if count <= 0:
            continue
        sure = all(volume in volume_files for volume in range(floor + 1, ceiling + 1)) and (bool(above) or (complete and whole_series >= begin))
        for volume, name in run:
            share = volume - floor - 1
            for chapter in range(begin + share * count // slots, begin + (share + 1) * count // slots):
                covered.setdefault(chapter, {"kind": "volume", "via": name, "volume": volume, "guessed": True, "sure": sure})
    for chapter, (name, volume) in sorted(chapter_files.items()):
        covered.setdefault(chapter, {"kind": "chapter", "via": name, "volume": volume, "sure": True})
    result = Holdings(numbers=numbers, pages=pages, volume_files=volume_files, chapter_files=chapter_files, covered=covered, complete=complete)
    if key:
        for old in [old for old in _HOLDINGS if old[0] == key[0]]:
            _HOLDINGS.pop(old, None)
        _HOLDINGS[key] = result
    return result
