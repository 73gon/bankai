"""The dashboard: who is working on what, what is due next, what arrived lately.

Pure assembly: the app gathers the parts -- queue rows, the automation's
heartbeat, the scheduler's passes, qBittorrent's torrents, the library walk --
and this turns them into lanes of workers, a list of what is due, and the
recent additions to both libraries.

Workers come in lanes, each with its own limit:

* Pipelines -- movie and show jobs, ``web.max_concurrent_jobs`` at a time.
* Anime publishing -- finished downloads moved into the library,
  ``anime.max_concurrent_transfers`` at a time.
* Transfers -- copies, repacks and torrent swaps; no limit.
* Anime automation -- the one cycle that finds and queues releases.
* Scheduler -- the web server's periodic passes, one after another.
* qBittorrent -- its active download slots.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from bankai.torrent.matcher import parse_se

# Files of one show that arrived within this long of its newest file count as
# a single addition: "Frieren -- 4 new episodes", not four rows.
RECENT_WINDOW_SECONDS = 3 * 86400
RECENT_LIMIT = 12
# Each lane's list of what is due stops here, with a count of the rest.
DUE_LIMIT = 8
_RUNNING = {"running", "stopped"}
# States in which qBittorrent holds a torrent in one of its download slots.
_DOWNLOADING = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "stalledDL"}


def _task(
    title: str,
    *,
    detail: str | None = None,
    percent: float | None = None,
    started_at: float | None = None,
    href: str | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "detail": detail,
        "percent": None if percent is None else max(0.0, min(100.0, float(percent))),
        "started_at": started_at,
        "href": href,
    }


def _job_task(row: dict[str, Any], href: str) -> dict[str, Any]:
    return _task(
        str(row.get("title") or row.get("id") or "Job"),
        detail=row.get("step_label") or row.get("phase") or row.get("status"),
        percent=row.get("overall_percent"),
        started_at=row.get("started_at"),
        href=href,
    )


def _slots(prefix: str, name: str, capacity: int, tasks: list[dict]) -> list[dict]:
    """``capacity`` numbered workers, busy with ``tasks`` in turn, then idle.

    More tasks than slots happens -- a stopped job keeps its slot, a limit
    lowered while jobs ran -- and each extra one is shown as a worker too.
    """
    count = max(capacity, len(tasks))
    return [
        {
            "id": f"{prefix}-{index + 1}",
            "name": f"{name} {index + 1}",
            "busy": index < len(tasks),
            "task": tasks[index] if index < len(tasks) else None,
        }
        for index in range(count)
    ]


def _lane(
    key: str,
    label: str,
    description: str,
    workers: list[dict],
    *,
    capacity: int | None,
    state: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "description": description,
        "capacity": capacity,
        "busy": sum(1 for worker in workers if worker["busy"]),
        "workers": workers,
        "state": state,
    }


def _speed(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}/s" if unit == "B" else f"{value:.1f} {unit}/s"
        value /= 1024
    return f"{value:.1f} GiB/s"


def _group(
    lane: str, label: str, items: list[dict], *, limit: int = DUE_LIMIT
) -> dict[str, Any]:
    return {"lane": lane, "label": label, "items": items[:limit], "more": max(0, len(items) - limit)}


def build(
    *,
    mas_rows: list[dict],
    anime_rows: list[dict],
    operations: list[dict],
    automation: dict[str, Any],
    passes: list[dict],
    torrents: list[dict] | None,
    download_slots: int | None,
    pipeline_slots: int,
    publish_slots: int,
    retry_pending: int | None,
    recent: dict[str, Any],
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now

    # -- Pipelines -----------------------------------------------------------
    pipeline_jobs = [
        _job_task(row, "/mas/queue")
        for row in mas_rows
        if row.get("status") in _RUNNING and not row.get("pending")
    ]
    pipelines = _lane(
        "pipelines",
        "Pipelines",
        "Movie and show jobs: extract, torrent, sync, remux.",
        _slots("pipeline", "Slot", max(1, pipeline_slots), pipeline_jobs),
        capacity=max(1, pipeline_slots),
    )

    # -- Anime publishing ----------------------------------------------------
    publish_jobs = [
        _job_task(row, "/a/queue")
        for row in anime_rows
        if row.get("status") in _RUNNING and not row.get("pending")
    ]
    publishing = _lane(
        "publishing",
        "Anime publishing",
        "Finished downloads checked and moved into the anime library.",
        _slots("publish", "Slot", max(1, publish_slots), publish_jobs),
        capacity=max(1, publish_slots),
    )

    # -- Transfers -----------------------------------------------------------
    transfers = _lane(
        "transfers",
        "Transfers",
        "Copies to the server, repacks and torrent swaps. No limit.",
        [
            {
                "id": f"transfer-{op['id']}",
                "name": str(op.get("kind") or "transfer").replace("_", " ").capitalize(),
                "busy": True,
                "task": _task(str(op.get("title") or op["id"]), started_at=op.get("started_at")),
            }
            for op in operations
        ],
        capacity=None,
    )

    # -- Anime automation ----------------------------------------------------
    activity = automation.get("activity") or {}
    alive = bool(automation.get("alive", True))
    running = bool(automation.get("running")) and alive
    done, total = activity.get("done"), activity.get("total")
    cycle_task = None
    if running:
        phase = str(activity.get("phase") or "Running a cycle")
        if done is not None and total:
            phase = f"{phase} ({done} of {total})"
        cycle_task = _task(
            phase,
            detail=activity.get("current"),
            percent=100.0 * done / total if done is not None and total else None,
            started_at=activity.get("cycle_started_at"),
            href="/a/settings",
        )
    automation_lane = _lane(
        "automation",
        "Anime automation",
        "Finds new Erai-raws releases and queues them in qBittorrent.",
        [{"id": "automation", "name": "Cycle", "busy": running, "task": cycle_task}],
        capacity=1,
        state=None if alive else "down",
    )

    # -- Scheduler -----------------------------------------------------------
    running_passes = [row for row in passes if row.get("running")]
    scheduler = _lane(
        "scheduler",
        "Scheduler",
        "The web server's periodic passes.",
        [
            {
                "id": "scheduler",
                "name": "Passes",
                "busy": bool(running_passes),
                "task": _task(
                    " · ".join(str(row["label"]) for row in running_passes),
                    started_at=min(
                        (row["last_run"] for row in running_passes if row.get("last_run")),
                        default=None,
                    ),
                )
                if running_passes
                else None,
            }
        ],
        capacity=1,
    )

    # -- qBittorrent ---------------------------------------------------------
    downloading = sorted(
        (row for row in torrents or [] if row.get("state") in _DOWNLOADING),
        key=lambda row: -float(row.get("dlspeed") or 0),
    )
    download_tasks = [
        _task(
            str(row.get("name") or row.get("hash")),
            detail=(
                "Fetching metadata"
                if "meta" in str(row.get("state"))
                else "Stalled, no peers sending"
                if row.get("state") == "stalledDL"
                else _speed(float(row.get("dlspeed") or 0))
            ),
            percent=100.0 * float(row.get("progress") or 0),
            started_at=row.get("added_on"),
            href="/qbittorrent",
        )
        for row in downloading
    ]
    downloads = _lane(
        "downloads",
        "qBittorrent",
        "Active download slots; the rest of its queue waits for one.",
        _slots("download", "Slot", download_slots or len(download_tasks), download_tasks)
        if download_slots or download_tasks
        else [],
        capacity=download_slots,
        state=None if torrents is not None else "down",
    )

    lanes = [pipelines, publishing, transfers, automation_lane, scheduler, downloads]

    # -- Due -----------------------------------------------------------------
    queued_jobs = sorted(
        (row for row in mas_rows if row.get("pending")),
        key=lambda row: (row.get("queue_position") or 0, row.get("started_at") or 0),
    )
    releases = [row for row in anime_rows if row.get("pending")]
    by_phase: dict[str, list[dict]] = {}
    for row in releases:
        by_phase.setdefault(str(row.get("phase") or "queued"), []).append(row)
    ready = sorted(by_phase.get("complete", []), key=lambda row: row.get("updated_at") or 0)
    waiting = len(by_phase.get("queued", [])) + len(by_phase.get("downloading", []))
    queued_torrents = sum(1 for row in torrents or [] if row.get("state") == "queuedDL")

    due = [
        _group(
            "pipelines",
            "Movie and show jobs waiting for a slot",
            [
                {
                    "title": row.get("title"),
                    "detail": row.get("step_label"),
                    "since": row.get("started_at"),
                    "position": row.get("queue_position"),
                    "href": "/mas/queue",
                }
                for row in queued_jobs
            ],
        ),
        _group(
            "publishing",
            "Downloaded, waiting to be published",
            [
                {
                    "title": row.get("title"),
                    "detail": "Next free publishing slot",
                    "since": row.get("updated_at"),
                    "href": "/a/queue",
                }
                for row in ready
            ],
        ),
    ]
    backlog = []
    if waiting:
        backlog.append(
            {
                "title": f"{waiting} anime release{'s' if waiting != 1 else ''} waiting for qBittorrent",
                "detail": f"{queued_torrents} queued for a download slot" if queued_torrents else None,
                "count": waiting,
                "href": "/a/queue",
            }
        )
    if retry_pending:
        backlog.append(
            {
                "title": f"{retry_pending} held release{'s' if retry_pending != 1 else ''} to check again",
                "detail": "On the automation's next cycle",
                "count": retry_pending,
                "href": "/a/review",
            }
        )
    due.append(_group("backlog", "Backlog", backlog))

    scheduled = []
    if automation.get("next_cycle_at") and not running:
        scheduled.append(
            {
                "title": "Anime automation cycle",
                "detail": "Feed, backlog and retries",
                "due_at": automation["next_cycle_at"],
                "running": False,
            }
        )
    for row in passes:
        scheduled.append(
            {
                "title": row["label"],
                "detail": f"Every {_interval(row['every_seconds'])}",
                "due_at": row.get("due_at"),
                "running": bool(row.get("running")),
            }
        )
    scheduled.sort(key=lambda row: (not row["running"], row.get("due_at") or now))
    due.append(_group("scheduled", "Scheduled", scheduled, limit=len(scheduled)))

    # -- Summary -------------------------------------------------------------
    total_workers = sum(len(lane["workers"]) for lane in lanes)
    busy_workers = sum(lane["busy"] for lane in lanes)
    return {
        "generated_at": now,
        "summary": {
            "workers_total": total_workers,
            "workers_busy": busy_workers,
            "due_total": len(queued_jobs) + len(ready) + waiting,
            "downloading": len(downloading),
            "download_speed": sum(float(row.get("dlspeed") or 0) for row in torrents or []),
            "automation": "down" if not alive else "running" if running else "idle",
        },
        "lanes": lanes,
        "due": due,
        "recent": recent,
    }


def _interval(seconds: float) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        hours = int(seconds // 3600)
        return "hour" if hours == 1 else f"{hours} hours"
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return "minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds:g} seconds"


# -- Recently added ----------------------------------------------------------


def recent_additions(
    files: Iterable[dict],
    cards: dict[str, dict],
    *,
    kind: str,
    href: str,
    limit: int = RECENT_LIMIT,
    window: float = RECENT_WINDOW_SECONDS,
) -> list[dict]:
    """The newest arrivals, one row per title, newest first.

    ``files`` are library-walk entries (``series`` is the title's folder);
    ``cards`` maps a folder name to its library card, for the poster and
    proper title. A show's episodes that arrived within ``window`` of its
    newest are counted together.
    """
    groups: dict[tuple[str, str], dict] = {}
    for entry in sorted(files, key=lambda row: -float(row.get("mtime") or 0)):
        folder = str(entry.get("series") or entry.get("name") or "")
        key = (str(entry.get("root") or ""), folder)
        mtime = float(entry.get("mtime") or 0)
        group = groups.get(key)
        if group is None:
            if len(groups) >= limit:
                continue  # a title older than every one already shown
            card = cards.get(folder) or {}
            group = groups[key] = {
                "key": card.get("key") or folder,
                "title": card.get("title") or folder,
                "poster_url": card.get("poster_url"),
                "kind": kind,
                "href": href,
                "added_at": mtime,
                "count": 0,
                "episodes": [],
            }
        elif group["added_at"] - mtime > window:
            continue
        group["count"] += 1
        if kind != "movie":
            identity = parse_se(str(entry.get("name") or ""))
            if identity is not None:
                group["episodes"].append(identity)
    out = []
    for group in groups.values():
        episodes = sorted(set(group.pop("episodes")))
        group["episode_label"] = _episode_label(episodes)
        out.append(group)
    return sorted(out, key=lambda row: -row["added_at"])


def _episode_label(episodes: list[tuple[int, int]]) -> str | None:
    """S01E04, S01E04-E06 or S01E09, S02E01 -- short enough for one line."""
    if not episodes:
        return None
    if len(episodes) == 1:
        season, episode = episodes[0]
        return f"S{season:02d}E{episode:02d}"
    seasons = {season for season, _ in episodes}
    if len(seasons) == 1:
        season = next(iter(seasons))
        numbers = [episode for _, episode in episodes]
        if numbers == list(range(numbers[0], numbers[-1] + 1)):
            return f"S{season:02d}E{numbers[0]:02d}-E{numbers[-1]:02d}"
        return f"S{season:02d}, {len(numbers)} episodes"
    return f"{len(episodes)} episodes across {len(seasons)} seasons"
