"""Which AniList episode each anime file is.

The anime library is laid out by bankai (see anime_layout), so a file's place
says what it is -- but only while the layout is current, and a name says
nothing about a file filed by hand. The index says it outright: each file,
by its path below the anime root, is one or more episodes of AniList entries.
The same relative path is used in staging (``<staging>/Shows/...``), so a
file keeps its identity when Transfer moves it into the library.

Written when a download is published, when the layout moves a file, when an
episode is deleted and when a folder is matched by hand; seeded once from
Shoko's links by the migration.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from bankai.cli import bgjobs
from bankai.config import get_settings

Pairs = list[tuple[int, int]]

_CACHE: tuple[float, dict[str, Pairs]] | None = None


def _path() -> Path:
    return bgjobs.jobs_root().parent / "anime_files.json"


def _lock():
    from bankai.web.erai import _STATE_LOCK

    return _STATE_LOCK


def anime_root() -> Path:
    return Path(get_settings().transfer.anime_shows_dir)


def staging_root() -> Path:
    return Path(get_settings().output.directory) / "Shows"


def key(path: str | Path) -> str | None:
    """A file's key: its path below the anime root or staging, with forward slashes."""
    raw = Path(path)
    for root in (anime_root(), staging_root()):
        try:
            return PurePosixPath(*raw.relative_to(root).parts).as_posix()
        except ValueError:
            continue
    return None


def locations(rel: str) -> list[Path]:
    """Where a key can be: in the library and in staging."""
    parts = PurePosixPath(rel).parts
    return [anime_root().joinpath(*parts), staging_root().joinpath(*parts)]


def _read() -> dict[str, Pairs]:
    global _CACHE
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _CACHE is None or _CACHE[0] != mtime:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rows: dict[str, Pairs] = {}
        for rel, pairs in (raw.items() if isinstance(raw, dict) else ()):
            clean = [(int(a), int(e)) for a, e in pairs or [] if str(a).isdigit() and str(e).isdigit()]
            if clean:
                rows[str(rel)] = clean
        _CACHE = (mtime, rows)
    return _CACHE[1]


def _write(rows: dict[str, Pairs]) -> None:
    global _CACHE
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    data = {rel: [list(pair) for pair in pairs] for rel, pairs in sorted(rows.items())}
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    _CACHE = None


def load() -> dict[str, Pairs]:
    """Key -> [(AniList id, episode)]; a copy, safe to change."""
    with _lock():
        return {rel: list(pairs) for rel, pairs in _read().items()}


@contextmanager
def editing() -> Iterator[dict[str, Pairs]]:
    """The index to change in place, saved when the block ends without error."""
    with _lock():
        rows = {rel: list(pairs) for rel, pairs in _read().items()}
        yield rows
        _write(rows)


def record(path: str | Path, pairs: Pairs) -> None:
    rel = key(path)
    if rel is None:
        return
    with editing() as rows:
        if pairs:
            rows[rel] = [(int(a), int(e)) for a, e in pairs]
        else:
            rows.pop(rel, None)


def forget(path: str | Path) -> None:
    record(path, [])


def of(path: str | Path) -> Pairs:
    rel = key(path)
    return list(_read().get(rel, [])) if rel else []


def by_entry(rows: dict[str, Pairs] | None = None) -> dict[int, dict[int, list[str]]]:
    """AniList id -> episode -> the keys of its files."""
    found: dict[int, dict[int, list[str]]] = {}
    for rel, pairs in (rows if rows is not None else _read()).items():
        for aid, episode in pairs:
            found.setdefault(aid, {}).setdefault(episode, []).append(rel)
    return found


def existing(rel: str) -> list[Path]:
    """The copies of a key that are on disk: the library's, then staging's."""
    return [path for path in locations(rel) if path.is_file()]


def on_disk(anilist_id: int, *, rows: dict[str, Pairs] | None = None) -> dict[int, list[Path]]:
    """One entry's episodes that have a file, and those files."""
    found: dict[int, list[Path]] = {}
    for episode, keys in by_entry(rows).get(int(anilist_id), {}).items():
        paths = [path for rel in keys for path in existing(rel)]
        if paths:
            found[episode] = paths
    return found


__all__ = [
    "Pairs",
    "anime_root",
    "by_entry",
    "editing",
    "existing",
    "forget",
    "key",
    "load",
    "locations",
    "of",
    "on_disk",
    "record",
    "staging_root",
]
