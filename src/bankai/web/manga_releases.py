"""Manga and light novel releases: found on nyaa, matched to AniList, downloaded only once approved.

Anime's held releases, with nothing automatic at the end:

1. ``discover`` (pass "Look for manga"): the newest uploads in nyaa's English
   literature category, and a few followed series (see manga_series) searched
   by name, each every ``manga.search_interval_days``. A release's name is
   read (manga_names) and matched to a followed series by its titles; one that
   matches none is not kept. Audiobooks are skipped, and so is a release
   whose every volume and chapter the library has ("owned").
2. Review: every match waits as "review", grouped by series, for its match to
   be confirmed or changed, then Download (approve) or Discard -- never
   offered again. Blacklisting the series discards all of its releases and
   stops looking for it.
3. ``reconcile`` (pass "Manga downloads"): an approved release goes to
   qBittorrent under ``manga.qbittorrent_category``. Once qBittorrent knows
   the torrent's files, those the library does not need (``wanted``) are left
   out of the download; once complete, the books that fill a gap are filed
   into the series' folder under the library's names ("Vol 01.cbz"), the
   torrent removed with the rest of its files, and OpenPanel told to scan.

State in ``manga_releases.json``: ``{releases: {info_hash: row}, searched: {aid: ts}}``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import anilist
from bankai.metadata import anilist_manga as manga_meta
from bankai.processor.naming import sanitise
from bankai.web import anime as nyaa
from bankai.web import manga_contents, manga_library, manga_names, manga_series, openpanel

log = get_logger(__name__)

REVIEW = "review"
QUEUED = "queued"
DOWNLOADING = "downloading"
PUBLISHING = "publishing"
DONE = "done"
FAILED = "failed"
DISCARDED = "discarded"
OWNED = "owned"
ACTIVE = frozenset({QUEUED, DOWNLOADING, PUBLISHING})

_NYAA = "https://nyaa.si"
CATEGORY = "3_1"  # Literature - English-translated
_BLACKLISTED = "series blacklisted"
_HAVE_IT = "the library has all of it"
# Why a book of a download stays out of the library.
_HAS = "the library has it"
_HAS_CHAPTERS = "the library has its chapters"
_MOVED = "moved to another series"
_IN_BETWEEN = "in between, outside the count"
_NO_NUMBER = "no volume or chapter in its name"
_DEAD = "nobody seeds it"
# A release nobody has seeded for this long is not offered: it cannot be downloaded.
_DEAD_AFTER = 30 * 86400
_LOCK = threading.Lock()
# What the passes are at, for the dashboard: {"step", "series"}.
progress: dict[str, Any] = {}
# A discovery running now: the pass and the page's "Look now" never overlap.
_discovering = threading.Event()


def _path() -> Path:
    return bgjobs.jobs_root().parent / "manga_releases.json"


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    raw.setdefault("releases", {})
    raw.setdefault("searched", {})
    return raw


def _write(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def releases() -> dict[str, dict[str, Any]]:
    return _read()["releases"]


def has_work() -> bool:
    """Approved releases not filed yet: what the downloads pass follows."""
    return any(row.get("status") in (QUEUED, DOWNLOADING) for row in releases().values())


def _change(info_hash: str, **changes: Any) -> dict[str, Any]:
    with _LOCK:
        state = _read()
        row = state["releases"].get(info_hash)
        if row is None:
            raise KeyError(info_hash)
        row.update(changes, updated_at=time.time())
        _write(state)
        return row


# -- Matching ----------------------------------------------------------------------


def _keys(value: str) -> set[str]:
    return {key for key in (anilist.normalise(value), anilist.loose(value), anilist.compact(value)) if key}


def title_index(anilist_ids: Any) -> dict[str, set[int]]:
    """Every title of these series, as matching keys: {key: {aid}}."""
    index: dict[str, set[int]] = {}
    for aid in anilist_ids:
        for title in manga_meta.titles(aid):
            for key in _keys(title):
                index.setdefault(key, set()).add(int(aid))
    return index


def _is_novel(anilist_id: int) -> bool:
    return manga_meta.kind_of(manga_meta.media(anilist_id).get("format")) == "novel"


def match(
    release: manga_names.ReleaseName, size_bytes: int, index: dict[str, set[int]], *, searched: int | None = None
) -> tuple[int | None, bool]:
    """The series a release is, and whether that is only a guess.

    A name that is one of a series' titles decides; where it is two series'
    (a manga and its light novel share their title), what the release says
    or weighs decides, then the series searched for. Searched for, a name
    that is the start of the series' title ("Frieren" for "Frieren - Beyond
    Journey's End") is a guess; one that goes on past it ("Oshi no Ko ...
    Guidebook") is something else. A release whose tags say novel is no manga.
    """
    found: set[int] = set()
    for name in release.names:
        for key in _keys(name):
            found |= index.get(key, set())
    if release.tagged_novel:
        found = {aid for aid in found if _is_novel(aid)}
    novel = manga_names.novel(release, size_bytes)
    if len(found) > 1 and novel is not None:
        found = {aid for aid in found if _is_novel(aid) == novel} or found
    if len(found) > 1 and searched in found:
        found = {searched}
    if len(found) == 1:
        return next(iter(found)), False
    if found:
        # Still two: the manga first, the user decides.
        return sorted(found, key=lambda aid: (_is_novel(aid), aid))[0], True
    if searched is not None and (_is_novel(searched) or not release.tagged_novel):
        titles = [anilist.normalise(title) for title in manga_meta.titles(searched)]
        for name in release.names:
            key = anilist.normalise(name)
            if key and any(title.startswith(f"{key} ") for title in titles):
                return searched, True
    return None, False


def _numbers(span: list[float] | tuple[float, float] | None) -> set[float]:
    if not span:
        return set()
    low, high = float(span[0]), float(span[1])
    if low != int(low) or high != int(high):
        return {low, high}
    return {float(value) for value in range(int(low), int(high) + 1)}


Owned = tuple[set[float], set[float]]


def complete(anilist_id: int, owned: Owned) -> bool:
    """A finished series whose every volume the folder has: its chapters are all in them."""
    entry = manga_meta.media(anilist_id)
    total = entry.get("volumes")
    return entry.get("status") == "FINISHED" and bool(total) and bool(owned[0]) and max(owned[0]) >= float(total)


def reaches() -> dict[int, tuple[int, int]]:
    """The highest chapter and volume the releases found of each series name."""
    found: dict[int, tuple[int, int]] = {}
    for row in releases().values():
        aid = int(row.get("anilist_id") or 0)
        chapter, volume = found.get(aid, (0, 0))
        if row.get("chapters"):
            chapter = max(chapter, int(row["chapters"][1]))
        if row.get("volumes"):
            volume = max(volume, int(row["volumes"][1]))
        found[aid] = (chapter, volume)
    return found


def reach(anilist_id: int) -> tuple[int, int]:
    """The highest chapter and volume the releases found of a series name."""
    return reaches().get(int(anilist_id), (0, 0))


def holdings(anilist_id: int) -> manga_contents.Holdings:
    """Which chapters a series' folder holds, and how sure that is (manga_contents.holdings)."""
    aid = int(anilist_id)
    entry = manga_meta.media(aid)
    return manga_contents.holdings(
        manga_library.folder_of(aid),
        complete=complete(aid, manga_library.owned(aid)),
        whole_series=max(int(entry.get("chapters") or 0), reach(aid)[0]),
    )


