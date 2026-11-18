"""German-dubbed films and shows found on the indexers, waiting for a word: Movies & Shows' review.

The anime side has its automation and its review; Movies & Shows had only searching by hand and the
dub pipeline (a stream's German track synced onto an English release, often needing its delay set by
hand). This pass asks Prowlarr for releases that carry German audio themselves ("German DL",
"German AC3" -- `pipeline.german_audio_named`, never "German Subbed"), matches each to TMDB by the
title and year its name gives, and keeps it for review. Nothing is downloaded until the user says so
(Download: a direct download into the library's German folder). A release no TMDB title answers for
waits under Unmatched, to be linked by hand, downloaded or removed. A film the library has in German
already is left out; a show's season says how much of it the library has.

State: `mas_releases.json` -- releases by id (the info hash, else a hash of name and size), each with its
status (review, queued, discarded), and `matches`: the TMDB title each guessed name was matched to
(or none), so a name is looked up once.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()
# Searches a pass asks Prowlarr, per kind: the indexers return their newest matches first.
QUERIES = ("German DL", "German")
# A release found once and not seen for this long, still waiting, is dropped (gone from the indexers).
_FORGET_SECONDS = 30 * 86400
# Fewer and it cannot download: German releases on public indexers are thinly seeded.
MIN_SEEDERS = 1
# AvistaZ's newest uploads read per kind and pass, 50 a page (one request every 6 s there).
AVISTAZ_PAGES = 3
# Below 1080p is not offered, nor a name that says no resolution (DVDRip, XviD: SD almost always).
MIN_HEIGHT = 1080


def sharp(title: str) -> bool:
    from bankai.web import direct

    found = direct.resolution(title) or ""
    return found[:-1].isdigit() and int(found[:-1]) >= MIN_HEIGHT
progress: dict[str, Any] = {}


def _store_path() -> Path:
    return bgjobs.jobs_root().parent / "mas_releases.json"


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw.setdefault("releases", {})
    raw.setdefault("matches", {})
    raw["releases"] = _fold(raw["releases"])
    return raw


def _write(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load() -> dict[str, Any]:
    with _LOCK:
        return _read()


def _same(title: str, size: int | None) -> str:
    """One torrent, whichever indexer lists it and however: its name and size."""
    return hashlib.sha1(f"{title.casefold().strip()}|{size or 0}".encode()).hexdigest()


def _ident(candidate: Any) -> str:
    # Not the link: LimeTorrents' carries a new token every search, and each pass added the
    # release again (one film 24 times, Nov 2026).
    return (candidate.info_hash or "").casefold() or _same(candidate.title, candidate.size_bytes)


# Which of two copies of one release decides what it is: a decision over waiting.
_STATUS_WEIGHT = {"queued": 3, "discarded": 2, "review": 1}


def _fold(releases: dict[str, Any]) -> dict[str, Any]:
    """One row per torrent: rows of one name and size (the same release from several indexers, or
    found again under a new link) made one, under its info hash when one of them has it."""
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for ident, row in releases.items():
        groups.setdefault(_same(row.get("title") or "", row.get("size_bytes")), []).append((ident, row))
    if len(groups) == len(releases):
        return releases
    folded: dict[str, Any] = {}
    for rows in groups.values():
        if len(rows) == 1:
            folded[rows[0][0]] = rows[0][1]
            continue
        keep = max(rows, key=lambda pair: (_STATUS_WEIGHT.get(pair[1].get("status") or "", 0), bool(pair[1].get("info_hash")),
                                           pair[1].get("seeders") or 0))
        row = dict(keep[1])
        row["seeders"] = max(other.get("seeders") or 0 for _, other in rows)
        row["found_at"] = min(other.get("found_at") or row.get("found_at") or 0 for _, other in rows)
        row["seen_at"] = max(other.get("seen_at") or 0 for _, other in rows)
        ident = next((other["info_hash"] for _, other in rows if other.get("info_hash")), None) or _same(row["title"], row.get("size_bytes"))
        row["id"] = ident
        folded[ident] = row
    return folded


def name_key(kind: str, title: str, year: int | None) -> str:
    """What a release is matched by: its kind and the title and year its name gives."""
    words = " ".join(re.findall(r"[a-z0-9]+", title.casefold()))
    return f"{kind}|{words}|{year or ''}"


# Where anime releases come from: a group in brackets, or a streaming site that streams anime.
_ANIME_SOURCES = re.compile(r"^\s*\[[^\]]+\]|\b(?:CR|ADN|Crunchyroll|HIDIVE|Tsundere-Raws)\b", re.I)


def _is_anime(title: str) -> bool:
    """A release the anime side files: from where anime comes, and named as an AniList entry (a special
    too -- "[Fuechslein] Mushoku Tensei - S00E02"). Left to the anime automation."""
    from bankai.metadata import anilist
    from bankai.web import anime

    if _ANIME_SOURCES.search(title) is None:
        return False
    table = anilist.cached_index()
    if table is None:
        return False
    try:
        if anime.anilist_match(title, table=table, mappings={}) is not None:
            return True
        # Several entries for the name (Kaguya-sama's seasons, its film) is anime all the same.
        for name, *_rest in anime.release_readings(title)[:2]:
            found = anilist.resolve_in(table, name)
            if found.anime is not None or found.candidates:
                return True
        return False
    except Exception:  # noqa: BLE001 -- unsure: kept for review
        return False


def _german_in_tail(title: str, guessed: str) -> bool:
    """Whether the German marker is in the release's tags, not its title: "The Good German (2006)"."""
    from bankai.processor.pipeline import german_audio_named

    words = re.findall(r"[a-z0-9]+", title.casefold())
    # With a year, the tags come after it ("The Good German 2006 720p": the title's own word); else
    # after the words of the title the name gives.
    years = [at for at, word in enumerate(words) if re.fullmatch(r"(19|20)\d\d", word) and at > 0]
    own = years[0] + 1 if years else len(re.findall(r"[a-z0-9]+", guessed.casefold()))
    return german_audio_named(" ".join(words[own:]))


