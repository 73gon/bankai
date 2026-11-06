"""German age ratings (FSK) for anime and shows, as ZonTV shows them: Jellyfin's ``OfficialRating``
"FSK-<age>", the format Jellyfin itself gives German films.

Where each title's rating comes from, best first:

- set by hand (``hand`` in ``age_ratings.json``);
- TMDB's German rating (``/tv/{id}/content_ratings``, kept a month in metadata.tmdb);
- 18 for an entry AniList files under Hentai;
- an estimate from another country's rating (``ESTIMATE``: US, then AU, BR, KR), marked so.

Which TMDB show a title is: a show's own TMDB id (Jellyfin's); an anime collection's from Fribb's
anime-lists by its title entry, else by another member, else TMDB's search by name and year
(Animation first) -- kept, as a guess shown for review, until set by hand (``tmdb``).

Nothing is written but what was approved (``written``): an anime's rating goes into its tvshow.nfo
(``<mpaa>``, with ``<uniqueid type="tmdb">``) through the layout -- its fingerprint holds the
rating, so the maintenance pass writes the NFO anew and Jellyfin re-reads it; never locked, as
genres. A show has no NFO (Jellyfin reads TMDB itself): its item is written as genre_sync writes
genres -- the whole DTO, ``OfficialRating`` set and locked, what it had kept for ``undo``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.metadata import anilist, animelists, tmdb, tmdb_titles
from bankai.web import anime_collections, anime_files, genres, jellyfin

log = get_logger(__name__)

AGES = (0, 6, 12, 16, 18)

# Another country's rating as an FSK age, best country first.
ESTIMATE: list[tuple[str, dict[str, int]]] = [
    ("US", {"TV-Y": 0, "TV-G": 0, "TV-Y7": 6, "TV-Y7-FV": 6, "TV-PG": 6, "TV-14": 12, "TV-MA": 16}),
    ("AU", {"G": 0, "PG": 6, "M": 12, "MA15+": 16, "MA 15+": 16, "R18+": 18, "R 18+": 18}),
    ("BR", {"L": 0, "10": 6, "12": 12, "14": 12, "16": 16, "18": 18}),
    ("KR", {"ALL": 0, "All": 0, "12": 12, "15": 16, "19": 18, "18": 18}),
]

_LOCK = threading.RLock()
_LOADED: tuple[float, dict[str, Any]] | None = None


def _path() -> Path:
    return bgjobs.jobs_root().parent / "age_ratings.json"


def load() -> dict[str, Any]:
    """``hand`` {key: age}, ``tmdb`` {key: {id, name, how}}, ``written`` {key: {rating, tmdb_id, at, items}}."""
    global _LOADED
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {"hand": {}, "tmdb": {}, "written": {}}
    if _LOADED is None or _LOADED[0] != mtime:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        _LOADED = (mtime, {part: dict(raw.get(part) or {}) for part in ("hand", "tmdb", "written")})
    return {part: dict(rows) for part, rows in _LOADED[1].items()}


def save(store: dict[str, Any]) -> None:
    global _LOADED
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    _LOADED = None


def fsk(age: int) -> str:
    return f"FSK-{age}"


def german_age(value: object) -> int | None:
    """TMDB's German rating as an age: "16", "FSK 16", "FSK-16"; None for anything else."""
    match = re.fullmatch(r"(?:FSK[\s-]*)?(\d{1,2})", str(value or "").strip(), re.IGNORECASE)
    age = int(match[1]) if match else None
    return age if age in AGES else None


def estimate(ratings: dict[str, str]) -> tuple[int, str] | None:
    """The FSK age another country's rating suggests, and which rating it is ("US TV-MA")."""
    for country, table in ESTIMATE:
        value = str(ratings.get(country) or "").strip()
        if value in table:
            return table[value], f"{country} {value}"
    return None


