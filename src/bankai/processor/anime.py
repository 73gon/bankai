"""Direct Nyaa download path for anime movies and episode packs.

Unlike the dub pipeline, this worker does not extract, sync, transcode, or
remux.  It downloads the selected Nyaa torrent and copies its video files into
the normal TVDB/Jellyfin naming layout, marking them approved immediately.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bankai.backend.transfer import _copy2_with_progress
from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata.tvdb import TVDBClient, TVDBEpisode
from bankai.processor.naming import (
    render_anidb_episode_path,
    render_episode_path,
    render_movie_path,
)
from bankai.torrent import actions as torrent_actions
from bankai.torrent.matcher import find_video_files, parse_se, pick_movie_file
from bankai.torrent.qbittorrent import QBittorrentClient, QBittorrentError, TorrentStatus
from bankai.web import review

log = get_logger(__name__)

_SIDE_CAR_EXTS = {".ass", ".ssa", ".srt", ".sub", ".vtt"}
_ANIME_SEASON_EPISODE = re.compile(
    r"\bS(?P<season>\d{1,2})\s*[-_. ]+\s*(?:E(?:P)?\s*)?(?P<episode>\d{1,4})\b",
    flags=re.I,
)
_ANIME_EPISODE = re.compile(
    r"(?:\b(?:E|EP|Episode)\s*|\s+-\s+)(?P<episode>\d{1,4})(?:v\d+)?\b",
    flags=re.I,
)
_SEASON_HINT = re.compile(r"\b(?:S|Season\s*)(?P<season>\d{1,2})\b", flags=re.I)


@dataclass(frozen=True, slots=True)
class EpisodeIdentity:
    season: int
    episode: int
    title: str | None = None


def episode_identity(
    filename: str,
    *,
    release_title: str,
    tvdb_episodes: list[TVDBEpisode],
    season_override: int | None = None,
    episode_override: int | None = None,
) -> EpisodeIdentity | None:
    if episode_override is not None:
        if season_override is not None:
            matched = next(
                (
                    item
                    for item in tvdb_episodes
                    if item.season == season_override and item.episode == episode_override
                ),
                None,
            )
            return EpisodeIdentity(
                season_override,
                episode_override,
                matched.name if matched else None,
            )
        absolute = next(
            (item for item in tvdb_episodes if item.absolute_number == episode_override),
            None,
        )
        if absolute:
            return EpisodeIdentity(absolute.season, absolute.episode, absolute.name)
        regular = [item for item in tvdb_episodes if item.season > 0]
        if 0 < episode_override <= len(regular):
            item = regular[episode_override - 1]
            return EpisodeIdentity(item.season, item.episode, item.name)
        return EpisodeIdentity(1, episode_override)

    explicit = parse_se(filename)
    if explicit:
        season, episode = explicit
        if season_override is not None:
            season = season_override
        matched = next(
            (item for item in tvdb_episodes if item.season == season and item.episode == episode),
            None,
        )
        return EpisodeIdentity(season, episode, matched.name if matched else None)

    anime_match = _ANIME_SEASON_EPISODE.search(filename)
    if anime_match:
        season = int(anime_match.group("season"))
        episode = int(anime_match.group("episode"))
        if season_override is not None:
            season = season_override
        matched = next(
            (item for item in tvdb_episodes if item.season == season and item.episode == episode),
            None,
        )
        return EpisodeIdentity(season, episode, matched.name if matched else None)

    episode_match = _ANIME_EPISODE.search(Path(filename).stem)
    if not episode_match:
        return None
    number = int(episode_match.group("episode"))
    season_match = _SEASON_HINT.search(release_title)
    if season_override is not None or season_match:
        season = (
            season_override if season_override is not None else int(season_match.group("season"))
        )
        matched = next(
            (item for item in tvdb_episodes if item.season == season and item.episode == number),
            None,
        )
        if matched:
            return EpisodeIdentity(season, number, matched.name)
        # Many anime keep absolute numbering in season-labelled Nyaa
        # releases (for example S02 - 29). Prefer a TVDB absolute match when
        # there is no such season-relative episode.
        absolute = next(
            (item for item in tvdb_episodes if item.absolute_number == number),
            None,
        )
        if absolute:
            return EpisodeIdentity(absolute.season, absolute.episode, absolute.name)
        return EpisodeIdentity(season, number)

    absolute = next((item for item in tvdb_episodes if item.absolute_number == number), None)
    if absolute:
        return EpisodeIdentity(absolute.season, absolute.episode, absolute.name)

    # Some TVDB series omit absoluteNumber.  The default-order episode list is
    # still deterministic, so use its non-special ordinal as a final mapping.
    regular = [item for item in tvdb_episodes if item.season > 0]
    if 0 < number <= len(regular):
        item = regular[number - 1]
        return EpisodeIdentity(item.season, item.episode, item.name)
    return EpisodeIdentity(1, number)


def anidb_episode_number(filename: str, *, episode_override: int | None = None) -> int | None:
    """The AniDB episode a file is: its own number, as Erai numbers per AniDB entry."""
    if episode_override is not None:
        return episode_override
    match = _ANIME_EPISODE.search(Path(filename).stem)
    return int(match.group("episode")) if match else None


def _organize_anidb(
    sources: list[Path],
    *,
    title: str,
    episode_override: int | None,
    library: Path,
    require_german_subtitles: bool,
    replace_existing: bool,
) -> list[Path]:
    """Stage each video under its AniDB folder, with no TVDB lookup at all."""
    if episode_override is not None and len(sources) != 1:
        raise RuntimeError(
            "a manual episode override requires a torrent containing exactly one video file"
        )
    outputs: list[Path] = []
    total = max(1, len(sources))
    for index, source in enumerate(sources):
        if require_german_subtitles:
            _require_german_subtitles(source)
        episode = anidb_episode_number(source.name, episode_override=episode_override)
        if episode is None:
            log.warning("[anime] skipped file with no episode number: %s", source.name)
            continue
        destination = render_anidb_episode_path(
            library=library, title=title, episode=episode
        ).with_suffix(source.suffix.casefold())
        _copy_with_sidecars(
            source,
            destination,
            start_percent=index / total * 100.0,
            end_percent=(index + 1) / total * 100.0,
            replace=replace_existing,
        )
        outputs.append(destination)
    return outputs


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


def _has_german_subtitles(source: Path) -> bool:
    """Verify an embedded or adjacent German subtitle track after download."""

    for sidecar in source.parent.glob(f"{source.stem}.*"):
        if sidecar.suffix.casefold() not in _SIDE_CAR_EXTS:
            continue
        qualifier = sidecar.name[len(source.stem) :]
        words = set(re.findall(r"[a-z]+", qualifier.casefold()))
        if words & {"de", "deu", "ger", "german", "deutsch"}:
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


def _require_german_subtitles(source: Path) -> None:
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
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


async def _tvdb_episode_map(tvdb_id: int) -> list[TVDBEpisode]:
    settings = get_settings().metadata
    if not settings.tvdb_enabled or not settings.tvdb_api_key:
        return []
    client = TVDBClient(
        api_key=settings.tvdb_api_key,
        pin=settings.tvdb_pin,
        languages=["eng", "jpn"],
    )
    try:
        return await client.series_episodes(tvdb_id)
    finally:
        await client.aclose()


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


async def download_anime(
    *,
    release_title: str,
    torrent_url: str,
    detail_url: str,
    magnet_uri: str,
    info_hash: str,
    media_kind: str,
    tvdb_id: int,
    english_title: str,
    year: int | None,
    season_override: int | None = None,
    episode_override: int | None = None,
    anidb_id: int | None = None,
    anidb_title: str | None = None,
    require_german_subtitles: bool = False,
    cleanup_torrent: bool = False,
    replace_existing: bool = False,
) -> dict[str, Any]:
    if media_kind not in {"show", "movie"}:
        raise ValueError("anime kind must be show or movie")
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
        elif anidb_id:
            outputs.extend(
                _organize_anidb(
                    find_video_files(root),
                    title=anidb_title or english_title,
                    episode_override=episode_override,
                    library=output.directory,
                    require_german_subtitles=require_german_subtitles,
                    replace_existing=replace_existing,
                )
            )
        else:
            tvdb_episodes = await _tvdb_episode_map(tvdb_id)
            sources = find_video_files(root)
            if episode_override is not None and len(sources) != 1:
                raise RuntimeError(
                    "a manual episode override requires a torrent containing exactly one video file"
                )
            source_total = max(1, len(sources))
            for source_index, source in enumerate(sources):
                if require_german_subtitles:
                    _require_german_subtitles(source)
                identity = episode_identity(
                    source.name,
                    release_title=release_title,
                    tvdb_episodes=tvdb_episodes,
                    season_override=season_override,
                    episode_override=episode_override,
                )
                if identity is None:
                    log.warning("[anime] skipped file with no episode number: %s", source.name)
                    continue
                destination = render_episode_path(
                    library=output.directory,
                    query=english_title,
                    series_title=english_title,
                    season=identity.season,
                    episode=identity.episode,
                    episode_title=identity.title,
                    year_override=str(year) if year else None,
                    audio_lang="jpn",
                    include_year=True,
                    season_folder_template=output.season_folder_template,
                    file_template=output.series_filename_template,
                ).with_suffix(source.suffix.casefold())
                _copy_with_sidecars(
                    source,
                    destination,
                    start_percent=source_index / source_total * 100.0,
                    end_percent=(source_index + 1) / source_total * 100.0,
                    replace=replace_existing,
                )
                outputs.append(destination)
        if not outputs:
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

        if require_german_subtitles:
            from bankai.backend.transfer import plan_transfer, transfer_with_rsync

            log.info('BANKAI_STAGE step=3 total=3 key=transfer label="Transfer to Anime library"')
            # Publish external subtitles first: a failed subtitle copy must
            # never leave a newly published video without its verified track.
            items = await asyncio.to_thread(plan_transfer, outputs, kind="anime")
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
            outputs = [item.destination for item in [*result.transferred, *result.skipped]]

        owned_by_bankai = cleanup_torrent or require_german_subtitles or not existed_before
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
            "final_path": str(outputs[0]),
            "paths": [str(path) for path in outputs],
            "tvdb_id": tvdb_id or None,
            "anidb_id": anidb_id,
            "torrent_hash": torrent_hash,
        }
    finally:
        await qbit.aclose()


__all__ = ["EpisodeIdentity", "download_anime", "episode_identity"]
