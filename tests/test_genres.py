"""bankai's curated genres: the rules engine, its checks and its stores."""

from __future__ import annotations

import pytest

from bankai.web import genres


@pytest.fixture()
def rules():
    return genres.check(genres.default_rules())


def names(rules, **kwargs):
    return genres.resolve(rules, **kwargs).names(rules)


def test_duplicates_and_german_leftovers_become_one_genre(rules):
    assert names(rules, genres=["Sci-Fi", "Science Fiction", "Komödie", "Comedy", "Liebesfilm"]) == [
        "Science Fiction", "Romance", "Comedy",
    ]
    assert names(rules, genres=["Sport", "Sports", "Children", "Family", "Suspense"]) == ["Sports", "Thriller", "Family & Kids"]


def test_specific_comes_before_broad(rules):
    result = genres.resolve(rules, genres=["Action", "Science Fiction", "Adventure"], tags=["superhero"])
    assert result.names(rules) == ["Superheroes", "Science Fiction", "Adventure", "Action"]
    assert result.sources["superheroes"] == [("tag", "superhero")]


def test_keyword_tags_and_the_ones_left_out(rules):
    assert names(rules, tags=["based on fairy tale"]) == ["Fairy Tales"]
    assert names(rules, tags=["psychological thriller"]) == ["Psychological", "Thriller"]
    assert names(rules, tags=["World War II", "heist", "martial arts"]) == ["History & War", "Crime", "Action"]
    assert names(rules, tags=["christmas", "ghost", "supernatural", "based on comic"]) == []


def test_content_flags_are_dropped_and_unknown_raw_genres_are_listed(rules):
    result = genres.resolve(rules, genres=["Ecchi", "Hentai", "Adapted From A Manga", "Shounen", "Drama"])
    assert result.names(rules) == ["Drama"]
    assert result.unmapped == ["Adapted From A Manga", "Shounen"]
    assert result.discarded == ["Ecchi", "Hentai"]


def test_the_anime_library_is_anime_and_anime_is_not_animation_too(rules):
    assert names(rules, genres=["Action", "Mecha", "Mahou Shoujo"], library="anime") == ["Science Fiction", "Fantasy", "Action", "Anime"]
    ghibli = genres.resolve(rules, genres=["Animation", "Fantasy"], tags=["anime"])
    assert ghibli.names(rules) == ["Fantasy", "Anime"]
    assert ghibli.dropped == {"animation": [("yields", "anime")]}
    assert names(rules, genres=["Animation", "Family"]) == ["Family & Kids", "Animation"]


def test_a_title_s_hand_choices_win(rules):
    # The LEGO Movie is no superhero film, whatever its keywords say.
    result = genres.resolve(rules, genres=["Animation", "Comedy"], tags=["superhero"], override={"remove": ["superheroes"], "add": ["family_kids"]})
    assert result.names(rules) == ["Comedy", "Family & Kids", "Animation"]
    assert result.sources["family_kids"] == [("added", "")]
    assert result.dropped == {"superheroes": [("removed", "")]}


def test_names_come_in_the_language_chosen(rules):
    result = genres.resolve(rules, genres=["Comedy", "Adventure"], tags=["fairy tale"])
    assert result.names(rules, "de") == ["Märchen", "Komödie", "Abenteuer"]
    assert result.names({**rules, "language": "de"}) == ["Märchen", "Komödie", "Abenteuer"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r["vocabulary"][0].update(en="Action/Adventure"), "cannot hold"),
        (lambda r: r["vocabulary"][0].update(de="Krimi|Thriller"), "cannot hold"),
        (lambda r: r["vocabulary"][1].update(id="superheroes"), "twice"),
        (lambda r: r["vocabulary"][1].update(en="Superheroes"), "twice"),
        (lambda r: r["vocabulary"][0].update(de=""), "no de name"),
        (lambda r: r["tags"].update({"spy": ["spies"]}), "no genre"),
        (lambda r: r.update(language="fr"), "language"),
    ],
)
def test_rules_that_cannot_be_kept_are_refused(change, message):
    raw = genres.default_rules()
    change(raw)
    with pytest.raises(genres.RulesError, match=message):
        genres.check(raw)


def test_mappings_are_kept_casefolded():
    raw = genres.default_rules()
    raw["genres"]["  Science   FICTION "] = ["science_fiction"]
    assert "science fiction" in genres.check(raw)["genres"]


def test_a_title_s_key_is_its_provider_id_never_jellyfin_s():
    assert genres.title_key("Movie", {"Tmdb": "1726", "Imdb": "tt0371746"}) == "tmdb:movie:1726"
    assert genres.title_key("Series", {"Tmdb": "1399", "Tvdb": "121361"}) == "tmdb:tv:1399"
    assert genres.title_key("Series", {"Tvdb": "121361"}) == "tvdb:tv:121361"
    assert genres.title_key("Movie", {"Imdb": "tt0371746"}) == "imdb:tt0371746"
    assert genres.title_key("Series", {"AniDB": "9541"}) is None
    assert genres.anime_key(41) == "anime:41"


