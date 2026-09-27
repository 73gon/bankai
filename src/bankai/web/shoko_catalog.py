"""What Shoko knows about the anime library, in one read.

Two listings: every series (one per AniDB entry) with its name and poster,
and every episode -- missing ones included -- with the files linked to it.
Together they say, for each file on disk, which AniDB entry and episode it
is, and for each entry, its whole AniDB episode list. The episode listing is
~15 MB and takes Shoko ~10 s, so it is read in the background and held.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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


@dataclass
class Catalog:
    entries: dict[int, Entry] = field(default_factory=dict)
    # Absolute file path -> the (AniDB id, episode number) it is linked to.
    # Regular episodes only; a double episode's file is linked to both.
    files: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

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


def build(series: Any, episodes: Any) -> Catalog:
    """The catalogue, from the two listings as Shoko returns them; pure."""
    catalog = Catalog()
    names: dict[int, tuple[str, str | None, tuple[int, ...]]] = {}
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
        catalog.entries[aid] = Entry(
            aid=aid,
            title=title,
            poster_url=poster,
            episodes=tuple(episodes_of[n] for n in sorted(episodes_of)),
            tvdb_ids=tvdb,
        )
    for links in catalog.files.values():
        links.sort(key=lambda link: link[1])
    return catalog


async def fetch() -> Catalog | None:
    """Read both listings from Shoko, or None when it is not connected."""
    if not shoko.configured():
        return None
    series = await shoko._get("/api/v3/Series", pageSize=0)
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
    return await asyncio.to_thread(build, series, episodes)
