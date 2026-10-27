"""What the manga pages show: the library, a series with its relations, nyaa searched by hand."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any

import httpx

from bankai.config import get_settings
from bankai.metadata import anilist
from bankai.metadata import anilist_manga as manga_meta
from bankai.web import anime_files, manga_contents, manga_library, manga_names, manga_series
from bankai.web import manga_releases as releases_mod

# Finished downloads stay on the library page this long.
_RECENT_SECONDS = 7 * 86400


def _card(
    anilist_id: int, *, followed: dict, links: dict, on_disk: dict, waiting: dict | None = None, reaches: dict | None = None,
) -> dict[str, Any]:
    row = on_disk.get(anilist_id)
    found = manga_library.stats(anilist_id) if row else {"volumes": set(), "chapters": set(), "books": 0, "size": 0}
    volumes, chapters = found["volumes"], found["chapters"]
    complete = bool(row) and releases_mod.complete(anilist_id, (volumes, chapters))
    choice = manga_meta.as_choice(anilist_id)
    reach_chapter, reach_volume = reaches.get(anilist_id, (0, 0)) if reaches is not None else releases_mod.reach(anilist_id)
    # What the drawer's Chapters tab counts: chapters, or a light novel's volumes.
    if choice["kind"] == "novel":
        _volume_of, first, last, have = _volume_span(anilist_id, reach_volume)
        unit = "volume"
    else:
        _holdings, first, last, have = _chapter_span(anilist_id, complete=complete, reach_chapter=reach_chapter)
        unit = "chapter"
    return {
        **choice,
        "why": followed.get(anilist_id, []),
        "anime": releases_mod.anime_names(anilist_id, links),
        "owned": manga_names.describe(volumes, chapters),
        "owned_volumes": len(volumes),
        "owned_chapters": len(chapters),
        "last_volume": max(volumes, default=None),
        "last_chapter": max(chapters, default=None),
        "books": found["books"],
        "size": found["size"],
        "count": {"unit": unit, "have": have, "total": last - first + 1 if last else 0},
        "complete": complete,
        "waiting": (waiting or {}).get(anilist_id, 0),
        "folder": (row or {}).get("folder"),
    }


def _waiting() -> dict[int, int]:
    """Releases waiting for review, by series."""
    counts: dict[int, int] = {}
    for row in releases_mod.releases().values():
        if row.get("status") == releases_mod.REVIEW:
            counts[int(row["anilist_id"])] = counts.get(int(row["anilist_id"]), 0) + 1
    return counts


def _chapter_span(anilist_id: int, *, complete: bool, reach_chapter: int) -> tuple[manga_contents.Holdings, int, int, int]:
    """A manga's holdings, the chapters it runs to -- first and last: the last a book holds when
    the series is complete, else as far as AniList's count or a release found goes -- and how
    many of them the folder has."""
    whole_series = max(int(manga_meta.media(anilist_id).get("chapters") or 0), reach_chapter)
    holdings = manga_contents.holdings(manga_library.folder_of(anilist_id), complete=complete, whole_series=whole_series)
    covered = holdings.covered
    last = max(covered, default=0) if complete else max(whole_series, max(covered, default=0))
    first = 0 if 0 in covered else 1
    return holdings, first, last, sum(1 for chapter in range(first, last + 1) if chapter in covered)


def _volume_span(anilist_id: int, reach_volume: int) -> tuple[dict[int, str], int, int, int]:
    """A light novel's volume files by number, the volumes it runs to -- first and last, as far
    as AniList's count, a release found or a file goes -- and how many of them the folder has."""
    numbers = manga_contents.holdings(manga_library.folder_of(anilist_id), complete=False, whole_series=0).numbers
    volume_of: dict[int, str] = {}
    for name, number in numbers.items():
        if number and number[0] == "volume" and number[1] == int(number[1]):
            volume_of.setdefault(int(number[1]), name)
    last = max(int(manga_meta.media(anilist_id).get("volumes") or 0), reach_volume, max(volume_of, default=0))
    first = 0 if 0 in volume_of else 1
    return volume_of, first, last, sum(1 for volume in range(first, last + 1) if volume in volume_of)


