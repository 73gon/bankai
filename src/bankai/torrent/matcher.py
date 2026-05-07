"""Episode-to-file matcher.

Given a downloaded torrent (a single video file or a season pack) and a
list of expected :class:`EpisodeRef`, return a mapping from episode â†’
local file path. The matcher is conservative:

* For movies: pick the largest video file in the directory.
* For series: regex on ``S\\d{2}E\\d{2}`` (also tolerates ``1x01``,
  ``S1E1``); if no parse, fall back to ordering by name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bankai.scraper.base import EpisodeRef

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm"}

_SXX_EXX = re.compile(r"[Ss](?P<season>\d{1,2})[._\s-]?[Ee](?P<episode>\d{1,3})")
_NUM_X_NUM = re.compile(r"\b(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b")


@dataclass(frozen=True, slots=True)
class EpisodeFile:
    episode: EpisodeRef
    path: Path


def find_video_files(root: Path) -> list[Path]:
    """All video files under ``root``, sorted by lowercased name."""
    if root.is_file():
        return [root] if root.suffix.lower() in _VIDEO_EXTS else []
    return sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_EXTS),
        key=lambda p: p.name.lower(),
    )


def pick_movie_file(root: Path) -> Path | None:
    """Largest video file under ``root`` â€” usually the feature in a movie release."""
    files = find_video_files(root)
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_size)


def parse_se(name: str) -> tuple[int, int] | None:
    """Return ``(season, episode)`` parsed from ``name`` or ``None``."""
    m = _SXX_EXX.search(name) or _NUM_X_NUM.search(name)
    if not m:
        return None
    return int(m.group("season")), int(m.group("episode"))


def match_episodes(root: Path, episodes: Iterable[EpisodeRef]) -> list[EpisodeFile]:
    """Pair each episode with the best matching local video file.

    Strategy:
        1. Build a ``{(s, e): path}`` map from files whose name parses cleanly.
        2. For each episode, look up by ``(season, episode)``.
        3. Episodes with no match are simply omitted from the result.
    """
    files = find_video_files(root)
    indexed: dict[tuple[int, int], Path] = {}
    for f in files:
        parsed = parse_se(f.name)
        if parsed and parsed not in indexed:
            indexed[parsed] = f

    matched: list[EpisodeFile] = []
    for ep in episodes:
        key = (ep.season, ep.episode)
        if key in indexed:
            matched.append(EpisodeFile(episode=ep, path=indexed[key]))
    return matched
