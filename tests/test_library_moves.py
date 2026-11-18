"""Library moves: a title dragged to another root is never lost on the way."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from bankai.web import library_moves


@pytest.fixture
def library(monkeypatch, tmp_path):
    roots = {kind: [tmp_path / kind / "a", tmp_path / kind / "b"] for kind in library_moves.KINDS}
    for listed in roots.values():
        for root in listed:
            root.mkdir(parents=True)
    monkeypatch.setattr(library_moves, "roots", lambda: roots)
    monkeypatch.setattr(library_moves, "_busy_paths", lambda: [])
    monkeypatch.setattr(library_moves, "_refresh", lambda *folders: None)
    monkeypatch.setattr(library_moves, "_store_path", lambda: tmp_path / "moves.json")
    film = roots["movie"][0] / "Troy (2004)"
    (film / "Subs").mkdir(parents=True)
    (film / "Troy (2004).mkv").write_bytes(os.urandom(3 * 2**20 + 7))
    (film / "Subs" / "de.srt").write_text("1\n", encoding="utf-8")
    return roots, film


def _across_drives(monkeypatch):
    """Rename across roots fails as it does between /mnt/g and /mnt/e; renames inside one root work."""
    real = os.rename

    def rename(src, dst):
        if Path(src).parent.parent != Path(dst).parent.parent or ".bankai-move" in str(src):
            if ".bankai-move" not in str(src):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst)

    monkeypatch.setattr(library_moves.os, "rename", rename)


def _row(source: Path, root: Path) -> dict:
    planned = library_moves.plan(str(source), str(root))
    return {"id": "m1", "source": planned["source"], "target": planned["target"], "size": planned["size"]}


def test_one_drive_is_one_rename(library):
    roots, film = library
    assert library_moves.move(_row(film, roots["movie"][1])) == "Renamed"
    assert not film.exists() and (roots["movie"][1] / film.name / "Subs" / "de.srt").is_file()


def test_across_drives_the_original_goes_only_after_the_copy_is_whole(library, monkeypatch):
    roots, film = library
    _across_drives(monkeypatch)
    data = (film / "Troy (2004).mkv").read_bytes()
    assert library_moves.move(_row(film, roots["show"][0])).startswith("Copied")
    moved = roots["show"][0] / film.name
    assert (moved / "Troy (2004).mkv").read_bytes() == data and (moved / "Subs" / "de.srt").is_file()
    assert not film.exists()
    assert not any(p.name.startswith(".") for p in roots["show"][0].iterdir())


def test_a_failed_copy_leaves_the_original_and_no_leftovers(library, monkeypatch):
    roots, film = library
    _across_drives(monkeypatch)
    real_stat = Path.stat

    def short(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if ".bankai-move" in str(self) and self.suffix == ".mkv":
            return os.stat_result((result.st_mode, 0, 0, 1, 0, 0, 5, 0, 0, 0))
        return result

    monkeypatch.setattr(Path, "stat", short)
    with pytest.raises(library_moves.MoveError, match="the copy is 5 bytes"):
        library_moves.move(_row(film, roots["show"][0]))
    monkeypatch.setattr(Path, "stat", real_stat)
    assert (film / "Troy (2004).mkv").stat().st_size == 3 * 2**20 + 7
    assert list(roots["show"][0].iterdir()) == []


def test_a_file_changed_while_copying_stops_the_move(library, monkeypatch):
    roots, film = library
    _across_drives(monkeypatch)
    inventory = library_moves._inventory
    calls = []

    def changing(source):
        calls.append(source)
        if len(calls) == 3:  # the look after the copy (plan and the copy's start came first)
            (film / "Subs" / "de.srt").write_text("rewritten\n", encoding="utf-8")
        return inventory(source)

    monkeypatch.setattr(library_moves, "_inventory", changing)
    with pytest.raises(library_moves.MoveError, match="changed"):
        library_moves.move(_row(film, roots["show"][0]))
    assert film.is_dir() and list(roots["show"][0].iterdir()) == []


def test_nothing_is_moved_over_a_title_already_there(library):
    roots, film = library
    (roots["movie"][1] / film.name).mkdir()
    with pytest.raises(library_moves.MoveError, match="exists already"):
        library_moves.plan(str(film), str(roots["movie"][1]))


def test_another_kind_of_folder_warns(library):
    roots, film = library
    planned = library_moves.plan(str(film), str(roots["show"][0]))
    assert planned["from_kind"] == "movie" and planned["to_kind"] == "show" and planned["warnings"]
    assert not library_moves.plan(str(film), str(roots["movie"][1]))["warnings"]


def test_a_move_cut_off_by_a_restart_leaves_the_original_and_goes_again(library, monkeypatch):
    roots, film = library
    monkeypatch.setattr(library_moves, "_work", lambda: None)
    partial = roots["show"][0] / ".Troy (2004).bankai-move"
    partial.mkdir()
    library_moves._save([{"id": "m1", "source": str(film), "target": str(roots["show"][0] / film.name), "status": "running"}])
    library_moves.recover()
    assert not partial.exists() and film.is_dir()
    assert library_moves._load()[0]["status"] == "queued"


def test_a_title_is_deleted_by_its_folder_and_only_inside_a_root(library, tmp_path):
    roots, film = library
    (film / "extras").mkdir()
    (film / "extras" / "trailer.mkv").write_bytes(b"x" * 10)
    done = library_moves.delete_title([str(film / "Troy (2004).mkv")], "movie")
    assert done["deleted"] == [str(film)] and not film.exists()
    # A file straight in a root, or outside every root, names no title folder: nothing goes.
    loose = roots["movie"][0] / "loose.mkv"
    loose.write_bytes(b"x")
    with pytest.raises(library_moves.MoveError):
        library_moves.delete_title([str(loose), str(tmp_path / "elsewhere" / "x.mkv")], "movie")
    assert loose.exists()
