""".torrent files added by hand, wherever a magnet link can be pasted.

AnimeZ (and any private tracker) gives a ``.torrent`` and no magnet, and a private
torrent's magnet finds nothing: no DHT, and the tracker wants the passkey only the
file carries. So a file dropped into a magnet field is kept here by its info hash
(``uploaded_torrents/<hash>.torrent``) and answered with a plain magnet of that
hash. Every path that takes a magnet goes on as it was; ``QBittorrentClient.add``
hands qBittorrent the kept file instead of a magnet whose hash is one of these,
and the magnet preview reads the file's own list instead of asking the swarm.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

MAX_BYTES = 20 * 1024 * 1024
KEEP_SECONDS = 90 * 24 * 3600


class TorrentFileError(ValueError):
    pass


def bdecode(data: bytes) -> Any:
    """A bencoded value: dicts with bytes keys, lists, ints, bytes."""

    def value(pos: int) -> tuple[Any, int]:
        head = data[pos : pos + 1]
        if head == b"i":
            end = data.index(b"e", pos)
            return int(data[pos + 1 : end]), end + 1
        if head == b"l":
            items, pos = [], pos + 1
            while data[pos : pos + 1] != b"e":
                item, pos = value(pos)
                items.append(item)
            return items, pos + 1
        if head == b"d":
            found, pos = {}, pos + 1
            while data[pos : pos + 1] != b"e":
                key, pos = value(pos)
                found[key], pos = value(pos)
            return found, pos + 1
        if head.isdigit():
            colon = data.index(b":", pos)
            start = colon + 1
            end = start + int(data[pos:colon])
            if end > len(data):
                raise ValueError("a string runs past the end")
            return data[start:end], end
        raise ValueError(f"unexpected byte at {pos}")

    try:
        decoded, end = value(0)
    except (ValueError, IndexError) as exc:
        raise TorrentFileError(f"not a torrent file: {exc}") from exc
    if end != len(data.rstrip(b"\r\n")):
        raise TorrentFileError("not a torrent file: data after its end")
    return decoded


def _text(raw: Any) -> str:
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")


def _directory() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "uploaded_torrents"


def read(data: bytes) -> dict[str, Any]:
    """What a .torrent is: its info hash, name, size, files, whether private, its trackers' count."""
    from bankai.torrent.worker import torrent_info_hash

    if len(data) > MAX_BYTES:
        raise TorrentFileError("that file is too large for a torrent")
    meta = bdecode(data)
    info = meta.get(b"info") if isinstance(meta, dict) else None
    if not isinstance(info, dict) or b"name" not in info:
        raise TorrentFileError("not a torrent file: it has no info")
    info_hash = torrent_info_hash(None, data)
    if not info_hash:
        raise TorrentFileError("not a torrent file: its info hash cannot be read")
    name = _text(info.get(b"name"))
    if isinstance(info.get(b"files"), list):
        files = [
            {"path": "/".join([name, *(_text(part) for part in row.get(b"path") or [])]), "length": int(row.get(b"length") or 0)}
            for row in info[b"files"]
            if isinstance(row, dict)
        ]
    else:
        files = [{"path": name, "length": int(info.get(b"length") or 0)}]
    trackers = [_text(meta.get(b"announce"))] if meta.get(b"announce") else []
    for tier in meta.get(b"announce-list") or []:
        trackers += [_text(url) for url in tier if isinstance(tier, list)]
    return {
        "info_hash": info_hash,
        "name": name,
        "size": sum(row["length"] for row in files),
        "files": files,
        "private": int(info.get(b"private") or 0) == 1,
        "trackers": len(dict.fromkeys(trackers)),
    }


def magnet(info_hash: str, name: str) -> str:
    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}"


def save(data: bytes) -> dict[str, Any]:
    """Keep a dropped .torrent; what it is, and the magnet that stands for it from now on."""
    facts = read(data)
    folder = _directory()
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob("*.torrent"):
        try:
            if now - old.stat().st_mtime > KEEP_SECONDS:
                old.unlink()
        except OSError:
            pass
    target = folder / f"{facts['info_hash']}.torrent"
    temp = target.with_suffix(".part")
    # A private tracker's file carries the passkey: readable by the user alone.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(temp, target)
    return {**{key: facts[key] for key in ("info_hash", "name", "size", "private", "trackers")},
            "files": len(facts["files"]), "magnet": magnet(facts["info_hash"], facts["name"])}


def stored(info_hash: str | None) -> bytes | None:
    """The kept .torrent of this hash, if one was dropped."""
    if not info_hash or not all(char in "0123456789abcdef" for char in info_hash.casefold()) or len(info_hash) != 40:
        return None
    try:
        return (_directory() / f"{info_hash.casefold()}.torrent").read_bytes()
    except OSError:
        return None


def metadata(info_hash: str) -> dict[str, Any] | None:
    """A kept .torrent in the shape qBittorrent's fetchMetadata answers (for the magnet preview)."""
    data = stored(info_hash)
    if data is None:
        return None
    facts = read(data)
    return {
        "info": {"name": facts["name"], "files": facts["files"], "private": facts["private"]},
        "trackers": [None] * facts["trackers"],
    }
