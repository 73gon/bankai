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


def test_an_unfinished_download_counts_what_its_files_take_not_what_arrived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over the Windows drive mount an unfinished file is full size from the start."""
    from types import SimpleNamespace

    from bankai.web import erai

    downloads = tmp_path / "qbit"
    batch = downloads / "[Erai-raws] Spy x Family - 01 ~ 12"
    batch.mkdir(parents=True)
    (batch / "01.mkv").write_bytes(b"x" * 800)
    (batch / "02.mkv").write_bytes(b"x" * 800)
    torrents = [
        SimpleNamespace(hash="a" * 40, content_path=str(batch), size_bytes=1600, progress=0.01),
        # Still fetching its metadata: it names the download folder itself.
        SimpleNamespace(hash="b" * 40, content_path=str(downloads), size_bytes=0, progress=0.0),
    ]

    class Client:
        async def login(self):
            pass

        async def list_torrents(self, **_kwargs):
            return torrents

        async def aclose(self):
            pass

    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", Client)
    monkeypatch.setattr(erai, "_load_state", lambda: {"releases": {}})
    monkeypatch.setattr(storage, "get_settings", lambda: Settings(paths={"downloads_dir": downloads}))

    groups = {group["label"]: group for group in storage._torrents()["groups"]}

    assert groups["Still downloading"] == {"label": "Still downloading", "bytes": 1600, "torrents": 2, "downloaded": 16}
