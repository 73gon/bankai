from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from bankai.backend.transfer import (
    TransferItem,
    TransferResult,
    _native_move,
    format_transfer_summary,
    plan_transfer,
)
from bankai.config import get_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def _transfer_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(tmp_path / "library"))
    monkeypatch.setenv("BANKAI_TRANSFER__MOVIES_DIR", str(tmp_path / "media12" / "movies"))
    monkeypatch.setenv("BANKAI_TRANSFER__SHOWS_DIR", str(tmp_path / "media12" / "shows"))
    monkeypatch.setenv(
        "BANKAI_TRANSFER__ANIME_SHOWS_DIR",
        str(tmp_path / "media12" / "shows_anime"),
    )
    reset_settings_cache()


def test_plan_transfer_preserves_movie_layout(tmp_path: Path) -> None:
    movie = tmp_path / "library" / "Movies" / "Arcane (2021)" / "Arcane (2021).mkv"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"fake")

    (item,) = plan_transfer([movie])

    assert item.kind == "movie"
    assert (
        item.destination == tmp_path / "media12" / "movies" / "Arcane (2021)" / "Arcane (2021).mkv"
    )


def test_plan_transfer_detects_show_files(tmp_path: Path) -> None:
    show = tmp_path / "library" / "Series" / "Arcane" / "Season 01" / "Arcane - S01E01.mkv"
    show.parent.mkdir(parents=True)
    show.write_bytes(b"fake")

    (item,) = plan_transfer([show])

    assert item.kind == "show"
    assert (
        item.destination
        == tmp_path / "media12" / "shows" / "Arcane" / "Season 01" / "Arcane - S01E01.mkv"
    )


def test_plan_transfer_routes_anime_to_dedicated_library_with_tvdb_id(
    tmp_path: Path,
) -> None:
    show = (
        tmp_path
        / "library"
        / "Shows"
        / "Frieren Beyond Journey's End (2023) [tvdbid-424536]"
        / "Season 02"
        / "Frieren Beyond Journey's End - S02E01.mkv"
    )
    show.parent.mkdir(parents=True)
    show.write_bytes(b"anime")

    (item,) = plan_transfer([show], kind="anime")

    assert item.kind == "anime"
    assert item.destination == (
        tmp_path
        / "media12"
        / "shows_anime"
        / "Frieren Beyond Journey's End (2023) [tvdbid-424536]"
        / "Season 02"
        / "Frieren Beyond Journey's End - S02E01.mkv"
    )


def test_plan_transfer_reuses_legacy_anime_folder_without_provider_suffix(
    tmp_path: Path,
) -> None:
    show = (
        tmp_path
        / "library"
        / "Shows"
        / "Attack on Titan (2013) [tvdbid-267440]"
        / "Season 04"
        / "Attack on Titan - S04E01.mkv"
    )
    show.parent.mkdir(parents=True)
    show.write_bytes(b"new")
    legacy = tmp_path / "media12" / "shows_anime" / "Attack on Titan"
    (legacy / "Season 01").mkdir(parents=True)
    (legacy / "Season 01" / "Attack on Titan - S01E01.mkv").write_bytes(b"old")

    (item,) = plan_transfer([show], kind="anime")

    assert item.destination == legacy / "Season 04" / "Attack on Titan - S04E01.mkv"


def test_plan_transfer_reuses_existing_show_folder(tmp_path: Path) -> None:
    show = tmp_path / "library" / "Series" / "Bleach" / "Season 02" / "Bleach - S02E01.mkv"
    show.parent.mkdir(parents=True)
    show.write_bytes(b"new")
    default_root = tmp_path / "media12" / "shows"
    alternate_root = tmp_path / "drive-e" / "media" / "shows"
    existing = alternate_root / "Bleach"
    (existing / "Season 01").mkdir(parents=True)
    (existing / "Season 01" / "Bleach - S01E01.mkv").write_bytes(b"old")
    settings = get_settings()
    settings.web.server_show_dirs = [default_root, alternate_root]

    (item,) = plan_transfer([show])

    assert item.destination == existing / "Season 02" / "Bleach - S02E01.mkv"


