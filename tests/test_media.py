from __future__ import annotations

from pathlib import Path

import pytest

from bankai.config import get_settings
from bankai.web.media import _track_duration, invalidate_server_cache, scan_server


def test_track_duration_prefers_matroska_track_tag_over_container_duration() -> None:
    stream = {
        "duration": "2415.584000",
        "tags": {"DURATION": "00:36:56.042000000"},
    }

    assert _track_duration(stream) == pytest.approx(2216.042)


def test_track_duration_falls_back_to_stream_duration() -> None:
    assert _track_duration({"duration": "123.456", "tags": {}}) == pytest.approx(123.456)


def test_scan_server_reports_root_and_prefers_populated_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_root = tmp_path / "g" / "shows"
    populated_root = tmp_path / "e" / "shows"
    (empty_root / "Bleach").mkdir(parents=True)
    season = populated_root / "Bleach" / "Season 01"
    season.mkdir(parents=True)
    (season / "Bleach - S01E01.mkv").write_bytes(b"episode")
    monkeypatch.setattr(
        get_settings().web,
        "server_show_dirs",
        [empty_root, populated_root],
    )
    invalidate_server_cache()

    (show,) = scan_server("show", use_cache=False)

    assert show.location == str(populated_root / "Bleach")
    assert show.directory == str(populated_root)
    invalidate_server_cache()
