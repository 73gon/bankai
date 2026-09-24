"""Identifying the encode and audio of every episode already in the library."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bankai.web import anime_library, library_walk


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Keep the probe cache out of the real state directory."""
    monkeypatch.setattr(anime_library, "_CODEC_CACHE", None)
    monkeypatch.setattr(
        anime_library, "_codec_cache_path", lambda: tmp_path / "anime_codecs.json"
    )
    yield
    monkeypatch.setattr(anime_library, "_CODEC_CACHE", None)


def _library(tmp_path, count=3):
    root = tmp_path / "shows_anime" / "Show" / "Season 01"
    root.mkdir(parents=True)
    for number in range(1, count + 1):
        (root / f"Show - S01E{number:02d}.mkv").write_bytes(b"video" * number)
    return tmp_path / "shows_anime"


def _episode(root, number=1):
    return root / "Show" / "Season 01" / f"Show - S01E{number:02d}.mkv"


def _row(path, season=1, episode=1):
    return {"path": str(path), "season_number": season, "episode": episode}


def test_the_sweep_identifies_and_caches_each_episode(tmp_path, monkeypatch):
    root = _library(tmp_path, count=3)
    probes: list[str] = []
    monkeypatch.setattr(
        anime_library,
        "probe_streams",
        lambda path: probes.append(path.name) or {"codec": "hevc", "audio": ["jpn"]},
    )

    assert anime_library.sweep_codecs(root, limit=10) == {"probed": 3, "remaining": 0}

    # A second pass probes nothing: every file is already identified.
    probes.clear()
    assert anime_library.sweep_codecs(root, limit=10) == {"probed": 0, "remaining": 0}
    assert probes == []


def test_the_sweep_is_bounded_so_it_never_hogs_the_disk(tmp_path, monkeypatch):
    root = _library(tmp_path, count=5)
    monkeypatch.setattr(
        anime_library, "probe_streams", lambda path: {"codec": "avc", "audio": ["jpn"]}
    )

    first = anime_library.sweep_codecs(root, limit=2)
    assert (first["probed"], first["remaining"]) == (2, 3)
    # The rest are picked up by later passes.
    assert anime_library.sweep_codecs(root, limit=10)["probed"] == 3


def test_a_replaced_episode_is_identified_again(tmp_path, monkeypatch):
    """An upgrade rewrites the file, which is exactly when the answer changes."""
    root = _library(tmp_path, count=1)
    episode = _episode(root)
    monkeypatch.setattr(
        anime_library, "probe_streams", lambda path: {"codec": "avc", "audio": ["jpn"]}
    )
    anime_library.sweep_codecs(root, limit=10)
    assert anime_library.probed_codecs([_row(episode)]) == {(1, 1): "avc"}

    # Published the way the pipeline does it: written beside, renamed over.
    # That changes the folder, which is what the held library tree watches.
    upgrade = episode.with_name(episode.name + ".part")
    upgrade.write_bytes(b"a different, larger file entirely")
    upgrade.replace(episode)
    later = time.time() + 5  # whole-second mtimes on some filesystems
    os.utime(episode.parent, (later, later))
    library_walk.refresh_all()
    monkeypatch.setattr(
        anime_library, "probe_streams", lambda path: {"codec": "hevc", "audio": ["jpn"]}
    )
    assert anime_library.sweep_codecs(root, limit=10)["probed"] == 1
    assert anime_library.probed_codecs([_row(episode)]) == {(1, 1): "hevc"}


def test_an_unprobed_episode_simply_has_no_answer(tmp_path):
    assert anime_library.probed_codecs([_row(tmp_path / "nope.mkv")]) == {}


def test_a_missing_ffprobe_does_not_poison_the_cache(tmp_path, monkeypatch):
    """Otherwise every episode would be recorded as unidentifiable forever."""
    root = _library(tmp_path, count=2)
    monkeypatch.setattr("bankai.web.media.ffprobe_bin", lambda: None)

    anime_library.sweep_codecs(root, limit=10)
    assert anime_library.probed_codecs([_row(_episode(root))]) == {}


def test_sweeping_a_library_that_is_not_there_is_harmless(tmp_path):
    assert anime_library.sweep_codecs(tmp_path / "gone", limit=5) == {
        "probed": 0,
        "remaining": 0,
    }


# --- German dubs -----------------------------------------------------------
# Erai-raws ships Japanese audio only, so a German dub in the library came from
# somewhere that cannot be replayed. These episodes are never upgraded.


@pytest.mark.parametrize(
    "audio,expected",
    [
        (["jpn", "ger"], True),
        (["deu"], True),
        (["de"], True),
        (["German"], True),
        (["Deutsch (Dub)"], True),
        (["jpn"], False),
        (["jpn", "eng"], False),
        ([], False),
        # A language that merely contains the letters must not count.
        (["german-adjacent-nonsense"], True),
        (["hungarian"], False),
    ],
)
def test_german_audio_is_recognised(audio, expected):
    assert anime_library.has_german_audio({"audio": audio}) is expected


def test_a_german_dubbed_episode_is_identified_from_its_file(tmp_path, monkeypatch):
    root = _library(tmp_path, count=2)

    def probe(path):
        dubbed = path.name.endswith("E01.mkv")
        return {"codec": "avc", "audio": ["jpn", "ger"] if dubbed else ["jpn"]}

    monkeypatch.setattr(anime_library, "probe_streams", probe)
    anime_library.sweep_codecs(root, limit=10)

    rows = [_row(_episode(root, 1), episode=1), _row(_episode(root, 2), episode=2)]
    assert anime_library.german_dubbed_episodes(rows) == {(1, 1)}


def test_probe_reads_codec_and_audio_from_one_call(monkeypatch):
    import json

    payload = json.dumps(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "tags": {"language": "jpn"}},
                {"codec_type": "audio", "tags": {"language": "ger"}},
            ]
        }
    )

    class Result:
        returncode = 0
        stdout = payload

    monkeypatch.setattr("bankai.web.media.ffprobe_bin", lambda: "ffprobe")
    monkeypatch.setattr(anime_library.subprocess, "run", lambda *a, **k: Result())

    probed = anime_library.probe_streams(Path("x.mkv"))
    assert probed == {"codec": "avc", "audio": ["jpn", "ger"]}
    assert anime_library.has_german_audio(probed) is True


def test_an_untagged_audio_track_falls_back_to_its_title(monkeypatch):
    import json

    payload = json.dumps(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "hevc"},
                {"codec_type": "audio", "tags": {"title": "German Dub"}},
            ]
        }
    )

    class Result:
        returncode = 0
        stdout = payload

    monkeypatch.setattr("bankai.web.media.ffprobe_bin", lambda: "ffprobe")
    monkeypatch.setattr(anime_library.subprocess, "run", lambda *a, **k: Result())

    probed = anime_library.probe_streams(Path("x.mkv"))
    assert probed["codec"] == "hevc"
    assert anime_library.has_german_audio(probed) is True