def decide(ratings: dict[str, str] | None, *, hand: int | None = None, adult: bool = False) -> dict[str, Any]:
    """A title's age and where it comes from: ``hand``, ``de``, ``adult`` or ``estimate`` (``basis``
    says from what); no age when nothing says one."""
    if hand in AGES:
        return {"age": hand, "source": "hand", "basis": None}
    found = german_age((ratings or {}).get("DE"))
    if found is not None:
        return {"age": found, "source": "de", "basis": f"DE {ratings['DE']}"}  # type: ignore[index]
    if adult:
        return {"age": 18, "source": "adult", "basis": "AniList: Hentai"}
    guess = estimate(ratings or {})
    if guess:
        return {"age": guess[0], "source": "estimate", "basis": guess[1]}
    return {"age": None, "source": None, "basis": None}


def nfo_rating(cid: int) -> tuple[str | None, int | None]:
    """What an anime collection's tvshow.nfo carries: its approved rating and TMDB id (None, None: not approved)."""
    row = load()["written"].get(genres.anime_key(cid)) or {}
    return row.get("rating"), row.get("tmdb_id")


# -- the review list ----------------------------------------------------------------


def _anime_titles() -> list[dict[str, Any]]:
    from bankai.web import anime_layout

    store = anime_collections.load()
    on_disk = set(anime_files.by_entry())
    names = anime_layout.folder_names(store)
    root = anime_files.anime_root()
    rows = []
    for cid, row in store.collections.items():
        if not row.members:
            continue
        # A folder with no episode left still shows in Jellyfin as a series: rated too
        # (Iruma-kun, only its artwork and tvshow.nfo since Oct 2026).
        if not on_disk & set(row.members) and not (names.get(cid) and (root / names[cid]).is_dir()):
            continue
        main = row.title if row.title is not None else row.members[0]
        kept = anilist.media(main)
        tmdb_id = animelists.ids(main).get("tmdb_tv")
        how = "anime-lists" if tmdb_id else None
        if not tmdb_id:
            tmdb_id = next((animelists.ids(aid)["tmdb_tv"] for aid in row.members if animelists.ids(aid).get("tmdb_tv")), None)
            how = "anime-lists (another season)" if tmdb_id else None
        rows.append({
            "key": genres.anime_key(cid),
            "cid": cid,
            "kind": "anime",
            "name": names.get(cid) or anilist.name(main) or f"Collection {cid}",
            "search": [kept.get("english"), kept.get("romaji")],
            "year": kept.get("year"),
            "adult": any("Hentai" in (anilist.media(aid).get("genres") or []) for aid in row.members),
            "tmdb_id": tmdb_id,
            "match": how,
            "item_id": None,
            "current": None,
        })
    return rows


async def _show_titles() -> list[dict[str, Any]]:
    rows = []
    for item in await jellyfin.catalog():
        if item["type"] != "Series":
            continue
        tmdb_id = (item.get("provider_ids") or {}).get("Tmdb")
        rows.append({
            "key": f"tmdb:tv:{tmdb_id}" if tmdb_id else f"jellyfin:{item['item_id']}",
            "kind": "show",
            "name": item["name"],
            "search": [item["name"]],
            "year": item.get("year"),
            "adult": False,
            "tmdb_id": int(tmdb_id) if str(tmdb_id or "").isdigit() else None,
            "match": "Jellyfin" if tmdb_id else None,
            "item_id": item["item_id"],
            "current": item.get("rating"),
            "locked": "OfficialRating" in (item.get("locked_fields") or []),
        })
    return rows


async def _search(row: dict[str, Any]) -> dict[str, Any] | None:
    """TMDB's show for a title it has no id of: by each of its names, with its year, Animation first."""
    for name in dict.fromkeys(filter(None, row["search"])):
        found = await tmdb.search_tv(name, row.get("year"))
        if row["kind"] == "anime":
            found = [hit for hit in found if 16 in (hit.get("genre_ids") or [])] or found
        if found:
            return {"id": int(found[0]["id"]), "name": found[0].get("name"), "how": "search"}
    return None


