"""Tests for the library review state store (incl. sync-review flags)."""

from __future__ import annotations

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
    review_mod.set_sync_review(
        path, needs_review=True, confidence=0.42, applied_delay_ms=-1200
    )
    st = review_mod.get_state(path)
    assert st.needs_sync_review is True
    assert st.sync_confidence == 0.42
    assert st.auto_delay_ms == -1200
    # Stage remains the default review stage.
    assert st.stage == "review"


def test_sync_review_survives_stage_change(tmp_path: Path) -> None:
    path = tmp_path / "movie.mkv"
    review_mod.set_sync_review(path, needs_review=True, confidence=0.5)
    review_mod.set_stage(path, "approved")
    st = review_mod.get_state(path)
    assert st.stage == "approved"
    assert st.needs_sync_review is True
