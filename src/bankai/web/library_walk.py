"""What video files sit under the library roots, without walking them per request.

The libraries live on Windows drives reached from WSL over 9p, where every
directory listing and every stat is a round trip. A full walk of the anime
library is ~10 s, the three Movies & Shows roots another ~5 s, and the pages,
the sidebar and the codec sweep each used to pay that on their own.

So the answer is held as a tree of directories, each with its video files and
the modification time it had when listed. Adding, removing or renaming a file
changes its directory's mtime, so keeping the tree current only needs a stat
per directory; only directories that changed are listed again. The scheduler
does that in the background every minute (:func:`refresh_all`), and requests
read the tree from memory. It is saved to disk, so a restart does not start
from nothing either.

A file rewritten in place under the same name changes no directory, so once an
hour the refresh ignores the mtimes and lists everything again.
"""

# Plain strings and os calls on purpose: this touches every directory of every
# library, and a Path per entry is measurable over thousands of them.
# ruff: noqa: PTH112, PTH116, PTH118, PTH122

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts"}
FULL_WALK_SECONDS = 3600.0
# A root no page has asked about for this long -- a path since removed from
# the settings -- stops being refreshed and is dropped.
UNUSED_SECONDS = 24 * 3600.0
# Wider than the filesystem's timestamp tick and any clock skew between WSL
# and Windows, whose clock stamps the files on /mnt/*.
_RACY_NS = 2_000_000_000

# root -> {"validated_at", "full_at", "dirs": {dir: {"mtime_ns", "files", "subdirs"}}}
_TREES: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()
# One refresh of a given root at a time; a second caller waits for it.
_ROOT_LOCKS: dict[str, threading.Lock] = {}
_LOADED = False
_STALE: set[str] = set()


def _store_path() -> Path:
    return bgjobs.jobs_root().parent / "library_walk.json"


def _root_lock(root: str) -> threading.Lock:
    with _LOCK:
        return _ROOT_LOCKS.setdefault(root, threading.Lock())


