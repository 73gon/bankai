"""Direct Nyaa download path for anime movies and episode packs.

Unlike the dub pipeline, this worker does not extract, sync, transcode, or
remux.  It downloads the selected Nyaa torrent and files each video as an
episode of its AniList entry, where the library layout puts that episode
(see anime_layout), marking them approved immediately.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from bankai.backend.transfer import _copy2_with_progress
from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import anilist
from bankai.processor.naming import render_movie_path
from bankai.torrent import actions as torrent_actions
from bankai.torrent.matcher import find_video_files, pick_movie_file
from bankai.torrent.qbittorrent import QBittorrentClient, QBittorrentError, TorrentStatus
from bankai.web import anime_files, anime_layout, anime_unsorted, review

log = get_logger(__name__)

_SIDE_CAR_EXTS = {".ass", ".ssa", ".srt", ".sub", ".vtt"}
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm"}
# Polls of three seconds for a torrent's file list before episodes are left out.
_FILE_LIST_TRIES = 100
_ANIME_EPISODE = re.compile(
    r"(?:\b(?:E|EP|Episode)\s*|\s+-\s+)(?P<episode>\d{1,4})(?:v\d+)?\b",
    flags=re.I,
)
# "S01E46": the E follows a digit, so the pattern above never sees it.
_SEASON_EPISODE = re.compile(r"(?<![A-Za-z0-9])S(?P<season>\d{1,2})[._ -]?E(?P<episode>\d{1,4})(?:v\d+)?(?!\d)", flags=re.I)


def batch_override(episode_override: int | None, sources: list[Path]) -> int | None:
    """The episode override to apply, which a batch torrent does not take.

    Releases are queued with their own episode number -- a batch, a whole
    season in one torrent ("Show - 01 ~ 12"), under its first. One number
    cannot be every file, so publishing refused it, five times, and thirteen
    finished seasons sat in qBittorrent as failed. Erai names each file of a
    batch by its own episode, so each is numbered by its name instead.
    """
    if episode_override is not None and len(sources) > 1:
        log.info("[anime] batch of %d files: each numbered by its own name", len(sources))
        return None
    return episode_override


def episode_number(filename: str, *, episode_override: int | None = None) -> int | None:
    """The episode a file is: its own number, as Erai numbers per AniList entry."""
    if episode_override is not None:
        return episode_override
    marked = season_episode(filename)
    if marked is not None:
        # Season 0 is specials: another AniList entry, never this one's episode.
        return marked[1] if marked[0] > 0 else None
    match = _ANIME_EPISODE.search(Path(filename).stem)
    return int(match.group("episode")) if match else None


def season_episode(filename: str) -> tuple[int, int] | None:
    """(season, episode) of a name marked "S01E46", else None."""
    match = _SEASON_EPISODE.search(Path(filename).stem)
    return (int(match.group("season")), int(match.group("episode"))) if match else None


def _named_entry(filename: str) -> tuple[int, str] | None:
    """The AniList entry a file's own name says it is (id, name), or None when the name tells none.

    A replacement checks it before writing over anything: Re:Zero's second season, its batch taken
    for the first season, replaced the first season's thirteen episodes (Nov 2026).
    """
    from bankai.web import anime as anime_web, erai

    # A name without an episode number is no release name ("random.mkv" is AniList's "Random").
    if erai._erai_identity_name(filename) is None and anime_web.release_episode_info(filename)[1] is None:
        return None
    try:
        found = anime_web.anilist_match(filename, table=anilist.cached_index(), mappings=erai._load_mappings())
    except Exception as exc:
        log.warning("[anime] could not tell which entry %s is: %s", filename, exc)
        return None
    return (int(found["anilist_id"]), str(found.get("name") or found["anilist_id"])) if found else None


def _staged_path(key: str, suffix: str) -> Path:
    """Where a file of this layout key is staged, with the file's own suffix."""
    return anime_files.staging_root().joinpath(*PurePosixPath(key).parts).with_suffix(suffix.casefold())


