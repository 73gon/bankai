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


def jobs_root() -> Path:
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
    pid: int | None = None
    child_pid: int | None = None
    status: str = "running"  # running | done | failed | cancelled
    finished_at: float | None = None
    exit_code: int | None = None
    final_path: str | None = None

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
        if self.pid is None or not _pid_alive(self.pid):
            return False
        try:
            killpg = getattr(os, "killpg", None)
            getpgid = getattr(os, "getpgid", None)
            if callable(killpg) and callable(getpgid):
                killpg(getpgid(self.pid), signal.SIGTERM)
            else:
                os.kill(self.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(self.pid, signal.SIGTERM)
            except Exception:
                return False
        self.status = "cancelled"
        self.finished_at = time.time()
        self.save()
        return True

    def delete(self) -> bool:
        root = jobs_root().resolve()
        target = self.dir.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return False
        shutil.rmtree(target, ignore_errors=True)
        return True


_FAILURE_REASON_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b:?\s*(.*)$")


def failure_reason(job: BgJob) -> str | None:
    """Best-effort human-readable failure reason from a failed job's log.

    Pulls the exception summary (e.g. ``PermanentWorkerError: no candidate
    met selector criteria ...``) out of the rich traceback so the UI can show
    a one-line ``Reason`` instead of a wall of stack frames.
    """
    try:
        text = job.log_path.read_text(errors="replace")
    except OSError:
        return None
    reason: str | None = None
    for raw in text.splitlines():
        line = raw.strip().strip("|").strip()
        m = _FAILURE_REASON_RE.match(line)
        if m:
            msg = (m.group(2) or "").strip()
            reason = f"{m.group(1)}: {msg}" if msg else m.group(1)
    if not reason:
        for raw in reversed(text.splitlines()):
            s = raw.strip().strip("|").strip()
            if s.startswith("ERROR") or " ERROR " in s:
                reason = s.split("ERROR", 1)[-1].strip(" :")
                break
    if reason:
        reason = re.sub(r"\s+", " ", reason).strip()
        if len(reason) > 240:
            reason = reason[:237] + "..."
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
        return path.read_text(errors="replace").splitlines()[-lines:]
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


def spawn(*, kind: str, title: str, args: list[str]) -> BgJob:
    """Spawn ``bankai <args>`` detached. Returns the BgJob."""
    job = BgJob(
        id=uuid.uuid4().hex[:8],
        kind=kind,
        title=title,
        args=args,
        started_at=time.time(),
    )
    job.dir.mkdir(parents=True, exist_ok=True)
    job.save()
    cmd = [sys.executable, "-m", "bankai.cli.bgjobs", "--supervise", job.id, *args]
    env = os.environ.copy()
    # Persist ANSI colour codes into the on-disk log so the viewer can
    # replay them with Rich; older releases set NO_COLOR=1 here, which is
    # what made `bankai background log` show plain text.
    env.pop("NO_COLOR", None)
    env.setdefault("FORCE_COLOR", "1")
    env.setdefault("TERM", "xterm-256color")
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
    env.setdefault("BANKAI_BG_JOB_ID", job.id)
    job.log_path.parent.mkdir(parents=True, exist_ok=True)
    with job.log_path.open("wb") as log_fh:
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
    if latest.status == "cancelled":
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
        data = job.log_path.read_text(errors="replace")
    except Exception as exc:
        return f"(log unreadable: {exc})"
    out = data.splitlines()[-lines:]
    return "\n".join(out)


def render_tail(job: BgJob, *, lines: int = 50) -> "Any":
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
    "clear_jobs",
    "get_job",
    "jobs_root",
    "list_jobs",
    "progress_snapshot",
    "render_tail",
    "spawn",
    "tail",
    "watch",
]


if __name__ == "__main__":
    raise SystemExit(_main())
