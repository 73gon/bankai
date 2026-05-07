"""Domain models for the job queue.

These are pure data classes (Pydantic ``BaseModel`` for free validation +
``model_copy``). The repository layer maps them to/from SQLite rows.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobKind(StrEnum):
    """High-level pipeline stages. Each maps to a ``Worker`` subclass."""

    SEARCH = "search"  # Resolve query → list of candidate sources.
    TORRENT = "torrent"  # Search Prowlarr + download via qBittorrent.
    EXTRACT = "extract"  # Pull dub audio from streaming source.
    SYNC = "sync"  # Align extracted audio against HQ video.
    REMUX = "remux"  # mkvmerge HQ video + synced audio.
    PIPELINE = "pipeline"  # Parent job that fans out to the above.


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaKind(StrEnum):
    MOVIE = "movie"
    EPISODE = "episode"


class Media(BaseModel):
    model_config = ConfigDict(frozen=False)

    id: int | None = None
    kind: MediaKind
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    created_at: str | None = None


class Source(BaseModel):
    id: int | None = None
    media_id: int
    site: str
    url: str
    language: str | None = None
    quality: str | None = None


class Job(BaseModel):
    id: int | None = None
    parent_id: int | None = None
    media_id: int | None = None
    kind: JobKind
    status: JobStatus = JobStatus.QUEUED
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class Artifact(BaseModel):
    id: int | None = None
    job_id: int
    kind: str  # 'audio' | 'video' | 'final' | 'intermediate'
    path: Path
    codec: str | None = None
    duration_ms: int | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
