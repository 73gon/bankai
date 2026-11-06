"""Artwork of the Movies & Shows library: where each chosen picture is written."""

from __future__ import annotations

from pathlib import Path

from bankai.web import mas_artwork


def test_a_show_pictures_each_folder_it_sits_in_and_its_season_folders():
    card = {"episodes": [
        {"path": "/g/shows/Show/Season 01/Show - S01E01.mkv", "rel_path": "Show/Season 01/Show - S01E01.mkv", "root": "/g/shows"},
        {"path": "/g/shows_en/Show (2009)/Season 02/Show - S02E01.mkv", "rel_path": "Show (2009)/Season 02/Show - S02E01.mkv", "root": "/g/shows_en"},
        {"season_number": 3, "episode": 1, "missing": True},
    ]}
    files = mas_artwork.files("show", card)
    assert files["poster"] == [Path("/g/shows/Show/poster.jpg"), Path("/g/shows_en/Show (2009)/poster.jpg")]
    assert files["season:2"] == [Path("/g/shows_en/Show (2009)/Season 02/poster.jpg")]
    assert mas_artwork.seasons(card) == {1, 2}


def test_a_film_in_its_own_folder_gets_poster_jpg_a_bare_file_its_own_name():
    card = {"files": [
        {"path": "/g/movies/Arrival (2016)/Arrival (2016).mkv", "rel_path": "Arrival (2016)/Arrival (2016).mkv", "root": "/g/movies"},
        {"path": "/e/movies/Arrival (2016).mkv", "rel_path": "Arrival (2016).mkv", "root": "/e/movies"},
    ]}
    files = mas_artwork.files("movie", card)
    assert files["poster"] == [Path("/g/movies/Arrival (2016)/poster.jpg"), Path("/e/movies/Arrival (2016)-poster.jpg")]
    assert files["logo"][1] == Path("/e/movies/Arrival (2016)-logo.png")
    assert "season:1" not in files


def test_a_chosen_picture_is_kept_by_tmdb_id_and_given_back():
    mas_artwork.choose("show", 1399, "poster", "https://image.tmdb.org/t/p/original/a.jpg")
    assert mas_artwork.chosen("show", 1399) == {"poster": "https://image.tmdb.org/t/p/original/a.jpg"}
    assert mas_artwork.chosen("movie", 1399) == {}
    slots = {row["slot"]: row for row in mas_artwork.panel("show", 1399, {1})}
    assert slots["poster"]["chosen"] and slots["season:1"]["url"] is None
    mas_artwork.choose("show", 1399, "poster", None)
    assert mas_artwork.load() == {}


def test_a_slot_not_chosen_shows_what_jellyfin_shows_now(monkeypatch):
    """A picture Jellyfin fetched itself has no TMDB address: the dialog shows Jellyfin's own,
    and says when a chosen backdrop is not the one Jellyfin shows first."""
    import asyncio

    from bankai.web import jellyfin

    card = {"key": "Show (2009)", "episodes": [
        {"path": "/mnt/g/shows/Show (2009)/Season 01/Show - S01E01.mkv", "rel_path": "Show (2009)/Season 01/Show - S01E01.mkv",
         "root": "/mnt/g/shows"},
    ]}
    images = {
        "series": [
            {"ImageType": "Primary", "ImageIndex": None, "ImageTag": "p", "Path": "G:\\shows\\Show (2009)\\poster.jpg"},
            {"ImageType": "Backdrop", "ImageIndex": 1, "ImageTag": "b1", "Path": "G:\\shows\\Show (2009)\\fanart.jpg"},
            {"ImageType": "Backdrop", "ImageIndex": 0, "ImageTag": "b0",
             "Path": "C:\\ProgramData\\Jellyfin\\Server\\metadata\\library\\d2\\x\\backdrop.jpg"},
            {"ImageType": "Banner", "ImageIndex": None, "ImageTag": "n", "Path": "C:\\x\\banner.jpg"},
        ],
        "season1": [{"ImageType": "Primary", "ImageIndex": None, "ImageTag": "s", "Path": "C:\\x\\season.jpg"}],
    }

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return Response(images[url.split("/")[2]])

    async def index(client):
        return {"/mnt/g/shows/Show (2009)": "series", "/mnt/g/shows/Show (2009)/Season 01": "season1"}

    monkeypatch.setattr(jellyfin, "configured", lambda: True)
    monkeypatch.setattr(jellyfin, "_client", Client)
    monkeypatch.setattr(jellyfin, "_index", index)
    monkeypatch.setattr(mas_artwork, "get_settings", lambda: type("S", (), {"jellyfin": type("J", (), {"url": "http://jf:8096/"})})())
    mas_artwork.choose("show", 7, "fanart", "https://image.tmdb.org/t/p/original/f.jpg")
    slots = asyncio.run(mas_artwork.with_jellyfin(mas_artwork.panel("show", 7, {1}), "show", card))
    by_slot = {row["slot"]: row for row in slots}
    assert by_slot["poster"]["jellyfin_url"] == "http://jf:8096/Items/series/Images/Primary?tag=p&maxWidth=800&quality=80"
    assert by_slot["poster"]["jellyfin_own"] is False  # the poster.jpg in the show's folder
    # Chosen, and bankai's fanart.jpg is there -- but Jellyfin shows the backdrop it fetched first.
    assert by_slot["fanart"]["chosen"] and by_slot["fanart"]["jellyfin_own"] is True
    assert "/Backdrop/0?" in by_slot["fanart"]["jellyfin_url"]
    assert by_slot["season:1"]["jellyfin_url"].startswith("http://jf:8096/Items/season1/Images/Primary")
    assert "jellyfin_url" not in by_slot["logo"]


def test_without_jellyfin_the_slots_stay_as_they_are(monkeypatch):
    import asyncio

    from bankai.web import jellyfin

    monkeypatch.setattr(jellyfin, "configured", lambda: True)

    def away():
        raise OSError("no route to host")

    monkeypatch.setattr(jellyfin, "_client", away)
    slots = [{"slot": "poster", "url": None, "chosen": False, "candidates": []}]
    assert asyncio.run(mas_artwork.with_jellyfin(slots, "movie", {"key": "A", "files": []})) == slots
