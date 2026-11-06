"""Replace with magnet link: ticked anime episodes replaced by a magnet's torrent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bankai.web import anime_files, erai

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=%5BGroup%5D%20Show%20-%2003"


def test_a_magnet_link_is_read_for_its_hash_and_name():
    assert erai.magnet_parts(MAGNET) == ("a" * 40, "[Group] Show - 03")
    with pytest.raises(ValueError):
        erai.magnet_parts("https://nyaa.si/view/1")


def test_one_magnet_replaces_ticked_episodes_of_several_entries_by_the_library_numbers(tmp_path, monkeypatch):
    """Attack on Titan's Season 4 is two parts and two specials, S04E01-E30; its pack is one torrent."""
    files = {
        tmp_path / "Show - S04E16.mkv": [(110277, 16)],
        tmp_path / "Show - S04E17.mkv": [(131681, 1)],
        tmp_path / "Show - S04E29.mkv": [(146984, 1)],
    }
    for path in files:
        path.write_bytes(b"x")
    added, saved = [], {}

    class Qbit:
        async def login(self):
            pass

        async def add(self, **kwargs):
            added.append(kwargs)

        async def aclose(self):
            pass

    async def index():
        return None

    async def anime(aid):
        return SimpleNamespace(id=aid, name=f"Part {aid}", episodes=16)

    monkeypatch.setattr(anime_files, "of", lambda path: files.get(Path(path), []))
    monkeypatch.setattr(erai, "_has_german", lambda path: False)
    monkeypatch.setattr(erai.anilist, "index", index)
    monkeypatch.setattr(erai.anilist, "anime", anime)
    monkeypatch.setattr(erai, "_load_state", lambda: {"releases": {}, "canonical": {}})
    monkeypatch.setattr(erai, "_save_state", lambda state: saved.update(state))
    monkeypatch.setattr("bankai.torrent.qbittorrent.QBittorrentClient", Qbit)

    result = asyncio.run(erai.replace_from_magnet([str(path) for path in files], MAGNET))
    assert result["episodes"] == [16, 17, 29] and added[0]["magnet"] == MAGNET
    release = saved["releases"]["a" * 40]
    args = release["args"]
    mapped = [args[i + 1] for i, arg in enumerate(args) if arg == "--episode-map"]
    assert mapped == ["16=110277:16", "17=131681:1", "29=146984:1"]
    assert "--replace-existing" in args and "--publish" in args and release["anilist_id"] == 110277
    assert {key for key in saved["canonical"]} == {"anilist:110277|16", "anilist:131681|1", "anilist:146984|1"}


def test_episodes_of_two_seasons_are_not_one_magnet(tmp_path, monkeypatch):
    one, two = tmp_path / "Show - S01E01.mkv", tmp_path / "Show - S02E01.mkv"
    for path in (one, two):
        path.write_bytes(b"x")
    monkeypatch.setattr(anime_files, "of", lambda path: [(1, 1)] if "S01" in str(path) else [(2, 1)])
    with pytest.raises(ValueError, match="one season"):
        asyncio.run(erai.replace_from_magnet([str(one), str(two)], MAGNET))


def test_files_with_german_are_replaced_only_when_that_is_confirmed(tmp_path, monkeypatch):
    path = tmp_path / "Show - S01E03.mkv"
    path.write_bytes(b"x")
    monkeypatch.setattr(anime_files, "of", lambda p: [(7, 3)])
    monkeypatch.setattr(erai, "_has_german", lambda p: True)
    with pytest.raises(ValueError, match="German"):
        asyncio.run(erai.replace_from_magnet([str(path)], MAGNET))


def test_a_review_card_recheck_names_its_releases_for_the_worker(monkeypatch, tmp_path):
    """With the automation off its cycle never comes: a card's releases are named and checked anyway."""
    path = tmp_path / "erai_cycle_request.json"
    monkeypatch.setattr(erai, "_cycle_request_path", lambda: path)
    erai._request_cycle(retries_only=True, only=["a", "b"])
    erai._request_cycle(retries_only=True, only=["c"])
    request = erai._read_json(path)
    assert request["retries_only"] is True and request["only"] == ["a", "b", "c"]
