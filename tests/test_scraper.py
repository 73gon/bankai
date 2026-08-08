"""Tests for scraper backends and the registry."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from bankai.queue.models import MediaKind
from bankai.scraper import all_backends, get_backend
from bankai.scraper.backends.bs_to import BsToBackend, BurningSeriesBackend
from bankai.scraper.backends.filmpalast import FilmpalastBackend
from bankai.scraper.base import ScraperBackend

FIXTURES = Path(__file__).parent / "fixtures"


def test_registry_lists_scraper_backends() -> None:
    backends = all_backends()
    for site_id in ("filmpalast", "kinox", "burningseries", "bs.to", "aniworld"):
        assert site_id in backends
        cls = get_backend(site_id)
        # Protocol conformance is structural â€” check at least the key members.
        assert hasattr(cls, "search")
        assert hasattr(cls, "resolve_stream")


def test_filmpalast_class_satisfies_protocol() -> None:
    assert isinstance(FilmpalastBackend(base_url="http://example.invalid"), ScraperBackend)


def test_burningseries_class_satisfies_protocol() -> None:
    assert isinstance(BurningSeriesBackend(base_url="http://example.invalid"), ScraperBackend)
    assert BurningSeriesBackend.supports_series is True
    assert BurningSeriesBackend.supports_movies is False
    assert BsToBackend.supports_series is False


@pytest.mark.asyncio
async def test_burningseries_search_uses_current_domain_and_deduplicates_index() -> None:
    html = (FIXTURES / "burningseries" / "series_index.html").read_text(encoding="utf-8")
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    BurningSeriesBackend._index_cache.clear()
    backend = BurningSeriesBackend(base_url="https://burningseries.ac")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="https://burningseries.ac",
        transport=httpx.MockTransport(handler),
    )
    try:
        results = await backend.search("Arcane", kind=MediaKind.EPISODE)
        movie_results = await backend.search("Arcane", kind=MediaKind.MOVIE)
    finally:
        await backend.aclose()

    assert requested_paths == ["/andere-serien"]
    assert len(results) == 1
    assert results[0].site == "burningseries"
    assert results[0].title == "Arcane | League of Legends"
    assert results[0].url == "https://burningseries.ac/serie/Arcane-League-of-Legends"
    assert movie_results == []


@pytest.mark.asyncio
async def test_burningseries_lists_only_german_season_episodes() -> None:
    html = (FIXTURES / "burningseries" / "season_arcane_de.html").read_text(encoding="utf-8")
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    BurningSeriesBackend._index_cache["http://example.invalid"] = (
        time.time(),
        [("Arcane | League of Legends", "Arcane-League-of-Legends")],
    )
    backend = BurningSeriesBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(handler),
    )
    try:
        episodes = await backend.list_season("Arcane", 2)
    finally:
        await backend.aclose()

    assert requested_paths == ["/serie/Arcane-League-of-Legends/2/de"]
    assert [(ep.season, ep.episode) for ep in episodes] == [(2, 1), (2, 2)]
    assert all(ep.language == "ger" and ep.url.endswith("/de") for ep in episodes)
    assert episodes[0].title == "Die Last der Krone"


@pytest.mark.asyncio
async def test_burningseries_resolve_prefers_voe_wrapper() -> None:
    html = (FIXTURES / "burningseries" / "episode_arcane_de.html").read_text(encoding="utf-8")
    backend = BurningSeriesBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
        ),
    )
    try:
        handle = await backend.resolve_stream(
            "http://example.invalid/serie/Arcane-League-of-Legends/2/1-Die-Last-der-Krone/de"
        )
    finally:
        await backend.aclose()

    assert handle.site == "burningseries"
    assert handle.url.endswith("/de/VOE")
    assert handle.hint == "playwright"


@pytest.mark.asyncio
async def test_filmpalast_search_parses_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / "filmpalast" / "search_inception.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/stream/"):
            year = request.url.path.rsplit("-", 1)[-1]
            return httpx.Response(
                200,
                text=(
                    f'<span id="release_text">{request.url.path[8:]}.GERMAN.1080p.WEB.H264</span>'
                    f'<li>Veröffentlicht: {year}</li>'
                ),
            )
        assert request.url.path.startswith("/search/title/")
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    # Swap the underlying client for one using our mock transport.
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        results = await backend.search("Inception", kind=MediaKind.MOVIE)
        results = await backend.enrich_search_results(results)
    finally:
        await backend.aclose()

    assert len(results) == 3
    titles = [r.title for r in results]
    assert "Inception (2010)" in titles
    assert "Interstellar (2014)" in titles
    inception = results[0]
    assert inception.year == 2010
    assert inception.url == "http://example.invalid/stream/inception-2010"
    assert inception.poster_url == "http://example.invalid/media/cover/inception.jpg"
    assert inception.kind is MediaKind.MOVIE
    assert inception.site == "filmpalast"
    assert inception.release_name == "inception-2010.GERMAN.1080p.WEB.H264"


@pytest.mark.asyncio
async def test_filmpalast_recent_groups_three_pages_and_extracts_release() -> None:
    requested_paths: list[str] = []

    def listing(page: int, *, next_page: int | None) -> str:
        next_link = f'<a href="/page/{next_page}">vorwärts +</a>' if next_page else ""
        return f"""
        <article class="liste rb">
          <a class="rb" href="/stream/movie-{page}">
            <img data-src="/cover/{page}.jpg"><h2>Movie {page}</h2>
          </a>
          <span>Release: Movie.{page}.2026.GERMAN.TELESYNC.1080p.X264-GROUP</span>
          <span>Jahr: 2026 / Spielzeit: 89 min</span>
        </article>
        {next_link}
        """

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        page = 1 if request.url.path == "/" else int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, text=listing(page, next_page=page + 1))

    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid", transport=httpx.MockTransport(handler)
    )
    try:
        results, has_next, source_start, source_end = await backend.recent(1)
    finally:
        await backend.aclose()

    assert requested_paths == ["/page/4", "/page/5", "/page/6"]
    assert (source_start, source_end, has_next) == (4, 6, True)
    assert [result.title for result in results] == ["Movie 4", "Movie 5", "Movie 6"]
    assert results[0].release_name == "Movie.4.2026.GERMAN.TELESYNC.1080p.X264-GROUP"
    assert results[0].poster_url == "http://example.invalid/cover/4.jpg"
    assert results[0].raw["runtime_minutes"] == "89"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("feed", "expected_paths"),
    [
        ("movies", ["/movies/new", "/movies/new/page/2", "/movies/new/page/3"]),
        ("shows", ["/serien/view", "/serien/view/page/2", "/serien/view/page/3"]),
        ("top", ["/movies/top", "/movies/top/page/2", "/movies/top/page/3"]),
    ],
)
async def test_filmpalast_recent_uses_feed_routes(
    feed: str,
    expected_paths: list[str],
) -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, text='<a href="/page/99">next</a>')

    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid", transport=httpx.MockTransport(handler)
    )
    try:
        await backend.recent(0, feed=feed)
    finally:
        await backend.aclose()

    assert requested_paths == expected_paths


@pytest.mark.asyncio
async def test_filmpalast_series_lookup_tries_direct_slug_first() -> None:
    html = """
    <html>
      <body>
        <a href="/stream/arcane-s01e01">Welcome to the Playground</a>
        <a href="/stream/arcane-s01e02">Some Mysteries</a>
      </body>
    </html>
    """
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/stream/arcane":
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        episodes = await backend.list_season("Arcane", 1)
    finally:
        await backend.aclose()

    assert requested_paths[0] == "/stream/arcane"
    assert [ep.episode for ep in episodes] == [1, 2]
    assert episodes[0].title == "Welcome to the Playground"


@pytest.mark.asyncio
async def test_filmpalast_series_lookup_keeps_episode_search_hits() -> None:
    search_html = """
    <article class="liste rb">
      <a class="rb" href="/stream/arcane-s01e01"><h2>Arcane S01E01</h2></a>
    </article>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stream/arcane":
            return httpx.Response(404)
        if request.url.path == "/stream/arcane-s01e01":
            return httpx.Response(200, text=search_html)
        if request.url.path.startswith("/search/title/"):
            return httpx.Response(200, text=search_html)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        episodes = await backend.list_season("Arcane", 1)
    finally:
        await backend.aclose()

    assert [(ep.season, ep.episode) for ep in episodes] == [(1, 1)]
    assert episodes[0].url == "http://example.invalid/stream/arcane-s01e01"