def adds(row: dict[str, Any], found: manga_contents.Holdings | None = None) -> Owned | None:
    """The volumes and whole chapters a release would add to its series' folder: volumes it has
    no book of, chapters it lacks (``Holdings.lacks``), neither moved from it to another
    series; None when it cannot say -- the name gives neither, and the series is not complete."""
    aid = int(row["anilist_id"])
    found = found if found is not None else holdings(aid)
    if not row.get("volumes") and not row.get("chapters"):
        return (set(), set()) if found.complete else None
    away = {manga_names.file_number(name) for name in manga_library.moved_away(aid)}
    volumes = {volume for volume in _numbers(row.get("volumes")) - found.volumes() if ("volume", volume) not in away}
    if found.complete:
        # A finished series has no volume past its last: such a name is another series'.
        volumes = {volume for volume in volumes if volume <= float(manga_meta.media(aid).get("volumes") or 0)}
    chapters = {
        chapter for chapter in _numbers(row.get("chapters"))
        if chapter == int(chapter) and found.lacks(int(chapter)) and ("chapter", chapter) not in away
    }
    return volumes, chapters


def library_name(filename: str) -> str:
    """The name a book gets in the library: "Vol 01.cbz", "Chapter 0138.cbz", else its own."""
    number = manga_names.file_number(filename)
    suffix = Path(filename).suffix
    if number:
        return manga_names.target_name(number[0], number[1], suffix)
    return sanitise(Path(filename).stem) + manga_names.library_extension(suffix)


