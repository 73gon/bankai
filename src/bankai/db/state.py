"""SQLite repository for jobs, media, sources, and artifacts.

Design notes:

* Plain ``sqlite3`` â€” no ORM. The schema is small enough that explicit SQL is
  clearer than mapping layers and avoids a heavy dep.
* All connections use ``Row`` factory so rows behave like dicts.
* ``WAL`` mode is enabled by the schema for concurrent readers + a single
  writer (matches our async dispatcher: many workers read, one writes at a
  time per connection).
* Each :class:`StateRepository` instance owns a single connection. Workers
  should construct their own instance; ``sqlite3`` connections are *not*
  thread-safe across threads.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from bankai.queue.models import Artifact, Job, JobKind, JobStatus, Media, MediaKind, Source

SCHEMA_PACKAGE = "bankai.db"
SCHEMA_RESOURCE = "schema.sql"


def _load_schema() -> str:
    return resources.files(SCHEMA_PACKAGE).joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(path: Path) -> None:
    """Create the database file and apply the schema."""
    conn = _connect(path)
    try:
        conn.executescript(_load_schema())
    finally:
        conn.close()


class StateRepository:
    """Thin repository facade over the SQLite state DB."""

    def __init__(self, path: Path) -> None:
        self._path = path
        initialize(path)
        self._conn = _connect(path)

    # ---- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateRepository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # ---- media -------------------------------------------------------------

    def upsert_media(self, media: Media) -> Media:
        cur = self._conn.execute(
            """
            INSERT INTO media (kind, title, year, season, episode, episode_title, imdb_id, tmdb_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id, created_at
            """,
            (
                media.kind.value,
                media.title,
                media.year,
                media.season,
                media.episode,
                media.episode_title,
                media.imdb_id,
                media.tmdb_id,
            ),
        )
        row = cur.fetchone()
        return media.model_copy(update={"id": row["id"], "created_at": row["created_at"]})

    def get_media(self, media_id: int) -> Media | None:
        row = self._conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        return _row_to_media(row) if row else None

    # ---- sources -----------------------------------------------------------

    def add_source(self, source: Source) -> Source:
        cur = self._conn.execute(
            """
            INSERT INTO sources (media_id, site, url, language, quality)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(site, url) DO UPDATE SET
                media_id = excluded.media_id,
                language = excluded.language,
                quality  = excluded.quality
            RETURNING id
            """,
            (source.media_id, source.site, source.url, source.language, source.quality),
        )
        return source.model_copy(update={"id": cur.fetchone()["id"]})

    # ---- jobs --------------------------------------------------------------

    def create_job(self, job: Job) -> Job:
        cur = self._conn.execute(
            """
            INSERT INTO jobs
                (parent_id, media_id, kind, status, payload, max_attempts, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id, created_at, updated_at
            """,
            (
                job.parent_id,
                job.media_id,
                job.kind.value,
                job.status.value,
                json.dumps(job.payload),
                job.max_attempts,
                job.priority,
            ),
        )
        row = cur.fetchone()
        return job.model_copy(
            update={
                "id": row["id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    def get_job(self, job_id: int) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def start_job(self, job_id: int) -> Job:
        """Mark a specific queued job as running for direct CLI execution."""
        self._conn.execute(
            """
            UPDATE jobs
               SET status = 'running',
                   attempts = attempts + 1,
                   started_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = ?
            """,
            (job_id,),
        )
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"job {job_id} not found")
        return job

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        kind: JobKind | None = None,
        limit: int = 100,
    ) -> list[Job]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM jobs {where} ORDER BY priority DESC, created_at LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_job(r) for r in rows]

    def claim_next_job(self, kinds: Iterable[JobKind]) -> Job | None:
        """Atomically transition the highest-priority queued job to running."""
        kind_values = [k.value for k in kinds]
        if not kind_values:
            return None
        placeholders = ",".join("?" for _ in kind_values)
        with self.transaction():
            row = self._conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE status = 'queued' AND kind IN ({placeholders})
                ORDER BY priority DESC, created_at
                LIMIT 1
                """,
                kind_values,
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                """
                UPDATE jobs
                   SET status = 'running',
                       attempts = attempts + 1,
                       started_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                 WHERE id = ?
                """,
                (row["id"],),
            )
            return _row_to_job(row).model_copy(
                update={"status": JobStatus.RUNNING, "attempts": row["attempts"] + 1}
            )

    def complete_job(self, job_id: int, result: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            """
            UPDATE jobs
               SET status = 'done',
                   result = ?,
                   error = NULL,
                   finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = ?
            """,
            (json.dumps(result) if result is not None else None, job_id),
        )

    def fail_job(self, job_id: int, error: str, *, retry: bool) -> JobStatus:
        """Mark a job failed. If ``retry`` and attempts remain, re-queue it."""
        row = self._conn.execute(
            "SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"job {job_id} not found")
        if retry and row["attempts"] < row["max_attempts"]:
            self._conn.execute(
                "UPDATE jobs SET status = 'queued', error = ? WHERE id = ?",
                (error, job_id),
            )
            return JobStatus.QUEUED
        self._conn.execute(
            """
            UPDATE jobs
               SET status = 'failed',
                   error = ?,
                   finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
             WHERE id = ?
            """,
            (error, job_id),
        )
        return JobStatus.FAILED

    def cancel_job(self, job_id: int) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ? AND status IN ('queued','running')",
            (job_id,),
        )

    def clear_jobs(self, statuses: Iterable[JobStatus]) -> int:
        """Delete jobs in the given statuses, cascading their artifacts."""
        status_values = [s.value for s in statuses]
        if not status_values:
            return 0
        placeholders = ",".join("?" for _ in status_values)
        cur = self._conn.execute(
            f"DELETE FROM jobs WHERE status IN ({placeholders})",
            status_values,
        )
        return int(cur.rowcount)

    # ---- artifacts ---------------------------------------------------------

    def add_artifact(self, artifact: Artifact) -> Artifact:
        cur = self._conn.execute(
            """
            INSERT INTO artifacts
                (job_id, kind, path, codec, duration_ms, size_bytes, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id, created_at
            """,
            (
                artifact.job_id,
                artifact.kind,
                str(artifact.path),
                artifact.codec,
                artifact.duration_ms,
                artifact.size_bytes,
                json.dumps(artifact.metadata) if artifact.metadata else None,
            ),
        )
        row = cur.fetchone()
        return artifact.model_copy(update={"id": row["id"], "created_at": row["created_at"]})

    def list_artifacts(self, job_id: int) -> list[Artifact]:
        rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
        return [_row_to_artifact(r) for r in rows]


# ---- row mappers -----------------------------------------------------------


def _row_to_media(row: sqlite3.Row) -> Media:
    return Media(
        id=row["id"],
        kind=MediaKind(row["kind"]),
        title=row["title"],
        year=row["year"],
        season=row["season"],
        episode=row["episode"],
        episode_title=row["episode_title"],
        imdb_id=row["imdb_id"],
        tmdb_id=row["tmdb_id"],
        created_at=row["created_at"],
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        parent_id=row["parent_id"],
        media_id=row["media_id"],
        kind=JobKind(row["kind"]),
        status=JobStatus(row["status"]),
        payload=json.loads(row["payload"]) if row["payload"] else {},
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        priority=row["priority"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        id=row["id"],
        job_id=row["job_id"],
        kind=row["kind"],
        path=Path(row["path"]),
        codec=row["codec"],
        duration_ms=row["duration_ms"],
        size_bytes=row["size_bytes"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        created_at=row["created_at"],
    )
