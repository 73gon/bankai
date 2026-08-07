"""Cross-process cleanup coordination for episode torrent batches.

The web queue launches one pipeline per episode.  Those pipelines may all use
the same season-pack torrent, so deleting it when the first episode finishes
forces later queue waves to download the pack again.  This module records the
completed members and torrent hashes for one explicitly queued episode batch;
the final distinct member claims cleanup for every torrent used by the batch.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from bankai.cli import bgjobs

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")


@dataclass(frozen=True, slots=True)
class GroupRelease:
    cleanup: bool
    torrent_hashes: tuple[str, ...]
    completed: int
    expected: int


def _root() -> Path:
    return bgjobs.jobs_root().parent / "torrent-groups"


def _paths(group_id: str) -> tuple[Path, Path]:
    if not _SAFE_ID.fullmatch(group_id):
        raise ValueError("invalid torrent group id")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{group_id}.json", root / f"{group_id}.lock"


@contextmanager
def _locked(lock_path: Path) -> Iterator[None]:
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(path: Path, value: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def get_candidate(group_id: str) -> dict | None:
    """Return the season-pack candidate already selected for this batch."""

    state_path, lock_path = _paths(group_id)
    with _locked(lock_path):
        candidate = _load(state_path).get("candidate")
        return dict(candidate) if isinstance(candidate, dict) else None


def remember_candidate(group_id: str, candidate: dict) -> dict:
    """Publish one season-pack choice; the first concurrent selector wins."""

    state_path, lock_path = _paths(group_id)
    with _locked(lock_path):
        state = _load(state_path)
        existing = state.get("candidate")
        if isinstance(existing, dict):
            return dict(existing)
        state["candidate"] = dict(candidate)
        _save(state_path, state)
        return dict(candidate)


def mark_complete(
    group_id: str,
    *,
    member_id: str,
    expected: int,
    torrent_hash: str,
) -> GroupRelease:
    """Mark one episode finished and let the final member claim cleanup."""

    if expected < 1:
        raise ValueError("torrent group size must be positive")
    state_path, lock_path = _paths(group_id)
    with _locked(lock_path):
        state = _load(state_path)
        members = {str(value) for value in state.get("members", []) if value}
        hashes = {str(value).lower() for value in state.get("torrent_hashes", []) if value}
        members.add(member_id)
        hashes.add(torrent_hash.lower())
        stored_expected = max(int(state.get("expected") or 0), expected)
        claimant = state.get("cleanup_claimed_by")
        cleanup = len(members) >= stored_expected and claimant is None
        if cleanup:
            claimant = member_id
        state = {
            "expected": stored_expected,
            "members": sorted(members),
            "torrent_hashes": sorted(hashes),
            "cleanup_claimed_by": claimant,
            "candidate": state.get("candidate"),
        }
        _save(state_path, state)
        return GroupRelease(
            cleanup=cleanup,
            torrent_hashes=tuple(sorted(hashes)),
            completed=len(members),
            expected=stored_expected,
        )


def finish_cleanup(group_id: str, *, member_id: str, success: bool) -> None:
    """Finish a cleanup claim, or release it so another retry can clean up."""

    state_path, lock_path = _paths(group_id)
    with _locked(lock_path):
        state = _load(state_path)
        if state.get("cleanup_claimed_by") != member_id:
            return
        if success:
            state_path.unlink(missing_ok=True)
            return
        state["cleanup_claimed_by"] = None
        _save(state_path, state)