def wanted(anilist_id: int, names: list[str], *, pages: dict[str, list[float]] | None = None) -> dict[str, str | None]:
    """Each of a download's books: None when it goes into the library, else why it stays out.

    Only what fills a gap goes in: a volume the folder has no book of -- unless
    its pages (``pages``, read once downloaded) name only chapters the library
    holds -- and a chapter the library lacks (``Holdings.lacks``).
    Out besides: a book of a name the folder has, one moved from the folder to
    another series, an in-between number, a second copy, and a book without a
    number beside numbered ones; a release of books without numbers (a one-shot)
    goes in whole.
    """
    aid = int(anilist_id)
    found = holdings(aid)
    present = set(found.numbers)
    volumes = found.volumes()
    # What goes in from this download, for the books after it.
    taken: set[int] = set()
    away = manga_library.moved_away(aid)
    numbers = {name: manga_names.file_number(name) for name in names}
    numbered = any(numbers.values())
    verdicts: dict[str, str | None] = {}
    # Volumes first: the chapters their pages name are no gap for the chapter files after.
    for name in sorted(names, key=lambda name: (numbers[name] or ("",))[0] != "volume"):
        number = numbers[name]
        target = library_name(name)
        if target in present:
            verdicts[name] = _HAS
        elif target in away:
            verdicts[name] = _MOVED
        elif number is None:
            verdicts[name] = _NO_NUMBER if numbered else None
        elif number[1] != int(number[1]):
            verdicts[name] = _IN_BETWEEN
        elif number[0] == "volume":
            named = set(manga_contents.own_chapters({int(chapter) for chapter in (pages or {}).get(name, []) if chapter == int(chapter)}))
            if number[1] in volumes:
                verdicts[name] = _HAS
            elif named and not any(found.lacks(chapter) and chapter not in taken for chapter in named):
                verdicts[name] = _HAS_CHAPTERS
            else:
                verdicts[name] = None
                volumes.add(number[1])
                taken |= named
        elif int(number[1]) in taken or not found.lacks(int(number[1])):
            verdicts[name] = _HAS
        else:
            verdicts[name] = None
            taken.add(int(number[1]))
    return verdicts


def dead(entry: dict[str, Any]) -> bool:
    """Nobody seeds it, and has not for a month: it cannot be downloaded."""
    if int(entry.get("seeders") or 0) > 0:
        return False
    try:
        published = parsedate_to_datetime(str(entry.get("published_at") or "")).timestamp()
    except (TypeError, ValueError):
        return False
    return time.time() - published > _DEAD_AFTER


# -- Finding releases -----------------------------------------------------------------


async def feed(client: httpx.AsyncClient, query: str = "") -> list[nyaa.NyaaEntry]:
    params = {"page": "rss", "c": CATEGORY, "f": "0"}
    if query:
        params |= {"q": query, "s": "seeders", "o": "desc"}
    response = await client.get("/", params=params)
    response.raise_for_status()
    return nyaa.parse_rss(response.text)


def queries(anilist_id: int) -> list[str]:
    """What a series is searched for by: its English and romaji titles up to a subtitle.

    nyaa wants every word of the search in the name, and releases write the
    subtitle as they please ("Mushoku Tensei - Jobless Reincarnation" for
    "Mushoku Tensei: Isekai Ittara Honki Dasu"): the part before it finds them
    all, and the matching sorts them out.
    """
    entry = manga_meta.media(anilist_id)
    found = []
    for title in (entry.get("english"), entry.get("romaji")):
        if not title:
            continue
        head = str(title).split(":")[0].split(" - ")[0]
        head = " ".join(word for word in head.replace("[", " ").replace("]", " ").split())
        if head and head.casefold() not in {query.casefold() for query in found}:
            found.append(head)
    return found


