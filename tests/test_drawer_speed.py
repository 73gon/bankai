"""What an anime drawer reads, without the slow parts: staging from the held tree, review state parsed once."""

from __future__ import annotations

import os
import time

from bankai.web import library_walk, media, review


def test_staging_from_the_held_tree_is_what_a_walk_finds(tmp_path, monkeypatch):
    root = tmp_path / "staging"
    episode = root / "Shows" / "Show" / "Season 02" / "Show - S02E03.mkv"
    movie = root / "Movies" / "Film (2020)" / "Film (2020).mkv"
    work = root / "Shows" / "Show" / "Season 02" / "Show - S02E04.repack.tmp.mkv"
    for path in (episode, movie, work):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
    library_walk.refresh_all()

    walked = media.scan_library(root)
    held = media.scan_library(root, held=True)

    assert sorted(entry.path for entry in held) == sorted(entry.path for entry in walked)
    by_path = {entry.path: entry for entry in held}
    assert (by_path[str(episode)].series, by_path[str(episode)].season) == ("Show", 2)
    assert by_path[str(movie)].kind == "movie"


def test_review_state_is_parsed_once_until_the_file_changes(tmp_path, monkeypatch):
    store = tmp_path / "review.json"
    monkeypatch.setattr(review, "_store_path", lambda: store)
    monkeypatch.setattr(review, "_READ", None)
    path = tmp_path / "a.mkv"
    review.set_stage(path, "approved")
    loads: list[int] = []
    real = review._load
    monkeypatch.setattr(review, "_load", lambda: loads.append(1) or real())

    for _ in range(5):
        assert review.get_state(path).stage == "approved"
    assert len(loads) == 1

    # Written again (by another process, say): read again.
    review.set_stage(path, "transferred")
    later = time.time() + 5
    os.utime(store, (later, later))
    assert review.get_state(path).stage == "transferred"
    # What a reader changes on its state never reaches the shared copy.
    review.get_state(path).stage = "deleted"
    assert review.get_state(path).stage == "transferred"


def test_the_file_list_is_built_once_per_tree_and_each_caller_gets_its_own(tmp_path):
    root = tmp_path / "anime"
    (root / "Show" / "Season 01").mkdir(parents=True)
    (root / "Show" / "Season 01" / "Show - S01E01.mkv").write_bytes(b"v")
    first = library_walk.files([root])
    first[0].pop("root")  # the anime page does this to its rows
    second = library_walk.files([root])
    assert second[0]["root"] == str(root)
    # A new file reaches the list once the tree is refreshed.
    (root / "Show" / "Season 01" / "Show - S01E02.mkv").write_bytes(b"v")
    later = time.time() + 5
    os.utime(root / "Show" / "Season 01", (later, later))
    library_walk.refresh_all()
    assert len(library_walk.files([root])) == 2


def test_the_release_state_is_parsed_again_only_when_it_changed(tmp_path, monkeypatch):
    from bankai.web import erai

    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr(erai, "_STATE_READ", None)
    state = erai._default_state()
    state["anime"] = {"1": {}}
    erai._save_state(state)
    reads: list[int] = []
    real = erai._load_state
    monkeypatch.setattr(erai, "_load_state", lambda: reads.append(1) or real())

    assert erai.state_for_reading()["anime"] == {"1": {}}
    assert erai.state_for_reading()["anime"] == {"1": {}}
    assert len(reads) == 1

    state["anime"] = {"2": {}}
    erai._save_state(state)
    later = time.time() + 5
    os.utime(tmp_path / "erai_automation.json", (later, later))
    assert erai.state_for_reading()["anime"] == {"2": {}}
