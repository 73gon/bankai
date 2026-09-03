from __future__ import annotations

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


def test_plan_transfer_routes_anime_to_dedicated_library_with_tmdb_id(
    tmp_path: Path,
) -> None:
    show = (
        tmp_path
        / "library"
        / "Shows"
        / "Frieren Beyond Journey's End (2023) [tmdbid-209867]"
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
        / "Frieren Beyond Journey's End (2023) [tmdbid-209867]"
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
        / "Attack on Titan (2013) [tmdbid-1429]"
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

    _native_move(source, destination, progress=progress.append)

    assert calls == 2
    assert destination.read_bytes() == b"complete episode"
    assert not source.exists()
    assert any("waiting_for_file_lock" in line for line in progress)