def _row(candidate: Any, kind: str) -> dict[str, Any]:
    from bankai.web import direct

    guess = direct.guess(candidate.title)
    return {
        "id": _ident(candidate),
        # Season 0 is a show's specials, a season all the same.
        "kind": "show" if guess.get("season") is not None or kind == "show" else "movie",
        "title": candidate.title,
        "indexer": candidate.indexer,
        "size_bytes": candidate.size_bytes,
        "seeders": candidate.seeders,
        "leechers": candidate.leechers,
        "published": candidate.publish_date,
        "resolution": direct.resolution(candidate.title),
        "info_url": candidate.info_url,
        "download_url": candidate.download_url or None,
        "magnet_uri": candidate.magnet_uri,
        "info_hash": (candidate.info_hash or "").casefold() or None,
        "guess": guess,
    }


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _plain(text: str) -> str:
    """A name compared without case, punctuation or umlauts: scene names write "Verrueckt" for "Verrückt"."""
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold().translate(_UMLAUTS)))


async def _match(kind: str, title: str, year: int | None) -> dict[str, Any] | None:
    """The TMDB title a release's name is: one of TMDB's results whose name (or another of its names,
    the German one too) is the release's, in the year it gives (one either side); None when none is.

    German releases often write the title with its German subtitle ("Bridget Jones Verrueckt nach
    ihm", "Hellraiser Das Schloss zur Hoelle"), which TMDB's search finds nothing for: the name's
    first words are searched then, a result taken only if one of its names is the whole of it, and
    only in the year the release gives.
    """
    from bankai.metadata import tmdb_titles
    from bankai.web import mas_follow

    if not tmdb_titles.configured() or not title.strip():
        return None
    words = title.split()
    queries = [title] + ([" ".join(words[:n]) for n in range(len(words) - 1, 0, -1)][:3] if year and len(words) > 1 else [])
    whole = _plain(title)
    for at, query in enumerate(queries):
        found = await tmdb_titles.get_title_aliases(query, kind="movie" if kind == "movie" else "series", detailed=3)
        for alias in found[:5]:
            names = [name for name in (alias.name, alias.english_title, alias.german_title, *alias.aliases) if name]
            if not (mas_follow.same_title(title, names) or whole in {_plain(name) for name in names}):
                continue
            if year and alias.year and abs(alias.year - year) > 1:
                continue
            if at and not (year and alias.year):
                continue  # matched on part of the name: only with the year to go by
            return {"tmdb_id": alias.tmdb_id, "name": alias.name, "year": alias.year, "poster_url": alias.poster_url,
                    "german_title": alias.german_title}
    return None


def _held(kind: str, match: dict[str, Any], season: int | None) -> dict[str, Any]:
    """What the library has of the title in German: a film's file, or a season's episodes."""
    from bankai.web import direct

    names = tuple(name for name in (match.get("german_title"),) if name)
    held = direct.held(kind, match["name"], match.get("year"), names)
    german = held.get("German") or {}
    if kind == "movie":
        return {"german": bool(german)}
    episodes = sorted(episode for (number, episode) in german if season is None or number == season)
    return {"german_episodes": len(episodes)}


