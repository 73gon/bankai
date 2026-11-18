"""Moving a title -- a film's folder, a show, an anime collection, a manga series -- from one library
root to another, dragged on the Library page (Nov 2026).

Nothing may be lost on the way, so a move never deletes before the new copy is whole:

* **Same drive**: one ``rename``. Atomic -- the folder is in one place or the other, never half.
* **Another drive** (``/mnt/g`` to ``/mnt/e``: separate mounts, rename fails with EXDEV): every file is
  copied into a hidden folder beside the target (``.<name>.bankai-move``), each written out to the disk
  (fsync) and its size checked against the source's. Then the source is looked at again -- a file that
  changed meanwhile (Tracks or Browser audio rewrote it) stops the move and the copy goes -- and only
  then is the hidden folder renamed to its name, and only after that is the source deleted.
* A target that exists already is never written into or over; a drive without room for the copy is
  refused before anything starts; a file being encoded or rewritten by the library worker stops it.

Any failure before the last step removes the hidden copy and leaves the source as it was. Moves run in
a thread of bankai-web, one at a time; the Dashboard's Workers card shows them (``lane``). A restart
cuts a copy off: the next start removes what it left (``recover``), the source untouched.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
_WORKER: threading.Thread | None = None
_CHUNK = 8 * 2**20
# Finished moves stay listed (Dashboard, the page) this long.
_KEEP_SECONDS = 6 * 3600
# Room left on the target drive after the copy.
_RESERVE = 2 * 2**30
_PARTIAL = ".{name}.bankai-move"
KINDS = ("movie", "show", "anime", "manga")


class MoveError(RuntimeError):
    pass


def _store_path() -> Path:
    return bgjobs.jobs_root().parent / "library_moves.json"


def _load() -> list[dict]:
    try:
        rows = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def _save(rows: list[dict]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _update(move_id: str, **fields: Any) -> None:
    with _LOCK:
        rows = _load()
        for row in rows:
            if row["id"] == move_id:
                row.update(fields)
        _save(rows)


def roots() -> dict[str, list[Path]]:
    """Every library root by kind, as the Library page lists them."""
    from bankai.config import get_settings

    settings = get_settings()
    web = settings.web
    manga = [Path(settings.manga.library_dir), Path(settings.manga.novels_dir)]
    return {
        "movie": [Path(p) for p in web.server_movie_dirs],
        "show": [Path(p) for p in web.server_show_dirs],
        "anime": [Path(p) for p in web.server_anime_dirs],
        "manga": manga,
    }


def kind_of_root(path: Path) -> str | None:
    for kind, listed in roots().items():
        if any(path == root for root in listed):
            return kind
    return None


def _busy_paths() -> list[str]:
    """Files the library worker is rewriting now: encode, browser audio, track cleanup."""
    from bankai.web import browser_audio, encode_library, track_cleanup

    found = []
    for module in (encode_library, browser_audio, track_cleanup):
        try:
            current = module.current_progress() or {}
        except Exception:  # noqa: BLE001 -- a heartbeat that cannot be read holds nothing
            continue
        path = current.get("path") or (current.get("current") or {}).get("path") if isinstance(current, dict) else None
        if path:
            found.append(str(path))
    return found


def _inventory(source: Path) -> list[tuple[Path, int, float]]:
    """Every file under the source (or the source itself), with size and mtime."""
    if source.is_file():
        stat = source.stat()
        return [(source, stat.st_size, stat.st_mtime)]
    out = []
    for folder, _dirs, files in os.walk(source):
        for name in files:
            path = Path(folder) / name
            stat = path.lstat()
            out.append((path, stat.st_size, stat.st_mtime))
    return out


def plan(source: str, target_root: str) -> dict[str, Any]:
    """What a move would do, for the confirmation: refused with a reason, or the kinds and sizes."""
    src = Path(source)
    dest_root = Path(target_root)
    source_root = src.parent
    from_kind, to_kind = kind_of_root(source_root), kind_of_root(dest_root)
    if from_kind is None:
        raise MoveError("the title is not directly in a library folder")
    if to_kind is None:
        raise MoveError("the target is not a library folder")
    if source_root == dest_root:
        raise MoveError("the title is in that folder already")
    if not src.exists():
        raise MoveError("the title is no longer there")
    if not dest_root.is_dir():
        raise MoveError(f"{dest_root} cannot be reached")
    target = dest_root / src.name
    if target.exists() or (dest_root / _PARTIAL.format(name=src.name)).exists():
        raise MoveError(f"{target} exists already: nothing is moved over it")
    with _LOCK:
        if any(row["source"] == str(src) and row["status"] in ("queued", "running") for row in _load()):
            raise MoveError("the title is being moved already")
    files = _inventory(src)
    size = sum(item[1] for item in files)
    same_drive = _same_drive(source_root, dest_root)
    if not same_drive:
        free = shutil.disk_usage(dest_root).free
        if free < size + _RESERVE:
            raise MoveError(f"not enough room on the target drive: {_gib(size)} to copy, {_gib(free)} free")
    warnings = []
    if from_kind != to_kind:
        warnings.append(f"This is a {_KIND_NAMES[from_kind]} folder; the target holds {_KIND_NAMES[to_kind]}s.")
    if from_kind == "anime" or to_kind == "anime":
        warnings.append("Anime is filed by bankai under its anime folder by AniList; a moved collection may be laid out again there.")
    if from_kind == "manga" or to_kind == "manga":
        warnings.append("OpenPanel finds the series in its new place on its next scan.")
    return {
        "source": str(src), "target": str(target), "from_kind": from_kind, "to_kind": to_kind,
        "files": len(files), "size": size, "same_drive": same_drive, "warnings": warnings,
    }


_KIND_NAMES = {"movie": "movie", "show": "show", "anime": "anime", "manga": "manga"}


def _gib(size: float) -> str:
    return f"{size / 2**30:.1f} GiB"


def _same_drive(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def start(source: str, target_root: str) -> dict[str, Any]:
    """Queue a move (checked by ``plan`` first) and start the worker."""
    global _WORKER
    planned = plan(source, target_root)
    row = {
        "id": uuid.uuid4().hex[:12],
        "source": planned["source"],
        "target": planned["target"],
        "title": Path(planned["source"]).name,
        "size": planned["size"],
        "files": planned["files"],
        "done_bytes": 0,
        "status": "queued",
        "step": "Waiting",
        "error": None,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
    }
    with _LOCK:
        rows = [r for r in _load() if r["status"] in ("queued", "running") or time.time() - float(r.get("finished_at") or 0) < _KEEP_SECONDS]
        rows.append(row)
        _save(rows)
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_work, name="library-moves", daemon=True)
            _WORKER.start()
    return row


def listing() -> list[dict]:
    with _LOCK:
        return [r for r in _load() if r["status"] in ("queued", "running") or time.time() - float(r.get("finished_at") or 0) < _KEEP_SECONDS]


def _work() -> None:
    while True:
        with _LOCK:
            row = next((r for r in _load() if r["status"] == "queued"), None)
            if row is None:
                return
            _update(row["id"], status="running", started_at=time.time(), step="Starting")
        try:
            how = move(row)
            _update(row["id"], status="done", step=how, finished_at=time.time(), done_bytes=row["size"])
        except Exception as exc:  # noqa: BLE001 -- every failure is the move's outcome
            log.warning("Move of %s failed: %s", row["source"], exc)
            _update(row["id"], status="failed", error=str(exc), finished_at=time.time())
        _refresh(Path(row["source"]).parent, Path(row["target"]).parent)


def _refresh(*folders: Path) -> None:
    """The page and the libraries read the held tree: read both roots again."""
    from bankai.web import library_walk, media as media_mod

    media_mod.invalidate_server_cache()
    for folder in folders:
        try:
            library_walk.directories(folder, rescan=True)
        except Exception:  # noqa: BLE001
            log.exception("Could not read %s again after a move", folder)


def move(row: dict) -> str:
    """Move one title; returns how ("Renamed" or "Copied and checked"). Raises with the source untouched."""
    src, target = Path(row["source"]), Path(row["target"])
    if target.exists():
        raise MoveError(f"{target} exists already")
    busy = [path for path in _busy_paths() if path == str(src) or path.startswith(str(src) + os.sep)]
    if busy:
        raise MoveError(f"{Path(busy[0]).name} is being rewritten by the library worker; move it once that is done")
    try:
        os.rename(src, target)
        return "Renamed"
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    return _copy_then_delete(row, src, target)


def _copy_then_delete(row: dict, src: Path, target: Path) -> str:
    partial = target.parent / _PARTIAL.format(name=target.name)
    if partial.exists():
        raise MoveError(f"{partial} exists: an earlier move's leftovers")
    before = _inventory(src)
    total = sum(item[1] for item in before) or 1
    done = 0
    last_saved = 0.0
    try:
        if src.is_file():
            pairs = [(src, partial)]
        else:
            partial.mkdir()
            pairs = [(path, partial / path.relative_to(src)) for path, _size, _mtime in before]
            for folder, dirs, _files in os.walk(src):
                for name in dirs:
                    (partial / Path(folder).relative_to(src) / name).mkdir(parents=True, exist_ok=True)
        for (path, size, _mtime), (_same, dest) in zip(before, pairs):
            _update(row["id"], step=f"Copying {path.name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as reader, dest.open("xb") as writer:
                while chunk := reader.read(_CHUNK):
                    writer.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last_saved > 1:
                        last_saved = time.monotonic()
                        _update(row["id"], done_bytes=done, percent=100.0 * done / total)
                writer.flush()
                os.fsync(writer.fileno())
            shutil.copystat(path, dest)
            if dest.stat().st_size != size:
                raise MoveError(f"{path.name}: the copy is {dest.stat().st_size} bytes, the original {size}")
        _update(row["id"], step="Checking", done_bytes=done, percent=100.0)
        # The source as it is now, against what was copied: a file rewritten meanwhile is not the copy.
        after = {path: (size, mtime) for path, size, mtime in _inventory(src)}
        if after != {path: (size, mtime) for path, size, mtime in before}:
            raise MoveError("files changed while being copied; nothing was moved")
        copied = sum(1 for _ in (_inventory(partial)))
        if copied != len(before):
            raise MoveError(f"{copied} files copied of {len(before)}")
        if target.exists():
            raise MoveError(f"{target} appeared meanwhile")
        os.rename(partial, target)
    except BaseException:
        _remove(partial)
        raise
    # The copy is whole and in place: only now the original goes.
    _update(row["id"], step="Removing the original")
    _remove(src)
    return "Copied, checked, original removed"


def _remove(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    except FileNotFoundError:
        pass


def recover() -> None:
    """At startup: a move cut off by a restart left a hidden copy; it goes, the source stays."""
    with _LOCK:
        rows = _load()
        changed = False
        for row in rows:
            if row["status"] == "running":
                target = Path(row["target"])
                _remove(target.parent / _PARTIAL.format(name=target.name))
                # The original is untouched: it goes again, from the start, with the rest queued.
                row.update(status="queued", step="Waiting (cut off by a restart, copying again)", done_bytes=0, started_at=None)
                changed = True
        if changed:
            _save(rows)
        queued = any(row["status"] == "queued" for row in rows)
    if queued:
        global _WORKER
        _WORKER = threading.Thread(target=_work, name="library-moves", daemon=True)
        _WORKER.start()


__all__ = ["KINDS", "MoveError", "listing", "plan", "recover", "roots", "start"]


def title_folders(paths: list[str], kind: str) -> list[Path]:
    """The title folders (straight under a library root of ``kind``) these files are in."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        for root in roots().get(kind, []):
            try:
                inside = path.relative_to(root)
            except ValueError:
                continue
            if len(inside.parts) >= 2:
                folder = root / inside.parts[0]
                if folder not in found:
                    found.append(folder)
            break
    return found


def delete_title(paths: list[str], kind: str) -> dict[str, Any]:
    """Delete a library title: the folders its files are in, each straight under a library root.
    Refused while a file of it is rewritten (encode, audio, tracks) or the title is being moved."""
    folders = title_folders(paths, kind)
    if not folders:
        raise MoveError("its files are in no library folder")
    busy = [path for path in _busy_paths() if any(Path(path).is_relative_to(folder) for folder in folders)]
    if busy:
        raise MoveError(f"{Path(busy[0]).name} is being rewritten (encode, audio or tracks): try once that is done")
    with _LOCK:
        moving = [row for row in _load() if row["status"] in ("queued", "running")
                  and any(Path(row["source"]) == folder for folder in folders)]
    if moving:
        raise MoveError("it is being moved: try once the move is done")
    size = 0
    for folder in folders:
        for item in folder.rglob("*"):
            try:
                size += item.stat().st_size if item.is_file() else 0
            except OSError:
                pass
        _remove(folder)
        log.info("[library] deleted %s", folder)
    _refresh(*{folder.parent for folder in folders})
    return {"deleted": [str(folder) for folder in folders], "size": size}

