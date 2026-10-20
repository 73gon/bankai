"""AniList: what anime is identified by.

Every anime bankai knows is an AniList entry -- one per season, or per cour,
as AniList splits them -- and a release is that entry plus its own episode
number. Two sources:

- anime-offline-database (see offline): every AniList entry with its title
  and synonyms from every site, which is what a release name is matched on,
  offline and without asking AniList anything;
- AniList's API, for what is shown and what links entries: names, format,
  episode counts, episode titles and air dates, covers, and the typed
  relations (sequel, prequel, side story...) that put a show's seasons in
  order. Asked fifty entries at a time, slowly, and kept a week.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.metadata import offline

log = get_logger(__name__)

# -- Names ---------------------------------------------------------------------

_SPELLED = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7}

# "Season 3", "3rd Season", "Third Season", "S3", "Final Season", each
# optionally followed by a "Part N"; or a bare "Part N" / "Cour N".
_SEASON_MARKER = re.compile(
    r"^(?P<base>.+?)\s+(?:(?:S|Season\s*)(?P<season>\d{1,2})"
    r"|(?P<nth>\d{1,2})(?:st|nd|rd|th)\s+Season"
    r"|(?P<spelled>Second|Third|Fourth|Fifth|Sixth|Seventh)\s+Season"
    r"|(?P<final>Final\s+Season))"
    r"(?:\s+(?:Part|Cour)\s+(?P<part>\d))?$"
    r"|^(?P<base_only>.+?)\s+(?:Part|Cour)\s+(?P<part_only>\d)$",
    re.IGNORECASE,
)
# Erai tags the same show once per audio language: "Dragon Raja (CA)".
_LANGUAGE_TAG = re.compile(r"\s*\((?:[A-Z]{2})\)\s*$")
# Formats a season of a show comes in; movies, specials and OVAs are not seasons.
SEASON_FORMATS = frozenset({"TV", "TV_SHORT", "ONA"})


def normalise(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold().replace("_", " "), re.UNICODE))


def loose(value: str) -> str:
    """A romanisation-insensitive key, for when the exact one finds nothing.

    Erai writes "wo" and "Tsuhan" where others write "o" and "Tsuuhan"; the
    same title otherwise. Only ever used when it finds exactly one anime.
    """
    joined = " ".join(f" {normalise(value)} ".replace(" wo ", " o ").split())
    for long, short in (("ou", "o"), ("uu", "u"), ("oo", "o"), ("aa", "a"), ("ii", "i"), ("ee", "e")):
        joined = joined.replace(long, short)
    return joined


def compact(value: str) -> str:
    """loose() without spaces: "Nani ka" and "Nanika" are one word apart."""
    return loose(value).replace(" ", "")


# -- The API cache -------------------------------------------------------------

_API = "https://graphql.anilist.co"
_KEEP = 7 * 86400
_BATCH = 50
# AniList allows ninety requests a minute, thirty while degraded: well under.
_PAUSE = 2.5
_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id format status episodes duration season seasonYear
      startDate { year month day }
      title { romaji english native }
      synonyms description(asHtml: false) genres
      studios(isMain: true) { nodes { name } }
      coverImage { extraLarge large } bannerImage
      airingSchedule(perPage: 100) { nodes { episode airingAt } }
      streamingEpisodes { title }
      relations { edges { relationType(version: 2) node { id type format } } }
    }
  }
}
"""
_SEARCH = """
query ($q: String, $n: Int) {
  Page(perPage: $n) {
    media(search: $q, type: ANIME, sort: SEARCH_MATCH) {
      id format status episodes seasonYear
      title { romaji english native }
      coverImage { large }
    }
  }
}
"""
_STREAMING = re.compile(r"^Episode\s+(\d+)\s*[-–:]\s*(.+)$", re.IGNORECASE)

_MEDIA: tuple[float, dict[int, dict]] | None = None
_LOCK = asyncio.Lock()


def _media_store() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "anilist_media.json"


