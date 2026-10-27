"""bankai's own genres: one curated list per title, instead of what the providers mixed.

Jellyfin's genres come from TMDB, TVDB, AniList and once AniDB, in English and
German, broad and duplicated (Sci-Fi beside Science Fiction, Komödie beside
Comedy). bankai keeps a vocabulary of its own and works out each title's
genres from what the providers say:

    raw genres      Jellyfin's Genres (TMDB / TVDB), or AniList's for anime
    keyword tags    Jellyfin's Tags: TMDB keywords ("superhero", "fairy tale")
    library         every title of the anime library is Anime

through ``genre_rules.json`` (vocabulary in priority order, mappings from raw
genre and tag to genre ids, library rules, and which genre gives way to which),
and per-title hand choices in ``genre_overrides.json`` (add / remove), keyed by
a stable id: ``anime:<collection id>``, ``tmdb:movie:<id>``, ``tmdb:tv:<id>``
(``tvdb:tv:<id>`` / ``imdb:<id>`` where a title has no TMDB id) -- never by
Jellyfin's item id, which comes from the path.

``resolve`` is pure: the stores are read by the caller. The result is ordered
specific before broad, as the vocabulary is, since clients often show three.
Writing it is genre_sync's.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs

LANGUAGES = ("en", "de")
# The NFO files' <genre> splits on "/", Jellyfin's genre query on "|".
_FORBIDDEN = ("/", "|")
_LOCK = threading.Lock()


def _genre(gid: str, en: str, de: str) -> dict[str, str]:
    return {"id": gid, "en": en, "de": de}


# Specific before broad: the order is the priority.
DEFAULT_VOCABULARY = [
    _genre("superheroes", "Superheroes", "Superhelden"),
    _genre("fairy_tales", "Fairy Tales", "Märchen"),
    _genre("sports", "Sports", "Sport"),
    _genre("western", "Western", "Western"),
    _genre("documentary", "Documentary", "Dokumentation"),
    _genre("music", "Music & Musicals", "Musik & Musical"),
    _genre("history_war", "History & War", "Geschichte & Krieg"),
    _genre("slice_of_life", "Slice of Life", "Slice of Life"),
    _genre("psychological", "Psychological", "Psycho"),
    _genre("supernatural", "Supernatural", "Übernatürlich"),
    _genre("horror", "Horror", "Horror"),
    _genre("mystery", "Mystery", "Mystery"),
    _genre("crime", "Crime", "Krimi"),
    _genre("science_fiction", "Science Fiction", "Science-Fiction"),
    _genre("fantasy", "Fantasy", "Fantasy"),
    _genre("romance", "Romance", "Romantik"),
    _genre("thriller", "Thriller", "Thriller"),
    _genre("comedy", "Comedy", "Komödie"),
    _genre("drama", "Drama", "Drama"),
    _genre("family_kids", "Family & Kids", "Familie & Kinder"),
    _genre("adventure", "Adventure", "Abenteuer"),
    _genre("action", "Action", "Action"),
    _genre("animation", "Animation", "Animation"),
    _genre("anime", "Anime", "Anime"),
]

# A raw genre (any provider, any language, compared casefolded) -> genre ids; [] drops it
# knowingly. A raw genre in neither list is "unmapped": dropped too, and listed for a decision.
DEFAULT_GENRE_MAP: dict[str, list[str]] = {
    "action": ["action"],
    "adventure": ["adventure"],
    "abenteuer": ["adventure"],
    "action & adventure": ["action", "adventure"],
    "animation": ["animation"],
    "zeichentrick": ["animation"],
    "anime": ["anime"],
    "comedy": ["comedy"],
    "komödie": ["comedy"],
    "crime": ["crime"],
    "krimi": ["crime"],
    "documentary": ["documentary"],
    "dokumentarfilm": ["documentary"],
    "drama": ["drama"],
    "family": ["family_kids"],
    "familie": ["family_kids"],
    "children": ["family_kids"],
    "kids": ["family_kids"],
    "fantasy": ["fantasy"],
    "mahou shoujo": ["fantasy"],
    "history": ["history_war"],
    "historie": ["history_war"],
    "war": ["history_war"],
    "kriegsfilm": ["history_war"],
    "war & politics": ["history_war"],
    "horror": ["horror"],
    "music": ["music"],
    "musik": ["music"],
    "musical": ["music"],
    "mystery": ["mystery"],
    "psychological": ["psychological"],
    "romance": ["romance"],
    "liebesfilm": ["romance"],
    "science fiction": ["science_fiction"],
    "sci-fi": ["science_fiction"],
    "mecha": ["science_fiction"],
    "sci-fi & fantasy": ["science_fiction", "fantasy"],
    "slice of life": ["slice_of_life"],
    "sports": ["sports"],
    "sport": ["sports"],
    "supernatural": ["supernatural"],
    "thriller": ["thriller"],
    "suspense": ["thriller"],
    "western": ["western"],
    "martial arts": ["action"],
    # Content flags, not genres.
    "ecchi": [],
    "hentai": [],
}

# A keyword tag (compared casefolded) -> genre ids. Left out on purpose: "christmas",
# "ghost", "supernatural" (mostly Harry Potter), "based on comic" (far too broad).
DEFAULT_TAG_MAP: dict[str, list[str]] = {
    "superhero": ["superheroes"],
    "superheroes": ["superheroes"],
    "superhero team": ["superheroes"],
    "superheroine": ["superheroes"],
    "female superhero": ["superheroes"],
    "marvel cinematic universe (mcu)": ["superheroes"],
    "dc extended universe (dceu)": ["superheroes"],
    "dc universe (dcu)": ["superheroes"],
    "fairy tale": ["fairy_tales"],
    "based on fairy tale": ["fairy_tales"],
    "martial arts": ["action"],
    "world war ii": ["history_war"],
    "historical fiction": ["history_war"],
    "period drama": ["history_war"],
    "organized crime": ["crime"],
    "gangster": ["crime"],
    "heist": ["crime"],
    "musical": ["music"],
    "psychological thriller": ["psychological", "thriller"],
    "anime": ["anime"],
}

# Every title of a library gets these.
DEFAULT_LIBRARIES: dict[str, list[str]] = {"anime": ["anime"]}

# A genre that, present, takes another's place: anime is not Animation as well.
DEFAULT_YIELDS: dict[str, list[str]] = {"anime": ["animation"]}


def default_rules() -> dict[str, Any]:
    return {
        "language": "en",
        "vocabulary": [dict(row) for row in DEFAULT_VOCABULARY],
        "genres": {key: list(value) for key, value in DEFAULT_GENRE_MAP.items()},
        "tags": {key: list(value) for key, value in DEFAULT_TAG_MAP.items()},
        "libraries": {key: list(value) for key, value in DEFAULT_LIBRARIES.items()},
        "yields": {key: list(value) for key, value in DEFAULT_YIELDS.items()},
    }


def norm(value: str) -> str:
    return " ".join(str(value).split()).casefold()


# -- Checking rules -------------------------------------------------------------------------


class RulesError(ValueError):
    """Rules that cannot be kept, saying why."""


def check(rules: dict[str, Any]) -> dict[str, Any]:
    """Rules made tidy (keys casefolded, ids deduplicated) or RulesError: every name in both
    languages, none with "/" or "|", ids unique, every mapping onto a genre that exists."""
    if rules.get("language") not in LANGUAGES:
        raise RulesError(f"language must be one of {', '.join(LANGUAGES)}")
    vocabulary = []
    seen: set[str] = set()
    names: dict[str, set[str]] = {language: set() for language in LANGUAGES}
    for row in rules.get("vocabulary") or []:
        gid = str(row.get("id") or "").strip()
        if not gid or not all(ch.isalnum() or ch == "_" for ch in gid):
            raise RulesError(f"a genre id must be letters, digits and _: {gid!r}")
        if gid in seen:
            raise RulesError(f"the genre id {gid} is there twice")
        seen.add(gid)
        clean = {"id": gid}
        for language in LANGUAGES:
            name = " ".join(str(row.get(language) or "").split())
            if not name:
                raise RulesError(f"{gid} has no {language} name")
            if any(mark in name for mark in _FORBIDDEN):
                raise RulesError(f"{name!r}: a genre name cannot hold / or |")
            if name.casefold() in names[language]:
                raise RulesError(f"the {language} name {name!r} is there twice")
            names[language].add(name.casefold())
            clean[language] = name
        vocabulary.append(clean)

    def mapping(section: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for raw, ids in (rules.get(section) or {}).items():
            key = norm(raw)
            if not key:
                continue
            targets = list(dict.fromkeys(str(gid) for gid in ids or []))
            unknown = [gid for gid in targets if gid not in seen]
            if unknown:
                raise RulesError(f"{section}: {raw!r} maps onto {', '.join(unknown)}, which is no genre")
            out[key] = targets
        return out

    return {
        "language": rules["language"],
        "vocabulary": vocabulary,
        "genres": mapping("genres"),
        "tags": mapping("tags"),
        "libraries": mapping("libraries"),
        "yields": mapping("yields"),
    }


# -- Resolving ------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Resolution:
    """A title's curated genres and where each came from."""

    # Genre ids, in priority order.
    ids: list[str]
    # Each id's reasons: ("genre", raw) / ("tag", raw) / ("library", name) / ("added", "") ...
    sources: dict[str, list[tuple[str, str]]]
    # Raw genres no rule knows: dropped, listed for a decision.
    unmapped: list[str]
    # Raw genres a rule drops knowingly (mapped onto nothing: Ecchi, Hentai).
    discarded: list[str]
    # Ids taken away, and why: ("removed", "") by hand, ("yields", winner).
    dropped: dict[str, list[tuple[str, str]]]

    def names(self, rules: dict[str, Any], language: str | None = None) -> list[str]:
        language = language or rules["language"]
        by_id = {row["id"]: row for row in rules["vocabulary"]}
        return [by_id[gid][language] for gid in self.ids if gid in by_id]


