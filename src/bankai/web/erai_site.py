"""What erai-raws.info itself says about each release, from its RSS feeds.

Nyaa's description is the only subtitle evidence bankai had, and Erai does not
always write the languages there: 165 review cards were held only because the
description did not say "German". The Erai-raws site lists every release's
subtitle languages, and its feeds -- signed with the member's own token, no
login session -- carry them per item, with the torrent's info hash:

    <erai:infohash>84b20ca2...</erai:infohash>
    <erai:subtitles>[us][br][mx][es][sa][de][it][ru]</erai:subtitles>

That hash is the Nyaa torrent's, so each release is matched exactly. The
site-wide feed is read newest first and then, a few pages a pass, back through
its history; a show's own feed fills in a show on demand. What was read is
kept in ``erai_site.json``: info hash -> name, subtitles, resolution, size,
date and page.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import suppress
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

BASE_URL = "https://www.erai-raws.info"
_NS = {"erai": "https://www.erai-raws.info/rss-page/"}
# Per pass: newest pages until one brings nothing new, then this many older.
_NEWEST_PAGES = 5
# The site-wide feed reaches back about eighteen months, some 100 pages:
# read in a few passes. Older releases come from each show's own feed.
_BACKFILL_PAGES = 20
_PAGE_DELAY_SECONDS = 2.0
# A show's own feed is read again at most this often.
_SHOW_FEED_SECONDS = 12 * 3600
# A search is asked again at most this often.
_SEARCH_SECONDS = 3600
GERMAN = "de"

# Erai's subtitle codes are the flags it shows.
LANGUAGES = {
    "us": "English", "br": "Portuguese (Brazil)", "pt": "Portuguese", "mx": "Spanish (Latin America)",
    "es": "Spanish", "sa": "Arabic", "de": "German", "it": "Italian", "ru": "Russian", "fr": "French",
    "jp": "Japanese", "pl": "Polish", "nl": "Dutch", "no": "Norwegian", "fi": "Finnish", "tr": "Turkish",
    "se": "Swedish", "gr": "Greek", "il": "Hebrew", "ro": "Romanian", "id": "Indonesian", "th": "Thai",
    "kr": "Korean", "dk": "Danish", "cn": "Chinese", "tw": "Chinese (Taiwan)", "vn": "Vietnamese",
    "ua": "Ukrainian", "hu": "Hungarian", "cz": "Czech", "hr": "Croatian", "my": "Malay",
    "ph": "Filipino", "in": "Hindi",
}

_LOADED: tuple[float, dict] | None = None
_LOCK = asyncio.Lock()


def _store() -> Path:
    return bgjobs.jobs_root().parent / "erai_site.json"


def configured() -> bool:
    return bool(get_settings().anime.erai_feed_token.strip())


def _empty() -> dict[str, Any]:
    return {"releases": {}, "backfill_next": 2, "complete": False, "shows": {}, "updated_at": 0.0}


def load() -> dict[str, Any]:
    """The index as last saved; re-read when the file changes (another process wrote it)."""
    global _LOADED
    path = _store()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _empty()
    if _LOADED is None or _LOADED[0] != mtime:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _empty()
        _LOADED = (mtime, {**_empty(), **(data if isinstance(data, dict) else {})})
    return _LOADED[1]


def _save(index: dict[str, Any]) -> None:
    global _LOADED
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    _LOADED = None


def lookup(info_hash: str) -> dict[str, Any] | None:
    return load()["releases"].get(str(info_hash or "").casefold())


def german(info_hash: str) -> bool | None:
    """Does Erai-raws list German subtitles for this torrent? None when it has not said."""
    row = lookup(info_hash)
    if row is None:
        return None
    return GERMAN in (row.get("subs") or [])


# -- reading feeds -----------------------------------------------------------


def _text(item: ET.Element, tag: str) -> str:
    node = item.find(tag, _NS)
    return (node.text or "").strip() if node is not None else ""


def parse_feed(xml: str) -> list[dict[str, Any]]:
    """The releases in one feed page; pure."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        info_hash = _text(item, "erai:infohash").casefold()
        if not re.fullmatch(r"[0-9a-f]{40}", info_hash):
            continue
        description = _text(item, "description")
        page = re.search(r'href="([^"]+)"', description)
        name = re.search(r">([^<]+)</a>", description)
        published = 0.0
        with suppress(TypeError, ValueError):
            published = parsedate_to_datetime(_text(item, "pubDate")).timestamp()
        title = re.sub(r"^\[[^]]*\]\s*", "", _text(item, "title"))
        out.append(
            {
                "hash": info_hash,
                "name": (name[1].strip() if name else "") or title,
                "title": title.split(" [", 1)[0].strip(),
                "subs": re.findall(r"\[(\w+)\]", _text(item, "erai:subtitles")),
                "res": _text(item, "erai:resolution"),
                "size": _text(item, "erai:size"),
                "category": _text(item, "erai:category").strip("[]"),
                "published": published,
                "page": page[1] if page else "",
            }
        )
    return out


def _merge(rows: list[dict[str, Any]], **changes: Any) -> int:
    """Add releases to the saved index, and apply ``changes``; how many were new.

    The web process and the automation worker both write the index, so it
    is re-read and written under the release state's lock, which holds
    across processes -- and only for the merge, never while a feed loads.
    """
    from bankai.web import erai

    with erai._STATE_LOCK:
        global _LOADED
        _LOADED = None
        index = load()
        known = index["releases"]
        new = 0
        for row in rows:
            row = dict(row)
            info_hash = row.pop("hash")
            new += info_hash not in known
            known[info_hash] = row
        for key, value in changes.items():
            if key in {"shows", "searches"}:
                index.setdefault(key, {}).update(value)
            else:
                index[key] = value
        _save(index)
    return new