def breakdown(anilist_id: int) -> dict[str, Any]:
    """Every chapter of a series and how the library has it -- in which volume's file, as its own
    file, or not at all; runs of chapters alike, in order -- and every file of its folder in a
    row of its own or a run's, to be ticked and moved.

    Which chapters each book holds is manga_contents.holdings' (read from the
    pages' names, or worked out between the volumes that say). How far the
    series goes: AniList's chapter count, the highest chapter a release found
    names, or the last one on disk; a finished series with every volume is whole
    whatever AniList counts (extras). A file that holds no chapter of its own --
    Volume 0 (its own numbering), a second copy, an in-between number, chapters
    another file holds, no number at all -- stands beside where it belongs,
    saying why. A light novel counts volumes: its EPUBs name no chapters.
    """
    entry = manga_meta.media(anilist_id)
    reach_chapter, reach_volume = releases_mod.reach(anilist_id)
    if manga_meta.kind_of(entry.get("format")) == "novel":
        return _volume_breakdown(anilist_id, reach_volume)
    complete = releases_mod.complete(anilist_id, manga_library.owned(anilist_id))
    holdings, first, last, have = _chapter_span(anilist_id, complete=complete, reach_chapter=reach_chapter)
    numbers, pages, covered = holdings.numbers, holdings.pages, holdings.covered
    volume_files = holdings.volume_files

    rows: list[dict[str, Any]] = []
    for chapter in range(first, last + 1):
        info = covered.get(chapter)
        key = ("missing",) if info is None else ("volume", info["via"]) if info["kind"] == "volume" else ("chapter", info["volume"])
        if rows and rows[-1]["_key"] == key and rows[-1]["to"] == chapter - 1:
            rows[-1]["to"] = chapter
            if info is not None and info["kind"] == "chapter":
                rows[-1]["names"].append(info["via"])
            continue
        rows.append({
            "_key": key,
            "from": chapter,
            "to": chapter,
            "have": info is not None,
            "kind": "missing" if info is None else info["kind"],
            "volume": None if info is None else info.get("volume"),
            "volume_to": None,
            "via": None if info is None else info["via"],
            "names": [] if info is None else [info["via"]],
            "guessed": bool(info and info.get("guessed")),
            "note": None,
        })
    for row in rows:
        row.pop("_key")

    # Each volume: a file, its chapters as files, missing -- or not known, past the
    # last volume a file names while chapters without one go on.
    named = {info["volume"] for info in covered.values() if info["kind"] == "chapter" and info["volume"]}
    total_volumes = max(int(entry.get("volumes") or 0), reach_volume, max(volume_files, default=0), max(named, default=0))
    highest_named = max([*named, *volume_files], default=0)
    unnamed_beyond = any(info["kind"] == "chapter" and not info["volume"] for info in covered.values())
    volumes = []
    for volume in range(1, total_volumes + 1):
        if volume in volume_files:
            state = "file"
        elif volume in named:
            state = "chapters"
        elif volume > highest_named and unnamed_beyond:
            state = "unknown"
        else:
            state = "missing"
        volumes.append({"volume": volume, "state": state})
    # The missing run at the end is the volumes missing at the end, when those are known.
    ending = []
    for row in reversed(volumes):
        if row["state"] != "missing":
            break
        ending.append(row["volume"])
    if rows and rows[-1]["kind"] == "missing" and ending:
        rows[-1]["volume"], rows[-1]["volume_to"] = min(ending), max(ending) if len(ending) > 1 else None

    shown = {name for row in rows for name in row["names"]}
    chapter_of = {chapter: info["via"] for chapter, info in covered.items()}
    volume_of = {volume: name for volume, (name, _chapters) in volume_files.items()}
    apart, doubles = [], []
    for name, number in numbers.items():
        if name in shown:
            continue
        if number and number[0] == "chapter" and number[1] == int(number[1]) and int(number[1]) in covered:
            doubles.append((int(number[1]), name))
        else:
            chapters = sorted({chapter for chapter, _volume in pages[name]})
            apart.append(_apart(name, number, chapters, volume_of=volume_of, chapter_of=chapter_of))
    # Chapter files of chapters another file holds (weekly chapters a volume has since
    # collected): a row a run, as the chapter files above.
    held: list[dict[str, Any]] = []
    for chapter, name in sorted(doubles):
        holder = covered[chapter]
        if held and held[-1]["_holder"] == holder["via"] and held[-1]["to"] in (chapter, chapter - 1):
            held[-1]["to"] = chapter
            held[-1]["names"].append(name)
            continue
        held.append({
            "_holder": holder["via"], "from": chapter, "to": chapter, "have": True, "kind": "chapter",
            "volume": holder.get("volume"), "volume_to": None, "via": name, "names": [name], "guessed": False,
        })
    for row in held:
        holder = row.pop("_holder")
        row["note"] = f"chapter {row['from']} is in {holder} too" if row["from"] == row["to"] else f"chapters {row['from']}–{row['to']} are in {holder} too"
        apart.append((("chapter", row["from"]), row))
    return {
        "unit": "chapter",
        "first": first,
        "last": last,
        "have": have,
        "missing": (last - first + 1) - have if last else 0,
        "rows": _place(rows, apart),
        "volumes": volumes,
        "complete": complete,
    }


