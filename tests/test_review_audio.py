"""A large file's audio, read once for the review studio."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from bankai.web import review_audio

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")


def test_each_track_is_read_once_into_small_files(tmp_path, monkeypatch) -> None:
    source = tmp_path / "film.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=stereo", "-t", "4", "-map", "0", "-map", "1", "-c:a", "flac", str(source)],
        check=True,
    )
    monkeypatch.setattr(review_audio, "folder", lambda path: tmp_path / "cache")
    review_audio._prepare(source)
    done = review_audio.ready(source)
    assert done["streams"] == [0, 1]
    tone = review_audio.samples(source, 0, 1.0, 2.0, detailed=True)
    silence = review_audio.samples(source, 1, 1.0, 2.0, detailed=True)
    assert len(tone) == 2 * review_audio.DETAIL_RATE
    assert max(abs(sample) for sample in tone) > 1000 and max(abs(sample) for sample in silence) == 0
    assert len(review_audio.samples(source, 0, 0, 4, detailed=False)) == 4 * review_audio.OVERVIEW_RATE
    assert review_audio.listening_copy(source, 1).stat().st_size > 0
