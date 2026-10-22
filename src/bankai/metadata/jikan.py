"""Episode titles from MyAnimeList, through Jikan, for entries AniList names none of.

AniList takes episode titles from streaming sites, and some list a whole show
as "Untitled" (Crunchyroll's Bleach). MyAnimeList titles nearly every episode;
Jikan serves it without a key, three requests a second and sixty a minute, a
hundred episodes a page. Asked only for entries in the library that have
untitled episodes, a few pages a maintenance pass; kept a week while an entry
airs, three months once it has finished.

Jikan only reads MyAnimeList's pages, and its API can be unreachable for days
(it never once answered after this shipped). Then the same episode list is
read from MyAnimeList itself -- the same MyAnimeList id, AniList's link.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from pathlib import Path

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger

log = get_logger(__name__)

_API = "https://api.jikan.moe/v4"
_MAL = "https://myanimelist.net/anime/{mal_id}/_/episode"
# MyAnimeList's episode list, like Jikan's, is a hundred episodes a page.
_MAL_PAGE = 100
_MAL_ROW = re.compile(r'<tr class="episode-list-data">(.*?)</tr>', re.S)
_MAL_NUMBER = re.compile(r'class="episode-number[^"]*"[^>]*data-raw="(\d+)"')
_MAL_TITLE = re.compile(r'class="episode-title[^"]*">\s*<a [^>]*>(.*?)</a>', re.S)
# One request a second: well under Jikan's three a second and sixty a minute.
_PAUSE = 1.1
_KEEP_AIRING = 7 * 86400
_KEEP_FINISHED = 90 * 86400

_STORE: tuple[float, dict[int, dict]] | None = None
_LOCK = asyncio.Lock()
# The last time Jikan could not be asked and why, for the dashboard; cleared by the next answer.
last_error: dict[str, object] = {}


def _store_path() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "jikan_episodes.json"


def _load() -> dict[int, dict]:
    global _STORE
    path = _store_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _STORE is None or _STORE[0] != mtime:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        _STORE = (mtime, {int(key): row for key, row in raw.items() if str(key).isdigit()})
    return _STORE[1]


def _save(rows: dict[int, dict]) -> None:
    global _STORE
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({str(key): row for key, row in rows.items()}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    _STORE = None


def titles(mal_id: object) -> dict[int, str]:
    """Episode number -> title, as kept for this MyAnimeList entry; empty when not asked yet."""
    try:
        row = _load().get(int(mal_id))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    if not row:
        return {}
    return {int(number): title for number, title in (row.get("titles") or {}).items() if str(number).isdigit() and title}


def due(mal_id: int, *, finished: bool, now: float | None = None) -> bool:
    """Not kept yet, or kept longer than an entry of its kind is."""
    row = _load().get(int(mal_id))
    if row is None:
        return True
    keep = _KEEP_FINISHED if finished else _KEEP_AIRING
    return (now or time.time()) - float(row.get("fetched_at") or 0) >= keep


async def _page(client: httpx.AsyncClient, mal_id: int, page: int) -> dict | None:
    """One page of an entry's episodes; None when MyAnimeList has no such entry."""
    for _attempt in range(3):
        response = await client.get(f"{_API}/anime/{mal_id}/episodes", params={"page": page})
        if response.status_code == 429:
            await asyncio.sleep(float(response.headers.get("Retry-After") or 3) + 1)
            continue
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json() or {}
    raise RuntimeError("Jikan kept refusing: rate limited")


async def _mal_page(client: httpx.AsyncClient, mal_id: int, page: int) -> dict | None:
    """The same page read from MyAnimeList itself, in the shape Jikan answers with."""
    response = await client.get(
        _MAL.format(mal_id=mal_id),
        params={"offset": (page - 1) * _MAL_PAGE},
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 (bankai)"},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    rows = _MAL_ROW.findall(response.text)
    data = []
    for row in rows:
        number, title = _MAL_NUMBER.search(row), _MAL_TITLE.search(row)
        if number and title:
            text = html.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip()
            data.append({"mal_id": int(number.group(1)), "title": text})
    # By rows, not titles: a row MyAnimeList has no title for still fills the page.
    return {"data": data, "pagination": {"has_next_page": len(rows) >= _MAL_PAGE}}


async def ensure(wanted: list[tuple[int, bool]], *, max_requests: int = 8) -> int:
    """Fetch the titles of these (MyAnimeList id, finished) entries that are due.

    At most ``max_requests`` pages; an entry whose pages do not all fit is
    taken up again next pass. Returns how many entries were completed.
    """
    async with _LOCK:
        rows = dict(_load())
        now = time.time()
        queue = [mal for mal, finished in dict.fromkeys(wanted) if due(mal, finished=finished, now=now)]
        if not queue:
            return 0
        done = requests = 0
        ask = _page
        async with httpx.AsyncClient(timeout=30, headers={"Accept": "application/json"}) as client:
            for mal in queue:
                # A long show takes several passes: it goes on from the page it got to.
                kept = rows.get(mal) or {}
                partial = kept.get("next_page")
                found: dict[str, str] = dict(kept.get("titles") or {}) if partial else {}
                page = int(partial or 1)
                complete = False
                while requests < max_requests:
                    if requests:
                        await asyncio.sleep(_PAUSE)
                    requests += 1
                    try:
                        try:
                            answer = await ask(client, mal, page)
                        except httpx.TransportError as exc:
                            if ask is _mal_page:
                                raise
                            # Jikan unreachable: MyAnimeList itself, for the rest of the pass.
                            log.info("Jikan unreachable (%s); reading MyAnimeList directly", type(exc).__name__)
                            ask = _mal_page
                            answer = await ask(client, mal, page)
                    except Exception as exc:
                        # A timeout's message is empty: name it, or the dashboard says nothing.
                        message = str(exc) or type(exc).__name__
                        log.warning("Episode titles could not be read for MyAnimeList %s: %s", mal, message)
                        since = last_error.get("since") if last_error else None
                        last_error.update(message=message, at=time.time(), since=since or time.time())
                        _save(rows)
                        return done
                    last_error.clear()
                    if answer is None:
                        complete = True
                        break
                    for item in answer.get("data") or []:
                        number, title = item.get("mal_id"), str(item.get("title") or "").strip()
                        if isinstance(number, int) and title:
                            found[str(number)] = title
                    page += 1
                    if not (answer.get("pagination") or {}).get("has_next_page"):
                        complete = True
                        break
                if not complete:
                    rows[mal] = {"titles": found, "fetched_at": 0, "next_page": page}
                    break
                rows[mal] = {"titles": found, "fetched_at": time.time()}
                done += 1
        _save(rows)
        return done


__all__ = ["due", "ensure", "titles"]
