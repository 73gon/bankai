"""Lightweight background-job manager.

Spawns ``bankai run`` (or ``shows``) as a detached subprocess so the
TUI returns to the menu immediately. Each job gets a directory under
``$XDG_STATE_HOME/bankai/jobs/<id>/`` containing:

    meta.json   {id, kind, query, args, started_at, pid, status,
                 finished_at, exit_code, final_path}
    log         combined stdout/stderr stream

The dispatcher / pipeline already persists per-stage state in sqlite;
this module is a *display* layer for the user-friendly queue UI.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_ROOT_CACHE: tuple[tuple, Path] | None = None


def jobs_root() -> Path:
    """The jobs directory, resolved once per environment.

    Every ``job.dir`` and ``job.log_path`` goes through here, so with
    thousands of jobs the ``mkdir`` it used to run on every call was
    thousands of filesystem round trips per queue snapshot.
    """
    global _ROOT_CACHE
    key = (
        os.environ.get("XDG_STATE_HOME"),
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("APPDATA"),
        os.environ.get("HOME"),
    )
    if _ROOT_CACHE is not None and _ROOT_CACHE[0] == key:
        return _ROOT_CACHE[1]
    root = _resolve_jobs_root()
    _ROOT_CACHE = (key, root)
    return root


def _resolve_jobs_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    candidates: list[Path] = []
    if base:
        candidates.append(Path(base))
    elif os.name == "nt":
        candidates.append(
            Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
        )
    else:
        candidates.append(Path.home() / ".local" / "state")
    candidates.append(Path(tempfile.gettempdir()) / "bankai-state")
    last_error: OSError | None = None
    for root in candidates:
        p = root / "bankai" / "jobs"
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("could not determine background job state directory")


@dataclass
class BgJob:
    id: str
    kind: str  # "movie" | "show" | "transfer"
    title: str  # display name
    args: list[str]  # full argv (after "bankai")
    started_at: float
    updated_at: float | None = None
    pid: int | None = None
    child_pid: int | None = None
    status: str = "running"  # running | stopped | done | failed | cancelled
    finished_at: float | None = None
    exit_code: int | None = None
    final_path: str | None = None
    german_source_url: str | None = None
    torrent_source_url: str | None = None
    torrent_source_title: str | None = None
    restart_safe: bool = False

    @property
    def dir(self) -> Path:
        return jobs_root() / self.id

    @property
    def log_path(self) -> Path:
        return self.dir / "log"

    @property
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    def save(self) -> None:
        self.updated_at = time.time()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.meta_path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(self.meta_path)

    def refresh(self) -> BgJob:
        # Older releases classified finished jobs by scanning logs. If a
        # success JSON was later found, repair the stale "failed" metadata.
        if self.status == "failed":
            final_path = _extract_final_path(self.log_path)
            if final_path:
                self.final_path = final_path
                self.status = "done"
                if self.finished_at is None:
                    self.finished_at = time.time()
                if self.exit_code is None:
                    self.exit_code = 0
                self.save()
            return self
        if self.status != "running" or self.pid is None:
            return self
        if not _pid_alive(self.pid):
            # Current supervisor writes the real exit code before exiting.
            # We only reach here when the supervisor vanished without
            # recording a result -- e.g. the web service was restarted and
            # killed the detached pipeline (the Cars 2 case). A job is only
            # genuinely "done" when the pipeline printed a final_path; with
            # no final_path the run never completed, so mark it "failed" so
            # the user can retry instead of seeing a phantom "installed".
            self.finished_at = time.time()
            self.final_path = _extract_final_path(self.log_path)
            self.status = "done" if self.final_path else "failed"
            self.save()
        return self

    def cancel(self) -> bool:
        if not self._terminate():
            return False
        self.status = "cancelled"
        self.finished_at = time.time()
        self.save()
        return True

    def stop(self) -> bool:
        """Pause a job so this same ledger entry can be continued later."""
        if self.status == "running" and not self._terminate():
            return False
        if self.status not in {"running", "cancelled", "stopped"}:
            return False
        self.status = "stopped"
        self.finished_at = time.time()
        self.save()
        return True

    def _terminate(self) -> bool:
        if self.pid is None or not _pid_alive(self.pid):
            return self.status == "cancelled"
        try:
            if sys.platform == "win32":
                proc = subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                )
                if proc.returncode != 0 and _pid_alive(self.pid):
                    return False
            else:
                killpg = getattr(os, "killpg", None)
                getpgid = getattr(os, "getpgid", None)
                if callable(killpg) and callable(getpgid):
                    killpg(getpgid(self.pid), signal.SIGTERM)
                else:
                    os.kill(self.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
            try:
                os.kill(self.pid, signal.SIGTERM)
            except Exception:
                return False
        return True

    def delete(self) -> bool:
        root = jobs_root().resolve()
        target = self.dir.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return False
        if not target.exists():
            return True
        # Move the ledger out of the scanner immediately. Windows can retain
        # short-lived handles to meta/log files even after the worker exits;
        # the old ignore_errors=True path reported success while the visible
        # queue row remained in place.
        tombstone = root / f".deleted-{self.id}-{uuid.uuid4().hex[:8]}"
        try:
            target.replace(tombstone)
            target = tombstone
        except OSError:
            pass
        for attempt in range(5):
            try:
                shutil.rmtree(target)
            except OSError:
                if attempt < 4:
                    time.sleep(0.1 * (attempt + 1))
            if not target.exists():
                return True
        # A successfully renamed tombstone is already absent from list_jobs;
        # a later scan will retry its cleanup.
        return target.name.startswith(".deleted-")


_FAILURE_REASON_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))\b:?\s*(.*)$"
)
_SOURCE_VIDEO_FPS_RE = re.compile(
    r"\[visual-sync\]\s+source nominal frame rate\s+(?P<fps>\d+(?:\.\d+)?)fps",
    re.IGNORECASE,
)
# A continuation of a wrapped exception message ends when we hit a blank line,
# a new traceback frame / box border, a log timestamp, or a BANKAI marker.
_REASON_BOUNDARY_RE = re.compile(
    r'^(?:[+|\u2502\u2570\u256d\u2500]|\d{4}-\d\d-\d\d|BANKAI_|File ")'
)


def failure_reason(job: BgJob) -> str | None:
    """Best-effort human-readable failure reason from a failed job's log.

    ``rich`` renders the exception summary (e.g. ``PermanentWorkerError: no
    candidate met selector criteria ...``) below the traceback box and wraps
    it across lines at the console width. We locate the last such summary and
    stitch the wrapped continuation lines back together so the UI can show a
    single clean ``Reason`` instead of a wall of stack frames.
    """
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    last_idx: int | None = None
    for i, raw in enumerate(lines):
        if _FAILURE_REASON_RE.match(raw.strip()):
            last_idx = i
    if last_idx is None:
        for raw in reversed(lines):
            s = raw.strip().strip("|").strip()
            if s.startswith("ERROR") or " ERROR " in s:
                return _shorten_reason(s.split("ERROR", 1)[-1].strip(" :"))
        return None
    parts = [lines[last_idx].strip()]
    for raw in lines[last_idx + 1 :]:
        s = raw.strip()
        if not s or _REASON_BOUNDARY_RE.match(s) or _FAILURE_REASON_RE.match(s):
            break
        parts.append(s)
    return _shorten_reason(" ".join(parts))


def source_video_fps(job: BgJob) -> float | None:
    """Return the German source video's declared FPS recorded in a job log."""

    for line in reversed(_read_log_tail(job.log_path, lines=800)):
        match = _SOURCE_VIDEO_FPS_RE.search(line)
        if match:
            try:
                fps = float(match.group("fps"))
                return fps if fps > 0 else None
            except ValueError:
                return None
    return None


