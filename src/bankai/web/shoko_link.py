"""Tell Shoko which AniDB episode each file bankai published is.

Shoko identifies files by hash against AniDB's file database, which knows
nothing of bankai's German-dub remuxes and not every Erai release either --
those sat in Shoko's "unrecognized" list, and so were missing from Jellyfin.
bankai knows exactly which AniDB anime and episode it filed each one as, so
it says so.

A file is only ever linked if bankai published it and Shoko has not identified
it by itself; one Shoko already recognised is left exactly as it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web import shoko

log = get_logger(__name__)

# A handful per round: each link is a few Shoko calls, and a series Shoko
# does not have yet costs it an AniDB request, which AniDB rate-limits.
BATCH = 25


def _candidates(state: dict[str, Any]) -> list[tuple[str, dict[str, Any], int, int, str]]:
    """Published AniDB releases Shoko has not been told about yet."""
    root = Path(get_settings().transfer.anime_shows_dir)
    rows = []
    for info_hash, release in state.get("releases", {}).items():
        if release.get("status") != "done" or release.get("shoko_linked"):
            continue
        canonical = str(release.get("canonical") or "")
        path = release.get("published_path")
        if not canonical.startswith("anidb:") or not path:
            continue
        head, _, number = canonical.partition("|")
        aid = head.split(":", 1)[1]
        if not aid.isdigit() or not number.isdigit():
            continue
        try:
            relative = Path(path).relative_to(root).as_posix()
        except ValueError:
            continue  # not in the library Shoko watches
        rows.append((info_hash, release, int(aid), int(number), relative))
    return rows


def _identified(file: dict[str, Any]) -> bool:
    return any(entry.get("EpisodeIDs") for entry in file.get("SeriesIDs") or [])


async def _import_folder_id() -> int | None:
    root = str(Path(get_settings().transfer.anime_shows_dir)).rstrip("/")
    for folder in await shoko._get("/api/v3/ImportFolder") or []:
        if str(folder.get("Path") or "").rstrip("/") == root:
            return int(folder["ID"])
    return None


async def scan_import_folder() -> bool:
    """Have Shoko look at the anime library for new, moved or removed files.

    Shoko cannot watch it itself: folder watching rests on Linux file
    notifications, which do not cross the 9p mount the library sits on, and
    Shoko has no timed scan. bankai already notices every change through the
    library tree, so it asks for a scan exactly when one happened.
    """
    if not shoko.configured():
        return False
    folder = await _import_folder_id()
    if folder is None:
        return False
    await shoko._send("GET", f"/api/v3/ImportFolder/{folder}/Scan")
    return True


async def link_published(state: dict[str, Any]) -> dict[str, int]:
    """One round of linking; marks each release it has settled.

    Returns counts by outcome. The state is changed in place; the caller saves.
    """
    tally = {"linked": 0, "already": 0, "waiting_for_scan": 0, "waiting_for_series": 0, "failed": 0}
    if not shoko.configured():
        return tally
    rescan = False
    requested_series: set[int] = set()
    for _info_hash, release, aid, episode, relative in _candidates(state)[:BATCH]:
        try:
            files = await shoko._get(
                "/api/v3/File/PathEndsWith", path=relative, includeXRefs="true", limit=2
            )
            if not files:
                # Not hashed yet; Shoko learns of it on a scan.
                rescan = True
                tally["waiting_for_scan"] += 1
                continue
            file = files[0]
            if _identified(file):
                release["shoko_linked"] = True
                tally["already"] += 1
                continue
            series = await shoko._send("GET", f"/api/v3/Series/AniDB/{aid}/Series")
            if not series:
                if aid not in requested_series:
                    # Shoko fetches the anime from AniDB, within its limits;
                    # the file is linked on a later round.
                    await shoko._send(
                        "POST",
                        f"/api/v3/Series/AniDB/{aid}/Refresh",
                        createSeriesEntry="true",
                        downloadRelations="false",
                    )
                    requested_series.add(aid)
                tally["waiting_for_series"] += 1
                continue
            await shoko._send(
                "POST",
                f"/api/v3/File/{file['ID']}/LinkFromSeries",
                # A one-episode range; Shoko requires both ends.
                json_body={
                    "SeriesID": series["IDs"]["ID"],
                    "RangeStart": str(episode),
                    "RangeEnd": str(episode),
                },
            )
            release["shoko_linked"] = True
            tally["linked"] += 1
        except Exception as exc:
            log.warning("Could not link %s in Shoko: %s", relative, exc)
            tally["failed"] += 1
    if rescan:
        await scan_import_folder()
    return tally
