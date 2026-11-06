"""bankai's curated genres (web.genres) against what Jellyfin shows: the dry run, per title.

Movies and series come from Jellyfin itself (jellyfin.catalog). Their raw
genres are TMDB's, by the TMDB id Jellyfin keeps (metadata.tmdb, English,
kept a month): one consistent source, which a wrong TVDB match cannot spoil.
Where TMDB has none, Jellyfin's Genres stand in -- as kept in
genre_sources.json until bankai first writes a title, since a written title's
Genres are locked and bankai's (and so the undo's source). Tags stay live. Anime come from the
collections on disk: their raw genres are AniList's, as tvshow.nfo writes them
now.

Writing (``apply``, the pass ``genre_sync``, ``undo``): a movie's or series'
whole item is fetched (``/Items/{id}?userId=<admin>``: with an API key the user
is required), its Genres set to the curated list and "Genres" added to its
LockedFields, and the whole object posted back -- Jellyfin's UpdateItem
overwrites every field it is sent, so a partial body would wipe Name, Tags,
ProviderIds and more. Tags stay unlocked: TMDB keywords remain a live source.
What was there before is kept in genre_state.json for ``undo``. Anime are
written through tvshow.nfo (anime_layout), never locked: Jellyfin's merge skips
locked fields, so a later NFO change would be ignored. Writing is switched on
per library or title in genre_state.json's "_write".
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import httpx

from bankai.logging import get_logger
from bankai.metadata import anilist, tmdb
from bankai.web import anime_collections, anime_files, genres, jellyfin

log = get_logger(__name__)
# Movies and series written a pass; the rest wait for the next.
WRITES_PER_PASS = 50
# What the writer keeps beside each title's record in genre_state.json.
_WRITE = "_write"

# Jellyfin's catalog is asked again after this long.
_CATALOG_SECONDS = 60.0
_CATALOG: tuple[float, list[dict[str, Any]]] | None = None


async def _catalog(fresh: bool = False) -> list[dict[str, Any]]:
    global _CATALOG
    if fresh or _CATALOG is None or time.monotonic() - _CATALOG[0] > _CATALOG_SECONDS:
        _CATALOG = (time.monotonic(), await jellyfin.catalog())
    return _CATALOG[1]


def _anime_titles() -> list[dict[str, Any]]:
    """Each collection with files on disk, and its title entry's AniList genres."""
    from bankai.web import anime_layout

    store = anime_collections.load()
    on_disk = set(anime_files.by_entry())
    names = anime_layout.folder_names(store)
    rows = []
    for cid, row in store.collections.items():
        if not row.members or not on_disk & set(row.members):
            continue
        main = row.title if row.title is not None else row.members[0]
        rows.append({
            "key": genres.anime_key(cid),
            "item_id": None,
            "type": "Series",
            "name": names.get(cid) or anilist.name(main) or f"Collection {cid}",
            "year": anilist.media(main).get("year"),
            "library": "anime",
            "genres": list(anilist.media(main).get("genres") or []),
            "tags": [],
        })
    return rows


def _reasons(result: genres.Resolution) -> dict[str, list[str]]:
    return {gid: [f"{kind}: {raw}" if raw else kind for kind, raw in why] for gid, why in result.sources.items()}


