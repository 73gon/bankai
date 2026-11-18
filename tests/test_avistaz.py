"""The AvistaZ network (AnimeZ, AvistaZ): rows, filters, the login, and private torrents seeding."""

from __future__ import annotations

import asyncio
import time

import pytest

from bankai.torrent import seeding
from bankai.web import avistaz


def _raw(title: str, *, audio=("Japanese",), subs=("English",), seed=5, files=1, quality="1080p", form="TV", ident=1):
    return {
        "url": f"https://animez.to/torrent/{ident}-x",
        "download": f"https://animez.to/rss/download/{ident}/x.torrent",
        "release_title": title,
        "file_name": title.replace(" ", "."),
        "info_hash": f"{ident:040x}",
        "seed": seed,
        "leech": 1,
        "completed": 3,
        "file_size": 2 * 1024**3,
        "file_count": files,
        "download_multiply": 0,
        "video_quality": quality,
        "format": form,
        "created_at_iso": "2026-11-01T10:00:00Z",
        "audio": [{"id": 1, "language": name} for name in audio],
        "subtitle": [{"id": 2, "language": name} for name in subs],
    }


@pytest.fixture
def animez(monkeypatch):
    monkeypatch.setattr(avistaz, "configured", lambda site: True)
    sent: list = []
    rows = [
        _raw("[SubsPlease] Frieren - 05 (1080p)", subs=("English", "German"), ident=1),
        _raw("[Group] Frieren S01 BD (1080p) Dual Audio", audio=("Japanese", "German"), files=28, ident=2),
        _raw("[Group] Frieren - 05 (720p)", quality="720p", ident=3),
        _raw("[Dead] Frieren - 04 (1080p)", seed=0, ident=4),
    ]

    async def search(site, params):
        sent.append((site, params))
        return rows, "https://animez.to/api/v1/jackett/torrents?search=frieren"

    async def matches(titles):
        return [None for _ in titles]

    monkeypatch.setattr(avistaz, "search", search)
    monkeypatch.setattr("bankai.web.anime.anilist_matches", matches)
    return sent


def test_animez_rows_come_as_nyaa_s_with_their_languages(animez) -> None:
    result = asyncio.run(avistaz.anime_search("frieren"))
    first = next(row for row in result["items"] if row["id"] == "1")
    assert first["source"] == "animez" and first["magnet_uri"] == ""
    assert first["audio_langs"] == ["ja"] and first["sub_langs"] == ["en", "de"]
    assert first["quality"] == "1080p" and first["freeleech"] and first["publisher"] == "SubsPlease"
    # Manga and novels are not anime: without a format asked, every anime format is.
    site, params = animez[0]
    assert site == "animez" and ("format[]", "MOVIE") in params and ("format[]", "MANGA") not in params


def test_animez_filters_languages_quality_batches_and_seeders_itself(animez) -> None:
    ids = lambda **kw: {row["id"] for row in asyncio.run(avistaz.anime_search("frieren", **kw))["items"]}
    assert ids(subtitles=["de"]) == {"1"}
    assert ids(audio=["de"]) == {"2"}
    assert ids(batch=True) == {"2"}
    assert ids(quality="720p") == {"3"}
    assert "4" not in ids(min_seeders=1)
    assert ids(publisher="subsplease") == {"1"}
    asyncio.run(avistaz.anime_search("frieren", formats=["MOVIE"]))
    assert [value for key, value in animez[-1][1] if key == "format[]"] == ["MOVIE"]


def test_the_description_is_made_from_the_row(animez) -> None:
    asyncio.run(avistaz.anime_search("frieren"))
    text = avistaz.description("2")
    assert "Japanese, German" in text and "private tracker" in text


def test_site_and_language_names() -> None:
    assert avistaz.site_of("https://animez.to/rss/download/1/x.torrent") == "animez"
    assert avistaz.site_of("https://avistaz.to/torrent/9") == "avistaz"
    assert avistaz.site_of("https://nyaa.si/download/1.torrent") is None
    assert avistaz.language_code("German") == "de" and avistaz.language_code("Portuguese (Brazil)") == "pt-br"


