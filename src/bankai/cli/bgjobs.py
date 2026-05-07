"""Lightweight background-job manager.

Spawns ``bankai run`` (or ``series``) as a detached subprocess so the
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
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


def jobs_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    p = Path(base) / "bankai" / "jobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class BgJob:
    id: str
    kind: str  # "movie" | "series"
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
            # This fallback is for jobs spawned by older versions.
            self.finished_at = time.time()
            self.final_path = _extract_final_path(self.log_path)
            self.status = (
                "done" if self.final_path or not _log_looks_failed(self.log_path) else "failed"
            )
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
    # Force colourless rich output in background logs.
    env.setdefault("NO_COLOR", "1")
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
    env.setdefault("NO_COLOR", "1")
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
    "clear_jobs",
    "get_job",
    "jobs_root",
    "list_jobs",
    "spawn",
    "tail",
    "watch",
]


if __name__ == "__main__":
    raise SystemExit(_main())