def _shorten_reason(reason: str) -> str | None:
    reason = re.sub(r"\s+", " ", reason).strip()
    if not reason:
        return None
    if len(reason) > 400:
        reason = reason[:397] + "..."
    return reason


@dataclass(frozen=True, slots=True)
class ProgressPart:
    label: str
    percent: float | None = None
    speed: int | None = None
    eta: int | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    step: int | None
    total_steps: int | None
    step_key: str | None
    step_label: str
    overall_percent: float | None
    parts: dict[str, ProgressPart]


_STAGE_RE = re.compile(
    r"BANKAI_STAGE\s+step=(?P<step>\d+)\s+total=(?P<total>\d+)\s+key=(?P<key>\w+)"
    r"\s+label=\"(?P<label>[^\"]+)\""
)
_PROGRESS_RE = re.compile(r"BANKAI_PROGRESS\s+(?P<data>.+)$")
_RSYNC_PERCENT_RE = re.compile(r"(?P<pct>\d{1,3})%")


def _unwrap_markers(lines: list[str]) -> list[str]:
    """Rejoin markers Rich split across console columns.

    Logs written before the job console was widened wrap mid-value, leaving
    ``label="Download`` on one line and ``from Nyaa"`` on the next, which no
    marker regex can match. An odd number of quotes after the marker is the
    reliable tell that the value is still open.
    """
    out: list[str] = []
    for line in lines:
        text = line.rstrip()
        if out and _has_open_quote(out[-1]):
            out[-1] = f"{out[-1]} {text.strip()}"
            continue
        out.append(text)
    return out


