"""Commit-based updates for the Windows bankai-web service.

The update worker is created through WMI, outside nssm's job object.
Its persisted maintenance flag stops all new web jobs while independent workers continue.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from bankai.cli import bgjobs

_LOCK = threading.RLock()
_CHECK_LOCK = threading.Lock()
_ACTIVE_PHASES = {"waiting", "applying", "restarting"}
_CHECK_TASK: asyncio.Task | None = None


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _path() -> Path:
    return bgjobs.jobs_root().parent / "updates.json"


def _read() -> dict:
    try:
        row = json.loads(_path().read_text(encoding="utf-8"))
        return row if isinstance(row, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write(row: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _patch(**fields: object) -> dict:
    with _LOCK:
        row = {**_read(), **fields, "updated_at": time.time()}
        _write(row)
        return row


def maintenance_active() -> bool:
    with _LOCK:
        row = _read()
        if row.get("phase") not in _ACTIVE_PHASES:
            return False
        # A dead helper must not leave the queue held forever.
        if time.time() - row.get("updated_at", 0) > 120:
            pid = row.get("pid")
            if not pid or not bgjobs._pid_alive(int(pid)):
                _patch(phase="failed", error="Update worker exited. Queued work has resumed.")
                return False
        return True


def _run(args: list[str], *, timeout: int = 30, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError(
            (result.stderr or result.stdout or "Update command failed").strip()[:700]
        )
    return result.stdout.strip()


def _git(*args: str) -> str:
    repo = _repo()
    return _run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args], timeout=60)


def _validate_checkout() -> None:
    if _git("branch", "--show-current") != "main":
        raise RuntimeError("Updates require the main branch.")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Local tracked changes must be resolved before updating.")


def check() -> dict:
    with _CHECK_LOCK:
        if maintenance_active():
            return status()
        fields: dict = {"checked_at": time.time()}
        try:
            _validate_checkout()
            current = _git("rev-parse", "HEAD")
            _git("fetch", "--quiet", "origin", "main")
            latest = _git("rev-parse", "origin/main")
            ahead, behind = map(
                int, _git("rev-list", "--left-right", "--count", "HEAD...origin/main").split()
            )
            if ahead:
                raise RuntimeError(
                    "Local main has commits outside origin/main; automatic update is unavailable."
                )
            supported = os.name == "nt"
            if supported:
                _run(["sc.exe", "query", "bankai-web"])
            fields.update(
                current_commit=current,
                latest_commit=latest,
                commits_behind=behind,
                available=behind > 0,
                supported=supported,
                error=None,
                unavailable_reason=None
                if supported
                else "Automatic updates require the Windows bankai-web service.",
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            fields.update(available=False, supported=False, error=str(exc))
        with _LOCK:
            if not maintenance_active():
                _patch(**fields)
        return status()


def trigger_check() -> None:
    global _CHECK_TASK
    if (
        not maintenance_active()
        and time.time() - _read().get("checked_at", 0) >= 300
        and (_CHECK_TASK is None or _CHECK_TASK.done())
    ):
        _CHECK_TASK = asyncio.create_task(asyncio.to_thread(check))


def status() -> dict:
    maintenance_active()
    row = _read()
    return {
        "phase": row.get("phase", "idle"),
        "available": row.get("available", False),
        "supported": row.get("supported", False),
        "checking": _CHECK_TASK is not None and not _CHECK_TASK.done(),
        "current_commit": row.get("current_commit"),
        "latest_commit": row.get("latest_commit"),
        "commits_behind": row.get("commits_behind", 0),
        "checked_at": row.get("checked_at"),
        "active_jobs": row.get("active_jobs", 0),
        "detail": row.get("detail"),
        "error": row.get("error"),
        "unavailable_reason": row.get("unavailable_reason"),
    }


def _launch_worker() -> int:
    return bgjobs.launch_windows_detached(
        [
            sys.executable,
            "-m",
            "bankai.web.updates",
            "--run",
            "--state",
            str(_path()),
            "--repo",
            str(_repo()),
            "--config",
            os.environ.get("BANKAI_CONFIG", str(_repo() / "config.toml")),
        ],
        _repo(),
    )


def start() -> dict:
    from bankai.web import jobs

    result = check()
    with jobs._LOCK, _LOCK:
        if maintenance_active():
            return status()
        repair = (
            result["phase"] == "failed" and _read().get("target_commit") == result["current_commit"]
        )
        if not result["supported"] or not (result["available"] or repair):
            raise RuntimeError(
                result["error"] or result["unavailable_reason"] or "Bankai is already up to date."
            )
        _validate_checkout()
        _patch(
            phase="waiting",
            error=None,
            pid=None,
            target_commit=result["latest_commit"],
            detail="Preparing update; running work will be preserved.",
        )
        try:
            pid = _launch_worker()
            _patch(pid=pid)
        except Exception as exc:
            _patch(phase="failed", error=str(exc))
            raise
    return status()


def _prepare_restart() -> None:
    """Only legacy service-owned workers need checkpoint/continue."""
    for job in bgjobs.list_jobs():
        if job.status != "running" or job.restart_safe:
            continue
        interrupted = list(_read().get("interrupted_jobs", []))
        if job.id not in interrupted:
            interrupted.append(job.id)
            _patch(interrupted_jobs=interrupted)
        # Do not pause/remove qBittorrent: the rerun reuses its existing hash.
        if not job.stop():
            raise RuntimeError(f"Could not safely stop legacy job {job.id}.")


def _resume_interrupted() -> None:
    for job_id in list(_read().get("interrupted_jobs", [])):
        job = bgjobs._load_job(job_id)
        if job is None:
            raise RuntimeError(f"Interrupted job {job_id} could not be recovered.")
        if job.status == "stopped":
            bgjobs.resume(job)
        elif job.status not in {"running", "done"}:
            raise RuntimeError(f"Interrupted job {job_id} is not resumable.")
        remaining = [item for item in _read().get("interrupted_jobs", []) if item != job_id]
        _patch(interrupted_jobs=remaining)


def _idle() -> tuple[bool, int]:
    """Ready means no service-owned work, not an empty never-ending queue."""
    jobs = bgjobs.list_jobs()
    active = [job for job in jobs if job.status == "running"]
    script = (
        "ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process | "
        "Where-Object {$_.Name -match '^(python|bankai)'} | "
        "Select-Object ProcessId,ParentProcessId,CommandLine)"
    )
    processes = json.loads(
        _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]) or "[]"
    )
    owned = {job.pid for job in active if job.restart_safe and job.pid}
    # Account for venv wrappers and all worker descendants, not just child_pid.
    changed = True
    while changed:
        before = len(owned)
        owned.update(row["ProcessId"] for row in processes if row["ParentProcessId"] in owned)
        changed = len(owned) != before
    unsafe = any(
        row["ProcessId"] not in owned
        and any(
            token in (row.get("CommandLine") or "")
            for token in ("anime-download", "bankai.cli.bgjobs")
        )
        for row in processes
    ) or any(not job.restart_safe for job in active)
    response = httpx.get("http://localhost:9988/api/anime/automation", timeout=15)
    response.raise_for_status()
    checking = response.json().get("running", False)
    return not unsafe and not checking, len(active)


def _apply(target: str) -> None:
    _validate_checkout()
    _git("fetch", "--quiet", "origin", "main")
    dependencies_changed = bool(
        _read().get("dependencies_required") or _git("diff", "HEAD", target, "--", "pyproject.toml")
    )
    if dependencies_changed:
        _patch(dependencies_required=True)
    _git("merge", "--ff-only", target)
    if _git("rev-parse", "HEAD") != target:
        raise RuntimeError("The requested commit could not be applied.")
    if not (_repo() / "src/bankai/web/static/index.html").is_file():
        raise RuntimeError("This update does not include the built web interface.")
    _patch(phase="applying", detail="Installing the update.")
    if dependencies_changed:
        _run([sys.executable, "-m", "pip", "install", "-e", ".[web]"], timeout=900, cwd=_repo())
        _patch(dependencies_required=False)
    _patch(phase="restarting", detail="Restarting Bankai.")
    _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Restart-Service bankai-web",
        ],
        timeout=60,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            response = httpx.get("http://localhost:9988/api/health", timeout=5)
            if response.status_code == 200:
                _resume_interrupted()
                _patch(
                    phase="done",
                    available=False,
                    current_commit=target,
                    latest_commit=target,
                    commits_behind=0,
                    detail="Update installed. Bankai is ready.",
                    error=None,
                )
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("Bankai did not become healthy after the update.")


def run_worker() -> None:
    try:
        target = _read()["target_commit"]
        if (
            not isinstance(target, str)
            or len(target) != 40
            or any(c not in "0123456789abcdef" for c in target)
        ):
            raise RuntimeError("Invalid update commit.")
        _prepare_restart()
        deadline = time.time() + 120
        while time.time() < deadline:
            idle, active = _idle()
            _patch(
                active_jobs=active,
                detail=f"{active} independent workers will continue during the update.",
            )
            if idle:
                break
            time.sleep(15)
        else:
            raise RuntimeError(
                "Could not establish restart safety within two minutes. Queued work has resumed."
            )
        _patch(phase="applying", detail="Applying the new commit.")
        _apply(target)
    except Exception as exc:
        try:
            _resume_interrupted()
        except Exception as recovery:
            exc = RuntimeError(f"{exc} Recovery pending: {recovery}")
        _patch(phase="failed", error=str(exc), detail="Update failed. Queued work has resumed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    state_path = arguments.state.resolve()
    repository = arguments.repo.resolve()
    os.environ["XDG_STATE_HOME"] = str(state_path.parent.parent)
    os.environ["BANKAI_CONFIG"] = arguments.config

    def _path() -> Path:
        return state_path

    def _repo() -> Path:
        return repository

    run_worker()
