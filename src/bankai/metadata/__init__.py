"""Metadata providers for title aliases and future enrichment."""

from bankai.metadata.tvdb import TitleAlias, TVDBClient, TVDBError, get_title_aliases

__all__ = ["TVDBClient", "TVDBError", "TitleAlias", "get_title_aliases"]
