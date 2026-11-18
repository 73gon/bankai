"""Uploaders followed for German audio their release names do not say (QxR on kickasstorrents)."""

from __future__ import annotations

import asyncio

from bankai.web import uploaders

LISTING = """<table><tr class="odd" > <td> <div class="torrentname">
<a href="/city-of-blood-2026-s01-qxr-t6750001.html" class="torType filmType"> </a>
<div class="markeredBlock torType filmType"> <a href="/city-of-blood-2026-s01-qxr-t6750001.html" class="cellMainLink"> City of Blood (2026) S01 (1080p WEB-DL x265 EAC3 5 1 Celdra)  QxR </a>
</div></div></td></tr>
<tr class="even" > <td> <div class="torrentname">
<a href="/oak-street-2026-qxr-t6750002.html" class="cellMainLink"> The End of Oak Street (2026) (2160p English + German) QxR </a>
</div></td></tr></table>"""


def _page(title: str, audio: list[str], extras: str = "") -> str:
    lines = "".join(f"<strong>Audio #{n}: </strong> {language} / E-AC-3 / 6 channels / 768 kb/s <br>" for n, language in enumerate(audio, 1))
    return (
        f'<h1 class="novertmarg"><a href="/x.html"><span>{title}</span></a></h1>'
        '<div class="seedLeachContainer"> <div class="seedBlock"> seeders: <strong> 52 </strong> </div>'
        '<div class="leechBlock"> leechers: <strong> 30 </strong> </div></div>'
        '<a href="magnet:?xt=urn:btih:E3E14755AEBD3F365DC01667954F13E0E0A5C621&amp;dn=x">magnet</a>'
        '<div class="widgetSize"><span class="torType filmType"></span> <strong>15.5 GB</strong></div>'
        f'<div class="textcontent" id="desc">{lines}<strong>Subtitle #1: </strong> English / UTF-8</div>{extras}'
    )


def test_listing_and_upload_pages_are_read() -> None:
    assert [row[1] for row in uploaders.read_listing(LISTING)] == ["6750001", "6750002"]
    found = uploaders.read_upload(_page("City of Blood (2026) S01 [QxR]", ["German"]))
    assert found["german"] and found["seeders"] == 52 and found["size_bytes"] == int(15.5 * 1024**3)
    assert found["magnet"].startswith("magnet:?xt=urn:btih:E3E1") and "&dn=x" in found["magnet"]


def test_a_german_word_in_the_name_is_not_german_audio() -> None:
    # Oak Street's "German" is an audio-description .ac3 among the extras; the MKV is English.
    page = _page("The End of Oak Street (2026) English + German", ["English", "English"],
                 extras="<li>The End of Oak Street German Descriptive Audio.ac3 (136.6 MB)</li>")
    assert uploaders.read_upload(page)["audio"] == ["English"] and not uploaders.read_upload(page)["german"]


def test_a_pass_reads_new_uploads_once_and_offers_the_german_ones(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(uploaders, "_path", lambda: tmp_path / "uploaders.json")
    monkeypatch.setattr(uploaders, "SPACING", 0)
    asked: list[str] = []
    pages = {
        "/user/QxR/": LISTING,
        "/user/QxR/2/": "",
        "/city-of-blood-2026-s01-qxr-t6750001.html": _page("City of Blood (2026) S01 (1080p) [QxR]", ["German"]),
        "/oak-street-2026-qxr-t6750002.html": _page("The End of Oak Street (2026) (2160p) [QxR]", ["English"]),
    }

    async def get(client, url):
        path = url.split("kickasstorrents.to", 1)[1]
        asked.append(path)
        return pages.get(path)

    monkeypatch.setattr(uploaders, "_get", get)
    uploaders.follow("kickass", "QxR")
    assert asyncio.run(uploaders.run()) == {"read": 2, "german": 1}
    [candidate] = uploaders.german_candidates()
    assert candidate.title.startswith("City of Blood") and candidate.info_hash == "e3e14755aebd3f365dc01667954f13e0e0a5c621"
    row = uploaders.listing()["uploaders"][0]
    assert row["read"] == 2 and row["german"] == 1 and row["done"]
    asked.clear()
    asyncio.run(uploaders.run())
    assert asked == ["/user/QxR/"], "read uploads are not read again, the reading back is done"
    uploaders.unfollow("kickass", "qxr")
    assert uploaders.german_candidates() == []


FULL_MEDIAINFO = """General
Complete name : Film.2024.MULTi.mkv

Video
Format : HEVC

Audio #1
Format : E-AC-3
Language : English

Audio #2
Format : E-AC-3
Title : Deutsch
Language : German

Text #1
Language : German
"""


def test_the_full_mediainfo_text_is_read_too() -> None:
    # Subtitles in German are no German audio: only the Audio sections count.
    assert uploaders.audio_languages(FULL_MEDIAINFO) == ["English", "German"]
    assert uploaders.audio_languages(FULL_MEDIAINFO.replace("Language : German\n\nText", "Language : French\n\nText")) == ["English", "French"]


def test_the_pirate_bay_is_read_gently_and_waits_when_asked(monkeypatch, tmp_path) -> None:
    import httpx

    monkeypatch.setattr(uploaders, "_path", lambda: tmp_path / "uploaders.json")
    monkeypatch.setattr(uploaders, "SPACING", 0)
    monkeypatch.setattr(uploaders, "TPB_SPACING", 0)
    refuse = {"on": False}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/q.php":
            return httpx.Response(200, json=[
                {"id": "1", "name": "Film.2024.1080p.MULTi", "info_hash": "A" * 40, "size": "4000000000", "seeders": "50", "leechers": "2"},
                {"id": "2", "name": "Other.2024.1080p", "info_hash": "B" * 40, "size": "1", "seeders": "5", "leechers": "0"},
            ])
        if refuse["on"]:
            return httpx.Response(429)
        descr = FULL_MEDIAINFO if request.url.params["id"] == "1" else "Audio: English / AAC"
        return httpx.Response(200, json={"descr": descr})

    real = httpx.AsyncClient
    monkeypatch.setattr(uploaders.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    uploaders.follow("tpb", "Freddy1714")
    refuse["on"] = True
    assert asyncio.run(uploaders.run()) == {"read": 0, "german": 0}
    row = uploaders.listing()["uploaders"][0]
    assert row["unread"] == 2 and row["blocked_until"], "a 429 is waited out, not asked again"
    asked.clear()
    asyncio.run(uploaders.run())
    assert asked == ["/q.php"]
    # Once the wait is over, the descriptions are read.
    data = uploaders.load()
    data["reading"]["tpb:freddy1714"]["blocked_until"] = 0
    uploaders._save(data)
    refuse["on"] = False
    assert asyncio.run(uploaders.run()) == {"read": 2, "german": 1}
    [candidate] = uploaders.german_candidates()
    assert candidate.title == "Film.2024.1080p.MULTi" and candidate.magnet_uri.startswith("magnet:?xt=urn:btih:" + "a" * 40)
