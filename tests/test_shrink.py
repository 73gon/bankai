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


@pytest.mark.parametrize(
    "tags",
    [
        {"NUMBER_OF_FRAMES": "88714", "DURATION": "01:01:40.113000000"},
        {"NUMBER_OF_FRAMES-eng": "88714"},
        {"DURATION-eng": "01:01:40.113000000"},
    ],
)
def test_the_picture_s_own_frames_count_not_the_container_s_length(tmp_path, monkeypatch, tags):
    # Game of Thrones S01E01: its AC3 tracks start 2.002 s in, the container runs 48 frames past the picture.
    answers = [
        {
            "format": {"duration": "3702.098000", "size": "26980337550"},
            "streams": [
                {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv420p10le", "r_frame_rate": "24000/1001", "tags": tags},
                {"codec_type": "audio", "codec_name": "ac3", "start_time": "2.002000"},
            ],
        },
        {"frames": []},
    ]
    monkeypatch.setattr(shrink, "_ffprobe", lambda _args: answers.pop(0))

    assert shrink.inspect(tmp_path / "video.mkv").frames == 88_714


def test_dolby_vision_profile_5_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(shrink, "inspect", lambda _path: got(tmp_path, dv_profile=5))

    with pytest.raises(shrink.ShrinkError, match="profile 5"):
        shrink.shrink(tmp_path / "in.mkv", tmp_path / "out.mkv", work=tmp_path / "work")


def run_steps(tmp_path, monkeypatch, *, encoded: dict | None = None, **changes):
    """shrink() with each tool faked; returns which steps ran."""
    source = got(tmp_path, **changes)
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
    source = got(tmp_path, frame_count=117_020)
    settings = shrink._settings(source, shrink.DEFAULT_QUALITY)

    with pytest.raises(shrink.ShrinkError, match="frames"):
        run_steps(tmp_path, monkeypatch, encoded={"settings": settings, "frames": 117_020 - 100}, frame_count=117_020)


@pytest.mark.parametrize(("counted", "refused"), [(88_714, False), (88_762, True)])
def test_a_length_that_disagrees_with_the_encode_is_settled_by_counting(tmp_path, monkeypatch, counted, refused):
    # No count in the file, and the container runs 48 frames longer than the encode.
    counts: list[Path] = []
    monkeypatch.setattr(shrink, "count_frames", lambda path: counts.append(path) or counted)
    # Nor the picture's own length: its frame times are read, and given to it.
    monkeypatch.setattr(shrink, "frame_times", lambda _path: grid(counted))
    source = got(tmp_path, duration=3702.098)
    settings = shrink._settings(source, shrink.DEFAULT_QUALITY)
    encoded = {"settings": settings, "frames": 88_714}

    if refused:
        with pytest.raises(shrink.ShrinkError, match="88714 frames of 88762"):
            run_steps(tmp_path, monkeypatch, encoded=encoded, duration=3702.098)
    else:
        ran, _result, _source = run_steps(tmp_path, monkeypatch, encoded=encoded, duration=3702.098)
        assert ran == ["rpu", "inject", "mux"]
        assert (tmp_path / "work" / "frames.txt").is_file()
    assert len(counts) == 1


def naruto(tmp_path: Path, **changes) -> dict:
    """Naruto Shippuden S12E329 as ffprobe reads it: a stream download, two jumps of 2 s in its frame times."""
    return {
        "dv_profile": None, "duration": 1389.597, "video_duration": 1389.597, "frame_count": 33221,
        "frame_rate": "27187/1134", **changes,
    }


def grid(count: int, *, jump_at: int | None = None, jump: float = 0.0) -> list[float]:
    step = 1001 / 24000
    return [index * step + (jump if jump_at is not None and index >= jump_at else 0.0) for index in range(count)]


def test_uneven_frames_keep_their_own_times(tmp_path, monkeypatch):
    times = grid(33221, jump_at=12_000, jump=3.9)
    monkeypatch.setattr(shrink, "frame_times", lambda _path: times)
    seen: dict = {}
    real = shrink.mux_command
    monkeypatch.setattr(shrink, "mux_command", lambda *args, **kwargs: seen.update(kwargs) or real(*args, **kwargs))

    ran, _result, _source = run_steps(tmp_path, monkeypatch, **naruto(tmp_path))

    assert ran == ["encode", "mux"]
    written = (tmp_path / "work" / "frames.txt").read_text(encoding="utf-8").splitlines()
    assert written[0] == "# timestamp format v2"
    assert len(written) == 1 + 33221
    # Line 0 is the header: frame k is line k + 1. The jump stays where it was.
    assert written[12_000 : 12_002] == [f"{times[11_999] * 1000:.3f}", f"{times[12_000] * 1000:.3f}"]
    assert float(written[12_001]) - float(written[12_000]) > 3900
    command = " ".join(real(_source, tmp_path / "video.hevc", tmp_path / "out.mkv", timestamps=seen["timestamps"]))
    assert f"--timestamps 0:{tmp_path / 'work' / 'frames.txt'}" in command
    assert "--default-duration" not in command


def test_even_frames_keep_the_even_grid(tmp_path, monkeypatch):
    monkeypatch.setattr(shrink, "frame_times", lambda _path: pytest.fail("frame times read for an even picture"))
    seen: dict = {}
    real = shrink.mux_command
    monkeypatch.setattr(shrink, "mux_command", lambda *args, **kwargs: seen.update(kwargs) or real(*args, **kwargs))

    run_steps(tmp_path, monkeypatch, dv_profile=None)

    assert seen["timestamps"] is None
    assert not (tmp_path / "work" / "frames.txt").exists()


def test_frame_times_that_do_not_match_the_encode_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(shrink, "frame_times", lambda _path: grid(33220))

    with pytest.raises(shrink.ShrinkError, match="33221 frames, the source's picture has 33220 frame times"):
        run_steps(tmp_path, monkeypatch, **naruto(tmp_path))
    assert not (tmp_path / "work" / "frames.txt").exists()


def test_frame_times_are_read_in_the_order_shown(tmp_path, monkeypatch):
    class Done:
        returncode = 0
        stdout = "0.000000\n0.083000\n0.042000\nN/A\n0.125000,\n"
        stderr = ""

    monkeypatch.setattr(shrink.subprocess, "run", lambda *_args, **_kwargs: Done())

    assert shrink.frame_times(tmp_path / "video.mkv") == [0.0, 0.042, 0.083, 0.125]


@pytest.mark.parametrize(("picture", "refused"), [(1387.771, True), (1389.597, False), (1389.640, False)])
def test_a_picture_shorter_than_the_source_s_is_refused(tmp_path, monkeypatch, picture, refused):
    # S12E326 encoded on an even grid: the audio still ran to 1389.6 s, the picture stopped 1.8 s short.
    source = got(tmp_path, **naruto(tmp_path))
    monkeypatch.setattr(shrink, "inspect", lambda _path: got(tmp_path, **naruto(tmp_path, video_duration=picture)))

    if refused:
        with pytest.raises(shrink.ShrinkError, match=r"new picture runs 1387\.77s, the source's 1389\.60s"):
            shrink.verify(source, tmp_path / "out.mkv")
    else:
        assert shrink.verify(source, tmp_path / "out.mkv").video_duration == picture


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


def test_a_trial_that_predicts_too_little_saving_stops_before_the_encode(tmp_path, monkeypatch):
    """Bleach's compact episodes came out larger; 293 encodes were thrown away whole."""
    from bankai.processor import shrink

    source = shrink.Source(path=tmp_path / "a.mkv", duration=1440.0, frame_rate="24000/1001", size=900 * 2**20)
    sizes = iter([150, 100] * 3)  # each new stretch half again as large as the source's
    monkeypatch.setattr(shrink, "_sample_bytes", lambda command, output: next(sizes))
    monkeypatch.setattr(shrink, "_tool", lambda name: name)
    predicted = shrink.trial(source, tmp_path / "work", quality=20, codec="hevc")
    assert predicted is not None and predicted < 0
    short = shrink.Source(path=tmp_path / "b.mkv", duration=60.0, frame_rate="24", size=10)
    assert shrink.trial(short, tmp_path / "work", quality=20, codec="hevc") is None


@pytest.mark.parametrize("returncode", [-15, -9, 255])
def test_a_program_stopped_by_a_service_stop_is_an_interruption_not_a_failure(returncode):
    # ffmpeg catches SIGTERM and exits with 255; a program killed outright has a negative code.
    with pytest.raises(shrink.ShrinkInterrupted):
        shrink._stopped(returncode, "the encode")


def test_a_program_that_failed_on_its_own_is_no_interruption():
    shrink._stopped(1, "the encode")
