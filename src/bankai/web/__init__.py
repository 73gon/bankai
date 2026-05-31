"""bankai web UI / HTTP API package.

The web layer is an optional frontend that reuses the same services and
background-job store as the CLI. It is served by FastAPI (see
:mod:`bankai.web.app`) as a single process that hosts both the JSON API
under ``/api`` and the prebuilt React frontend as static assets.
"""

from __future__ import annotations

__all__ = ["create_app", "run_server"]


def create_app():  # type: ignore[no-untyped-def]
    """Lazily import and build the FastAPI app (keeps fastapi optional)."""
    from bankai.web.app import create_app as _create_app

    return _create_app()


def run_server(*, host: str | None = None, port: int | None = None) -> None:
    from bankai.web.server import run_server as _run_server

    _run_server(host=host, port=port)