@pytest.mark.asyncio
async def test_filmpalast_search_falls_back_to_shorter_query() -> None:
    """Long, punctuated German titles return nothing from filmpalast's search;
    the backend should retry with a trimmed query (the Green Book case)."""
    result_html = """
    <article class="liste rb">
      <a class="rb" href="/stream/green-book-eine-besondere-freundschaft"><h2>Green Book - Eine besondere Freundschaft (2018)</h2></a>
    </article>
    """
    queried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/stream/"):
            return httpx.Response(
                200,
                text=(
                    '<span id="release_text">Green.Book.2018.GERMAN.1080p.BluRay.x264</span>'
                    '<li>Veröffentlicht: 2018</li>'
                ),
            )
        assert request.url.path.startswith("/search/title/")
        from urllib.parse import unquote

        q = unquote(request.url.path[len("/search/title/") :])
        queried.append(q)
        if q == "Green Book":
            return httpx.Response(200, text=result_html)
        return httpx.Response(200, text="<html></html>")

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        results = await backend.search("Green Book - Eine besondere Freundschaft", kind=MediaKind.MOVIE)
        results = await backend.enrich_search_results(results)
    finally:
        await backend.aclose()

    assert len(results) == 1
    assert "Green Book" in results[0].title
    assert "Green Book" in queried
    assert results[0].release_name == "Green.Book.2018.GERMAN.1080p.BluRay.x264"


