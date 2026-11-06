"""nekobt.to, Discover's second anime source beside Nyaa.

Its search is a JSON API (``/api/v1/torrents/search``) that already says per
release what Nyaa only hides in a description: audio, subtitle and fansub
languages, the source (BD Mini, WEB-DL, ...), the codec, the groups. A list of
languages in one parameter means all of them (``audio_lang=de,ja`` is German
*and* Japanese), so a choice of several is asked one language at a time and
merged: any of them. Subtitles are a release's own or a fansub's, either one.

Rows come out in the shape Nyaa's do (``anime.entry_to_dict``), with the
fields only nekobt has added. Downloads go by the magnet: there is no
``.torrent`` link to hand over.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.web import anime as anime_mod

log = get_logger(__name__)

BASE = "https://nekobt.to"
PAGE_SIZE = 100
# nekobt's own numbers for the source and the codec of a release's picture.
VIDEO_TYPES = {
    15: "Hybrid", 14: "BD Remux", 13: "BD Encode", 12: "BD Mini", 11: "BD Disc", 9: "WEB-DL",
    8: "WEB Encode", 7: "WEB Mini", 6: "DVD Encode", 5: "DVD Remux", 16: "DVD Disc", 4: "TV Raw",
    3: "TV Encode", 2: "LaserDisc", 1: "VHS", 0: "Other",
}
CODECS = {1: "H.264", 2: "HEVC", 3: "AV1", 4: "VP9", 5: "MPEG-2", 6: "XviD", 7: "WMV", 8: "VC-1", 0: "Other"}
_LANGUAGE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,4})?")
# Descriptions are long (screenshots, encoder settings): kept here from the search, sent
# only when a row's description is opened.
_DESCRIPTIONS: dict[str, str] = {}
_DESCRIPTIONS_KEPT = 3000
_GROUPS: dict[str, tuple[float, str | None]] = {}
_GROUP_TTL = 24 * 3600


def valid_language(code: str) -> bool:
    return bool(_LANGUAGE.fullmatch(code))


def detail_url(torrent_id: str) -> str:
    return f"{BASE}/torrents/{torrent_id}"


def _client() -> httpx.AsyncClient:
    headers = {"User-Agent": get_settings().scraper.user_agent, "Accept": "application/json"}
    return httpx.AsyncClient(base_url=BASE, headers=headers, timeout=30, follow_redirects=True)


def _langs(raw: Any) -> list[str]:
    return [part.strip().casefold() for part in str(raw or "").split(",") if part.strip()]


def _int(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{num} B"


def _publisher(row: dict) -> str | None:
    """The group that put it up, else the uploader -- unless the upload is anonymous."""
    groups = [group for group in row.get("groups") or [] if isinstance(group, dict)]
    uploading = next((group for group in groups if group.get("uploading_group")), None)
    if uploading and not uploading.get("anonymous"):
        return str(uploading.get("display_name") or uploading.get("name") or "") or None
    uploader = row.get("uploader") or {}
    if row.get("anonymous") or not isinstance(uploader, dict):
        return None
    return str(uploader.get("display_name") or uploader.get("username") or "") or None


def _row(row: dict) -> dict[str, Any]:
    torrent_id = str(row.get("id") or "")
    title = str(row.get("title") or "")
    size_bytes = _int(row.get("filesize"))
    uploaded = _int(row.get("uploaded_at"))
    video_type = row.get("video_type")
    codec = row.get("video_codec")
    if len(_DESCRIPTIONS) >= _DESCRIPTIONS_KEPT:
        _DESCRIPTIONS.pop(next(iter(_DESCRIPTIONS)))
    _DESCRIPTIONS[torrent_id] = str(row.get("description") or "")
    groups = [str(group.get("display_name") or "") for group in row.get("groups") or [] if isinstance(group, dict)]
    return {
        "source": "nekobt",
        "id": torrent_id,
        "title": title,
        "download_url": "",
        "detail_url": detail_url(torrent_id),
        "magnet_uri": str(row.get("magnet") or ""),
        "info_hash": str(row.get("infohash") or "").casefold(),
        "category_id": str(row.get("category") or ""),
        "category": "",
        "size": _size(size_bytes),
        "size_bytes": size_bytes,
        "seeders": _int(row.get("seeders")),
        "leechers": _int(row.get("leechers")),
        "downloads": _int(row.get("completed")),
        "comments": _int(row.get("comment_count")),
        "trusted": _int(row.get("level")) >= 3,
        "remake": False,
        "published_at": datetime.fromtimestamp(uploaded / 1000, UTC).isoformat() if uploaded else None,
        "publisher": _publisher(row),
        "groups": [name for name in groups if name],
        "quality": anime_mod._quality(title),
        "video_type": VIDEO_TYPES.get(video_type) if isinstance(video_type, int) else None,
        "codec": CODECS.get(codec) if isinstance(codec, int) else None,
        "audio_langs": _langs(row.get("audio_lang")),
        "sub_langs": _langs(row.get("sub_lang")),
        "fansub_langs": _langs(row.get("fsub_lang")),
        "batch": bool(row.get("batch")),
        "description": "",
        "anilist": None,
    }


async def _group_id(client: httpx.AsyncClient, name: str) -> str | None:
    """The group of this name on nekobt, so the search asks for its releases alone."""
    wanted = anime_mod._compact(name)
    hit = _GROUPS.get(wanted)
    if hit and time.time() - hit[0] < _GROUP_TTL:
        return hit[1]
    found = None
    try:
        response = await client.get("/api/v1/groups/search", params={"query": name})
        response.raise_for_status()
        for group in (response.json().get("data") or {}).get("results") or []:
            names = (group.get("display_name"), group.get("name"), group.get("tag"), group.get("display_tag"))
            if any(anime_mod._compact(str(value or "")) == wanted for value in names):
                found = str(group.get("id"))
                break
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("nekobt group lookup for %s failed: %s", name, exc)
        return None
    _GROUPS[wanted] = (time.time(), found)
    return found


def _publisher_matches(publisher: str, row: dict) -> bool:
    wanted = anime_mod._compact(publisher)
    names = [row.get("publisher") or "", *row.get("groups", []), anime_mod._publisher(row["title"]) or ""]
    return bool(wanted) and any(wanted in anime_mod._compact(name) for name in names)


def _keep(
    row: dict,
    *,
    quality: str | None,
    publisher: str | None,
    title_terms: list[str],
    description_terms: list[str],
    subtitles: list[str],
    audio: list[str],
    batch: bool | None = None,
    min_seeders: int = 0,
) -> bool:
    if quality and quality.casefold() != "all" and (row["quality"] or "").casefold() != quality.casefold():
        return False
    if publisher and not _publisher_matches(publisher, row):
        return False
    if title_terms and not any(term in row["title"].casefold() for term in title_terms):
        return False
    description = _DESCRIPTIONS.get(row["id"], "").casefold()
    if description_terms and not any(term in description for term in description_terms):
        return False
    if subtitles and not set(subtitles) & set(row["sub_langs"] + row["fansub_langs"]):
        return False
    if batch is not None and row["batch"] != batch:
        return False
    if row["seeders"] < min_seeders:
        return False
    return not audio or bool(set(audio) & set(row["audio_langs"]))


async def search(
    query: str = "",
    *,
    page: int = 0,
    quality: str | None = None,
    publisher: str | None = None,
    title_filters: str | None = None,
    description_filters: str | None = None,
    subtitles: list[str] | None = None,
    audio: list[str] | None = None,
    batch: bool | None = None,
    min_seeders: int = 0,
) -> dict[str, Any]:
    """One page of nekobt's releases, as Discover lists them, and the requests that found them.

    ``batch``: True for batches alone, False for single episodes, None for both.
    ``min_seeders``: fewer seeders and a release is left out (nekobt has no such filter).
    """
    subtitles = [code.casefold() for code in subtitles or [] if valid_language(code.casefold())]
    audio = [code.casefold() for code in audio or [] if valid_language(code.casefold())]
    base: dict[str, Any] = {"query": query.strip(), "limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
    if batch is not None:
        base["batch"] = "true" if batch else "false"
    async with _client() as client:
        group = await _group_id(client, publisher.strip()) if publisher and publisher.strip() else None
        if group:
            base["group_id"] = group
        # Any of the chosen languages: one request each, merged. Subtitles a release's own or
        # a fansub's; audio asked only without subtitles, else filtered from what came back.
        variants: list[dict[str, str]] = (
            [{field: code} for code in subtitles for field in ("sub_lang", "fansub_lang")]
            or [{"audio_lang": code} for code in audio]
            or [{}]
        )
        requests = [{**base, **variant} for variant in variants]
        sent = [f"{BASE}/api/v1/torrents/search?{urlencode(params)}" for params in requests]

        async def fetch(params: dict[str, Any]) -> dict:
            response = await client.get("/api/v1/torrents/search", params=params)
            response.raise_for_status()
            body = response.json()
            if body.get("error"):
                raise RuntimeError(str(body.get("message") or "nekobt refused the search"))
            return body.get("data") or {}

        answers = await asyncio.gather(*(fetch(params) for params in requests), return_exceptions=True)
    pages = [answer for answer in answers if isinstance(answer, dict)]
    if not pages:
        failure = next((answer for answer in answers if isinstance(answer, BaseException)), None)
        raise RuntimeError(f"nekobt did not answer: {failure}")
    by_id: dict[str, dict] = {}
    for data in pages:
        for raw in data.get("results") or []:
            if isinstance(raw, dict) and raw.get("id") is not None and not raw.get("deleted"):
                by_id.setdefault(str(raw["id"]), _row(raw))
    title_terms = anime_mod.split_filter_terms(title_filters)
    description_terms = anime_mod.split_filter_terms(description_filters)
    items = [
        row for row in by_id.values()
        if _keep(
            row, quality=quality, publisher=publisher, title_terms=title_terms,
            description_terms=description_terms, subtitles=subtitles, audio=audio, batch=batch,
            min_seeders=min_seeders,
        )
    ]
    items.sort(key=lambda row: (row["seeders"], row["downloads"]), reverse=True)
    await _identify(items)
    return {
        "items": items,
        "page": page,
        "has_next": any(data.get("more") for data in pages),
        "aliases": [],
        "sent": sent,
    }


async def _identify(items: list[dict]) -> None:
    """Each row's AniList entry, told from its name as Nyaa's are: offline, no requests."""
    from bankai.metadata import anilist
    from bankai.web import erai

    table = await anilist.index()
    mappings = await asyncio.to_thread(erai._load_mappings)
    for row in items:
        row["anilist"] = anime_mod.anilist_match(row["title"], table=table, mappings=mappings)


async def description(torrent_id: str) -> str:
    """A release's description: kept from the search, else asked of nekobt."""
    if not re.fullmatch(r"\d{1,24}", torrent_id):
        raise ValueError("not a nekobt torrent id")
    if torrent_id in _DESCRIPTIONS:
        return _DESCRIPTIONS[torrent_id]
    async with _client() as client:
        response = await client.get(f"/api/v1/torrents/{torrent_id}")
        response.raise_for_status()
        body = response.json()
    text = str((body.get("data") or {}).get("description") or "")
    _DESCRIPTIONS[torrent_id] = text
    return text


__all__ = ["CODECS", "VIDEO_TYPES", "description", "detail_url", "search", "valid_language"]
