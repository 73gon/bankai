"""Review and blacklist cards are AniDB entries; the name keys still serve older decisions."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from bankai.metadata import anidb
from bankai.web import erai


@pytest.mark.parametrize(
    "titles",
    [
        # Different seasons of one show, as they sat in review as separate cards.
        [
            "[Erai-raws] Jidouhanbaiki ni Umarekawatta Ore wa Meikyuu o Samayou S2 - 01 [1080p][HEVC]",
            "[Erai-raws] Jidouhanbaiki ni Umarekawatta Ore wa Meikyuu o Samayou S3 - 01 [1080p][HEVC]",
        ],
        [
            "[Erai-raws] Boku no Hero Academia 5th Season - 01 [1080p HEVC][Multiple Subtitle]",
            "[Erai-raws] Boku no Hero Academia 7th Season - 21 [1080p][HEVC][Multiple Subtitle]",
            "[Erai-raws] Boku no Hero Academia Final Season - 09 [1080p CR WEBRip HEVC AAC]",
        ],
        [
            "[Erai-raws] Kidou Senshi Gundam - Suisei no Majo - 00 ~ 12 [1080p][HEVC][BATCH]",
            "[Erai-raws] Kidou Senshi Gundam - Suisei no Majo Season 2 - 01 [1080p][HEVC]",
        ],
        [
            "[Erai-raws] Yuukoku no Moriarty 2nd Season - 01 [1080p HEVC].mkv",
            "[Erai-raws] Yuukoku no Moriarty Part 2 - 01 [1080p HEVC][Multiple Subtitle].mkv",
        ],
        # One season split in two because a marker threw the parser off.
        [
            "[Erai-raws] Yuru Camp Season 2 - 01 [1080p HEVC][Multiple Subtitle].mkv",
            "[Erai-raws] Yuru Camp Season 2 - 13 END [1080p HEVC][Multiple Subtitle].mkv",
        ],
        [
            "[Erai-raws] Oshi no Ko 2nd Season - 01 [1080p][HEVC][Multiple Subtitle]",
            "[Erai-raws] Oshi no Ko 2nd Season - 01 (Repack) [1080p][HEVC][Multiple Subtitle]",
        ],
        [
            "[Erai-raws] Shingeki no Kyojin - The Final Season - 01 [1080p HEVC][Multiple Sub]",
            "[Erai-raws] Shingeki no Kyojin - The Final Season - 16 END [1080p HEVC][Multiple]",
        ],
    ],
)
def test_every_season_and_variant_of_a_show_shares_one_key(titles):
    assert len({erai._show_key(title) for title in titles}) == 1


def test_different_shows_stay_apart():
    assert erai._show_key("[Erai-raws] Boku no Hero Academia 5th Season - 01 [1080p]") != (
        erai._show_key("[Erai-raws] Vigilante - Boku no Hero Academia Illegals - 01 [1080p]")
    )
    assert erai._show_key("[Erai-raws] Spy x Family - 01 [1080p]") != erai._show_key(
        "[Erai-raws] Oshi no Ko - 01 [1080p]"
    )


TITLES = ET.fromstring(
    """<animetitles>
  <anime aid="17449"><title xml:lang="x-jat" type="main">"Oshi no Ko"</title></anime>
  <anime aid="18086"><title xml:lang="x-jat" type="main">"Oshi no Ko" (2024)</title>
    <title xml:lang="en" type="official">Oshi no Ko Season 2</title></anime>
  <anime aid="16000"><title xml:lang="x-jat" type="main">Spy x Family</title></anime>
</animetitles>"""
)
RECORDS = ET.fromstring(
    """<anime-list>
  <anime anidbid="17449" tvdbid="421069" defaulttvdbseason="1" episodeoffset="0"/>
  <anime anidbid="18086" tvdbid="421069" defaulttvdbseason="2" episodeoffset="0"/>
