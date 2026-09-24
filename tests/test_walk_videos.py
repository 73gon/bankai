"""The shared library walk: videos only, stat'ed once, nothing else touched."""

from __future__ import annotations

from bankai.web.anime_library import walk_videos


def test_only_video_files_come_back(tmp_path):
    show = tmp_path / "Frieren" / "Season 01"
    show.mkdir(parents=True)
    (show / "Frieren - S01E01.mkv").write_bytes(b"x" * 7)
    (show / "Frieren - S01E01.ass").write_text("subs")
    (show / "poster.JPG").write_bytes(b"")
    (tmp_path / "Loose Film (2020).MP4").write_bytes(b"x" * 3)

    found = {path.name: stat.st_size for path, stat in walk_videos(tmp_path)}

    # Nested and bare files both, any case of extension, sizes from the one stat.
    assert found == {"Frieren - S01E01.mkv": 7, "Loose Film (2020).MP4": 3}


def test_a_missing_root_yields_nothing(tmp_path):
    assert list(walk_videos(tmp_path / "not-mounted")) == []
