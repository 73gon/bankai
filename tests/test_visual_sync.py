"""Tests for visual timeline matching helpers."""

from __future__ import annotations

from bankai.processor.visual_sync import (
    VisualMatch,
    _confidence,
    _fit_timeline,
    _hash_distance,
    _median,
    _sample_times,
    _theil_sen_slope,
    _variance,
)


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


def test_median_handles_even_and_odd() -> None:
    assert _median([3.0, 1.0, 2.0]) == 2.0
    assert _median([4.0, 1.0, 3.0, 2.0]) == 2.5
    assert _median([]) == 0.0


def test_median_offset_is_robust_to_an_outlier() -> None:
    # Constant 12s offset with one bad match; median must ignore the outlier.
    offsets = [12.0, 12.1, 11.9, 60.0]
    assert _median(offsets) == 12.05


def test_theil_sen_slope_detects_speed_drift() -> None:
    # source runs 4.27% faster than reference (25 -> 23.976 fps case).
    slope = 25.0 / 23.976
    xs = [100.0, 500.0, 900.0, 1300.0]
    ys = [x * slope + 5.0 for x in xs]
    assert abs(_theil_sen_slope(xs, ys) - slope) < 1e-9


def test_theil_sen_slope_is_one_for_pure_offset() -> None:
    xs = [100.0, 500.0, 900.0]
    ys = [x + 12.0 for x in xs]
    assert _theil_sen_slope(xs, ys) == 1.0


def test_confidence_high_for_tight_agreement() -> None:
    matches = [
        VisualMatch(reference_time=100.0, source_time=112.0, distance=0.02),
        VisualMatch(reference_time=500.0, source_time=512.0, distance=0.03),
        VisualMatch(reference_time=900.0, source_time=912.0, distance=0.02),
    ]
    conf = _confidence(matches, spread=0.05, sample_count=3)
    assert conf > 0.8


def test_confidence_low_for_scattered_offsets() -> None:
    matches = [
        VisualMatch(reference_time=100.0, source_time=112.0, distance=0.2),
        VisualMatch(reference_time=500.0, source_time=530.0, distance=0.2),
    ]
    conf = _confidence(matches, spread=8.0, sample_count=9)
    assert conf < 0.3


def test_variance_flat_frame_is_zero() -> None:
    assert _variance(bytes([100] * 16)) == 0.0
    assert _variance(bytes([0, 255] * 8)) > 0.0