def _row(entry: nyaa.NyaaEntry, release: manga_names.ReleaseName, anilist_id: int, *, guess: bool, how: str) -> dict:
    kept = dataclasses.asdict(entry)
    # Not shown anywhere, and the longest part of a row.
    kept.pop("description", None)
    return {
        "status": REVIEW,
        "entry": kept,
        "anilist_id": anilist_id,
        "kind": manga_meta.kind_of(manga_meta.media(anilist_id).get("format")),
        "guess": guess,
        "how": how,
        "group": release.group,
        "volumes": list(release.volumes) if release.volumes else None,
        "chapters": list(release.chapters) if release.chapters else None,
        "found_at": time.time(),
        "updated_at": time.time(),
    }


def consider(
    entries: list[nyaa.NyaaEntry],
    index: dict[str, set[int]],
    followed: dict[int, list[str]],
    known: set[str],
    *,
    searched: int | None = None,
) -> dict[str, dict[str, Any]]:
    """The releases among ``entries`` worth keeping: new, matched to a followed series."""
    novels = get_settings().manga.novels
    kept: dict[str, dict[str, Any]] = {}
    owned: dict[int, manga_contents.Holdings] = {}
    for entry in entries:
        if entry.info_hash in known or entry.info_hash in kept or entry.category_id not in ("", CATEGORY):
            continue
        release = manga_names.parse(entry.title)
        if release.audiobook or dead(dataclasses.asdict(entry)):
            continue
        aid, guess = match(release, entry.size_bytes, index, searched=searched)
        if aid is None or aid not in followed or (not novels and _is_novel(aid)):
            continue
        row = _row(entry, release, aid, guess=guess, how="search" if searched else "feed")
        if aid not in owned:
            owned[aid] = holdings(aid)
        new = adds(row, owned[aid])
        if new is not None and not new[0] and not new[1]:
            row |= {"status": OWNED, "reason": _HAVE_IT}
        kept[entry.info_hash] = row
    return kept


def _due(state: dict[str, Any], followed: dict[int, list[str]]) -> list[int]:
    """The followed series to search this pass: never searched first, then the longest ago."""
    settings = get_settings().manga
    now = time.time()
    searched = state.get("searched") or {}
    due = [
        aid for aid in followed
        if now - float(searched.get(str(aid)) or 0) >= settings.search_interval_days * 86400
    ]
    due.sort(key=lambda aid: float(searched.get(str(aid)) or 0))
    return due[: settings.searches_per_pass]


def discovering() -> bool:
    return _discovering.is_set()


async def discover() -> dict[str, Any]:
    """One pass: the newest uploads, and the series due for a search."""
    settings = get_settings().manga
    if not settings.enabled:
        return {"skipped": "off"}
    if _discovering.is_set():
        return {"skipped": "already looking"}
    _discovering.set()
    try:
        return await _discover(settings)
    finally:
        _discovering.clear()
        progress.clear()


async def _discover(settings: Any) -> dict[str, Any]:
    progress.update(step="series")
    await manga_library.adopt()
    from bankai.web import manga_views

    # The chapters in the library's volumes, read a few hundred archives a pass.
    await asyncio.to_thread(manga_views.warm)
    followed = manga_series.followed()
    if not followed:
        progress.clear()
        return {"followed": 0}
    await manga_meta.ensure(list(followed), max_batches=4)
    index = title_index(followed)
    state = _read()
    known = set(state["releases"])
    found: dict[str, dict[str, Any]] = {}
    searched: dict[str, float] = {}
    error = None
    headers = {"User-Agent": get_settings().scraper.user_agent}
    try:
        async with httpx.AsyncClient(base_url=_NYAA, headers=headers, timeout=30, follow_redirects=True) as client:
            progress.update(step="feed")
            found |= consider(await feed(client), index, followed, known)
            for aid in _due(state, followed):
                progress.update(step="search", series=manga_meta.name(aid))
                for query in queries(aid):
                    await asyncio.sleep(settings.request_delay_seconds)
                    found |= consider(await feed(client, query), index, followed, known | set(found), searched=aid)
                searched[str(aid)] = time.time()
    # An answer that is not RSS raises ElementTree's ParseError, a SyntaxError.
    except (httpx.HTTPError, ValueError, SyntaxError) as exc:
        error = str(exc) or type(exc).__name__
        log.warning("[manga] nyaa could not be read: %s", error)
    finally:
        progress.clear()
    with _LOCK:
        state = _read()
        for info_hash, row in found.items():
            state["releases"].setdefault(info_hash, row)
        tidy(state)
        state["searched"].update(searched)
        state["last_poll"] = time.time()
        state["last_found"] = sum(1 for row in found.values() if row["status"] == REVIEW)
        state["last_error"] = error
        _write(state)
    offered = sum(1 for row in found.values() if row["status"] == REVIEW)
    if offered:
        log.info("[manga] %d new release(s) to review", offered)
    return {"followed": len(followed), "found": offered, "searched": len(searched), "error": error}


