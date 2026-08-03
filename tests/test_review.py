"""Tests for the library review state store (incl. sync-review flags)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bankai.web import review as review_mod


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point the review store at an isolated state dir.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))


def test_default_state_has_no_sync_flag(tmp_path: Path) -> None:
    st = review_mod.get_state(tmp_path / "movie.mkv")
    assert st.needs_sync_review is False
    assert st.sync_confidence is None
    assert st.auto_delay_ms == 0


def test_set_sync_review_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    review_mod.set_sync_review(path, needs_review=True, confidence=0.42, applied_delay_ms=-1200)
    st = review_mod.get_state(path)
    assert st.needs_sync_review is True
    assert st.sync_confidence == 0.42
    assert st.auto_delay_ms == -1200
    # Stage remains the default review stage.
    assert st.stage == "review"


def test_legacy_duplicate_padded_source_fps_is_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    review_mod.set_sync_review(
        path,
        needs_review=False,
        confidence=0.9,
        source_fps=60.0,
        reference_fps=23.976,
        drift_ratio=1.0,
    )

    assert review_mod.get_state(path).source_fps == pytest.approx(23.976)


def test_source_provenance_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"

    state = review_mod.set_sources(
        path,
        german_source_url="https://voe.sx/german",
        torrent_source_url="magnet:?xt=urn:btih:abc",
        torrent_source_title="Movie.2024.1080p",
    )

    assert state.german_source_url == "https://voe.sx/german"
    assert state.torrent_source_url == "magnet:?xt=urn:btih:abc"
    assert state.torrent_source_title == "Movie.2024.1080p"
    assert review_mod.get_state(path).torrent_source_title == "Movie.2024.1080p"


def test_new_output_resets_review_and_operation_state(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    review_mod.set_delay(path, 900)
    review_mod.set_stage(path, "approved")
    review_mod.set_transfer(path, "done", percent=100)
    review_mod.set_repack(path, "repacking", percent=45, kind="audio")

    state = review_mod.reset_for_new_output(path)

    assert state.stage == "review"
    assert state.delay_ms == 0
    assert state.transfer_status == "idle"
    assert state.transfer_percent == 0
    assert state.repack_status == "idle"
    assert state.repack_percent == 0
    assert state.repack_kind is None


def test_sync_review_survives_stage_change(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    review_mod.set_sync_review(path, needs_review=True, confidence=0.5)
    review_mod.set_stage(path, "approved")
    st = review_mod.get_state(path)
    assert st.stage == "approved"
    assert st.needs_sync_review is True


def test_repack_state_stays_on_the_library_entry(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"

    running = review_mod.set_repack(path, "repacking", percent=37.5, kind="audio")
    assert running.stage == "repacking"
    assert running.repack_status == "repacking"
    assert running.repack_percent == 37.5
    assert running.repack_kind == "audio"

    failed = review_mod.set_repack(path, "failed", note="mkvmerge failed")
    assert failed.stage == "review"
    assert failed.repack_status == "failed"
    assert failed.note == "mkvmerge failed"


def test_completed_repack_restores_the_correct_stage(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mkv"
    review_mod.set_repack(audio, "repacking", kind="audio")
    assert review_mod.set_repack(audio, "done", percent=100).stage == "approved"

    torrent = tmp_path / "torrent.mkv"
    review_mod.set_repack(torrent, "repacking", kind="torrent")
    assert review_mod.set_repack(torrent, "done", percent=100).stage == "review"


def test_created_at_is_written_once(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"

    first = review_mod.ensure_created_at(path, 100.0)
    second = review_mod.ensure_created_at(path, 900.0)

    assert first.created_at == 100.0
    assert second.created_at == 100.0


def test_save_retries_a_transient_windows_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source: str | Path, target: str | Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "temporarily denied")
        real_replace(source, target)

    monkeypatch.setattr(review_mod.os, "replace", flaky_replace)

    state = review_mod.set_stage(tmp_path / "movie.mkv", "approved")

    assert state.stage == "approved"
    assert attempts == 3
