"""SQLite-backed job cache for the web queue.

The authoritative job state still lives in :mod:`bankai.cli.bgjobs` (one
directory + log file per detached pipeline run). Reading every ``meta.json``
and tailing every log on *each* poll / WebSocket tick was the source of the
slow Queue tab and missing live updates.

This module decouples the expensive scan from request handling:

* A background daemon thread (:class:`JobRefresher`) periodically scans the
  bgjobs store + pending queue, computes a progress snapshot for each job,
  and upserts a denormalised row into ``queue.sqlite3``.
* The API / WebSocket read from this table, which is indexed and cheap, so
  the Queue tab paints instantly and supports filtering + pagination.

The store is self-contained (its own SQLite file) so it never contends with
the pipeline's own state DB.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web import jobs as webjobs

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    kind            TEXT,
    title           TEXT,
    status          TEXT,
    started_at      REAL,
    finished_at     REAL,
    exit_code       INTEGER,
    final_path      TEXT,
    step            INTEGER,
    total_steps     INTEGER,
    step_label      TEXT,
    overall_percent REAL,
    pending         INTEGER DEFAULT 0,
    updated_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at DESC);
"""

_COLUMNS = (
    "id",
    "kind",
    "title",
    "status",
    "started_at",
    "finished_at",
    "exit_code",
    "final_path",
    "step",
    "total_steps",
    "step_label",
    "overall_percent",
    "pending",
)


def _db_path() -> Path:
    return bgjobs.jobs_root().parent / "queue.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 3000")
    return conn


