"""AniList's manga and light novels: what a manga release is identified by.

The counterpart of anilist (anime). AniList keeps manga and light novels as
one type, MANGA, told apart by format: MANGA and ONE_SHOT are read as
pictures, NOVEL as text. Asked fifty entries at a time, slowly, and kept a
week in ``anime_metadata/anilist_manga.json``; the relations kept are all of
them, anime and manga alike, so an entry knows its anime adaptation as well as
its sequels and spin-offs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from bankai.cli import bgjobs
from bankai.logging import get_logger
from bankai.metadata import anilist

log = get_logger(__name__)

NOVEL_FORMATS = frozenset({"NOVEL"})
MANGA_FORMATS = frozenset({"MANGA", "ONE_SHOT"})

_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: MANGA) {
      id format status volumes chapters countryOfOrigin
      startDate { year month day }
      title { romaji english native }
      synonyms description(asHtml: false) genres
      staff(perPage: 4, sort: RELEVANCE) { edges { role node { name { full } } } }
      coverImage { extraLarge large } bannerImage
      relations { edges { relationType(version: 2) node { id type format } } }
    }
  }
}
"""
_SEARCH = """
query ($q: String, $n: Int, $formats: [MediaFormat]) {
  Page(perPage: $n) {
    media(search: $q, type: MANGA, format_in: $formats, sort: SEARCH_MATCH) {
      id format status volumes chapters
      startDate { year }
      title { romaji english native }
      coverImage { large }
    }
  }
}
"""

_MEDIA: tuple[float, dict[int, dict]] | None = None
_LOCK = asyncio.Lock()


def kind_of(format_: str | None) -> str:
    """"novel" for a light novel, "manga" for everything read as pictures."""
    return "novel" if format_ in NOVEL_FORMATS else "manga"


def _store() -> Path:
    return bgjobs.jobs_root().parent / "anime_metadata" / "anilist_manga.json"


def media_all() -> dict[int, dict]:
    """Everything kept from AniList's API about manga and light novels, by AniList id."""
    global _MEDIA
    path = _store()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _MEDIA is None or _MEDIA[0] != mtime:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        _MEDIA = (mtime, {int(key): row for key, row in raw.items() if str(key).isdigit()})
    return _MEDIA[1]


def media(anilist_id: object) -> dict:
    try:
        return media_all().get(int(anilist_id), {})  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}


