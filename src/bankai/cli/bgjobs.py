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


def jobs_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    candidates: list[Path] = []
    if base:
        candidates.append(Path(base))
    elif os.name == "nt":
        candidates.append(Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home()))
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
        self.meta_path.write_text(json.dumps(asdict(self), indent=2))

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


_FAILURE_REASON_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))\b:?\s*(.*)$")
# A continuation of a wrapped exception message ends when we hit a blank line,
# a new traceback frame / box border, a log timestamp, or a BANKAI marker.
_REASON_BOUNDARY_RE = re.compile(r'^(?:[+|\u2502\u2570\u256d\u2500]|\d{4}-\d\d-\d\d|BANKAI_|File ")')


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


def progress_snapshot(job: BgJob) -> ProgressSnapshot:
    job = job.refresh()
    lines = _read_log_tail(job.log_path, lines=800)
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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def _read_log_tail(path: Path, *, lines: int = 200) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
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


def list_jobs() -> list[BgJob]:
    jobs: list[BgJob] = []
    for meta in sorted(jobs_root().glob("*/meta.json"), reverse=True):
        if meta.parent.name.startswith(".deleted-"):
            shutil.rmtree(meta.parent, ignore_errors=True)
            continue
        try:
            data = json.loads(meta.read_text())
            jobs.append(BgJob(**data).refresh())
        except Exception:
            continue
    return sorted(jobs, key=lambda j: j.started_at, reverse=True)


def get_job(job_id: str) -> BgJob | None:
    for j in list_jobs():
        if j.id == job_id or j.id.startswith(job_id):
            return j
    return None


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
        DETACHED = 0x00000008
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=DETACHED,
        )
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


def spawn(
    *, kind: str, title: str, args: list[str], created_at: float | None = None
) -> BgJob:
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
    cmd = [_bankai_cmd(), *args]
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
