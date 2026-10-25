"""Each anime collection's artwork: what Jellyfin shows of it, chosen automatically or by hand.

AniList keeps a poster and a banner per entry; TMDB keeps backdrops, logos,
landscape thumbs and season posters -- the "Specials" season's too, which no
AniList entry is. The layout (anime_layout.write_nfo) writes each slot as
the local image Jellyfin reads:

    poster     poster.jpg       the show's poster
    fanart     fanart.jpg       backdrop
    landscape  landscape.jpg    thumb: a backdrop with the title on it
    logo       logo.png         clear logo
    banner     banner.jpg       wide banner
    season:N   Season NN/poster.jpg

A slot chosen by hand (any picture's URL, offered from both sources in the
show's Artwork dialog) is kept in ``anime_artwork.json`` by collection id and
wins over the automatic choice; "Automatic" forgets it.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.metadata import anilist, animelists, tmdb
from bankai.web import anime_collections as collections

SHOW_SLOTS: dict[str, tuple[str, str]] = {
    "poster": ("Poster", "poster.jpg"),
    "fanart": ("Backdrop", "fanart.jpg"),
    "landscape": ("Thumb", "landscape.jpg"),
    "logo": ("Logo", "logo.png"),
    "banner": ("Banner", "banner.jpg"),
}
# Every file the layout may write in a show's folder, for its tidying.
FILES = frozenset(name for _label, name in SHOW_SLOTS.values())

_LOCK = threading.Lock()


def season_slot(number: int) -> str:
    return f"season:{number}"


def _path() -> Path:
    return bgjobs.jobs_root().parent / "anime_artwork.json"


def load() -> dict[str, dict[str, str]]:
    """The slots chosen by hand: collection id -> slot -> URL."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def choose(cid: int, slot: str, url: str | None) -> None:
    """Keep a slot's picture chosen by hand; None gives it back to the automatic choice."""
    with _LOCK:
        rows = load()
        mine = dict(rows.get(str(cid)) or {})
        if url:
            mine[slot] = url
        else:
            mine.pop(slot, None)
        if mine:
            rows[str(cid)] = mine
        else:
            rows.pop(str(cid), None)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _tmdb_on() -> bool:
    return get_settings().anime.artwork_tmdb and tmdb.configured()


def _members(row: collections.Collection) -> list[int]:
    """The title entry first, then the rest in order."""
    title = row.title if row.title is not None else (row.members[0] if row.members else None)
    return list(dict.fromkeys([aid for aid in [title, *row.members] if aid is not None]))


def _show_ref(row: collections.Collection) -> tuple[str, int] | None:
    """The TMDB show (else film) the collection is: its title entry's, else its first member's that has one."""
    members = _members(row)
    for kind in ("tv", "movie"):
        for aid in members:
            found = animelists.ids(aid).get(f"tmdb_{kind}")
            if found:
                return kind, int(found)
    return None


def _season_numbers(plan: collections.Plan) -> set[int]:
    numbers = set(plan.seasons.values())
    if plan.specials:
        numbers.add(0)
    return numbers


def wanted(row: collections.Collection, plan: collections.Plan) -> dict[tuple[str, int], set[int]]:
    """The TMDB records a collection's artwork is read from, and the seasons of each."""
    out: dict[tuple[str, int], set[int]] = {}
    ref = _show_ref(row)
    if ref is not None:
        out[ref] = {0} if ref[0] == "tv" and plan.specials else set()
    for aid in plan.seasons:
        ids = animelists.ids(aid)
        if ids.get("tmdb_tv") and isinstance(ids.get("tmdb_season"), int):
            out.setdefault(("tv", int(ids["tmdb_tv"])), set()).add(int(ids["tmdb_season"]))
    return out


def _anilist(aid: int, url: str | None, label: str) -> dict[str, Any] | None:
    if not url:
        return None
    return {"url": url, "preview": url, "source": "anilist", "label": label, "anilist_id": aid, "lang": None}


# How a picture's language ranks, by what the slot wants: text in English
# first wherever there is text; a textless picture where the text would be in
# the way (a backdrop), else straight after English.
_ENGLISH_FIRST = {"en": 0, None: 1}
_TEXTLESS_FIRST = {None: 0, "en": 1}


def _by_language(images: list[dict], rank: dict) -> list[dict]:
    """TMDB's pictures by language as ``rank`` says, the rest after; best voted first within each."""
    return sorted(images or [], key=lambda item: rank.get(item.get("lang"), len(rank)))


def _tmdb(item: dict, label: str, preview: str = "w342") -> dict[str, Any]:
    return {
        "url": tmdb.url(item["path"]),
        "preview": tmdb.url(item["path"], preview),
        "source": "tmdb",
        "label": label,
        "lang": item.get("lang"),
        "width": item.get("w"),
        "height": item.get("h"),
    }