def _save(rows: dict[int, dict]) -> None:
    global _MEDIA
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({str(key): row for key, row in rows.items()}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    _MEDIA = None


def parse(item: dict) -> dict:
    """One API answer, as kept."""
    title = item.get("title") or {}
    image = item.get("coverImage") or {}
    authors = []
    for edge in (item.get("staff") or {}).get("edges") or []:
        role = str(edge.get("role") or "")
        full = ((edge.get("node") or {}).get("name") or {}).get("full")
        # "Story & Art", "Story", "Art", "Original Story": not translators and letterers.
        if full and re.search(r"story|art|original", role, re.IGNORECASE) and full not in authors:
            authors.append(full)
    relations = [
        [edge["node"]["id"], edge.get("relationType"), edge["node"].get("type"), edge["node"].get("format")]
        for edge in (item.get("relations") or {}).get("edges") or []
        if isinstance((edge.get("node") or {}).get("id"), int)
    ]
    start = item.get("startDate") or {}
    return {
        "romaji": title.get("romaji"),
        "english": title.get("english"),
        "native": title.get("native"),
        "synonyms": item.get("synonyms") or [],
        "format": item.get("format"),
        "status": item.get("status"),
        "volumes": item.get("volumes"),
        "chapters": item.get("chapters"),
        "year": start.get("year"),
        "country": item.get("countryOfOrigin"),
        "description": item.get("description"),
        "genres": item.get("genres") or [],
        "authors": authors,
        "cover": image.get("extraLarge") or image.get("large"),
        "banner": item.get("bannerImage"),
        "relations": relations,
        "fetched_at": time.time(),
    }


async def ensure(anilist_ids: set[int] | list[int], *, max_batches: int = 6, refresh: bool = True) -> int:
    """Fetch what is not kept yet -- or, with ``refresh``, a week old -- of these entries.

    Returns how many entries were fetched. At most ``max_batches`` requests.
    """
    async with _LOCK:
        rows = dict(media_all())
        now = time.time()
        wanted = [
            int(aid) for aid in dict.fromkeys(anilist_ids)
            if int(aid) not in rows or (refresh and now - float(rows[int(aid)].get("fetched_at", 0)) >= anilist._KEEP)
        ][: anilist._BATCH * max_batches]
        if not wanted:
            return 0
        fetched = 0
        async with httpx.AsyncClient(timeout=30, headers={"Content-Type": "application/json"}) as client:
            for start in range(0, len(wanted), anilist._BATCH):
                ids = wanted[start : start + anilist._BATCH]
                try:
                    answer = await anilist._ask(client, _QUERY, {"ids": ids})
                except Exception as exc:
                    log.warning("AniList could not be asked about manga: %s", exc)
                    anilist._note(exc)
                    break
                anilist._note(None)
                found = ((answer.get("data") or {}).get("Page") or {}).get("media") or []
                seen = set()
                for item in found:
                    if isinstance(item, dict) and isinstance(item.get("id"), int):
                        rows[item["id"]] = parse(item)
                        seen.add(item["id"])
                        fetched += 1
                for missing in set(ids) - seen:
                    rows[missing] = {**rows.get(missing, {}), "fetched_at": time.time(), "missing": True}
                if start + anilist._BATCH < len(wanted):
                    await asyncio.sleep(anilist._PAUSE)
        _save(rows)
        return fetched


async def search(query: str, limit: int = 20, *, kind: str | None = None) -> list[dict]:
    """AniList's own search for manga (``kind`` "manga") or light novels ("novel"), for picking by hand."""
    query = query.strip()
    if not query:
        return []
    if query.isdigit() or "anilist.co/manga/" in query:
        found = re.search(r"(\d+)", query.split("anilist.co/manga/")[-1])
        if found:
            await ensure([int(found[1])], refresh=False)
            return [as_choice(int(found[1]))] if name(int(found[1])) else []
    formats = sorted(NOVEL_FORMATS if kind == "novel" else MANGA_FORMATS if kind == "manga" else NOVEL_FORMATS | MANGA_FORMATS)
    async with httpx.AsyncClient(timeout=30, headers={"Content-Type": "application/json"}) as client:
        answer = await anilist._ask(client, _SEARCH, {"q": query, "n": limit, "formats": formats})
    rows = ((answer.get("data") or {}).get("Page") or {}).get("media") or []
    return [
        {
            "anilist_id": item["id"],
            "name": (item.get("title") or {}).get("english") or (item.get("title") or {}).get("romaji"),
            "romaji": (item.get("title") or {}).get("romaji"),
            "english": (item.get("title") or {}).get("english"),
            "format": item.get("format"),
            "kind": kind_of(item.get("format")),
            "volumes": item.get("volumes"),
            "chapters": item.get("chapters"),
            "status": item.get("status"),
            "year": (item.get("startDate") or {}).get("year"),
            "cover": (item.get("coverImage") or {}).get("large"),
        }
        for item in rows if isinstance(item, dict) and isinstance(item.get("id"), int)
    ]


# -- An entry ------------------------------------------------------------------


def name(anilist_id: object) -> str:
    """How an entry is shown: English, else romaji, else native; "" when not kept."""
    kept = media(anilist_id)
    return str(kept.get("english") or kept.get("romaji") or kept.get("native") or "")


def titles(anilist_id: object) -> list[str]:
    """Every title an entry goes by: what a release name is matched on."""
    kept = media(anilist_id)
    found = [kept.get("english"), kept.get("romaji"), kept.get("native"), *(kept.get("synonyms") or [])]
    return list(dict.fromkeys(str(title) for title in found if title))


def as_choice(anilist_id: int) -> dict[str, Any]:
    """An entry as the pick-a-manga dialog and the review cards show it."""
    kept = media(anilist_id)
    return {
        "anilist_id": anilist_id,
        "name": name(anilist_id),
        "romaji": kept.get("romaji"),
        "english": kept.get("english"),
        "format": kept.get("format"),
        "kind": kind_of(kept.get("format")),
        "volumes": kept.get("volumes"),
        "chapters": kept.get("chapters"),
        "status": kept.get("status"),
        "year": kept.get("year"),
        "cover": kept.get("cover"),
    }