def test_a_run_out_token_is_renewed_once(monkeypatch, tmp_path) -> None:
    import httpx

    monkeypatch.setattr(avistaz, "_token_path", lambda: tmp_path / "tokens.json")
    monkeypatch.setattr(avistaz, "SPACING", 0)
    monkeypatch.setattr(avistaz, "login", lambda site: ("me", "secret", "pid"))
    avistaz._tokens.clear()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth"):
            return httpx.Response(200, json={"token": f"t{len(calls)}"})
        if request.headers["Authorization"] == "Bearer old":
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"data": [{"release_title": "x"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(avistaz.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    avistaz._store_token("animez", "old")
    rows, sent = asyncio.run(avistaz.search("animez", [("search", "x")]))
    assert rows == [{"release_title": "x"}] and calls[1].endswith("/auth")
    assert "Bearer" not in sent and (tmp_path / "tokens.json").stat().st_mode & 0o077 == 0


class _Qbit:
    def __init__(self, rows):
        self.rows = rows
        self.moved: list = []
        self.resumed: list = []

    async def info(self, hashes):
        return [row for row in self.rows if row["hash"] in hashes]

    async def set_category(self, hashes, category):
        self.moved.append((hashes, category))

    async def resume(self, hashes):
        self.resumed.append(hashes)


def _t(key, *, private=True, progress=1.0, size=4, downloaded=None, uploaded=0.0, seeding=3600):
    gb = 10**9
    return {
        "hash": key, "name": key, "private": private, "progress": progress, "total_size": size * gb,
        "downloaded": int((size * progress if downloaded is None else downloaded) * gb), "uploaded": int(uploaded * gb),
        "seeding_time": seeding,
    }


def test_private_torrents_seed_by_the_hit_and_run_rules(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(seeding, "_path", lambda: tmp_path / "holds.json")
    client = _Qbit([
        _t("owed"),                                   # 1 h of 80 h seeded
        _t("public", private=False),
        _t("under 10%", progress=0.05),               # may be deleted
        _t("partial", progress=0.5),                  # owes 2 GB's time
        _t("time met", seeding=82 * 3600),            # 4 GB: 80 h + margin
        _t("0.9 back", uploaded=3.6),
        _t("0.8 back", uploaded=3.2),
    ])
    left = asyncio.run(seeding.hold_unseeded(client, [row["hash"] for row in client.rows]))
    assert left == ["public", "under 10%", "time met", "0.9 back"]
    assert client.moved == [(["owed", "partial", "0.8 back"], seeding.CATEGORY)]
    assert client.resumed == ["owed|partial|0.8 back"]
    # The rules' table: 1 GB 74 h, 50 GB 172 h, 60 GB 190 h, 1000 GB 472 h.
    for gb, hours in ((1, 74), (50, 172), (60, 190), (80, 219), (1000, 472)):
        assert round(seeding.required_hours(gb * 10**9)) == hours
    # A partial torrent's time is that of what it downloaded, counted from when it was held.
    half = _t("partial", progress=0.5)
    assert not seeding.seeded_enough(half, held_at=time.time() - 70 * 3600)
    assert seeding.seeded_enough(half, held_at=time.time() - 78 * 3600)


def test_review_takes_avistaz_s_word_for_german_audio(monkeypatch) -> None:
    from bankai.web import mas_releases

    class Prowlarr:
        async def search(self, query, categories=None):
            return []

        async def aclose(self):
            pass

    rows = {
        "movie": [
            avistaz.candidate({**_raw("Parasite 2019 BluRay", audio=("Korean", "German"), ident=7), "type": "Movie"}),
            avistaz.candidate({**_raw("The Host 2006 BluRay", audio=("Korean",), ident=8), "type": "Movie"}),
        ],
        "show": [],
    }

    async def mas_search(query, kind, *, pages=1):
        return rows[kind]

    async def match(kind, title, year):
        return {"tmdb_id": 496243, "name": "Parasite", "year": 2019, "poster_url": None, "german_title": None}

    monkeypatch.setattr("bankai.torrent.prowlarr.ProwlarrClient", Prowlarr)
    monkeypatch.setattr(avistaz, "mas_search", mas_search)
    monkeypatch.setattr(mas_releases, "_match", match)
    monkeypatch.setattr(mas_releases, "_held", lambda kind, match, season: {"german": False})
    monkeypatch.setattr(mas_releases, "_is_anime", lambda title: False)

    assert asyncio.run(mas_releases.discover())["added"] == 1
    [card] = mas_releases.review()["titles"]
    # The name says no German and no resolution; the tracker says both.
    assert card["name"] == "Parasite" and card["releases"][0]["title"] == "Parasite 2019 BluRay 1080p"
    # Read again next pass, it is not dropped for a name that names no German.
    asyncio.run(mas_releases.discover())
    assert mas_releases.review_count() == 1


def test_a_refused_login_is_not_asked_again_at_once(monkeypatch, tmp_path) -> None:
    import httpx

    monkeypatch.setattr(avistaz, "_token_path", lambda: tmp_path / "tokens.json")
    monkeypatch.setattr(avistaz, "SPACING", 0)
    monkeypatch.setattr(avistaz, "login", lambda site: ("me", "secret", "pid"))
    avistaz._tokens.clear()
    avistaz._refused.clear()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(422, json={"message": "Only member rank and above can use API"})

    real = httpx.AsyncClient
    monkeypatch.setattr(avistaz.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    for _ in range(3):
        with pytest.raises(avistaz.AvistazError, match="member rank"):
            asyncio.run(avistaz.search("avistaz", [("search", "x")]))
    assert len(calls) == 1
    avistaz.forget_token("avistaz")
    with pytest.raises(avistaz.AvistazError):
        asyncio.run(avistaz.search("avistaz", [("search", "x")]))
    assert len(calls) == 2