async def _organize_show(
    sources: list[Path],
    *,
    anilist_id: int,
    episode_override: int | None,
    require_german_subtitles: bool,
    replace_existing: bool,
    keep_episodes: frozenset[int] = frozenset(),
    german_sub_url: str | None = None,
    leftover: list[tuple[Path, str]] | None = None,
    episode_map: dict[int, tuple[int, int]] | None = None,
    skip_owned: bool = False,
    owned_left: list[Path] | None = None,
) -> list[tuple[Path, int, int]]:
    """Stage each video where the layout puts its episode, and say so in the file index.

    Returns each staged file with the AniList entry and episode it is.
    A batch numbered straight through -- a second cour's first episode
    called "13" -- has each file past its entry's end settled into the
    sequel, as the automation settles a single release. ``keep_episodes``
    are left out: a replacement taken over a whole season must not publish
    over the episodes whose file carries a German dub. ``german_sub_url`` is
    the German file Erai-raws uploaded beside a single-episode release: it is
    staged next to the video as "<stem>.de.ass" before German is checked.

    A file that is no episode of the entry it can tell -- no number, or
    marked as another season ("S00E01" beside "S01E01".."S01E64": OVAs) --
    goes to ``leftover`` with the reason, for matching by hand in Review.
    Files marked with seasons are numbered by the lowest season past 0.
    ``skip_owned``: an episode the library has a file of is left out, not
    staged beside it (a download that is no replacement never writes over one).
    """
    if episode_override is not None and len(sources) != 1:
        raise RuntimeError(
            "a manual episode override requires a torrent containing exactly one video file"
        )
    # The entry, its relations and its sequels: what settling and the layout read.
    await anime_layout.ensure_metadata([anilist_id])
    entry = await anilist.anime(anilist_id)
    placed: list[tuple[Path, int, int]] = []
    owned: dict[int, set[int]] = {}
    marked = {source: season_episode(source.name) for source in sources}
    main = min((pair[0] for pair in marked.values() if pair and pair[0] > 0), default=None)
    for source in sources:
        pair = marked[source] if episode_override is None else None
        if pair is not None and pair[0] != main:
            reason = "a special (season 0)" if pair[0] == 0 else f"season {pair[0]}, beside season {main}"
            log.warning("[anime] %s is %s: not filed as an episode of this entry", source.name, reason)
            if leftover is not None:
                leftover.append((source, reason))
            continue
        episode = pair[1] if pair is not None else episode_number(source.name, episode_override=episode_override)
        if episode is None:
            log.warning("[anime] skipped file with no episode number: %s", source.name)
            if leftover is not None:
                leftover.append((source, "no episode number"))
            continue
        if episode in keep_episodes:
            log.info("[anime] episode %d kept as it is (German dub): %s", episode, source.name)
            continue
        if episode_map is not None:
            # Replace with magnet link: the torrent's episode N is the library's SxxE<N> that was
            # ticked, of whichever AniList entry that file is; the rest of the torrent stays out.
            if episode not in episode_map:
                log.info("[anime] episode %d was not ticked to be replaced: %s", episode, source.name)
                if leftover is not None:
                    leftover.append((source, "not ticked to be replaced"))
                continue
            placed.append((source, *episode_map[episode]))
            continue
        aid, number = anilist_id, episode
        if episode_override is None and entry is not None:
            settled, number = anilist.settle_in(entry, episode)
            aid = settled.id
            if aid != anilist_id:
                log.info("[anime] %s is episode %d of its sequel, AniList %d", source.name, number, aid)
        if replace_existing and episode_override is None:
            named = _named_entry(source.name)
            if named is not None and named[0] not in (anilist_id, aid):
                reason = f"its name is {named[1]}, not the entry it would replace"
                log.warning("[anime] %s: %s; nothing replaced with it", source.name, reason)
                if leftover is not None:
                    leftover.append((source, reason))
                continue
        if skip_owned:
            if aid not in owned:
                owned.update(anime_files.in_library([aid]))
            if number in owned[aid]:
                log.info("[anime] episode %d of AniList %d is in the library already, left out: %s", number, aid, source.name)
                if owned_left is not None:
                    owned_left.append(source)
                continue
        placed.append((source, aid, number))
    targets: dict[int, dict[int, str]] = {}
    for aid in dict.fromkeys(aid for _source, aid, _number in placed):
        numbers = sorted({number for _source, other, number in placed if other == aid})
        targets[aid] = await anime_layout.place_new(aid, numbers)
    outputs: list[tuple[Path, int, int]] = []
    total = max(1, len(placed))
    for index, (source, aid, number) in enumerate(placed):
        key = targets[aid].get(number)
        if not key:
            raise RuntimeError(f"the library layout has no place for AniList {aid} episode {number}")
        destination = _staged_path(key, source.suffix)
        if german_sub_url and len(placed) == 1:
            await _stage_german_sub(german_sub_url, destination)
        if require_german_subtitles:
            _require_german_subtitles(source, staged=destination)
        _copy_with_sidecars(
            source,
            destination,
            start_percent=index / total * 100.0,
            end_percent=(index + 1) / total * 100.0,
            replace=replace_existing,
        )
        anime_files.record(destination, [(aid, number)])
        outputs.append((destination, aid, number))
    return outputs