@pytest.mark.asyncio
async def test_filmpalast_resolve_prefers_supported_hoster() -> None:
    """A page may list several mirrors; veev.to defeats yt-dlp and playwright,
    so we must pick the supported voe.sx mirror instead (the Arcane S02 case)."""
    page = """
    <a class="button iconPlay" href="https://veev.to/e/abc">veev</a>
    <a href="https://voe.sx/d6b91t8" class="button iconPlay">voe</a>
    <a class="button iconPlay" href="https://vinovo.to/d/x2g">vinovo</a>
    """
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=page)),
    )
    try:
        handle = await backend.resolve_stream("http://example.invalid/stream/arcane-s02e01")
    finally:
        await backend.aclose()

    assert handle.url == "https://voe.sx/d6b91t8"
    assert handle.hint == "playwright"


@pytest.mark.asyncio
async def test_filmpalast_resolve_all_returns_ranked_hosters() -> None:
    html = """
    <a class="button iconPlay" href="https://veev.to/slow">Veev</a>
    <a class="button iconPlay" href="https://streamtape.com/backup">Streamtape</a>
    <a class="button iconPlay" href="https://voe.sx/primary">VOE</a>
    """

    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html)),
    )
    try:
        handles = await backend.resolve_all_streams("http://example.invalid/stream/x")
    finally:
        await backend.aclose()

    assert [handle.url for handle in handles] == [
        "https://voe.sx/primary",
        "https://streamtape.com/backup",
        "https://veev.to/slow",
    ]


@pytest.mark.asyncio
async def test_filmpalast_supported_streams_excludes_unreliable_hosters() -> None:
    html = """
    <a class="button iconPlay" href="https://unknown.invalid/file">Unknown</a>
    <a class="button iconPlay" href="https://veev.to/dead">Veev</a>
    <a class="button iconPlay" href="https://voe.sx/working">VOE</a>
    """
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html)),
    )
    try:
        handles = await backend.resolve_supported_streams("http://example.invalid/stream/x")
    finally:
        await backend.aclose()

    assert [handle.url for handle in handles] == ["https://voe.sx/working"]


@pytest.mark.asyncio
async def test_filmpalast_live_streams_rejects_dead_voe_and_vinovo() -> None:
    html = """
    <a class="button iconPlay" href="https://voe.sx/dead">VOE</a>
    <a class="button iconPlay" href="https://vinovo.to/d/placeholder">Vinovo</a>
    <a class="button iconPlay" href="https://streamtape.com/live">Streamtape</a>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.invalid":
            return httpx.Response(200, text=html)
        if request.url.host == "voe.sx":
            return httpx.Response(404, text="File not found")
        if request.url.host == "streamtape.com":
            return httpx.Response(200, text="<html>player</html>")
        raise AssertionError(f"unexpected probe: {request.url}")

    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(handler),
    )
    try:
        handles = await backend.resolve_live_streams("http://example.invalid/stream/x")
    finally:
        await backend.aclose()

    assert [handle.url for handle in handles] == ["https://streamtape.com/live"]


@pytest.mark.asyncio
async def test_filmpalast_resolve_reads_data_player_url_and_prefers_vidsonic() -> None:
    html = """
    <a class="button iconPlay" href="https://voe.sx/backup">VOE</a>
    <a class="button iconPlay" href="#"
       data-player-url="https://st-us-01.vidsonic.net/e/current">VidSonic</a>
    <a class="button iconPlay" data-player-url="https://firestream.to/e/third">Fire</a>
    """
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html)),
    )
    try:
        handles = await backend.resolve_all_streams("http://example.invalid/stream/x")
    finally:
        await backend.aclose()

    assert [handle.url for handle in handles] == [
        "https://st-us-01.vidsonic.net/e/current",
        "https://firestream.to/e/third",
        "https://voe.sx/backup",
    ]
    assert [handle.hint for handle in handles] == ["playwright"] * 3


@pytest.mark.asyncio
async def test_filmpalast_resolve_returns_ytdlp_handle() -> None:
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://example.invalid",
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    try:
        handle = await backend.resolve_stream("http://example.invalid/stream/x")
    finally:
        await backend.aclose()
    # Network call to example.invalid fails, so we fall back to playwright.
    assert handle.hint in ("ytdlp", "playwright")
    assert handle.site == "filmpalast"


@pytest.mark.asyncio
async def test_search_propagates_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    backend = FilmpalastBackend(base_url="http://example.invalid")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(base_url="http://example.invalid", transport=transport)
    try:
        with pytest.raises(Exception, match="HTTP 500"):
            await backend.search("anything")
    finally:
        await backend.aclose()