_LIBRARY: list[Any] = [None, None]  # (library_walk generation, index)


def _library() -> dict[tuple[str, str], dict[str, set[tuple[int, int]]]]:
    """What the Movies & Shows roots hold, from the library's held tree (no read of the disk: the review must
    not wait on G: while torrents write to it): per (library, folder key) the German and English (season,
    episode)s -- (0, 0) a film. A file in an English folder carrying German counts as German."""
    from bankai.web import direct, library_walk

    generation = library_walk.generation()
    if _LIBRARY[0] == generation and _LIBRARY[1] is not None:
        return _LIBRARY[1]
    index: dict[tuple[str, str], dict[str, set[tuple[int, int]]]] = {}
    for target in direct.targets():
        if target["library"] not in ("movies", "shows") or not target["exists"]:
            continue
        for row in library_walk.files([target["root"]]):
            if re.search(r"\bsample\b", row["name"], re.I) or row["name"].startswith("."):
                continue
            folder = direct._key(re.sub(r"\s*\((19|20)\d\d\)\s*$", "", row["series"]))
            if target["library"] == "movies":
                keys = {(0, 0)}
            else:
                numbers = direct.file_episode(row["name"])
                if numbers is None:
                    continue
                season, first, last = numbers
                keys = {(season, episode) for episode in range(first, (last or first) + 1)}
            language = target["language"]
            if language == "English" and direct.has_german(Path(row["path"]), probe=False):
                language = "German"
            index.setdefault((target["library"], folder), {"German": set(), "English": set()})[language] |= keys
    _LIBRARY[:] = [generation, index]
    return index


def _owned(index: dict, kind: str, match: dict[str, Any], guess: dict[str, Any], title: str = "") -> bool:
    """Whether the library has this release's film, episode or whole season in German already."""
    from bankai.web import direct

    library = "movies" if kind == "movie" else "shows"
    german: set[tuple[int, int]] = set()
    for name in (match.get("name"), match.get("german_title")):
        if name:
            german |= (index.get((library, _key(name))) or {}).get("German", set())
    if kind == "movie":
        return (0, 0) in german
    season, episode = guess.get("season"), guess.get("episode")
    numbers = direct.file_episode(title) if title and episode is None else None
    if numbers is not None and numbers[0] == season:
        episode = numbers[1]
    if season is None:
        return False
    if episode is not None:
        return (int(season), int(episode)) in german
    total = (match.get("seasons") or {}).get(str(season))
    return bool(total) and sum(1 for number, _ in german if number == int(season)) >= int(total)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def anime_show(record: dict) -> bool:
    """A show TMDB files as animation from Japan: anime, the anime library's (AniList's) to handle."""
    genres = {int(row["id"]) for row in record.get("genres") or [] if str(row.get("id") or "").isdigit()}
    japanese = record.get("original_language") == "ja" or "JP" in (record.get("origin_country") or [])
    return _ANIMATION in genres and japanese


_ANIMATION = 16  # TMDB's genre id


async def _show_facts(tmdb_id: int) -> dict[str, Any]:
    """Episodes per season, and whether the show is anime; nothing when TMDB did not answer."""
    from bankai.metadata import tmdb_titles

    try:
        record = await tmdb_titles.details("tv", tmdb_id) or {}
    except Exception:  # noqa: BLE001 -- asked again on the next pass
        return {}
    if not record:
        return {}
    seasons = {str(row["season_number"]): int(row.get("episode_count") or 0)
               for row in record.get("seasons") or [] if isinstance(row.get("season_number"), int)}
    return {"seasons": seasons, "anime": anime_show(record)}


def _for_anime(row: dict[str, Any], match: dict[str, Any] | None) -> bool:
    """A show the anime library handles: its German releases are not offered here (films are)."""
    return row.get("kind") == "show" and bool((match or {}).get("anime"))


