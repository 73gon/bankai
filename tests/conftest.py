"""Shared test isolation."""

from __future__ import annotations

import pytest

from bankai.web import library_walk


@pytest.fixture(autouse=True)
def _isolated_library_walk(tmp_path_factory, monkeypatch):
    """The held library tree is saved to disk; never to the real state directory."""
    store = tmp_path_factory.mktemp("library_walk") / "library_walk.json"
    monkeypatch.setattr(library_walk, "_store_path", lambda: store)
    monkeypatch.setattr(library_walk, "_TREES", {})
    monkeypatch.setattr(library_walk, "_STALE", set())
    monkeypatch.setattr(library_walk, "_LOADED", False)
