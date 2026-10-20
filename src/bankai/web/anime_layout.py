"""The anime library on disk, laid out for Jellyfin, with NFO files.

    <anime root>/<Collection>/tvshow.nfo, poster.jpg, fanart.jpg
    <anime root>/<Collection>/Season 01/season.nfo, poster.jpg
    <anime root>/<Collection>/Season 01/<Collection> - S01E05.mkv (+ .nfo, subtitles)
    <anime root>/<Collection>/Season 00/<Collection> - S00E03.mkv   (movies, OVAs, side stories)

A collection (see anime_collections) is one show. Its main line is Season 1,
2, 3... and every other entry a block of Season 00. Jellyfin reads what each
file is from the NFO next to it, so it needs no metadata source of its own:
names, episode titles, air dates and pictures all come from AniList.

When a collection changes -- an entry joins, one is split off, its title
entry is changed -- its files are renamed into place; renames only, within
the library's own volume. Every rename is written down first (see
``moves_dir``), so a re-layout can be undone.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.metadata import anilist
from bankai.processor.naming import sanitise
from bankai.web import anime_collections as collections
from bankai.web import anime_files

log = get_logger(__name__)

VIDEO_SUFFIXES = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".webm"})
# What travels with a video when it is renamed: subtitles, its NFO, a thumbnail.
_COMPANIONS = re.compile(r"^(?:\.[\w-]+)*\.(?:ass|ssa|srt|sub|idx|vtt|sup|nfo)$|^-thumb\.(?:jpg|png)$", re.IGNORECASE)
# Pictures are left out when the disk has less than this free.
_IMAGE_RESERVE = 2 * 1024**3


# -- Names -------------------------------------------------------------------------


def folder_names(store: collections.Store) -> dict[int, str]:
    """Each collection's folder name: its name, made safe, and unique.

    Two collections of one name -- a remake and its original -- are told
    apart by the year; the older collection keeps the plain name.
    """
    taken: dict[str, int] = {}
    result: dict[int, str] = {}
    for cid in sorted(store.collections):
        row = store.collections[cid]
        base = sanitise(collections.name(row), fallback=f"AniList {row.title}")
        candidates = [base]
        year = anilist.year(row.title) if row.title is not None else None
        if year:
            candidates.append(f"{base} ({year})")
        candidates.append(f"{base} [{cid}]")
        for candidate in candidates:
            if candidate.casefold() not in taken:
                taken[candidate.casefold()] = cid
                result[cid] = candidate
                break
    return result


def season_folder(season: int) -> str:
    return f"Season {season:02d}"


def file_name(folder: str, season: int, number: int, suffix: str) -> str:
    return sanitise(f"{folder} - S{season:02d}E{number:02d}{suffix.casefold()}")


@dataclass
class Layout:
    """Where everything goes, for the collections as they are now."""

    store: collections.Store
    rows: dict[str, anime_files.Pairs]
    folders: dict[int, str] = field(default_factory=dict)
    plans: dict[int, collections.Plan] = field(default_factory=dict)
    of: dict[int, int] = field(default_factory=dict)

    @classmethod
    def build(cls, store: collections.Store, rows: dict[str, anime_files.Pairs]) -> Layout:
        layout = cls(store, rows, folder_names(store), {}, store.of())
        held = anime_files.by_entry(rows)
        for cid, row in store.collections.items():
            layout.plans[cid] = collections.plan(
                row, episodes_on_disk=lambda aid: max(held.get(aid, {0: []}), default=0), store=store
            )
        return layout

    def target(self, aid: int, episode: int, suffix: str) -> str | None:
        """The key a file of this episode belongs at, or None if its entry is in no collection."""
        cid = self.of.get(int(aid))
        if cid is None:
            return None
        place = self.plans[cid].place(int(aid), int(episode))
        if place is None:
            return None
        folder = self.folders[cid]
        season, number = place
        return PurePosixPath(folder, season_folder(season), file_name(folder, season, number, suffix)).as_posix()


# -- Moving files into place -------------------------------------------------------


@dataclass
class Move:
    old: str
    new: str


@dataclass
class Moves:
    moves: list[Move] = field(default_factory=list)
    # Two files of one episode: the second stays where it is.
    duplicates: list[tuple[str, str]] = field(default_factory=list)
    # Entries in no collection: their files stay where they are.
    unplaced: list[str] = field(default_factory=list)


def plan_moves(layout: Layout, *, cids: set[int] | None = None) -> Moves:
    """The renames that put every indexed file of these collections where it belongs."""
    result = Moves()
    claimed: dict[str, str] = {}
    current = set(layout.rows)
    for rel in sorted(layout.rows):
        pairs = layout.rows[rel]
        aid, episode = pairs[0]
        cid = layout.of.get(aid)
        if cids is not None and cid not in cids:
            continue
        target = layout.target(aid, episode, PurePosixPath(rel).suffix)
        if target is None:
            result.unplaced.append(rel)
            continue
        if target in claimed or (target != rel and target in current and layout.rows[target][0] == (aid, episode)):
            # Already there, or claimed by another file of the same episode.
            result.duplicates.append((rel, claimed.get(target, target)))
            continue
        claimed[target] = rel
        if target != rel:
            result.moves.append(Move(rel, target))
    return result


def moves_dir() -> Path:
    return bgjobs.jobs_root().parent / "anime_layout_moves"


def _companions(video: Path) -> list[Path]:
    """Files beside a video that belong to it: subtitles, its NFO, its thumbnail."""
    stem = video.stem
    found = []
    try:
        for sibling in video.parent.iterdir():
            if sibling == video or not sibling.name.startswith(stem) or not sibling.is_file():
                continue
            if _COMPANIONS.match(sibling.name[len(stem):]):
                found.append(sibling)
    except OSError:
        pass
    return found


# What bankai writes into a show's folder itself, and so may take away again.
_OWN_FILES = frozenset({"tvshow.nfo", "season.nfo", "poster.jpg", "fanart.jpg"})


def _prune(directory: Path, root: Path) -> None:
    """Remove directories left empty, up to the root.

    A show folder whose videos have all moved out -- the collection renamed,
    its seasons renumbered -- still holds the NFO files and pictures written
    for it, and Jellyfin would show it as an empty show. Those go too, but
    only those, and only once no video is left anywhere under the folder.
    """
    try:
        relative = directory.relative_to(root)
        # Videos still beside the one moved out: nothing to take away (and
        # the common case, checked without walking the whole show).
        if any(path.suffix.casefold() in VIDEO_SUFFIXES for path in directory.iterdir()):
            return
    except (ValueError, OSError):
        return
    if relative.parts:
        show = root / relative.parts[0]
        try:
            leftovers = [path for path in show.rglob("*") if path.is_file()]
        except OSError:
            leftovers = []
        if leftovers and all(path.name.casefold() in _OWN_FILES for path in leftovers):
            for path in leftovers:
                path.unlink(missing_ok=True)
            for folder in sorted({path.parent for path in leftovers}, key=lambda p: -len(p.parts)):
                if folder != show:
                    with suppress(OSError):
                        folder.rmdir()
    current = directory
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except FileNotFoundError:
            pass  # taken away with the show's own files above
        except OSError:
            return
        current = current.parent


def apply(moves: Moves, *, label: str = "layout") -> dict:
    """Rename the files; the index follows. Returns what was done.

    Each rename is logged to a file before it happens: ``undo`` puts them back.
    A target that exists already is never overwritten: a move into a place
    another file is about to leave waits for it, pass after pass.
    """
    if not moves.moves:
        return {"moved": 0, "skipped": [], "log": None}
    folder = moves_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime(f"{label}-%Y%m%d-%H%M%S-{os.getpid()}")
    log_path = folder / f"{stamp}.jsonl"
    number = 1
    while log_path.exists():  # a log per run, even for two runs in one second
        number += 1
        log_path = folder / f"{stamp}-{number}.jsonl"
    renamed: dict[str, str] = {}
    done: set[Path] = set()
    pending = list(moves.moves)
    with log_path.open("a", encoding="utf-8") as journal:
        while pending:
            waiting = []
            for move in pending:
                outcome = _move(move, journal, done)
                if outcome == "blocked":
                    waiting.append(move)
                elif outcome == "moved":
                    renamed[move.old] = move.new
            if len(waiting) == len(pending):
                break
            pending = waiting
    skipped = [f"{move.old}: {move.new} is taken" for move in pending]
    if renamed:
        with anime_files.editing() as rows:
            for old, new in renamed.items():
                if old in rows:
                    rows[new] = rows.pop(old)
    if not renamed:
        log_path.unlink(missing_ok=True)
    return {"moved": len(renamed), "skipped": skipped, "log": str(log_path) if renamed else None}


def _same_file(left: Path, right: Path) -> bool:
    """One file under two spellings: the library's disk ignores case."""
    try:
        return left.samefile(right)
    except OSError:
        return False


