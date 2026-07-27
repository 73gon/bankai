from __future__ import annotations

import pytest

from bankai.web.media import _track_duration


def test_track_duration_prefers_matroska_track_tag_over_container_duration() -> None:
    stream = {
        "duration": "2415.584000",
        "tags": {"DURATION": "00:36:56.042000000"},
    }

    assert _track_duration(stream) == pytest.approx(2216.042)


def test_track_duration_falls_back_to_stream_duration() -> None:
    assert _track_duration({"duration": "123.456", "tags": {}}) == pytest.approx(123.456)
