"""Dolby and DTS audio made AAC, so every browser plays the library's files directly."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from bankai.web import browser_audio as ba
from bankai.web import track_cleanup


def audio(lang: str, codec: str, *, channels: int = 6, bit_rate: int | None = 448_000, default: bool = False, profile: str | None = None) -> dict:
    return {
        "kind": "audio", "lang": lang, "name": None, "codec": codec, "default": default, "forced": False,
        "bytes": None, "channels": channels, "bit_rate": bit_rate, "profile": profile,
    }


def sub(lang: str) -> dict:
    return {"kind": "subtitles", "lang": lang, "name": None, "codec": "subrip", "default": False, "forced": False, "bytes": None}


def test_lossy_dolby_and_dts_are_replaced_and_playable_tracks_kept():
    entry = {"tracks": [audio("ger", "ac3", default=True), audio("eng", "eac3"), audio("jpn", "aac"), audio("fre", "dts", profile="DTS"), sub("ger")]}

    change = ba.plan("/m/a.mkv", entry)

    assert (change["replace"], change["add"]) == ([0, 1, 3], [])


def test_a_language_with_only_lossless_audio_gets_one_aac_from_its_default_track():
    entry = {"tracks": [
        audio("eng", "truehd", channels=8, profile="Dolby TrueHD + Dolby Atmos"),
        audio("eng", "dts", channels=6, profile="DTS-HD MA", default=True),
        audio("ger", "dts", channels=6, profile="DTS-HD MA"),
        audio("ger", "ac3"),
    ]}

    change = ba.plan("/m/a.mkv", entry)

    # English has only lossless: an AAC from its default track. German's AC3 becomes AAC, so nothing is added.
    assert (change["replace"], change["add"]) == ([3], [1])


def test_an_audio_description_is_no_language_s_main_sound():
    described = audio("eng", "ac3")
    described["name"] = "English [Audio Description]"
    entry = {"tracks": [described, audio("eng", "truehd", channels=8, default=True), audio("ger", "eac3")]}

    change = ba.plan("/m/a.mkv", entry)

    assert (change["replace"], change["add"], change["default"]) == ([0, 2], [1], None)


def test_a_lossless_default_gives_its_flag_to_its_language_s_aac():
    entry = {"tracks": [audio("eng", "ac3"), audio("eng", "truehd", channels=8, default=True), audio("ger", "ac3")]}

    change = ba.plan("/m/a.mkv", entry)

    # The English AC3 becomes AAC and the default: browsers play the default track.
    assert (change["replace"], change["add"], change["default"]) == ([0, 2], [], 0)
    # Already AAC beside it: only the flag moves.
    only_flag = ba.plan("/m/a.mkv", {"tracks": [audio("eng", "aac"), audio("eng", "truehd", default=True)]})
    assert (only_flag["replace"], only_flag["add"], only_flag["default"]) == ([], [], 0)


def test_a_file_whose_audio_all_plays_or_that_is_no_mkv_needs_nothing():
    assert ba.plan("/m/a.mkv", {"tracks": [audio("ger", "aac"), audio("jpn", "opus"), audio("eng", "flac")]}) is None
    assert ba.plan("/m/a.mp4", {"tracks": [audio("ger", "ac3")]}) is None


def test_dts_whose_profile_was_not_read_counts_lossy_but_unsure():
    track = audio("eng", "dts")
    del track["profile"]

    assert ba.plan("/m/a.mkv", {"tracks": [track]})["unsure"] is True


def test_the_aac_is_never_thinner_than_its_source():
    assert ba.bitrate(6, 448_000, from_lossless=False) == 448_000
    assert ba.bitrate(6, 640_000, from_lossless=False) == 640_000
    assert ba.bitrate(6, 320_000, from_lossless=False) == 384_000
    assert ba.bitrate(2, 128_000, from_lossless=False) == 192_000
    assert ba.bitrate(2, 1_509_000, from_lossless=False) == 512_000  # past 256k a channel nothing is heard
    assert ba.bitrate(8, None, from_lossless=False) == 512_000
    assert ba.bitrate(6, None, from_lossless=True) == 448_000
    assert ba.bitrate(2, None, from_lossless=True) == 256_000


@pytest.mark.parametrize(
    ("name", "renamed"),
    [
        ("Deutsch DD 5.1", "Deutsch AAC 5.1"),
        ("German [Dolby Digital Plus 7.1]", "German [AAC 7.1]"),
        ("BTM DDP5.1", "BTM AAC 5.1"),
        ("AMZN_EAC3", "AMZN_AAC"),
        ("[Erai-raws]_EAC3_AMZN", "[Erai-raws]_AAC_AMZN"),
        ("AC3 2.0 @ 192 kbps", "AAC 2.0 @ 224 kbps"),
        ("English - AC-3 DOLBY SURROUND EX 5.1", "English - AAC 5.1"),
        ("English [Dolby Digital EX 5.1]", "English [AAC 5.1]"),
        ("DTS-ES Matrix 6.1", "AAC 6.1"),
        ("German [DTS 5.1]", "German [AAC 5.1]"),
        ("English - AC-3 2.0 (Audiodeskription)", "English - AAC 2.0 (Audiodeskription)"),
        # No codec in the name: as it was.
        ("Surround 5.1", "Surround 5.1"),
        ("Deutsch", "Deutsch"),
        ("Stereo @ 192 kbps", "Stereo @ 192 kbps"),
        ("English - Dolby Digital Plus + Atmos 5.1", "English - AAC 5.1"),
        ("E-AC-3 Atmos 5.1", "AAC 5.1"),
        ("Dolby Atmos 5.1", "AAC 5.1"),
        ("English [DTS-HD MA + DTS:X 7.1]", "English [AAC 7.1]"),
        ("DTS-1 510 Kbps", "AAC-224 kbps"),
        ("Dolby Digital Plus / 5.1 / 48 kHz / 640 kbps", "AAC / 5.1 / 48 kHz / 224 kbps"),
        ("GalaxyRG - Split.2016.1080p.BluRay.DDP5.1.x265", "GalaxyRG - Split.2016.1080p.BluRay.AAC5.1.x265"),
        (None, None),
    ],
)
def test_a_replaced_track_s_name_says_aac_for_its_codec(name, renamed):
    assert ba.aac_name(name, 224_000) == renamed


def test_a_lossless_track_s_aac_is_five_one_at_most():
    assert ba.out_channels(audio("eng", "truehd", channels=8)) == 6
    assert ba.out_channels(audio("eng", "dts", channels=2, profile="DTS-HD MA")) == 2
    assert ba.out_channels(audio("eng", "eac3", channels=8)) == 8


def test_only_files_tried_by_hand_are_done_while_it_is_off(monkeypatch):
    inventory = {
        "/m/a.mkv": {"scope": "movies", "size": 1, "tracks": [audio("ger", "ac3")]},
        "/m/b.mkv": {"scope": "movies", "size": 1, "tracks": [audio("ger", "eac3")]},
        "/m/c.mkv": {"scope": "movies", "size": 1, "tracks": [audio("ger", "aac")]},
    }
    monkeypatch.setattr(track_cleanup, "load_inventory", lambda: inventory)
    monkeypatch.setattr(track_cleanup, "held", lambda path: None)
    done = []
    monkeypatch.setattr(ba, "convert_file", lambda path, scope: done.append(str(path)) or {"path": str(path)})

    assert ba.enabled() is False
    assert ba.convert_next() is None
    assert ba.try_files(["/m/b.mkv", "/m/c.mkv"]) == {"queued": 1, "skipped": 1}
    assert ba.enabled() is True
    ba.convert_next()
    assert done == ["/m/b.mkv"]
    assert ba.preview("movies")["files"] == 2


# -- a real file, written anew by ffmpeg and mkvmerge -------------------------------------


needs_tools = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe", "mkvmerge")), reason="needs ffmpeg and mkvmerge"
)


def _make(tmp_path: Path) -> Path:
    """Ten seconds: a picture, German AC3 5.1 (default, 40 ms late), English PCM stereo (lossless), a subtitle."""
    pieces = tmp_path / "pieces"
    pieces.mkdir()
    run = lambda *args: subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)  # noqa: E731
    run("-f", "lavfi", "-i", "testsrc=size=160x90:rate=24:duration=10", "-c:v", "mpeg4", str(pieces / "v.mkv"))
    run("-f", "lavfi", "-i", "sine=f=440:d=10", "-af", "pan=5.1(side)|FL=c0|FR=c0|FC=c0", "-c:a", "ac3", "-b:a", "448k", str(pieces / "de.mka"))
    run("-f", "lavfi", "-i", "sine=f=660:d=10", "-ac", "2", "-c:a", "pcm_s16le", str(pieces / "en.mka"))
    (pieces / "de.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n", encoding="utf-8")
    path = tmp_path / "Film (2000).mkv"
    subprocess.run(
        [
            "mkvmerge", "-q", "-o", str(path), str(pieces / "v.mkv"),
            "--language", "0:ger", "--track-name", "0:Deutsch DD 5.1", "--default-track-flag", "0:1", "--sync", "0:40", str(pieces / "de.mka"),
            "--language", "0:eng", "--track-name", "0:English", "--default-track-flag", "0:0", str(pieces / "en.mka"),
            "--language", "0:ger", "--forced-display-flag", "0:1", str(pieces / "de.srt"),
        ],
        check=True,
    )
    stamp = time.time() - 7200
    os.utime(path, (stamp, stamp))
    return path


@needs_tools
def test_a_file_gets_aac_in_place_of_its_ac3_and_beside_its_lossless_track(tmp_path, monkeypatch):
    path = _make(tmp_path)
    before = path.stat()
    monkeypatch.setattr(track_cleanup, "roots", lambda scope: [str(tmp_path)])

    result = ba.convert_file(path, "movies")

    assert "error" not in result, result
    assert (result["replaced"], result["added"]) == (1, 1)
    info = json.loads(subprocess.run(["mkvmerge", "-J", str(path)], capture_output=True, text=True, check=True).stdout)
    tracks = [
        (t["type"], t["codec"], t["properties"].get("language"), t["properties"].get("track_name"),
         t["properties"].get("default_track"), t["properties"].get("forced_track"), t["properties"].get("audio_channels"))
        for t in info["tracks"]
    ]
    assert tracks == [
        ("video", "MPEG-4p2", "und", None, False, False, None),
        # The AC3's place, name and flags, as AAC with its six channels.
        ("audio", "AAC", "ger", "Deutsch AAC 5.1", True, False, 6),
        # Added before the lossless English, which keeps everything.
        ("audio", "AAC", "eng", "AAC 2.0", False, False, 2),
        ("audio", "PCM", "eng", "English", False, False, 2),
        ("subtitles", "SubRip/SRT", "ger", None, True, True, None),
    ]
    starts = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert abs(float(starts) - 0.040) < 0.03  # still 40 ms late
    assert path.stat().st_mtime_ns == before.st_mtime_ns + 1_000_000_000
    assert not list(tmp_path.glob(".*"))
    assert ba.plan(str(path), track_cleanup.load_inventory()[str(path)]) is None


@needs_tools
def test_a_file_changed_while_it_was_written_is_left_as_it_is(tmp_path, monkeypatch):
    path = _make(tmp_path)
    real_run = ba._run

    def touching(command, step):
        result = real_run(command, step)
        if step == "writing":
            os.utime(path, None)
        return result

    monkeypatch.setattr(ba, "_run", touching)
    original = path.read_bytes()

    result = ba.convert_file(path, "movies")

    assert result["error"] == "the original changed while it was being written anew"
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".*"))


def test_a_copy_cut_off_by_a_restart_is_thrown_away(tmp_path):
    parts = [tmp_path / ".a.audio.part.mka", tmp_path / ".a.mkv.audio.part"]
    for part in parts:
        part.write_bytes(b"half")
    ba._save({**ba._state(), "current": {"path": str(tmp_path / "a.mkv"), "parts": [str(p) for p in parts]}})

    ba.recover()

    assert not any(part.exists() for part in parts)
    assert "current" not in ba._state()


@needs_tools
def test_a_file_whose_audio_all_plays_but_its_lossless_default_only_moves_the_flag(tmp_path):
    pieces = tmp_path / "pieces"
    pieces.mkdir()
    run = lambda *args: subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)  # noqa: E731
    run("-f", "lavfi", "-i", "testsrc=size=160x90:rate=24:duration=5", "-c:v", "mpeg4", str(pieces / "v.mkv"))
    run("-f", "lavfi", "-i", "sine=d=5", "-ac", "2", "-c:a", "aac", str(pieces / "aac.mka"))
    run("-f", "lavfi", "-i", "sine=d=5", "-ac", "2", "-c:a", "pcm_s16le", str(pieces / "pcm.mka"))
    path = tmp_path / "Film (2000).mkv"
    subprocess.run(
        [
            "mkvmerge", "-q", "-o", str(path), str(pieces / "v.mkv"),
            "--language", "0:eng", "--default-track-flag", "0:0", str(pieces / "aac.mka"),
            "--language", "0:eng", "--default-track-flag", "0:1", str(pieces / "pcm.mka"),
        ],
        check=True,
    )

    result = ba.convert_file(path, "movies")

    assert "error" not in result, result
    assert (result["replaced"], result["added"], result["default_moved"]) == (0, 0, True)
    info = json.loads(subprocess.run(["mkvmerge", "-J", str(path)], capture_output=True, text=True, check=True).stdout)
    assert [(t["codec"], t["properties"].get("default_track")) for t in info["tracks"] if t["type"] == "audio"] == [("AAC", True), ("PCM", False)]
