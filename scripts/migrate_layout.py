#!/usr/bin/env python3
"""Migrate existing bankai library files to the current v0.2 layout.

Dry-run is the default. Use ``--apply`` after reviewing the proposed moves.

Supported migrations:

* Movies/Title (Year)/Title (Year) [ger].mkv
  -> Movies/Title (Year)/Title (Year).mkv
* Series/Show/Season 01/Show - S01E03 - Episode Title [ger].mkv
  -> Shows/Show/Season 01/Show - S01E03.mkv
* Shows/Show/Season 01/Show - S01E03 - Episode Title [ger].mkv
  -> Shows/Show/Season 01/Show - S01E03.mkv
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from bankai.config import get_settings, reset_settings_cache  # noqa: E402
from bankai.processor.naming import render_episode_path, render_movie_path  # noqa: E402

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
EPISODE_RE = re.compile(r"\b[Ss](?P<season>\d{1,2})[Ee](?P<episode>\d{1,3})\b")
AUDIO_TAG_RE = re.compile(r"\s+\[[^\]]+\]$")


@dataclass(frozen=True, slots=True)
class Move:
    source: Path
    target: Path
    reason: str


@dataclass(frozen=True, slots=True)
class Problem:
    source: Path
    reason: str


def _normalise(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _same_path(left: Path, right: Path) -> bool:
    return _normalise(left) == _normalise(right)


def _strip_audio_tag(stem: str) -> str:
    return AUDIO_TAG_RE.sub("", stem).strip()


def _movie_query(path: Path, movies_root: Path) -> str:
    rel = path.relative_to(movies_root)
    # Prefer the containing folder because the old pipeline already used
    # Title (Year) for movie folders when the query included a year.
    if len(rel.parts) > 1:
        return rel.parts[0]
    return _strip_audio_tag(path.stem)


def plan_movies(library: Path, settings: object) -> tuple[list[Move], list[Problem]]:
    moves: list[Move] = []
    problems: list[Problem] = []
    movies_root = library / "Movies"
    if not movies_root.exists():
        return moves, problems

    for source in sorted(movies_root.rglob("*.mkv")):
        query = _movie_query(source, movies_root)
        if not YEAR_RE.search(query):
            problems.append(Problem(source=source, reason="movie year could not be inferred"))
            continue
        target = render_movie_path(
            library=library,
            query=query,
            audio_lang=settings.audio.language_tag,
            folder_template=settings.output.movie_folder_template,
            file_template=settings.output.filename_template,
        )
        if _same_path(source, target):
            continue
        if target.exists():
            problems.append(Problem(source=source, reason=f"target already exists: {target}"))
            continue
        moves.append(Move(source=source, target=target, reason="movie filename/template update"))
    return moves, problems


def _series_candidates(library: Path) -> Iterable[Path]:
    for root_name in ("Series", "Shows"):
        root = library / root_name
        if root.exists():
            yield from sorted(root.rglob("*.mkv"))


def _show_from_path(source: Path, library: Path) -> str | None:
    try:
        rel = source.relative_to(library)
    except ValueError:
        return None
    if len(rel.parts) < 3:
        return None
    if rel.parts[0] not in {"Series", "Shows"}:
        return None
    return rel.parts[1].strip() or None


def plan_shows(library: Path, settings: object) -> tuple[list[Move], list[Problem]]:
    moves: list[Move] = []
    problems: list[Problem] = []
    for source in _series_candidates(library):
        show = _show_from_path(source, library)
        if not show:
            problems.append(Problem(source=source, reason="show folder could not be inferred"))
            continue
        match = EPISODE_RE.search(source.stem)
        if not match:
            problems.append(Problem(source=source, reason="SxxEyy marker could not be inferred"))
            continue
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        target = render_episode_path(
            library=library,
            query=f"{show} S{season:02d}E{episode:02d}",
            series_title=show,
            season=season,
            episode=episode,
            episode_title=None,
            audio_lang=settings.audio.language_tag,
            season_folder_template=settings.output.season_folder_template,
            file_template=settings.output.series_filename_template,
        )
        if _same_path(source, target):
            continue
        if target.exists():
            problems.append(Problem(source=source, reason=f"target already exists: {target}"))
            continue
        moves.append(Move(source=source, target=target, reason="show layout/filename update"))
    return moves, problems


def _prune_empty_dirs(library: Path) -> int:
    removed = 0
    roots = [library / "Series", library / "Shows", library / "Movies"]
    for root in roots:
        if not root.exists():
            continue
        for directory in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                continue
            removed += 1
        try:
            if root.name == "Series":
                root.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def _format_move(move: Move) -> str:
    return f"{move.source}\n  -> {move.target}\n  ({move.reason})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="config.toml to load instead of the default")
    parser.add_argument("--library", type=Path, help="library root; defaults to output.directory")
    parser.add_argument("--dry-run", action="store_true", help="preview only (default)")
    parser.add_argument("--apply", action="store_true", help="perform moves instead of printing a dry-run")
    parser.add_argument("--no-movies", action="store_true", help="skip Movies migration")
    parser.add_argument("--no-shows", action="store_true", help="skip Series/Shows migration")
    parser.add_argument("--keep-empty-dirs", action="store_true", help="do not prune empty old directories after apply")
    args = parser.parse_args(argv)

    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run cannot be used together")

    if args.config:
        os.environ["BANKAI_CONFIG"] = str(args.config.expanduser())
    reset_settings_cache()
    settings = get_settings()
    library = _normalise(args.library or settings.output.directory)

    all_moves: list[Move] = []
    all_problems: list[Problem] = []
    if not args.no_movies:
        moves, problems = plan_movies(library, settings)
        all_moves.extend(moves)
        all_problems.extend(problems)
    if not args.no_shows:
        moves, problems = plan_shows(library, settings)
        all_moves.extend(moves)
        all_problems.extend(problems)

    print(f"library: {library}")
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"planned moves: {len(all_moves)}")
    print(f"skipped/problems: {len(all_problems)}")
    print()

    if all_moves:
        print("Moves:")
        for move in all_moves:
            print(_format_move(move))
        print()

    if all_problems:
        print("Skipped:")
        for problem in all_problems:
            print(f"{problem.source}\n  !! {problem.reason}")
        print()

    if not args.apply:
        print("Dry-run only. Re-run with --apply to move files.")
        return 0

    applied = 0
    for move in all_moves:
        move.target.parent.mkdir(parents=True, exist_ok=True)
        move.source.rename(move.target)
        applied += 1
    pruned = 0 if args.keep_empty_dirs else _prune_empty_dirs(library)
    print(f"Applied moves: {applied}")
    print(f"Removed empty directories: {pruned}")
    return 0 if not all_problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
