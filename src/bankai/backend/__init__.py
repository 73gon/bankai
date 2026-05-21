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
from bankai.backend.transfer import (
    TransferError,
    TransferItem,
    TransferKind,
    TransferResult,
    format_transfer_summary,
    plan_transfer,
    transfer_with_rsync,
)

__all__ = [
    "BatchMovie",
    "SeriesLookupResult",
    "TransferError",
    "TransferItem",
    "TransferKind",
    "TransferResult",
    "build_movie_args",
    "format_transfer_summary",
    "list_series_episodes",
    "parse_movie_batch",
    "plan_transfer",
    "search_stream_sources",
    "title_aliases",
    "transfer_with_rsync",
]
