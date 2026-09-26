"""Shoko Server as bankai's window onto AniDB.

Shoko keeps AniDB's complete title list locally and fetches anime data within
AniDB's strict rate limits, which a second client of bankai's own would have
to reproduce -- and a mistake there bans the same AniDB account Shoko uses.
So for anime identity bankai asks Shoko.
"""

from __future__ import annotations

from typing import Any

import httpx

from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

# AniDB title types worth matching releases against. Short names and loose
# synonyms ("AoT", "Monster") would blacklist or match whatever else happens
# to share them.
_MATCHING_TITLE_TYPES = {"main", "official"}


class ShokoError(RuntimeError):
    pass


def configured() -> bool:
    shoko = get_settings().shoko
    return bool(shoko.url and shoko.api_key)


def _client(timeout: float = 15.0) -> httpx.AsyncClient:
    shoko = get_settings().shoko
    return httpx.AsyncClient(
        base_url=shoko.url.rstrip("/"),
        headers={"apikey": shoko.api_key} if shoko.api_key else {},
        timeout=timeout,
    )


async def _get(endpoint: str, /, **params: Any) -> Any:
    # Positional-only: Shoko has a query parameter called "path" of its own.
    if not configured():
        raise ShokoError("Shoko is not connected; run `bankai shoko login` on the server")
    async with _client() as client:
        response = await client.get(endpoint, params={k: v for k, v in params.items() if v is not None})
    if response.status_code == 401:
        raise ShokoError("Shoko rejected bankai's API key; run `bankai shoko login` again")
    response.raise_for_status()
    return response.json() if response.content else None


async def _send(method: str, endpoint: str, /, *, json_body: Any = None, **params: Any) -> Any:
    if not configured():
        raise ShokoError("Shoko is not connected; run `bankai shoko login` on the server")
    async with _client() as client:
        response = await client.request(
            method,
            endpoint,
            json=json_body,
            params={k: v for k, v in params.items() if v is not None},
        )
    if response.status_code == 404:
        return None
    if response.status_code == 401:
        raise ShokoError("Shoko rejected bankai's API key; run `bankai shoko login` again")
    response.raise_for_status()
    return response.json() if response.content else True


async def login(username: str, password: str, *, device: str = "bankai") -> str:
    """Trade a Shoko username and password for an API key."""
    async with _client() as client:
        response = await client.post(
            "/api/auth", json={"user": username, "pass": password, "device": device}
        )
    if response.status_code in {401, 403}:
        raise ShokoError("Shoko did not accept that username and password")
    response.raise_for_status()
    key = (response.json() or {}).get("apikey")
    if not key:
        raise ShokoError("Shoko answered the login without an API key")
    return str(key)


def poster_path(image: dict[str, Any] | None) -> str | None:
    """bankai's own URL for a Shoko image, which needs the API key to fetch.

    Only for an image Shoko has actually downloaded. Search results carry a
    poster record for every anime, but for one outside the collection there
    is no file behind it (no RelativeFilepath) and Shoko answers 404.
    """
    if not image or not image.get("ID") or not image.get("Source") or not image.get("Type"):
        return None
    if not image.get("RelativeFilepath"):
        return None
    return f"/api/anime/anidb/image/{image['Source']}/{image['Type']}/{image['ID']}"


def _anime(row: dict[str, Any]) -> dict[str, Any]:
    titles = row.get("Titles") or []
    english = next(
        (
            title["Name"]
            for title in titles
            if str(title.get("Language") or "").casefold() == "en"
            and str(title.get("Type") or "").casefold() in _MATCHING_TITLE_TYPES
        ),
        None,
    )
    air_date = str(row.get("AirDate") or "")
    return {
        "anidb_id": int(row["ID"]),
        # AniDB's main title is the romaji one Erai-raws names releases after.
        "title": row.get("Title") or english or "",
        "english_title": english,
        "matching_titles": sorted(
            {
                str(title["Name"])
                for title in titles
                if title.get("Name")
                and str(title.get("Type") or "").casefold() in _MATCHING_TITLE_TYPES
            }
            | ({str(row["Title"])} if row.get("Title") else set())
        ),
        "type": row.get("Type"),
        "episode_count": row.get("EpisodeCount"),
        "year": int(air_date[:4]) if air_date[:4].isdigit() else None,
        "poster_url": poster_path(row.get("Poster")),
    }


async def _search(query: str, *, fuzzy: bool, limit: int, by_id: bool = False) -> list[dict]:
    result = await _get(
        "/api/v3/Series/AniDB/Search",
        query=query,
        # Not local: "local" is only what is already in the Shoko collection,
        # a few series early on, and fuzzy matching then returned the nearest
        # of those -- "Frieren" came back as Kanojo, Okarishimasu. The full
        # AniDB title list is what Shoko keeps for exactly this.
        local="false",
        includeTitles="true",
        fuzzy="true" if fuzzy else "false",
        searchById="true" if by_id else "false",
        pageSize=limit,
    )
    rows = result.get("List") if isinstance(result, dict) else result
    return [row for row in rows or [] if row.get("ID")]


async def search_anidb(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """AniDB anime by title, across AniDB's full title list.

    Exact first: fuzzy matching pads the answer with loose neighbours ("Uma
    no Friends" for Frieren), so it is only the fallback for a typo that
    finds nothing otherwise.
    """
    query = query.strip()
    if not query:
        return []
    rows = await _search(query, fuzzy=False, limit=limit)
    if not rows:
        rows = await _search(query, fuzzy=True, limit=limit)
    return [_anime(row) for row in rows]


async def anidb_anime(anidb_id: int) -> dict[str, Any]:
    """One AniDB anime by id, from the title list.

    Not /Series/AniDB/{id}: that only answers for anime already in the Shoko
    collection, and a show being blacklisted is usually one that is not.
    """
    for row in await _search(str(int(anidb_id)), fuzzy=False, limit=5, by_id=True):
        if int(row.get("ID") or 0) == int(anidb_id):
            return _anime(row)
    raise ShokoError(f"AniDB has no anime {anidb_id}")


def best_match(results: list[dict[str, Any]], *names: str) -> dict[str, Any] | None:
    """The result one of the names is exactly a title of, if exactly one is."""

    def norm(value: str) -> str:
        return " ".join(value.casefold().split())

    wanted = {norm(name) for name in names if name}
    hits = [row for row in results if wanted & {norm(t) for t in row.get("matching_titles") or []}]
    return hits[0] if len(hits) == 1 else None


async def image(source: str, kind: str, image_id: str) -> tuple[bytes, str]:
    if not configured():
        raise ShokoError("Shoko is not connected")
    async with _client() as client:
        response = await client.get(f"/api/v3/Image/{source}/{kind}/{image_id}")
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "image/jpeg")


async def forget_missing_files() -> None:
    """Have Shoko drop records of files deleted from disk, and Jellyfin with it."""
    try:
        await _get("/api/v3/Action/RemoveMissingFiles/false")
    except Exception as exc:  # best effort: the next Shoko scan catches it too
        log.warning("Could not ask Shoko to forget deleted files: %s", exc)
