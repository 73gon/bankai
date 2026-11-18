"""Blu-ray disc images (``BDMV``) in a torrent: their episodes or film as MKV files.

A "COMPLETE BLURAY" release (AnimeZ's iFPD Oshi no Ko: 2 discs, 201 files, 83 GB) is
the discs as they are: ``<disc>/BDMV/STREAM/00005.m2ts`` with no episode in any
name, beside menus, NCOP/NCED and trailers, and ``BDMV/PLAYLIST/*.mpls`` saying
what plays. bankai's filing reads episodes from names and knew no ``.m2ts``: such
a torrent failed with "no video".

Here each disc's playlists are read with ``mkvmerge -J`` (their length and the
clips they play); an episode is a playlist of at least ``EPISODE_MIN`` seconds,
the same clips counted once, a "play all" playlist (whose clips are other
episode playlists') left out; episodes go in disc order, then by their first
clip, numbered on across discs. Each is remuxed by mkvmerge from its playlist --
lossless, every audio and subtitle track and the chapters kept -- into a work
folder beside the download, named ``Episode NN.mkv`` so filing numbers them as
it numbers any batch. A film is its longest playlist. The disc files stay for
seeding (a private tracker's rule).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from bankai.logging import get_logger

log = get_logger(__name__)

# Shorter is an opening, an ending, a trailer or a menu loop.
EPISODE_MIN = 15 * 60
# Longer than any episode or film: a disc's everything-in-one playlist.
PLAYLIST_MAX = 4 * 3600


def discs(root: Path) -> list[Path]:
    """The disc folders under a download (each holding ``BDMV/PLAYLIST``), in disc order."""
    if not root.is_dir():
        return []
    found = {path.parent.parent for path in root.rglob("PLAYLIST") if path.is_dir() and path.parent.name.upper() == "BDMV"}
    # BACKUP holds a copy of the same playlists.
    found = {disc for disc in found if disc.name.upper() != "BACKUP"}

    def order(path: Path) -> list[Any]:
        return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(path.relative_to(root)))]

    return sorted(found, key=order)


def _identify(playlist: Path) -> dict[str, Any] | None:
    try:
        done = subprocess.run(
            ["mkvmerge", "-J", str(playlist)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        data = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        log.warning("[bdmv] could not read %s: %s", playlist, exc)
        return None
    props = (data.get("container") or {}).get("properties") or {}
    if not props.get("playlist"):
        return None
    return {
        "path": playlist,
        "seconds": int(props.get("playlist_duration") or 0) / 1e9,
        "clips": [Path(str(name)).name for name in props.get("playlist_file") or []],
        "size": int(props.get("playlist_size") or 0),
    }


def choose(playlists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A disc's episode playlists, in play order (see the module's note)."""
    long = [row for row in playlists if row and EPISODE_MIN <= row["seconds"] <= PLAYLIST_MAX and row["clips"]]
    # The same clips for the same length once (a second playlist for another audio default). Some
    # discs keep every episode in one stream, each playlist a stretch of it: the length tells them apart.
    unique: dict[tuple[frozenset[str], int], dict[str, Any]] = {}
    for row in sorted(long, key=lambda row: row["path"].name):
        unique.setdefault((frozenset(row["clips"]), round(row["seconds"] / 10)), row)
    rows = list(unique.values())

    def plays_all(row: dict[str, Any]) -> bool:
        """Two or more other playlists within its clips whose lengths add up to its own."""
        inside = [other for other in rows if other is not row and set(other["clips"]) <= set(row["clips"])
                  and other["seconds"] < row["seconds"]]
        return len(inside) >= 2 and abs(sum(other["seconds"] for other in inside) - row["seconds"]) <= 0.05 * row["seconds"]

    rows = [row for row in rows if not plays_all(row)]
    return sorted(rows, key=lambda row: (row["clips"][0], row["path"].name))


def episodes(root: Path) -> list[dict[str, Any]]:
    """Every disc's episode playlists, numbered on across discs."""
    found: list[dict[str, Any]] = []
    for disc in discs(root):
        rows = [_identify(path) for path in sorted((disc / "BDMV" / "PLAYLIST").glob("*.mpls"))]
        for row in choose([row for row in rows if row]):
            found.append({**row, "disc": disc.name})
    for number, row in enumerate(found, start=1):
        row["episode"] = number
    return found


def film(root: Path) -> dict[str, Any] | None:
    """A film disc's main playlist: the longest."""
    rows = [_identify(path) for disc in discs(root) for path in sorted((disc / "BDMV" / "PLAYLIST").glob("*.mpls"))]
    rows = [row for row in rows if row and row["seconds"] <= PLAYLIST_MAX]
    return max(rows, key=lambda row: (row["seconds"], row["size"]), default=None)


def _remux(playlist: Path, target: Path) -> Path:
    part = target.with_name(f".{target.name}.part")
    done = subprocess.run(
        ["mkvmerge", "-q", "-o", str(part), str(playlist)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # 1 is warnings only (a gap in a stream's timestamps, common on discs).
    if done.returncode not in (0, 1) or not part.exists():
        part.unlink(missing_ok=True)
        raise RuntimeError(f"mkvmerge could not remux {playlist.name}: {(done.stdout or done.stderr).strip()[-300:]}")
    part.replace(target)
    return target


def work_folder(root: Path, info_hash: str) -> Path:
    """Beside the download, on its volume: filing hard-links from here."""
    return root.parent / f".bankai-bdmv-{info_hash[:12]}"


def remux_episodes(root: Path, work: Path, *, only: set[int] | None = None) -> list[Path]:
    """The discs' episodes as ``Episode NN.mkv`` in ``work``; ``only``: those episode numbers alone."""
    rows = episodes(root)
    if not rows:
        return []
    work.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for row in rows:
        if only is not None and row["episode"] not in only:
            continue
        target = work / f"Episode {row['episode']:02d}.mkv"
        if not target.exists():
            log.info(
                "[bdmv] episode %d: %s %s (%d min, %s)", row["episode"], row["disc"], row["path"].name,
                round(row["seconds"] / 60), ", ".join(row["clips"]),
            )
            log.info('BANKAI_PROGRESS stage=organize pct=%.0f status="Remuxing episode %d of %d from the discs"',
                     100 * (row["episode"] - 1) / len(rows), row["episode"], len(rows))
            _remux(row["path"], target)
        out.append(target)
    return out


def remux_film(root: Path, work: Path, name: str) -> Path | None:
    row = film(root)
    if row is None:
        return None
    work.mkdir(parents=True, exist_ok=True)
    target = work / f"{name}.mkv"
    log.info("[bdmv] the film: %s (%d min)", row["path"].name, round(row["seconds"] / 60))
    return target if target.exists() else _remux(row["path"], target)


def remove_work(work: Path) -> None:
    """The remuxes go once filed (the library has its own name for them)."""
    shutil.rmtree(work, ignore_errors=True)
