"""What Shoko knows about the anime library, in one read.

Two listings: every series (one per AniDB entry) with its name and poster,
and every episode -- missing ones included -- with the files linked to it.
Together they say, for each file on disk, which AniDB entry and episode it
is, and for each entry, its whole AniDB episode list. The episode listing is
~15 MB and takes Shoko ~10 s, so it is read in the background and held.

Beside them, Shoko's groups -- the collections a show's seasons are kept
together in, which Jellyfin shows through Shokofin -- and the AniDB relations
between entries, sequels and prequels among them.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bankai.web import shoko


@dataclass(frozen=True)
class EntryEpisode:
    number: int
    title: str | None
    aired: str | None


@dataclass(frozen=True)
class Entry:
    """One AniDB entry in the Shoko collection."""

    aid: int
    title: str
    poster_url: str | None
    episodes: tuple[EntryEpisode, ...]
    tvdb_ids: tuple[int, ...] = ()
    # Shoko's own id for the series, and the top-level group it is in.
    series_id: int | None = None
    group_id: int | None = None


@dataclass(frozen=True)
class Group:
    """One Shoko group: the seasons of a show kept together as one collection."""

    id: int
    name: str
    # The entry the group is named and pictured after.
    main_aid: int | None
    has_custom_name: bool = False


@dataclass
class Catalog:
    entries: dict[int, Entry] = field(default_factory=dict)
    # Absolute file path -> the (AniDB id, episode number) it is linked to.
    # Regular episodes only; a double episode's file is linked to both.
    files: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    groups: dict[int, Group] = field(default_factory=dict)
    # (AniDB id, related AniDB id, AniDB relation type: "Sequel", "SideStory", ...).
    # Known for the entries Shoko holds; their relatives need not be held.
    relations: list[tuple[int, int, str]] = field(default_factory=list)

    def entry_of(self, path: str) -> int | None:
        links = self.files.get(path)
        return links[0][0] if links else None


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get("List") or []
    return [row for row in payload or [] if isinstance(row, dict)]


def _title(episode: dict) -> str | None:
    anidb = episode.get("AniDB") or {}
    return anidb.get("Title") or episode.get("Name") or None


def build(series: Any, episodes: Any, groups: Any = None, relations: Any = None) -> Catalog:
    """The catalogue, from the listings as Shoko returns them; pure."""
    catalog = Catalog()
    names: dict[int, tuple[str, str | None, tuple[int, ...]]] = {}
    placed: dict[int, tuple[int | None, int | None]] = {}
    for row in _rows(series):
        ids = row.get("IDs") or {}
        aid = ids.get("AniDB")
        if not isinstance(aid, int):
            continue
        posters = (row.get("Images") or {}).get("Posters") or []
        poster = next(
            (shoko.poster_path(image) for image in posters if image.get("Preferred")), None
        ) or next((shoko.poster_path(image) for image in posters if shoko.poster_path(image)), None)
        tvdb = tuple(int(value) for value in ids.get("TvDB") or [] if str(value).isdigit())
        names[aid] = (str(row.get("Name") or aid), poster, tvdb)
        group = ids.get("TopLevelGroup") or ids.get("ParentGroup")
        placed[aid] = (
            ids.get("ID") if isinstance(ids.get("ID"), int) else None,
            group if isinstance(group, int) else None,
        )

    lists: dict[int, dict[int, EntryEpisode]] = {aid: {} for aid in names}
    for row in _rows(episodes):
        anidb = row.get("AniDB") or {}
        aid, number = anidb.get("AnimeID"), anidb.get("EpisodeNumber")
        if anidb.get("Type") != "Episode" or not isinstance(aid, int) or not isinstance(number, int):
            continue
        lists.setdefault(aid, {})[number] = EntryEpisode(
            number=number, title=_title(row), aired=anidb.get("AirDate") or None
        )
        for file in row.get("Files") or []:
            for location in file.get("Locations") or []:
                path = location.get("AbsolutePath")
                if path:
                    catalog.files.setdefault(str(path), []).append((aid, number))
    for aid, episodes_of in lists.items():
        title, poster, tvdb = names.get(aid, (str(aid), None, ()))
        series_id, group_id = placed.get(aid, (None, None))
        catalog.entries[aid] = Entry(
            aid=aid,
            title=title,
            poster_url=poster,
            episodes=tuple(episodes_of[n] for n in sorted(episodes_of)),
            tvdb_ids=tvdb,
            series_id=series_id,
            group_id=group_id,
        )
    for links in catalog.files.values():
        links.sort(key=lambda link: link[1])
    for row in _rows(groups):
        ids = row.get("IDs") or {}
        if not isinstance(ids.get("ID"), int) or ids.get("ParentGroup"):
            continue  # sub-groups are part of their top-level group
        main = ids.get("MainAnime")
        catalog.groups[ids["ID"]] = Group(
            id=ids["ID"],
            name=str(row.get("Name") or ids["ID"]),
            main_aid=main if isinstance(main, int) else None,
            has_custom_name=bool(row.get("HasCustomName")),
        )
    for row in _rows(relations):
        left = (row.get("IDs") or {}).get("AniDB")
        right = (row.get("RelatedIDs") or {}).get("AniDB")
        if isinstance(left, int) and isinstance(right, int) and row.get("Type"):
            catalog.relations.append((left, right, str(row["Type"])))
    return catalog


def _links_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "shoko_file_links.json"


def save_links(catalog: Catalog) -> None:
    """Keep which AniDB episode each file is, for the automation worker.

    The worker is its own process and does not read Shoko's catalogue; with
    these it places every file on disk by AniDB entry, whatever its folder is
    called. A catalogue with no files -- Shoko down or empty -- keeps the last.
    """
    if not catalog.files:
        return
    path = _links_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(catalog.files), encoding="utf-8")
    tmp.replace(path)


def saved_links() -> Catalog | None:
    """The file links last saved, as a catalogue with no entries; None if none were."""
    try:
        raw = json.loads(_links_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = {
        str(path): [(int(aid), int(number)) for aid, number in links]
        for path, links in raw.items()
        if isinstance(links, list)
    }
    return Catalog(files=files)


# -- Relations of entries Shoko holds no series for ------------------------------
#
# Shoko lists the AniDB relations of the anime it has a record of: the ones on
# disk, and one step out from them. A season further out -- the sequel of a
# sequel not downloaded yet -- has none, so it showed with only the one link
# pointing at it. Those are asked for here, one anime at a time: what Shoko
# already has is read from it, and a record it lacks is fetched by Shoko from
# AniDB (no series is created), a few per refresh and a hundred a day at most,
# so AniDB is never pressed. Answers are kept a week.

_RELATIONS_KEEP = 7 * 86400
_ASK_PER_RUN = 3
_ASK_PER_DAY = 100


def _relations_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "anidb_relations.json"


def _load_relations() -> dict:
    try:
        raw = json.loads(_relations_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _save_relations(data: dict) -> None:
    path = _relations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def add_known_relations(catalog: Catalog) -> None:
    """Add the relations kept for entries Shoko holds no series for; no requests."""
    have = set(catalog.relations)
    for row in (_load_relations().get("aids") or {}).values():
        for left, right, kind in row.get("relations") or []:
            if (left, right, kind) not in have:
                have.add((left, right, kind))
                catalog.relations.append((int(left), int(right), str(kind)))


async def fill_relations(catalog: Catalog, wanted: set[int]) -> int:
    """Learn the relations of the wanted entries Shoko lists none for; returns how many."""
    import time

    data = _load_relations()
    kept: dict = data.setdefault("aids", {})
    today = time.strftime("%Y-%m-%d")
    if data.get("day") != today:
        data.update(day=today, asked=0)
    listed = {left for left, _right, _kind in catalog.relations}
    learned = asked = 0
    for aid in sorted(wanted):
        if aid in catalog.entries or aid in listed:
            continue
        hit = kept.get(str(aid))
        if hit and time.time() - float(hit.get("fetched_at", 0)) < _RELATIONS_KEEP:
            continue
        rows = await shoko._send("GET", f"/api/v3/Series/AniDB/{aid}/Relations")
        if rows is None:
            # No record of it at all: Shoko fetches one from AniDB, within its own limits.
            if asked >= _ASK_PER_RUN or int(data.get("asked", 0)) >= _ASK_PER_DAY:
                continue
            asked += 1
            data["asked"] = int(data.get("asked", 0)) + 1
            await shoko._send(
                "POST",
                f"/api/v3/Series/AniDB/{aid}/Refresh",
                createSeriesEntry="false",
                downloadRelations="false",
                skipTmdbUpdate="true",
                immediate="true",
            )
            rows = await shoko._send("GET", f"/api/v3/Series/AniDB/{aid}/Relations")
            if rows is None:
                continue
        relations = []
        for row in rows if isinstance(rows, list) else []:
            left = (row.get("IDs") or {}).get("AniDB")
            right = (row.get("RelatedIDs") or {}).get("AniDB")
            if isinstance(left, int) and isinstance(right, int) and row.get("Type"):
                relations.append([left, right, str(row["Type"])])
        kept[str(aid)] = {"fetched_at": time.time(), "relations": relations}
        learned += 1
    _save_relations(data)
    add_known_relations(catalog)
    return learned


async def fetch() -> Catalog | None:
    """Read both listings from Shoko, or None when it is not connected."""
    if not shoko.configured():
        return None
    series = await shoko._get("/api/v3/Series", pageSize=0)
    groups = await shoko._get("/api/v3/Group", pageSize=0, includeEmpty="false")
    relations = await shoko._get("/api/v3/Series/AniDB/Relations", pageSize=0)
    async with shoko._client(timeout=120.0) as client:
        response = await client.get(
            "/api/v3/Episode",
            params={
                "pageSize": 0,
                "includeMissing": "true",
                "includeUnaired": "true",
                "includeFiles": "true",
                "includeAbsolutePaths": "true",
                "includeDataFrom": "AniDB",
            },
        )
    response.raise_for_status()
    # Parsing 15 MB of JSON is best kept off the event loop.
    episodes = await asyncio.to_thread(response.json)
    return await asyncio.to_thread(build, series, episodes, groups, relations)
