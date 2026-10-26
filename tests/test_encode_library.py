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


def test_an_anime_episode_that_is_not_hevc_becomes_hevc_whatever_its_bitrate():
    change, reason = encode.assess("/a/Show - S01E01.mkv", entry("h264", 1920, 4.0, bit_depth=8, scope="anime", duration=1440.0))
    shows, _ = encode.assess("/s/Show - S01E01.mkv", entry("h264", 1920, 4.0, bit_depth=8, duration=1440.0))

    assert reason is None and shows is None
    assert (change["codec"], change["quality"]) == ("hevc", 20)
    # About 45% of its picture's bits.
    assert change["estimate"] == pytest.approx(4e6 / 8 * 1440 * 0.45, rel=0.01)


def test_hevc_and_av1_anime_are_not_listed_for_that():
    assert encode.assess("/a/a.mkv", entry("hevc", 1920, 3.0, scope="anime")) == (None, None)
    assert encode.assess("/a/a.mkv", entry("av1", 1920, 3.0, scope="anime")) == (None, None)


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


def test_the_biggest_saving_is_listed_first_and_files_done_are_not_listed_again(library):
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


def test_nothing_is_encoded_unasked(library, monkeypatch):
    path = library("GoT - S08E03.mkv", entry())
    calls = fake_shrink(monkeypatch)

    assert encode.encode_next() is None
    assert calls == [] and path.read_bytes() == b"x" * 1000


def test_a_queued_file_is_encoded_and_takes_the_original_s_place(library, monkeypatch):
    path = library("GoT - S08E03.mkv", entry())
    before = path.stat()
    calls = fake_shrink(monkeypatch)
    assert encode.enqueue([str(path)])["added"] == 1

    result = encode.encode_next()

    assert path.read_bytes() == b"n" * 400
    assert (calls[0]["codec"], calls[0]["quality"]) == ("hevc", 22)
    assert path.stat().st_mtime_ns == before.st_mtime_ns + 1_000_000_000
    assert result["saved"] == 600
    assert encode.queue() == []
    # Done once: not listed again though it may stay above the line.
    assert encode.candidates()[0] == []
    assert encode.overview()["done"] == {"files": 1, "bytes": 600}


def test_the_queue_goes_in_its_order_one_at_a_time(library, monkeypatch):
    first = library("A - S01E01.mkv", entry(mbps=35.0))
    second = library("A - S01E02.mkv", entry(mbps=80.0))
    calls = fake_shrink(monkeypatch)
    encode.enqueue([str(first), str(second)])

    encode.encode_next()

    assert [call["source"] for call in calls] == [first]
    assert [row["path"] for row in encode.queue()] == [str(second)]


def test_only_files_the_page_lists_can_be_queued(library):
    worth = library("A - S01E01.mkv", entry())
    small = library("A - S01E02.mkv", entry(mbps=5.0))

    result = encode.enqueue([str(worth), str(small), str(worth)])

    assert (result["added"], result["refused"], result["queued"]) == (1, [small.name], 1)


def test_files_leave_the_queue_by_hand(library):
    first = library("A - S01E01.mkv", entry())
    second = library("A - S01E02.mkv", entry())
    encode.enqueue([str(first), str(second)])

    assert encode.dequeue([str(first)]) == 1
    assert [row["path"] for row in encode.queue()] == [str(second)]
    assert encode.dequeue() == 1 and encode.queue() == []


def test_a_result_not_a_fifth_smaller_is_thrown_away(library, monkeypatch):
    path = library("A - S01E01.mkv", entry())
    fake_shrink(monkeypatch, size=900)
    encode.enqueue([str(path)])

    result = encode.encode_next()

    assert "smaller" in result["skipped"]
    assert path.read_bytes() == b"x" * 1000
    assert encode.queue() == []
    assert encode.overview()["scopes"]["shows"]["left"] == {"not worth it": 1}


