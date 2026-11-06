"""Artwork of the Movies & Shows library, chosen in bankai rather than in Jellyfin.

Jellyfin fetches these libraries' pictures itself; nothing changes that until a
picture is chosen here (a TMDB picture from the title's Artwork dialog, or any
image's address). A chosen picture is written as the local image Jellyfin
reads, which a normal refresh takes over the one it fetched:

    poster     poster.jpg       (a film beside others in one folder: <film>-poster.jpg)
    fanart     fanart.jpg       backdrop
    landscape  landscape.jpg    thumb: a backdrop with the title on it
    logo       logo.png         clear logo
    season:N   Season NN/poster.jpg

Kept in ``mas_artwork.json`` by TMDB id (``tv:1399``, ``movie:389``); a title must
be matched to TMDB first. "Automatic" removes the picture bankai wrote, so
Jellyfin goes back to its own.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import tmdb
from bankai.web.anime_artwork import _ENGLISH_FIRST, _TEXTLESS_FIRST, _by_language, _tmdb

log = get_logger(__name__)

SLOTS: dict[str, tuple[str, str]] = {
    "poster": ("Poster", "poster.jpg"),
    "fanart": ("Backdrop", "fanart.jpg"),
    "landscape": ("Thumb", "landscape.jpg"),
    "logo": ("Logo", "logo.png"),
}

_LOCK = threading.Lock()


def _path() -> Path:
    return bgjobs.jobs_root().parent / "mas_artwork.json"


def ref(kind: str, tmdb_id: int) -> tuple[str, int]:
    """TMDB's name for the kind ("tv" / "movie") and the id."""
    if kind not in {"show", "movie"}:
        raise ValueError("kind must be show or movie")
    return ("tv" if kind == "show" else "movie"), int(tmdb_id)


def load() -> dict[str, dict[str, str]]:
    """The slots chosen by hand: ``tv:<id>`` / ``movie:<id>`` -> slot -> URL."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _key(kind: str, tmdb_id: int) -> str:
    name, number = ref(kind, tmdb_id)
    return f"{name}:{number}"


def chosen(kind: str, tmdb_id: int) -> dict[str, str]:
    return dict(load().get(_key(kind, tmdb_id)) or {})


def choose(kind: str, tmdb_id: int, slot: str, url: str | None) -> None:
    """Keep a slot's picture; None gives the slot back to Jellyfin."""
    key = _key(kind, tmdb_id)
    with _LOCK:
        rows = load()
        mine = dict(rows.get(key) or {})
        if url:
            mine[slot] = url
        else:
            mine.pop(slot, None)
        if mine:
            rows[key] = mine
        else:
            rows.pop(key, None)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def slot_names(kind: str, seasons: set[int]) -> list[str]:
    names = list(SLOTS)
    if kind == "show":
        names += [f"season:{number}" for number in sorted(seasons)]
    return names


def candidates(kind: str, tmdb_id: int, seasons: set[int]) -> dict[str, list[dict[str, Any]]]:
    """What each slot can show, from what is kept of TMDB's pictures, best first."""
    kept = tmdb.images(*ref(kind, tmdb_id))
    out: dict[str, list[dict[str, Any]]] = {slot: [] for slot in slot_names(kind, seasons)}
    out["poster"] = [_tmdb(item, "TMDB") for item in _by_language(kept.get("posters"), _ENGLISH_FIRST)]
    # Textless backdrops first: the title on a backdrop sits under Jellyfin's own.
    out["fanart"] = [_tmdb(item, "TMDB", "w780") for item in _by_language(kept.get("backdrops"), _TEXTLESS_FIRST)]
    # A thumb is the opposite: the title on it is the point.
    out["landscape"] = [_tmdb(item, "TMDB", "w780") for item in _by_language(kept.get("backdrops"), _ENGLISH_FIRST)]
    # Jellyfin reads logo.png; TMDB has some logos only as SVG.
    out["logo"] = [
        _tmdb(item, "TMDB", "w500") for item in _by_language(kept.get("logos"), {"en": 0})
        if str(item.get("path") or "").casefold().endswith(".png")
    ]
    for number in seasons if kind == "show" else ():
        posters = (kept.get("seasons") or {}).get(str(number))
        out[f"season:{number}"] = [_tmdb(item, f"TMDB season {number}") for item in _by_language(posters, _ENGLISH_FIRST)]
    return out


