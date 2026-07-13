"""Tests for web job scheduler helpers (transfer column plumbing)."""

from __future__ import annotations

from bankai.web.jobs import _transfer_target


def test_transfer_target_extracts_path() -> None:
    assert _transfer_target(["transfer-run", "/mnt/media/bankai/Movies/X/X.mkv", "--kind", "movie"]) == "/mnt/media/bankai/Movies/X/X.mkv"


def test_transfer_target_skips_leading_flags() -> None:
    assert _transfer_target(["transfer-run", "--kind", "show", "/lib/Shows/S/E.mkv"]) == "/lib/Shows/S/E.mkv"


def test_transfer_target_ignores_non_transfer_jobs() -> None:
    assert _transfer_target(["run", "Movie", "--url", "http://x"]) is None
    assert _transfer_target([]) is None
