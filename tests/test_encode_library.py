"""Oversized videos encoded again, one at a time, and put in place only once checked."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bankai.processor import shrink
from bankai.web import encode_library as encode
from bankai.web import track_cleanup as tracks


def entry(codec="hevc", width=3840, mbps=51.0, *, bit_depth=10, dv=None, interlaced=False, scope="shows", size=34 * 2**30, duration=4880.0):
    return {
        "scope": scope,
        "size": size,
        "mtime": 0.0,
        "tracks": [],
        "duration": duration,
        "bit_rate": None,
        "video": {
            "codec": codec, "width": width, "height": width * 9 // 16, "bit_depth": bit_depth,
            "interlaced": interlaced, "hdr": True, "dv": dv, "bps": int(mbps * 1e6),
        },
    }


def test_a_4k_remux_is_encoded_as_hevc_at_22():
    change, reason = encode.assess("/m/GoT - S08E03.mkv", entry())

    assert reason is None
    assert (change["codec"], change["quality"], change["mbps"]) == ("hevc", 22, 51.0)
    # (51 - 22) Mbps for 4880 s, in bytes.
    assert change["estimate"] == pytest.approx(29e6 / 8 * 4880, rel=0.01)


def test_h264_stays_h264_and_only_well_above_the_line():
    change, _ = encode.assess("/m/Ice Age (2009).mkv", entry("h264", 1920, 25.4, bit_depth=8))
    small, _ = encode.assess("/m/Gladiator II (2024).mkv", entry("h264", 1920, 16.0, bit_depth=8))

    assert (change["codec"], change["quality"]) == ("h264", 19)
    assert small is None


def test_10_bit_h264_becomes_hevc():
    change, _ = encode.assess("/m/Old Anime BD - 01.mkv", entry("h264", 1920, 20.0, bit_depth=10))

    assert (change["codec"], change["quality"]) == ("hevc", 20)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (entry(dv=5), "Dolby Vision profile 5"),
        (entry("mpeg2video", 1920, 30.0, bit_depth=8, interlaced=True), "interlaced picture"),
    ],
)
def test_what_would_go_wrong_is_left_with_its_reason(row, reason):
    assert encode.assess("/m/a.mkv", row) == (None, reason)


def test_efficient_codecs_and_other_containers_are_left_alone():
    assert encode.assess("/m/a.mkv", entry("av1")) == (None, None)
    assert encode.assess("/m/a.mp4", entry()) == (None, None)


def test_the_work_folder_is_on_the_file_s_own_drive():
    assert encode.work_dir(Path("/mnt/e/media/movies/F (2000)/F (2000).mkv")).parent == Path("/mnt/e/bankai/encode")


@pytest.fixture()
def library(monkeypatch, tmp_path):
    """One video per library on disk, the inventory knowing them as these entries."""
    rows: dict[str, dict] = {}
    monkeypatch.setattr(tracks, "load_inventory", lambda: rows)
    monkeypatch.setattr(tracks, "note", lambda path, scope: None)
    from bankai.web import library_walk

    monkeypatch.setattr(library_walk, "files", lambda roots: [])
    monkeypatch.setattr(encode, "work_dir", lambda path: tmp_path / "work" / path.stem)

    def add(name: str, row: dict, *, data: bytes = b"x" * 1000, age: float = 7200.0) -> Path:
        path = tmp_path / row["scope"] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        rows[str(path)] = {**row, "size": len(data)}
        return path

    return add


def test_the_biggest_saving_goes_first_and_files_done_are_not_taken_again(library):
    small = library("A - S01E01.mkv", entry(mbps=35.0))
    big = library("A - S01E02.mkv", entry(mbps=60.0))
    encode._save_state({"encoded": {str(small): 1000}})

    rows, _left = encode.candidates()

    assert [path for path, _scope, _change in rows] == [str(big)]


def fake_shrink(monkeypatch, *, size: int = 400, ssim: float = 0.99, during=None):
    calls: list[dict] = []

    def run(source, destination, *, work, quality, codec, on_progress=None):
        calls.append({"source": source, "quality": quality, "codec": codec})
        work.mkdir(parents=True, exist_ok=True)
        if on_progress:
            on_progress("encode", 0.5)
        destination.write_bytes(b"n" * size)
        if during:
            during()
        return {"dolby_vision": 8}

    monkeypatch.setattr(shrink, "shrink", run)
    monkeypatch.setattr(shrink, "quality_check", lambda *_a, **_k: [{"ssim": ssim}, {"ssim": 0.999}])
    return calls


def test_an_encoded_file_takes_the_original_s_place(library, monkeypatch):
    path = library("GoT - S08E03.mkv", entry())
    before = path.stat()
    encode.set_enabled("shows", True)
    calls = fake_shrink(monkeypatch)

    result = encode.encode_next()

    assert path.read_bytes() == b"n" * 400
    assert (calls[0]["codec"], calls[0]["quality"]) == ("hevc", 22)
    assert path.stat().st_mtime_ns == before.st_mtime_ns + 1_000_000_000
    assert result["saved"] == 600
    # Done once: not taken again though it may stay above the line.
    assert encode.candidates()[0] == []
    assert encode.overview()["scopes"]["shows"]["done"] == {"files": 1, "bytes": 600}


def test_a_result_not_a_fifth_smaller_is_thrown_away(library, monkeypatch):
    path = library("A - S01E01.mkv", entry())
    encode.set_enabled("shows", True)
    fake_shrink(monkeypatch, size=900)

    result = encode.encode_next()

    assert "smaller" in result["skipped"]
    assert path.read_bytes() == b"x" * 1000
    assert encode.overview()["scopes"]["shows"]["left"] == {"not worth it": 1}


def test_a_picture_that_differs_is_not_put_in_place(library, monkeypatch):
    path = library("A - S01E01.mkv", entry())
    encode.set_enabled("shows", True)
    fake_shrink(monkeypatch, ssim=0.62)

    result = encode.encode_next()

    assert "differs" in result["error"]
    assert path.read_bytes() == b"x" * 1000


def test_an_original_that_changed_meanwhile_is_kept(library, monkeypatch):
    path = library("A - S01E01.mkv", entry())
    encode.set_enabled("shows", True)
    fake_shrink(monkeypatch, during=lambda: path.write_bytes(b"an upgrade"))

    result = encode.encode_next()

    assert "changed" in result["error"]
    assert path.read_bytes() == b"an upgrade"


def test_only_libraries_switched_on_and_files_free_to_touch(library, monkeypatch):
    library("Off - S01E01.mkv", entry(scope="movies"))
    fresh = library("New - S01E01.mkv", entry(), age=60)
    busy = library("Busy - S01E01.mkv", entry())
    encode.set_enabled("shows", True)
    monkeypatch.setattr(tracks, "progress", {"path": str(busy)})
    calls = fake_shrink(monkeypatch)

    assert encode.encode_next() is None
    assert calls == []
    assert encode.overview()["scopes"]["shows"]["waiting"] == {"changed in the last hour": 1, "having tracks taken out": 1}
    assert fresh.read_bytes() == b"x" * 1000


def test_an_encode_cut_off_keeps_its_steps_and_drops_its_result(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "video.hevc").write_bytes(b"done encode")
    (work / "out.mkv").write_bytes(b"half")
    encode._save_state({"current": {"path": str(tmp_path / "A.mkv"), "scope": "shows", "work": str(work)}})

    encode.recover()

    assert (work / "video.hevc").exists() and not (work / "out.mkv").exists()
    assert "current" not in encode._state()


def test_the_page_answers_and_switches(library):
    from fastapi.testclient import TestClient

    from bankai.web.app import create_app

    library("GoT - S08E06.mkv", entry(mbps=55.0))
    with TestClient(create_app()) as client:
        body = client.get("/api/encode").json()
        assert body["scopes"]["shows"]["preview"]["files"] == 1
        assert body["scopes"]["shows"]["next"][0]["codec"] == "hevc"
        assert client.post("/api/encode/shows", json={"enabled": True}).json()["rules"] == {"enabled": True}
        assert client.post("/api/encode/shows", json={"enabled": "yes"}).status_code == 422
        assert client.post("/api/encode/music", json={"enabled": True}).status_code == 404


def test_an_encode_stopped_by_a_restart_keeps_its_steps_and_is_no_failure(library, monkeypatch, tmp_path):
    path = library("GoT - S08E06.mkv", entry())
    encode.set_enabled("shows", True)

    def stopped(source, destination, *, work, quality, codec, on_progress=None):
        work.mkdir(parents=True, exist_ok=True)
        (work / "video.dv.hevc").write_bytes(b"an hour of encoding")
        raise shrink.ShrinkInterrupted("mkvmerge was stopped")

    monkeypatch.setattr(shrink, "shrink", stopped)

    result = encode.encode_next()

    assert result["interrupted"] is True
    assert (tmp_path / "work" / path.stem / "video.dv.hevc").exists()
    assert encode._state()["failed"] == {}
    assert path.read_bytes() == b"x" * 1000