def _volume_breakdown(anilist_id: int, reach_volume: int) -> dict[str, Any]:
    """A light novel's breakdown, by volume: an EPUB's pages name no chapters."""
    volume_of, first, last, have = _volume_span(anilist_id, reach_volume)
    numbers = manga_contents.holdings(manga_library.folder_of(anilist_id), complete=False, whole_series=0).numbers
    complete = releases_mod.complete(anilist_id, manga_library.owned(anilist_id))
    rows: list[dict[str, Any]] = []
    for volume in range(first, last + 1):
        name = volume_of.get(volume)
        if name is None and rows and rows[-1]["kind"] == "missing":
            rows[-1]["to"] = volume
            continue
        rows.append({
            "from": volume, "to": volume, "have": name is not None, "kind": "volume" if name else "missing",
            "volume": volume, "volume_to": None, "via": name, "names": [name] if name else [], "guessed": False, "note": None,
        })
    shown = set(volume_of.values())
    apart = [_apart(name, number, [], volume_of=volume_of, chapter_of={}) for name, number in numbers.items() if name not in shown]
    return {
        "unit": "volume",
        "first": first,
        "last": last,
        "have": have,
        "missing": (last - first + 1) - have if last else 0,
        "rows": _place(rows, apart),
        "volumes": [],
        "complete": complete,
    }


def _apart(
    name: str, number: tuple[str, float] | None, chapters: list[float], *, volume_of: dict[int, str], chapter_of: dict[int, str],
) -> tuple[tuple[str, float], dict[str, Any]]:
    """A file no run shows: its row, saying why, and what it stands beside (its volume or chapter)."""
    row: dict[str, Any] = {
        "from": None, "to": None, "have": True, "kind": "other", "volume": None, "volume_to": None,
        "via": name, "names": [name], "guessed": False, "note": "its name gives no single volume or chapter",
    }
    if number is None:
        return ("end", 0), row
    kind, value = number
    whole = int(value) if value == int(value) else None
    row["kind"] = kind
    if kind == "chapter":
        row["from"] = row["to"] = value
        row["note"] = "in between, outside the count" if whole is None else "a chapter, where the rows count volumes"
        return ("chapter", value), row
    row["volume"] = value
    if chapters:
        row["from"], row["to"] = chapters[0], chapters[-1]
    if value == 0:
        row["note"] = "its own numbering, before the series"
        return ("start", 0), row
    held = sorted({chapter_of[int(chapter)] for chapter in chapters if chapter == int(chapter) and int(chapter) in chapter_of} - {name})
    if whole is None:
        row["note"] = "in between, outside the count"
    elif volume_of.get(whole, name) != name:
        row["note"] = f"the same volume as {volume_of[whole]}"
    elif held:
        row["note"] = f"its chapters are in {', '.join(held)} too"
    elif chapters:
        row["note"] = "in-between chapters, outside the count"
    else:
        row["note"] = "its pages do not say which chapters it holds"
    return ("volume", value), row


def _place(rows: list[dict[str, Any]], apart: list[tuple[tuple[str, float], dict[str, Any]]]) -> list[dict[str, Any]]:
    """The runs, each file apart after the last run of its volume (or chapter) or below: Vol 0
    first, a file without a number last."""
    after: dict[int, list[tuple[tuple[Any, ...], dict[str, Any]]]] = {}
    for (by, number), row in apart:
        field = "from" if by == "chapter" else "volume"
        anchor = (
            -1 if by == "start"
            else len(rows) - 1 if by == "end"
            else max((index for index, run in enumerate(rows) if run[field] is not None and run[field] <= number), default=-1)
        )
        after.setdefault(anchor, []).append(((by == "end", by != "start", number, row["via"]), row))
    placed: list[dict[str, Any]] = []
    for index in range(-1, len(rows)):
        if index >= 0:
            placed.append(rows[index])
        placed.extend(row for _order, row in sorted(after.get(index, []), key=lambda item: item[0]))
    return placed