def tidy(state: dict[str, Any]) -> int:
    """Waiting releases the library has meanwhile, or nobody seeds any longer: out of the review.
    Every release's name is read again first, so a better reading reaches those found before it;
    a waiting one it no longer matches -- a novel by its tags, under a manga -- goes, as if never
    found."""
    owned: dict[int, manga_contents.Holdings] = {}
    changed = 0
    for info_hash, row in list(state["releases"].items()):
        if title := (row.get("entry") or {}).get("title"):
            release = manga_names.parse(title)
            if row.get("status") == REVIEW and row.get("how") != "hand" and release.tagged_novel and not _is_novel(int(row["anilist_id"])):
                del state["releases"][info_hash]
                changed += 1
                continue
            numbers = {
                "volumes": list(release.volumes) if release.volumes else None,
                "chapters": list(release.chapters) if release.chapters else None,
            }
            if any(row.get(key) != value for key, value in numbers.items()):
                row.update(numbers)
                changed += 1
        if row.get("status") != REVIEW:
            continue
        aid = int(row["anilist_id"])
        if aid not in owned:
            owned[aid] = holdings(aid)
        new = adds(row, owned[aid])
        if new is not None and not new[0] and not new[1]:
            row.update(status=OWNED, reason=_HAVE_IT, updated_at=time.time())
            changed += 1
        elif dead(row.get("entry") or {}):
            row.update(status=DISCARDED, reason=_DEAD, updated_at=time.time())
            changed += 1
    return changed


# -- Review -------------------------------------------------------------------------------


def _rank(view: dict[str, Any]) -> tuple:
    adding = bool(view.get("adds")) or view.get("adds") is None
    volumes = view["volumes"][1] - view["volumes"][0] + 1 if view.get("volumes") else 0
    return (not adding, -volumes, not view["trusted"], -view["seeders"], -view["found_at"])


def release_view(info_hash: str, row: dict[str, Any], found: manga_contents.Holdings | None = None) -> dict[str, Any]:
    entry = row.get("entry") or {}
    new = adds(row, found) if row.get("status") == REVIEW else None
    return {
        "info_hash": info_hash,
        "title": entry.get("title"),
        "size": entry.get("size"),
        "size_bytes": entry.get("size_bytes") or 0,
        "seeders": entry.get("seeders") or 0,
        "leechers": entry.get("leechers") or 0,
        "downloads": entry.get("downloads") or 0,
        "trusted": bool(entry.get("trusted")),
        "remake": bool(entry.get("remake")),
        "published_at": entry.get("published_at"),
        "detail_url": entry.get("detail_url"),
        "group": row.get("group"),
        "volumes": row.get("volumes"),
        "chapters": row.get("chapters"),
        "kind": row.get("kind"),
        "guess": bool(row.get("guess")),
        "how": row.get("how"),
        "status": row.get("status"),
        "reason": row.get("reason"),
        "error": row.get("error"),
        "progress": row.get("progress"),
        "adds": None if new is None else manga_names.describe(*new),
        "found_at": float(row.get("found_at") or 0),
        "copied": row.get("copied"),
        "left": row.get("left"),
    }


def anime_names(anilist_id: int, links: dict[int, list[tuple[int, str, str | None]]] | None = None) -> list[dict]:
    """The anime on disk a series is linked to, as {anilist_id, name, relation}."""
    rows = (links if links is not None else manga_series.anime_links()).get(int(anilist_id), [])
    seen: dict[int, dict] = {}
    for anime_id, relation, _format in rows:
        seen.setdefault(anime_id, {"anilist_id": anime_id, "name": anilist.name(anime_id), "relation": relation})
    return list(seen.values())


def series_card(anilist_id: int, *, followed: dict[int, list[str]], links: dict) -> dict[str, Any]:
    volumes, chapters = manga_library.owned(anilist_id)
    return {
        **manga_meta.as_choice(anilist_id),
        "why": followed.get(anilist_id, []),
        "anime": anime_names(anilist_id, links),
        "owned": manga_names.describe(volumes, chapters),
        "folder": (manga_library.series().get(anilist_id) or {}).get("folder"),
    }


