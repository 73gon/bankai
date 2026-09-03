"""Filename / folder layout helpers.

The pipeline writes finished MKVs into a Plex / Jellyfin-friendly tree
under ``OutputSettings.directory``. The exact layout is driven by the
templates in :class:`bankai.config.OutputSettings`:

* Movies::

    <directory>/Movies/<movie_folder_template>/<filename_template>

* Episodes::

    <directory>/Shows/<series_title>/<season_folder_template>/<series_filename_template>

Templates are plain ``str.format`` strings. Supported placeholders:

* ``{title}``         \u2014 the (cleaned) work title (English or query).
* ``{year}``          \u2014 release year, or ``"unknown"`` when absent.
* ``{audio_lang}``    \u2014 language tag for the muxed audio (``ger``).
* ``{season}`` / ``{episode}`` \u2014 integers (use ``:02d`` for padding).
* ``{episode_title}`` \u2014 episode display title or ``""``.
* ``{series_title}``  \u2014 normalised show name (episodes only).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Characters that are unsafe on Windows / problematic for media scanners.
_BAD_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTISPACE_RE = re.compile(r"\s{2,}")
_TRAILING_DOTS_SPACE_RE = re.compile(r"[ .]+$")


def sanitise(component: str, *, fallback: str = "untitled") -> str:
    """Make a single path component safe on Windows + media scanners.

    Windows-illegal characters (``: / \\ < > " | ? *``) are **dropped**
    rather than replaced, so a TVDB title like ``Paul Blart: Mall Cop``
    becomes ``Paul Blart Mall Cop`` (matching how Plex/Jellyfin expect
    the folder) instead of ``Paul Blart_ Mall Cop``.
    """
    cleaned = _BAD_CHARS_RE.sub("", component)
    cleaned = _MULTISPACE_RE.sub(" ", cleaned).strip()
    cleaned = _TRAILING_DOTS_SPACE_RE.sub("", cleaned).strip()
    return cleaned or fallback


def _year_from_query(query: str) -> tuple[str, str | None]:
    """Split ``"Cars 2 2011"`` into ``("Cars 2", "2011")``."""
    m = re.search(r"\s*\(?(\b(?:19|20)\d{2}\b)\)?\s*$", query)
    if not m:
        return query.strip(), None
    title = query[: m.start()].rstrip(" ._-()")
    return title.strip() or query.strip(), m.group(1)


def _strip_episode_marker(name: str) -> str:
    """Remove a trailing ``SxxEyy``-style marker from a query."""
    cleaned = re.sub(r"\s*[Ss]\d{1,2}[EeXx]\d{1,3}.*$", "", name)
    return cleaned.strip(" -_") or name


def _render(template: str, fields: dict[str, Any]) -> str:
    """``str.format`` that swallows missing keys / bad format specs.

    A misconfigured template should never crash the pipeline; it should
    just degrade gracefully to a safe default.
    """
    try:
        return template.format(**fields)
    except (KeyError, ValueError, IndexError):
        # Fall back to a minimal, always-valid layout.
        if "season" in fields and "episode" in fields:
            return "{title} - S{season:02d}E{episode:02d}.mkv".format(**fields)
        return "{title}.mkv".format(**fields)


def render_movie_path(
    *,
    library: Path,
    query: str,
    title_override: str | None = None,
    year_override: str | None = None,
    audio_lang: str = "ger",
    folder_template: str,
    file_template: str,
) -> Path:
    """Compute the final ``Movies/.../*.mkv`` path for a movie."""
    parsed_title, parsed_year = _year_from_query(query)
    title = title_override or parsed_title
    year = year_override or parsed_year or "unknown"
    fields = {
        "title": title,
        "year": year,
        "audio_lang": audio_lang,
    }
    folder = sanitise(_render(folder_template, fields))
    filename = sanitise(_render(file_template, fields), fallback="movie.mkv")
    if not filename.lower().endswith(".mkv"):
        filename += ".mkv"
    return library / "Movies" / folder / filename


def render_episode_path(
    *,
    library: Path,
    query: str,
    series_title: str | None,
    season: int,
    episode: int,
    episode_title: str | None = None,
    year_override: str | None = None,
    audio_lang: str = "ger",
    season_folder_template: str,
    file_template: str,
    series_folder_override: str | None = None,
) -> Path:
    """Compute the final ``Shows/.../*.mkv`` path for one episode."""
    parsed_show, parsed_year = _year_from_query(_strip_episode_marker(query))
    title = series_title or parsed_show
    year = year_override or parsed_year or ""
    fields: dict[str, Any] = {
        "title": title,
        "series_title": title,
        "year": year or "unknown",
        "audio_lang": audio_lang,
        "season": season,
        "episode": episode,
        "episode_title": (episode_title or "").strip(),
    }
    show_folder = sanitise(series_folder_override or title)
    season_folder = sanitise(_render(season_folder_template, fields))
    filename = sanitise(_render(file_template, fields), fallback="episode.mkv")
    if not filename.lower().endswith(".mkv"):
        filename += ".mkv"
    return library / "Shows" / show_folder / season_folder / filename


__all__ = ["render_episode_path", "render_movie_path", "sanitise"]