def _has_open_quote(line: str) -> bool:
    start = max(line.find("BANKAI_STAGE"), line.find("BANKAI_PROGRESS"))
    return start >= 0 and line.count('"', start) % 2 == 1


def progress_snapshot(job: BgJob) -> ProgressSnapshot:
    job = job.refresh()
    lines = _unwrap_markers(_read_log_tail(job.log_path, lines=800))
    step: int | None = None
    total: int | None = None
    step_key: str | None = None
    step_label = _default_step_label(job)
    parts: dict[str, ProgressPart] = {}

    for line in lines:
        stage_match = _STAGE_RE.search(line)
        if stage_match:
            step = int(stage_match.group("step"))
            total = int(stage_match.group("total"))
            step_key = stage_match.group("key")
            step_label = stage_match.group("label")
            continue

        progress_match = _PROGRESS_RE.search(line)
        if progress_match:
            data = _parse_progress_data(progress_match.group("data"))
            stage = str(data.get("stage") or "")
            if not stage:
                continue
            parts[stage] = ProgressPart(
                label=_part_label(stage),
                percent=_parse_percent(data.get("pct")),
                speed=_parse_int(data.get("speed")),
                eta=_parse_int(data.get("eta")),
                status=str(data.get("status") or data.get("state") or "") or None,
            )
            if stage == "transfer" and job.kind == "transfer":
                step = 1
                total = 1
                step_key = "transfer"
                step_label = "Transfer files"
            elif stage == "repack" and job.kind == "repack":
                step = 1
                total = 1
                step_key = "repack"
                step_label = "Repacking audio"
            elif stage in {"replace", "torrent"} and job.kind == "torrent_replace":
                step = 1
                total = 1
                step_key = stage
                step_label = "Replacing torrent"
            continue

        fallback_stage = _fallback_stage(line)
        if fallback_stage is not None:
            step, total, step_key, step_label = fallback_stage
            continue

        if job.kind == "transfer":
            rsync_match = _RSYNC_PERCENT_RE.search(line)
            if rsync_match:
                pct = float(rsync_match.group("pct"))
                parts["transfer"] = ProgressPart(label="Transfer", percent=pct)
                step = 1
                total = 1
                step_key = "transfer"
                step_label = "Transfer files"

    overall: float | None
    if job.status == "done":
        overall = 100.0
    else:
        overall = _overall_percent(step=step, total=total, step_key=step_key, parts=parts)

    return ProgressSnapshot(
        step=step,
        total_steps=total,
        step_key=step_key,
        step_label=step_label,
        overall_percent=overall,
        parts=parts,
    )