async def _leave_out_owned(
    qbit: QBittorrentClient, torrent_hash: str, *, anilist_id: int, episode_override: int | None
) -> tuple[list[str], int]:
    """Set the torrent's files of episodes the library has to "do not download".

    Numbered as ``_organize_show`` numbers them. Returns the names left out and
    the number of videos. Waits a few minutes for the file list (the torrent's
    metadata); without it everything downloads, and organizing leaves them out.
    """
    files: list[dict[str, Any]] = []
    for _ in range(_FILE_LIST_TRIES):
        try:
            files = await qbit.files(torrent_hash)
        except (httpx.HTTPError, QBittorrentError) as exc:
            log.debug("[anime] no file list for %s yet: %s", torrent_hash[:8], exc)
        except Exception as exc:
            log.warning("[anime] could not read the file list of %s: %s", torrent_hash[:8], exc)
            return [], 0
        if files:
            break
        await asyncio.sleep(3)
    videos = [row for row in files if Path(str(row.get("name") or "")).suffix.casefold() in _VIDEO_EXTS]
    if not videos:
        return [], 0
    if episode_override is not None and len(videos) != 1:
        episode_override = None
    await anime_layout.ensure_metadata([anilist_id])
    entry = await anilist.anime(anilist_id)
    marked = {row["name"]: season_episode(row["name"]) for row in videos} if episode_override is None else {}
    main = min((pair[0] for pair in marked.values() if pair and pair[0] > 0), default=None)
    owned: dict[int, set[int]] = {}
    left: list[str] = []
    for row in videos:
        name = str(row["name"])
        pair = marked.get(name)
        if pair is not None and pair[0] != main:
            continue
        number = pair[1] if pair is not None else episode_number(name, episode_override=episode_override)
        if number is None:
            continue
        aid = anilist_id
        if episode_override is None and entry is not None:
            settled, number = anilist.settle_in(entry, number)
            aid = settled.id
        if aid not in owned:
            owned.update(anime_files.in_library([aid]))
        if number in owned[aid]:
            left.append(name)
    # A subtitle beside a video left out ("<stem>.en.ass") goes with it.
    stems = {str(PurePosixPath(name).with_suffix("")) for name in left}
    names = set(left) | {
        str(row.get("name")) for row in files
        if Path(str(row.get("name"))).suffix.casefold() in _SIDE_CAR_EXTS
        and any(str(row.get("name")).startswith(stem + ".") for stem in stems)
    }
    indexes = [int(row["index"]) for row in files if str(row.get("name")) in names and "index" in row]
    if indexes and len(left) < len(videos):
        await qbit.file_priority(torrent_hash, indexes, 0)
    return sorted(names), len(videos)