def review() -> list[dict[str, Any]]:
    """The releases waiting for a decision, a card per series, the newest found first."""
    groups: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for info_hash, row in releases().items():
        if row.get("status") == REVIEW:
            groups.setdefault(int(row["anilist_id"]), []).append((info_hash, row))
    followed = manga_series.followed()
    links = manga_series.anime_links()
    cards = []
    for aid, rows in groups.items():
        found = holdings(aid)
        views = sorted((release_view(info_hash, row, found) for info_hash, row in rows), key=_rank)
        cards.append({**series_card(aid, followed=followed, links=links), "releases": views})
    cards.sort(key=lambda card: -max(view["found_at"] for view in card["releases"]))
    return cards


_COUNT: tuple[int, int] | None = None


def review_count() -> int:
    """Series with releases waiting: the sidebar's badge, asked every few seconds, read once per change."""
    global _COUNT
    try:
        stamp = _path().stat().st_mtime_ns
    except OSError:
        return 0
    if _COUNT is None or _COUNT[0] != stamp:
        _COUNT = (stamp, len({row.get("anilist_id") for row in releases().values() if row.get("status") == REVIEW}))
    return _COUNT[1]


def _free_enough(row: dict[str, Any]) -> str | None:
    settings = get_settings()
    size = int((row.get("entry") or {}).get("size_bytes") or 0)
    floor = settings.manga.min_free_space_gib * 2**30
    for where in (manga_library.root(row.get("kind") or "manga"), Path(settings.qbittorrent.save_path or "/")):
        try:
            free = shutil.disk_usage(where if where.exists() else where.parent).free
        except OSError:
            continue
        if free - size < floor:
            return f"{free / 2**30:.0f} GiB free on {where}"
    return None


def approve(info_hash: str) -> dict[str, Any]:
    """Download it: the next ``reconcile`` adds it to qBittorrent."""
    row = releases().get(info_hash)
    if row is None:
        raise KeyError(info_hash)
    if row.get("status") not in (REVIEW, FAILED, OWNED):
        raise ValueError(f"the release is {row.get('status')}")
    short = _free_enough(row)
    if short:
        raise ValueError(f"not enough room: {short}")
    return _change(info_hash, status=QUEUED, approved_at=time.time(), error=None, reason=None, picked=False, copied=None, left=None)


def discard(info_hash: str) -> dict[str, Any]:
    """Never offered again."""
    return _change(info_hash, status=DISCARDED, reason="discarded by hand")


async def rematch(info_hashes: list[str], anilist_id: int) -> int:
    """These releases are another series."""
    await manga_meta.ensure([int(anilist_id)], refresh=False)
    if not manga_meta.name(anilist_id):
        raise ValueError(f"AniList has no manga {anilist_id}")
    kind = manga_meta.kind_of(manga_meta.media(anilist_id).get("format"))
    changed = 0
    with _LOCK:
        state = _read()
        for info_hash in info_hashes:
            row = state["releases"].get(info_hash)
            if row is not None and row.get("status") in (REVIEW, OWNED, FAILED):
                row.update(anilist_id=int(anilist_id), kind=kind, guess=False, how="hand", status=REVIEW, updated_at=time.time())
                changed += 1
        _write(state)
    return changed


def blacklist(anilist_id: int) -> int:
    """Never look for this series again: its waiting releases are discarded."""
    manga_series.set_policy(anilist_id, "blacklisted", origin="review")
    with _LOCK:
        state = _read()
        changed = 0
        for row in state["releases"].values():
            if int(row.get("anilist_id") or 0) == int(anilist_id) and row.get("status") in (REVIEW, OWNED):
                row.update(status=DISCARDED, reason=_BLACKLISTED, updated_at=time.time())
                changed += 1
        _write(state)
    return changed


def restore(anilist_id: int) -> int:
    """Off the blacklist: the releases it discarded wait for review again."""
    manga_series.clear_policy(anilist_id)
    with _LOCK:
        state = _read()
        changed = 0
        for row in state["releases"].values():
            if int(row.get("anilist_id") or 0) == int(anilist_id) and row.get("reason") == _BLACKLISTED:
                row.update(status=REVIEW, reason=None, updated_at=time.time())
                changed += 1
        _write(state)
    return changed


