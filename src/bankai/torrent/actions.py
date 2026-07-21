"""Persistent hand-off between a torrent worker and the web UI.

When strict quality/size/seeder rules reject every search result, a detached
pipeline can pause without failing. The candidates are written beside the
background-job registry; the UI records one explicit choice in the same file
and the worker resumes from it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.torrent.prowlarr import TorrentCandidate


def candidate_id(candidate: TorrentCandidate | dict[str, Any]) -> str:
    if isinstance(candidate, TorrentCandidate):
        value = candidate.info_hash or candidate.magnet_uri or candidate.download_url or candidate.title
    else:
        value = (
            candidate.get("info_hash")
            or candidate.get("magnet_uri")
            or candidate.get("download_url")
            or candidate.get("title")
            or ""
        )
    return hashlib.sha1(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]


def candidate_to_dict(candidate: TorrentCandidate, *, eligible: bool = False) -> dict[str, Any]:
    return {
        "id": candidate_id(candidate),
        "title": candidate.title,
        "indexer": candidate.indexer,
        "indexer_id": candidate.indexer_id,
        "download_url": candidate.download_url,
        "info_url": candidate.info_url,
        "magnet_uri": candidate.magnet_uri,
        "info_hash": candidate.info_hash,
        "size_bytes": candidate.size_bytes,
        "seeders": candidate.seeders,
        "leechers": candidate.leechers,
        "publish_date": candidate.publish_date,
        "runtime_seconds": candidate.runtime_seconds,
        "eligible": eligible,
    }


def candidate_from_dict(raw: dict[str, Any]) -> TorrentCandidate:
    return TorrentCandidate(
        title=str(raw.get("title") or ""),
        indexer=str(raw.get("indexer") or ""),
        indexer_id=raw.get("indexer_id"),
        download_url=str(raw.get("download_url") or ""),
        info_url=raw.get("info_url"),
        magnet_uri=raw.get("magnet_uri"),
        info_hash=raw.get("info_hash"),
        size_bytes=int(raw.get("size_bytes") or 0),
        seeders=int(raw.get("seeders") or 0),
        leechers=int(raw.get("leechers") or 0),
        publish_date=raw.get("publish_date"),
        raw={},
    )


def _root() -> Path:
    path = bgjobs.jobs_root().parent / "torrent_actions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in "-_")
    return _root() / f"{safe}.json"


def _active_path(job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in "-_")
    return _root() / f"{safe}.active.json"


def set_active_torrent(job_id: str, torrent_hash: str) -> None:
    path = _active_path(job_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"hash": torrent_hash}), encoding="utf-8")
    tmp.replace(path)


def get_active_torrent(job_id: str) -> str | None:
    try:
        value = json.loads(_active_path(job_id).read_text(encoding="utf-8")).get("hash")
        return str(value) if value else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def clear_active_torrent(job_id: str) -> None:
    _active_path(job_id).unlink(missing_ok=True)


def get_request(job_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(_path(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def create_request(
    job_id: str,
    *,
    query: str,
    candidates: list[TorrentCandidate],
    eligible_ids: set[str],
    target_runtime_seconds: float | None,
) -> dict[str, Any]:
    request = {
        "job_id": job_id,
        "status": "waiting",
        "query": query,
        "target_runtime_seconds": target_runtime_seconds,
        "candidates": [
            candidate_to_dict(candidate, eligible=candidate_id(candidate) in eligible_ids)
            for candidate in sorted(candidates, key=lambda item: item.seeders, reverse=True)
        ],
        "selected": None,
    }
    path = _path(job_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(request, indent=2), encoding="utf-8")
    tmp.replace(path)
    return request


def choose(job_id: str, selected_id: str) -> dict[str, Any] | None:
    request = get_request(job_id)
    if request is None:
        return None
    selected = next(
        (candidate for candidate in request.get("candidates", []) if candidate.get("id") == selected_id),
        None,
    )
    if selected is None:
        return None
    request["selected"] = selected
    request["status"] = "selected"
    path = _path(job_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(request, indent=2), encoding="utf-8")
    tmp.replace(path)
    return selected


async def wait_for_choice(job_id: str, *, cancel_token: asyncio.Event) -> TorrentCandidate:
    while not cancel_token.is_set():
        request = get_request(job_id)
        selected = request.get("selected") if request else None
        if isinstance(selected, dict):
            _path(job_id).unlink(missing_ok=True)
            return candidate_from_dict(selected)
        await asyncio.sleep(1.0)
    raise asyncio.CancelledError


def clear(job_id: str) -> None:
    _path(job_id).unlink(missing_ok=True)
    clear_active_torrent(job_id)