class JobStore:
    """Thread-safe SQLite cache of the unified job snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn = _connect()
        self._conn.executescript(_SCHEMA)

    # -- writes ----------------------------------------------------------
    def replace_all(self, snapshot: list[dict[str, Any]]) -> None:
        """Atomically replace the cached snapshot with the latest scan."""
        rows = [tuple(s.get(col) for col in _COLUMNS) for s in snapshot]
        ids = [s["id"] for s in snapshot]
        now = time.time()
        placeholders = ",".join("?" for _ in _COLUMNS)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            try:
                # Drop rows whose job no longer exists in the store.
                if ids:
                    q = ",".join("?" for _ in ids)
                    cur.execute(f"DELETE FROM jobs WHERE id NOT IN ({q})", ids)
                else:
                    cur.execute("DELETE FROM jobs")
                cur.executemany(
                    f"INSERT OR REPLACE INTO jobs "
                    f"({','.join(_COLUMNS)}, updated_at) "
                    f"VALUES ({placeholders}, {now})",
                    rows,
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # -- reads -----------------------------------------------------------
    def query(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "all":
            if status == "active":
                clauses.append("status IN ('running','queued')")
            elif status == "finished":
                clauses.append("status IN ('done','success')")
            elif status == "failed":
                clauses.append("status IN ('error','failed','cancelled')")
            else:
                clauses.append("status = ?")
                params.append(status)
        if search:
            clauses.append("LOWER(title) LIKE ?")
            params.append(f"%{search.lower()}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        page = max(1, page)
        page_size = max(1, min(200, page_size))
        offset = (page - 1) * page_size

        with self._lock:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM jobs{where}", params
            ).fetchone()["n"]
            rows = self._conn.execute(
                f"SELECT * FROM jobs{where} "
                f"ORDER BY (status IN ('running','queued')) DESC, started_at DESC "
                f"LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
            # Status counts for the filter chips (ignores search + status).
            count_where = ""
            count_params: list[Any] = []
            if search:
                count_where = " WHERE LOWER(title) LIKE ?"
                count_params.append(f"%{search.lower()}%")
            count_rows = self._conn.execute(
                f"SELECT status, COUNT(*) AS n FROM jobs{count_where} GROUP BY status",
                count_params,
            ).fetchall()

        counts = {"all": 0, "active": 0, "finished": 0, "failed": 0}
        for cr in count_rows:
            counts["all"] += cr["n"]
            st = cr["status"]
            if st in ("running", "queued"):
                counts["active"] += cr["n"]
            elif st in ("done", "success"):
                counts["finished"] += cr["n"]
            elif st in ("error", "failed", "cancelled"):
                counts["failed"] += cr["n"]

        jobs = [self._row_to_job(r) for r in rows]
        return {
            "jobs": jobs,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "counts": counts,
        }

    def all_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs "
                "ORDER BY (status IN ('running','queued')) DESC, started_at DESC"
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"],
            "kind": r["kind"],
            "title": r["title"],
            "status": r["status"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "exit_code": r["exit_code"],
            "final_path": r["final_path"],
            "step": r["step"],
            "total_steps": r["total_steps"],
            "step_label": r["step_label"],
            "overall_percent": r["overall_percent"],
            "pending": bool(r["pending"]),
        }


_STORE: JobStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> JobStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = JobStore()
    return _STORE


class JobRefresher(threading.Thread):
    """Daemon thread that keeps the SQLite job cache fresh."""

    def __init__(self, interval: float = 1.2) -> None:
        super().__init__(name="job-refresher", daemon=True)
        self._interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        store = get_store()
        # Prime once immediately so the first request is already warm.
        self._refresh_once(store)
        while not self._stop.wait(self._interval):
            self._refresh_once(store)

    @staticmethod
    def _refresh_once(store: JobStore) -> None:
        try:
            snapshot = webjobs.snapshot()
            store.replace_all(snapshot)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("job refresh failed: %s", exc)


_REFRESHER: JobRefresher | None = None


def start_refresher() -> None:
    """Start the background refresher (idempotent)."""
    global _REFRESHER
    if _REFRESHER is not None and _REFRESHER.is_alive():
        return
    interval = max(0.5, float(get_settings().web.cache_ttl_seconds and 1.2 or 1.2))
    _REFRESHER = JobRefresher(interval=interval)
    _REFRESHER.start()
    log.info("job refresher started (interval=%.1fs)", interval)


def stop_refresher() -> None:
    global _REFRESHER
    if _REFRESHER is not None:
        _REFRESHER.stop()
        _REFRESHER = None


# ---------------------------------------------------------------------------
# Structured log timeline
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_TS_RE = re.compile(r"^\[?(\d{2}:\d{2}:\d{2})\]?\s*")
_STAGE_RE = re.compile(
    r'BANKAI_STAGE\s+step=(\d+)\s+total=(\d+)\s+key=(\w+)\s+label="([^"]+)"'
)
_PROGRESS_RE = re.compile(r"BANKAI_PROGRESS\s+(.+)$")

# Patterns of genuinely informative log lines worth surfacing as a timeline.
_INTERESTING = (
    (re.compile(r"\[filmpalast\]\s+resolved hoster", re.I), "info"),
    (re.compile(r"resolved hoster|mirror|hoster:", re.I), "info"),
    (re.compile(r"\[playwright\]\s+selected media URL", re.I), "info"),
    (re.compile(r"\[playwright\]\s+", re.I), "info"),
    (re.compile(r"switching to Playwright", re.I), "info"),
    (re.compile(r"ffmpeg pull", re.I), "info"),
    (re.compile(r"redirect", re.I), "info"),
    (re.compile(r"added torrent|qbittorrent|seeders|torrent:", re.I), "info"),
    (re.compile(r"download(ed|ing)\b", re.I), "info"),
    (re.compile(r"sync|alass|offset", re.I), "info"),
    (re.compile(r"remux|mkvmerge|writing final|final_path", re.I), "info"),
    (re.compile(r"\bwarn(ing)?\b", re.I), "warn"),
    (re.compile(r"\berror\b|traceback|failed|exception", re.I), "error"),
)


def _clean(line: str) -> str:
    line = _ANSI_RE.sub("", line).rstrip()
    return line


def parse_log_events(job: "bgjobs.BgJob", *, max_events: int = 120) -> list[dict[str, Any]]:
    """Turn a raw job log into a concise, human-readable timeline.

    Picks out stage transitions, hoster/redirect resolution, download and
    sync milestones, warnings and errors — dropping the low-signal noise so
    the Queue detail view shows meaningful progress instead of a wall of
    text (or "waiting for log output").
    """
    try:
        raw = job.log_path.read_text(errors="replace") if job.log_path.exists() else ""
    except Exception:
        return []
    if not raw:
        return []

    events: list[dict[str, Any]] = []
    last_progress: dict[str, str] = {}

    def push(level: str, stage: str | None, message: str, ts: str | None) -> None:
        if not message:
            return
        # Collapse consecutive duplicates.
        if events and events[-1]["message"] == message:
            return
        events.append({"level": level, "stage": stage, "message": message, "time": ts})

    for rawline in raw.splitlines():
        line = _clean(rawline)
        if not line:
            continue
        ts_match = _TS_RE.search(line)
        ts = ts_match.group(1) if ts_match else None

        stage_match = _STAGE_RE.search(line)
        if stage_match:
            step, total, key, label = stage_match.groups()
            push("stage", key, f"Stage {step}/{total}: {label}", ts)
            continue

        prog_match = _PROGRESS_RE.search(line)
        if prog_match:
            data = _parse_kv(prog_match.group(1))
            stage = data.get("stage", "")
            pct = data.get("pct")
            if stage:
                # Only surface a progress event when it changes meaningfully.
                # We deliberately drop the raw "state=downloading" token —
                # it's noisy and the moving bar already conveys activity.
                key = stage
                summary_parts = []
                if pct is not None:
                    try:
                        summary_parts.append(f"{float(pct):.0f}%")
                    except ValueError:
                        pass
                if data.get("speed"):
                    summary_parts.append(_human_speed(data["speed"]))
                summary = (
                    f"{_stage_name(stage)}: {' · '.join(summary_parts)}"
                    if summary_parts
                    else None
                )
                if summary and last_progress.get(key) != summary:
                    last_progress[key] = summary
                    push("progress", stage, summary, ts)
            continue

        for pattern, level in _INTERESTING:
            if pattern.search(line):
                # Trim the logger prefix (module + level) for readability.
                msg = _strip_logger_prefix(line)
                push(level, None, msg, ts)
                break

    if len(events) > max_events:
        events = events[-max_events:]
    return events


def _parse_kv(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in raw.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _stage_name(stage: str) -> str:
    return {
        "stream": "Audio extract",
        "extract": "Audio extract",
        "torrent": "HQ video",
        "sync": "Audio sync",
        "remux": "Remux",
        "transfer": "Transfer",
    }.get(stage, stage.replace("_", " ").title())


def _human_speed(value: str) -> str:
    try:
        b = float(value)
    except ValueError:
        return value
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1024 or unit == "GB/s":
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} GB/s"


_LOGGER_PREFIX_RE = re.compile(
    r"^\[?\d{2}:\d{2}:\d{2}\]?\s*"  # optional timestamp
    r"(?:\[?(?:DEBUG|INFO|WARNING|WARN|ERROR)\]?\s*)?"  # optional level
    r"(?:[\w.]+:\d+\s*[-|]\s*)?",  # optional module:line -
    re.I,
)


def _strip_logger_prefix(line: str) -> str:
    cleaned = _LOGGER_PREFIX_RE.sub("", line, count=1).strip()
    return cleaned or line.strip()