async def follow(anilist_id: int) -> None:
    await manga_meta.ensure([int(anilist_id)], refresh=False)
    manga_series.set_policy(anilist_id, "followed", origin="search")
    # Searched for at the next pass.
    with _LOCK:
        state = _read()
        state["searched"].pop(str(int(anilist_id)), None)
        _write(state)


async def download(entry: dict[str, Any], anilist_id: int) -> dict[str, Any]:
    """A release picked by hand from the search: approved as it is added."""
    info_hash = str(entry.get("info_hash") or "").lower()
    if not info_hash:
        raise ValueError("the release has no info hash")
    await manga_meta.ensure([int(anilist_id)], refresh=False)
    release = manga_names.parse(str(entry.get("title") or ""))
    fields = {field.name for field in dataclasses.fields(nyaa.NyaaEntry)}
    nyaa_entry = nyaa.NyaaEntry(**{key: value for key, value in entry.items() if key in fields})
    row = _row(nyaa_entry, release, int(anilist_id), guess=False, how="hand")
    short = _free_enough(row)
    if short:
        raise ValueError(f"not enough room: {short}")
    row |= {"status": QUEUED, "approved_at": time.time()}
    with _LOCK:
        state = _read()
        existing = state["releases"].get(info_hash)
        if existing is not None and existing.get("status") in ACTIVE | {DONE}:
            raise ValueError(f"the release is {existing.get('status')} already")
        state["releases"][info_hash] = row
        _write(state)
    return row


# -- Downloading and filing -------------------------------------------------------------


def save_path() -> Path:
    return Path(get_settings().qbittorrent.save_path or "/downloads") / "manga"


def _phase(torrent: Any) -> str:
    state_name = str(getattr(torrent, "state", "")).casefold()
    if "error" in state_name or "missing" in state_name:
        return "error"
    if float(getattr(torrent, "progress", 0.0)) >= 1.0 or state_name.endswith("up"):
        return "complete"
    return "downloading"


def file_release(row: dict[str, Any], content: Path, *, finished: set[str] | None = None) -> dict[str, Any]:
    """A finished download's books that fill a gap (``wanted``), into its series' folder under
    the library's names; the rest stays out, to go with the torrent.

    ``finished``, when given, are the paths qBittorrent completed: a file it was
    told to leave out may lie there half written. Copied beside, then renamed.
    """
    books = [content] if content.is_file() else sorted(path for path in content.rglob("*") if path.is_file())
    books = [path for path in books if path.suffix.lower() in manga_names.BOOK_EXTENSIONS]
    if finished is not None:
        books = [path for path in books if str(path) in finished]
    if not books:
        raise ValueError("the download holds no CBZ, CBR, PDF or EPUB")
    aid = int(row["anilist_id"])
    pages = {path.name: [chapter for chapter, _volume in manga_contents.read(path)] for path in books if path.suffix.lower() in (".cbz", ".zip")}
    verdicts = wanted(aid, [path.name for path in books], pages=pages)
    left = [[path.name, verdicts[path.name]] for path in books if verdicts[path.name]]
    going = [path for path in books if not verdicts[path.name]]
    folder = manga_library.ensure_folder(aid, row.get("kind") or "manga") if going else manga_library.folder_of(aid)
    copied = []
    for source in going:
        name = library_name(source.name)
        target = folder / name
        if target.exists():
            left.append([source.name, _HAS])
            continue
        part = folder / f".{name}.part"
        shutil.copyfile(source, part)
        part.replace(target)
        copied.append(name)
    return {"folder": folder.name if folder else None, "copied": copied, "left": left}


async def _pick(qbit: Any, info_hash: str, row: dict[str, Any]) -> bool:
    """Leave out of a download the files the library does not need, once qBittorrent knows
    them; one that needs none is not downloaded at all. False while the files are not known."""
    files = await qbit.files(info_hash)
    if not files:
        return False
    books = {position: Path(file["name"]).name for position, file in enumerate(files) if Path(file["name"]).suffix.lower() in manga_names.BOOK_EXTENSIONS}
    verdicts = wanted(int(row["anilist_id"]), list(books.values()))
    skip = [int(file.get("index", position)) for position, file in enumerate(files) if position not in books or verdicts[books[position]]]
    left = [[name, verdicts[name]] for name in books.values() if verdicts[name]]
    if len(skip) == len(files):
        await qbit.remove(info_hash, delete_files=True)
        _change(info_hash, status=DONE, progress=1.0, done_at=time.time(), copied=[], left=left, picked=True,
                folder=(manga_library.series().get(int(row["anilist_id"])) or {}).get("folder"))
        log.info("[manga] %s: nothing in it the library lacks, not downloaded", (row.get("entry") or {}).get("title"))
        return True
    if skip:
        await qbit.file_priority(info_hash, skip, 0)
    _change(info_hash, picked=True, left=left)
    return True