def test_a_picture_that_differs_is_not_put_in_place_and_can_be_queued_again(library, monkeypatch):
    path = library("A - S01E01.mkv", entry())
    fake_shrink(monkeypatch, ssim=0.62)
    encode.enqueue([str(path)])

    result = encode.encode_next()

    assert "differs" in result["error"]
    assert path.read_bytes() == b"x" * 1000
    listed = encode.overview()["scopes"]["shows"]["candidates"]
    assert "differs" in listed[0]["failed"]
    assert encode.enqueue([str(path)])["added"] == 1


def test_an_original_that_changed_meanwhile_is_kept(library, monkeypatch):
    path = library("A - S01E01.mkv", entry())
    fake_shrink(monkeypatch, during=lambda: path.write_bytes(b"an upgrade"))
    encode.enqueue([str(path)])

    result = encode.encode_next()

    assert "changed" in result["error"]
    assert path.read_bytes() == b"an upgrade"


def test_queued_files_not_free_to_touch_keep_their_place(library, monkeypatch):
    fresh = library("New - S01E01.mkv", entry(), age=60)
    busy = library("Busy - S01E01.mkv", entry())
    monkeypatch.setattr(tracks, "progress", {"path": str(busy)})
    calls = fake_shrink(monkeypatch)
    encode.enqueue([str(fresh), str(busy)])

    assert encode.encode_next() is None
    assert calls == []
    waiting = {row["path"]: row["waiting"] for row in encode.overview()["queue"]}
    assert waiting == {str(fresh): "changed in the last hour", str(busy): "having tracks taken out"}


def test_an_encode_cut_off_keeps_its_steps_and_drops_its_result(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "video.hevc").write_bytes(b"done encode")
    (work / "out.mkv").write_bytes(b"half")
    encode._save_state({"current": {"path": str(tmp_path / "A.mkv"), "scope": "shows", "work": str(work)}})

    encode.recover()

    assert (work / "video.hevc").exists() and not (work / "out.mkv").exists()
    assert "current" not in encode._state()


def test_an_encode_stopped_by_a_restart_keeps_its_steps_and_its_place(library, monkeypatch, tmp_path):
    path = library("GoT - S08E06.mkv", entry())
    encode.enqueue([str(path)])

    def stopped(source, destination, *, work, quality, codec, on_progress=None):
        work.mkdir(parents=True, exist_ok=True)
        (work / "video.dv.hevc").write_bytes(b"an hour of encoding")
        raise shrink.ShrinkInterrupted("mkvmerge was stopped")

    monkeypatch.setattr(shrink, "shrink", stopped)

    result = encode.encode_next()

    assert result["interrupted"] is True
    assert (tmp_path / "work" / path.stem / "video.dv.hevc").exists()
    assert encode._state()["failed"] == {}
    assert [row["path"] for row in encode.queue()] == [str(path)]
    assert path.read_bytes() == b"x" * 1000


def test_the_page_lists_queues_and_unqueues(library):
    from fastapi.testclient import TestClient

    from bankai.web.app import create_app

    path = library("GoT - S08E06.mkv", entry(mbps=55.0))
    with TestClient(create_app()) as client:
        body = client.get("/api/encode").json()
        listed = body["scopes"]["shows"]["candidates"]
        assert [(row["codec"], row["queued"]) for row in listed] == [("hevc", None)]
        assert client.post("/api/encode/queue", json={"paths": [str(path)]}).json()["added"] == 1
        assert client.get("/api/encode").json()["queue"][0]["path"] == str(path)
        assert client.post("/api/encode/queue/remove", json={"all": True}).json() == {"removed": 1}
        assert client.post("/api/encode/queue", json={"paths": "everything"}).status_code == 422


def test_the_dashboard_shows_the_encoding_lane():
    from tests.test_dashboard import _build, _lane

    board = _build(
        encoding={
            "current": {"path": "/m/GoT - S08E06.mkv", "step": "encode", "fraction": 0.42, "started": 1_799_999_000.0, "eta": 1800},
            "queued": 3,
        }
    )
    lane = _lane(board, "encoding")
    assert lane["busy"] == 1 and lane["note"] == "3 more queued"
    task = lane["workers"][0]["task"]
    assert (task["title"], task["percent"]) == ("GoT - S08E06.mkv", 42.0)
    assert "30 min left" in task["detail"]
