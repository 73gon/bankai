"""The Dashboard's Recently added manga: one row per download filed, its books in a line."""

from __future__ import annotations

from bankai.metadata import anilist_manga
from bankai.web import dashboard


def test_books_are_named_as_runs():
    assert dashboard._book_label(["Vol 01.cbz", "Vol 02.cbz", "Vol 03.cbz"]) == "Vol 01-03"
    assert dashboard._book_label(["Vol 01.cbz", "Vol 03.cbz"]) == "2 volumes"
    assert dashboard._book_label(["Vol 12.cbz", "Chapter 0191.cbz"]) == "Vol 12, Chapter 0191"
    assert dashboard._book_label(["Chapter 0168.1.cbz"]) == "Chapter 0168.1"
    assert dashboard._book_label(["a.epub", "b.epub"]) == "2 books"
    assert dashboard._book_label(["Vol 05.cbz"], replaced=3) == "Vol 05 · replaces 3 chapter files"


def test_one_row_per_download_filed_newest_first(monkeypatch):
    monkeypatch.setattr(anilist_manga, "media", lambda aid: {"cover": f"https://img/{aid}.jpg"})
    monkeypatch.setattr(anilist_manga, "name", lambda aid: f"Series {aid}")
    rows = dashboard.recent_manga([
        {"status": "done", "anilist_id": 1, "copied": ["Vol 01.cbz"], "done_at": 10, "entry": {"info_hash": "a"}},
        {"status": "done", "anilist_id": 2, "copied": ["Vol 02.cbz"], "done_at": 20, "entry": {"info_hash": "b"}},
        {"status": "done", "anilist_id": 3, "copied": [], "done_at": 30, "entry": {"info_hash": "c"}},
        {"status": "review", "anilist_id": 4, "copied": ["Vol 01.cbz"], "entry": {"info_hash": "d"}},
    ])
    assert [row["title"] for row in rows] == ["Series 2", "Series 1"]
    assert rows[0]["href"] == "/m/library?series=2" and rows[0]["poster_url"] == "https://img/2.jpg"
    assert rows[0]["kind"] == "book" and rows[0]["episode_label"] == "Vol 02"