def _fallback_stage(line: str) -> tuple[int, int, str, str] | None:
    lowered = line.lower()
    if "stage 1/4" in lowered or "stage=extract" in lowered:
        return 1, 4, "extract", "Extract stream audio"
    if "stage 2/4" in lowered or "stage=torrent" in lowered:
        return 2, 4, "torrent", "Download HQ video"
    if "stage 3/4" in lowered or "stage=sync" in lowered:
        return 3, 4, "sync", "Sync audio"
    if "stage 4/4" in lowered or "stage=remux" in lowered:
        return 4, 4, "remux", "Write final MKV"
    return None


def _default_step_label(job: BgJob) -> str:
    if job.status == "done":
        return "Done"
    if job.status == "failed":
        return "Failed"
    if job.kind == "transfer":
        return "Waiting to transfer"
    if job.kind == "repack":
        return "Waiting to repack"
    if job.kind == "torrent_replace":
        return "Waiting to replace torrent"
    return "Starting"


def _parse_progress_data(raw: str) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _parse_percent(value: object) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(100.0, float(str(value))))
    except ValueError:
        return None


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def _part_label(stage: str) -> str:
    return {
        "stream": "Filmpalast audio",
        "torrent": "HQ video",
        "transfer": "Transfer",
        "repack": "Repack",
        "replace": "Replace torrent",
    }.get(stage, stage.replace("_", " ").title())


def _overall_percent(
    *,
    step: int | None,
    total: int | None,
    step_key: str | None,
    parts: dict[str, ProgressPart],
) -> float | None:
    if step is None or total is None or total <= 0:
        for part in parts.values():
            if part.percent is not None:
                return part.percent
        return None
    stage_progress = 0.0
    if step_key == "extract":
        stage_progress = parts.get("stream", ProgressPart("")).percent or 0.0
    elif step_key:
        stage_progress = parts.get(step_key, ProgressPart("")).percent or 0.0
    return max(0.0, min(100.0, ((step - 1) + stage_progress / 100.0) / total * 100.0))


