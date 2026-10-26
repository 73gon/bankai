"""Files of a download bankai could not say the episode of: kept for matching by hand.

A batch carries more than its entry's episodes. SavI0r's Fullmetal Alchemist:
Brotherhood holds the 64 episodes as S01E01..S01E64 and, beside them, four
OVAs and the film as S00E01..S00E04 and S00E22 -- other AniList entries, under
numbers that would land on episodes 1-4 and 22 of the show. Extras with no
number at all (NCOP, NCED) are the same. The download used to fail on them, or
drop them; now each such file waits in Anime -> Review, where it is matched to
an AniList entry and episode as in Shoko: tick files, pick the entry, adjust the
numbers.

The files are kept beside staging (``<staging>/../anime_unsorted``), on the
same volume, as a second name for the download's bytes: instant, no extra
space, and the torrent can be removed meanwhile. Not inside staging: the
Library and Transfer read everything there. Rows are kept in
``anime_unsorted.json`` in the state directory, one per file.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

_SIDECAR_EXTS = {".ass", ".ssa", ".srt", ".sub", ".vtt"}


def root() -> Path:
    return Path(get_settings().output.directory).parent / "anime_unsorted"


def _path() -> Path:
    return bgjobs.jobs_root().parent / "anime_unsorted.json"


def _lock():
    from bankai.web.erai import _STATE_LOCK

    return _STATE_LOCK


def _read() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): row for key, row in raw.items() if isinstance(row, dict)} if isinstance(raw, dict) else {}


def _write(rows: dict[str, dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load() -> dict[str, dict[str, Any]]:
    with _lock():
        return _read()


def _safe(name: str) -> str:
    """A folder name of any release title."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip(" .")
    return re.sub(r"\s+", " ", cleaned)[:150] or "release"


def sidecars(video: Path) -> list[Path]:
    """Subtitle files beside a video, by its stem (escaped: "[1080p]" is a glob of its own)."""
    return [
        path
        for path in video.parent.glob(f"{glob.escape(video.stem)}.*")
        if path.suffix.casefold() in _SIDECAR_EXTS
    ]


def _link(source: Path, target: Path, *, replace: bool = False) -> None:
    """``target`` as a second name for ``source``, else a copy of it; one already there is kept unless ``replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not replace and target.exists() and target.stat().st_size == source.stat().st_size:
        return
    tmp = target.with_name(f".{target.name}.{os.getpid()}.part")
    tmp.unlink(missing_ok=True)
    try:
        try:
            os.link(source, tmp)
        except OSError:
            shutil.copy2(source, tmp)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def add(
    source: Path,
    *,
    base: Path,
    reason: str,
    release_title: str,
    detail_url: str | None,
    info_hash: str,
    anilist_id: int | None,
    season: int | None = None,
    episode: int | None = None,
) -> Path:
    """Keep a download's file (and its subtitles) for matching by hand. Returns where it is kept."""
    try:
        rel = source.relative_to(base) if base.is_dir() else Path(source.name)
    except ValueError:
        rel = Path(source.name)
    target = root() / _safe(release_title) / rel
    _link(source, target)
    for sidecar in sidecars(source):
        _link(sidecar, target.parent / (target.stem + sidecar.name[len(source.stem):]))
    key = hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:12]
    with _lock():
        rows = _read()
        rows[key] = {
            "id": key,
            "path": str(target),
            "name": target.name,
            "folder": "" if rel.parent == Path() else rel.parent.as_posix(),
            "size": target.stat().st_size,
            "reason": reason,
            "release_title": release_title,
            "detail_url": detail_url,
            "info_hash": info_hash.casefold(),
            "anilist_id": anilist_id,
            "season": season,
            "episode": episode,
            "added_at": time.time(),
        }
        _write(rows)
    log.info("[anime] kept for matching in Review: %s (%s)", target.name, reason)
    return target


def _remove_file(path: Path) -> None:
    for sidecar in sidecars(path):
        sidecar.unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    # Empty folders up to the root go with the last file.
    top = root()
    folder = path.parent
    while folder != top and top in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            break
        folder = folder.parent


def forget(ids: list[str], *, delete: bool) -> int:
    """Drop these rows; with ``delete`` their kept files too. Returns how many were dropped."""
    with _lock():
        rows = _read()
        gone = [rows.pop(key) for key in ids if key in rows]
        _write(rows)
    if delete:
        for row in gone:
            with suppress(OSError):
                _remove_file(Path(row["path"]))
    return len(gone)


def _suggestions(release_aids: list[int]) -> list[dict[str, Any]]:
    """The entries a release's leftover files most likely are: its own, those related to it, its collection's."""
    from bankai.metadata import anilist
    from bankai.web import anime_collections as collections

    store = collections.load()
    order: dict[int, str] = {}
    for aid in release_aids:
        order.setdefault(aid, "This release")
        for other, kind, _form in anilist.relations(aid):
            order.setdefault(int(other), str(kind or "").replace("_", " ").capitalize())
        row = store.collection_of(aid)
        for member in row.members if row is not None else []:
            order.setdefault(int(member), "Same show")
    out = []
    for aid, relation in order.items():
        kept = anilist.media(aid)
        out.append(
            {
                "anilist_id": aid,
                "title": collections.entry_name(aid),
                "format": kept.get("format"),
                "episodes": anilist.episode_count(aid),
                "year": kept.get("year"),
                "cover": anilist.cover(aid),
                "relation": relation,
            }
        )
    return out


