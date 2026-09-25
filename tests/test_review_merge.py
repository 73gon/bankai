"""One review card per anime, every season of it merged."""

from __future__ import annotations

import pytest

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


@pytest.fixture()
def held(monkeypatch):
    """Held releases of two seasons of one show and one other show."""
    releases = {
        "a" * 40: {"status": "held", "title": "[Erai-raws] Oshi no Ko - 03 [1080p][HEVC]", "reason": "No German subtitles"},
        "b" * 40: {"status": "held", "title": "[Erai-raws] Oshi no Ko 2nd Season - 01 [1080p][HEVC]", "reason": "No German subtitles"},
        "c" * 40: {"status": "held", "title": "[Erai-raws] Oshi no Ko 2nd Season - 02 [1080p][HEVC]", "reason": "TVDB match is ambiguous"},
        "d" * 40: {"status": "held", "title": "[Erai-raws] Spy x Family - 01 [1080p][HEVC]", "reason": "No German subtitles"},
    }
    state = {"releases": releases, "held": []}
    policies: dict = {}
    saved: dict = {"retries": {}}
    monkeypatch.setattr(erai, "_load_state", lambda: state)
    monkeypatch.setattr(erai, "_save_state", lambda value: None)
    monkeypatch.setattr(erai, "_load_policies", lambda: policies)
    monkeypatch.setattr(erai, "_save_policies", lambda value: policies.update(value))
    monkeypatch.setattr(erai, "_load_mappings", lambda: {})
    monkeypatch.setattr(erai, "_load_retry_requests", lambda: saved["retries"])
    monkeypatch.setattr(erai, "_save_retry_requests", lambda value: saved.update(retries=value))
    monkeypatch.setattr(erai, "_catalog_entries", lambda state: {})
    return {"state": state, "policies": policies, "saved": saved}


def test_two_seasons_are_one_card(held):
    cards = erai.review_items(held["state"])

    assert [card["source_title"] for card in cards] == ["Oshi no Ko", "Spy x Family"]
    oshi = cards[0]
    assert oshi["release_count"] == 3
    assert oshi["seasons"] == [2]
    assert set(oshi["reasons"]) == {"No German subtitles", "TVDB match is ambiguous"}
    assert len(oshi["keys"]) == 2


def test_the_card_lists_every_season_s_releases(held):
    key = erai.review_items(held["state"])[0]["key"]
    rows = erai.review_releases(key)
    # Season-less first episode last, the numbered season in order.
    assert [(row["season"], row["episode"]) for row in rows] == [(2, 1), (2, 2), (None, 3)]


def test_a_decision_on_the_card_covers_every_season(held, monkeypatch):
    import asyncio

    async def no_cycle(**kwargs):
        return None

    # The decision schedules a retry cycle; that is the automation's business.
    monkeypatch.setattr(erai, "run_cycle", no_cycle)
    asyncio.run(erai.review_action("b" * 40, "allow_german"))

    assert {row["mode"] for row in held["policies"].values()} == {"german_allowed"}
    assert set(held["policies"]) == {
        erai._mapping_key("[Erai-raws] Oshi no Ko - 03 [1080p][HEVC]"),
        erai._mapping_key("[Erai-raws] Oshi no Ko 2nd Season - 01 [1080p][HEVC]"),
    }
    # Spy x Family is another show and was not touched.
    assert erai._mapping_key("[Erai-raws] Spy x Family - 01 [1080p][HEVC]") not in held["policies"]


def test_a_show_discarded_across_seasons_is_one_blacklist_card(held):
    held["policies"].update(
        {
            "oshi no ko": {"mode": "blacklisted", "source_title": "Oshi no Ko"},
            "oshi no ko 2nd season": {"mode": "blacklisted", "source_title": "Oshi no Ko 2nd Season"},
            "spy x family": {"mode": "blacklisted", "source_title": "Spy x Family"},
        }
    )
    cards = erai.blacklist_items()

    assert [card["source_title"] for card in cards] == ["Oshi no Ko", "Spy x Family"]
    assert sorted(cards[0]["keys"]) == ["oshi no ko", "oshi no ko 2nd season"]


def test_restoring_the_card_restores_every_season(held, monkeypatch):
    policies = {
        "oshi no ko": {"mode": "blacklisted", "source_title": "Oshi no Ko"},
        "oshi no ko 2nd season": {"mode": "blacklisted", "source_title": "Oshi no Ko 2nd Season"},
        "spy x family": {"mode": "blacklisted", "source_title": "Spy x Family"},
    }
    saved: list[dict] = []
    monkeypatch.setattr(erai, "_load_policies", lambda: dict(policies))
    monkeypatch.setattr(erai, "_save_policies", lambda value: saved.append(dict(value)))
    # The app calls this inside its event loop, where the retry cycle it
    # schedules can run; here there are no held releases to retry anyway.
    monkeypatch.setattr(erai, "_request_series_retries", lambda state, key: 0)

    key = erai.blacklist_items()[0]["key"]
    erai.remove_blacklist(key)

    # Both seasons came off the blacklist; the other show stayed on it.
    assert list(saved[-1]) == ["spy x family"]
