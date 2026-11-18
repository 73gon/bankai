"""A .torrent dropped where a magnet link goes: kept, answered with a magnet, added as the file."""

from __future__ import annotations

import asyncio
import base64

import pytest

from bankai.torrent import uploads


def _bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(map(_bencode, value)) + b"e"
    return b"d" + b"".join(_bencode(key) + _bencode(value[key]) for key in sorted(value)) + b"e"


def _torrent(private: bool = True) -> bytes:
    info = {
        "name": "[Group] Show S01 (1080p)",
        "piece length": 262144,
        "pieces": b"x" * 20,
        "files": [{"path": ["Show - 01.mkv"], "length": 100}, {"path": ["Show - 02.mkv"], "length": 200}],
        "private": 1 if private else 0,
    }
    return _bencode({"announce": "https://tracker.animez.to/announce/PASSKEY", "info": info})


@pytest.fixture
def kept(monkeypatch, tmp_path):
    monkeypatch.setattr(uploads, "_directory", lambda: tmp_path / "uploaded")
    return tmp_path / "uploaded"


def test_a_dropped_torrent_is_kept_and_answered_with_its_magnet(kept) -> None:
    saved = uploads.save(_torrent())
    assert saved["private"] and saved["files"] == 2 and saved["size"] == 300
    assert saved["magnet"].startswith(f"magnet:?xt=urn:btih:{saved['info_hash']}&dn=")
    assert "PASSKEY" not in saved["magnet"]
    assert (kept / f"{saved['info_hash']}.torrent").stat().st_mode & 0o077 == 0
    meta = uploads.metadata(saved["info_hash"])
    assert [row["path"] for row in meta["info"]["files"]] == [
        "[Group] Show S01 (1080p)/Show - 01.mkv", "[Group] Show S01 (1080p)/Show - 02.mkv",
    ]


def test_what_is_no_torrent_is_refused(kept) -> None:
    with pytest.raises(uploads.TorrentFileError):
        uploads.save(b"<html>login</html>")
    with pytest.raises(uploads.TorrentFileError):
        uploads.save(_bencode({"announce": "x"}))


def test_qbittorrent_is_handed_the_file_for_its_magnet(kept, monkeypatch) -> None:
    from bankai.torrent.qbittorrent import QBittorrentClient

    saved = uploads.save(_torrent())
    sent: dict = {}

    class Response:
        status_code = 200
        text = "Ok."

        def raise_for_status(self):
            pass

    async def post(path, data=None, files=None):
        sent.update(path=path, data=data, files=files)
        return Response()

    client = QBittorrentClient.__new__(QBittorrentClient)
    client._settings = type("S", (), {"category": "bankai"})()
    client._ensure_login = lambda: asyncio.sleep(0)
    client._client = type("C", (), {"post": staticmethod(post)})()
    asyncio.run(client.add(magnet=saved["magnet"]))
    assert "urls" not in (sent["data"] or {})
    assert sent["files"], "the kept file goes to qBittorrent, not the magnet"


def test_the_upload_endpoint(kept) -> None:
    from fastapi.testclient import TestClient

    from bankai.web.app import create_app

    with TestClient(create_app()) as client:
        ok = client.post("/api/torrents/upload", json={"data": base64.b64encode(_torrent()).decode()})
        bad = client.post("/api/torrents/upload", json={"data": base64.b64encode(b"nope").decode()})
    assert ok.status_code == 200 and ok.json()["magnet"].startswith("magnet:?xt=urn:btih:")
    assert bad.status_code == 422


def test_a_show_s_missing_episodes_are_filled_from_the_torrent() -> None:
    from bankai.web import magnet_preview

    files = [
        {"path": "Pack/Show.S01E01.1080p.mkv", "size": 1},
        {"path": "Pack/Show.S01E02.1080p.mkv", "size": 1},
        {"path": "Pack/Show.S01E03.1080p.mkv", "size": 1},
    ]
    rows = magnet_preview.plan(
        files, ["/lib/Show/Season 01/Show - S01E01.mkv"], "show",
        [{"season_number": 1, "episode": 3}],
    )
    by_name = {row["name"]: row for row in rows}
    assert by_name["Show.S01E01.1080p.mkv"]["replaces"] == "Show - S01E01.mkv"
    assert by_name["Show.S01E03.1080p.mkv"]["replaces"] == "S01E03" and by_name["Show.S01E03.1080p.mkv"]["fills"]
    assert by_name["Show.S01E02.1080p.mkv"]["replaces"] is None