</anime-list>"""
)


@pytest.fixture()
def held(monkeypatch):
    """Held releases of two seasons of one show, one other show, one unknown name."""
    releases = {
        "a" * 40: {"status": "held", "title": "[Erai-raws] Oshi no Ko - 03 [1080p][HEVC]", "reason": "No German subtitles"},
        "b" * 40: {"status": "held", "title": "[Erai-raws] Oshi no Ko 2nd Season - 01 [1080p][HEVC]", "reason": "No German subtitles"},
        "c" * 40: {"status": "held", "title": "[Erai-raws] Oshi no Ko 2nd Season - 02 (Repack) [1080p][HEVC]", "reason": "No German subtitles"},
        "d" * 40: {"status": "held", "title": "[Erai-raws] Spy x Family - 01 [1080p][HEVC]", "reason": "No German subtitles"},
        "e" * 40: {"status": "held", "title": "[Erai-raws] Nobody Knows This One - 01 [1080p][HEVC]", "reason": "No AniDB anime has this title"},
    }
    state = {"releases": releases, "held": []}
    policies: dict = {}
    saved: dict = {"retries": {}}
    table = anidb.build_index(TITLES, RECORDS)
    monkeypatch.setattr(anidb, "_INDEX", ((0, 0), table))
    monkeypatch.setattr(erai, "_SHOW_KEYS_CACHE", None)
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(erai, "_load_policies", lambda: policies)
    monkeypatch.setattr(erai, "_save_policies", lambda value: policies.update(value))
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai, "_load_retry_requests", lambda: saved["retries"])
    monkeypatch.setattr(erai, "_save_retry_requests", lambda value: saved.update(retries=value))
    monkeypatch.setattr(erai, "_catalog_entries", lambda state: {})

    async def no_cycle(**kwargs):
        return None

    monkeypatch.setattr(erai, "run_cycle", no_cycle)
    return {"state": state, "policies": policies, "table": table}


def test_each_season_is_its_own_card_as_anidb_splits_them(held):
    cards = {card["key"]: card for card in erai.review_items(held["state"], held["table"])}

    assert set(cards) == {"anidb:17449", "anidb:18086", "anidb:16000", erai._mapping_key("[Erai-raws] Nobody Knows This One - 01 [1080p]")}
    assert cards["anidb:17449"]["release_count"] == 1
    # A "(Repack)" variant lands on its season's card, not one of its own.
    assert cards["anidb:18086"]["release_count"] == 2
    assert cards["anidb:18086"]["anidb_title"] == '"Oshi no Ko" (2024)'
    assert cards["anidb:18086"]["english_title"] == "Oshi no Ko Season 2"


def test_the_card_lists_its_entry_s_releases_only(held):
    rows = erai.review_releases("anidb:18086")
    assert [row["episode"] for row in rows] == [1, 2]


def test_discarding_a_season_blocks_that_anidb_entry_only(held):
    import asyncio

    asyncio.run(erai.review_action("b" * 40, "blacklist"))

    assert list(held["policies"]) == ["anidb:18086"]
    assert held["policies"]["anidb:18086"]["anidb_id"] == 18086
    releases = held["state"]["releases"]
    assert releases["b" * 40]["status"] == "blacklisted"
    assert releases["c" * 40]["status"] == "blacklisted"
    # Season 1 is an AniDB entry of its own and stays in review.
    assert releases["a" * 40]["status"] == "held"


def test_a_decision_on_the_card_covers_every_release_of_the_entry(held):
    import asyncio

    asyncio.run(erai.review_action("b" * 40, "allow_german"))

    # Both Erai names of season 2, and nothing of season 1 or Spy x Family.
    assert set(held["policies"]) == {
        erai._mapping_key("[Erai-raws] Oshi no Ko 2nd Season - 01 [1080p][HEVC]"),
        erai._mapping_key("[Erai-raws] Oshi no Ko 2nd Season - 02 (Repack) [1080p][HEVC]"),
    }


def test_one_entry_blocked_twice_is_one_blacklist_card(held):
    held["policies"].update(
        {
            "anidb:18086": {"mode": "blacklisted", "anidb_id": 18086, "anidb_title": '"Oshi no Ko" (2024)'},
            "oshi no ko 2nd season": {"mode": "blacklisted", "anidb_id": 18086, "source_title": "Oshi no Ko 2nd Season"},
            "anidb:17449": {"mode": "blacklisted", "anidb_id": 17449, "anidb_title": '"Oshi no Ko"'},
        }
    )
    cards = {card["key"]: card for card in erai.blacklist_items()}
    assert set(cards) == {"anidb:18086", "anidb:17449"}
    assert sorted(cards["anidb:18086"]["keys"]) == ["anidb:18086", "oshi no ko 2nd season"]


def test_restoring_an_entry_brings_back_its_releases_only(held, monkeypatch):
    held["policies"].update(
        {
            "anidb:18086": {"mode": "blacklisted", "anidb_id": 18086},
            "anidb:17449": {"mode": "blacklisted", "anidb_id": 17449},
        }
    )
    releases = held["state"]["releases"]
    for info_hash in ("a" * 40, "b" * 40):
        releases[info_hash]["status"] = "blacklisted"
    monkeypatch.setattr(erai, "_hold", lambda state, entry, reason: None)
    monkeypatch.setattr(erai, "_request_series_retries", lambda state, key: 0)
    popped = []
    real_save = erai._save_policies
    monkeypatch.setattr(erai, "_save_policies", lambda value: popped.append(sorted(value)) or real_save(value))

    erai.remove_blacklist("anidb:18086")

    assert releases["b" * 40]["status"] == "held"
    assert releases["a" * 40]["status"] == "blacklisted"
    assert popped[-1] == ["anidb:17449"]
