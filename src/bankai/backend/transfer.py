"""Safe media transfers to the mounted server library."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from bankai.config import get_settings

TransferKind = Literal["movie", "show", "auto"]
ProgressCallback = Callable[[str], None]

_VIDEO_EXTENSIONS = {
    ".avi",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
    ".wmv",
}
_SHOW_RE = re.compile(r"\b[Ss]\d{1,2}[ ._-]?[Ee]\d{1,3}\b")


class TransferError(Exception):
    """A transfer could not be planned or executed."""


@dataclass(frozen=True, slots=True)
class TransferItem:
    source: Path
    destination: Path
    kind: Literal["movie", "show"]


@dataclass(slots=True)
class TransferResult:
    transferred: list[TransferItem] = field(default_factory=list)
    skipped: list[TransferItem] = field(default_factory=list)
    failed: list[tuple[TransferItem, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def plan_transfer(paths: list[Path], *, kind: TransferKind = "auto") -> list[TransferItem]:
    settings = get_settings()
    items: list[TransferItem] = []
    for raw_path in paths:
        path = raw_path.expanduser()
        if not path.exists():
            raise TransferError(f"not found: {path}")
        for source, root in _iter_transfer_files(path):
            resolved_kind = _resolve_kind(source, kind)
            destination = _destination_for(source, root=root, kind=resolved_kind)
            if source.resolve() == destination.resolve():
                raise TransferError(f"source and destination are the same: {source}")
            items.append(TransferItem(source=source, destination=destination, kind=resolved_kind))
    return _dedupe_items(items, library_root=settings.output.directory)


def transfer_with_rsync(
    paths: list[Path],
    *,
    kind: TransferKind = "auto",
    progress: ProgressCallback = print,
) -> TransferResult:
    settings = get_settings()
    rsync = shutil.which(settings.transfer.rsync_binary)
    if rsync is None:
        raise TransferError(f"rsync not found: {settings.transfer.rsync_binary}")
    result = TransferResult()
    items = plan_transfer(paths, kind=kind)
    if not items:
        progress("No files to transfer.")
        return result
    progress(f"Planning complete: {len(items)} file(s)")
    completed = 0
    progress("BANKAI_PROGRESS stage=transfer pct=0.0 status=starting")
    for item in items:
        if item.destination.exists():
            progress(f"SKIP exists: {item.destination}")
            result.skipped.append(item)
            completed += 1
            _emit_transfer_progress(progress, completed=completed, total=len(items))
            continue
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        progress(f"MOVE {item.source} -> {item.destination}")
        cmd = [
            rsync,
            "-a",
            "--human-readable",
            "--info=progress2",
            "--ignore-existing",
            "--remove-source-files",
            str(item.source),
            str(item.destination.parent) + os.sep,
        ]
        try:
            _run_rsync(cmd, progress=progress)
        except TransferError as exc:
            result.failed.append((item, str(exc)))
            progress(f"FAILED {item.source}: {exc}")
            completed += 1
            _emit_transfer_progress(progress, completed=completed, total=len(items))
            continue
        if item.destination.exists():
            result.transferred.append(item)
            progress(f"DONE {item.destination}")
        elif item.source.exists():
            result.failed.append((item, "rsync finished but destination file was not created"))
            progress(f"FAILED {item.source}: destination was not created")
        else:
            result.transferred.append(item)
            progress(f"DONE {item.destination}")
        completed += 1
        _emit_transfer_progress(progress, completed=completed, total=len(items))
    return result


def format_transfer_summary(result: TransferResult) -> str:
    lines = [
        f"Transferred: {len(result.transferred)}",
        f"Skipped existing: {len(result.skipped)}",
        f"Failed: {len(result.failed)}",
    ]
    if result.transferred:
        lines.append("")
        lines.append("Moved:")
        lines.extend(f"- {item.destination.name}" for item in result.transferred[:20])
    if result.skipped:
        lines.append("")
        lines.append("Skipped because destination exists:")
        lines.extend(f"- {item.destination.name}" for item in result.skipped[:20])
    if result.failed:
        lines.append("")
        lines.append("Failed:")
        lines.extend(f"- {item.source.name}: {error}" for item, error in result.failed[:10])
    return "\n".join(lines)


def _iter_transfer_files(path: Path) -> list[tuple[Path, Path]]:
    if path.is_file():
        return [(path, path.parent)]
    files = [
        child
        for child in path.rglob("*")
        if child.is_file() and child.suffix.casefold() in _VIDEO_EXTENSIONS
    ]
    return [(child, path) for child in sorted(files)]


def _resolve_kind(source: Path, kind: TransferKind) -> Literal["movie", "show"]:
    if kind == "movie":
        return "movie"
    if kind == "show":
        return "show"
    settings = get_settings()
    for folder in ("Series", "Shows", "shows"):
        try:
            source.relative_to(Path(settings.output.directory) / folder)
            return "show"
        except ValueError:
            pass
    if _SHOW_RE.search(source.name):
        return "show"
    return "movie"


def _destination_for(source: Path, *, root: Path, kind: Literal["movie", "show"]) -> Path:
    settings = get_settings()
    output_root = Path(settings.output.directory)
    movies_root = output_root / "Movies"
    series_roots = [output_root / "Series", output_root / "Shows", output_root / "shows"]
    try:
        return Path(settings.transfer.movies_dir) / source.relative_to(movies_root)
    except ValueError:
        pass
    for series_root in series_roots:
        try:
            return Path(settings.transfer.shows_dir) / source.relative_to(series_root)
        except ValueError:
            pass
    base = Path(settings.transfer.shows_dir if kind == "show" else settings.transfer.movies_dir)
    try:
        return base / source.relative_to(root)
    except ValueError:
        return base / source.name


def _dedupe_items(items: list[TransferItem], *, library_root: Path) -> list[TransferItem]:
    seen: set[Path] = set()
    out: list[TransferItem] = []
    for item in sorted(items, key=lambda i: _sort_key(i.source, library_root=Path(library_root))):
        key = item.source.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _sort_key(path: Path, *, library_root: Path) -> tuple[int, str]:
    try:
        rel = path.relative_to(library_root)
    except ValueError:
        rel = path
    return (len(rel.parts), str(rel).casefold())


def _run_rsync(cmd: list[str], *, progress: ProgressCallback) -> None:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        clean = line.strip()
        if clean:
            progress(clean)
    code = proc.wait()
    if code != 0:
        raise TransferError(f"rsync exited with {code}")


def _emit_transfer_progress(
    progress: ProgressCallback,
    *,
    completed: int,
    total: int,
) -> None:
    pct = completed / total * 100.0 if total else 100.0
    progress(f"BANKAI_PROGRESS stage=transfer pct={pct:.1f} status=running")
