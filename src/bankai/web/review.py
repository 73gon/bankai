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
import time
from dataclasses import asdict, dataclass
from pathlib import Path


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
    # Transfer is tracked per-entry (shown as a column on the library row)
    # instead of as a standalone queue job.
    transfer_status: str = "idle"  # idle | transferring | done | failed
    transfer_percent: float = 0.0


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
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def _key(path: str | Path) -> str:
    return str(Path(path).resolve())


def get_state(path: str | Path) -> ReviewState:
    data = _load()
    raw = data.get(_key(path))
    if raw is None:
        return ReviewState(path=str(path))
    raw.setdefault("path", str(path))
    return ReviewState(**raw)


def set_stage(path: str | Path, stage: str, *, note: str | None = None) -> ReviewState:
    data = _load()
    key = _key(path)
    raw = data.get(key, {"path": str(path)})
    raw["path"] = str(path)
    raw["stage"] = stage
    raw["updated_at"] = time.time()
    if note is not None:
        raw["note"] = note
    if stage == "transferred":
        raw["transferred_at"] = time.time()
    data[key] = raw
    _save(data)
    return ReviewState(**raw)


def set_delay(path: str | Path, delay_ms: int) -> ReviewState:
    data = _load()
    key = _key(path)
    raw = data.get(key, {"path": str(path), "stage": "review"})
    raw["path"] = str(path)
    raw["delay_ms"] = delay_ms
    raw["updated_at"] = time.time()
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
    data = _load()
    key = _key(path)
    raw = data.get(key, {"path": str(path), "stage": "review"})
    raw["path"] = str(path)
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
    data[key] = raw
    _save(data)
    return ReviewState(**raw)


def set_transfer(path: str | Path, status: str, *, percent: float | None = None) -> ReviewState:
    """Update the per-entry transfer status shown in the library column.

    ``status`` is one of ``idle|transferring|done|failed``. When ``done`` the
    entry also advances to the ``transferred`` stage.
    """
    data = _load()
    key = _key(path)
    raw = data.get(key, {"path": str(path), "stage": "review"})
    raw["path"] = str(path)
    raw["transfer_status"] = status
    if percent is not None:
        raw["transfer_percent"] = float(percent)
    if status == "done":
        raw["stage"] = "transferred"
        raw["transferred_at"] = time.time()
        raw["transfer_percent"] = 100.0
    raw["updated_at"] = time.time()
    data[key] = raw
    _save(data)
    return ReviewState(**raw)


def forget(path: str | Path) -> None:
    data = _load()
    if data.pop(_key(path), None) is not None:
        _save(data)


def all_states() -> dict[str, ReviewState]:
    return {k: ReviewState(**v) for k, v in _load().items()}


def to_dict(state: ReviewState) -> dict:
    return asdict(state)