def _label(slot: str) -> str:
    if slot.startswith("season:"):
        number = int(slot.split(":", 1)[1])
        return "Specials poster" if number == 0 else f"Season {number} poster"
    return SLOTS[slot][0]


def panel(kind: str, tmdb_id: int, seasons: set[int]) -> list[dict[str, Any]]:
    """The Artwork dialog: each slot's label, the picture chosen (None: Jellyfin's own) and what it can show."""
    mine = chosen(kind, tmdb_id)
    return [
        {"slot": slot, "label": _label(slot), "url": mine.get(slot), "chosen": bool(mine.get(slot)), "candidates": items}
        for slot, items in candidates(kind, tmdb_id, seasons).items()
    ]


# -- where the pictures go --------------------------------------------------------

_SEASON_DIR = re.compile(r"(?:Season|Staffel)\s*0*(\d+)", re.IGNORECASE)


def files(kind: str, card: dict[str, Any]) -> dict[str, list[Path]]:
    """Each slot's picture files for a library card: one per folder the title sits in.

    A show sits in one folder per root (German and English apart); a film in its
    own folder, or as a bare file beside others, which takes ``<film>-poster.jpg``.
    """
    rows = card.get("episodes") if kind == "show" else card.get("files")
    out: dict[str, list[Path]] = {}

    def add(slot: str, path: Path) -> None:
        if path not in out.setdefault(slot, []):
            out[slot].append(path)

    for row in rows or []:
        if not row.get("path") or not row.get("root") or row.get("missing"):
            continue
        video = Path(row["path"])
        parts = Path(row.get("rel_path") or video.name).parts
        if kind == "movie":
            if len(parts) > 1:
                folder = Path(row["root"]) / parts[0]
                for slot, (_label, name) in SLOTS.items():
                    add(slot, folder / name)
            else:
                for slot, (_label, name) in SLOTS.items():
                    stem, dot, suffix = name.partition(".")
                    add(slot, video.with_name(f"{video.stem}-{stem}{dot}{suffix}"))
            continue
        if len(parts) < 2:
            continue  # an episode loose in the root has no show folder to picture
        folder = Path(row["root"]) / parts[0]
        for slot, (_label, name) in SLOTS.items():
            add(slot, folder / name)
        if len(parts) > 2:
            match = _SEASON_DIR.fullmatch(parts[1])
            if match:
                add(f"season:{int(match[1])}", folder / parts[1] / "poster.jpg")
    return out


def seasons(card: dict[str, Any]) -> set[int]:
    """The seasons a show has folders for: those can carry a poster."""
    return {int(slot.split(":", 1)[1]) for slot in files("show", card) if slot.startswith("season:")}


