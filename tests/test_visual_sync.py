"""Tests for visual timeline matching helpers."""

from __future__ import annotations

from bankai.processor.visual_sync import VisualMatch, _fit_timeline, _hash_distance, _sample_times


def test_fit_timeline_estimates_offset_and_slope() -> None:
    matches = [
        VisualMatch(reference_time=100.0, source_time=112.0, distance=0.05),
        VisualMatch(reference_time=500.0, source_time=512.0, distance=0.04),
        VisualMatch(reference_time=900.0, source_time=912.0, distance=0.03),
    ]

    slope, offset = _fit_timeline(matches)

    assert slope == 1.0
    assert offset == 12.0


def test_hash_distance_is_normalized() -> None:
    assert _hash_distance(0b0000, 0b1111, bits=4) == 1.0
    assert _hash_distance(0b1010, 0b1110, bits=4) == 0.25


def test_sample_times_stay_inside_video() -> None:
    samples = _sample_times(1_000.0, 3)

    assert samples == [150.0, 500.0, 850.0]
