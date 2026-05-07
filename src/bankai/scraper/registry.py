"""Backend registry â€” auto-discovers concrete scrapers in this package."""

from __future__ import annotations

import importlib
import pkgutil
from threading import Lock
from typing import TypeVar

from bankai.logging import get_logger
from bankai.scraper.base import ScraperBackend

log = get_logger(__name__)

_REGISTRY: dict[str, type[ScraperBackend]] = {}
_LOCK = Lock()
_DISCOVERED = False

_B = TypeVar("_B", bound=type)


def register(cls: _B) -> _B:
    """Class decorator: register a backend by its ``site_id``."""
    site_id = getattr(cls, "site_id", None)
    if not site_id:
        raise TypeError(f"{cls.__name__} must define a non-empty `site_id`")
    with _LOCK:
        existing = _REGISTRY.get(site_id)
        if existing and existing is not cls:
            log.warning("backend %s already registered (%s); replacing", site_id, existing)
        _REGISTRY[site_id] = cls
    return cls


def _discover() -> None:
    """Import every module in ``bankai.scraper.backends`` so ``@register`` fires."""
    global _DISCOVERED
    with _LOCK:
        if _DISCOVERED:
            return
        _DISCOVERED = True
    pkg = importlib.import_module("bankai.scraper.backends")
    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{mod.name}")


def get_backend(site_id: str) -> type[ScraperBackend]:
    _discover()
    try:
        return _REGISTRY[site_id]
    except KeyError as exc:
        raise KeyError(f"unknown scraper backend: {site_id!r}") from exc


def all_backends() -> dict[str, type[ScraperBackend]]:
    _discover()
    return dict(_REGISTRY)