def _rename(old: Path, new: Path) -> None:
    """A rename that also works when only the case of the name changes.

    The library is on NTFS, which takes "BLUE LOCK" for "Blue Lock": renamed
    straight across, the name keeps its old case. Going through a temporary
    name makes it take the new one.
    """
    if old.name != new.name and old.name.casefold() == new.name.casefold() and old.parent == new.parent:
        step = old.with_name(f".{old.name}.{os.getpid()}.case")
        old.replace(step)
        step.replace(new)
    else:
        old.replace(new)


def _exact_folders(root: Path, target: Path, done: set[Path]) -> None:
    """Give each folder on the way to ``target`` exactly its name, case and all.

    A collection renamed only in case ("Blue Lock" -> "BLUE LOCK") would
    otherwise keep its old folder, which the disk takes for the new one, and
    the index -- which compares names exactly -- would not know its files.
    """
    current = root
    for part in target.relative_to(root).parts[:-1]:
        wanted = current / part
        if wanted not in done:
            done.add(wanted)
            try:
                actual = next(
                    (child for child in current.iterdir() if child.name.casefold() == part.casefold()), None
                )
            except OSError:
                return
            if actual is not None and actual.name != part:
                _rename(actual, wanted)
        current = wanted


def repair_folder_case(folders: Iterable[str]) -> int:
    """Give show folders that differ from their collection's name only in case that name.

    Moves into a folder renamed only in case went into the old one -- NTFS
    took one for the other -- and the index, which compares names exactly,
    then knew none of its files. Returns how many folders were renamed.
    """
    wanted = {name.casefold(): name for name in folders}
    fixed = 0
    for root in (anime_files.anime_root(), anime_files.staging_root()):
        try:
            children = [child for child in root.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            name = wanted.get(child.name.casefold())
            if name is None or child.name == name:
                continue
            target = root / name
            if target.exists() and not _same_file(child, target):
                continue  # a folder of its own, on a disk that tells case apart
            _rename(child, target)
            fixed += 1
    return fixed


def _move(move: Move, journal, done: set[Path] | None = None) -> str:
    """One file and its companions, in the library and in staging: moved, blocked or missing."""
    outcome = "missing"
    done = done if done is not None else set()
    for root in (anime_files.anime_root(), anime_files.staging_root()):
        source = root.joinpath(*PurePosixPath(move.old).parts)
        target = root.joinpath(*PurePosixPath(move.new).parts)
        if not source.is_file():
            continue
        if target.exists() and not _same_file(source, target):
            return "blocked"
        # A folder renamed in case only is still found under its old spelling.
        _exact_folders(root, target, done)
        pairs = [(source, target)] + [
            (extra, target.with_name(target.stem + extra.name[len(source.stem):]))
            for extra in _companions(source)
        ]
        target.parent.mkdir(parents=True, exist_ok=True)
        for old, new in pairs:
            if new.exists() and not _same_file(old, new):
                continue
            journal.write(json.dumps({"from": str(old), "to": str(new)}, ensure_ascii=False) + "\n")
            journal.flush()
            _rename(old, new)
        _prune(source.parent, root)
        outcome = "moved"
    return outcome


def undo(log_path: Path) -> int:
    """Put back the renames of one layout run, last first; the index follows. Returns how many."""
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    undone = 0
    back: dict[str, str] = {}
    for row in reversed(lines):
        source, target = Path(row["to"]), Path(row["from"])
        if source.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            undone += 1
            old, new = anime_files.key(source), anime_files.key(target)
            if old and new and source.suffix.casefold() in VIDEO_SUFFIXES:
                back[old] = new
    if back:
        with anime_files.editing() as rows:
            for old, new in back.items():
                if old in rows:
                    rows[new] = rows.pop(old)
    return undone


# -- NFO files -----------------------------------------------------------------------


def _xml(tag: str, value: object) -> str:
    return f"  <{tag}>{escape(str(value))}</{tag}>\n" if value not in (None, "", []) else ""


def _plain(text: str | None) -> str | None:
    """AniList's descriptions carry a little HTML: <br>, <i>."""
    if not text:
        return None
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def tvshow_nfo(row: collections.Collection, title: str) -> str:
    main = row.title if row.title is not None else row.members[0]
    kept = anilist.media(main)
    status = "Continuing" if any(anilist.media(aid).get("status") == "RELEASING" for aid in row.members) else "Ended"
    body = (
        _xml("title", title)
        + _xml("originaltitle", kept.get("romaji"))
        + _xml("plot", _plain(kept.get("description")))
        + _xml("year", kept.get("year"))
        + _xml("premiered", kept.get("start"))
        + _xml("status", status)
        + "".join(_xml("genre", genre) for genre in kept.get("genres") or [])
        + "".join(_xml("studio", studio) for studio in kept.get("studios") or [])
        + f'  <uniqueid type="anilist" default="true">{main}</uniqueid>\n'
    )
    return f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<tvshow>\n{body}</tvshow>\n'


def season_nfo(season: int, aid: int | None, show: str) -> str:
    if season == 0 or aid is None:
        title, extra = "Specials", ""
    else:
        kept = anilist.media(aid)
        title = collections.short_label(collections.entry_name(aid), show)
        if title == show:
            title = f"Season {season}"
        extra = (
            _xml("plot", _plain(kept.get("description")))
            + _xml("premiered", kept.get("start"))
            + _xml("year", kept.get("year"))
            + f'  <uniqueid type="anilist" default="true">{aid}</uniqueid>\n'
        )
    body = _xml("title", title) + _xml("seasonnumber", season) + extra
    return f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<season>\n{body}</season>\n'


def episode_nfo(
    show: str, season: int, number: int, pairs: list[tuple[int, int]], *, on_disk: int = 0
) -> str:
    """An episode's NFO. A special is titled by its entry, and by its episode too
    when the entry has more than one -- by AniList's count, or by the files on
    disk (``on_disk``) where AniList counts a two-part special as one."""
    aid, episode = pairs[0]
    known = {n: (title, aired) for n, title, aired in anilist.episodes(aid)}
    title, aired = known.get(episode, (None, None))
    if season == 0:
        name = collections.entry_name(aid)
        many = max(anilist.episode_count(aid) or 1, on_disk, episode) > 1
        title = f"{name} - {title or f'Episode {episode}'}" if many else name
        aired = aired or anilist.media(aid).get("start")
    title = title or f"Episode {episode}"
    if len(pairs) > 1:
        title = f"{title} (+{len(pairs) - 1})"
    body = (
        _xml("title", title)
        + _xml("showtitle", show)
        + _xml("season", season)
        + _xml("episode", number)
        + _xml("aired", aired)
        + f'  <uniqueid type="anilist" default="true">{aid}</uniqueid>\n'
    )
    return f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<episodedetails>\n{body}</episodedetails>\n'


def _write_text(path: Path, text: str) -> bool:
    """Write only what changed, so the disk is not touched for nothing. True if written.

    A write that fails -- a full disk -- is skipped, not raised: an NFO file
    is written again on the next pass, and a run of renames must not stop
    halfway for one.
    """
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not write %s: %s", path, exc)
        tmp.unlink(missing_ok=True)
        return False
    return True


@dataclass
class Picture:
    path: Path
    url: str


def write_nfo(layout: Layout, cids: Iterable[int]) -> tuple[int, list[Picture]]:
    """Write the NFO files of these collections' folders in the library.

    Returns how many were written, and the pictures each folder should have,
    for ``fetch_pictures``. Staged files get theirs once Transfer has moved
    them, from the next pass.
    """
    written = 0
    pictures: list[Picture] = []
    root = anime_files.anime_root()
    held = anime_files.by_entry(layout.rows)
    by_folder: dict[int, list[str]] = {}
    for rel, pairs in layout.rows.items():
        cid = layout.of.get(pairs[0][0])
        if cid is not None:
            by_folder.setdefault(cid, []).append(rel)
    for cid in cids:
        row = layout.store.collections.get(cid)
        if row is None:
            continue
        folder = root / layout.folders[cid]
        if not folder.is_dir():
            continue
        show = collections.name(row)
        written += _write_text(folder / "tvshow.nfo", tvshow_nfo(row, show))
        main = row.title if row.title is not None else row.members[0]
        if anilist.cover(main):
            pictures.append(Picture(folder / "poster.jpg", anilist.cover(main)))
        if anilist.media(main).get("banner"):
            pictures.append(Picture(folder / "fanart.jpg", anilist.media(main)["banner"]))
        plan = layout.plans[cid]
        # A season made of several entries is named and pictured after its first.
        by_season = {number: plan.first_of(number) for number in set(plan.seasons.values())}
        for season_dir in folder.glob("Season *"):
            match = re.fullmatch(r"Season (\d+)", season_dir.name)
            if not match or not season_dir.is_dir():
                continue
            number = int(match[1])
            aid = by_season.get(number)
            written += _write_text(season_dir / "season.nfo", season_nfo(number, aid, show))
            if aid is not None and anilist.cover(aid):
                pictures.append(Picture(season_dir / "poster.jpg", anilist.cover(aid)))
        for rel in by_folder.get(cid, []):
            video = root.joinpath(*PurePosixPath(rel).parts)
            if not video.is_file():
                continue
            pairs = layout.rows[rel]
            place = plan.place(*pairs[0])
            if place is None:
                continue
            on_disk = len(held.get(pairs[0][0], {}))
            written += _write_text(
                video.with_suffix(".nfo"), episode_nfo(show, place[0], place[1], pairs, on_disk=on_disk)
            )
    return written, pictures


def _pictures_path() -> Path:
    return bgjobs.jobs_root().parent / "anime_layout_pictures.json"


async def fetch_pictures(pictures: list[Picture]) -> int:
    """Download each picture that is missing, or whose source changed. Returns how many.

    Left out while the library's disk is nearly full: a poster is not worth a
    failed download.
    """
    if not pictures:
        return 0
    try:
        free = shutil.disk_usage(anime_files.anime_root()).free
    except OSError:
        return 0
    if free < _IMAGE_RESERVE:
        log.info("anime layout: %d pictures left out, the library disk is nearly full", len(pictures))
        return 0
    try:
        sources = json.loads(_pictures_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sources = {}
    due = [p for p in pictures if not p.path.exists() or sources.get(str(p.path)) != p.url]
    fetched = 0
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for picture in due:
            try:
                response = await client.get(picture.url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.debug("picture %s: %s", picture.url, exc)
                continue
            tmp = picture.path.with_name(f".{picture.path.name}.part")
            try:
                tmp.write_bytes(response.content)
                tmp.replace(picture.path)
            except OSError as exc:
                log.debug("picture %s: %s", picture.path, exc)
                tmp.unlink(missing_ok=True)
                continue
            sources[str(picture.path)] = picture.url
            fetched += 1
    if fetched:
        path = _pictures_path()
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return fetched


# -- Keeping it current ----------------------------------------------------------------


async def ensure_metadata(aids: Iterable[int]) -> None:
    """Fetch what the layout needs of these entries, if not kept yet.

    The entries, everything related to them (the relation graph shows it),
    and their sequels and prequels as far as they go: the chain decides the
    seasons.
    """
    def batches(ids: set[int]) -> int:
        return len(ids) // 50 + 1

    wanted = {int(aid) for aid in aids}
    await anilist.ensure(wanted, refresh=False, max_batches=batches(wanted))
    near = {other for aid in wanted for other, _kind, _form in anilist.relations(aid)} - wanted
    await anilist.ensure(near, refresh=False, max_batches=batches(near))
    seen = wanted | near
    frontier = {other for aid in near for other, kind, _form in anilist.relations(aid) if kind in collections.MAIN_LINE}
    for _ in range(8):
        frontier -= seen
        if not frontier:
            break
        await anilist.ensure(frontier, refresh=False, max_batches=batches(frontier))
        seen |= frontier
        frontier = {other for aid in frontier for other, kind, _form in anilist.relations(aid) if kind in collections.MAIN_LINE}


async def relayout(cids: set[int] | None = None, *, pictures: bool = True, label: str = "layout") -> dict:
    """Put these collections' files where they belong (all, by default), and write their NFO files."""
    store = await asyncio.to_thread(collections.load)
    rows = await asyncio.to_thread(anime_files.load)
    targets = set(store.collections) if cids is None else {cid for cid in cids if cid in store.collections}
    layout = await asyncio.to_thread(Layout.build, store, rows)
    await asyncio.to_thread(repair_folder_case, [layout.folders[cid] for cid in targets if cid in layout.folders])
    # All of them: the files of no collection are reported too.
    moves = plan_moves(layout, cids=None if cids is None else targets)
    result = await asyncio.to_thread(apply, moves, label=label)
    layout.rows = await asyncio.to_thread(anime_files.load)
    written, wanted = await asyncio.to_thread(write_nfo, layout, targets)
    fetched = await fetch_pictures(wanted) if pictures else 0
    return {
        **result,
        "duplicates": moves.duplicates,
        "unplaced": moves.unplaced,
        "nfo_written": written,
        "pictures": fetched,
    }


async def place_new(aid: int, episodes: list[int]) -> dict[int, str]:
    """Where new files of an entry go: its collection joined (or made) first.

    Returns episode -> key. Used by the downloader before it stages a file.
    """
    await ensure_metadata([aid])
    with collections.editing() as store:
        collections.join([int(aid)], store)
        row = store.collection_of(int(aid))
        if row is not None:
            collections.settle_specials(row)
    store = await asyncio.to_thread(collections.load)
    rows = await asyncio.to_thread(anime_files.load)
    # The new episodes count as on disk: a Season 00 block must be long enough for them.
    for number in episodes:
        rows.setdefault(f"\0new/{aid}/{number}", [(int(aid), int(number))])
    layout = Layout.build(store, rows)
    return {number: layout.target(aid, number, ".mkv") or "" for number in episodes}


def collection_of_entry(aid: int) -> int | None:
    row = collections.load().collection_of(int(aid))
    return row.id if row is not None else None


__all__ = [
    "Layout",
    "Move",
    "Moves",
    "apply",
    "collection_of_entry",
    "episode_nfo",
    "fetch_pictures",
    "file_name",
    "folder_names",
    "moves_dir",
    "place_new",
    "plan_moves",
    "relayout",
    "season_folder",
    "season_nfo",
    "tvshow_nfo",
    "undo",
    "write_nfo",
]