def media_all() -> dict[int, dict]:
    """Everything kept from AniList's API, by AniList id."""
    global _MEDIA
    path = _media_store()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _MEDIA is None or _MEDIA[0] != mtime:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        _MEDIA = (mtime, {int(key): row for key, row in raw.items() if str(key).isdigit()})
    return _MEDIA[1]


def media(anilist_id: object) -> dict:
    try:
        return media_all().get(int(anilist_id), {})  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}


def _save_media(rows: dict[int, dict]) -> None:
    global _MEDIA
    path = _media_store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({str(key): row for key, row in rows.items()}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    _MEDIA = None


def _date(parts: dict | None) -> str | None:
    if not parts or not parts.get("year"):
        return None
    return f"{parts['year']:04d}-{parts.get('month') or 1:02d}-{parts.get('day') or 1:02d}"


def parse(item: dict) -> dict:
    """One API answer, as kept: what bankai shows and links by."""
    title = item.get("title") or {}
    airing = {}
    for node in (item.get("airingSchedule") or {}).get("nodes") or []:
        if isinstance(node.get("episode"), int) and isinstance(node.get("airingAt"), int):
            stamp = datetime.fromtimestamp(node["airingAt"], tz=UTC)
            airing[str(node["episode"])] = stamp.date().isoformat()
    streaming = {}
    for episode in item.get("streamingEpisodes") or []:
        match = _STREAMING.match(str(episode.get("title") or ""))
        if match:
            streaming.setdefault(str(int(match[1])), match[2].strip())
    relations = [
        [edge["node"]["id"], edge.get("relationType"), edge["node"].get("format")]
        for edge in (item.get("relations") or {}).get("edges") or []
        if (edge.get("node") or {}).get("type") == "ANIME" and isinstance(edge["node"].get("id"), int)
    ]
    image = item.get("coverImage") or {}
    return {
        "romaji": title.get("romaji"),
        "english": title.get("english"),
        "native": title.get("native"),
        "synonyms": item.get("synonyms") or [],
        "format": item.get("format"),
        "status": item.get("status"),
        "episodes": item.get("episodes"),
        "duration": item.get("duration"),
        "season": item.get("season"),
        "year": item.get("seasonYear") or (item.get("startDate") or {}).get("year"),
        "start": _date(item.get("startDate")),
        "description": item.get("description"),
        "genres": item.get("genres") or [],
        "studios": [node.get("name") for node in (item.get("studios") or {}).get("nodes") or [] if node.get("name")],
        "cover": image.get("extraLarge") or image.get("large"),
        "banner": item.get("bannerImage"),
        "airing": airing,
        "streaming": streaming,
        "relations": relations,
        "fetched_at": time.time(),
    }


async def _ask(client: httpx.AsyncClient, query: str, variables: dict) -> dict:
    for _attempt in range(3):
        response = await client.post(_API, json={"query": query, "variables": variables})
        if response.status_code == 429:
            # Too many: AniList says how long to wait.
            await asyncio.sleep(float(response.headers.get("Retry-After") or 30) + 1)
            continue
        response.raise_for_status()
        return response.json() or {}
    raise RuntimeError("AniList kept refusing: rate limited")


async def ensure(anilist_ids: set[int] | list[int], *, max_batches: int = 6, refresh: bool = True) -> int:
    """Fetch what is not kept yet -- or, with ``refresh``, a week old -- of these entries.

    Returns how many entries were fetched. At most ``max_batches`` requests.
    """
    async with _LOCK:
        rows = dict(media_all())
        now = time.time()
        wanted = [
            int(aid) for aid in dict.fromkeys(anilist_ids)
            if int(aid) not in rows or (refresh and now - float(rows[int(aid)].get("fetched_at", 0)) >= _KEEP)
        ][: _BATCH * max_batches]
        if not wanted:
            return 0
        fetched = 0
        async with httpx.AsyncClient(timeout=30, headers={"Content-Type": "application/json"}) as client:
            for start in range(0, len(wanted), _BATCH):
                ids = wanted[start : start + _BATCH]
                try:
                    answer = await _ask(client, _QUERY, {"ids": ids})
                except Exception as exc:
                    log.warning("AniList could not be asked: %s", exc)
                    break
                found = ((answer.get("data") or {}).get("Page") or {}).get("media") or []
                seen = set()
                for item in found:
                    if isinstance(item, dict) and isinstance(item.get("id"), int):
                        rows[item["id"]] = parse(item)
                        seen.add(item["id"])
                        fetched += 1
                # Gone from AniList: remembered as such rather than asked for again.
                for missing in set(ids) - seen:
                    rows[missing] = {**rows.get(missing, {}), "fetched_at": time.time(), "missing": True}
                if start + _BATCH < len(wanted):
                    await asyncio.sleep(_PAUSE)
        _save_media(rows)
        return fetched


async def search(query: str, limit: int = 20) -> list[dict]:
    """AniList's own search, for picking an entry by hand."""
    query = query.strip()
    if not query:
        return []
    if query.isdigit() or "anilist.co/anime/" in query:
        found = re.search(r"(\d+)", query.split("anilist.co/anime/")[-1])
        if found:
            await ensure([int(found[1])], refresh=False)
            return [as_choice(int(found[1]))] if media(int(found[1])) else []
    async with httpx.AsyncClient(timeout=30, headers={"Content-Type": "application/json"}) as client:
        answer = await _ask(client, _SEARCH, {"q": query, "n": limit})
    rows = ((answer.get("data") or {}).get("Page") or {}).get("media") or []
    return [
        {
            "anilist_id": item["id"],
            "name": (item.get("title") or {}).get("english") or (item.get("title") or {}).get("romaji"),
            "romaji": (item.get("title") or {}).get("romaji"),
            "english": (item.get("title") or {}).get("english"),
            "format": item.get("format"),
            "episodes": item.get("episodes"),
            "year": item.get("seasonYear"),
            "cover": (item.get("coverImage") or {}).get("large"),
        }
        for item in rows if isinstance(item, dict) and isinstance(item.get("id"), int)
    ]


# -- An entry ------------------------------------------------------------------


@dataclass(frozen=True)
class Anime:
    id: int
    # How it is shown: English, else romaji, else the database's title.
    name: str
    romaji: str | None
    english: str | None
    titles: tuple[str, ...]
    format: str | None = None
    episodes: int | None = None
    year: int | None = None

    @property
    def title(self) -> str:
        """The romanised title, as a release would be named."""
        return self.romaji or self.name

    def as_dict(self) -> dict[str, Any]:
        return {
            "anilist_id": self.id,
            "name": self.name,
            "romaji": self.romaji,
            "english": self.english,
            "format": self.format,
            "episodes": self.episodes,
            "year": self.year,
            "matching_titles": list(self.titles),
        }


def _anime(anilist_id: int, row: dict, kept: dict) -> Anime:
    romaji = kept.get("romaji") or row.get("title") or None
    english = kept.get("english") or None
    names = [row.get("title"), *(row.get("synonyms") or []), kept.get("romaji"), kept.get("english")]
    return Anime(
        id=anilist_id,
        name=english or romaji or str(row.get("title") or anilist_id),
        romaji=romaji,
        english=english,
        titles=tuple(dict.fromkeys(name for name in names if name)),
        format=kept.get("format") or _format_of(row.get("type")),
        episodes=kept.get("episodes") or row.get("episodes"),
        year=kept.get("year") or row.get("year"),
    )


def _format_of(kind: object) -> str | None:
    """anime-offline-database's type, in AniList's words."""
    return {"TV": "TV", "MOVIE": "MOVIE", "OVA": "OVA", "ONA": "ONA", "SPECIAL": "SPECIAL"}.get(str(kind or "").upper())


def name(anilist_id: object) -> str | None:
    """How an entry is shown: English, else romaji, else the database's title."""
    kept = media(anilist_id)
    return kept.get("english") or kept.get("romaji") or offline.entry(anilist_id).get("title") or None


def cover(anilist_id: object) -> str | None:
    return media(anilist_id).get("cover") or offline.entry(anilist_id).get("picture") or None


def kind(anilist_id: object) -> str | None:
    """Its format: TV, MOVIE, OVA, ONA, SPECIAL, MUSIC; TV_SHORT counts as TV."""
    value = media(anilist_id).get("format") or _format_of(offline.entry(anilist_id).get("type"))
    return "TV" if value == "TV_SHORT" else value


def relations(anilist_id: object) -> list[tuple[int, str, str | None]]:
    """(other entry, AniList relation type, its format) for each anime it is related to."""
    return [(int(other), str(kind_), form) for other, kind_, form in media(anilist_id).get("relations") or []]


def episode_count(anilist_id: object) -> int | None:
    return media(anilist_id).get("episodes") or offline.entry(anilist_id).get("episodes")


def _own_numbers(numbered: dict, count: int) -> dict[int, str]:
    """An entry's episodes by its own numbers, from a list AniList may number another way.

    Streaming sites list a second season as "Episode 26".."Episode 37", or
    list the first season's episodes on the second's page. Numbers that fit
    the entry are taken as they are; a run of exactly as many as the entry has
    is renumbered from 1; anything else says nothing about this entry.
    """
    rows = {int(number): value for number, value in numbered.items() if str(number).isdigit()}
    if not rows or not count:
        return rows
    keys = sorted(rows)
    if keys[-1] <= count:
        return rows
    if len(keys) == count and keys[-1] - keys[0] == count - 1:
        return {number - keys[0] + 1: rows[number] for number in keys}
    return {}


def episodes(anilist_id: object) -> list[tuple[int, str | None, str | None]]:
    """(number, title, air date) of each of its episodes, as far as known.

    As many as AniList says it has; while it is airing and has no count yet,
    as many as are scheduled.
    """
    kept = media(anilist_id)
    count = episode_count(anilist_id) or 0
    airing = _own_numbers(kept.get("airing") or {}, count)
    streaming = _own_numbers(kept.get("streaming") or {}, count)
    last = count or max([*airing, *streaming], default=0)
    return [(n, streaming.get(n), airing.get(n)) for n in range(1, last + 1)]


def finished(anilist_id: object) -> bool:
    return (media(anilist_id).get("status") or offline.entry(anilist_id).get("status")) == "FINISHED"


def year(anilist_id: object) -> int | None:
    return media(anilist_id).get("year") or offline.entry(anilist_id).get("year")


def as_choice(anilist_id: int) -> dict:
    """An entry as the pick-an-anime dialog shows it."""
    kept = media(anilist_id)
    return {
        "anilist_id": anilist_id,
        "name": name(anilist_id),
        "romaji": kept.get("romaji"),
        "english": kept.get("english"),
        "format": kind(anilist_id),
        "episodes": episode_count(anilist_id),
        "year": year(anilist_id),
        "cover": cover(anilist_id),
    }


# -- The title index -------------------------------------------------------------


@dataclass
class Resolution:
    anime: Anime | None = None
    method: str = ""
    candidates: list[int] = field(default_factory=list)
    error: str | None = None


@dataclass
class _Index:
    anime: dict[int, Anime]
    exact: dict[str, set[int]]
    loose: dict[str, set[int]]
    primary: dict[str, set[int]] = field(default_factory=dict)
    compact: dict[str, set[int]] = field(default_factory=dict)


_INDEX: tuple[tuple[float, float], _Index] | None = None


def build_index(entries: dict[int, dict], kept: dict[int, dict]) -> _Index:
    """The lookup tables: every title of every entry, its main ones apart."""
    anime: dict[int, Anime] = {}
    exact: dict[str, set[int]] = defaultdict(set)
    primary: dict[str, set[int]] = defaultdict(set)
    loose_keys: dict[str, set[int]] = defaultdict(set)
    compact_keys: dict[str, set[int]] = defaultdict(set)
    for anilist_id in set(entries) | set(kept):
        row = entries.get(anilist_id, {})
        api = kept.get(anilist_id, {})
        if api.get("missing") and not row:
            continue
        entry = _anime(anilist_id, row, api)
        anime[anilist_id] = entry
        mains = {row.get("title"), api.get("romaji"), api.get("english")}
        for value in entry.titles:
            exact[normalise(value)].add(anilist_id)
            loose_keys[loose(value)].add(anilist_id)
            compact_keys[compact(value)].add(anilist_id)
            if value in mains:
                primary[normalise(value)].add(anilist_id)
    for table in (exact, primary, loose_keys, compact_keys):
        table.pop("", None)
    return _Index(anime=anime, exact=dict(exact), loose=dict(loose_keys), primary=dict(primary), compact=dict(compact_keys))


def table() -> _Index | None:
    """The index over what is kept; rebuilt when either source changes. No network."""
    global _INDEX
    stamps = []
    for path in (offline._store(), _media_store()):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            stamps.append(0.0)
    revision = (stamps[0], stamps[1])
    if not stamps[0] and not stamps[1]:
        return None
    if _INDEX is None or _INDEX[0] != revision:
        _INDEX = (revision, build_index(offline.entries(), media_all()))
    return _INDEX[1]


async def index() -> _Index | None:
    return await asyncio.to_thread(table)


def cached_index() -> _Index | None:
    """The index as last built, without building it; for sync callers that must not wait."""
    return _INDEX[1] if _INDEX is not None else table()


async def anime(anilist_id: int) -> Anime | None:
    found = await index()
    if found is None:
        return None
    if int(anilist_id) not in found.anime:
        await ensure([int(anilist_id)], refresh=False)
        found = await index()
    return found.anime.get(int(anilist_id)) if found else None


def _names(value: str) -> list[str]:
    """The name, each side of a "Romaji | English" pair, and without a language tag."""
    variants = [value.strip()]
    if "|" in value:
        variants.extend(part.strip() for part in value.split("|"))
    variants.extend(_LANGUAGE_TAG.sub("", variant) for variant in list(variants))
    return list(dict.fromkeys(v for v in variants if v))


def resolve_in(found: _Index, value: str) -> Resolution:
    """Which AniList entry an Erai show name is; over a built index, no network.

    Strictest first, each level only when it finds exactly one entry: main
    titles, then synonyms too, then romanisation-insensitive, then without
    spaces, then a sequel traced by its season number.
    """
    names = _names(value)
    ambiguous: set[int] = set()
    levels = (
        (found.primary, normalise, "exact"),
        (found.exact, normalise, "exact"),
        (found.loose, loose, "loose"),
        (found.compact, compact, "loose"),
    )
    for lookup, key, method in levels:
        for variant in names:
            hits = lookup.get(key(variant), set())
            if len(hits) == 1:
                hit = next(iter(hits))
                return Resolution(found.anime[hit], method if variant == names[0] else "split")
            if len(hits) > 1 and not ambiguous:
                ambiguous = set(hits)
    seasonal = _resolve_season(found, names[0])
    if seasonal.anime is None and ":" in names[0]:
        # "Ore dake Level Up na Ken Season 2: Arise from the Shadow": the
        # season marker sits before a subtitle.
        seasonal = _resolve_season(found, names[0].rsplit(":", 1)[0])
    if seasonal.anime is not None or not ambiguous:
        return seasonal
    return Resolution(
        candidates=sorted(ambiguous),
        error="AniList match is ambiguous: " + ", ".join(found.anime[a].name for a in sorted(ambiguous)[:4]),
    )


def _next_season(anilist_id: int) -> int | None:
    """The entry that continues a show's seasons after this one: its TV-like sequel."""
    sequels = [other for other, kind_, form in relations(anilist_id) if kind_ == "SEQUEL" and form in SEASON_FORMATS]
    return min(sequels, key=lambda other: (year(other) or 9999, other)) if sequels else None


def _first_season(found: _Index, hits: set[int]) -> int | None:
    """Of the entries a show's bare name finds, the first season: TV-like, earliest."""
    seasons = [aid for aid in hits if kind(aid) in {"TV", "ONA"}]
    pool = seasons or list(hits)
    return min(pool, key=lambda aid: (year(aid) or 9999, aid)) if pool else None


def _resolve_season(found: _Index, value: str) -> Resolution:
    """A sequel named by season number, found by walking the show's sequels on AniList.

    Release names say "6th Season" where AniList names an entry by year or
    subtitle. Season N is the show's first season followed N-1 times to the
    TV sequel; a "Part N" goes N-1 further.
    """
    match = _SEASON_MARKER.match(value.strip())
    if not match:
        return Resolution(error="No AniList entry has this title")
    base = match["base"] or match["base_only"]
    part = int(match["part"] or match["part_only"] or 1)
    hits = (
        found.primary.get(normalise(base))
        or found.exact.get(normalise(base))
        or found.loose.get(loose(base))
        or set()
    )
    current = _first_season(found, hits)
    if current is None:
        return Resolution(error="No AniList entry has this title, and its season cannot be traced")
    if match["base_only"]:
        steps = part - 1  # a bare "Part N" continues the base itself
    elif match["final"]:
        steps = 10**3  # to the last season there is
    else:
        season = int(match["season"] or match["nth"] or _SPELLED.get((match["spelled"] or "").casefold(), 1))
        steps = season - 1 + (part - 1)
    for _ in range(steps):
        after = _next_season(current)
        if after is None:
            if match["final"]:
                break
            return Resolution(error="AniList's sequels of this show do not reach that season yet")
        current = after
    entry = found.anime.get(current)
    if entry is None:
        return Resolution(error="That season is not in the title index yet")
    return Resolution(entry, "season")


async def resolve(value: str) -> Resolution:
    found = await index()
    if found is None:
        return Resolution(error="AniList titles are unavailable")
    result = resolve_in(found, value)
    if result.anime is None and _SEASON_MARKER.match(value.strip()):
        # The sequels walked may not be fetched yet: fetch the show's, and try again.
        base = _SEASON_MARKER.match(value.strip())
        name_ = base["base"] or base["base_only"]
        hits = found.primary.get(normalise(name_)) or found.exact.get(normalise(name_)) or set()
        chain = set(hits)
        for _ in range(6):
            await ensure(chain, refresh=False, max_batches=1)
            more = {other for aid in chain for other, kind_, _form in relations(aid) if kind_ == "SEQUEL"} - chain
            if not more:
                break
            chain |= more
        result = resolve_in(await index(), value)
    return result


def settle_in(entry: Anime, episode: int) -> tuple[Anime, int]:
    """Put an episode number past the end of its entry where it belongs; no network.

    AniList splits a season with a broadcast break into parts, each numbered
    from 1. Some releases number straight through instead -- Crunchyroll calls
    the first episode of a second cour "13". Past the entry's episode count,
    the number moves on into its TV sequel.
    """
    found = cached_index()
    current, number = entry, episode
    for _ in range(12):
        count = episode_count(current.id)
        after = _next_season(current.id)
        if not count or number <= count or after is None or found is None or after not in found.anime:
            break
        number -= count
        current = found.anime[after]
    return current, number


async def settle(entry: Anime, episode: int) -> tuple[Anime, int]:
    await ensure([entry.id], refresh=False, max_batches=1)
    after = _next_season(entry.id)
    if after is not None:
        await ensure([after], refresh=False, max_batches=1)
    return settle_in(entry, episode)


__all__ = [
    "Anime",
    "Resolution",
    "anime",
    "as_choice",
    "build_index",
    "cached_index",
    "cover",
    "ensure",
    "episode_count",
    "episodes",
    "finished",
    "index",
    "kind",
    "media",
    "name",
    "normalise",
    "relations",
    "resolve",
    "resolve_in",
    "search",
    "settle",
    "settle_in",
    "table",
    "year",
]
