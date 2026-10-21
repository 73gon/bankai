from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from bankai.metadata import anilist, jikan

BLEACH, BLEACH_MAL = 269, 269


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(jikan, "_STORE", None)
    monkeypatch.setattr(jikan, "_PAUSE", 0)
    return tmp_path


def _page(numbers: range, more: bool) -> dict:
    return {
        "pagination": {"has_next_page": more},
        "data": [{"mal_id": n, "title": f"Episode title {n}"} for n in numbers],
    }


@respx.mock
def test_titles_come_page_by_page_and_a_long_show_goes_on_next_pass(state) -> None:
    route = respx.get(f"{jikan._API}/anime/{BLEACH_MAL}/episodes")
    route.side_effect = lambda request: httpx.Response(
        200,
        json={
            "1": _page(range(1, 101), True),
            "2": _page(range(101, 201), True),
            "3": _page(range(201, 301), True),
            "4": _page(range(301, 367), False),
        }[request.url.params["page"]],
    )

    assert asyncio.run(jikan.ensure([(BLEACH_MAL, True)], max_requests=2)) == 0
    assert len(jikan.titles(BLEACH_MAL)) == 200  # what it got so far is shown already
    assert jikan.due(BLEACH_MAL, finished=True)

    assert asyncio.run(jikan.ensure([(BLEACH_MAL, True)], max_requests=8)) == 1
    titles = jikan.titles(BLEACH_MAL)
    assert len(titles) == 366 and titles[366] == "Episode title 366"
    assert [request.url.params["page"] for request, _ in route.calls] == ["1", "2", "3", "4"]
    assert not jikan.due(BLEACH_MAL, finished=True)
    # Kept: nothing is asked again.
    assert asyncio.run(jikan.ensure([(BLEACH_MAL, True)])) == 0
    assert route.call_count == 4


@respx.mock
def test_an_entry_myanimelist_does_not_know_is_kept_as_known_empty(state) -> None:
    respx.get(f"{jikan._API}/anime/1/episodes").mock(return_value=httpx.Response(404))
    assert asyncio.run(jikan.ensure([(1, True)])) == 1
    assert jikan.titles(1) == {} and not jikan.due(1, finished=True)


@respx.mock
def test_myanimelist_names_the_episodes_the_streaming_sites_call_untitled(state, anilist_data) -> None:
    anilist_data.add(BLEACH, "Bleach", english="Bleach", episodes=3, mal_id=BLEACH_MAL,
                     streaming={1: "Untitled", 2: "Untitled", 3: "A Real Streaming Title"})
    assert anilist.untitled(BLEACH)
    respx.get(f"{jikan._API}/anime/{BLEACH_MAL}/episodes").mock(
        return_value=httpx.Response(200, json=_page(range(1, 4), False))
    )
    asyncio.run(jikan.ensure([(BLEACH_MAL, True)]))

    titles = [title for _number, title, _aired in anilist.episodes(BLEACH)]
    # The streaming site's own title stays first; MyAnimeList fills the rest.
    assert titles == ["Episode title 1", "Episode title 2", "A Real Streaming Title"]
    assert not anilist.untitled(BLEACH)