def _load() -> None:
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        _LOADED = True
        try:
            saved = json.loads(_store_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(saved, dict):
            for root, tree in saved.items():
                if isinstance(tree, dict) and isinstance(tree.get("dirs"), dict):
                    _TREES.setdefault(root, tree)


def _save() -> None:
    with _LOCK:
        payload = json.dumps(_TREES)
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not save the library walk: %s", exc)


def _trusted(node: dict[str, Any], mtime_ns: int) -> bool:
    """Can an unchanged mtime be believed to mean an unchanged directory?

    Only if the directory was already this old when it was listed. Directory
    timestamps move in coarse ticks -- 170 of 200 files created right after a
    listing on NTFS left the mtime exactly as it was -- so a change landing in
    the same tick as the listing is invisible to the comparison. A directory
    that recent is listed again next time, the way git treats "racy" files.
    """
    return (
        node.get("mtime_ns") == mtime_ns
        and node.get("listed_at_ns", 0) - mtime_ns > _RACY_NS
    )


def _list(directory: str) -> dict[str, Any] | None:
    """One directory's video files and subdirectories, as it is now."""
    listed_at_ns = time.time_ns()
    try:
        mtime_ns = os.stat(directory).st_mtime_ns
        entries = os.scandir(directory)
    except OSError:
        return None
    files: list[list] = []
    subdirs: list[str] = []
    with entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(entry.name)
                elif os.path.splitext(entry.name)[1].casefold() in VIDEO_SUFFIXES:
                    stat = entry.stat()
                    files.append([entry.name, stat.st_size, stat.st_mtime])
            except OSError:
                continue
    return {"mtime_ns": mtime_ns, "listed_at_ns": listed_at_ns, "files": files, "subdirs": subdirs}


def _refresh(root: str, *, full: bool) -> dict[str, Any]:
    """Bring one root's tree up to date and return it."""
    with _LOCK:
        old = (_TREES.get(root) or {}).get("dirs") or {}
    dirs: dict[str, dict] = {}
    listed = 0

    def visit(directory: str) -> None:
        nonlocal listed
        cached = None if full else old.get(directory)
        if cached is not None:
            try:
                unchanged = _trusted(cached, os.stat(directory).st_mtime_ns)
            except OSError:
                return  # gone
            node = cached if unchanged else _list(directory)
            listed += not unchanged
        else:
            node = _list(directory)
            listed += 1
        if node is None:
            return
        dirs[directory] = node
        for name in node["subdirs"]:
            visit(os.path.join(directory, name))

    started = time.time()
    if os.path.isdir(root):
        visit(root)
    tree = {
        "used_at": (_TREES.get(root) or {}).get("used_at", started),
        "validated_at": started,
        "full_at": started if full else (_TREES.get(root) or {}).get("full_at", 0.0),
        "dirs": dirs,
    }
    with _LOCK:
        _TREES[root] = tree
    if listed:
        log.debug("library walk: re-listed %d of %d directories under %s", listed, len(dirs), root)
    return tree


def _tree(root: str, *, rescan: bool) -> dict[str, Any]:
    _load()
    with _LOCK:
        tree = _TREES.get(root)
        if tree is not None:
            tree["used_at"] = time.time()
            rescan = rescan or root in _STALE
    if tree is not None and not rescan:
        return tree
    with _root_lock(root):
        with _LOCK:
            tree = _TREES.get(root)
        # Someone else may have built it while this caller waited.
        if tree is None or rescan:
            tree = _refresh(root, full=tree is None)
            with _LOCK:
                _STALE.discard(root)
            _save()
    return tree


def mark_stale() -> None:
    """Have the next read of every root check it against the disk first.

    For changes this process makes itself, such as a rename on the Server
    page, which should show on the next load rather than within a minute.
    Only the directories that changed are listed again.
    """
    with _LOCK:
        _STALE.update(_TREES)


def directories(root: str | Path, *, rescan: bool = False) -> dict[str, dict]:
    """The held tree of one root: directory -> its video files and subdirectories."""
    return _tree(str(root), rescan=rescan)["dirs"]


def files(roots: list[str | Path], *, rescan: bool = False) -> list[dict]:
    """Every video file under ``roots``, from the held tree.

    The first call for a root walks it; later calls answer from memory and
    the scheduler keeps the tree current. ``rescan`` checks it against the
    disk before answering, which is what the pages' Rescan buttons ask for.
    """
    out: list[dict] = []
    for raw in roots:
        root = str(raw)
        tree = _tree(root, rescan=rescan)
        base = Path(root)
        for directory, node in tree["dirs"].items():
            for name, size, mtime in node["files"]:
                path = Path(directory) / name
                try:
                    relative = path.relative_to(base)
                except ValueError:
                    continue
                parts = relative.parts
                out.append(
                    {
                        "path": str(path),
                        "rel_path": str(relative),
                        "name": name,
                        "series": parts[0] if len(parts) > 1 else path.stem,
                        "season": parts[1] if len(parts) > 2 else None,
                        "size": size,
                        "mtime": mtime,
                        "root": root,
                    }
                )
    return out


def refresh_all() -> int:
    """Check every root in use against the disk; for the scheduler.

    Returns how many roots were refreshed. Roots nobody has asked for are not
    walked: which ones matter is decided by the pages and settings in use.
    """
    _load()
    now = time.time()
    with _LOCK:
        for stale in [r for r, t in _TREES.items() if now - t.get("used_at", now) >= UNUSED_SECONDS]:
            _TREES.pop(stale)
        roots = list(_TREES)
    for root in roots:
        with _root_lock(root):
            with _LOCK:
                full_at = (_TREES.get(root) or {}).get("full_at", 0.0)
            _refresh(root, full=now - full_at >= FULL_WALK_SECONDS)
    if roots:
        _save()
    return len(roots)


def forget() -> None:
    """Drop everything held, in memory and on disk; for tests and rescans."""
    global _LOADED
    with _LOCK:
        _TREES.clear()
        _LOADED = True
    _save()