async def _page(client: httpx.AsyncClient, path: str, **params: Any) -> list[dict[str, Any]]:
    token = get_settings().anime.erai_feed_token.strip()
    response = await client.get(path, params={"token": token, "type": "torrent", **params})
    if response.status_code >= 400:
        # Not raise_for_status: its message is the URL, token and all, and
        # that ends up in the service log.
        raise FeedError(
            "Erai-raws refused the feed token -- check it in Anime Settings"
            if response.status_code in {401, 403}
            else f"Erai-raws answered {response.status_code} for {path}"
        )
    return parse_feed(response.text)


class FeedError(RuntimeError):
    """A feed that could not be read; the message never carries the token."""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=httpx.Timeout(60, connect=15),
        follow_redirects=True,
        headers={"User-Agent": get_settings().scraper.user_agent},
    )


async def refresh() -> dict[str, Any]:
    """One pass: the newest releases, then a few pages further back in history."""
    if not configured():
        return {"configured": False}
    try:
        return await _refresh()
    except (FeedError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, FeedError) else f"Erai-raws could not be reached: {type(exc).__name__}"
        _merge([], last_error=message, updated_at=time.time())
        log.warning("%s", message)
        return {"configured": True, "error": message}


async def _refresh() -> dict[str, Any]:
    async with _LOCK:
        added = 0
        async with _client() as client:
            for number in range(1, _NEWEST_PAGES + 1):
                rows = await _page(client, "/feed/", **({"paged": number} if number > 1 else {}))
                new = _merge(rows)
                added += new
                if not rows or not new:
                    break
                await asyncio.sleep(_PAGE_DELAY_SECONDS)
            state = load()
            page, complete = int(state.get("backfill_next") or 2), bool(state.get("complete"))
            for _ in range(0 if complete else _BACKFILL_PAGES):
                await asyncio.sleep(_PAGE_DELAY_SECONDS)
                rows = await _page(client, "/feed/", paged=page)
                if not rows:
                    complete = True
                    break
                added += _merge(rows)
                page += 1
        _merge([], backfill_next=page, complete=complete, updated_at=time.time(), last_error=None)
    known = len(load()["releases"])
    if added:
        log.info("Erai-raws: %d releases added (%d known)", added, known)
    return {"configured": True, "added": added, "known": known}


def show_slug(name: str) -> str:
    """The site's address for a show: "Meitantei Precure" -> "meitantei-precure"."""
    value = re.sub(r"[^\w\s-]", "", name.casefold().replace("&", " "))
    return re.sub(r"[\s_-]+", "-", value).strip("-")


def show_slugs(name: str) -> list[str]:
    """Addresses a show might be under, likeliest first.

    Erai names some releases "Romaji | English" and keeps the show under the
    romaji half: "Ao no Miburo | Blue Miburo" is at ao-no-miburo. A language
    tag -- "(CA)" -- is not part of the address either.
    """
    cleaned = re.sub(r"\s*\((?:[A-Z]{2})\)\s*$", "", name.strip())
    parts = [cleaned, *(part.strip() for part in cleaned.split("|"))] if "|" in cleaned else [cleaned]
    slugs = [show_slug(part) for part in (parts[1:] + parts[:1] if "|" in cleaned else parts)]
    return [slug for slug in dict.fromkeys(slugs) if slug]


async def fill_show(name: str) -> int:
    """Read one show's own feed -- its whole history -- into the index."""
    if not configured() or not name.strip():
        return 0
    read = load().get("shows") or {}
    tried: dict[str, float] = {}
    async with _client() as client:
        for slug in show_slugs(name):
            if time.time() - float(read.get(slug) or 0) < _SHOW_FEED_SECONDS:
                return 0  # read lately, under this address
            tried[slug] = time.time()
            try:
                rows = await _page(client, f"/anime-list/{slug}/feed/")
            except (FeedError, httpx.HTTPError) as exc:
                log.debug("Erai-raws feed for %s unavailable: %s", slug, type(exc).__name__)
                rows = []
            if rows:
                return _merge(rows, shows=tried)
            await asyncio.sleep(_PAGE_DELAY_SECONDS)
    _merge([], shows=tried)
    # Under none of the addresses tried: the site's search finds it by name.
    return await search(name)


def _search_key(term: str) -> str:
    return " ".join(term.casefold().split())


async def search(term: str) -> int:
    """The site's search, as a feed, into the index: every season of a show.

    The site-wide feed reaches back about eighteen months, so a show's older
    seasons were missing from the tab. The search feed has them all -- "oshi
    no ko" brings its three seasons -- at every resolution.
    """
    key = _search_key(term)
    if not configured() or len(key) < 3:
        return 0
    if time.time() - float((load().get("searches") or {}).get(key) or 0) < _SEARCH_SECONDS:
        return 0
    try:
        async with _client() as client:
            rows = await _page(client, "/", s=key, feed="rss2")
    except (FeedError, httpx.HTTPError) as exc:
        log.debug("Erai-raws search for %r failed: %s", key, type(exc).__name__)
        rows = []
    return _merge(rows, searches={key: time.time()})


def summary() -> dict[str, Any]:
    index = load()
    releases = index["releases"]
    return {
        "configured": configured(),
        "known": len(releases),
        "newest": max((float(r.get("published") or 0) for r in releases.values()), default=None),
        "complete": bool(index.get("complete")),
        "updated_at": index.get("updated_at") or None,
        "error": index.get("last_error"),
    }