async def plan(*, fresh: bool = False, language: str | None = None) -> dict[str, Any]:
    """Every title with its genres now and as curated, and what the vocabulary catches."""
    rules = genres.rules()
    language = language or rules["language"]
    picks = genres.overrides()
    written = genres.state()
    items = await _catalog(fresh)
    keyed: dict[str, dict[str, Any]] = {}
    unkeyed = []
    for item in items:
        key = genres.title_key(item["type"], item["provider_ids"])
        if key is None:
            unkeyed.append({"name": item["name"], "library": item["library"], "item_id": item["item_id"]})
            continue
        keyed.setdefault(key, {"genres": item["genres"], "tags": item["tags"], "provider_ids": item["provider_ids"], "name": item["name"]})
        item["key"] = key
    kept = genres.keep_sources(keyed, written=written)
    await tmdb.ensure_genres([(key.split(":")[1], key.split(":")[2]) for key in keyed if key.startswith("tmdb:")])

    titles = []
    unmapped: Counter[str] = Counter()
    unmapped_titles: dict[str, list[str]] = {}
    discarded: dict[str, list[str]] = {}
    catches: dict[tuple[str, str], list[str]] = {}
    for item in [*(row for row in items if row.get("key")), *_anime_titles()]:
        key = item["key"]
        if item["library"] == "anime":
            raw_genres, raw_source = item["genres"], "AniList"
        else:
            from_tmdb = tmdb.genres(key.split(":")[1], key.split(":")[2]) if key.startswith("tmdb:") else None
            raw_genres, raw_source = (from_tmdb, "TMDB") if from_tmdb else ((kept.get(key) or {}).get("genres", item["genres"]), "Jellyfin")
        library = "anime" if item["library"] == "anime" else None
        result = genres.resolve(rules, genres=raw_genres, tags=item["tags"], library=library, override=picks.get(key))
        after = result.names(rules, language)
        # What Jellyfin's own genres give and the curated list does not: about to go, shown
        # so a click keeps it (Avatar's Fantasy, once its TVDB match was put right).
        jellyfin_raw = (kept.get(key) or {}).get("genres", item["genres"]) if library is None else []
        removed = set((picks.get(key) or {}).get("remove") or [])
        from_jellyfin = genres.resolve(rules, genres=jellyfin_raw, tags=item["tags"]).ids
        jellyfin_only = [gid for gid in from_jellyfin if gid not in result.ids and gid not in removed]
        # Kept by a click on one of those: Jellyfin's own genre, not one added by hand -- shown as the
        # rules' (it was shown blue, "added by hand", for a genre the title always had).
        kept_from_jellyfin = [gid for gid in (picks.get(key) or {}).get("add") or [] if gid in from_jellyfin]
        for raw in result.discarded:
            discarded.setdefault(raw.casefold(), []).append(item["name"])
        for raw in result.unmapped:
            unmapped[raw] += 1
            unmapped_titles.setdefault(raw, []).append(item["name"])
        for why in result.sources.values():
            for kind, raw in why:
                if kind in ("tag", "genre"):
                    catches.setdefault((kind, raw.casefold()), []).append(item["name"])
        titles.append({
            "key": key,
            "item_id": item["item_id"],
            "name": item["name"],
            "year": item["year"],
            "library": item["library"],
            "type": item["type"],
            "before": item["genres"],
            "raw": raw_genres,
            "raw_source": raw_source,
            "after": after,
            "ids": result.ids,
            "reasons": _reasons(result),
            "dropped": {gid: [f"{kind}: {raw}" if raw else kind for kind, raw in why] for gid, why in result.dropped.items()},
            "unmapped": result.unmapped,
            "jellyfin_only": jellyfin_only,
            "kept_from_jellyfin": kept_from_jellyfin,
            "override": picks.get(key),
            "written": key in written,
            "locked": "Genres" in item.get("locked_fields", []),
            "changes": item["genres"] != after,
        })
    titles.sort(key=lambda row: (row["library"], row["name"].casefold()))
    counts = Counter(gid for row in titles for gid in row["ids"])
    keys = Counter(row["key"] for row in titles)
    return {
        "language": language,
        "rules": rules,
        "vocabulary": [{**row, "titles": counts.get(row["id"], 0)} for row in rules["vocabulary"]],
        "titles": titles,
        "unmapped": [{"raw": raw, "titles": count, "examples": unmapped_titles[raw][:5]} for raw, count in unmapped.most_common()],
        "catches": [
            {"kind": kind, "raw": raw, "ids": rules["tags" if kind == "tag" else "genres"].get(raw, []), "titles": sorted(set(names))}
            for (kind, raw), names in sorted(catches.items())
        ],
        # Raw genres dropped knowingly, and the titles that carry them.
        "discarded": [{"raw": raw, "titles": sorted(set(names))} for raw, names in sorted(discarded.items())],
        "unkeyed": unkeyed,
        "write": switches(),
        # One title in two libraries (Filme and Filme English): one key, written alike.
        "shared": sorted(key for key, count in keys.items() if count > 1),
        "summary": {
            "titles": len(titles),
            "changes": sum(1 for row in titles if row["changes"]),
            "jellyfin": sum(1 for row in titles if row["item_id"]),
            "written": sum(1 for row in titles if row["item_id"] and row["written"] and row["locked"] and row["before"] == row["after"]),
            "anime": sum(1 for row in titles if row["library"] == "anime"),
        },
    }


# -- Writing ----------------------------------------------------------------------------------


class WriteError(RuntimeError):
    """A title that could not be written, saying why."""


def switches() -> dict[str, Any]:
    """What may be written: {"movies": bool, "anime": bool, "anime_cids": [...]}."""
    raw = genres.state().get(_WRITE) or {}
    return {"movies": bool(raw.get("movies")), "anime": bool(raw.get("anime")), "anime_cids": [int(c) for c in raw.get("anime_cids") or []]}