def resolve(
    rules: dict[str, Any],
    *,
    genres: Iterable[str] = (),
    tags: Iterable[str] = (),
    library: str | None = None,
    override: dict[str, Any] | None = None,
) -> Resolution:
    """A title's curated genres from its raw genres, keyword tags and library, and its hand choices."""
    sources: dict[str, list[tuple[str, str]]] = {}
    dropped: dict[str, list[tuple[str, str]]] = {}
    unmapped: list[str] = []
    discarded: list[str] = []

    def give(gid: str, why: tuple[str, str]) -> None:
        reasons = sources.setdefault(gid, [])
        if why not in reasons:
            reasons.append(why)

    for raw in genres:
        key = norm(raw)
        if not key:
            continue
        if key not in rules["genres"]:
            if raw not in unmapped:
                unmapped.append(raw)
            continue
        if not rules["genres"][key] and raw not in discarded:
            discarded.append(raw)
        for gid in rules["genres"][key]:
            give(gid, ("genre", raw))
    for raw in tags:
        for gid in rules["tags"].get(norm(raw), []):
            give(gid, ("tag", raw))
    if library:
        for gid in rules["libraries"].get(norm(library), []):
            give(gid, ("library", library))
    for winner, losers in rules["yields"].items():
        if winner in sources:
            for gid in losers:
                if gid in sources and gid != winner:
                    sources.pop(gid)
                    dropped.setdefault(gid, []).append(("yields", winner))
    override = override or {}
    for gid in override.get("remove") or []:
        if gid in sources:
            sources.pop(gid)
            dropped.setdefault(gid, []).append(("removed", ""))
    for gid in override.get("add") or []:
        give(gid, ("added", ""))
    order = [row["id"] for row in rules["vocabulary"]]
    ids = [gid for gid in order if gid in sources]
    return Resolution(ids=ids, sources={gid: sources[gid] for gid in ids}, unmapped=unmapped, discarded=discarded, dropped=dropped)


