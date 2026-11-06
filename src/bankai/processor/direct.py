"""A movie or show downloaded from a torrent and filed as it is (web/direct.py).

No extract, sync or remux: qBittorrent downloads the release, its video goes
into the library folder picked for it -- a second name for the same bytes where
the volume allows, a copy elsewhere -- and the torrent is removed once filed.

Given a folder per language instead (``roots``: followed shows, see
web.mas_follow), each file goes by its own audio: with German into the German
folder, else into the English one. A German episode replaces an English-only
copy of it; an English one never goes beside a German one.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.processor.anime import _copy_with_sidecars, _download_root, _progress
from bankai.torrent import actions as torrent_actions
from bankai.torrent.matcher import find_video_files, pick_movie_file
from bankai.torrent.qbittorrent import QBittorrentClient, QBittorrentError, TorrentStalled
from bankai.web import direct

log = get_logger(__name__)

# A category of its own: the anime automation pauses every unfinished torrent of
# bankai's category while it is off, and that took these too (Oct 2026).
CATEGORY = "bankai-direct"


async def _link(magnet_uri: str | None, download_url: str | None) -> tuple[str | None, bytes | None, str | None]:
    """What to hand qBittorrent -- a magnet, or the .torrent file fetched here (qBittorrent,
    inside the VPN container, cannot reach Prowlarr) -- and the info hash it has."""
    from bankai.torrent.worker import fetch_torrent, torrent_info_hash

    if magnet_uri and magnet_uri.casefold().startswith("magnet:"):
        return magnet_uri, None, torrent_info_hash(magnet_uri, None)
    # Prowlarr's "magnet" for some indexers (The Pirate Bay via Knaben) is its own
    # http link: handed over as it was, qBittorrent answered 202 "pending" and never
    # got it, and every Modern Family season failed in its first second (Oct 2026).
    # Such a link is the one to fetch; the download URL is then the indexer's page.
    magnet, data = await fetch_torrent(str(magnet_uri or download_url))
    if not magnet and not data:
        raise RuntimeError("the release's download link gave no torrent")
    return magnet, data, torrent_info_hash(magnet, data)


async def _locate(client: QBittorrentClient, *, expected: str | None, before: set[str]) -> tuple[str, bool]:
    """The torrent's hash in qBittorrent, and whether it was there before this job added it."""
    for _ in range(30):
        rows = await client.list_torrents(category=None)
        if expected:
            exact = next((row.hash for row in rows if row.hash.casefold() == expected), None)
            if exact:
                return exact, exact in before
        added = [row.hash for row in rows if row.hash not in before]
        if len(added) == 1:
            return added[0], False
        await asyncio.sleep(1)
    raise QBittorrentError("could not find the torrent in qBittorrent")


