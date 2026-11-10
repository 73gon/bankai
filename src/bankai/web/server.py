"""Uvicorn runner + systemd user-service installer for the web UI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

SERVICE_NAME = "bankai-web.service"
# Set when running as the server (not in tests): the app's shutdown arms ``_exit_soon``.
exit_after_shutdown = False


def run_server(*, host: str | None = None, port: int | None = None) -> None:
    """Run the FastAPI app with uvicorn (blocking)."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("uvicorn is not installed. Install the web extra:\n  pip install 'bankai[web]'") from exc

    settings = get_settings()
    bind_host = host or settings.web.host
    bind_port = port or settings.web.port
    from bankai.web.app import create_app

    app = create_app()
    log.info("starting bankai web on http://%s:%s", bind_host, bind_port)
    global exit_after_shutdown
    exit_after_shutdown = True
    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info")


def _exit_soon(grace: float = 10.0) -> None:
    """The server has stopped: the process exits within ``grace`` seconds, even while a worker thread runs.

    Python waits for every worker thread before it exits: a pass reading a file over 9p held a restart
    60 s after the app had shut down (Nov 2026). State files are written beside and renamed into place,
    so one cut off leaves the last one whole -- as systemd's kill after 90 s did.
    """
    import logging
    import os
    import threading

    def leave() -> None:
        busy = [t.name for t in threading.enumerate() if t is not threading.current_thread() and not t.daemon and t.is_alive()]
        logging.getLogger(__name__).warning("Exiting with %d thread(s) still at work: %s", len(busy), ", ".join(busy[:8]))
        logging.shutdown()
        os._exit(0)

    timer = threading.Timer(grace, leave)
    timer.daemon = True
    timer.start()


def _systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _service_unit(*, exec_path: str, host: str, port: int, workdir: str) -> str:
    return f"""[Unit]
Description=bankai web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_path} web serve --host {host} --port {port}
WorkingDirectory={workdir}
Restart=on-failure
RestartSec=3
# Only kill the uvicorn process on stop/restart. Background pipeline jobs
# are spawned detached (new session); without this a deploy restart would
# kill an in-flight download mid-run (the Cars 2 phantom-"done" bug).
KillMode=process
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def install_service(*, host: str | None = None, port: int | None = None, enable: bool = True) -> Path:
    """Write and (optionally) enable+start the bankai-web user service.

    Returns the path to the written unit file. Raises RuntimeError if
    systemd is unavailable.
    """
    settings = get_settings()
    bind_host = host or settings.web.host
    bind_port = port or settings.web.port
    exec_path = shutil.which("bankai") or str(Path(sys.executable).with_name("bankai"))
    workdir = str(Path.cwd())
    unit_path = _systemd_user_dir() / SERVICE_NAME
    unit_path.write_text(_service_unit(exec_path=exec_path, host=bind_host, port=bind_port, workdir=workdir))

    systemctl = shutil.which("systemctl")
    if systemctl is None:
        raise RuntimeError("systemctl not found; unit written but not enabled")

    # Enable lingering so the user service runs without an active login.
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    loginctl = shutil.which("loginctl")
    if loginctl and user:
        subprocess.run([loginctl, "enable-linger", user], check=False)

    subprocess.run([systemctl, "--user", "daemon-reload"], check=False)
    if enable:
        subprocess.run([systemctl, "--user", "enable", SERVICE_NAME], check=False)
        subprocess.run([systemctl, "--user", "restart", SERVICE_NAME], check=False)
    return unit_path


def service_status() -> dict:
    """Return a small dict describing the systemd service state."""
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return {"available": False, "active": False, "detail": "systemctl not found"}
    proc = subprocess.run(
        [systemctl, "--user", "is-active", SERVICE_NAME],
        capture_output=True,
        text=True,
    )
    state = (proc.stdout or proc.stderr or "").strip()
    return {"available": True, "active": state == "active", "detail": state}
