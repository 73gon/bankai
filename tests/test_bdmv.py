"""Blu-ray disc images: which playlists are the episodes, in which order."""

from __future__ import annotations

from pathlib import Path

from bankai.processor import bdmv


def _row(name: str, minutes: float, *clips: str) -> dict:
    return {"path": Path(f"/d/BDMV/PLAYLIST/{name}.mpls"), "seconds": minutes * 60, "clips": list(clips), "size": 1}


def test_episodes_are_the_long_playlists_once_without_play_all() -> None:
    rows = [
        _row("00000", 0.2, "00000.m2ts"),            # menu
        _row("00001", 92, "00005.m2ts"),             # episode 1, double length
        _row("00002", 24, "00006.m2ts"),
        _row("00003", 24, "00007.m2ts"),
        _row("00004", 24, "00008.m2ts"),
        _row("00005", 164, "00005.m2ts", "00006.m2ts", "00007.m2ts", "00008.m2ts"),  # play all
        _row("00006", 1.5, "00010.m2ts"),            # NCOP
        _row("00007", 24, "00006.m2ts"),             # the same episode again (another audio default)
    ]
    chosen = bdmv.choose(rows)
    assert [row["clips"] for row in chosen] == [["00005.m2ts"], ["00006.m2ts"], ["00007.m2ts"], ["00008.m2ts"]]


def test_discs_are_found_in_order_without_their_backup(tmp_path) -> None:
    for disc in ("Show.S01D10", "Show.S01D02", "Show.S01D01"):
        (tmp_path / "Release" / disc / "BDMV" / "PLAYLIST").mkdir(parents=True)
        (tmp_path / "Release" / disc / "BDMV" / "BACKUP" / "PLAYLIST").mkdir(parents=True)
    assert [disc.name for disc in bdmv.discs(tmp_path / "Release")] == ["Show.S01D01", "Show.S01D02", "Show.S01D10"]


def test_episodes_number_on_across_discs(tmp_path, monkeypatch) -> None:
    for disc in ("D01", "D02"):
        folder = tmp_path / disc / "BDMV" / "PLAYLIST"
        folder.mkdir(parents=True)
        for name in ("00001", "00002"):
            (folder / f"{name}.mpls").write_bytes(b"")
    monkeypatch.setattr(bdmv, "_identify", lambda path: _row(path.stem, 24, f"{path.stem}.m2ts") | {"path": path})
    rows = bdmv.episodes(tmp_path)
    assert [(row["disc"], row["episode"]) for row in rows] == [("D01", 1), ("D01", 2), ("D02", 3), ("D02", 4)]


def test_episodes_cut_from_one_stream_stay_apart() -> None:
    rows = [
        _row("00001", 24, "00001.m2ts"),
        _row("00002", 24.5, "00001.m2ts"),
        _row("00003", 23.5, "00001.m2ts"),
        _row("00004", 72, "00001.m2ts"),  # play all, the same stream
    ]
    assert [row["path"].stem for row in bdmv.choose(rows)] == ["00001", "00002", "00003"]
