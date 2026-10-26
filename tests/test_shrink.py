"""Shrinking a video: the picture encoded again, everything else kept, the result checked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bankai.processor import shrink


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    monkeypatch.setattr(shrink, "_tool", lambda name: name)


def got(tmp_path: Path, **changes) -> shrink.Source:
    """Game of Thrones S08E03 as ffprobe reads it: UHD, HDR10, Dolby Vision profile 7."""
    path = tmp_path / "Game of Thrones - S08E03.mkv"
    path.write_bytes(b"x")
    streams = [
        {"codec_type": "video", "codec_name": "hevc"},
        {"codec_type": "audio", "codec_name": "ac3"},
        {"codec_type": "audio", "codec_name": "truehd"},
        {"codec_type": "audio", "codec_name": "ac3"},
        {"codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
    ]
    values = {
        "path": path,
        "duration": 4880.722,
        "frame_rate": "24000/1001",
        "width": 3840,
        "height": 2160,
        "bit_depth": 10,
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
        "language": "eng",
        "dv_profile": 7,
        "mastering": {
            "red_x": 0.708, "red_y": 0.292, "green_x": 0.17, "green_y": 0.797, "blue_x": 0.131, "blue_y": 0.046,
            "white_point_x": 0.3127, "white_point_y": 0.329, "min_luminance": 0.0001, "max_luminance": 1000.0,
        },
        "size": 34_572_357_657,
        "streams": streams,
    }
    return shrink.Source(**{**values, **changes})


def test_the_picture_is_encoded_alone_on_the_gpu_with_its_colours(tmp_path):
    command = shrink.encode_command(got(tmp_path), tmp_path / "video.hevc", quality=22)

    joined = " ".join(command)
    assert "-c:v hevc_nvenc" in joined and "-cq 22" in joined
    # One frame out for each frame in: Dolby Vision's metadata goes back per frame.
    assert "-map 0:v:0 -fps_mode passthrough" in joined
    assert "-profile:v main10" in joined
    assert "-maxrate 40M" in joined
    assert "-color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc -color_range tv" in joined
    assert command[-3:] == ["-f", "hevc", str(tmp_path / "video.hevc")]


def test_the_tracks_are_put_together_with_hdr10_on_the_picture(tmp_path):
    source = got(tmp_path)
    command = shrink.mux_command(source, tmp_path / "video.dv.hevc", tmp_path / "out.mkv", drop_audio=[2])

    joined = " ".join(command)
    assert "--default-duration 0:24000/1001p" in joined
    assert "--colour-transfer-characteristics 0:16" in joined and "--colour-primaries 0:9" in joined
    assert "--chromaticity-coordinates 0:0.708,0.292,0.17,0.797,0.131,0.046" in joined
    assert "--max-luminance 0:1000 --min-luminance 0:0.0001" in joined
    # Unknown light levels (0) are not written as zero.
    assert "--max-content-light" not in joined
    assert command[-4:] == ["--no-video", "--audio-tracks", "!2", str(source.path)]


def test_what_ffprobe_says_is_read(tmp_path, monkeypatch):
    path = tmp_path / "video.mkv"
    answers = [
        {
            "format": {"duration": "4880.722", "size": "34572357657"},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le", "r_frame_rate": "24000/1001",
                    "color_primaries": "bt2020", "color_transfer": "smpte2084", "color_space": "bt2020nc",
                    "width": 3840, "height": 2160, "tags": {"language": "eng"},
                    "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": 7}],
                },
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"codec_type": "audio", "codec_name": "truehd"},
            ],
        },
        {
            "frames": [
                {
                    "side_data_list": [
                        {
                            "side_data_type": "Mastering display metadata",
                            "red_x": "35400/50000", "red_y": "14600/50000", "green_x": "8500/50000", "green_y": "39850/50000",
                            "blue_x": "6550/50000", "blue_y": "2300/50000", "white_point_x": "15635/50000",
                            "white_point_y": "16450/50000", "min_luminance": "1/10000", "max_luminance": "10000000/10000",
                        },
                        {"side_data_type": "Content light level metadata", "max_content": 0, "max_average": 0},
                    ]
                }
            ]
        },
    ]
    monkeypatch.setattr(shrink, "_ffprobe", lambda _args: answers.pop(0))

    source = shrink.inspect(path)

    assert (source.dv_profile, source.bit_depth, source.hdr, source.language) == (7, 10, True, "eng")
    assert source.frames == 117_020
    assert source.mastering["red_x"] == pytest.approx(0.708)
    assert source.mastering["max_luminance"] == pytest.approx(1000)


def test_dolby_vision_profile_5_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(shrink, "inspect", lambda _path: got(tmp_path, dv_profile=5))

    with pytest.raises(shrink.ShrinkError, match="profile 5"):
        shrink.shrink(tmp_path / "in.mkv", tmp_path / "out.mkv", work=tmp_path / "work")


def run_steps(tmp_path, monkeypatch, *, encoded: dict | None = None):
    """shrink() with each tool faked; returns which steps ran."""
    source = got(tmp_path)
    ran: list[str] = []
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    if encoded is not None:
        (work / "video.hevc").write_bytes(b"encoded")
        (work / "encode.json").write_text(json.dumps(encoded), encoding="utf-8")

    def encode(_command, *, total_frames, on_progress=None):
        ran.append("encode")
        (work / "video.hevc").write_bytes(b"encoded")
        return total_frames

    def extract(_source, rpu):
        ran.append("rpu")
        rpu.write_bytes(b"rpu")

    def inject(_video, _rpu, output):
        ran.append("inject")
        output.write_bytes(b"dv")

    class Done:
        returncode = 0
        stdout = stderr = ""

    def mux(command, **_kwargs):
        ran.append("mux")
        Path(command[3]).write_bytes(b"muxed")
        return Done()

    monkeypatch.setattr(shrink, "inspect", lambda _path: source)
    monkeypatch.setattr(shrink, "_run_encode", encode)
    monkeypatch.setattr(shrink, "_extract_rpu", extract)
    monkeypatch.setattr(shrink, "_inject_rpu", inject)
    monkeypatch.setattr(shrink.subprocess, "run", mux)
    monkeypatch.setattr(shrink, "verify", lambda _source, _out, **_kw: got(tmp_path, dv_profile=8))
    result = shrink.shrink(source.path, tmp_path / "out" / "S08E03.mkv", work=work)
    return ran, result, source


def test_every_step_runs_and_the_result_takes_the_destination(tmp_path, monkeypatch):
    ran, result, _source = run_steps(tmp_path, monkeypatch)

    assert ran == ["encode", "rpu", "inject", "mux"]
    assert (tmp_path / "out" / "S08E03.mkv").read_bytes() == b"muxed"
    assert result["dolby_vision"] == 8
    assert not list((tmp_path / "out").glob(".*.part"))


def test_an_encode_already_done_with_the_same_settings_is_not_done_again(tmp_path, monkeypatch):
    source = got(tmp_path)
    settings = shrink._settings(source, shrink.DEFAULT_QUALITY)

    ran, _result, _source = run_steps(tmp_path, monkeypatch, encoded={"settings": settings, "frames": source.frames})

    assert ran == ["rpu", "inject", "mux"]


def test_an_encode_with_other_settings_is_done_again(tmp_path, monkeypatch):
    source = got(tmp_path)
    settings = {**shrink._settings(source, shrink.DEFAULT_QUALITY), "quality": 26}

    ran, _result, _source = run_steps(tmp_path, monkeypatch, encoded={"settings": settings, "frames": source.frames})

    assert ran[0] == "encode"


def test_an_encode_missing_frames_is_refused(tmp_path, monkeypatch):
    source = got(tmp_path)
    settings = shrink._settings(source, shrink.DEFAULT_QUALITY)

    with pytest.raises(shrink.ShrinkError, match="frames"):
        run_steps(tmp_path, monkeypatch, encoded={"settings": settings, "frames": source.frames - 100})


def test_dolby_vision_can_be_left_out_and_added_later_from_the_same_encode(tmp_path, monkeypatch):
    source = got(tmp_path)
    settings = shrink._settings(source, shrink.DEFAULT_QUALITY)
    ran: list[str] = []
    monkeypatch.setattr(shrink, "inspect", lambda _path: source)
    monkeypatch.setattr(shrink, "_extract_rpu", lambda *_a: ran.append("rpu"))
    monkeypatch.setattr(shrink, "_inject_rpu", lambda *_a: ran.append("inject"))
    work = tmp_path / "work"
    work.mkdir()
    (work / "video.hevc").write_bytes(b"encoded")
    (work / "encode.json").write_text(json.dumps({"settings": settings, "frames": source.frames}), encoding="utf-8")

    class Done:
        returncode = 0
        stdout = stderr = ""

    def mux(command, **_kwargs):
        output = Path(command[command.index("-o") + 1])
        ran.append("mux")
        output.write_bytes(b"muxed")
        return Done()

    checked: list[bool] = []
    monkeypatch.setattr(shrink.subprocess, "run", mux)
    monkeypatch.setattr(shrink, "verify", lambda _s, _o, **kw: checked.append(kw["dolby_vision"]) or got(tmp_path, dv_profile=None))

    shrink.shrink(source.path, tmp_path / "out.mkv", work=work, dolby_vision=False)

    assert not any(step in ran for step in ("rpu", "inject"))
    assert checked == [False]


def test_an_h264_source_stays_h264(tmp_path):
    source = got(tmp_path, bit_depth=8, color_primaries="bt709", color_transfer="bt709", color_space="bt709", dv_profile=None)

    joined = " ".join(shrink.encode_command(source, tmp_path / "video.h264", quality=19, codec="h264"))

    assert "-c:v h264_nvenc" in joined and "-profile:v high" in joined and "-cq 19" in joined
    assert "main10" not in joined and "-color_range" not in joined
    assert joined.endswith(f"-f h264 {tmp_path / 'video.h264'}")