def _windows_pid_alive(pid: int) -> bool:
    """Query a process without sending Windows console-control events."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # Access denied still means it exists.
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return ctypes.get_last_error() == 5
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            return _windows_pid_alive(pid)
        os.kill(pid, 0)
        return True
    except PermissionError:
        # A process owned by another Windows service account can deny the
        # query even though it is alive. Treat access denied as existence;
        # otherwise a harmless cross-account status read rewrites a running
        # job as failed while its process tree continues in the background.
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _read_log_tail(path: Path, *, lines: int = 200) -> list[str]:
    try:
        # Progress polling used to decode the entire lifetime log on every
        # refresh. Long-running downloads can produce multi-megabyte logs, so
        # read backwards until the requested tail is complete instead.
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            chunks: list[bytes] = []
            newline_count = 0
            while position > 0 and newline_count <= lines:
                size = min(64 * 1024, position)
                position -= size
                handle.seek(position)
                chunk = handle.read(size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
            data = b"".join(reversed(chunks))
        return data.decode("utf-8", errors="replace").splitlines()[-lines:]
    except Exception:
        return []


def _extract_final_path(path: Path) -> str | None:
    final_re = re.compile(r'"final_path"\s*:\s*"([^"]+)"')
    for line in reversed(_read_log_tail(path)):
        match = final_re.search(line)
        if match:
            return match.group(1)
    return None


def _log_looks_failed(path: Path) -> bool:
    for line in reversed(_read_log_tail(path)):
        low = line.lower()
        if "workererror" in low or "traceback" in low or "error:" in low:
            return True
    return False


# job dir -> (identity of its meta.json and log, the job as last read)
_JOB_CACHE: dict[str, tuple[tuple, BgJob]] = {}


def _file_key(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _read_job(directory: Path) -> BgJob | None:
    """One job, re-read only when its files changed.

    Parsing every meta.json on every call -- and, for each failed job,
    scanning its whole log for a late success line -- was thousands of reads
    per queue snapshot, and the sidebar, the queue and the scheduler all take
    snapshots. A finished job whose meta.json and log are as they were is the
    same job. A running one is refreshed every time: that is its liveness check.
    """
    meta = directory / "meta.json"
    meta_key = _file_key(meta)
    if meta_key is None:
        _JOB_CACHE.pop(str(directory), None)
        return None
    key = (meta_key, _file_key(directory / "log"))
    hit = _JOB_CACHE.get(str(directory))
    if hit is not None and hit[0] == key and hit[1].status != "running":
        # A copy: callers change fields before saving, and must not change
        # what the next caller is handed.
        return copy.copy(hit[1])
    try:
        job = BgJob(**json.loads(meta.read_text())).refresh()
    except Exception:
        return None
    # refresh() may have saved; key on what is on disk now.
    _JOB_CACHE[str(directory)] = ((_file_key(meta), _file_key(directory / "log")), job)
    return copy.copy(job)


def list_jobs() -> list[BgJob]:
    jobs: list[BgJob] = []
    root = jobs_root()
    seen: set[str] = set()
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name.startswith(".deleted-"):
            shutil.rmtree(entry.path, ignore_errors=True)
            continue
        seen.add(entry.path)
        job = _read_job(Path(entry.path))
        if job is not None:
            jobs.append(job)
    for gone in set(_JOB_CACHE) - seen:
        _JOB_CACHE.pop(gone, None)
    return sorted(jobs, key=lambda j: j.started_at, reverse=True)


def get_job(job_id: str) -> BgJob | None:
    """One job by id, or by a unique-enough prefix of one, without reading the rest."""
    if not job_id or "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
        return None
    root = jobs_root()
    exact = _read_job(root / job_id)
    if exact is not None:
        return exact
    try:
        names = sorted((entry.name for entry in os.scandir(root) if entry.is_dir()), reverse=True)
    except OSError:
        return None
    for name in names:
        if name.startswith(job_id) and not name.startswith(".deleted-"):
            job = _read_job(root / name)
            if job is not None:
                return job
    return None


def archive_finished_jobs(
    *,
    keep_ids: set[str] | frozenset[str] = frozenset(),
    older_than_days: float = 30.0,
    dry_run: bool = False,
) -> int:
    """Move finished jobs nobody needs out of the jobs directory.

    Every queue snapshot visits every job, and a week of retries left
    thousands of them. Two kinds go, into ``jobs_archive`` beside it -- moved,
    not deleted, logs intact:

    * an anime download superseded by a newer job for the same release: the
      newest attempt is the one the queue shows and the one a retry replaces;
    * any job finished longer than ``older_than_days`` ago -- except a
      completed movie or show, which is what marks a title as already added
      in Search and Discover.

    Nothing running or stopped is touched, nor anything in ``keep_ids``: the
    jobs releases still point at. ``dry_run`` counts without moving anything.
    """
    now = time.time()
    finished = {"done", "failed", "cancelled"}
    jobs = list_jobs()
    newest_for_release: dict[str, BgJob] = {}
    for job in jobs:
        if job.args and job.args[0] == "anime-download":
            info_hash = argument_value(job.args, "--info-hash")
            if not info_hash:
                continue
            current = newest_for_release.get(info_hash)
            if current is None or job.started_at > current.started_at:
                newest_for_release[info_hash] = job

    def superseded(job: BgJob) -> bool:
        if not job.args or job.args[0] != "anime-download":
            return False
        newest = newest_for_release.get(argument_value(job.args, "--info-hash") or "")
        return newest is not None and newest.id != job.id

    def old(job: BgJob) -> bool:
        ended = job.finished_at or job.updated_at or job.started_at
        if now - ended < older_than_days * 86400:
            return False
        return not (job.status == "done" and job.kind in {"movie", "show"})

    archive = jobs_root().parent / "jobs_archive"
    moved = 0
    for job in jobs:
        if job.status not in finished or job.id in keep_ids:
            continue
        if not (superseded(job) or old(job)):
            continue
        if dry_run:
            moved += 1
            continue
        try:
            archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(job.dir), str(archive / job.id))
        except OSError:
            continue
        _JOB_CACHE.pop(str(job.dir), None)
        moved += 1
    return moved


def argument_value(args: list[str], option: str) -> str | None:
    """Return the last value supplied for a simple CLI option."""
    value: str | None = None
    for index, arg in enumerate(args):
        if arg == option and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith(f"{option}="):
            value = arg.split("=", 1)[1]
    return value


def set_provenance(
    job_id: str,
    *,
    german_source_url: str | None = None,
    torrent_source_url: str | None = None,
    torrent_source_title: str | None = None,
) -> bool:
    """Persist source provenance on a running background-job ledger."""
    job = _load_job(job_id)
    if job is None:
        return False
    if german_source_url is not None:
        job.german_source_url = german_source_url
    if torrent_source_url is not None:
        job.torrent_source_url = torrent_source_url
    if torrent_source_title is not None:
        job.torrent_source_title = torrent_source_title
    job.save()
    return True


def launch_windows_detached(command: list[str], cwd: Path) -> int:
    """Launch via WMI, outside the web service job object; never show a window."""
    quoted = subprocess.list2cmdline(command).replace("'", "''")
    directory = str(cwd).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';"
        "$startup=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
        "-Property @{ShowWindow=[uint16]0;CreateFlags=[uint32]16777216};"
        "$result=Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine='{quoted}';CurrentDirectory='{directory}';"
        "ProcessStartupInformation=$startup};"
        "if($result.ReturnValue -ne 0){throw ('Worker launch failed: '+$result.ReturnValue)};"
        "$result.ProcessId"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Worker launch failed").strip())
    return int(result.stdout.strip())


def _launch(job: BgJob) -> BgJob:
    job.dir.mkdir(parents=True, exist_ok=True)
    job.save()
    cmd = [sys.executable, "-m", "bankai.cli.bgjobs", "--supervise", job.id, *job.args]
    env = os.environ.copy()
    # Persist ANSI colour codes into the on-disk log so the viewer can
    # replay them with Rich; older releases set NO_COLOR=1 here, which is
    # what made `bankai background log` show plain text.
    env.pop("NO_COLOR", None)
    env.setdefault("FORCE_COLOR", "1")
    env.setdefault("TERM", "xterm-256color")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("BANKAI_BG_JOB_ID", job.id)
    if sys.platform == "win32":
        # WMI does not inherit the service environment. Restore it from an
        # owner-scoped temporary file, not a command line containing secrets.
        payload = job.dir / "launch.json"
        payload.write_text(
            json.dumps({"env": env, "id": job.id, "args": job.args}), encoding="utf-8"
        )
        job.restart_safe = True
        job.save()
        try:
            job.pid = launch_windows_detached(
                [sys.executable, "-m", "bankai.cli.bgjobs", "--launch-job", str(payload)],
                Path.cwd(),
            )
        except Exception:
            payload.unlink(missing_ok=True)
            job.status = "failed"
            job.save()
            raise
        job.save()
        return job
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    job.pid = proc.pid
    job.save()
    return job


def spawn(*, kind: str, title: str, args: list[str], created_at: float | None = None) -> BgJob:
    """Spawn ``bankai <args>`` detached. Returns the BgJob."""
    return _launch(
        BgJob(
            id=uuid.uuid4().hex[:8],
            kind=kind,
            title=title,
            args=args,
            started_at=created_at or time.time(),
            german_source_url=argument_value(args, "--url"),
        )
    )


def resume(job: BgJob) -> BgJob:
    """Continue a stopped job using its original id and arguments."""
    if job.status != "stopped":
        raise ValueError("only stopped jobs can be continued")
    job.updated_at = time.time()
    job.pid = None
    job.child_pid = None
    job.status = "running"
    job.finished_at = None
    job.exit_code = None
    job.final_path = None
    return _launch(job)


def _bankai_cmd() -> str:
    """Resolve a callable bankai entry point (sibling of current python)."""
    cand = Path(sys.executable).with_name("bankai")
    if cand.exists():
        return str(cand)
    return "bankai"


def _load_job(job_id: str) -> BgJob | None:
    meta = jobs_root() / job_id / "meta.json"
    try:
        return BgJob(**json.loads(meta.read_text()))
    except Exception:
        return None


def _supervise(job_id: str, args: list[str]) -> int:
    """Run the real bankai command, capture logs, and persist final status."""
    job = _load_job(job_id)
    if job is None:
        return 2
    # Avoid holding bankai.exe open on Windows while the package is updated.
    cmd = (
        [sys.executable, "-m", "bankai.cli.main", *args]
        if sys.platform == "win32"
        else [_bankai_cmd(), *args]
    )
    env = os.environ.copy()
    env.pop("NO_COLOR", None)
    env.setdefault("FORCE_COLOR", "1")
    env.setdefault("TERM", "xterm-256color")
    # Log messages contain Unicode (arrows, ellipses). Under a Windows service
    # the child's stdout defaults to cp1252, which raises UnicodeEncodeError
    # inside the log handler. Force UTF-8 so the log stream never crashes.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("BANKAI_BG_JOB_ID", job.id)
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve output across stop/continue cycles.
    with job.log_path.open("ab") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        job.child_pid = proc.pid
        job.save()
        exit_code = proc.wait()

    latest = _load_job(job_id) or job
    if latest.status in {"cancelled", "stopped"}:
        return exit_code
    latest.exit_code = exit_code
    latest.finished_at = time.time()
    latest.final_path = _extract_final_path(latest.log_path)
    latest.status = "done" if exit_code == 0 else "failed"
    latest.save()
    return exit_code


def clear_jobs(*, statuses: set[str]) -> int:
    count = 0
    for job in list_jobs():
        if job.status in statuses and job.delete():
            count += 1
    return count


def tail(job: BgJob, *, lines: int = 50) -> str:
    if not job.log_path.exists():
        return "(no log yet)"
    try:
        data = job.log_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"(log unreadable: {exc})"
    out = data.splitlines()[-lines:]
    return "\n".join(out)


def render_tail(job: BgJob, *, lines: int = 50) -> Any:
    """Return a Rich-renderable that preserves ANSI colours from the log.

    Background workers run with ``FORCE_COLOR=1``, so the log file on
    disk already contains the ANSI escape sequences emitted by Rich. We
    convert those back into styled :class:`rich.text.Text` here so the
    interactive log viewer shows colour instead of brackets like
    ``[green]done[/green]``.
    """
    from rich.text import Text

    raw = tail(job, lines=lines)
    return Text.from_ansi(raw)


def watch(job: BgJob) -> None:
    """Follow the job's log until it ends or user hits Ctrl-C."""
    if not job.log_path.exists():
        time.sleep(0.5)
    pos = 0
    try:
        while True:
            try:
                with job.log_path.open("rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                    if chunk:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.flush()
            except FileNotFoundError:
                pass
            j = job.refresh()
            if j.status != "running":
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        return


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--launch-job":
        payload_path = Path(args[1])
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        os.environ.update(payload["env"])
        payload_path.unlink(missing_ok=True)
        # Wait for the WMI PID to be persisted before writing child/result fields.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = _load_job(payload["id"])
            if job is not None and job.pid is not None:
                return _supervise(job.id, payload["args"])
            time.sleep(0.05)
        return 2
    if len(args) >= 2 and args[0] == "--supervise":
        return _supervise(args[1], args[2:])
    print("usage: python -m bankai.cli.bgjobs --supervise JOB_ID [bankai args...]", file=sys.stderr)
    return 2


__all__ = [
    "BgJob",
    "ProgressPart",
    "ProgressSnapshot",
    "argument_value",
    "clear_jobs",
    "get_job",
    "jobs_root",
    "list_jobs",
    "progress_snapshot",
    "render_tail",
    "resume",
    "set_provenance",
    "spawn",
    "tail",
    "watch",
]


if __name__ == "__main__":
    raise SystemExit(_main())