def _left_out_paths(files: list[dict[str, Any]], status: TorrentStatus) -> set[Path]:
    """Files set to "do not download": qBittorrent may still have written part of one."""
    save = _mapped_path(status.save_path)
    return {save.joinpath(*PurePosixPath(str(row["name"])).parts) for row in files if row.get("priority") == 0}


def _current_paths(published: list[tuple[Path, int, int]]) -> list[Path]:
    """Where each published file is now: a re-layout may have renamed it.

    Followed by its episode in the file index, under the same root.
    """
    current: list[Path] = []
    for path, aid, episode in published:
        if path.exists():
            current.append(path)
            continue
        root = next(
            (base for base in (anime_files.anime_root(), anime_files.staging_root()) if path.is_relative_to(base)),
            None,
        )
        moved = [
            candidate
            for candidate in anime_files.on_disk(aid).get(episode, [])
            if root is None or candidate.is_relative_to(root)
        ]
        current.append(moved[0] if moved else path)
    return current


def _mapped_path(raw: str) -> Path:
    settings = get_settings().qbittorrent
    translated = raw
    for remote, local in settings.path_map.items():
        if translated.startswith(remote):
            translated = local + translated[len(remote) :]
            break
    return Path(translated)


def _download_root(status: TorrentStatus) -> Path:
    content = _mapped_path(status.content_path) if status.content_path else None
    if content is not None and content.exists():
        return content
    save = _mapped_path(status.save_path)
    named = save / status.name
    return named if named.exists() else save


