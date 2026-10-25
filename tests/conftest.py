"""Shared test isolation."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bankai.metadata import anilist, offline
from bankai.web import anime_collections, anime_files, anime_layout, library_walk


@pytest.fixture(autouse=True)
def _never_the_live_instance(tmp_path_factory, monkeypatch):
    """Every test gets its own config and state directory, and a qBittorrent nobody answers at.

    Without a config of its own a test read ``config.toml`` from the working
    directory -- on the server, the live instance's: its qBittorrent, its
    Jellyfin, its Erai-raws login. A test running the download hold pass
    with an empty state paused every real download it did not know of, and
    wrote its record of that into its own temporary state, so nothing ever
    resumed them (Oct 2026). A test may still set its own; this is the floor.
    """
    from bankai.config import reset_settings_cache

    root = tmp_path_factory.mktemp("isolated")
    config = root / "config.toml"
    config.write_text('[qbittorrent]\nurl = "http://127.0.0.1:9"\n', encoding="utf-8")
    monkeypatch.setenv("BANKAI_CONFIG", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    reset_settings_cache()
    yield
    reset_settings_cache()


class AniListData:
    """A small AniList, written where the code reads it: the offline titles and the API cache.

    ``add`` takes what a test needs of an entry; the rest has defaults.
    Relations are (other id, AniList relation type) pairs, or triples with
    the other's format.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.offline: dict[str, dict] = {}
        self.media: dict[str, dict] = {}

    def add(
        self,
        aid: int,
        romaji: str,
        *,
        english: str | None = None,
        format: str = "TV",
        episodes: int | None = 12,
        start: str | None = "2020-01-01",
        status: str = "FINISHED",
        relations: list | tuple = (),
        synonyms: list | tuple = (),
        streaming: dict[int, str] | None = None,
        airing: dict[int, str] | None = None,
        cover: str | None = None,
        banner: str | None = None,
        description: str | None = None,
        anidb: list[int] | tuple = (),
        fetched: bool = True,
        mal_id: int | None = None,
    ) -> None:
        year = int(start[:4]) if start else None
        offline_type = {"TV_SHORT": "TV"}.get(format, format)
        self.offline[str(aid)] = {
            "title": romaji,
            "synonyms": list(synonyms),
            "type": offline_type,
            "episodes": episodes,
            "status": status,
            "year": year,
            "season": None,
            "picture": None,
            "anidb": list(anidb),
            "related": [],
        }
        if fetched:
            rows = []
            for relation in relations:
                other, kind = relation[0], relation[1]
                form = relation[2] if len(relation) > 2 else (self.media.get(str(other), {}).get("format") or "TV")
                rows.append([int(other), kind, form])
            self.media[str(aid)] = {
                "romaji": romaji,
                "english": english,
                "native": None,
                "synonyms": list(synonyms),
                "format": format,
                "status": status,
                "episodes": episodes,
                "duration": 24,
                "season": None,
                "year": year,
                "start": start,
                "description": description,
                "genres": [],
                "studios": [],
                "cover": cover,
                "banner": banner,
                "airing": {str(k): v for k, v in (airing or {}).items()},
                "streaming": {str(k): v for k, v in (streaming or {}).items()},
                "relations": rows,
                "mal_id": mal_id,
                "fetched_at": time.time(),
            }
        self.save()

    def save(self) -> None:
        offline._store().parent.mkdir(parents=True, exist_ok=True)
        offline._store().write_text(json.dumps(self.offline), encoding="utf-8")
        anilist._media_store().write_text(json.dumps(self.media), encoding="utf-8")
        cross = {str(a): int(aid) for aid, row in self.offline.items() for a in row["anidb"]}
        offline._cross_store().write_text(json.dumps(cross), encoding="utf-8")
        reset_anilist_caches()

    def index(self):
        return anilist.table()


def reset_anilist_caches() -> None:
    anilist._MEDIA = None
    anilist._INDEX = None
    offline._LOADED = None
    offline._CROSS = None
    anime_collections._TABLES = None
    anime_files._CACHE = None


@pytest.fixture(autouse=True)
def _isolated_anime_state(tmp_path_factory, monkeypatch):
    """AniList's data, the file index and the collections: never the real ones, never the network."""
    root = tmp_path_factory.mktemp("anime_state")
    monkeypatch.setattr(offline, "_store", lambda: root / "anime_metadata" / "offline_anilist.json")
    monkeypatch.setattr(anilist, "_media_store", lambda: root / "anime_metadata" / "anilist_media.json")
    monkeypatch.setattr(anime_files, "_path", lambda: root / "anime_files.json")
    monkeypatch.setattr(anime_collections, "_path", lambda: root / "anime_collections.json")
    monkeypatch.setattr(anime_layout, "moves_dir", lambda: root / "anime_layout_moves")
    monkeypatch.setattr(anime_layout, "_pictures_path", lambda: root / "anime_layout_pictures.json")
    reset_anilist_caches()

    async def no_ensure(*args, **kwargs):
        return 0

    async def no_search(*args, **kwargs):
        return []

    async def no_refresh(*args, **kwargs):
        return False

    async def no_pictures(*args, **kwargs):
        return 0

    monkeypatch.setattr(anilist, "ensure", no_ensure)
    monkeypatch.setattr(anilist, "search", no_search)
    monkeypatch.setattr(offline, "refresh", no_refresh)
    monkeypatch.setattr(anime_layout, "fetch_pictures", no_pictures)
    yield
    reset_anilist_caches()


@pytest.fixture
def anilist_data(tmp_path) -> AniListData:
    """Entries for a test to add: ``anilist_data.add(16498, "Shingeki no Kyojin", ...)``."""
    return AniListData(tmp_path)


@pytest.fixture(autouse=True)
def _isolated_library_walk(tmp_path_factory, monkeypatch):
    """The held library tree is saved to disk; never to the real state directory."""
    store = tmp_path_factory.mktemp("library_walk") / "library_walk.json"
    monkeypatch.setattr(library_walk, "_store_path", lambda: store)
    monkeypatch.setattr(library_walk, "_TREES", {})
    monkeypatch.setattr(library_walk, "_STALE", set())
    monkeypatch.setattr(library_walk, "_LOADED", False)


@pytest.fixture(autouse=True)
def _no_startup_backfill(monkeypatch):
    """The app's startup backfill probes every library file in a thread; with ffprobe
    on PATH it lands in tests that patch subprocess.run and count their calls."""
    from bankai.web import app as app_mod

    monkeypatch.setattr(app_mod, "_backfill_review_metadata", lambda: None)


@pytest.fixture(autouse=True)
def _no_disk_scan(monkeypatch):
    """A layout pass that moves files starts a disk scan in a thread: it would walk the real disk."""
    from bankai.web import storage

    monkeypatch.setattr(storage, "refresh", lambda: False)


@pytest.fixture(autouse=True)
def _no_snapshot_warming(monkeypatch):
    """The web app's background warming would build every tab in each test's app."""
    monkeypatch.setenv("BANKAI_WARM_SNAPSHOTS", "0")
    monkeypatch.delenv("BANKAI_ANIME_WORKER", raising=False)
