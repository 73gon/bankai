"""Scraper backends for streaming sites.

Each backend implements :class:`ScraperBackend` and is registered via
:func:`bankai.scraper.registry.register`. The CLI/dispatcher pick a backend
either from explicit ``--site`` / ``[scraper] backend`` config or by trying
each registered backend in turn until one returns results.
"""

from bankai.scraper.base import (
    EpisodeRef,
    ScraperBackend,
    ScraperError,
    SearchResult,
    StreamHandle,
)
from bankai.scraper.registry import all_backends, get_backend, register

__all__ = [
    "EpisodeRef",
    "ScraperBackend",
    "ScraperError",
    "SearchResult",
    "StreamHandle",
    "all_backends",
    "get_backend",
    "register",
]