async def discover() -> dict[str, int]:
    """One pass: the newest German releases of both kinds, matched and kept for review."""
    from bankai.processor.pipeline import german_audio_named
    from bankai.torrent.prowlarr import ProwlarrClient
    from bankai.torrent.worker import _CAT_MOVIES, _CAT_TV

    seen: dict[str, dict[str, Any]] = {}
    prowlarr = ProwlarrClient()
    try:
        for kind, categories in (("movie", _CAT_MOVIES), ("show", _CAT_TV)):
            for query in QUERIES:
                progress.update(step=f"Searching {query} ({'films' if kind == 'movie' else 'shows'})")
                try:
                    candidates = await prowlarr.search(query, categories=categories)
                except Exception as exc:  # noqa: BLE001 -- the other searches go on
                    log.warning("German release search %r failed: %r", query, exc)
                    continue
                for candidate in candidates:
                    if candidate.seeders < MIN_SEEDERS or not german_audio_named(candidate.title) or not sharp(candidate.title):
                        continue
                    row = _row(candidate, kind)
                    if not _german_in_tail(candidate.title, row["guess"]["title"]):
                        continue
                    # The same torrent from another indexer: the best seeded listing.
                    if row["id"] not in seen or (row["seeders"] or 0) > (seen[row["id"]]["seeders"] or 0):
                        seen[row["id"]] = row
        # AvistaZ (not in Prowlarr), when set up: its newest films and shows whose audio, as the
        # tracker lists it, has German -- its names seldom say so.
        from bankai.web import avistaz

        for kind in ("movie", "show"):
            progress.update(step=f"Reading AvistaZ's newest {'films' if kind == 'movie' else 'shows'}")
            try:
                candidates = await avistaz.mas_search("", kind, pages=AVISTAZ_PAGES)
            except Exception as exc:  # noqa: BLE001 -- the rest of the pass goes on
                log.warning("AvistaZ's newest %ss could not be read: %r", kind, exc)
                continue
            for candidate in candidates:
                if candidate.seeders < MIN_SEEDERS or "de" not in candidate.audio_langs or not sharp(candidate.title):
                    continue
                row = {**_row(candidate, kind), "german_audio": True}
                if row["id"] not in seen or (row["seeders"] or 0) > (seen[row["id"]]["seeders"] or 0):
                    seen[row["id"]] = row
        # Uploaders followed (web.uploaders): their uploads whose MediaInfo has a German audio
        # track, read by its own pass -- names that say only "English", or nothing (QxR).
        from bankai.web import direct as direct_mod
        from bankai.web import uploaders

        for candidate in await asyncio.to_thread(uploaders.german_candidates):
            if candidate.seeders < MIN_SEEDERS or not sharp(candidate.title):
                continue
            guessed = direct_mod.guess(candidate.title)
            row = {**_row(candidate, "show" if guessed.get("season") is not None else "movie"), "german_audio": True}
            if row["id"] not in seen or (row["seeders"] or 0) > (seen[row["id"]]["seeders"] or 0):
                seen[row["id"]] = row
    finally:
        await prowlarr.aclose()
        progress.clear()

    # Anime is the anime automation's; read off the event loop (the title index).
    anime = set(await asyncio.to_thread(lambda: {ident for ident, row in seen.items() if _is_anime(row["title"])}))
    with _LOCK:
        data = _read()
    now = time.time()
    added = matched = 0
    for ident, row in seen.items():
        if ident in anime:
            continue
        kept = data["releases"].get(ident)
        if kept is not None:
            # Known: its seeders and the like brought up to date, its status left as it is.
            kept.update({key: row[key] for key in ("seeders", "leechers", "size_bytes", "info_url", "download_url", "magnet_uri")})
            kept["seen_at"] = now
            continue
        guess = row["guess"]
        key = name_key(row["kind"], guess["title"], guess.get("year"))
        if key not in data["matches"]:
            try:
                data["matches"][key] = await _match(row["kind"], guess["title"], guess.get("year"))
            except Exception as exc:  # noqa: BLE001 -- asked again on the next pass
                log.info("TMDB match for %s failed: %r", guess["title"], exc)
                continue
        match = data["matches"][key]
        if match is not None and row["kind"] == "show" and ("seasons" not in match or "anime" not in match):
            # Episodes per season, so a pack of a season the library has whole is not offered.
            match.update(await _show_facts(int(match["tmdb_id"])))
        held: dict[str, Any] = {}
        if match is not None:
            matched += 1
            try:
                held = await asyncio.to_thread(_held, row["kind"], match, guess.get("season"))
            except Exception:  # noqa: BLE001 -- a root not reachable: kept without it
                held = {}
            # A film the library has in German already is no news.
            if row["kind"] == "movie" and held.get("german"):
                continue
        data["releases"][ident] = {**row, "key": key, "held": held, "status": "review", "found_at": now, "seen_at": now}
        added += 1
    # Shows matched before their seasons (or whether they are anime) were kept: asked once.
    for key, match in data["matches"].items():
        if key.startswith("show|") and match is not None and ("seasons" not in match or "anime" not in match):
            match.update(await _show_facts(int(match["tmdb_id"])))
    # Waiting releases the indexers no longer list go, and those a better reading no longer takes for German.
    for ident, row in list(data["releases"].items()):
        # A tracker's own word for German audio (AvistaZ) stands where the name says nothing.
        named = row.get("german_audio") or german_audio_named(row["title"])
        if row.get("status") == "review" and (now - float(row.get("seen_at") or 0) > _FORGET_SECONDS
                                              or not named or not sharp(row["title"])):
            data["releases"].pop(ident)
    with _LOCK:
        fresh = _read()
        fresh["matches"].update(data["matches"])
        for ident in [ident for ident, row in fresh["releases"].items() if ident not in data["releases"] and row.get("status") == "review"]:
            fresh["releases"].pop(ident)
        for ident, row in data["releases"].items():
            old = fresh["releases"].get(ident)
            # A decision made meanwhile (downloaded, removed, linked) is kept.
            fresh["releases"][ident] = {**row, **({k: old[k] for k in ("status", "key", "job_id") if k in old} if old else {})}
        _write(fresh)
    return {"found": len(seen), "added": added, "matched": matched, "anime": len(anime)}