def candidates(row: collections.Collection, plan: collections.Plan) -> dict[str, list[dict[str, Any]]]:
    """What each slot can show, best first: AniList's pictures, then TMDB's (from what is kept of it)."""
    members = _members(row)
    out: dict[str, list[dict[str, Any]]] = {slot: [] for slot in SHOW_SLOTS}
    for number in sorted(_season_numbers(plan)):
        out[season_slot(number)] = []

    def add(slot: str, item: dict[str, Any] | None) -> None:
        if item is not None and slot in out and all(seen["url"] != item["url"] for seen in out[slot]):
            out[slot].append(item)

    for aid in members:
        name = collections.entry_name(aid)
        add("poster", _anilist(aid, anilist.cover(aid), name))
        add("banner", _anilist(aid, anilist.media(aid).get("banner"), name))
    for number in sorted(set(plan.seasons.values())):
        # A season of several entries is pictured after its first, as it is named.
        first = plan.first_of(number)
        for aid in [first, *(aid for aid, at in plan.seasons.items() if at == number and aid != first)]:
            if aid is not None:
                add(season_slot(number), _anilist(aid, anilist.cover(aid), collections.entry_name(aid)))
    for aid in plan.specials:
        add(season_slot(0), _anilist(aid, anilist.cover(aid), collections.entry_name(aid)))

    ref = _show_ref(row)
    kept = tmdb.images(*ref) if ref is not None else {}
    for item in _by_language(kept.get("posters"), _ENGLISH_FIRST):
        add("poster", _tmdb(item, "TMDB"))
    # Textless backdrops first: the title on a backdrop sits under Jellyfin's own.
    for item in _by_language(kept.get("backdrops"), _TEXTLESS_FIRST):
        add("fanart", _tmdb(item, "TMDB", "w780"))
    # A thumb is the opposite: the title on it is the point, in English.
    for item in _by_language(kept.get("backdrops"), _ENGLISH_FIRST):
        add("landscape", _tmdb(item, "TMDB", "w780"))
    # Jellyfin reads logo.png; TMDB has some logos only as SVG. A logo is text: English first.
    for item in _by_language(kept.get("logos"), {"en": 0}):
        if str(item.get("path") or "").casefold().endswith(".png"):
            add("logo", _tmdb(item, "TMDB", "w500"))
    for aid in members:
        add("fanart", _anilist(aid, anilist.media(aid).get("banner"), collections.entry_name(aid)))
    if ref is not None and ref[0] == "tv" and plan.specials:
        for item in _by_language((kept.get("seasons") or {}).get("0"), _ENGLISH_FIRST):
            add(season_slot(0), _tmdb(item, "TMDB Specials"))
    for aid, number in sorted(plan.seasons.items(), key=lambda pair: pair[1]):
        ids = animelists.ids(aid)
        if ids.get("tmdb_tv") and isinstance(ids.get("tmdb_season"), int):
            season = ids["tmdb_season"]
            for item in _by_language((tmdb.images("tv", int(ids["tmdb_tv"])).get("seasons") or {}).get(str(season)), _ENGLISH_FIRST):
                add(season_slot(number), _tmdb(item, f"TMDB season {season}"))
    return out


def _automatic(slot: str, items: list[dict[str, Any]], tmdb_on: bool) -> str | None:
    """The automatic choice of a slot, from its candidates."""
    usable = [item for item in items if tmdb_on or item["source"] != "tmdb"]
    from_tmdb = [item for item in usable if item["source"] == "tmdb"]
    from_anilist = [item for item in usable if item["source"] == "anilist"]
    if slot in ("poster", "banner") or (slot.startswith("season:") and slot != season_slot(0)):
        # AniList's own picture, as it always was: the title entry's, the season's first entry's.
        pick = from_anilist[:1] or from_tmdb[:1]
    elif slot == season_slot(0):
        # Specials are no one entry: TMDB's specials season, else the first side story's cover.
        pick = from_tmdb[:1] or from_anilist[:1]
    elif slot == "landscape":
        # Ordered English, textless, then the rest (see candidates).
        pick = from_tmdb[:1]
    else:  # fanart, logo
        pick = from_tmdb[:1] or from_anilist[:1]
    return pick[0]["url"] if pick else None


def resolved(cid: int, row: collections.Collection, plan: collections.Plan) -> dict[str, str]:
    """Each slot's picture: chosen by hand, else automatic. A slot with neither is left out."""
    chosen = load().get(str(cid)) or {}
    tmdb_on = _tmdb_on()
    out: dict[str, str] = {}
    for slot, items in candidates(row, plan).items():
        url = chosen.get(slot) or _automatic(slot, items, tmdb_on)
        if url:
            out[slot] = url
    return out


def panel(cid: int, row: collections.Collection, plan: collections.Plan) -> list[dict[str, Any]]:
    """The Artwork dialog: each slot's label, picture now, whether chosen by hand, and what it can show."""
    chosen = load().get(str(cid)) or {}
    tmdb_on = _tmdb_on()
    slots = []
    for slot, items in candidates(row, plan).items():
        if slot.startswith("season:"):
            number = int(slot.split(":", 1)[1])
            label = "Specials poster" if number == 0 else f"Season {number} poster"
        else:
            label = SHOW_SLOTS[slot][0]
        url = chosen.get(slot) or _automatic(slot, items, tmdb_on)
        slots.append(
            {
                "slot": slot,
                "label": label,
                "url": url,
                "chosen": bool(chosen.get(slot)),
                "candidates": [item for item in items if tmdb_on or item["source"] != "tmdb"],
            }
        )
    return slots


async def ensure(pairs: Iterable[tuple[collections.Collection, collections.Plan]], *, limit: int = 10) -> int:
    """Fetch the TMDB records these collections need, at most ``limit``. Returns how many were fetched."""
    if not _tmdb_on():
        return 0
    fetched = 0
    for row, plan in pairs:
        for (kind, tmdb_id), seasons in wanted(row, plan).items():
            if fetched >= limit:
                return fetched
            if not tmdb.due(kind, tmdb_id, seasons):
                continue
            if not await tmdb.ensure(kind, tmdb_id, seasons):
                return fetched  # TMDB is refusing or down: the next pass tries again
            fetched += 1
    return fetched


__all__ = ["FILES", "SHOW_SLOTS", "candidates", "choose", "ensure", "load", "panel", "resolved", "season_slot", "wanted"]