async def apply(kind: str, tmdb_id: int, card: dict[str, Any]) -> int:
    """Write the chosen pictures beside the title, and take away the ones bankai wrote for slots now Jellyfin's.

    Returns how many pictures were downloaded. Only files bankai wrote (their source is
    recorded) are ever removed: a poster someone put there by hand stays.
    """
    from bankai.web import anime_layout, jellyfin

    mine = chosen(kind, tmdb_id)
    targets = files(kind, card)
    pictures = [anime_layout.Picture(path, mine[slot]) for slot, paths in targets.items() if mine.get(slot) for path in paths]
    try:
        sources = json.loads(anime_layout._pictures_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sources = {}
    gone: list[Path] = []
    for slot, paths in targets.items():
        if mine.get(slot):
            continue
        for path in paths:
            if str(path) in sources and path.is_file():
                path.unlink(missing_ok=True)
                sources.pop(str(path), None)
                gone.append(path.parent)
    if gone:
        with _LOCK:
            target = anime_layout._pictures_path()
            tmp = target.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
        jellyfin.queue(gone)
    return await anime_layout.fetch_pictures(pictures)


# -- what Jellyfin shows now ------------------------------------------------------
#
# A slot not chosen here shows the picture Jellyfin fetched itself, which bankai
# never wrote and cannot name by its TMDB address: Jellyfin keeps it as a file of
# its own. So the dialog asks Jellyfin which picture each slot shows now and shows
# that one (through the poster proxy: Jellyfin's pictures need no key).

# Jellyfin's image types by slot; a season's poster is the season's Primary.
_JELLYFIN_TYPES = {"Primary": "poster", "Backdrop": "fanart", "Thumb": "landscape", "Logo": "logo"}


def _jellyfin_items(kind: str, card: dict[str, Any]) -> list[tuple[str, Path]]:
    """Where Jellyfin has the title: (slot, path) pairs, the title's own first ("title"), then
    each season's folder ("season:N"). A show's folder per root; a film's files, the largest first."""
    out: list[tuple[str, Path]] = []
    if kind == "movie":
        for row in sorted(card.get("files") or [], key=lambda row: -int(row.get("size") or 0)):
            if row.get("path"):
                out.append(("title", Path(row["path"])))
        return out
    seasons: list[tuple[str, Path]] = []
    for row in card.get("episodes") or []:
        if not row.get("path") or not row.get("root") or row.get("missing"):
            continue
        parts = Path(row.get("rel_path") or Path(row["path"]).name).parts
        if len(parts) < 2:
            continue
        show = ("title", Path(row["root"]) / parts[0])
        if show not in out:
            out.append(show)
        match = _SEASON_DIR.fullmatch(parts[1]) if len(parts) > 2 else None
        if match:
            season = (f"season:{int(match[1])}", Path(row["root"]) / parts[0] / parts[1])
            if season not in seasons:
                seasons.append(season)
    return out + seasons


async def _jellyfin_now(kind: str, card: dict[str, Any]) -> dict[str, tuple[str, Path | None]]:
    """Each slot's picture in Jellyfin now: (its address, the file it is, as bankai sees it)."""
    from bankai.web import jellyfin

    base = get_settings().jellyfin.url.rstrip("/")
    found: dict[str, tuple[str, Path | None]] = {}
    async with jellyfin._client() as client:
        index = await jellyfin._index(client)
        for slot, path in _jellyfin_items(kind, card):
            item = index.get(str(path))
            if not item or (slot != "title" and slot in found):
                continue
            response = await client.get(f"/Items/{item}/Images")
            response.raise_for_status()
            # Jellyfin shows the lowest index of a type first: a backdrop it fetched before
            # bankai's fanart.jpg stays the one shown.
            for image in sorted(response.json() or [], key=lambda image: image.get("ImageIndex") or 0):
                kind_of = str(image.get("ImageType") or "")
                own = _JELLYFIN_TYPES.get(kind_of)
                target = own if slot == "title" else slot if kind_of == "Primary" else None
                if target is None or target in found:
                    continue
                number = image.get("ImageIndex")
                address = f"{base}/Items/{item}/Images/{kind_of}" + (f"/{number}" if number is not None else "")
                found[target] = (
                    f"{address}?tag={image.get('ImageTag') or ''}&maxWidth=800&quality=80",
                    jellyfin._local(str(image.get("Path") or "")),
                )
    return found


async def with_jellyfin(slots: list[dict[str, Any]], kind: str, card: dict[str, Any]) -> list[dict[str, Any]]:
    """Each slot with ``jellyfin_url``, the picture Jellyfin shows in it now, and ``jellyfin_own``:
    that picture is one Jellyfin fetched, not the one bankai wrote. As they are when Jellyfin is
    not set up or cannot be asked: the dialog works without it."""
    from bankai.web import jellyfin

    if not jellyfin.configured():
        return slots
    try:
        now = await asyncio.wait_for(_jellyfin_now(kind, card), timeout=10)
    except Exception as error:
        log.info("Jellyfin's pictures of %s could not be read: %s", card.get("key"), error)
        return slots
    written = files(kind, card)
    out: list[dict[str, Any]] = []
    for slot in slots:
        picture = now.get(slot["slot"])
        if picture is None:
            out.append(slot)
            continue
        url, path = picture
        mine = {str(item) for item in written.get(slot["slot"], [])}
        out.append({**slot, "jellyfin_url": url, "jellyfin_own": path is None or str(path) not in mine})
    return out


__all__ = [
    "SLOTS", "apply", "candidates", "choose", "chosen", "files", "load", "panel", "ref", "seasons", "with_jellyfin",
]
