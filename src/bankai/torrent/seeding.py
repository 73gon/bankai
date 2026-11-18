"""Private torrents seed until their tracker's rule is met, then go.

bankai removes a torrent once its files are filed. On a public tracker that is
fine; AvistaZ and AnimeZ (the AvistaZ network) count a torrent left before its seed time
(``required_hours``: 74 h for 1 GB, 472 h for 1000 GB) as a hit and run, and enough
of those close the account. So ``QBittorrentClient.remove`` hands every finished
private torrent whose seeding is not done to ``hold_unseeded``: it moves into
the category ``bankai-seeding`` (out of every category bankai tidies) with what
its remover asked for kept, and ``release_done`` -- a scheduler pass -- removes
it once the rule is met. Unfinished private torrents are removed at once: a hit
and run counts only what was downloaded whole.

Which torrents are private qBittorrent says itself (``private``), not a list
of tracker names.
"""

from __future__ import annotations

import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bankai.logging import get_logger

if TYPE_CHECKING:
    from bankai.torrent.qbittorrent import QBittorrentClient

log = get_logger(__name__)

CATEGORY = "bankai-seeding"
GIB = 1024**3


# The AvistaZ network's H&R table (the user's screenshot of the rules, Nov 2026): 72 h and 2 h per
# GB up to 50 GB (1 GB 74 h, 50 GB 172 h), then 100 * ln(GB) - 219 (60 GB 190 h, 1000 GB 472 h) --
# Prowlarr's own figures. Sizes in decimal GB (the larger count: never short), an hour's margin.
MARGIN_SECONDS = 3600


def required_hours(size_bytes: int) -> float:
    gb = max(0.0, size_bytes / 1e9)
    return 72 + 2 * gb if gb <= 50 else 100 * math.log(gb) - 219.2023


def required_seconds(size_bytes: int) -> int:
    """How long a private torrent seeds before it may go."""
    return math.ceil(required_hours(size_bytes) * 3600) + MARGIN_SECONDS


# The rules (AvistaZ network, updated 2026-10-21): clear once seeded the table's time OR 0.9 of
# what was downloaded uploaded back; a torrent downloaded under 10% may go at once; a partial one
# (10% or more) owes the time of the amount downloaded.
RATIO = 0.9
EXEMPT_FRACTION = 0.10


def downloaded_bytes(row: dict[str, Any]) -> int:
    """What the tracker counts as downloaded: never more than the torrent."""
    size = int(row.get("total_size") or row.get("size") or 0)
    got = int(row.get("downloaded") or 0) or int(size * float(row.get("progress") or 0))
    return min(got, size) if size else got


def owes(row: dict[str, Any]) -> bool:
    """A private torrent that would be a hit and run if it went now."""
    size = int(row.get("total_size") or row.get("size") or 0)
    return bool(row.get("private")) and size > 0 and downloaded_bytes(row) >= EXEMPT_FRACTION * size


def seeded_enough(row: dict[str, Any], *, held_at: float | None = None) -> bool:
    """The seed time of what was downloaded, or 0.9 of it uploaded. A finished torrent counts
    qBittorrent's seeding time; an unfinished one the time it has been held running."""
    got = downloaded_bytes(row)
    if got and int(row.get("uploaded") or 0) >= RATIO * got:
        return True
    finished = float(row.get("progress") or 0) >= 1.0
    seconds = int(row.get("seeding_time") or 0) if finished else (time.time() - held_at if held_at else 0)
    return seconds >= required_seconds(got)


def _path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "seeding_holds.json"


@contextmanager
def _locked():
    import fcntl

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, dict[str, Any]]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


async def hold_unseeded(client: QBittorrentClient, hashes: list[str]) -> list[str]:
    """Of these hashes, the ones to remove now; finished private torrents still owing
    their seeding are held instead. Unreadable: removed as asked (nothing worse than before)."""
    if not hashes:
        return hashes
    try:
        rows = await client.info(hashes)
    except Exception as exc:
        log.warning("could not read whether torrents are private: %s", exc)
        return hashes
    held = {str(row["hash"]).casefold(): row for row in rows if owes(row) and not seeded_enough(row)}
    if not held:
        return hashes
    try:
        await client.set_category(list(held), CATEGORY)
        # Kept running: a stopped torrent counts as gone. An unfinished one goes on downloading.
        await client.resume("|".join(held))
    except Exception as exc:
        log.warning("could not move private torrents aside to seed; removing them: %s", exc)
        return hashes
    with _locked() as path:
        data = load()
        for key, row in held.items():
            data[key] = {
                "name": row.get("name"),
                "held_at": time.time(),
                "from_category": row.get("category"),
                "size": int(row.get("total_size") or row.get("size") or 0),
                "downloaded": downloaded_bytes(row),
            }
        _save(path, data)
    for row in held.values():
        log.info(
            "[seeding] %s is private and has seeded %.1f h of %.1f h (ratio %.2f of 0.9): kept seeding, removed once done",
            row.get("name"), int(row.get("seeding_time") or 0) / 3600,
            required_seconds(downloaded_bytes(row)) / 3600, float(row.get("ratio") or 0),
        )
    return [item for item in hashes if item.casefold() not in held]


async def release_done(client: QBittorrentClient | None = None) -> list[str]:
    """Remove the held torrents whose seeding is done, with their files (the library has its own
    name for them). One taken out of the category by hand is forgotten, not removed."""
    from bankai.torrent.qbittorrent import QBittorrentClient

    own = client is None
    client = client or QBittorrentClient()
    removed: list[str] = []
    try:
        await client.login()
        rows = await client.list_torrents(category=CATEGORY)
        by_hash = {row.hash.casefold(): row for row in rows}
        details = {str(row["hash"]).casefold(): row for row in await client.info(list(by_hash))} if by_hash else {}
        holds = load()
        done = [
            key for key, row in details.items()
            if seeded_enough(row, held_at=(holds.get(key) or {}).get("held_at")) or not owes(row)
        ]
        if done:
            await client.remove("|".join(done), delete_files=True, force=True)
            removed = [str(details[key].get("name")) for key in done]
            log.info("[seeding] done seeding, removed: %s", ", ".join(removed))
        with _locked() as path:
            data = load()
            for key in list(data):
                if key in done or key not in by_hash:
                    data.pop(key, None)
            _save(path, data)
    finally:
        if own:
            await client.aclose()
    return removed


def listing() -> list[dict[str, Any]]:
    """What is held, for the Dashboard."""
    return [{"hash": key, **row} for key, row in sorted(load().items(), key=lambda item: item[1].get("held_at") or 0)]