def test_plan_transfer_prefers_populated_show_folder(tmp_path: Path) -> None:
    show = tmp_path / "library" / "Series" / "Bleach" / "Season 03" / "Bleach - S03E01.mkv"
    show.parent.mkdir(parents=True)
    show.write_bytes(b"new")
    default_root = tmp_path / "media12" / "shows"
    duplicate = default_root / "Bleach" / "Season 01"
    duplicate.mkdir(parents=True)
    (duplicate / "Bleach - S01E01.mkv").write_bytes(b"one")
    established_root = tmp_path / "drive-e" / "media" / "shows"
    established = established_root / "Bleach" / "Season 02"
    established.mkdir(parents=True)
    (established / "Bleach - S02E01.mkv").write_bytes(b"one")
    (established / "Bleach - S02E02.mkv").write_bytes(b"two")
    settings = get_settings()
    settings.web.server_show_dirs = [default_root, established_root]

    (item,) = plan_transfer([show])

    assert item.destination == established_root / "Bleach" / "Season 03" / "Bleach - S03E01.mkv"


def test_transfer_summary_mentions_skipped_existing(tmp_path: Path) -> None:
    source = tmp_path / "movie.mkv"
    destination = tmp_path / "media12" / "movies" / "movie.mkv"
    result = TransferResult()
    result.skipped.append(TransferItem(source=source, destination=destination, kind="movie"))

    summary = format_transfer_summary(result)

    assert "Skipped existing: 1" in summary
    assert "movie.mkv" in summary


def test_native_move_waits_and_retries_transient_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "library" / "episode.mkv"
    destination = tmp_path / "media" / "episode.mkv"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"complete episode")
    original_copy2 = __import__("shutil").copy2
    calls = 0

    def flaky_copy2(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(13, "file is in use", str(src))
        original_copy2(src, dst)

    progress: list[str] = []
    monkeypatch.setattr("bankai.backend.transfer.shutil.copy2", flaky_copy2)
    monkeypatch.setattr("bankai.backend.transfer.time.sleep", lambda _seconds: None)
    # Both paths are under one tmp_path, so this is the copy across volumes.
    monkeypatch.setattr("bankai.backend.transfer._same_volume", lambda *_: False)

    _native_move(source, destination, progress=progress.append)

    assert calls == 2
    assert destination.read_bytes() == b"complete episode"
    assert not source.exists()
    assert any("waiting_for_file_lock" in line for line in progress)


def test_native_move_reports_byte_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mkv"
    destination = tmp_path / "media" / "episode.mkv"
    destination.parent.mkdir()
    source.write_bytes(b"x" * (2 * 1024 * 1024))

    def slow_copy(src: Path, dst: Path) -> None:
        with src.open("rb") as reader, dst.open("wb") as writer:
            writer.write(reader.read(1024 * 1024))
            writer.flush()
            time.sleep(0.04)
            shutil.copyfileobj(reader, writer)
        shutil.copystat(src, dst)

    progress: list[str] = []
    monkeypatch.setattr("bankai.backend.transfer._COPY_PROGRESS_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("bankai.backend.transfer.shutil.copy2", slow_copy)

    monkeypatch.setattr("bankai.backend.transfer._same_volume", lambda *_: False)
    _native_move(source, destination, progress=progress.append)

    percentages = [
        float(line.split("pct=", 1)[1].split()[0])
        for line in progress
        if "BANKAI_PROGRESS stage=transfer" in line
    ]
    assert any(0 < percent < 100 for percent in percentages)
    assert percentages[-1] == 100
def test_a_publish_on_one_volume_renames_instead_of_copying(tmp_path, monkeypatch):
    """Publishing wrote every episode twice: into staging, then the library.

    On one volume a rename does the same job instantly, needs no second copy,
    and lands atomically, so a scanner never sees a half-written file.
    """
    from bankai.backend.transfer import _native_move

    source = tmp_path / "staging" / "episode.mkv"
    destination = tmp_path / "media" / "episode.mkv"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"complete episode")

    copies = 0

    def counted(src, dst):
        nonlocal copies
        copies += 1

    monkeypatch.setattr("bankai.backend.transfer.shutil.copy2", counted)
    progress: list[str] = []
    _native_move(source, destination, progress=progress.append)

    assert copies == 0
    assert destination.read_bytes() == b"complete episode"
    assert not source.exists()


def test_a_rename_that_cannot_happen_still_copies(tmp_path, monkeypatch):
    """Two paths can share a device number across a junction or mount point."""
    from bankai.backend.transfer import _native_move

    source = tmp_path / "staging" / "episode.mkv"
    destination = tmp_path / "media" / "episode.mkv"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"complete episode")

    real_replace = Path.replace

    def refuse(self, target):
        # Only the direct staging -> library rename is refused. The copy path
        # finishes with a replace of its own .part file, and failing that too
        # would test nothing.
        if self.parent.name == "staging":
            raise OSError(18, "Invalid cross-device link")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", refuse)
    monkeypatch.setattr("bankai.backend.transfer._same_volume", lambda *_: True)
    _native_move(source, destination, progress=lambda _line: None)

    assert destination.read_bytes() == b"complete episode"
    assert not source.exists()


