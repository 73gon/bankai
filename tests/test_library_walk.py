"""The held library tree: walked once, kept current per directory, saved to disk."""

from __future__ import annotations

import os
import time

from bankai.web import library_walk


def _library(tmp_path):
    season = tmp_path / "Frieren" / "Season 01"
    season.mkdir(parents=True)
    (season / "Frieren - S01E01.mkv").write_bytes(b"x" * 7)
    (season / "Frieren - S01E01.ass").write_text("subs")
    (season / "poster.JPG").write_bytes(b"")
    (tmp_path / "Loose Film (2020).MP4").write_bytes(b"x" * 3)
    return season


def _names(roots):
    return sorted(row["name"] for row in library_walk.files(roots))


def _age(root):
    """Everything under ``root`` written an hour ago, as a real library is."""
    old = time.time() - 3600
    for directory, _dirs, _files in os.walk(root):
        os.utime(directory, (old, old))


def _bump(directory):
    """A change a minute ago: past any timestamp tick, so the mtime is trusted."""
    recent = time.time() - 60
    os.utime(directory, (recent, recent))


def test_only_video_files_come_back(tmp_path):
    _library(tmp_path)
    rows = {row["name"]: row for row in library_walk.files([tmp_path])}

    # Nested and bare files both, any case of extension, sizes from the one stat.
    assert sorted(rows) == ["Frieren - S01E01.mkv", "Loose Film (2020).MP4"]
    assert rows["Frieren - S01E01.mkv"]["size"] == 7
    assert rows["Frieren - S01E01.mkv"]["series"] == "Frieren"
    assert rows["Frieren - S01E01.mkv"]["season"] == "Season 01"
    assert rows["Loose Film (2020).MP4"]["series"] == "Loose Film (2020)"


def test_a_missing_root_yields_nothing(tmp_path):
    assert library_walk.files([tmp_path / "not-mounted"]) == []


def test_requests_read_the_held_tree_until_it_is_refreshed(tmp_path):
    season = _library(tmp_path)
    _age(tmp_path)
    assert len(_names([tmp_path])) == 2

    (season / "Frieren - S01E02.mkv").write_bytes(b"x")
    _bump(season)
    # Held: a page load does not touch the disk.
    assert len(_names([tmp_path])) == 2
    # The scheduler's refresh finds it.
    library_walk.refresh_all()
    assert "Frieren - S01E02.mkv" in _names([tmp_path])


def test_only_directories_that_changed_are_listed_again(tmp_path, monkeypatch):
    season = _library(tmp_path)
    other = tmp_path / "Dandadan" / "Season 01"
    other.mkdir(parents=True)
    (other / "Dandadan - S01E01.mkv").write_bytes(b"x")
    _age(tmp_path)
    library_walk.files([tmp_path])

    listed = []
    real = library_walk._list
    monkeypatch.setattr(library_walk, "_list", lambda d: listed.append(d) or real(d))

    (season / "Frieren - S01E02.mkv").write_bytes(b"x")
    _bump(season)
    library_walk.refresh_all()

    assert listed == [str(season)]


def test_a_rescan_checks_the_disk_before_answering(tmp_path):
    season = _library(tmp_path)
    _age(tmp_path)
    library_walk.files([tmp_path])
    (season / "Frieren - S01E01.mkv").unlink()
    _bump(season)

    assert "Frieren - S01E01.mkv" not in [
        row["name"] for row in library_walk.files([tmp_path], rescan=True)
    ]


def test_mark_stale_makes_the_next_read_check(tmp_path):
    """A rename made on the Server page shows on the very next load."""
    season = _library(tmp_path)
    _age(tmp_path)
    library_walk.files([tmp_path])
    (season / "Frieren - S01E01.mkv").rename(season / "Frieren - S01E01 (renamed).mkv")
    _bump(season)

    library_walk.mark_stale()
    assert "Frieren - S01E01 (renamed).mkv" in _names([tmp_path])


def test_a_restart_starts_from_the_saved_tree(tmp_path, monkeypatch):
    _library(tmp_path)
    _age(tmp_path)
    library_walk.files([tmp_path])

    # A fresh process: nothing in memory, only what was saved.
    monkeypatch.setattr(library_walk, "_TREES", {})
    monkeypatch.setattr(library_walk, "_LOADED", False)
    monkeypatch.setattr(library_walk, "_list", lambda d: (_ for _ in ()).throw(AssertionError(d)))

    assert len(_names([tmp_path])) == 2


def test_a_root_nobody_reads_any_more_is_dropped(tmp_path, monkeypatch):
    _library(tmp_path)
    _age(tmp_path)
    library_walk.files([tmp_path])
    library_walk._TREES[str(tmp_path)]["used_at"] = time.time() - library_walk.UNUSED_SECONDS - 1

    assert library_walk.refresh_all() == 0
    assert str(tmp_path) not in library_walk._TREES


def test_a_change_in_the_same_tick_as_the_listing_is_still_found(tmp_path):
    """Directory mtimes move in coarse ticks: a file created right after a
    listing can leave the mtime exactly as it was listed with."""
    season = _library(tmp_path)
    library_walk.files([tmp_path])  # listed while every mtime is brand new
    listed = os.stat(season).st_mtime_ns

    (season / "Frieren - S01E02.mkv").write_bytes(b"x")
    os.utime(season, ns=(listed, listed))  # the tick did not move

    library_walk.refresh_all()
    assert "Frieren - S01E02.mkv" in _names([tmp_path])