def test_rules_and_overrides_are_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    raw = genres.default_rules()
    raw["language"] = "de"
    raw["vocabulary"].append({"id": "spy", "en": "Spy", "de": "Agenten"})
    raw["tags"]["spy"] = ["spy"]
    genres.save_rules(raw)

    assert genres.rules()["language"] == "de"
    genres.save_override("tmdb:movie:137106", add=[], remove=["superheroes"])
    assert genres.overrides() == {"tmdb:movie:137106": {"add": [], "remove": ["superheroes"]}}
    with pytest.raises(genres.RulesError, match="no such genre"):
        genres.save_override("tmdb:movie:1", add=["spies"], remove=[])
    genres.save_override("tmdb:movie:137106", add=[], remove=[])
    assert genres.overrides() == {}


def test_raw_values_are_kept_until_bankai_writes_a_title(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    genres.keep_sources({"tmdb:movie:1": {"genres": ["Action"]}}, written=[])
    # Written: its Genres are bankai's now, so Jellyfin's are no source any more.
    genres.keep_sources({"tmdb:movie:1": {"genres": ["Superheroes"]}, "tmdb:movie:2": {"genres": ["Drama"]}}, written=["tmdb:movie:1"])
    assert genres.sources() == {"tmdb:movie:1": {"genres": ["Action"]}, "tmdb:movie:2": {"genres": ["Drama"]}}


def test_a_fingerprint_changes_with_the_rules_and_the_title_s_choices(rules):
    first = genres.fingerprint(rules, None)
    assert genres.fingerprint(rules, {"add": ["sports"]}) != first
    changed = genres.check({**genres.default_rules(), "language": "de"})
    assert genres.fingerprint(changed, None) != first


def test_tmdb_s_genres_are_the_raw_source_and_jellyfin_s_stand_in_where_it_has_none(tmp_path, monkeypatch):
    import asyncio

    from bankai.metadata import tmdb
    from bankai.web import genre_sync, jellyfin

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    catalog = [
        # Matched to the wrong TVDB entry: Jellyfin has its stunt documentary's genre.
        {"item_id": "a", "type": "Movie", "name": "Avatar: The Way of Water", "year": 2022, "library": "Filme",
         "genres": ["Documentary"], "tags": [], "provider_ids": {"Tmdb": "76600"}, "locked_fields": [], "path": None},
        {"item_id": "c", "type": "Movie", "name": "Chappie", "year": 2015, "library": "Filme",
         "genres": ["Action", "Crime"], "tags": [], "provider_ids": {"Tmdb": "198184"}, "locked_fields": [], "path": None},
    ]

    async def listing():
        return catalog

    async def ensure(items, **_kwargs):
        return 0

    monkeypatch.setattr(jellyfin, "catalog", listing)
    monkeypatch.setattr(genre_sync, "_CATALOG", None)
    monkeypatch.setattr(genre_sync, "_anime_titles", lambda: [])
    monkeypatch.setattr(tmdb, "ensure_genres", ensure)
    monkeypatch.setattr(tmdb, "genres", lambda kind, tmdb_id: ["Science Fiction", "Adventure", "Action"] if tmdb_id == "76600" else None)

    plan = asyncio.run(genre_sync.plan())

    rows = {row["name"]: row for row in plan["titles"]}
    assert (rows["Avatar: The Way of Water"]["raw_source"], rows["Avatar: The Way of Water"]["after"]) == (
        "TMDB", ["Science Fiction", "Adventure", "Action"],
    )
    assert (rows["Chappie"]["raw_source"], rows["Chappie"]["after"]) == ("Jellyfin", ["Crime", "Action"])


def test_a_genre_jellyfin_had_and_a_click_kept_is_jellyfin_s_own_not_added_by_hand(tmp_path, monkeypatch):
    """Clicked back from the struck-through list, Avatar's Documentary was shown blue, "added by hand"."""
    import asyncio

    from bankai.metadata import tmdb
    from bankai.web import genre_sync, jellyfin

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    catalog = [
        {"item_id": "a", "type": "Movie", "name": "Avatar: The Way of Water", "year": 2022, "library": "Filme",
         "genres": ["Documentary"], "tags": [], "provider_ids": {"Tmdb": "76600"}, "locked_fields": [], "path": None},
    ]

    async def listing():
        return catalog

    async def ensure(items, **_kwargs):
        return 0

    monkeypatch.setattr(jellyfin, "catalog", listing)
    monkeypatch.setattr(genre_sync, "_CATALOG", None)
    monkeypatch.setattr(genre_sync, "_anime_titles", lambda: [])
    monkeypatch.setattr(tmdb, "ensure_genres", ensure)
    monkeypatch.setattr(tmdb, "genres", lambda kind, tmdb_id: ["Science Fiction", "Adventure"])

    row = asyncio.run(genre_sync.plan())["titles"][0]
    assert (row["jellyfin_only"], row["kept_from_jellyfin"]) == (["documentary"], [])
    genres.save_override("tmdb:movie:76600", ["documentary", "action"], [])
    monkeypatch.setattr(genre_sync, "_CATALOG", None)
    row = asyncio.run(genre_sync.plan())["titles"][0]
    # Action is added by hand; Documentary is the title's own, kept.
    assert (row["jellyfin_only"], row["kept_from_jellyfin"]) == ([], ["documentary"])


def _jellyfin(monkeypatch, items):
    """A Jellyfin answering /Users, GET /Items/{id} and POST /Items/{id}; posts are kept."""
    import json as jsonlib

    import httpx

    from bankai.web import jellyfin

    posted = []

    def handler(request):
        if request.url.path == "/Users":
            return httpx.Response(200, json=[{"Id": "u1", "Policy": {"IsAdministrator": True}}])
        item_id = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            assert request.url.params.get("userId") == "u1"
            return httpx.Response(200, json=items[item_id])
        body = jsonlib.loads(request.content)
        posted.append(body)
        items[item_id] = body
        return httpx.Response(204)

    monkeypatch.setattr(jellyfin, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://jf"))
    return posted


def _title(key, item_id, before, after, locked=False):
    return {"key": key, "item_id": item_id, "name": key, "before": before, "after": after, "locked": locked}


def test_a_title_is_written_whole_with_its_genres_locked_and_undone_to_what_it_was(tmp_path, monkeypatch):
    import asyncio

    from bankai.web import genre_sync

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    item = {"Id": "a", "Name": "Iron Man", "Overview": "...", "Tags": ["superhero"], "ProviderIds": {"Tmdb": "1726"},
            "Genres": ["Action", "Science Fiction", "Adventure"], "LockedFields": ["Overview"], "DisplayOrder": None,
            "Trickplay": {"a": {"320": {"Width": 320}}}}
    posted = _jellyfin(monkeypatch, {"a": dict(item)})
    after = ["Superheroes", "Science Fiction", "Adventure", "Action"]

    async def plan(**_kwargs):
        return {"titles": [_title("tmdb:movie:1726", "a", item["Genres"], after)]}

    monkeypatch.setattr(genre_sync, "plan", plan)
    result = asyncio.run(genre_sync.apply(keys={"tmdb:movie:1726"}, limit=None))

    assert result["written"] == 1
    # The whole item went back: only Genres and LockedFields differ -- and Trickplay, read only,
    # which Jellyfin cannot read back, stays out.
    assert "Trickplay" not in posted[0]
    assert {key for key in item if key != "Trickplay" and posted[0][key] != item[key]} == {"Genres", "LockedFields"}
    assert (posted[0]["Genres"], posted[0]["LockedFields"]) == (after, ["Overview", "Genres"])

    assert asyncio.run(genre_sync.undo("tmdb:movie:1726")) == 1
    assert (posted[1]["Genres"], posted[1]["LockedFields"]) == (item["Genres"], ["Overview"])
    assert "tmdb:movie:1726" not in genres.state()


def test_an_item_without_its_tags_or_provider_ids_is_never_posted(tmp_path, monkeypatch):
    import asyncio

    from bankai.web import genre_sync

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    posted = _jellyfin(monkeypatch, {"a": {"Id": "a", "Name": "Broken", "Tags": None, "ProviderIds": {}, "LockedFields": []}})

    async def plan(**_kwargs):
        return {"titles": [_title("tmdb:movie:1", "a", ["Drama"], ["Comedy"])]}

    monkeypatch.setattr(genre_sync, "plan", plan)
    result = asyncio.run(genre_sync.apply(keys={"tmdb:movie:1"}, limit=None))

    assert (result["written"], posted) == (0, [])
    assert "without Tags" in result["failed"][0]


def test_nothing_is_written_while_writing_is_off_and_a_written_title_is_left_alone(tmp_path, monkeypatch):
    import asyncio

    from bankai.web import genre_sync

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    posted = _jellyfin(monkeypatch, {"a": {"Id": "a", "Name": "A", "Tags": [], "ProviderIds": {}, "LockedFields": ["Genres"]}})

    async def plan(**_kwargs):
        return {"titles": [_title("tmdb:movie:1", "a", ["Comedy"], ["Comedy"], locked=True)]}

    monkeypatch.setattr(genre_sync, "plan", plan)
    assert asyncio.run(genre_sync.apply()) == {"idle": "Writing movies and series is off"}
    genre_sync.switch(movies=True)
    assert asyncio.run(genre_sync.apply())["written"] == 0
    assert posted == []


def test_anime_get_their_curated_genres_only_where_switched_on(tmp_path, monkeypatch):
    from bankai.web import genre_sync

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert genre_sync.anime_genres(41, ["Romance", "Comedy", "Ecchi"]) == ["Romance", "Comedy", "Ecchi"]
    genre_sync.switch(anime_cids=[41])
    assert genre_sync.anime_genres(41, ["Romance", "Comedy", "Ecchi"]) == ["Romance", "Comedy", "Anime"]
    assert genre_sync.anime_genres(42, ["Romance"]) == ["Romance"]
