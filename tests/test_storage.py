from __future__ import annotations

import os
from pathlib import Path

import pytest

from bankai.config import Settings
from bankai.web import storage


def test_disk_usage_counts_a_linked_episode_once_and_breaks_areas_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anime = tmp_path / "media" / "shows_anime" / "Frieren" / "Season 01"
    downloads = tmp_path / "qbit"
    staging = tmp_path / "staging"
    for folder in (anime, downloads, staging, tmp_path / "media" / "movies", tmp_path / "media" / "shows"):
        folder.mkdir(parents=True)
    torrent_file = downloads / "[Erai-raws] Frieren - 01.mkv"
    torrent_file.write_bytes(b"v" * 1000)
    # Published: the same bytes under the library's name.
    os.link(torrent_file, anime / "Frieren - S01E01.mkv")
    (anime / "Frieren - S01E01.nfo").write_bytes(b"n" * 10)
    (anime.parent / "poster.jpg").write_bytes(b"p" * 50)
    (downloads / "[Erai-raws] Frieren - 02.mkv.!qb").write_bytes(b"d" * 300)

    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: Settings(
            transfer={
                "anime_shows_dir": tmp_path / "media" / "shows_anime",
                "movies_dir": tmp_path / "media" / "movies",
                "shows_dir": tmp_path / "media" / "shows",
            },
            output={"directory": staging},
            paths={"downloads_dir": downloads, "work_dir": tmp_path / "missing"},
        ),
    )
    monkeypatch.setattr(storage, "_torrents", lambda: None)

    result = storage.scan()
    areas = {area["key"]: area for area in result["areas"]}

    assert areas["anime"]["bytes"] == 1060
    parts = {part["label"]: part["bytes"] for part in areas["anime"]["parts"]}
    assert parts == {"Video": 1000, "Posters & artwork": 50, "Metadata (NFO)": 10}
    # The torrent's file is the library's episode: counted there, shared here.
    assert areas["downloads"]["bytes"] == 300
    assert areas["downloads"]["shared_bytes"] == 1000
    assert {part["label"] for part in areas["downloads"]["parts"]} == {"Unfinished copies"}
    assert "work" not in areas
    volume = next(item for item in result["volumes"] if item["path"] == areas["anime"]["volume"])
    assert volume["total"] >= volume["used"] >= 0
