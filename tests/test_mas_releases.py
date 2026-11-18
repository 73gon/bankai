"""Movies & Shows review: German-dubbed releases found on the indexers, kept for a word."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bankai.web import mas_releases


@pytest.mark.parametrize(
    ("title", "guessed", "german"),
    [
        ("High Five 2025 German DL 1080p BluRay x264-LizardSquad", "High Five", True),
        ("Steve.2025.2160p.NF.WEB-DL.DV.HDR.ENG.LATINO.GERMAN.ITALIAN", "Steve", True),
        ("Die Schleim Tagebuecher S01 German DL DTS 1080p", "Die Schleim Tagebuecher", True),
        # The title's own word, not a language: the year comes after it.
        ("The Good German (2006) 720p BRRip x264 -YTS", "The Good", False),
        ("When Paris Was German (2020) 720p WEBRip-WORLD", "When Paris Was", False),
        ("Inception.2010.German.Subbed.1080p.WEB", "Inception", False),
    ],
)
def test_german_audio_is_read_from_the_release_s_tags_not_its_title(title, guessed, german) -> None:
    assert mas_releases._german_in_tail(title, guessed) is german


def _candidate(title: str, info_hash: str, seeders: int = 9):
    return SimpleNamespace(
        title=title, info_hash=info_hash, download_url="http://x/1.torrent", magnet_uri=None, indexer="Knaben",
        size_bytes=8 * 1024**3, seeders=seeders, leechers=0, publish_date=None, info_url="http://x/view/1",
    )


def test_a_pass_keeps_german_releases_for_review_and_unmatched_ones_apart(monkeypatch) -> None:
    found = [
        _candidate("Valiant One 2025 German DL 1080p BluRay x264-LizardSquad", "a" * 40),
        _candidate("Valiant One 2025 German DL 720p BluRay x264-LizardSquad", "b" * 40),  # below 1080p
        _candidate("Nobody Knows This 2024 German DL 1080p WEB H264-GRP", "c" * 40),
        _candidate("Some Film 2024 1080p BluRay x264-GRP", "d" * 40),  # no German
        _candidate("Valiant One 2025 German DL 2160p WEB H265-GRP", "e" * 40, seeders=0),  # cannot download
    ]

    class Prowlarr:
        async def search(self, query, categories=None):
            return found if categories and 2000 in categories else []

        async def aclose(self):
            pass

    async def match(kind, title, year):
        return {"tmdb_id": 7, "name": "Valiant One", "year": 2025, "poster_url": None, "german_title": None} if title == "Valiant One" else None

    monkeypatch.setattr("bankai.torrent.prowlarr.ProwlarrClient", Prowlarr)
    monkeypatch.setattr(mas_releases, "_match", match)
    monkeypatch.setattr(mas_releases, "_held", lambda kind, match, season: {"german": False})
    monkeypatch.setattr(mas_releases, "_is_anime", lambda title: False)

    result = asyncio.run(mas_releases.discover())
    review = mas_releases.review()

    assert result["added"] == 2
    [card] = review["titles"]
    assert card["name"] == "Valiant One" and len(card["releases"]) == 1
    assert [row["title"] for row in review["unmatched"]] == ["Nobody Knows This 2024 German DL 1080p WEB H264-GRP"]
    assert mas_releases.review_count() == 2

    # Linked by hand, the unmatched one joins its title; removed, a release is not offered again.
    mas_releases.link(["c" * 40], "movie", {"tmdb_id": 8, "name": "Nobody Knows This", "year": 2024, "poster_url": None})
    review = mas_releases.review()
    assert {card["name"] for card in review["titles"]} == {"Valiant One", "Nobody Knows This"}
    assert review["unmatched"] == []
    mas_releases.discard(["a" * 40])
    asyncio.run(mas_releases.discover())
    assert mas_releases.load()["releases"]["a" * 40]["status"] == "discarded"
    assert [card["name"] for card in mas_releases.review()["titles"]] == ["Nobody Knows This"]
    assert mas_releases.review_count() == 1


def test_a_film_the_library_has_in_german_is_not_offered(monkeypatch) -> None:
    class Prowlarr:
        async def search(self, query, categories=None):
            return [_candidate("Valiant One 2025 German DL 1080p BluRay x264-GRP", "a" * 40)] if categories and 2000 in categories else []

        async def aclose(self):
            pass

    async def match(kind, title, year):
        return {"tmdb_id": 7, "name": "Valiant One", "year": 2025, "poster_url": None, "german_title": None}

    monkeypatch.setattr("bankai.torrent.prowlarr.ProwlarrClient", Prowlarr)
    monkeypatch.setattr(mas_releases, "_match", match)
    monkeypatch.setattr(mas_releases, "_held", lambda kind, match, season: {"german": True})
    monkeypatch.setattr(mas_releases, "_is_anime", lambda title: False)

    assert asyncio.run(mas_releases.discover())["added"] == 0


def test_a_release_listed_under_a_new_link_each_search_is_kept_once(monkeypatch) -> None:
    # LimeTorrents gives no info hash and a new token in its link every search: one film was offered 24 times.
    links = iter(range(100))

    class Prowlarr:
        async def search(self, query, categories=None):
            if not (categories and 2000 in categories):
                return []
            listing = _candidate("Hell Is Where the Home Is 2018 German DL 1080p BluRay x264 LizardSquad", None)
            listing.download_url = f"http://127.0.0.1:9696/1/download?link={next(links)}"
            return [listing]

        async def aclose(self):
            pass

    monkeypatch.setattr("bankai.torrent.prowlarr.ProwlarrClient", Prowlarr)
    monkeypatch.setattr(mas_releases, "_match", lambda kind, title, year: asyncio.sleep(0, None))
    monkeypatch.setattr(mas_releases, "_is_anime", lambda title: False)

    asyncio.run(mas_releases.discover())
    asyncio.run(mas_releases.discover())
    assert len(mas_releases.load()["releases"]) == 1


def test_rows_of_one_release_kept_before_are_folded_into_one() -> None:
    row = {"title": "Film 2018 German DL 1080p", "size_bytes": 5, "status": "review", "seeders": 2, "info_hash": None}
    folded = mas_releases._fold({"x": {**row, "id": "x"}, "y": {**row, "id": "y", "status": "discarded", "seeders": 4}})
    [(ident, kept)] = folded.items()
    assert kept["status"] == "discarded" and kept["seeders"] == 4 and kept["id"] == ident


def test_web_dl_is_no_german_dl() -> None:
    from bankai.processor.pipeline import german_audio_named

    assert not german_audio_named("[ToonsHub] Nukitashi The Animation S01E06 1080p AMZN WEB-DL DDP2.0 (German Sub)")
    assert german_audio_named("Title 2019 German DL Subbed 1080p WEB-DL")


def test_what_the_library_has_in_german_is_not_offered() -> None:
    index = {
        ("movies", "valiantone"): {"German": {(0, 0)}, "English": set()},
        ("shows", "dark"): {"German": {(1, n) for n in range(1, 11)} | {(2, 1)}, "English": set()},
    }
    show = {"name": "Dark", "seasons": {"1": 10, "2": 8}}
    assert mas_releases._owned(index, "movie", {"name": "Valiant One"}, {})
    assert not mas_releases._owned(index, "movie", {"name": "Nobody Knows This"}, {})
    # A season pack only when the library has the season whole; an episode when it has that one.
    assert mas_releases._owned(index, "show", show, {"season": 1})
    assert not mas_releases._owned(index, "show", show, {"season": 2})
    assert mas_releases._owned(index, "show", show, {"season": 2, "episode": 1})
    assert not mas_releases._owned(index, "show", show, {"season": 2, "episode": 2})
    assert mas_releases._owned(index, "show", show, {"season": 2}, "Dark S02E01 German DL 1080p WEB x264-GRP")


def test_below_1080p_or_no_resolution_is_not_offered():
    assert mas_releases.sharp("Film 2020 German DL 1080p BluRay x264")
    assert mas_releases.sharp("Film 2020 German DL 2160p UHD")
    assert not mas_releases.sharp("Film 2020 German DL 720p WEB x264")
    assert not mas_releases.sharp("Billions S07E07 German DL XviD-AFG")


def test_an_anime_show_is_left_to_the_anime_library_but_an_anime_film_is_offered(monkeypatch):
    def candidate(title, info_hash, categories):
        return _candidate(title, info_hash) if categories and (2000 in categories) == title.startswith("Akira") else None

    names = ["Cowboy Bebop S01 German Dubbed DL AC3 1080p BluRay x264-GRP", "Akira 1988 German DL 1080p BluRay x264-GRP",
             "Bibi And Tina S01 GERMAN 1080p AMZN WEBRip x264-NTb"]

    class Prowlarr:
        async def search(self, query, categories=None):
            return [c for at, name in enumerate(names) if (c := candidate(name, str(at) * 40, categories))]

        async def aclose(self):
            pass

    async def match(kind, title, year):
        ids = {"Cowboy Bebop": 30991, "Akira": 149, "Bibi And Tina": 61205}
        return {"tmdb_id": ids[title], "name": title, "year": year, "poster_url": None, "german_title": None}

    async def facts(tmdb_id):
        return {"seasons": {"1": 26}, "anime": tmdb_id == 30991}

    monkeypatch.setattr("bankai.torrent.prowlarr.ProwlarrClient", Prowlarr)
    monkeypatch.setattr(mas_releases, "_match", match)
    monkeypatch.setattr(mas_releases, "_show_facts", facts)
    monkeypatch.setattr(mas_releases, "_held", lambda kind, match, season: {"german": False})
    monkeypatch.setattr(mas_releases, "_is_anime", lambda title: False)

    asyncio.run(mas_releases.discover())
    assert {card["name"] for card in mas_releases.review()["titles"]} == {"Akira", "Bibi And Tina"}
    assert mas_releases.review_count() == 2


def test_anime_is_animation_from_japan():
    assert mas_releases.anime_show({"genres": [{"id": 16}], "original_language": "ja", "origin_country": ["JP"]})
    assert not mas_releases.anime_show({"genres": [{"id": 16}], "original_language": "en", "origin_country": ["US"]})
    assert not mas_releases.anime_show({"genres": [{"id": 18}], "original_language": "ja", "origin_country": ["JP"]})