async def reconcile() -> dict[str, Any]:
    """Approved releases: added to qBittorrent, followed, and filed once complete."""
    from bankai.torrent.qbittorrent import QBittorrentClient

    active = {key: row for key, row in releases().items() if row.get("status") in (QUEUED, DOWNLOADING)}
    if not active:
        return {}
    settings = get_settings().manga
    filed = 0
    async with QBittorrentClient() as qbit:
        # By hash among all of them: one qBittorrent put in another category is still found.
        torrents = {torrent.hash.lower(): torrent for torrent in await qbit.list_torrents()}
        for info_hash, row in active.items():
            entry = row.get("entry") or {}
            torrent = torrents.get(info_hash)
            if torrent is None:
                try:
                    await qbit.add(
                        magnet=entry.get("magnet_uri") or None,
                        torrent_url=None if entry.get("magnet_uri") else entry.get("download_url"),
                        category=settings.qbittorrent_category,
                        save_path=save_path(),
                    )
                    _change(info_hash, status=DOWNLOADING, progress=0.0, error=None)
                except Exception as exc:
                    _change(info_hash, error=f"qBittorrent: {exc}")
                continue
            phase = _phase(torrent)
            if phase == "error":
                _change(info_hash, status=FAILED, error=f"qBittorrent: {torrent.state}")
            elif phase == "downloading":
                if not row.get("picked"):
                    try:
                        if await _pick(qbit, info_hash, row) and releases()[info_hash].get("status") == DONE:
                            continue
                    except Exception as exc:
                        log.warning("[manga] the files of %s could not be picked: %s", info_hash[:8], exc)
                _change(info_hash, status=DOWNLOADING, progress=round(float(torrent.progress), 3))
            else:
                _change(info_hash, status=PUBLISHING, progress=1.0)
                progress.update(step="filing", series=manga_meta.name(row["anilist_id"]))
                try:
                    listed = await qbit.files(info_hash)
                except Exception as exc:
                    log.warning("[manga] the files of %s could not be listed: %s", info_hash[:8], exc)
                    listed = []
                finished = {
                    str(Path(torrent.save_path) / file["name"])
                    for file in listed
                    if int(file.get("priority", 1)) > 0 and float(file.get("progress") or 0) >= 1
                } if listed else None
                try:
                    result = await asyncio.to_thread(file_release, row, Path(torrent.content_path), finished=finished)
                except (OSError, ValueError) as exc:
                    _change(info_hash, status=FAILED, error=str(exc))
                    continue
                finally:
                    progress.clear()
                try:
                    await qbit.remove(info_hash, delete_files=True)
                except Exception as exc:
                    log.warning("[manga] torrent %s could not be removed: %s", info_hash[:8], exc)
                _change(info_hash, status=DONE, error=None, done_at=time.time(), **result)
                log.info("[manga] %s: %d file(s) into %s, %d left out", entry.get("title"), len(result["copied"]), result["folder"], len(result["left"]))
                filed += 1
    if filed:
        await openpanel.scan()
    return {"active": len(active), "filed": filed}


def status() -> dict[str, Any]:
    """For the settings page and the dashboard."""
    state = _read()
    counts: dict[str, int] = {}
    for row in state["releases"].values():
        counts[row.get("status") or "?"] = counts.get(row.get("status") or "?", 0) + 1
    settings = get_settings().manga
    return {
        "enabled": settings.enabled,
        "followed": len(manga_series.followed()),
        "counts": counts,
        "review": review_count(),
        "last_poll": state.get("last_poll"),
        "last_found": state.get("last_found"),
        "last_error": state.get("last_error"),
        "searched": len(state.get("searched") or {}),
        "openpanel": openpanel.configured(),
        "progress": dict(progress),
    }
