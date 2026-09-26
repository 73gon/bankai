"""AniDB identity for anime releases: which AniDB anime a release name is.

Erai-raws names releases after AniDB's romaji titles and numbers episodes per
AniDB entry, so an AniDB anime plus the release's own episode number is the
whole identity -- no season numbering to translate into, which is where the
TVDB route needed Anime-Lists, TheXEM, air-date gaps and saved mappings, and
where most of what got held for review came from.

Everything here reads two files bankai already keeps, refreshed daily: AniDB's
title dump (every title of every anime) and Anime-Lists (which AniDB entry is
which season of a TVDB show -- used here only to find sequels named by season
number, and to recognise what the TVDB era already downloaded).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bankai.metadata import anime_mapping

# Main and official titles decide first; synonyms and short names only when
# they do not. "Mao" is a show's main title and another show's short name.
_PRIMARY_TYPES = {"main", "official"}
_SECONDARY_TYPES = {"syn", "short"}
_MATCHING_TYPES = _PRIMARY_TYPES | _SECONDARY_TYPES
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


def normalise(value: str) -> str:
    return anime_mapping.normalise(value)


def loose(value: str) -> str:
    """A romanisation-insensitive key, for when the exact one finds nothing.

    Erai writes "wo" and "Tsuhan" where AniDB writes "o" and "Tsuuhan"; the
    same title otherwise. Only ever used when it finds exactly one anime.
    """
    joined = " ".join(normalise(value).replace(" wo ", " o ").split())
    for long, short in (("ou", "o"), ("uu", "u"), ("oo", "o"), ("aa", "a"), ("ii", "i"), ("ee", "e")):
        joined = joined.replace(long, short)
    return joined


def compact(value: str) -> str:
    """loose() without spaces: "Nani ka" and "Nanika" are one word apart."""
    return loose(value).replace(" ", "")


@dataclass(frozen=True)
class AniDBAnime:
    aid: int
    title: str
    english_title: str | None
    titles: tuple[str, ...]
    tvdb_id: int | None = None
    tvdb_season: str | None = None
    tvdb_offset: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "anidb_id": self.aid,
            "title": self.title,
            "english_title": self.english_title,
            "matching_titles": list(self.titles),
        }


@dataclass
class Resolution:
    anime: AniDBAnime | None = None
    method: str = ""
    candidates: list[int] = field(default_factory=list)
    error: str | None = None


@dataclass
class _Index:
    anime: dict[int, AniDBAnime]
    exact: dict[str, set[int]]
    loose: dict[str, set[int]]
    by_tvdb: dict[int, list[AniDBAnime]]
    primary: dict[str, set[int]] = field(default_factory=dict)
    compact: dict[str, set[int]] = field(default_factory=dict)


_INDEX: tuple[tuple[int, int], _Index] | None = None


def build_index(titles: Any, records: Any) -> _Index:
    """The lookup tables, from the two parsed XML documents."""
    mapping: dict[int, Any] = {}
    for record in records if records is not None else []:
        aid = record.get("anidbid")
        if aid and aid.isdigit():
            mapping[int(aid)] = record
    anime: dict[int, AniDBAnime] = {}
    exact: dict[str, set[int]] = defaultdict(set)
    primary: dict[str, set[int]] = defaultdict(set)
    loose_keys: dict[str, set[int]] = defaultdict(set)
    compact_keys: dict[str, set[int]] = defaultdict(set)
    for node in titles if titles is not None else []:
        aid = int(node.get("aid"))
        main = english = None
        names: list[str] = []
        for title in node.findall("title"):
            kind = title.get("type")
            text = (title.text or "").strip()
            if not text:
                continue
            language = title.get("{http://www.w3.org/XML/1998/namespace}lang") or title.get("lang")
            if kind == "main":
                main = text
            if kind == "official" and language == "en" and english is None:
                english = text
            if kind in _MATCHING_TYPES:
                names.append(text)
                exact[normalise(text)].add(aid)
                loose_keys[loose(text)].add(aid)
                compact_keys[compact(text)].add(aid)
                if kind in _PRIMARY_TYPES:
                    primary[normalise(text)].add(aid)
        record = mapping.get(aid)
        tvdb = record.get("tvdbid") if record is not None else None
        anime[aid] = AniDBAnime(
            aid=aid,
            title=main or (names[0] if names else str(aid)),
            english_title=english,
            titles=tuple(dict.fromkeys(names)),
            tvdb_id=int(tvdb) if tvdb and tvdb.isdigit() else None,
            tvdb_season=record.get("defaulttvdbseason") if record is not None else None,
            tvdb_offset=int((record.get("episodeoffset") if record is not None else 0) or 0),
        )
    by_tvdb: dict[int, list[AniDBAnime]] = defaultdict(list)
    for entry in anime.values():
        if entry.tvdb_id:
            by_tvdb[entry.tvdb_id].append(entry)
    for table in (exact, primary, loose_keys, compact_keys):
        table.pop("", None)
    return _Index(
        anime=anime,
        exact=dict(exact),
        loose=dict(loose_keys),
        by_tvdb=dict(by_tvdb),
        primary=dict(primary),
        compact=dict(compact_keys),
    )


async def index() -> _Index | None:
    global _INDEX
    titles = await anime_mapping._resource("anime-titles.xml.gz", anime_mapping._TITLES_URL)
    records = await anime_mapping._resource("anime-list.xml", anime_mapping._LIST_URL)
    if titles is None:
        return None
    revision = (id(titles), id(records))
    if _INDEX is None or _INDEX[0] != revision:
        _INDEX = (revision, build_index(titles, records))
    return _INDEX[1]


def cached_index() -> _Index | None:
    """The index as last built, without loading anything; for sync callers."""
    return _INDEX[1] if _INDEX is not None else None


async def anime(aid: int) -> AniDBAnime | None:
    table = await index()
    return table.anime.get(int(aid)) if table else None


def _names(name: str) -> list[str]:
    """The name, each side of a "Romaji | English" pair, and without a language tag."""
    variants = [name.strip()]
    if "|" in name:
        variants.extend(part.strip() for part in name.split("|"))
    variants.extend(_LANGUAGE_TAG.sub("", variant) for variant in list(variants))
    return list(dict.fromkeys(v for v in variants if v))


def resolve_in(table: _Index, name: str) -> Resolution:
    """Which AniDB anime an Erai show name is; pure, over a built index.

    Strictest first, each level only when it finds exactly one anime: main
    and official titles, then synonyms too, then romanisation-insensitive,
    then without spaces, then a sequel traced by its season number.
    """
    names = _names(name)
    ambiguous: set[int] = set()
    levels = (
        (table.primary, normalise, "exact"),
        (table.exact, normalise, "exact"),
        (table.loose, loose, "loose"),
        (table.compact, compact, "loose"),
    )
    for lookup, key, method in levels:
        for variant in names:
            hits = lookup.get(key(variant), set())
            if len(hits) == 1:
                aid = next(iter(hits))
                return Resolution(table.anime[aid], method if variant == names[0] else "split")
            if len(hits) > 1 and not ambiguous:
                ambiguous = set(hits)
    seasonal = _resolve_season(table, names[0])
    if seasonal.anime is None and ":" in names[0]:
        # "Ore dake Level Up na Ken Season 2: Arise from the Shadow": the
        # season marker sits before a subtitle.
        seasonal = _resolve_season(table, names[0].rsplit(":", 1)[0])
    if seasonal.anime is not None or not ambiguous:
        return seasonal
    return Resolution(
        candidates=sorted(ambiguous),
        error="AniDB match is ambiguous: "
        + ", ".join(table.anime[a].title for a in sorted(ambiguous)[:4]),
    )


def _resolve_season(table: _Index, name: str) -> Resolution:
    """A sequel named by season number, found through its TVDB season.

    AniDB names sequels by year -- "Boku no Hero Academia (2022)" -- where
    Erai writes "6th Season". Anime-Lists records which AniDB entry is which
    season of a TVDB show, so season N of the show the base name belongs to
    is the entry recorded as that season.
    """
    match = _SEASON_MARKER.match(name.strip())
    if not match:
        return Resolution(error="No AniDB anime has this title")
    base = match["base"] or match["base_only"]
    part = int(match["part"] or match["part_only"] or 1)
    bases = (
        table.primary.get(normalise(base))
        or table.exact.get(normalise(base))
        or table.loose.get(loose(base))
        or set()
    )
    shows = {table.anime[aid].tvdb_id for aid in bases if table.anime[aid].tvdb_id}
    if len(shows) != 1:
        return Resolution(error="No AniDB anime has this title, and its season cannot be traced")
    family = table.by_tvdb[shows.pop()]
    if match["final"]:
        seasons = [int(e.tvdb_season) for e in family if (e.tvdb_season or "").isdigit()]
        season = str(max(seasons)) if seasons else None
    elif match["season"] or match["nth"]:
        season = str(int(match["season"] or match["nth"]))
    elif match["spelled"]:
        season = str(_SPELLED[match["spelled"].casefold()])
    else:
        # A bare "Part N" continues whichever season the base name is.
        own = [table.anime[aid].tvdb_season for aid in bases if table.anime[aid].tvdb_season]
        season = own[0] if own else "1"
    parts = sorted((e for e in family if e.tvdb_season == season), key=lambda e: e.tvdb_offset)
    if len(parts) < part:
        return Resolution(error=f"AniDB has no entry recorded as season {season} part {part}")
    return Resolution(parts[part - 1], f"season {season} part {part}")


async def resolve(name: str) -> Resolution:
    table = await index()
    if table is None:
        return Resolution(error="AniDB titles are unavailable")
    return resolve_in(table, name)


def settle_episode(table: _Index, entry: AniDBAnime, episode: int) -> tuple[AniDBAnime, int]:
    """Put an episode number past the end of its AniDB entry where it belongs.

    AniDB splits a season with a broadcast break into parts, each numbered
    from 1. Some releases number straight through instead -- Crunchyroll
    calls the first episode of a second cour "13". Anime-Lists records where
    each part starts within its TVDB season, which is what tells the two apart.
    """
    if not entry.tvdb_id or not (entry.tvdb_season or "").isdigit():
        return entry, episode
    parts = sorted(
        (e for e in table.by_tvdb.get(entry.tvdb_id, []) if e.tvdb_season == entry.tvdb_season),
        key=lambda e: e.tvdb_offset,
    )
    later = [e for e in parts if e.tvdb_offset > entry.tvdb_offset]
    if not later:
        return entry, episode  # the last part: no end to overrun
    length = later[0].tvdb_offset - entry.tvdb_offset
    if episode <= length:
        return entry, episode
    # Numbered from the start of the season: "Part 2 - 13" is part 2's first.
    if entry.tvdb_offset and entry.tvdb_offset < episode <= entry.tvdb_offset + length:
        return entry, episode - entry.tvdb_offset
    # Numbered on from this part into whichever part holds that number.
    within_season = entry.tvdb_offset + episode
    target = max((e for e in parts if e.tvdb_offset < within_season), key=lambda e: e.tvdb_offset)
    return target, within_season - target.tvdb_offset


async def settle(entry: AniDBAnime, episode: int) -> tuple[AniDBAnime, int]:
    table = await index()
    return settle_episode(table, entry, episode) if table else (entry, episode)


def legacy_tvdb_episode(entry: AniDBAnime, episode: int) -> tuple[int, int, int] | None:
    """The (TVDB id, season, episode) the TVDB era would have filed this under.

    Used to recognise what was downloaded before the switch, which is keyed
    and foldered by TVDB, so an episode already held is not fetched twice.
    Only where Anime-Lists gives a plain season and offset.
    """
    if not entry.tvdb_id or not (entry.tvdb_season or "").isdigit():
        return None
    season = int(entry.tvdb_season)
    if season <= 0:
        return None
    return entry.tvdb_id, season, episode + entry.tvdb_offset