def switch(**changes: Any) -> dict[str, Any]:
    rows = genres.state()
    current = {**switches(), **changes}
    rows[_WRITE] = current
    genres.save_state(rows)
    return current


def anime_genres(cid: int, raw: list[str]) -> list[str]:
    """What tvshow.nfo says of a collection: the curated genres where writing them is on, else AniList's."""
    on = switches()
    if not (on["anime"] or cid in on["anime_cids"]):
        return list(raw)
    rules = genres.rules()
    key = genres.anime_key(cid)
    return genres.resolve(rules, genres=raw, library="anime", override=genres.overrides().get(key)).names(rules)


async def _admin(client: httpx.AsyncClient) -> str:
    response = await client.get("/Users")
    response.raise_for_status()
    for user in response.json() or []:
        if (user.get("Policy") or {}).get("IsAdministrator"):
            return str(user["Id"])
    raise WriteError("Jellyfin has no administrator to write as")


async def _item(client: httpx.AsyncClient, item_id: str, user: str) -> dict[str, Any]:
    response = await client.get(f"/Items/{item_id}", params={"userId": user})
    response.raise_for_status()
    dto = response.json()
    # Jellyfin's UpdateItem takes every field as sent and throws on a null Tags or ProviderIds.
    missing = [field for field in ("Name", "Tags", "ProviderIds", "LockedFields") if dto.get(field) is None]
    if missing:
        raise WriteError(f"Jellyfin's item {item_id} came without {', '.join(missing)}")
    # Read only, and Jellyfin 10.11 cannot read its own TrickplayInfoDto back: posted, the
    # whole update fails with a 500 (Iron Man's trial, Oct 2026). UpdateItem never uses it.
    dto.pop("Trickplay", None)
    return dto


async def write_item(client: httpx.AsyncClient, item_id: str, names: list[str], user: str) -> dict[str, Any]:
    """Write one item's genres, locked; what it had before, for undo."""
    dto = await _item(client, item_id, user)
    before = {"genres": list(dto.get("Genres") or []), "locked": list(dto["LockedFields"])}
    dto["Genres"] = list(names)
    if "Genres" not in dto["LockedFields"]:
        dto["LockedFields"] = [*dto["LockedFields"], "Genres"]
    response = await client.post(f"/Items/{item_id}", json=dto)
    response.raise_for_status()
    return before


async def apply(*, keys: set[str] | None = None, limit: int | None = WRITES_PER_PASS) -> dict[str, Any]:
    """Write the curated genres of movies and series whose Jellyfin genres differ or are not locked:
    ``keys`` (a trial), or all where writing movies is on, ``limit`` a run."""
    if keys is None and not switches()["movies"]:
        return {"idle": "Writing movies and series is off"}
    current = await plan(fresh=True)
    rows = genres.state()
    due = [
        row for row in current["titles"]
        if row["item_id"] and (keys is None or row["key"] in keys)
        and row["after"] and (row["after"] != row["before"] or not row["locked"])
    ]
    empty = [row["name"] for row in current["titles"] if row["item_id"] and (keys is None or row["key"] in keys) and not row["after"]]
    written, failed = 0, []
    async with jellyfin._client() as client:
        user = await _admin(client)
        for row in due[:limit] if limit else due:
            try:
                before = await write_item(client, row["item_id"], row["after"], user)
            except (httpx.HTTPError, WriteError) as exc:
                failed.append(f"{row['name']}: {exc}")
                log.warning("[genres] %s could not be written: %s", row["name"], exc)
                continue
            mine = rows.setdefault(row["key"], {"items": {}})
            # The first state seen is what undo goes back to, not one bankai wrote itself.
            mine["items"].setdefault(row["item_id"], before)
            mine.update(written=row["after"], at=time.time(), name=row["name"])
            genres.save_state(rows)
            written += 1
    global _CATALOG
    _CATALOG = None
    return {"written": written, "remaining": max(0, len(due) - written - len(failed)), "failed": failed, "empty": empty}


async def undo(key: str) -> int:
    """Put a title's genres and locks back as they were before bankai wrote them."""
    rows = genres.state()
    mine = rows.get(key)
    if not mine:
        return 0
    done = 0
    async with jellyfin._client() as client:
        user = await _admin(client)
        for item_id, before in mine["items"].items():
            dto = await _item(client, item_id, user)
            dto["Genres"] = list(before["genres"])
            dto["LockedFields"] = list(before["locked"])
            response = await client.post(f"/Items/{item_id}", json=dto)
            response.raise_for_status()
            done += 1
    rows.pop(key, None)
    genres.save_state(rows)
    global _CATALOG
    _CATALOG = None
    return done