def groups() -> list[dict[str, Any]]:
    """The kept files by the download they came from, newest first; rows whose file is gone are dropped."""
    from bankai.web import anime_collections as collections

    rows = load()
    missing = [key for key, row in rows.items() if not Path(row["path"]).is_file()]
    if missing:
        forget(missing, delete=False)
    by_release: dict[str, dict[str, Any]] = {}
    for key, row in rows.items():
        if key in missing:
            continue
        group = by_release.setdefault(
            row.get("info_hash") or row.get("release_title") or key,
            {
                "key": row.get("info_hash") or key,
                "release_title": row.get("release_title"),
                "detail_url": row.get("detail_url"),
                "anilist_id": row.get("anilist_id"),
                "anilist_title": collections.entry_name(row["anilist_id"]) if row.get("anilist_id") else None,
                "added_at": row.get("added_at") or 0,
                "files": [],
            },
        )
        group["added_at"] = max(group["added_at"], row.get("added_at") or 0)
        group["files"].append({field: row.get(field) for field in ("id", "name", "folder", "size", "reason", "season", "episode")})
    out = sorted(by_release.values(), key=lambda group: group["added_at"], reverse=True)
    for group in out:
        group["files"].sort(key=lambda item: (item["folder"] or "", item["name"].casefold()))
        group["size"] = sum(item["size"] or 0 for item in group["files"])
        group["suggestions"] = _suggestions([group["anilist_id"]] if group["anilist_id"] else [])
    return out


class Taken(Exception):
    """Episodes that already have a file in the library; filed over only when asked to."""

    def __init__(self, episodes: list[int]) -> None:
        super().__init__("already in the library: episode " + ", ".join(str(n) for n in episodes))
        self.episodes = episodes


async def file_into(ids: list[str], anilist_id: int, episodes: list[int], *, replace: bool = False) -> dict[str, Any]:
    """Make kept files these episodes of an AniList entry, and publish them into the library.

    ``episodes[i]`` is the number of ``ids[i]``. An entry in no collection of
    its own -- an OVA, a film -- joins the show its download was of. Raises
    Taken for episodes the library has a file of already, unless ``replace``.
    """
    import asyncio

    from bankai.processor.anime import _staged_path, publish_staged
    from bankai.web import anime_collections as collections
    from bankai.web import anime_files, anime_layout, review

    if len(ids) != len(episodes) or not ids:
        raise ValueError("one episode number per file")
    if len(set(episodes)) != len(episodes):
        raise ValueError("two files cannot be the same episode")
    rows = load()
    unknown = [key for key in ids if key not in rows]
    if unknown:
        raise LookupError("these files are no longer waiting to be matched")
    picked = [rows[key] for key in ids]
    aid = int(anilist_id)
    await anime_layout.ensure_metadata([aid])

    def home() -> None:
        with collections.editing() as store:
            if store.collection_of(aid) is not None or collections.nearest(aid, store) is not None:
                return
            for row in picked:
                own = store.collection_of(int(row["anilist_id"])) if row.get("anilist_id") else None
                if own is not None:
                    collections.include(store, own.id, aid)
                    return

    await asyncio.to_thread(home)
    held = await asyncio.to_thread(anime_files.on_disk, aid)
    taken = sorted(number for number in episodes if number in held)
    if taken and not replace:
        raise Taken(taken)
    targets = await anime_layout.place_new(aid, list(episodes))
    staged: list[Path] = []
    for row, number in zip(picked, episodes, strict=True):
        key = targets.get(number)
        if not key:
            raise RuntimeError(f"the library layout has no place for AniList {aid} episode {number}")
        source = Path(row["path"])
        destination = _staged_path(key, source.suffix)
        # Over whatever is staged there: the file it replaces may be.
        await asyncio.to_thread(_link, source, destination, replace=True)
        for sidecar in sidecars(source):
            target = destination.parent / (destination.stem + sidecar.name[len(source.stem):])
            await asyncio.to_thread(_link, sidecar, target, replace=True)
        anime_files.record(destination, [(aid, number)])
        review.set_stage(destination, "approved", note="Matched by hand from a download's leftover files.")
        review.set_sources(
            destination,
            torrent_source_url=row.get("detail_url"),
            torrent_source_title=row.get("release_title"),
        )
        staged.append(destination)
    moved = await publish_staged(staged, replace_existing=bool(taken))
    published = set(moved.values())
    # The files they replace, where they were somewhere else.
    for number in taken:
        for old in held.get(number, []):
            if old not in published and old.exists():
                for companion in sidecars(old):
                    companion.unlink(missing_ok=True)
                old.unlink(missing_ok=True)
                anime_files.forget(old)
    cid = anime_layout.collection_of_entry(aid)
    layout: dict[str, Any] = {}
    if cid is not None:
        try:
            layout = await anime_layout.relayout({cid}, label="match")
        except Exception as exc:
            log.warning("[anime] could not lay out the collection: %s", exc)
    forget(ids, delete=True)
    return {"filed": len(staged), "replaced": len(taken), "anilist_id": aid, "moved": layout.get("moved", 0)}


__all__ = ["Taken", "add", "file_into", "forget", "groups", "load", "root", "sidecars"]
