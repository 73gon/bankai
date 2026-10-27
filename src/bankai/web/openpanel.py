"""OpenPanel, the manga reader: told to scan its libraries after a download.

OpenPanel scans only at startup or when asked, so a volume bankai files would
stay unseen until then. An admin's login (``manga.openpanel_*``) gets a token,
kept in ``openpanel_token.json`` and asked for again when OpenPanel no longer
takes it. Nothing happens while ``manga.openpanel_url`` is empty.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)


def _token_path() -> Path:
    return bgjobs.jobs_root().parent / "openpanel_token.json"


def _kept_token() -> str:
    try:
        return str(json.loads(_token_path().read_text(encoding="utf-8")).get("token") or "")
    except (OSError, ValueError):
        return ""


def _keep_token(token: str) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"token": token}), encoding="utf-8")
    tmp.replace(path)


def configured() -> bool:
    return bool(get_settings().manga.openpanel_url.strip())


async def _login(client: httpx.AsyncClient, base: str) -> str:
    settings = get_settings().manga
    response = await client.post(
        f"{base}/api/auth/login", json={"username": settings.openpanel_username, "password": settings.openpanel_password}
    )
    response.raise_for_status()
    token = str((response.json() or {}).get("token") or "")
    if not token:
        raise RuntimeError("OpenPanel answered the login without a token")
    _keep_token(token)
    return token


async def scan() -> str:
    """Ask OpenPanel to scan; returns what it said ("started", "already_running"), or why not."""
    if not configured():
        return "not configured"
    base = get_settings().manga.openpanel_url.strip().rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token = _kept_token() or await _login(client, base)
            response = await client.post(f"{base}/api/admin/scan", headers={"Authorization": f"Bearer {token}"})
            if response.status_code in (401, 403):
                token = await _login(client, base)
                response = await client.post(f"{base}/api/admin/scan", headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            return str((response.json() or {}).get("status") or "started")
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        log.info("[manga] OpenPanel could not be asked to scan: %s", exc)
        return f"failed: {exc}"
