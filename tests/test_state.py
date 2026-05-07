"""Tests for the SQLite state repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.db.state import StateRepository
from bankai.queue.models import Artifact, Job, JobKind, JobStatus, Media, MediaKind, Source


@pytest.fixture
def repo(tmp_path: Path):
    db = tmp_path / "state.sqlite3"
    with StateRepository(db) as r:
        yield r


def test_create_and_fetch_job(repo: StateRepository) -> None:
    job = repo.create_job(Job(kind=JobKind.EXTRACT, payload={"url": "http://x"}))
    assert job.id is not None
    fetched = repo.get_job(job.id)
    assert fetched is not None
    assert fetched.kind is JobKind.EXTRACT
    assert fetched.payload == {"url": "http://x"}
    assert fetched.status is JobStatus.QUEUED


def test_claim_next_job_only_returns_matching_kinds(repo: StateRepository) -> None:
    a = repo.create_job(Job(kind=JobKind.EXTRACT))
    b = repo.create_job(Job(kind=JobKind.SYNC))

    claimed = repo.claim_next_job([JobKind.SYNC])
    assert claimed is not None
    assert claimed.id == b.id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.attempts == 1

    # Second claim for SYNC returns nothing; EXTRACT is still queued.
    assert repo.claim_next_job([JobKind.SYNC]) is None
    extract_claim = repo.claim_next_job([JobKind.EXTRACT])
    assert extract_claim is not None
    assert extract_claim.id == a.id


def test_start_job_marks_specific_job_running(repo: StateRepository) -> None:
    job = repo.create_job(Job(kind=JobKind.PIPELINE))
    assert job.id is not None
    started = repo.start_job(job.id)
    assert started.status is JobStatus.RUNNING
    assert started.attempts == 1
    assert started.started_at is not None


def test_claim_respects_priority_then_creation_order(repo: StateRepository) -> None:
    low = repo.create_job(Job(kind=JobKind.EXTRACT, priority=0))
    high = repo.create_job(Job(kind=JobKind.EXTRACT, priority=10))
    first = repo.claim_next_job([JobKind.EXTRACT])
    second = repo.claim_next_job([JobKind.EXTRACT])
    assert first is not None and first.id == high.id
    assert second is not None and second.id == low.id


def test_complete_and_fail(repo: StateRepository) -> None:
    job = repo.create_job(Job(kind=JobKind.REMUX, max_attempts=2))
    claimed = repo.claim_next_job([JobKind.REMUX])
    assert claimed is not None
    repo.complete_job(claimed.id, {"out": "/tmp/x.mkv"})  # type: ignore[arg-type]
    fetched = repo.get_job(job.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.status is JobStatus.DONE
    assert fetched.result == {"out": "/tmp/x.mkv"}


def test_fail_with_retry_requeues(repo: StateRepository) -> None:
    job = repo.create_job(Job(kind=JobKind.EXTRACT, max_attempts=2))
    claimed = repo.claim_next_job([JobKind.EXTRACT])
    assert claimed is not None
    status = repo.fail_job(claimed.id, "boom", retry=True)  # type: ignore[arg-type]
    assert status is JobStatus.QUEUED
    fetched = repo.get_job(job.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.status is JobStatus.QUEUED
    assert fetched.error == "boom"
    assert fetched.attempts == 1


def test_fail_without_retry_marks_failed(repo: StateRepository) -> None:
    job = repo.create_job(Job(kind=JobKind.EXTRACT, max_attempts=3))
    claimed = repo.claim_next_job([JobKind.EXTRACT])
    assert claimed is not None
    status = repo.fail_job(claimed.id, "permanent", retry=False)  # type: ignore[arg-type]
    assert status is JobStatus.FAILED
    fetched = repo.get_job(job.id)  # type: ignore[arg-type]
    assert fetched is not None
    assert fetched.status is JobStatus.FAILED


def test_max_attempts_exhausted(repo: StateRepository) -> None:
    repo.create_job(Job(kind=JobKind.EXTRACT, max_attempts=1))
    claimed = repo.claim_next_job([JobKind.EXTRACT])
    assert claimed is not None
    status = repo.fail_job(claimed.id, "first", retry=True)  # type: ignore[arg-type]
    assert status is JobStatus.FAILED


def test_media_and_source_round_trip(repo: StateRepository) -> None:
    media = repo.upsert_media(Media(kind=MediaKind.MOVIE, title="Inception", year=2010))
    assert media.id is not None
    source = repo.add_source(
        Source(media_id=media.id, site="filmpalast", url="https://x/inception")
    )
    assert source.id is not None
    # Idempotent on (site, url).
    again = repo.add_source(
        Source(media_id=media.id, site="filmpalast", url="https://x/inception", quality="1080p")
    )
    assert again.id == source.id


def test_artifacts(repo: StateRepository, tmp_path: Path) -> None:
    job = repo.create_job(Job(kind=JobKind.EXTRACT))
    art = repo.add_artifact(
        Artifact(
            job_id=job.id,  # type: ignore[arg-type]
            kind="audio",
            path=tmp_path / "out.aac",
            codec="aac",
            duration_ms=120_000,
        )
    )
    assert art.id is not None
    arts = repo.list_artifacts(job.id)  # type: ignore[arg-type]
    assert len(arts) == 1
    assert arts[0].codec == "aac"


def test_list_jobs_filters(repo: StateRepository) -> None:
    repo.create_job(Job(kind=JobKind.EXTRACT))
    repo.create_job(Job(kind=JobKind.SYNC))
    extracts = repo.list_jobs(kind=JobKind.EXTRACT)
    assert len(extracts) == 1
    queued = repo.list_jobs(status=JobStatus.QUEUED)
    assert len(queued) == 2


def test_clear_jobs_deletes_requested_statuses(repo: StateRepository) -> None:
    done = repo.create_job(Job(kind=JobKind.PIPELINE))
    failed = repo.create_job(Job(kind=JobKind.PIPELINE))
    queued = repo.create_job(Job(kind=JobKind.PIPELINE))
    assert done.id is not None
    assert failed.id is not None
    assert queued.id is not None

    repo.complete_job(done.id)
    repo.fail_job(failed.id, "boom", retry=False)

    deleted = repo.clear_jobs([JobStatus.DONE, JobStatus.FAILED])
    assert deleted == 2
    assert repo.get_job(done.id) is None
    assert repo.get_job(failed.id) is None
    assert repo.get_job(queued.id) is not None
