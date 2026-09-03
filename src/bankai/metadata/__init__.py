"""Metadata providers for title aliases and future enrichment."""

from bankai.metadata.tmdb import TMDBClient, TMDBEpisode, TMDBError, TMDBTitle
from bankai.metadata.tvdb import TitleAlias, TVDBClient, TVDBError, get_title_aliases

__all__ = [
    "TMDBClient",
    "TMDBEpisode",
    "TMDBError",
    "TMDBTitle",
    "TVDBClient",
    "TVDBError",
    "TitleAlias",
    "get_title_aliases",
]
