from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from bankai.metadata import anilist, jikan

BLEACH, BLEACH_MAL = 269, 269
# An entry no other test keeps, so AniList is really asked about it.
UNKNOWN = 990_001
# The real one: every test's AniList is stubbed out of the network (conftest); this one fakes the network instead.
REAL_ENSURE = anilist.ensure


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


@respx.mock
def test_anilist_saying_why_it_refuses_is_kept_for_the_dashboard(state, monkeypatch) -> None:
    """"Temporarily disabled" read as "nothing to do" for an hour: it is kept, and cleared by the next answer."""
    monkeypatch.setattr(anilist, "last_error", {})
    route = respx.post(anilist._API)
    route.mock(return_value=httpx.Response(403, json={
        "errors": [{"message": "The AniList API has been temporarily disabled due to severe stability issues.", "status": 403}],
        "data": None,
    }))
    assert asyncio.run(REAL_ENSURE([UNKNOWN], max_batches=1)) == 0
    assert anilist.last_error["message"] == "403: The AniList API has been temporarily disabled due to severe stability issues."
    since = anilist.last_error["since"]
    asyncio.run(REAL_ENSURE([UNKNOWN], max_batches=1))
    assert anilist.last_error["since"] == since  # when it began, not the latest try

    route.mock(return_value=httpx.Response(200, json={"data": {"Page": {"media": []}}}))
    asyncio.run(REAL_ENSURE([UNKNOWN], max_batches=1))
    assert anilist.last_error == {}


def _mal_html(numbers: range) -> str:
    """MyAnimeList's episode list, as its rows are written."""
    rows = "".join(
        f'<tr class="episode-list-data">\n'
        f'  <td class="episode-number nowrap" data-raw="{n}">{n}</td>\n'
        f'  <td class="episode-video nowrap"></td>\n'
        f'  <td class="episode-title fs12">'
        # Filler episodes carry that label before the link.
        + ('<span class="fl-r di-ib pr4 icon-episode-type-bg">Filler</span>' if n % 3 == 0 else '')
        + f'<a href="https://myanimelist.net/anime/{BLEACH_MAL}/x/episode/{n}" '
        f'class="fl-l fw-b ">Title {n} &amp; more</a>\n    <br><span class="di-ib"></span>\n  </td>\n'
        f'  <td class="episode-aired nowrap">Oct 11, 2022</td></tr>'
        for n in numbers
    )
    return f"<table><tbody>{rows}</tbody></table>"


@respx.mock
def test_with_jikan_unreachable_the_titles_come_from_myanimelist_itself(state) -> None:
    """Jikan's API timed out on every pass, so no title was ever kept."""
    respx.get(f"{jikan._API}/anime/{BLEACH_MAL}/episodes").mock(side_effect=httpx.ConnectTimeout(""))
    mal = respx.get(jikan._MAL.format(mal_id=BLEACH_MAL))
    mal.side_effect = lambda request: httpx.Response(
        200,
        text={"0": _mal_html(range(1, 101)), "100": _mal_html(range(101, 131))}[request.url.params["offset"]],
    )

    assert asyncio.run(jikan.ensure([(BLEACH_MAL, True)])) == 1

    titles = jikan.titles(BLEACH_MAL)
    assert len(titles) == 130
    assert titles[1] == "Title 1 & more" and titles[130] == "Title 130 & more"
    assert not jikan.last_error


@respx.mock
def test_a_timeout_is_named_for_the_dashboard(state) -> None:
    respx.get(f"{jikan._API}/anime/{BLEACH_MAL}/episodes").mock(side_effect=httpx.ConnectTimeout(""))
    respx.get(jikan._MAL.format(mal_id=BLEACH_MAL)).mock(side_effect=httpx.ConnectTimeout(""))

    assert asyncio.run(jikan.ensure([(BLEACH_MAL, True)])) == 0
    assert jikan.last_error["message"] == "ConnectTimeout"
    jikan.last_error.clear()


@respx.mock
def test_a_pass_never_drops_what_another_process_kept_meanwhile(state) -> None:
    """The scheduler's pass wrote back the file as it had read it, over a catch-up's 205 entries."""
    other = 4242

    def answer(request):
        # While this pass is asking, another process keeps an entry of its own.
        jikan._save({other: {"titles": {"1": "Kept elsewhere"}, "fetched_at": 1e12}})
        return httpx.Response(200, json=_page(range(1, 4), False))

    respx.get(f"{jikan._API}/anime/{BLEACH_MAL}/episodes").mock(side_effect=answer)

    assert asyncio.run(jikan.ensure([(BLEACH_MAL, True)])) == 1
    assert jikan.titles(other) == {1: "Kept elsewhere"}
    assert len(jikan.titles(BLEACH_MAL)) == 3


@respx.mock
def test_an_airing_show_myanimelist_counts_ahead_of_anilist_keeps_its_titles(state, anilist_data) -> None:
    """One Piece: MyAnimeList listed 1180 episodes, AniList counted 1168, and every title was dropped."""
    piece, piece_mal = 21, 21
    anilist_data.add(piece, "One Piece", english="One Piece", episodes=4, mal_id=piece_mal)
    respx.get(f"{jikan._API}/anime/{piece_mal}/episodes").mock(
        return_value=httpx.Response(200, json=_page(range(1, 7), False))
    )
    asyncio.run(jikan.ensure([(piece_mal, False)]))

    titles = [title for _number, title, _aired in anilist.episodes(piece)]
    assert titles == [f"Episode title {n}" for n in range(1, 5)]
    assert not anilist.untitled(piece)
