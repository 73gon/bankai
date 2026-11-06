"""The Erai-raws search's suggestions: show names from the feeds read, other names leading to them."""

from __future__ import annotations

from bankai.metadata import offline
from bankai.web import anime, erai_site


def test_a_releases_title_without_episodes_and_marks_is_its_show():
    assert erai_site.show_name("Kaguya 3 - 01 ~ 13 (HEVC)") == "Kaguya 3"
    assert erai_site.show_name("Sousou no Frieren 2nd Season - 05") == "Sousou no Frieren 2nd Season"
    assert erai_site.show_name("Re:Zero kara Hajimeru Isekai Seikatsu - Movie") == "Re:Zero kara Hajimeru Isekai Seikatsu"
    assert erai_site.show_name("Dan Da Dan (HEVC)") == "Dan Da Dan"


def test_suggestions_start_with_the_query_then_hold_it_and_other_names_lead_to_erais(monkeypatch):
    erai_site._SHOWS = None
    erai_site._save({
        **erai_site._empty(),
        "releases": {
            "a": {"title": "Sousou no Frieren - 01", "published": 10},
            "b": {"title": "Sousou no Frieren - 02 (HEVC)", "published": 20},
            "c": {"title": "Ore dake Level Up na Ken - 01", "published": 5},
            "d": {"title": "Frieren Kaiser - 01", "published": 1},
        },
    })
    monkeypatch.setattr(offline, "entries", lambda: {
        1: {"title": "Sousou no Frieren", "synonyms": ["Frieren: Beyond Journey's End", "Frieren"]},
        2: {"title": "Ore dake Level Up na Ken", "synonyms": ["Solo Leveling"]},
    })
    # Both start with it (Frieren by its other name): the newer first.
    names = [row["name"] for row in erai_site.suggest("frie")]
    assert names == ["Sousou no Frieren", "Frieren Kaiser"]
    assert [row["name"] for row in erai_site.suggest("kai")] == ["Frieren Kaiser"]
    frieren = erai_site.suggest("frie")[0]
    assert frieren["releases"] == 2 and frieren["via"] == "Frieren"
    assert erai_site.suggest("solo lev") == [{"name": "Ore dake Level Up na Ken", "releases": 1, "newest": 5.0, "via": "Solo Leveling"}]
    assert erai_site.suggest("f") == []


def test_a_magnet_carries_the_hash_name_and_nyaas_trackers():
    link = anime.magnet_uri("abc123", "[Erai-raws] Show - 01")
    assert link.startswith("magnet:?xt=urn:btih:abc123&dn=%5BErai-raws%5D%20Show%20-%2001&tr=")