def _copy_with_sidecars(
    source: Path,
    destination: Path,
    *,
    start_percent: float = 0.0,
    end_percent: float = 100.0,
    replace: bool = False,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # An upgrade is published over the episode it replaces, so skip_existing --
    # which exists to avoid re-copying work already done -- must not apply.
    if replace or not destination.exists() or not get_settings().output.skip_existing:
        _atomic_copy2(
            source,
            destination,
            progress=log.info,
            stage="organize",
            start_percent=start_percent,
            end_percent=end_percent,
        )
    for sidecar in source.parent.glob(f"{source.stem}.*"):
        if sidecar.suffix.casefold() not in _SIDE_CAR_EXTS:
            continue
        # Preserve language/format qualifiers such as `.de.forced.ass` rather
        # than collapsing every sidecar to the same `.ass` destination.
        qualifier = sidecar.name[len(source.stem) :]
        sidecar_target = destination.parent / f"{destination.stem}{qualifier}"
        if replace or not sidecar_target.exists() or not get_settings().output.skip_existing:
            _atomic_copy2(sidecar, sidecar_target)


def _german_sidecar(video: Path) -> bool:
    """Is a German subtitle file beside this video? Whole-word language tokens of its qualifier."""
    # Escaped: an Erai name's "[1080p]" is a pattern of its own to glob.
    for sidecar in video.parent.glob(f"{glob.escape(video.stem)}.*"):
        if sidecar.suffix.casefold() not in _SIDE_CAR_EXTS:
            continue
        qualifier = sidecar.name[len(video.stem) :]
        words = set(re.findall(r"[a-z]+", qualifier.casefold()))
        if words & {"de", "deu", "ger", "german", "deutsch"}:
            return True
    return False


async def _stage_german_sub(url: str, destination: Path) -> None:
    """Fetch German subtitles Erai-raws uploaded for the release next to its staged video.

    Published with it from there like any sidecar. A file not to be had is
    only logged: the German check, where the release needs one, says the rest.
    """
    from bankai.web import erai_subs

    target = erai_subs.sidecar_path(destination)
    if await erai_subs.download(url, target):
        log.info("[anime] German subtitles from Erai-raws staged as %s", target.name)
    else:
        log.warning("[anime] the German subtitles Erai-raws uploaded could not be fetched")


def _has_german_subtitles(source: Path) -> bool:
    """Verify an embedded or adjacent German subtitle track after download."""

    if _german_sidecar(source):
        return True
    from bankai.web.media import ffprobe_bin

    binary = ffprobe_bin()
    if binary is None:
        raise RuntimeError("ffprobe is required to verify German subtitles")
    result = subprocess.run(
        [binary, "-v", "error", "-show_streams", "-of", "json", str(source)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not inspect subtitles in {source.name}: {result.stderr.strip()}")
    try:
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid subtitle metadata for {source.name}") from exc
    german = {"de", "deu", "ger", "german", "deutsch"}
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags") or {}
        language = str(tags.get("language") or "").casefold()
        title_words = set(re.findall(r"[a-z]+", str(tags.get("title") or "").casefold()))
        if language in german or title_words & german:
            return True
    return False


def _require_german_subtitles(source: Path, *, staged: Path | None = None) -> None:
    """``staged``: where the video is staged, beside which German may have been put already."""
    if staged is not None and _german_sidecar(staged):
        return
    if not _has_german_subtitles(source):
        raise RuntimeError(
            f"German subtitle verification failed for {source.name}; file was not added to the library"
        )


def _atomic_copy2(
    source: Path,
    destination: Path,
    *,
    progress=None,
    stage: str = "organize",
    start_percent: float = 0.0,
    end_percent: float = 100.0,
) -> None:
    """Publish a completed anime file atomically.

    A direct ``copy2`` creates the final ``.mkv`` before the copy has
    finished, allowing the Library scanner and Transfer action to open a file
    that is still locked by the organizer.  A non-media ``.part`` name keeps
    it invisible until the final replace.
    """

    tmp = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    try:
        tmp.unlink(missing_ok=True)
        try:
            # Downloads and staging share a volume: a second name for the same
            # bytes is instant and needs no free space, where a copy needs the
            # whole file again and fails outright on a full disk. The torrent
            # goes on seeding the same bytes; removing it later leaves this name.
            os.link(source, tmp)
        except OSError:
            if progress is None:
                shutil.copy2(source, tmp)
            else:
                _copy2_with_progress(
                    source,
                    tmp,
                    progress=progress,
                    stage=stage,
                    start_percent=start_percent,
                    end_percent=end_percent,
                    copier=shutil.copy2,
                )
        else:
            if progress is not None:
                progress(f"BANKAI_PROGRESS stage={stage} pct={end_percent:.1f} status=linked")
        tmp.replace(destination)
        # Dated when it joined the library, not when it was downloaded (a link
        # or copy keeps that): "recently added" goes by this, and a download
        # that waited weeks to be published is still new to the library.
        with suppress(OSError):
            os.utime(destination)
    finally:
        tmp.unlink(missing_ok=True)


async def _locate_torrent(
    client: QBittorrentClient,
    *,
    info_hash: str,
    before_hashes: set[str],
) -> str:
    normalized = info_hash.casefold()
    for _ in range(30):
        rows = await client.list_torrents(category=None)
        exact = next((item.hash for item in rows if item.hash.casefold() == normalized), None)
        if exact:
            return exact
        added = [item.hash for item in rows if item.hash not in before_hashes]
        if len(added) == 1:
            return added[0]
        await asyncio.sleep(1)
    raise QBittorrentError("could not locate the Nyaa torrent in qBittorrent")


def _progress(status: TorrentStatus) -> None:
    log.info(
        "BANKAI_PROGRESS stage=torrent pct=%.1f speed=%d eta=%d",
        status.progress * 100,
        status.dlspeed,
        status.eta,
    )


async def publish_staged(outputs: list[Path], *, replace_existing: bool = False) -> dict[Path, Path]:
    """Move staged anime files into the library, their subtitle sidecars with them.

    Returns each moved file's new path by its staged one. ``replace_existing``
    publishes over a file already at the destination (an upgrade). Raises when
    any file could not be moved.
    """
    from bankai.backend.transfer import plan_transfer, transfer_with_rsync

    # Publish external subtitles first: a failed subtitle copy must
    # never leave a newly published video without its verified track.
    items = await asyncio.to_thread(plan_transfer, outputs, kind="anime")
    if replace_existing:
        # An upgrade is published over the episode it replaces: the
        # layout puts it at the same place, where Transfer would
        # otherwise skip it as already there. The staged copy stays
        # until it has moved, so a failed move loses nothing.
        for item in items:
            with suppress(OSError):
                item.destination.unlink(missing_ok=True)
    for item in items:
        for sidecar in item.source.parent.glob(f"{item.source.stem}.*"):
            if sidecar.suffix.casefold() not in _SIDE_CAR_EXTS:
                continue
            qualifier = sidecar.name[len(item.source.stem) :]
            target = item.destination.parent / f"{item.destination.stem}{qualifier}"
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_copy2(sidecar, target)
    result = await asyncio.to_thread(
        transfer_with_rsync,
        outputs,
        kind="anime",
        progress=log.info,
    )
    if result.failed:
        raise RuntimeError(
            "Anime transfer failed: " + "; ".join(error for _, error in result.failed)
        )
    for item in [*result.transferred, *result.skipped]:
        # Keep separately downloaded German subtitle sidecars with
        # their video. The normal video transfer only plans videos.
        for sidecar in item.source.parent.glob(f"{item.source.stem}.*"):
            if sidecar.suffix.casefold() not in _SIDE_CAR_EXTS:
                continue
            qualifier = sidecar.name[len(item.source.stem) :]
            target = item.destination.parent / f"{item.destination.stem}{qualifier}"
            if not target.exists():
                _atomic_copy2(sidecar, target)
            if target.exists() and target.stat().st_size == sidecar.stat().st_size:
                sidecar.unlink()
        review.set_stage(item.source, "transferred")
        review.set_transfer(item.source, "done", percent=100)
    return {item.source: item.destination for item in [*result.transferred, *result.skipped]}


async def download_anime(
    *,
    release_title: str,
    torrent_url: str,
    detail_url: str,
    magnet_uri: str,
    info_hash: str,
    media_kind: str,
    english_title: str,
    year: int | None,
    episode_override: int | None = None,
    anilist_id: int | None = None,
    anilist_title: str | None = None,
    require_german_subtitles: bool = False,
    cleanup_torrent: bool = False,
    replace_existing: bool = False,
    keep_episodes: frozenset[int] = frozenset(),
    publish: bool = False,
    german_sub_url: str | None = None,
    episode_map: dict[int, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    if media_kind not in {"show", "movie"}:
        raise ValueError("anime kind must be show or movie")
    if media_kind == "show" and not anilist_id:
        raise ValueError("an anime show is filed by its AniList entry: --anilist-id is required")
    if not info_hash or not re.fullmatch(r"[0-9a-fA-F]{40}", info_hash):
        raise ValueError("Nyaa info hash is invalid")

    background_id = os.environ.get("BANKAI_BG_JOB_ID")
    if background_id:
        bgjobs.set_provenance(
            background_id,
            torrent_source_url=detail_url,
            torrent_source_title=release_title,
        )

    settings = get_settings()
    # Reuse the configured category. qBittorrent rejects or silently drops an
    # unknown category, and Nyaa provenance is tracked on the bankai job/file.
    category = settings.qbittorrent.category
    total_steps = 3 if require_german_subtitles else 2
    log.info('BANKAI_STAGE step=1 total=%d key=torrent label="Download from Nyaa"', total_steps)
    qbit = QBittorrentClient()
    torrent_hash: str | None = None
    existed_before = False
    try:
        await qbit.login()
        before = await qbit.list_torrents(category=None)
        before_hashes = {item.hash for item in before}
        existed_before = info_hash.casefold() in {item.hash.casefold() for item in before}
        save_path = Path(settings.qbittorrent.save_path) if settings.qbittorrent.save_path else None
        await qbit.add(
            magnet=magnet_uri or None,
            torrent_url=None if magnet_uri else torrent_url,
            category=category,
            save_path=save_path,
        )
        torrent_hash = await _locate_torrent(qbit, info_hash=info_hash, before_hashes=before_hashes)
        if background_id:
            torrent_actions.set_active_torrent(background_id, torrent_hash)
        # Episodes the library has are not downloaded again; a replacement takes them on purpose.
        skip_owned = media_kind == "show" and not replace_existing and episode_map is None
        if skip_owned:
            left_out, videos = await _leave_out_owned(
                qbit, torrent_hash, anilist_id=int(anilist_id), episode_override=episode_override
            )
            if left_out and len([name for name in left_out if Path(name).suffix.casefold() in _VIDEO_EXTS]) >= videos:
                log.info("[anime] every episode of %s is in the library already: nothing downloaded", release_title)
                if not existed_before:
                    with suppress(Exception):
                        await qbit.remove(torrent_hash, delete_files=True)
                return {
                    "final_path": None,
                    "paths": [],
                    "kept_for_review": 0,
                    "anilist_id": anilist_id,
                    "torrent_hash": torrent_hash,
                    "already_in_library": videos,
                }
            if left_out:
                log.info("[anime] %d file(s) the library has are not downloaded: %s", len(left_out), ", ".join(left_out[:5]))
        # The discovery prefill deliberately adds a large ordered backlog to
        # qBittorrent. A worker must promote its own torrent before waiting;
        # otherwise errored entries at the head of qBittorrent's queue can
        # occupy every download slot while all Bankai workers wait behind it.
        await qbit.top_priority(torrent_hash)
        await qbit.resume(torrent_hash)
        await qbit.force_start(torrent_hash, enabled=True)
        try:
            status = await qbit.wait_until_complete(torrent_hash, progress_cb=_progress)
        finally:
            try:
                # A completed prefilled torrent may remain for seeding. Return
                # it to qBittorrent's normal upload limits when this worker no
                # longer needs an immediate download slot.
                await qbit.force_start(torrent_hash, enabled=False)
            except Exception as exc:
                log.warning("Could not restore qBittorrent queueing for %s: %s", torrent_hash, exc)
        if background_id:
            torrent_actions.clear_active_torrent(background_id)

        log.info('BANKAI_STAGE step=2 total=%d key=organize label="Organize"', total_steps)
        root = _download_root(status)
        outputs: list[Path] = []
        kept_for_review = 0
        # Each show file with the AniList entry and episode it is.
        episodes: list[tuple[Path, int, int]] = []
        # Episodes the library had, left out while filing.
        owned_left: list[Path] = []
        output = settings.output
        if media_kind == "movie":
            source = pick_movie_file(root)
            if source is None:
                raise RuntimeError(f"the Nyaa torrent contains no movie file under {root}")
            if require_german_subtitles:
                _require_german_subtitles(source)
            destination = render_movie_path(
                library=output.directory,
                query=english_title,
                title_override=english_title,
                year_override=str(year) if year else None,
                audio_lang="jpn",
                folder_template=output.movie_folder_template,
                file_template=output.filename_template,
            ).with_suffix(source.suffix.casefold())
            _copy_with_sidecars(source, destination, replace=replace_existing)
            outputs.append(destination)
        else:
            sources = find_video_files(root)
            if skip_owned:
                try:
                    skipped = _left_out_paths(await qbit.files(torrent_hash), status)
                except Exception as exc:
                    log.warning("[anime] could not read which files were left out: %s", exc)
                    skipped = set()
                sources = [source for source in sources if source not in skipped]
            log.info("[anime] filing as AniList %d: %s", anilist_id, anilist_title or english_title)
            leftover: list[tuple[Path, str]] = []
            episodes = await _organize_show(
                sources,
                anilist_id=int(anilist_id),
                episode_override=batch_override(episode_override, sources),
                require_german_subtitles=require_german_subtitles,
                replace_existing=replace_existing,
                keep_episodes=keep_episodes,
                german_sub_url=german_sub_url,
                leftover=leftover,
                episode_map=episode_map,
                skip_owned=skip_owned,
                owned_left=owned_left,
            )
            outputs.extend(path for path, _aid, _episode in episodes)
            # Left out on purpose (Replace with magnet link): nothing to match by hand.
            leftover = [(source, reason) for source, reason in leftover if reason != "not ticked to be replaced"]
            for source, reason in leftover:
                pair = season_episode(source.name)
                anime_unsorted.add(
                    source,
                    base=root,
                    reason=reason,
                    release_title=release_title,
                    detail_url=detail_url,
                    info_hash=info_hash,
                    anilist_id=int(anilist_id),
                    season=pair[0] if pair else None,
                    episode=pair[1] if pair else episode_number(source.name),
                )
            if leftover:
                log.info("[anime] %d file(s) wait in Anime -> Review to be matched by hand", len(leftover))
            kept_for_review = len(leftover)
        if not outputs and not kept_for_review and owned_left:
            log.info("[anime] every episode of %s is in the library already: nothing filed", release_title)
            if settings.paths.cleanup_after_success and not existed_before:
                with suppress(Exception):
                    await qbit.remove(torrent_hash, delete_files=True)
            return {
                "final_path": None,
                "paths": [],
                "kept_for_review": 0,
                "anilist_id": anilist_id,
                "torrent_hash": torrent_hash,
                "already_in_library": len(owned_left),
            }
        if not outputs and not kept_for_review:
            raise RuntimeError("the selected Nyaa release contained no recognizable anime files")

        for destination in outputs:
            review.set_stage(
                destination,
                "approved",
                note="Downloaded directly from Nyaa; audio synchronization is not required.",
            )
            review.set_sources(
                destination,
                torrent_source_url=detail_url,
                torrent_source_title=release_title,
            )
        log.info('BANKAI_PROGRESS stage=organize pct=100 status="ready"')

        # The automation's downloads go on into the library, and so does a
        # show picked by hand (``publish``: the Anime search page, the
        # library's drawer). Without it the files wait in staging for Transfer.
        if require_german_subtitles or publish:
            log.info('BANKAI_STAGE step=3 total=3 key=transfer label="Transfer to Anime library"')
            moved = await publish_staged(outputs, replace_existing=replace_existing and media_kind == "show")
            outputs = list(moved.values())
            episodes = [(moved[path], aid, episode) for path, aid, episode in episodes if path in moved]

        if episodes:
            # Its collection laid out again: NFO files for the new episodes,
            # and any renumbering their arrival brings.
            collections = {
                cid for aid in {aid for _path, aid, _episode in episodes}
                if (cid := anime_layout.collection_of_entry(aid)) is not None
            }
            try:
                await anime_layout.relayout(collections)
            except Exception as exc:
                # Published either way; the next layout pass catches up.
                log.warning("[anime] could not lay out the collection: %s", exc)
            outputs = _current_paths(episodes)

        # A download published into the library -- picked in its drawer or its
        # missing-episodes dialog -- is bankai's too, even when its torrent was
        # already in qBittorrent: two Liar Game episodes, queued again over
        # their own unfinished torrents, were left seeding after publishing.
        owned_by_bankai = cleanup_torrent or require_german_subtitles or publish or not existed_before
        if settings.paths.cleanup_after_success and torrent_hash and owned_by_bankai:
            # Autonomous Erai releases are deliberately prefilled, so they
            # already exist when their worker starts. Delete their qBittorrent
            # copy only after the final library publication above succeeded.
            # A manually pre-existing torrent remains user-owned.
            try:
                await qbit.remove(torrent_hash, delete_files=True)
                log.info("[anime] removed torrent %s + files", torrent_hash[:8])
            except Exception as exc:
                log.warning("[anime] could not remove torrent %s: %s", torrent_hash[:8], exc)
        return {
            "final_path": str(outputs[0]) if outputs else None,
            "paths": [str(path) for path in outputs],
            "kept_for_review": kept_for_review,
            "anilist_id": anilist_id,
            "torrent_hash": torrent_hash,
        }
    finally:
        await qbit.aclose()


__all__ = ["download_anime", "episode_number", "publish_staged", "season_episode"]
