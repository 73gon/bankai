"""Backend service layer for CLI and future web/API frontends."""

from __future__ import annotations

from bankai.backend.services import (
    BatchMovie,
    SeriesLookupResult,
    build_movie_args,
    list_series_episodes,
    parse_movie_batch,
    search_stream_sources,
    title_aliases,
)

__all__ = [
    "BatchMovie",
    "SeriesLookupResult",
    "build_movie_args",
    "list_series_episodes",
    "parse_movie_batch",
    "search_stream_sources",
    "title_aliases",
]