def warm(*, budget: int = 400) -> int:
    """Read the pages' names of library archives not read yet, a few hundred a pass; then what
    each series' folder holds, so the review and the drawer answer at once."""
    paths = []
    for anilist_id in manga_library.series():
        folder = manga_library.folder_of(anilist_id)
        if folder is None:
            continue
        try:
            paths.extend(path for path in folder.iterdir() if path.suffix.lower() in (".cbz", ".zip"))
        except OSError:
            continue
    before = len(manga_contents.chapters(paths, budget=0))
    after = len(manga_contents.chapters(paths, budget=budget))
    # Once every archive is read: holdings read the ones a folder has left all at once.
    if after == len(paths):
        for anilist_id in manga_library.series():
            releases_mod.holdings(anilist_id)
    return after - before


def library() -> dict[str, Any]:
    """The series on disk, those followed with nothing on disk yet, folders to match, and downloads."""
    followed = manga_series.followed()
    links = manga_series.anime_links()
    on_disk = manga_library.series()
    rows = releases_mod.releases()
    now = time.time()
    downloads = [
        releases_mod.release_view(info_hash, row)
        for info_hash, row in rows.items()
        if row.get("status") in releases_mod.ACTIVE | {releases_mod.FAILED}
        or (row.get("status") == releases_mod.DONE and now - float(row.get("done_at") or 0) < _RECENT_SECONDS)
    ]
    for view, row in zip(downloads, (rows[view["info_hash"]] for view in downloads), strict=True):
        view |= {"anilist_id": row.get("anilist_id"), "name": manga_meta.name(row.get("anilist_id"))}
    downloads.sort(key=lambda view: -float(rows[view["info_hash"]].get("updated_at") or 0))
    waiting = _waiting()
    reaches = releases_mod.reaches()
    cards = {"followed": followed, "links": links, "on_disk": on_disk, "waiting": waiting, "reaches": reaches}
    return {
        "series": [_card(aid, **cards) for aid in on_disk],
        "followed": [_card(aid, **cards) for aid in followed if aid not in on_disk],
        "unmatched": manga_library.unmatched(),
        "downloads": downloads,
    }


async def linked(anime_ids: list[int]) -> list[dict[str, Any]]:
    """The manga and light novels these anime link to, with what bankai does about each."""
    edges: dict[int, tuple[str, str | None]] = {}
    for anime_id in anime_ids:
        for manga_id, relation, format_ in anilist.media(anime_id).get("manga") or []:
            edges.setdefault(int(manga_id), (str(relation), format_))
    await manga_meta.ensure(list(edges), refresh=False, max_batches=1)
    on_disk = manga_library.series()
    followed = manga_series.followed()
    blacklisted = manga_series.blacklisted()
    waiting = {int(row.get("anilist_id") or 0) for row in releases_mod.releases().values() if row.get("status") == releases_mod.REVIEW}
    return [
        {
            "anilist_id": aid,
            "title": manga_meta.name(aid) or f"AniList {aid}",
            "format": manga_meta.media(aid).get("format") or format_,
            "relation": relation,
            "year": manga_meta.media(aid).get("year"),
            "cover": manga_meta.media(aid).get("cover"),
            "owned": manga_names.describe(*manga_library.owned(aid)) if aid in on_disk else "",
            "on_disk": aid in on_disk,
            "followed": aid in followed,
            "blacklisted": aid in blacklisted,
            "in_review": aid in waiting,
        }
        for aid, (relation, format_) in edges.items()
    ]


def blacklist() -> list[dict[str, Any]]:
    return sorted(
        ({**row, "anilist_id": aid} for aid, row in manga_series.policies().items() if row.get("mode") == "blacklisted"),
        key=lambda row: -float(row.get("updated_at") or 0),
    )