async def download_direct(
    *,
    release_title: str,
    download_url: str | None,
    magnet_uri: str | None,
    info_hash: str | None,
    info_url: str | None,
    media_kind: str,
    root: str | None,
    title: str,
    year: int | None,
    season: int | None = None,
    roots: dict[str, str] | None = None,
    only: set[tuple[int, int]] | None = None,
    names: tuple[str, ...] = (),
    replace_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    if media_kind not in {"movie", "show"}:
        raise ValueError("kind must be movie or show")
    if not (download_url or magnet_uri):
        raise ValueError("the release has neither a download link nor a magnet")
    if not title.strip():
        raise ValueError("a title is needed to name the folder")
    by_language = {language: direct.target(path, media_kind) for language, path in (roots or {}).items() if path}
    if not by_language and not root:
        raise ValueError("a folder, or a folder per language, is needed")
    target = direct.target(root, media_kind) if root else None
    background_id = os.environ.get("BANKAI_BG_JOB_ID")
    if background_id:
        bgjobs.set_provenance(background_id, torrent_source_url=info_url or "", torrent_source_title=release_title)

    settings = get_settings()
    log.info('BANKAI_STAGE step=1 total=2 key=torrent label="Download"')
    qbit = QBittorrentClient()
    try:
        await qbit.login()
        before = {row.hash for row in await qbit.list_torrents(category=None)}
        magnet, torrent_file, found_hash = await _link(magnet_uri, download_url)
        expected = (info_hash or found_hash or "").casefold() or None
        save_path = Path(settings.qbittorrent.save_path) if settings.qbittorrent.save_path else None
        await qbit.add(
            magnet=magnet,
            torrent_file=None if magnet else torrent_file,
            category=CATEGORY,
            save_path=save_path,
        )
        torrent_hash, existed_before = await _locate(qbit, expected=expected, before=before)
        if background_id:
            torrent_actions.set_active_torrent(background_id, torrent_hash)
        await qbit.top_priority(torrent_hash)
        await qbit.resume(torrent_hash)
        await qbit.force_start(torrent_hash, enabled=True)
        try:
            status = await qbit.wait_until_complete(torrent_hash, progress_cb=_progress, stall_seconds=STALL_SECONDS)
        except TorrentStalled as exc:
            # Dead: never to be picked again; the torrent and what it fetched go.
            _reject(torrent_hash, release_title, f"stalled: {exc}")
            try:
                await qbit.remove(torrent_hash, delete_files=True)
            except Exception as remove_exc:
                log.warning("[direct] could not remove stalled torrent %s: %s", torrent_hash[:8], remove_exc)
            raise RuntimeError(f"{release_title}: stalled, {exc}; another release is looked for") from exc
        finally:
            try:
                await qbit.force_start(torrent_hash, enabled=False)
            except Exception as exc:
                log.warning("Could not restore qBittorrent queueing for %s: %s", torrent_hash, exc)
        # The active torrent stays recorded while its files are filed (cleared when the job
        # ends, below): the sweep leaves a torrent alone while a job still reads it.
        # Bankai's own (its category) goes once done with, even if an earlier attempt added it:
        # "was there before" alone kept every retry's torrent seeding (Modern Family, Oct 2026).
        ours = not existed_before or torrent_hash in {row.hash for row in await qbit.list_torrents(category=CATEGORY)}

        log.info(
            'BANKAI_STAGE step=2 total=2 key=organize label="Into %s"',
            target["label"] if target else " / ".join(row["label"] for row in by_language.values()),
        )
        content = _download_root(status)
        if media_kind == "movie":
            picked = pick_movie_file(content)
            sources = [picked] if picked else []
        else:
            sources = find_video_files(content)
        if not sources:
            raise RuntimeError(f"the torrent holds no video under {content}")
        if target:
            placed, left = direct.placements(
                sources, kind=media_kind, root=Path(target["root"]), title=title, year=year, season=season
            )
            replaces: dict[Path, list[Path]] = {}
        else:
            placed, left, replaces = _by_language(
                sources, release_title=release_title, kind=media_kind, by_language=by_language,
                title=title, year=year, season=season, only=only, names=names, replace_paths=replace_paths,
            )
        filed: list[Path] = []
        already: list[Path] = []
        replaced: list[Path] = []
        for index, (source, destination) in enumerate(placed):
            # An upgrade at the very name of the copy it replaces: that copy is moved aside
            # first, and back if the new one cannot be copied.
            in_the_way = [old for old in replaces.get(destination, []) if old == destination]
            if destination.exists() and not in_the_way:
                # Never over a file the library has: that one may be the German one.
                log.info("[direct] already there, left as it is: %s", destination)
                already.append(destination)
                continue
            aside = [(old, old.with_name(f".{old.name}.replaced")) for old in in_the_way]
            for old, parked in aside:
                old.rename(parked)
            share = 100 / max(1, len(placed))
            try:
                _copy_with_sidecars(source, destination, start_percent=index * share, end_percent=(index + 1) * share)
            except BaseException:
                for old, parked in aside:
                    parked.rename(old)
                raise
            log.info("[direct] %s -> %s", source.name, destination)
            subtitle = direct.english_subtitle_file(source)
            if subtitle is not None and subtitle.parent != source.parent:
                # From the release's Subs folder, which the copy above does not carry.
                beside = destination.with_name(f"{destination.stem}.en{subtitle.suffix.casefold()}")
                if not beside.exists():
                    shutil.copy2(subtitle, beside)
                    log.info("[direct] English subtitles: %s -> %s", subtitle.name, beside.name)
            filed.append(destination)
            for old, parked in aside:
                parked.unlink(missing_ok=True)
                log.info("[direct] replaced by a better picture: %s", old)
                replaced.append(old)
            for old in replaces.get(destination, []):
                if old == destination:
                    continue
                stop = next((Path(row["root"]) for row in by_language.values() if Path(row["root"]) in old.parents), old.parent)
                for gone in direct.remove_with_sidecars(old, stop=stop):
                    log.info("[direct] replaced, removed: %s", gone)
                replaced.append(old)
        if replace_paths and filed:
            # Replace with magnet link: the Dashboard's Recently added marks them "Replaced".
            from bankai.web import replaced as replaced_store

            replaced_store.record([path for path in filed if replaces.get(path)], release_title)
        for source, reason in left:
            log.info("[direct] not filed, %s: %s", reason, source.name)
        settled = any(reason.startswith(("the library has it", "not one of the episodes")) for _source, reason in left)
        if any(reason == NO_ENGLISH_SUBTITLES for _source, reason in left):
            # Not to be picked again for these episodes; with nothing of it filed, it goes.
            _reject(torrent_hash, release_title, NO_ENGLISH_SUBTITLES)
            if not filed and not already:
                await _remove(qbit, torrent_hash, "no English subtitles: ")
                raise RuntimeError(f"{release_title}: no English subtitles in the English episodes; another release is looked for")
        if not filed and not already and not settled:
            raise RuntimeError("no file of the torrent could be filed: " + "; ".join(f"{s.name} ({r})" for s, r in left[:5]))
        log.info('BANKAI_PROGRESS stage=organize pct=100 status="ready"')

        if settings.paths.cleanup_after_success and ours and (filed or already or settled):
            # The library holds its own name for the bytes (or a copy), or had the episodes
            # already: nothing more to take from the torrent, so it goes.
            await _remove(qbit, torrent_hash)
        return {
            "final_path": str((filed or already)[0]) if filed or already else "",
            "paths": [str(path) for path in filed],
            "already": [str(path) for path in already],
            "replaced": [str(path) for path in replaced],
            "not_filed": [{"name": source.name, "reason": reason} for source, reason in left],
            "torrent_hash": torrent_hash,
        }
    finally:
        if background_id:
            torrent_actions.clear_active_torrent(background_id)
        await qbit.aclose()


async def _remove(qbit: QBittorrentClient, torrent_hash: str, why: str = "") -> None:
    try:
        await qbit.remove(torrent_hash, delete_files=True)
        log.info("[direct] %sremoved torrent %s + files", why, torrent_hash[:8])
    except Exception as exc:
        log.warning("[direct] could not remove torrent %s: %s", torrent_hash[:8], exc)


# A finished torrent no job reads is left this long before the sweep removes it: time for a
# job that just found it to record it, and for one still running older code to file it.
SWEEP_GRACE_SECONDS = 2 * 3600


async def sweep(qbit: QBittorrentClient | None = None, *, now: float | None = None) -> list[str]:
    """Torrents of bankai's direct downloads that no running job reads: removed with their files.

    Finished ones after two hours of seeding: a job cut off (restart, deleted from the queue) or
    ended by a failure after the download left them seeding for good, and the next attempt took
    one up as "already there". Unfinished ones once they are two hours old: no job waits for
    them any more -- three Modern Family torrents of Oct 28 were still fetching their metadata
    two days later -- and the follow pass searches the episode again.
    """
    import time

    now = time.time() if now is None else now
    reading = {
        (torrent_actions.get_active_torrent(job.id) or "").casefold()
        for job in bgjobs.list_jobs()
        if job.status in {"running", "stopped"} and job.args and job.args[0] == "direct-download"
    }
    own = qbit is None
    client = qbit or QBittorrentClient()
    removed: list[str] = []
    try:
        await client.login()
        for row in await client.list_torrents(category=CATEGORY):
            if row.hash.casefold() in reading:
                continue
            if row.progress >= 1:
                if not row.completion_on or now - row.completion_on < SWEEP_GRACE_SECONDS:
                    continue
                why = "finished, no job reads it: "
            else:
                if not row.added_on or now - row.added_on < SWEEP_GRACE_SECONDS:
                    continue
                why = "not finished, and no job waits for it: "
            await _remove(client, row.hash, why)
            removed.append(row.name)
    finally:
        if own:
            await client.aclose()
    return removed


NO_ENGLISH_SUBTITLES = "no English subtitles"
# A download whose progress has not moved this long is given up (no metadata, no seeder).
STALL_SECONDS = 60 * 60


def _rejected_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "mas_rejected.json"


def _reject(info_hash: str | None, release_title: str, reason: str) -> None:
    """Remember a release not to pick again (web.mas_follow.pick skips it), with why."""
    import json
    import os
    import time

    if not info_hash:
        return
    path = _rejected_path()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = {}
    rows = rows if isinstance(rows, dict) else {}
    rows[info_hash.casefold()] = {"release": release_title, "reason": reason, "at": time.time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _by_language(
    sources: list[Path],
    *,
    release_title: str,
    kind: str,
    by_language: dict[str, dict[str, Any]],
    title: str,
    year: int | None,
    season: int | None,
    only: set[tuple[int, int]] | None,
    names: tuple[str, ...],
    replace_paths: tuple[Path, ...] = (),
) -> tuple[list[tuple[Path, Path]], list[tuple[Path, str]], dict[Path, list[Path]]]:
    """Each file into the folder of its audio's language, and the English-only copies it replaces.

    A German file replaces the English-only copies; an English one only copies of a lower
    picture class (Modern Family S08 in 720p by a 1080p release), often at its own name.
    ``replace_paths``: library files picked by hand to be replaced (Replace with magnet link):
    the release's file for their episode replaces them whatever their language or picture.
    """
    chosen: dict[tuple[int, int], list[Path]] = {}
    for path in replace_paths:
        if kind == "movie":
            chosen.setdefault((0, 0), []).append(path)
            continue
        numbers = direct.file_episode(path.name)
        if numbers:
            for episode in range(numbers[1], (numbers[2] or numbers[1]) + 1):
                chosen.setdefault((numbers[0], episode), []).append(path)
    library = direct.held(kind, title, year, names)
    placed: list[tuple[Path, Path]] = []
    left: list[tuple[Path, str]] = []
    replaces: dict[Path, list[Path]] = {}
    for source in sources:
        german = direct.has_german(source, release_title)
        language = "German" if german else "English"
        row = by_language.get(language) or next(iter(by_language.values()))
        # The show's folder as either library names it: the German folder may hold it under its German name.
        folder_title, folder_year = title, year
        existing = direct.existing_folder(Path(row["root"]), title, year, names)
        if existing is not None:
            dated = re.match(r"^(.*?)\s*\(((?:19|20)\d\d)\)\s*$", existing.name)
            folder_title, folder_year = (dated.group(1), int(dated.group(2))) if dated else (existing.name, year)
        found, skipped = direct.placements(
            [source], kind=kind, root=Path(row["root"]), title=folder_title, year=folder_year, season=season,
        )
        left.extend(skipped)
        for src, destination in found:
            if kind == "movie":
                keys = [(0, 0)]
            else:
                numbers = direct.file_episode(destination.name)
                keys = [(numbers[0], e) for e in range(numbers[1], (numbers[2] or numbers[1]) + 1)] if numbers else []
            if only is not None and kind != "movie" and not any(key in only for key in keys):
                left.append((src, "not one of the episodes wanted"))
                continue
            picked = [path for key in keys for path in chosen.get(key, [])]
            if picked:
                if not german and direct.has_english_subtitles(src) is not True:
                    left.append((src, NO_ENGLISH_SUBTITLES))
                    continue
                # Picked by hand to be replaced: whatever the library holds of it.
                placed.append((src, destination))
                replaces[destination] = list(dict.fromkeys(picked))
                continue
            if any(key in library["German"] for key in keys):
                left.append((src, "the library has it in German"))
                continue
            english = [path for key in keys for path in library["English"].get(key, [])]
            if not german and direct.has_english_subtitles(src) is not True:
                # English audio needs English subtitles; the release is passed over for another.
                left.append((src, NO_ENGLISH_SUBTITLES))
                continue
            if not german and english:
                # An English copy is replaced by a better picture (720p by 1080p), or by as good
                # a one when it has no English subtitles -- never by its like.
                new, old = direct.picture_rank(src), [direct.picture_rank(path) for path in english]
                lacking = any(direct.has_english_subtitles(path) is False for path in english)
                known = new is not None and all(rank is not None for rank in old)
                if not known or not (all(rank < new for rank in old) or (lacking and all(rank <= new for rank in old))):
                    left.append((src, "the library has it in English"))
                    continue
                placed.append((src, destination))
                replaces[destination] = english
                continue
            placed.append((src, destination))
            if german:
                replaces[destination] = [path for path in english if path != destination]
    return placed, left, replaces


__all__ = ["download_direct"]
