"""Every torrent in bankai's anime category is some job's until its files are in the library.

qBittorrent keeps a torrent through any restart; what is to become of it is
written down beside it -- a job record (``--info-hash``, a running job's active
torrent), a queued job, or a release of the automation's table
(``erai_state.json``). When the job went -- deleted from the queue, tidied
away, lost -- the torrent stayed with nobody to file it: My Hero Academia
S07E05-08, whose failed jobs were tidied away as "duplicate" on Oct 29,
finished downloading that night and sat seeding with the library missing them.

So each pass of the release reconciler (``erai.reconcile_releases``, every
20 s from startup) reads the category from what is on disk -- qBittorrent's
list, the job records, the release table -- never from memory, and ties every
torrent nobody has (``plan``):

- finished, an episode it holds not in the library: the release table takes it
  up as "complete", and the same pass starts the job that files it -- the
  automation's publishing path, retried and tracked like any release;
- finished, every episode it holds in the library: the torrent goes with its
  files, as its job would have removed it (``paths.cleanup_after_success``);
- anything else -- still downloading with no owner, a release held, filtered
  or removed on purpose, a name that says no episode, a blacklisted show -- is
  left as it is and counted, for a look.

Deleting a job from the queue takes its torrent with it (``drop_torrent_of``),
unless something else still needs it: a deleted job is a download given up.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from bankai.logging import get_logger

log = get_logger(__name__)

# A finished torrent is taken up once it has sat this long with no owner: a job
# files and removes its own torrent within a minute or two of the download ending.
ADOPT_AFTER_SECONDS = 15 * 60
# A finished torrent whose episodes are all in the library goes after this, as
# processor.direct's sweep gives a finished torrent no job reads.
LEFTOVER_AFTER_SECONDS = 2 * 3600
# A release record that says nothing is left to do: while its torrent is still
# here and unfiled, the record is wrong, not the torrent. "duplicate" only says
# another release had claimed the episode -- not that the library has it.
STALE_STATES = frozenset({"", "duplicate", "done", "existing"})
# Releases the reconciler drives itself.
ACTIVE_STATES = frozenset({"queued", "downloading", "complete", "transferring", "deleting"})


@dataclass(frozen=True)
class Tie:
    """What becomes of one torrent nobody has."""

    info_hash: str
    title: str
    action: str  # "adopt" | "remove" | "leave"
    why: str
    pairs: tuple[tuple[int, int], ...] = field(default_factory=tuple)


def job_hashes(jobs: Iterable[Any] | None = None, pending: Iterable[Any] | None = None) -> set[str]:
    """The info hashes some job reads, whatever its state, and the queued jobs' too.

    A failed job still owns its torrent: it is in the queue, run again or kept
    for a look. A done one too: it decided about its torrent when it finished
    (a torrent that was the user's before it stays theirs).
    """
    from bankai.cli import bgjobs
    from bankai.torrent import actions

    found: set[str] = set()
    for job in bgjobs.list_jobs() if jobs is None else jobs:
        args = list(getattr(job, "args", None) or [])
        value = bgjobs.argument_value(args, "--info-hash")
        if value:
            found.add(value.casefold())
        if getattr(job, "status", "") in {"running", "stopped"}:
            active = actions.get_active_torrent(job.id)
            if active:
                found.add(active.casefold())
    if pending is None:
        from bankai.web import jobs as webjobs

        pending = webjobs._load_pending()
    for item in pending:
        value = bgjobs.argument_value(list(getattr(item, "args", None) or []), "--info-hash")
        if value:
            found.add(value.casefold())
    return found


def plan(
    torrents: Iterable[Any],
    releases: dict[str, dict[str, Any]],
    owned: set[str],
    *,
    identify: Callable[[str, dict[str, Any]], list[tuple[int, int]] | None],
    in_library: Callable[[set[int]], dict[int, set[int]]],
    blocked: set[int],
    cleanup: bool,
    now: float | None = None,
) -> list[Tie]:
    """What becomes of each torrent no job and no driven release has; owned ones are not listed.

    ``identify`` names a release's (AniList id, episode) pairs from its title
    and record, ``in_library`` each entry's episodes in the library.
    """
    now = time.time() if now is None else now
    ties: list[Tie] = []
    for torrent in torrents:
        key = str(torrent.hash).casefold()
        release = releases.get(key) or {}
        status = str(release.get("status") or "")
        if key in owned or status in ACTIVE_STATES:
            continue
        title = str(release.get("title") or getattr(torrent, "name", "") or key)
        if float(torrent.progress) < 1.0:
            ties.append(Tie(key, title, "leave", "not downloaded, and no job is waiting for it"))
            continue
        if status not in STALE_STATES:
            ties.append(Tie(key, title, "leave", f"its release is {status}"))
            continue
        pairs = identify(title, release)
        if not pairs:
            ties.append(Tie(key, title, "leave", "its name says no episode"))
            continue
        if any(aid in blocked for aid, _episode in pairs):
            ties.append(Tie(key, title, "leave", "the show is blacklisted"))
            continue
        finished = float(getattr(torrent, "completion_on", 0) or 0) or float(getattr(torrent, "added_on", 0) or 0)
        have = in_library({aid for aid, _episode in pairs})
        if all(episode in have.get(aid, set()) for aid, episode in pairs):
            if cleanup and now - finished >= LEFTOVER_AFTER_SECONDS:
                ties.append(Tie(key, title, "remove", "its episodes are in the library", tuple(pairs)))
            continue
        if now - finished < ADOPT_AFTER_SECONDS:
            continue
        ties.append(Tie(key, title, "adopt", "finished, and no job filed it", tuple(pairs)))
    return ties


def publishing_args(
    info_hash: str, title: str, *, anilist_id: int, name: str, episode: int | None
) -> list[str]:
    """A job filing a finished torrent into the library, as a download picked by hand files one.

    No German check: the release was picked on purpose, by the automation or by
    hand, when it was downloaded. The torrent is in qBittorrent already, so the
    magnet built from its hash re-adds nothing; the URLs only satisfy the check
    that it is Nyaa's. One entry without an episode is a batch: its files are
    numbered by their names.
    """
    args = [
        "anime-download",
        "--release-title", title,
        "--torrent-url", "https://nyaa.si/",
        "--detail-url", "https://nyaa.si/",
        "--magnet-uri", f"magnet:?xt=urn:btih:{info_hash}",
        "--info-hash", info_hash,
        "--kind", "show",
        "--anilist-id", str(anilist_id),
        "--anilist-title", name,
        "--english-title", name,
    ]
    if episode is not None:
        args += ["--episode", str(episode)]
    return [*args, "--publish"]


async def drop_torrent_of(args: list[str], *, job_id: str | None = None, active: str | None = None) -> str | None:
    """Remove the torrent of a job deleted from the queue, unless something else still needs it.

    The job's torrent is its ``--info-hash`` (anime) or the torrent it was
    reading (``active``: read before the job is stopped, which clears it).
    Kept when another job or a driven release reads it. Returns the name of
    the torrent removed, or None.
    """
    from bankai.cli import bgjobs
    from bankai.config import get_settings
    from bankai.torrent import actions
    from bankai.torrent.qbittorrent import QBittorrentClient
    from bankai.web import erai

    wanted = {
        value.casefold()
        for value in (
            bgjobs.argument_value(list(args), "--info-hash"),
            active or (actions.get_active_torrent(job_id) if job_id else None),
        )
        if value
    }
    if not wanted:
        return None
    others = job_hashes(job for job in bgjobs.list_jobs() if job.id != job_id)
    releases = erai.state_for_reading().get("releases") or {}
    driven = {key.casefold() for key, row in releases.items() if isinstance(row, dict) and row.get("status") in ACTIVE_STATES}
    wanted -= others | driven
    if not wanted:
        return None
    from bankai.processor import direct
    from bankai.torrent import worker

    qbit = QBittorrentClient()
    try:
        await qbit.login()
        # Only in bankai's own categories: a torrent of the user's stays theirs.
        for category in dict.fromkeys((get_settings().qbittorrent.category, direct.CATEGORY, worker.CATEGORY)):
            for torrent in await qbit.list_torrents(category=category):
                if torrent.hash.casefold() in wanted:
                    await qbit.remove(torrent.hash, delete_files=True)
                    log.info("Removed the torrent of a job deleted from the queue: %s", torrent.name)
                    return str(torrent.name)
    except Exception as exc:
        log.warning("Could not remove the torrent of a deleted job: %s", exc)
    finally:
        await qbit.aclose()
    return None


__all__ = [
    "ACTIVE_STATES",
    "ADOPT_AFTER_SECONDS",
    "LEFTOVER_AFTER_SECONDS",
    "STALE_STATES",
    "Tie",
    "drop_torrent_of",
    "job_hashes",
    "plan",
    "publishing_args",
]