async def graph(anilist_id: int) -> dict[str, Any]:
    """A series and what AniList relates it to: its anime (marked when on disk) and other manga."""
    entry = manga_meta.media(anilist_id)
    relations = entry.get("relations") or []
    manga_ids = [int(row[0]) for row in relations if row[2] == "MANGA"]
    anime_ids = [int(row[0]) for row in relations if row[2] == "ANIME"]
    await manga_meta.ensure(manga_ids, refresh=False, max_batches=1)
    await anilist.ensure(anime_ids, refresh=False, max_batches=1)
    anime_on_disk = set(anime_files.by_entry())
    on_disk = manga_library.series()
    followed = manga_series.followed()
    blacklisted = manga_series.blacklisted()

    def manga_node(aid: int, relation: str | None) -> dict[str, Any]:
        kept = manga_meta.media(aid)
        return {
            "anilist_id": aid, "type": "MANGA", "format": kept.get("format"), "title": manga_meta.name(aid) or f"AniList {aid}",
            "year": kept.get("year"), "cover": kept.get("cover"), "relation": relation,
            "on_disk": aid in on_disk, "followed": aid in followed, "blacklisted": aid in blacklisted,
            "owned": manga_names.describe(*manga_library.owned(aid)) if aid in on_disk else "",
        }

    nodes = [manga_node(anilist_id, None)]
    edges = []
    for other, relation, kind, _format in relations:
        if kind == "MANGA":
            nodes.append(manga_node(int(other), relation))
        else:
            kept = anilist.media(int(other))
            nodes.append({
                "anilist_id": int(other), "type": "ANIME", "format": kept.get("format"),
                "title": anilist.name(int(other)) if kept else f"AniList {other}",
                "year": kept.get("year"), "cover": kept.get("cover"), "relation": relation,
                "on_disk": int(other) in anime_on_disk,
            })
        edges.append({"from": anilist_id, "to": int(other), "label": relation})
    return {"root": anilist_id, "nodes": nodes, "edges": edges}


async def series_detail(anilist_id: int) -> dict[str, Any]:
    await manga_meta.ensure([int(anilist_id)], refresh=False)
    entry = manga_meta.media(anilist_id)
    followed = manga_series.followed()
    links = manga_series.anime_links()
    rows = [
        (info_hash, row) for info_hash, row in releases_mod.releases().items()
        if int(row.get("anilist_id") or 0) == int(anilist_id)
    ]
    found = releases_mod.holdings(anilist_id)
    views = sorted((releases_mod.release_view(info_hash, row, found) for info_hash, row in rows), key=lambda view: -view["found_at"])
    return {
        **_card(anilist_id, followed=followed, links=links, on_disk=manga_library.series(), waiting=_waiting()),
        "description": entry.get("description"),
        "genres": entry.get("genres") or [],
        "authors": entry.get("authors") or [],
        "banner": entry.get("banner"),
        "country": entry.get("country"),
        "files": manga_library.files(anilist_id),
        "releases": views,
        "policy": manga_series.policies().get(int(anilist_id)),
        "breakdown": await asyncio.to_thread(breakdown, anilist_id),
        "graph": await graph(anilist_id),
    }


async def search(query: str) -> list[dict[str, Any]]:
    """nyaa's English literature searched by hand, each release read and matched where it can be."""
    followed = manga_series.followed()
    index = releases_mod.title_index(followed)
    headers = {"User-Agent": get_settings().scraper.user_agent}
    async with httpx.AsyncClient(base_url="https://nyaa.si", headers=headers, timeout=30, follow_redirects=True) as client:
        entries = await releases_mod.feed(client, query.strip())
    known = releases_mod.releases()
    rows = []
    for entry in entries:
        release = manga_names.parse(entry.title)
        aid, guess = releases_mod.match(release, entry.size_bytes, index)
        novel = manga_names.novel(release, entry.size_bytes)
        rows.append({
            **dataclasses.asdict(entry),
            "volumes": list(release.volumes) if release.volumes else None,
            "chapters": list(release.chapters) if release.chapters else None,
            "group": release.group,
            "audiobook": release.audiobook,
            "kind": None if novel is None else "novel" if novel else "manga",
            "match": manga_meta.as_choice(aid) if aid else None,
            "guess": guess,
            "status": (known.get(entry.info_hash) or {}).get("status"),
        })
    return rows
