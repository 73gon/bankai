from __future__ import annotations

from pathlib import Path

import pytest

from bankai.backend.transfer import (
    TransferItem,
    TransferResult,
    format_transfer_summary,
    plan_transfer,
)
from bankai.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _transfer_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANKAI_OUTPUT__DIRECTORY", str(tmp_path / "library"))
    monkeypatch.setenv("BANKAI_TRANSFER__MOVIES_DIR", str(tmp_path / "media12" / "movies"))
    monkeypatch.setenv("BANKAI_TRANSFER__SHOWS_DIR", str(tmp_path / "media12" / "shows"))
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


def test_transfer_summary_mentions_skipped_existing(tmp_path: Path) -> None:
    source = tmp_path / "movie.mkv"
    destination = tmp_path / "media12" / "movies" / "movie.mkv"
    result = TransferResult()
    result.skipped.append(TransferItem(source=source, destination=destination, kind="movie"))

    summary = format_transfer_summary(result)

    assert "Skipped existing: 1" in summary
    assert "movie.mkv" in summary
