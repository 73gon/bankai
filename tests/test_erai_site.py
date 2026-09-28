"""Erai-raws' own subtitle lists, read from its token feeds."""

from __future__ import annotations

import asyncio

import pytest

from bankai.web import erai, erai_site

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:erai="https://www.erai-raws.info/rss-page/"><channel>
<item>
  <title>[Torrent] Meitantei Precure - 35 (HEVC) [1080p CR WEBRip HEVC AAC][us][de][Encoded]</title>
  <pubDate>Mon, 28 Sep 2026 01:31:54 +0000</pubDate>
  <erai:resolution>1080p</erai:resolution>
  <erai:size>562.52MB</erai:size>
  <erai:infohash>84B20CA269C0E67680BAC521B13C699CC3D031C1</erai:infohash>
  <erai:subtitles>[us][br][de][it]</erai:subtitles>
  <erai:category>[Encoded]</erai:category>
  <description><![CDATA[<a href="https://www.erai-raws.info/encodes/meitantei-precure-35-hevc/">[Erai-raws] Meitantei Precure - 35 [1080p CR WEBRip HEVC AAC][MultiSub][CDE6232F].mkv</a> | Subtitles: [us][de]]]></description>
</item>
<item>
  <title>[Torrent] Other Show - 02 [1080p]</title>
  <erai:infohash>1111111111111111111111111111111111111111</erai:infohash>
  <erai:subtitles>[us][br]</erai:subtitles>
  <description><![CDATA[<a href="https://www.erai-raws.info/episodes/other-show-02/">[Erai-raws] Other Show - 02 [1080p][MultiSub].mkv</a>]]></description>
</item>
<item><title>no hash</title></item>
</channel></rss>"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(erai_site, "_store", lambda: tmp_path / "erai_site.json")
    monkeypatch.setattr(erai, "_state_path", lambda: tmp_path / "erai_automation.json")
    monkeypatch.setattr(erai_site, "configured", lambda: True)
    monkeypatch.setattr(erai_site, "_LOADED", None)
    return tmp_path


def test_a_feed_item_carries_its_hash_and_subtitles():
    rows = erai_site.parse_feed(FEED)
    assert [row["hash"] for row in rows] == [
        "84b20ca269c0e67680bac521b13c699cc3d031c1",
        "1111111111111111111111111111111111111111",
    ]
    first = rows[0]
    assert first["subs"] == ["us", "br", "de", "it"]
    assert first["name"] == "[Erai-raws] Meitantei Precure - 35 [1080p CR WEBRip HEVC AAC][MultiSub][CDE6232F].mkv"
    assert first["title"] == "Meitantei Precure - 35 (HEVC)"
    assert first["page"] == "https://www.erai-raws.info/encodes/meitantei-precure-35-hevc/"
    assert first["published"] > 0


def test_german_is_known_per_torrent(store):
    erai_site._merge(erai_site.parse_feed(FEED))
    assert erai_site.german("84B20CA269C0E67680BAC521B13C699CC3D031C1") is True
    assert erai_site.german("1" * 40) is False
    assert erai_site.german("2" * 40) is None  # not listed: nothing is known


def test_a_show_is_found_by_its_page_address():
    assert erai_site.show_slug("Meitantei Precure") == "meitantei-precure"
    assert erai_site.show_slug("Oshi no Ko: Season 2!") == "oshi-no-ko-season-2"


def test_held_releases_are_settled_by_erai_raws(store, monkeypatch):
    erai_site._merge(erai_site.parse_feed(FEED))

    async def no_feed(name):
        return 0

    monkeypatch.setattr(erai_site, "fill_show", no_feed)
    monkeypatch.setattr(erai, "_catalog_entries", lambda state: {})
    state = erai._default_state()
    reason = "Nyaa description does not explicitly list German subtitles"
    state["releases"] = {
        "84b20ca269c0e67680bac521b13c699cc3d031c1": {"status": "held", "title": "[Erai-raws] Meitantei Precure - 35", "reason": reason},
        "1" * 40: {"status": "held", "title": "[Erai-raws] Other Show - 02", "reason": reason},
        "3" * 40: {"status": "held", "title": "[Erai-raws] Unknown - 01", "reason": reason},
    }

    assert asyncio.run(erai._recheck_german_holds(state)) == 2
    releases = state["releases"]
    assert releases["84b20ca269c0e67680bac521b13c699cc3d031c1"]["reason"].startswith("Erai-raws lists German")
    assert releases["1" * 40]["reason"] == "Erai-raws lists no German subtitles"
    assert releases["3" * 40]["reason"] == reason  # Erai-raws has not said
    assert set(erai._load_retry_requests()) == {"84b20ca269c0e67680bac521b13c699cc3d031c1"}


def test_the_whole_feed_link_can_be_pasted_as_the_token():
    from bankai.web.app import _validate_setting_value

    link = "https://www.erai-raws.info/anime-list/x/feed/?token=0123456789abcdef0123456789abcdef&res=1080p&type=torrent"
    assert _validate_setting_value("anime.erai_feed_token", link) == "0123456789abcdef0123456789abcdef"
    assert _validate_setting_value("anime.erai_feed_token", " 0123456789abcdef0123456789abcdef ") == "0123456789abcdef0123456789abcdef"
    with pytest.raises(ValueError):
        _validate_setting_value("anime.erai_feed_token", "not a token!")


def test_a_refused_token_is_reported_without_the_token(store, monkeypatch):
    import httpx

    def refuse(request):
        return httpx.Response(403, request=request)

    monkeypatch.setattr(erai_site, "_client", lambda: httpx.AsyncClient(base_url=erai_site.BASE_URL, transport=httpx.MockTransport(refuse)))
    result = asyncio.run(erai_site.refresh())
    assert "refused the feed token" in result["error"]
    summary = erai_site.summary()
    assert "refused" in summary["error"] and "token=" not in summary["error"]