async def _names(ids: set[int], kept: dict[str, dict]) -> dict[int, str]:
    """TMDB's names of shows, for the review: kept by id beside the matches."""
    known = {int(row["id"]): row.get("name") for row in kept.values() if row.get("id") and row.get("name")}
    wanted = [tmdb_id for tmdb_id in ids if tmdb_id not in known]
    if wanted and tmdb.configured():
        gate = asyncio.Semaphore(8)
        async with tmdb_titles.client(timeout=30) as http:
            async def ask(tmdb_id: int) -> None:
                async with gate:
                    data = await tmdb_titles.get(http, f"tv/{tmdb_id}")
                    if data and data.get("name"):
                        known[tmdb_id] = str(data["name"])
            await asyncio.gather(*(ask(tmdb_id) for tmdb_id in wanted))
    return known


async def plan() -> dict[str, Any]:
    """Every anime collection and show with the rating bankai would give it, where from, and what it has."""
    rows = _anime_titles() + await _show_titles()
    with _LOCK:
        store = load()
    changed = False
    for row in rows:
        chosen = store["tmdb"].get(row["key"])
        if chosen and chosen.get("how") == "hand":
            row["tmdb_id"], row["match"] = int(chosen["id"]), "by hand"
        elif not row["tmdb_id"]:
            if not chosen:
                chosen = await _search(row)
                if chosen:
                    store["tmdb"][row["key"]] = chosen
                    changed = True
            if chosen:
                row["tmdb_id"], row["match"] = int(chosen["id"]), "name search"
    names = await _names({row["tmdb_id"] for row in rows if row["tmdb_id"]}, store["tmdb"])
    for row in rows:
        # Kept beside the match, so the next review asks TMDB for no name.
        if row["tmdb_id"] and names.get(row["tmdb_id"]):
            entry = store["tmdb"].setdefault(row["key"], {"id": row["tmdb_id"], "how": "id"})
            if entry.get("id") == row["tmdb_id"] and entry.get("name") != names[row["tmdb_id"]]:
                entry["name"] = names[row["tmdb_id"]]
                changed = True
    if changed:
        with _LOCK:
            fresh = load()
            fresh["tmdb"] = {**fresh["tmdb"], **store["tmdb"]}
            save(fresh)
            store = fresh
    await tmdb.ensure_content_ratings([row["tmdb_id"] for row in rows if row["tmdb_id"]])
    out = []
    for row in rows:
        ratings = tmdb.content_ratings(row["tmdb_id"]) if row["tmdb_id"] else None
        hand = store["hand"].get(row["key"])
        decided = decide(ratings, hand=hand, adult=row["adult"])
        written = store["written"].get(row["key"]) or {}
        rating = fsk(decided["age"]) if decided["age"] is not None else None
        out.append({
            "key": row["key"],
            "kind": row["kind"],
            "name": row["name"],
            "year": row["year"],
            "tmdb_id": row["tmdb_id"],
            "tmdb_name": names.get(row["tmdb_id"]) if row["tmdb_id"] else None,
            "match": row["match"],
            "ratings": ratings or {},
            "rating": rating,
            **decided,
            # Shows: what Jellyfin has now; anime: what bankai wrote into tvshow.nfo.
            "current": row["current"] if row["kind"] == "show" else written.get("rating"),
            "written": written.get("rating"),
            "due": rating is not None and (written.get("rating") != rating or (row["kind"] == "show" and row["current"] != rating)),
        })
    out.sort(key=lambda row: (row["kind"] != "anime", row["name"].casefold()))
    return {"titles": out, "configured": tmdb.configured()}


# -- writing ----------------------------------------------------------------------


async def _write_show(client: httpx.AsyncClient, item_id: str, rating: str, user: str) -> dict[str, Any]:
    """One show's OfficialRating, locked; what it had before, for undo."""
    from bankai.web import genre_sync

    dto = await genre_sync._item(client, item_id, user)
    before = {"rating": dto.get("OfficialRating"), "locked": list(dto["LockedFields"])}
    dto["OfficialRating"] = rating
    if "OfficialRating" not in dto["LockedFields"]:
        dto["LockedFields"] = [*dto["LockedFields"], "OfficialRating"]
    response = await client.post(f"/Items/{item_id}", json=dto)
    response.raise_for_status()
    return before


