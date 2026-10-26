"""Tracks switched off taken out of the libraries' videos, one file at a time, losslessly."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from bankai.config import reset_settings_cache
from bankai.web import track_cleanup as tracks


def test_a_language_is_one_code_however_it_is_written():
    assert [tracks.language(code) for code in ("de", "deu", "ger", "de-DE", "GER")] == ["ger"] * 5
    assert tracks.language("es-419") == "spa"
    assert [tracks.language(code) for code in (None, "", "xx", "1234")] == ["und"] * 4


def test_a_root_listed_for_two_libraries_belongs_to_the_first(monkeypatch, tmp_path):
    monkeypatch.setenv("BANKAI_TRANSFER__ANIME_SHOWS_DIR", "/m/anime")
    monkeypatch.setenv("BANKAI_TRANSFER__SHOWS_DIR", "/m/shows")
    monkeypatch.setenv("BANKAI_TRANSFER__MOVIES_DIR", "/m/movies")
    monkeypatch.setenv("BANKAI_WEB__SERVER_ANIME_DIRS", '["/m/anime", "/e/shows"]')
    monkeypatch.setenv("BANKAI_WEB__SERVER_SHOW_DIRS", '["/m/shows", "/e/shows"]')
    monkeypatch.setenv("BANKAI_WEB__SERVER_MOVIE_DIRS", '["/m/movies"]')
    reset_settings_cache()

    assert tracks.roots("anime") == ["/m/anime", "/e/shows"]
    assert tracks.roots("shows") == ["/m/shows"]
    assert tracks.roots("movies") == ["/m/movies"]


def track(kind: str, lang: str, size: int | None = 1000, default: bool = False) -> dict:
    return {"kind": kind, "lang": lang, "name": None, "codec": "x", "default": default, "forced": False, "bytes": size}


RULE = {"enabled": True, "audio": ["spa"], "subtitles": ["ara", "por"]}


def found(*kept: dict, video: dict | None = None) -> dict:
    """What inspect_file answers: the tracks, the video, the length."""
    return {"tracks": list(kept), "video": video, "duration": None, "bit_rate": None}


def test_what_a_rule_takes_out_of_a_file():
    entry = {"tracks": [track("audio", "eng"), track("audio", "spa", 5000), track("subtitles", "ara"), track("subtitles", "ger")]}

    assert tracks.plan("/m/a.mkv", entry, RULE) == {"audio": 1, "subtitles": 1, "convert": False, "bytes": 6000}


def test_a_file_never_loses_its_last_audio_track():
    entry = {"tracks": [track("audio", "spa"), track("subtitles", "ger")]}

    assert tracks.plan("/m/a.mkv", entry, RULE) is None


def test_a_file_in_another_container_is_put_into_mkv_even_with_nothing_to_take_out():
    entry = {"tracks": [track("audio", "eng")]}

    assert tracks.plan("/m/Mr. Bean's Holiday (2007).mp4", entry, RULE) == {"audio": 0, "subtitles": 0, "convert": True, "bytes": 0}


def test_german_and_english_are_always_kept_and_japanese_in_anime():
    anime = tracks.set_rules("anime", audio=["ger", "jpn", "spa"], subtitles=["eng", "deu", "ara"])
    movies = tracks.set_rules("movies", audio=["jpn", "en"], subtitles=["de"])

    assert (anime["audio"], anime["subtitles"]) == (["spa"], ["ara"])
    assert (movies["audio"], movies["subtitles"]) == (["jpn"], [])
    # Rules written before the protection are read without them too.
    tracks._write("track_rules.json", {"shows": {"enabled": True, "audio": ["ger", "fre"], "subtitles": ["eng"]}})
    assert tracks.rules()["shows"] == {"enabled": True, "audio": ["fre"], "subtitles": []}


def test_a_code_that_is_no_language_is_left_out_of_the_rules():
    rule = tracks.set_rules("anime", audio=["spa", "xx", "und"], subtitles=["pt-BR"])

    assert rule["audio"] == ["spa", "und"]
    assert rule["subtitles"] == ["por"]
    assert tracks.rules()["anime"]["enabled"] is False


@pytest.fixture()
def library(monkeypatch, tmp_path):
    """Three libraries of one root each, listed as the held tree would list them."""
    roots = {scope: tmp_path / scope for scope in tracks.SCOPES}
    for root in roots.values():
        root.mkdir()
    monkeypatch.setattr(tracks, "roots", lambda scope: [str(roots[scope])])

    def files(asked):
        rows = []
        for root in asked:
            for path in sorted(Path(root).rglob("*")):
                if path.suffix in {".mkv", ".mp4"} and not path.name.startswith("."):
                    stat = path.stat()
                    rows.append({"path": str(path), "size": stat.st_size, "mtime": stat.st_mtime})
        return rows

    from bankai.web import library_walk

    monkeypatch.setattr(library_walk, "files", files)
    return roots


def video(root: Path, name: str, data: bytes = b"video", *, age: float = 7200.0) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def test_the_tracks_of_every_file_are_read_a_slice_at_a_time(library, monkeypatch):
    for number in range(3):
        video(library["anime"], f"Show/Season 01/Show - S01E0{number + 1}.mkv")
    video(library["movies"], "Film (2000)/Film (2000).mkv")
    probed: list[str] = []
    monkeypatch.setattr(tracks, "inspect_file", lambda path: probed.append(path.name) or found(track("audio", "jpn"), track("subtitles", "ara")))

    first = tracks.read_tracks(limit=3)
    second = tracks.read_tracks(limit=3)
    third = tracks.read_tracks(limit=3)

    assert (first["read"], first["remaining"], second["read"], third["read"]) == (3, 1, 1, 0)
    inventory = tracks.load_inventory()
    assert {entry["scope"] for entry in inventory.values()} == {"anime", "movies"}
    langs = tracks.languages("anime")
    assert [(row["code"], row["files"]) for row in langs["subtitles"]] == [("ara", 3)]


def test_a_changed_file_is_read_again_and_a_removed_one_forgotten(library, monkeypatch):
    first = video(library["shows"], "A/Season 01/A - S01E01.mkv")
    second = video(library["shows"], "A/Season 01/A - S01E02.mkv")
    monkeypatch.setattr(tracks, "inspect_file", lambda path: found(track("audio", "eng")))
    tracks.read_tracks()

    first.write_bytes(b"longer video")
    second.unlink()
    result = tracks.read_tracks()

    assert result["read"] == 1
    assert list(tracks.load_inventory()) == [str(first)]


class Mkvmerge:
    """mkvmerge -J answers by path, and a mux that writes the part file."""

    def __init__(self, source_tracks: list[dict], written_tracks: list[dict] | None = None):
        self.source = {"tracks": source_tracks, "container": {"properties": {"duration": 3_000_000_000_000}}}
        self.written = {"tracks": written_tracks or [], "container": {"properties": {"duration": 3_000_000_000_000}}}
        self.commands: list[list[str]] = []
        self.during_mux = None

    def json(self, path: Path) -> dict:
        return self.written if path.name.endswith(tracks._PART) else self.source

    def run(self, command, **_kwargs):
        self.commands.append(command)
        Path(command[command.index("-o") + 1]).write_bytes(b"clean")
        if self.during_mux:
            self.during_mux()

        class Done:
            returncode = 0
            stdout = stderr = ""

        return Done()


def mk(track_id: int, kind: str, lang: str, default: bool = False) -> dict:
    return {"id": track_id, "type": kind, "properties": {"language": lang, "default_track": default}}


@pytest.fixture()
def mkvmerge(monkeypatch):
    def arrange(source_tracks, written_tracks=None):
        fake = Mkvmerge(source_tracks, written_tracks)
        monkeypatch.setattr(tracks, "_mkvmerge_json", fake.json)
        monkeypatch.setattr(tracks.subprocess, "run", fake.run)
        monkeypatch.setattr(tracks, "probe", lambda path: [track("audio", "eng")])
        monkeypatch.setattr(tracks, "inspect_file", lambda path: found(track("audio", "eng")))
        return fake

    return arrange


def test_a_file_is_written_anew_without_the_tracks_switched_off(library, mkvmerge):
    path = video(library["anime"], "Show/Season 01/Show - S01E01.mkv", b"original with spanish")
    before = path.stat()
    tracks.set_rules("anime", enabled=True, audio=["spa"], subtitles=["ara"])
    fake = mkvmerge(
        [mk(0, "video", "jpn"), mk(1, "audio", "jpn", True), mk(2, "audio", "spa"), mk(3, "subtitles", "ger"), mk(4, "subtitles", "ara")],
        [mk(0, "video", "jpn"), mk(1, "audio", "jpn", True), mk(2, "subtitles", "ger")],
    )

    result = tracks.clean_file(path, "anime")

    assert path.read_bytes() == b"clean"
    command = fake.commands[0]
    assert command[command.index("--audio-tracks") + 1] == "1"
    assert command[command.index("--subtitle-tracks") + 1] == "3"
    assert "--default-track-flag" not in command
    # Where it was among the recently added, a second on: Jellyfin reads its tracks again.
    assert path.stat().st_mtime_ns == before.st_mtime_ns + 1_000_000_000
    assert (result["audio"], result["subtitles"]) == (1, 1)
    assert not list(path.parent.glob(".*"))


def test_the_default_audio_taken_out_passes_to_the_first_one_left(library, mkvmerge):
    path = video(library["shows"], "A/A - S01E01.mkv")
    tracks.set_rules("shows", enabled=True, audio=["fre"])
    fake = mkvmerge(
        [mk(0, "video", "fre"), mk(1, "audio", "fre", True), mk(2, "audio", "ger")],
        [mk(0, "video", "fre"), mk(1, "audio", "ger")],
    )

    tracks.clean_file(path, "shows")

    command = fake.commands[0]
    assert command[command.index("--default-track-flag") + 1] == "2:1"


def test_a_file_that_changed_while_it_was_written_is_left_as_it_is(library, mkvmerge):
    path = video(library["anime"], "Show/Show - S01E01.mkv", b"original")
    tracks.set_rules("anime", enabled=True, subtitles=["ara"])
    fake = mkvmerge(
        [mk(0, "video", "jpn"), mk(1, "audio", "jpn"), mk(2, "subtitles", "ara")],
        [mk(0, "video", "jpn"), mk(1, "audio", "jpn")],
    )
    fake.during_mux = lambda: path.write_bytes(b"an HEVC upgrade, published meanwhile")

    result = tracks.clean_file(path, "anime")

    assert "changed" in result["error"]
    assert path.read_bytes() == b"an HEVC upgrade, published meanwhile"
    assert not list(path.parent.glob(".*"))


def test_a_new_file_missing_a_track_is_thrown_away(library, mkvmerge):
    path = video(library["movies"], "F/F.mkv", b"original")
    tracks.set_rules("movies", enabled=True, subtitles=["ara"])
    mkvmerge(
        [mk(0, "video", "eng"), mk(1, "audio", "eng"), mk(2, "audio", "ger"), mk(3, "subtitles", "ara")],
        [mk(0, "video", "eng"), mk(1, "audio", "eng")],
    )

    result = tracks.clean_file(path, "movies")

    assert "tracks" in result["error"]
    assert path.read_bytes() == b"original"
    # Not tried again for a day, unless it changes.
    assert tracks.candidates("movies") == [] or str(path) not in {row[0] for row in tracks.candidates("movies")}


def test_an_mp4_is_put_into_mkv_and_the_mp4_removed(library, mkvmerge):
    path = video(library["movies"], "Mr. Bean's Holiday (2007)/Mr. Bean's Holiday (2007).mp4", b"mp4")
    tracks.set_rules("movies", enabled=True)
    fake = mkvmerge([mk(0, "video", "und"), mk(1, "audio", "eng")], [mk(0, "video", "und"), mk(1, "audio", "eng")])

    result = tracks.clean_file(path, "movies")

    target = path.with_suffix(".mkv")
    assert target.read_bytes() == b"clean" and not path.exists()
    assert result["converted"] is True
    assert fake.commands[0][-1] == str(path)
    assert str(path) not in tracks.load_inventory() and str(target) in tracks.load_inventory()


def test_an_mp4_with_a_track_mkvmerge_cannot_carry_is_left_alone(library, mkvmerge, monkeypatch):
    path = video(library["movies"], "F (2000)/F (2000).mp4", b"mp4")
    tracks.set_rules("movies", enabled=True)
    mkvmerge([mk(0, "video", "und"), mk(1, "audio", "eng")])
    monkeypatch.setattr(tracks, "probe", lambda _path: [track("audio", "eng"), track("subtitles", "eng")])

    result = tracks.clean_file(path, "movies")

    assert "cannot carry" in result["error"]
    assert path.read_bytes() == b"mp4"


def test_only_enabled_libraries_are_cleaned_and_seeding_or_new_files_wait(library, mkvmerge, monkeypatch):
    seeding = video(library["anime"], "S/S - S01E01.mkv")
    os.link(seeding, library["anime"] / "torrent copy.bin")
    fresh = video(library["anime"], "S/S - S01E02.mkv", age=60)
    other = video(library["shows"], "B/B - S01E01.mkv")
    monkeypatch.setattr(tracks, "inspect_file", lambda path: found(track("audio", "jpn"), track("subtitles", "ara")))
    tracks.read_tracks()
    tracks.set_rules("anime", enabled=True, subtitles=["ara"])
    tracks.set_rules("shows", enabled=False, subtitles=["ara"])

    assert tracks.clean_next() is None
    waiting = tracks.overview()["scopes"]["anime"]["waiting"]
    assert waiting == {"still seeding": 1, "changed in the last hour": 1}
    assert other.read_bytes() == b"video" and fresh.read_bytes() == b"video"


def test_a_copy_cut_off_by_a_restart_is_thrown_away(tmp_path):
    part = tmp_path / ".A.mkv.tracks.part"
    part.write_bytes(b"half")
    state = {"current": {"path": str(tmp_path / "A.mkv"), "part": str(part), "scope": "shows"}}
    tracks._save_state(state)

    tracks.recover()

    assert not part.exists()
    assert "current" not in json.loads(tracks._path("track_cleanup.json").read_text(encoding="utf-8"))


def test_the_page_answers_each_library_and_takes_rules(library, monkeypatch):
    from fastapi.testclient import TestClient

    from bankai.web.app import create_app

    video(library["anime"], "S/S - S01E01.mkv")
    monkeypatch.setattr(tracks, "inspect_file", lambda path: found(track("audio", "jpn"), track("subtitles", "ara", 4000)))
    tracks.read_tracks()
    with TestClient(create_app()) as client:
        body = client.get("/api/tracks").json()
        assert set(body["scopes"]) == {"anime", "shows", "movies"}
        assert body["scopes"]["anime"]["languages"]["subtitles"][0]["code"] == "ara"

        changed = client.post("/api/tracks/anime", json={"subtitles": ["ara"]}).json()
        assert changed["rules"]["subtitles"] == ["ara"]
        assert changed["preview"] == {"files": 1, "tracks": 1, "bytes": 4000, "convert": 0}
        assert client.post("/api/tracks/music", json={}).status_code == 404
        assert client.post("/api/tracks/anime", json={"audio": "spa"}).status_code == 422


def test_a_rewrite_stopped_by_a_restart_is_no_failure(library, mkvmerge, monkeypatch):
    path = video(library["movies"], "T2 (1991)/T2 (1991).mkv", b"original")
    tracks.set_rules("movies", enabled=True, subtitles=["ara"])
    mkvmerge([mk(0, "video", "eng"), mk(1, "audio", "eng"), mk(2, "subtitles", "ara")])

    class Stopped:
        returncode = -15
        stdout = stderr = ""

    def run(command, **_kwargs):
        Path(command[command.index("-o") + 1]).write_bytes(b"half")
        return Stopped()

    monkeypatch.setattr(tracks.subprocess, "run", run)

    result = tracks.clean_file(path, "movies")

    assert result["interrupted"] is True
    assert path.read_bytes() == b"original" and not list(path.parent.glob(".*"))
    assert tracks._state()["failed"] == {}