def fingerprint(rules: dict[str, Any], override: dict[str, Any] | None) -> str:
    """What a title's genres are made of, hashed: for the anime layout's fingerprints."""
    blob = json.dumps([rules, override or {}], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


# -- Keys -----------------------------------------------------------------------------------


def title_key(kind: str, provider_ids: dict[str, Any]) -> str | None:
    """A movie's or series' stable key from its provider ids; None when it has none of them."""
    ids = {str(name).casefold(): str(value) for name, value in (provider_ids or {}).items() if value}
    media = "movie" if kind.casefold() == "movie" else "tv"
    if ids.get("tmdb"):
        return f"tmdb:{media}:{ids['tmdb']}"
    if media == "tv" and ids.get("tvdb"):
        return f"tvdb:tv:{ids['tvdb']}"
    if ids.get("imdb"):
        return f"imdb:{ids['imdb']}"
    return None


def anime_key(cid: int) -> str:
    return f"anime:{cid}"


# -- Stores ---------------------------------------------------------------------------------


def _path(name: str) -> Path:
    return bgjobs.jobs_root().parent / name


def _read(name: str) -> dict[str, Any]:
    try:
        raw = json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(name: str, data: dict[str, Any]) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def rules() -> dict[str, Any]:
    """The rules in force: genre_rules.json, or the defaults until it is first saved."""
    raw = _read("genre_rules.json")
    if not raw:
        return check(default_rules())
    try:
        return check(raw)
    except RulesError:
        return check(default_rules())


def save_rules(raw: dict[str, Any]) -> dict[str, Any]:
    clean = check(raw)
    with _LOCK:
        _write("genre_rules.json", clean)
    return clean


def overrides() -> dict[str, dict[str, list[str]]]:
    return {key: value for key, value in _read("genre_overrides.json").items() if isinstance(value, dict)}


def save_override(key: str, add: Iterable[str], remove: Iterable[str]) -> dict[str, list[str]]:
    """One title's hand choices; both empty forgets them."""
    known = {row["id"] for row in rules()["vocabulary"]}
    add, remove = list(dict.fromkeys(add)), list(dict.fromkeys(remove))
    unknown = [gid for gid in [*add, *remove] if gid not in known]
    if unknown:
        raise RulesError(f"no such genre: {', '.join(unknown)}")
    if set(add) & set(remove):
        raise RulesError("a genre cannot be added and removed at once")
    with _LOCK:
        rows = overrides()
        if add or remove:
            rows[key] = {"add": add, "remove": remove}
        else:
            rows.pop(key, None)
        _write("genre_overrides.json", rows)
    return {"add": add, "remove": remove}


def sources() -> dict[str, dict[str, Any]]:
    """Each title's raw genres, tags and provider ids as Jellyfin had them before bankai wrote it."""
    return _read("genre_sources.json")


def keep_sources(rows: dict[str, dict[str, Any]], written: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Take Jellyfin's raw values for every title bankai has not written yet; a written title's
    stay as they were, since its Genres are locked and no longer Jellyfin's."""
    frozen = set(written)
    with _LOCK:
        kept = sources()
        changed = False
        for key, row in rows.items():
            if key in frozen and key in kept:
                continue
            if kept.get(key) != row:
                kept[key] = row
                changed = True
        if changed:
            _write("genre_sources.json", kept)
    return kept


def state() -> dict[str, dict[str, Any]]:
    """What bankai last wrote, per title: for skipping unchanged titles and for undo."""
    return _read("genre_state.json")


def save_state(rows: dict[str, dict[str, Any]]) -> None:
    with _LOCK:
        _write("genre_state.json", rows)