async def apply(keys: set[str] | None = None) -> dict[str, Any]:
    """Write the ratings of these titles (all that have one when None): anime into tvshow.nfo
    through the layout, shows into their Jellyfin items."""
    from bankai.web import anime_layout, genre_sync

    current = await plan()
    due = [row for row in current["titles"] if row["rating"] and (keys is None or row["key"] in keys)]
    items = {row["key"]: row for row in await _show_titles()}
    written, failed, cids = 0, [], set()
    with _LOCK:
        store = load()
        for row in due:
            if row["kind"] == "anime":
                store["written"][row["key"]] = {"rating": row["rating"], "tmdb_id": row["tmdb_id"], "at": time.time()}
                cids.add(int(row["key"].split(":", 1)[1]))
        save(store)
    shows = [row for row in due if row["kind"] == "show" and items.get(row["key"], {}).get("item_id")]
    if shows:
        async with jellyfin._client() as client:
            user = await genre_sync._admin(client)
            for row in shows:
                item_id = items[row["key"]]["item_id"]
                try:
                    before = await _write_show(client, item_id, row["rating"], user)
                except (httpx.HTTPError, genre_sync.WriteError) as exc:
                    failed.append(f"{row['name']}: {exc}")
                    log.warning("[age ratings] %s could not be written: %s", row["name"], exc)
                    continue
                with _LOCK:
                    store = load()
                    mine = store["written"].setdefault(row["key"], {"items": {}})
                    mine.setdefault("items", {}).setdefault(item_id, before)
                    mine.update(rating=row["rating"], tmdb_id=row["tmdb_id"], at=time.time())
                    save(store)
                written += 1
    if cids:
        # Their fingerprints hold the rating now: laid out (nothing moves that is in place) and
        # their tvshow.nfo written anew at once, rather than on the maintenance pass.
        result = await anime_layout.relayout(cids, pictures=False, label="age ratings")
        written += len(cids)
        log.info("[age ratings] %d anime written into tvshow.nfo (%s NFO files)", len(cids), result.get("nfo_written"))
    genre_sync._CATALOG = None
    return {"written": written, "anime": len(cids), "shows": len(shows) - len(failed), "failed": failed}


def set_hand(key: str, age: int | None) -> None:
    """A title's rating set by hand (None: back to what TMDB says)."""
    with _LOCK:
        store = load()
        if age is None:
            store["hand"].pop(key, None)
        elif age in AGES:
            store["hand"][key] = age
        else:
            raise ValueError(f"not an FSK age: {age}")
        save(store)


def set_tmdb(key: str, tmdb_id: int | None) -> None:
    """The TMDB show a title is, set by hand (None: matched again)."""
    with _LOCK:
        store = load()
        if tmdb_id is None:
            store["tmdb"].pop(key, None)
        else:
            store["tmdb"][key] = {"id": int(tmdb_id), "how": "hand"}
        save(store)


async def undo(key: str) -> int:
    """Take a written rating back: an anime's out of its tvshow.nfo, a show's item as it was."""
    from bankai.web import anime_layout, genre_sync

    with _LOCK:
        store = load()
        mine = store["written"].get(key)
    if not mine:
        return 0
    done = 0
    if key.startswith("anime:"):
        with _LOCK:
            store = load()
            store["written"].pop(key, None)
            save(store)
        await anime_layout.relayout({int(key.split(":", 1)[1])}, pictures=False, label="age ratings undo")
        return 1
    async with jellyfin._client() as client:
        user = await genre_sync._admin(client)
        for item_id, before in (mine.get("items") or {}).items():
            dto = await genre_sync._item(client, item_id, user)
            dto["OfficialRating"] = before.get("rating")
            dto["LockedFields"] = list(before.get("locked") or [])
            response = await client.post(f"/Items/{item_id}", json=dto)
            response.raise_for_status()
            done += 1
    with _LOCK:
        store = load()
        store["written"].pop(key, None)
        save(store)
    return done


__all__ = ["AGES", "ESTIMATE", "apply", "decide", "estimate", "fsk", "german_age", "load", "nfo_rating", "plan", "set_hand", "set_tmdb", "undo"]
