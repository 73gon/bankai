"""Which manga and light novels bankai looks for, and which it never offers again.

Followed: every manga and light novel AniList links to an anime on disk as its
source, its adaptation or an alternative version (spin-offs and side stories
show in the relation graph, but nothing looks for them); every series in the
libraries; every series followed by hand. A blacklisted series is none of
these: never searched for, its releases never offered. Both kinds of choice
are kept in ``manga_policies.json`` as ``anilist:<id>`` rows, like the anime
blacklist.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.metadata import anilist
from bankai.metadata import anilist_manga as manga_meta
from bankai.web import anime_files, manga_library

# The relations an anime's manga is followed by: what it was adapted from, or into.
LINKS = frozenset({"ADAPTATION", "SOURCE", "ALTERNATIVE"})


def _path() -> Path:
    return bgjobs.jobs_root().parent / "manga_policies.json"


def _read() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(rows: dict[str, dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def policies() -> dict[int, dict[str, Any]]:
    return {
        int(key.split(":", 1)[1]): row
        for key, row in _read().items()
        if key.startswith("anilist:") and key.split(":", 1)[1].isdigit()
    }


def set_policy(anilist_id: int, mode: str, *, origin: str) -> None:
    """``mode`` "blacklisted" or "followed", with what the series looked like then."""
    rows = _read()
    rows[f"anilist:{int(anilist_id)}"] = {
        "mode": mode,
        "anilist_id": int(anilist_id),
        "title": manga_meta.name(anilist_id),
        "format": manga_meta.media(anilist_id).get("format"),
        "cover": manga_meta.media(anilist_id).get("cover"),
        "origin": origin,
        "updated_at": time.time(),
    }
    _write(rows)


def clear_policy(anilist_id: int) -> None:
    rows = _read()
    if rows.pop(f"anilist:{int(anilist_id)}", None) is not None:
        _write(rows)


def blacklisted() -> set[int]:
    return {aid for aid, row in policies().items() if row.get("mode") == "blacklisted"}


def anime_links() -> dict[int, list[tuple[int, str, str | None]]]:
    """Each manga or light novel an anime on disk links to: {manga id: [(anime id, relation, format)]}."""
    links: dict[int, list[tuple[int, str, str | None]]] = {}
    for anime_id in anime_files.by_entry():
        for manga_id, relation, format_ in anilist.media(anime_id).get("manga") or []:
            links.setdefault(int(manga_id), []).append((int(anime_id), str(relation), format_))
    return links


def followed() -> dict[int, list[str]]:
    """Every series looked for, with why: "anime", "library", "hand"."""
    novels = get_settings().manga.novels
    reasons: dict[int, list[str]] = {}
    for manga_id, rows in anime_links().items():
        linked = [row for row in rows if row[1] in LINKS]
        if linked and (novels or not any(row[2] == "NOVEL" for row in linked)):
            reasons.setdefault(manga_id, []).append("anime")
    for aid, row in manga_library.series().items():
        if novels or row.get("kind") != "novel":
            reasons.setdefault(aid, []).append("library")
    for aid, row in policies().items():
        if row.get("mode") == "followed":
            reasons.setdefault(aid, []).append("hand")
    for aid in blacklisted():
        reasons.pop(aid, None)
    return reasons
