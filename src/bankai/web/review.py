"""Persistent review / approval state for library files.

Finished MKVs land in the library in a *review* state. The user QCs the
German dub in the browser, optionally adjusts the audio delay (which
repacks the file), and finally *approves* the file. Approval moves it to
the *transfer* stage \u2014 nothing is sent to the media server without it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path

_STORE_THREAD_LOCK = threading.RLock()


def _state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        root = Path(base)
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    else:
        root = Path.home() / ".local" / "state"
    try:
        target = root / "bankai"
        target.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        target = Path(tempfile.gettempdir()) / "bankai-state" / "bankai"
        target.mkdir(parents=True, exist_ok=True)
        return target


def _store_path() -> Path:
    return _state_root() / "review.json"


# stage: "review" -> "approved" -> "transferred"
@dataclass(slots=True)
class ReviewState:
    path: str
    stage: str = "review"
    delay_ms: int = 0
    # Frozen on first discovery. Files are atomically replaced during a repack,
    # which changes their filesystem creation time on Windows.
    created_at: float | None = None
    updated_at: float = 0.0
    transferred_at: float | None = None
    note: str | None = None
    needs_sync_review: bool = False
    sync_confidence: float | None = None
    auto_delay_ms: int = 0
    # Frame-rate drift diagnostics captured during visual sync (German source
    # vs the HQ reference). drift_ratio is the measured source/reference speed
    # ratio; source_fps/reference_fps are the raw stream frame rates.
    source_fps: float | None = None
    reference_fps: float | None = None
    drift_ratio: float | None = None
    # Provenance for the two inputs that produced the reviewed MKV.
    german_source_url: str | None = None
    torrent_source_url: str | None = None
    torrent_source_title: str | None = None
    # Transfer is tracked per-entry (shown as a column on the library row)
    # instead of as a standalone queue job.
    transfer_status: str = "idle"  # idle | transferring | done | failed
    transfer_percent: float = 0.0
    # Repack/replacement jobs are detached but rendered on this same library
    # row, never as an extra queue entry.
    repack_status: str = "idle"  # idle | repacking | done | failed
    repack_percent: float = 0.0
    repack_kind: str | None = None  # audio | torrent


def _load() -> dict[str, dict]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, dict]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="review.", suffix=".tmp", dir=p.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows can briefly deny a replacement while antivirus/indexing or
        # another reader has the destination open. Unique temp names prevent
        # writers from deleting each other's files; retries handle that short
        # destination lock without losing the state update.
        for attempt in range(8):
            try:
                os.replace(tmp, p)  # noqa: PTH105 - explicit atomic replace
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.4, 0.025 * (2**attempt)))
    finally:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)


@contextmanager
def _store_lock():
    """Serialise review read-modify-write cycles across threads/processes."""
    lock_path = _store_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _STORE_THREAD_LOCK, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + 10.0
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for review state lock") from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _key(path: str | Path) -> str:
    return str(Path(path).resolve())


def _update(path: str | Path, change: Callable[[dict], None]) -> ReviewState:
    """Atomically update one entry and return its fully defaulted state."""
    with _store_lock():
        data = _load()
        key = _key(path)
        raw = data.get(key, {"path": str(path), "stage": "review"})
        raw["path"] = str(path)
        change(raw)
        data[key] = raw
        _save(data)
        return ReviewState(**raw)


def get_state(path: str | Path) -> ReviewState:
    data = _load()
    raw = data.get(_key(path))
    if raw is None:
        return ReviewState(path=str(path))
    raw.setdefault("path", str(path))
    return ReviewState(**raw)


def set_stage(path: str | Path, stage: str, *, note: str | None = None) -> ReviewState:
    def change(raw: dict) -> None:
        now = time.time()
        raw["stage"] = stage
        raw["updated_at"] = now
        if note is not None:
            raw["note"] = note
        if stage == "transferred":
            raw["transferred_at"] = now

    return _update(path, change)


def reset_for_new_output(path: str | Path) -> ReviewState:
    """Reset review/operation state after a newly produced MKV is installed."""

    def change(raw: dict) -> None:
        raw.update(
            {
                "stage": "review",
                "delay_ms": 0,
                "transferred_at": None,
                "note": None,
                "needs_sync_review": False,
                "sync_confidence": None,
                "auto_delay_ms": 0,
                "source_fps": None,
                "reference_fps": None,
                "drift_ratio": None,
                "transfer_status": "idle",
                "transfer_percent": 0.0,
                "repack_status": "idle",
                "repack_percent": 0.0,
                "repack_kind": None,
                "updated_at": time.time(),
            }
        )

    return _update(path, change)


def set_delay(path: str | Path, delay_ms: int) -> ReviewState:
    def change(raw: dict) -> None:
        raw["delay_ms"] = delay_ms
        raw["updated_at"] = time.time()

    return _update(path, change)


def ensure_created_at(path: str | Path, created_at: float) -> ReviewState:
    """Persist the first observed creation time and never overwrite it."""
    with _store_lock():
        data = _load()
        key = _key(path)
        raw = data.get(key, {"path": str(path), "stage": "review"})
        raw["path"] = str(path)
        if not raw.get("created_at"):
            raw["created_at"] = float(created_at)
            data[key] = raw
            _save(data)
        return ReviewState(**raw)


def set_sync_review(
    path: str | Path,
    *,
    needs_review: bool,
    confidence: float | None = None,
    applied_delay_ms: int = 0,
    source_fps: float | None = None,
    reference_fps: float | None = None,
    drift_ratio: float | None = None,
) -> ReviewState:
    """Record the automatic-alignment outcome for a finished library file.

    ``needs_review`` marks titles whose visual sync was low-confidence so the
    web UI can surface them for a quick manual delay nudge. ``applied_delay_ms``
    is the offset the pipeline already baked in via ``mkvmerge --sync`` so the
    review player can show it as the current baseline.
    """

    def change(raw: dict) -> None:
        raw["needs_sync_review"] = bool(needs_review)
        raw["sync_confidence"] = confidence
        raw["auto_delay_ms"] = int(applied_delay_ms)
        if source_fps is not None:
            raw["source_fps"] = float(source_fps)
        if reference_fps is not None:
            raw["reference_fps"] = float(reference_fps)
        if drift_ratio is not None:
            raw["drift_ratio"] = float(drift_ratio)
        raw["updated_at"] = time.time()

    return _update(path, change)


def set_sources(
    path: str | Path,
    *,
    german_source_url: str | None = None,
    torrent_source_url: str | None = None,
    torrent_source_title: str | None = None,
) -> ReviewState:
    """Persist the German stream and HQ torrent provenance for one output."""

    def change(raw: dict) -> None:
        if german_source_url is not None:
            raw["german_source_url"] = german_source_url
        if torrent_source_url is not None:
            raw["torrent_source_url"] = torrent_source_url
        if torrent_source_title is not None:
            raw["torrent_source_title"] = torrent_source_title
        raw["updated_at"] = time.time()

    return _update(path, change)


def set_transfer(path: str | Path, status: str, *, percent: float | None = None) -> ReviewState:
    """Update the per-entry transfer status shown in the library column.

    ``status`` is one of ``idle|transferring|done|failed``. When ``done`` the
    entry also advances to the ``transferred`` stage.
    """

    def change(raw: dict) -> None:
        now = time.time()
        raw["transfer_status"] = status
        if percent is not None:
            raw["transfer_percent"] = float(percent)
        if status == "done":
            raw["stage"] = "transferred"
            raw["transferred_at"] = now
            raw["transfer_percent"] = 100.0
        raw["updated_at"] = now

    return _update(path, change)


def set_repack(
    path: str | Path,
    status: str,
    *,
    percent: float | None = None,
    kind: str | None = None,
    note: str | None = None,
) -> ReviewState:
    """Update the detached repack/replacement status for a library entry."""

    def change(raw: dict) -> None:
        effective_kind = kind or raw.get("repack_kind")
        raw["repack_status"] = status
        if percent is not None:
            raw["repack_percent"] = float(percent)
        if kind is not None:
            raw["repack_kind"] = kind
        if note is not None:
            raw["note"] = note
        if status == "repacking":
            raw["stage"] = "repacking"
            raw["repack_percent"] = float(percent or 0.0)
        elif status == "done":
            # Audio repacks originate from Approve; torrent replacement must
            # return to Review so the newly downloaded video can be checked.
            raw["stage"] = "approved" if effective_kind == "audio" else "review"
            raw["repack_percent"] = 100.0
        elif status == "failed":
            raw["stage"] = "review"
        raw["updated_at"] = time.time()

    return _update(path, change)


def forget(path: str | Path) -> None:
    with _store_lock():
        data = _load()
        if data.pop(_key(path), None) is not None:
            _save(data)


def all_states() -> dict[str, ReviewState]:
    return {k: ReviewState(**v) for k, v in _load().items()}


def to_dict(state: ReviewState) -> dict:
    return asdict(state)