def review() -> dict[str, Any]:
    """What waits: a card per title (TMDB's), the releases of it under it; and the unmatched releases."""
    data = load()
    try:
        index = _library()
    except Exception:  # noqa: BLE001 -- a root not reachable: shown without it
        index = {}
    cards: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    for ident, row in data["releases"].items():
        # Downloaded ones are the queue's now.
        if row.get("status") != "review" or not sharp(row["title"]):
            continue
        match = data["matches"].get(row.get("key") or "")
        if _for_anime(row, match):
            continue
        # What the library has in German since it was found (downloaded from here, or otherwise).
        if match is not None and _owned(index, row["kind"], match, row.get("guess") or {}, row["title"]):
            continue
        release = {key: row.get(key) for key in (
            "id", "title", "indexer", "size_bytes", "seeders", "leechers", "published", "resolution", "info_url",
            "status", "found_at",
        )}
        release["season"] = (row.get("guess") or {}).get("season")
        release["guess"] = row.get("guess")
        # As the pass found it: a show's season, how many of its episodes the library has in German.
        release["held"] = row.get("held") or {}
        if match is None:
            unmatched.append({**release, "kind": row["kind"]})
            continue
        card = cards.setdefault(f"{row['kind']}:{match['tmdb_id']}", {
            "key": f"{row['kind']}:{match['tmdb_id']}", "kind": row["kind"], **match, "releases": [],
        })
        card["releases"].append(release)
    for card in cards.values():
        card["releases"].sort(key=lambda release: (-(release.get("seeders") or 0), release["title"]))
        card["found_at"] = max(release.get("found_at") or 0 for release in card["releases"])
    unmatched.sort(key=lambda release: -(release.get("found_at") or 0))
    return {
        "titles": sorted(cards.values(), key=lambda card: -card["found_at"]),
        "unmatched": unmatched,
    }


def review_count() -> int:
    data = load()
    return sum(
        1
        for row in data["releases"].values()
        if row.get("status") == "review" and sharp(row["title"]) and not _for_anime(row, data["matches"].get(row.get("key") or ""))
    )


def _set(ids: list[str], **changes: Any) -> int:
    with _LOCK:
        data = _read()
        done = 0
        for ident in ids:
            if ident in data["releases"]:
                data["releases"][ident].update(changes)
                done += 1
        _write(data)
    return done


def discard(ids: list[str]) -> int:
    """Removed from the review: never offered again (the pass keeps it, as discarded)."""
    return _set(ids, status="discarded")


def link(ids: list[str], kind: str, match: dict[str, Any]) -> int:
    """These releases are this TMDB title: their name is matched to it from now on."""
    with _LOCK:
        data = _read()
        done = 0
        for ident in ids:
            row = data["releases"].get(ident)
            if row is None:
                continue
            guess = row.get("guess") or {}
            key = name_key(kind, guess.get("title") or row["title"], guess.get("year"))
            row.update(kind=kind, key=key)
            data["matches"][key] = match
            done += 1
        _write(data)
    return done


def release(ident: str) -> dict[str, Any] | None:
    row = load()["releases"].get(ident)
    if row is None:
        return None
    return {**row, "match": load()["matches"].get(row.get("key") or "")}


def mark_queued(ident: str, job_id: str | None) -> None:
    _set([ident], status="queued", job_id=job_id)
