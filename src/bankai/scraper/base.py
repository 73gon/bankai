"""Public types and protocol for site scrapers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from bankai.queue.models import MediaKind


class ScraperError(Exception):
    """Backend-level error (network, parsing, blocked, etc)."""


class CloudflareBlocked(ScraperError):
    """Backend hit a Cloudflare interstitial â€” caller should fall back."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single hit returned from a site search."""

    site: str
    title: str
    url: str
    kind: MediaKind
    year: int | None = None
    quality: str | None = None
    poster_url: str | None = None
    release_name: str | None = None
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EpisodeRef:
    """A single episode of a series, expanded from a series page."""

    site: str
    series_title: str
    season: int
    episode: int
    title: str
    url: str
    language: str | None = None  # e.g. "ger", "ger-sub", "en"


@dataclass(frozen=True, slots=True)
class StreamHandle:
    """A resolvable stream reference handed to the extraction layer.

    ``hint`` tells the extractor what to do:

    * ``"ytdlp"`` â€” pass URL straight to yt-dlp.
    * ``"playwright"`` â€” launch the headless browser fallback.
    * ``"direct"`` â€” URL is already a direct media URL (.mp4 / .m3u8).
    """

    site: str
    url: str
    hint: Literal["ytdlp", "playwright", "direct"] = "ytdlp"
    headers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ScraperBackend(Protocol):
    """Interface every site scraper implements.

    Backends are stateless w.r.t. user data; they may hold an
    :class:`httpx.AsyncClient` for connection pooling.
    """

    site_id: str
    display_name: str
    supports_movies: bool
    supports_series: bool

    async def search(
        self, query: str, *, kind: MediaKind | None = None, limit: int = 20
    ) -> list[SearchResult]:
        """Return search hits for ``query`` (movies and/or episodes)."""

    async def list_episodes(self, series_url: str) -> list[EpisodeRef]:
        """Expand a series page into a list of episode references."""

    async def resolve_stream(self, url: str) -> StreamHandle:
        """Convert a movie/episode page URL into a :class:`StreamHandle`."""

    async def aclose(self) -> None:
        """Release any pooled resources (HTTP client, browser, â€¦)."""