def test_a_staged_duplicate_is_discarded_when_the_library_already_has_it(tmp_path, monkeypatch):
    """This is what filled a 465 GB drive with 256 GiB of staging.

    Each retry staged the episode again, found the destination already there,
    skipped the transfer and walked away from the copy it had just made.
    """
    from bankai.backend import transfer as transfer_mod

    staged = tmp_path / "library" / "Shows" / "Show" / "episode.mkv"
    published = tmp_path / "media" / "episode.mkv"
    staged.parent.mkdir(parents=True)
    published.parent.mkdir(parents=True)
    staged.write_bytes(b"same bytes")
    published.write_bytes(b"same bytes")

    item = transfer_mod.TransferItem(source=staged, destination=published, kind="show")
    monkeypatch.setattr(
        transfer_mod, "get_settings", lambda: type("S", (), {"output": type("O", (), {"directory": tmp_path / "library"})()})()
    )
    transfer_mod._discard_staged_duplicate(item, progress=lambda _line: None)

    assert not staged.exists()
    assert published.read_bytes() == b"same bytes"


def test_a_staged_copy_is_kept_when_the_published_one_differs(tmp_path, monkeypatch):
    """A destination of a different size is truncated or a different encode.

    Six files in the library were shorter than their staged copies and
    cluster-aligned, which is what an interrupted write looks like. Deleting
    staging there would have left the broken one as the only one.
    """
    from bankai.backend import transfer as transfer_mod

    staged = tmp_path / "library" / "episode.mkv"
    published = tmp_path / "media" / "episode.mkv"
    staged.parent.mkdir(parents=True)
    published.parent.mkdir(parents=True)
    staged.write_bytes(b"the whole episode")
    published.write_bytes(b"truncated")

    item = transfer_mod.TransferItem(source=staged, destination=published, kind="show")
    lines: list[str] = []
    transfer_mod._discard_staged_duplicate(item, progress=lines.append)

    assert staged.exists()
    assert any("size differs" in line for line in lines)


def test_one_volume_renames_even_where_rsync_is_installed(tmp_path, monkeypatch):
    """Linux always has rsync, and rsync copies even within one filesystem.

    Staging and the library share a volume by design, so the publish should
    be a rename there -- instant, and no second pass over a 9p mount.
    """
    from bankai.backend import transfer as transfer_mod

    source = tmp_path / "library" / "Movies" / "Film (2020)" / "Film (2020).mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"feature")
    monkeypatch.setenv("BANKAI_TRANSFER__MOVIES_DIR", str(tmp_path / "media12" / "movies"))
    reset_settings_cache()

    # rsync present, as it is on any Linux host.
    monkeypatch.setattr(transfer_mod.shutil, "which", lambda _name: "/usr/bin/rsync")
    ran_rsync = False

    def fail_if_used(*_args, **_kwargs):
        nonlocal ran_rsync
        ran_rsync = True

    monkeypatch.setattr(transfer_mod, "_run_rsync", fail_if_used)

    result = transfer_mod.transfer_with_rsync([source], progress=lambda _line: None)

    assert ran_rsync is False
    assert len(result.transferred) == 1
    assert result.transferred[0].destination.read_bytes() == b"feature"
    assert not source.exists()
