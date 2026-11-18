"""The AvistaZ network's trackers: AnimeZ (anime, Search's fourth source) and AvistaZ (Asian
films and shows, Movies & Shows).

Both answer the API Prowlarr and Jackett use (``/api/v1/jackett/...``): a login of
name, password and PID (the passkey on the site's profile page) gives a bearer
token; a search gives 50 rows a page, each with its audio and subtitle languages
by name, its info hash, size, seeders and resolution -- and on AnimeZ its format
(TV, MOVIE, OVA, ...). The API filters by none of the languages: a choice of
audio or subtitles is filtered here, as nekobt's batches are. One request every
6.1 s per site, as Prowlarr keeps to.

Private trackers: a ``.torrent`` is fetched with the token (qBittorrent never
sees the site), there is no magnet, and a finished torrent seeds until the
site's rule is met (``torrent.seeding``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from bankai.config import get_settings
from bankai.logging import get_logger

log = get_logger(__name__)

SITES = {"animez": "https://animez.to", "avistaz": "https://avistaz.to"}
LABEL = {"animez": "AnimeZ", "avistaz": "AvistaZ"}
PAGE_SIZE = 50
SPACING = 6.1
# AnimeZ's formats. Without one asked the search takes manga and novels too.
ANIME_FORMATS = {"TV": "TV", "TV_SHORT": "TV short", "MOVIE": "Movie", "SPECIAL": "Special", "OVA": "OVA", "ONA": "ONA"}
# AvistaZ's resolution codes (video_quality[]), as Prowlarr sends them.
QUALITY_CODES = {"2160p": ["6"], "1080p": ["3", "7"], "720p": ["2"], "480p": ["1"]}
# The languages the sites name, as the codes the rest of bankai uses (nekobt's, ISO 639-1).
LANGUAGE_CODES = {
    "english": "en", "german": "de", "japanese": "ja", "chinese": "zh", "mandarin": "zh", "cantonese": "yue",
    "korean": "ko", "french": "fr", "spanish": "es", "italian": "it", "portuguese": "pt", "russian": "ru",
    "arabic": "ar", "thai": "th", "vietnamese": "vi", "indonesian": "id", "malay": "ms", "polish": "pl",
    "turkish": "tr", "dutch": "nl", "swedish": "sv", "norwegian": "no", "danish": "da", "finnish": "fi",
    "czech": "cs", "hungarian": "hu", "greek": "el", "hebrew": "he", "hindi": "hi", "tagalog": "tl",
    "filipino": "tl", "romanian": "ro", "ukrainian": "uk", "bulgarian": "bg", "croatian": "hr", "persian": "fa",
    "brazilian portuguese": "pt-br", "portuguese (brazil)": "pt-br", "spanish (latin america)": "es-419",
    "latin american spanish": "es-419", "chinese (simplified)": "zh", "chinese (traditional)": "zh-hant",
}

_tokens: dict[str, str] = {}
# A refused login, or "too many requests": (until, why). Asking again at once only lengthens a
# lockout -- every search logged in anew and AnimeZ answered 429 (Nov 2026). New login
# details (``forget_token``) clear it.
_refused: dict[str, tuple[float, str]] = {}
REFUSED_WAIT = 3600.0
BUSY_WAIT = 900.0
_locks: dict[str, asyncio.Lock] = {}
_last: dict[str, float] = {}
# The rows' details, kept from the search for the description drawer.
_DETAILS: dict[str, dict[str, Any]] = {}
_DETAILS_KEPT = 3000


class AvistazError(RuntimeError):
    pass


def login(site: str) -> tuple[str, str, str]:
    settings = get_settings()
    if site == "animez":
        anime = settings.anime
        return anime.animez_username.strip(), anime.animez_password, anime.animez_pid.strip()
    if site == "avistaz":
        own = settings.avistaz
        return own.username.strip(), own.password, own.pid.strip()
    raise ValueError(f"not an AvistaZ network site: {site}")


def configured(site: str) -> bool:
    return all(login(site))


def site_of(url: str | None) -> str | None:
    """The site a link is on, when it is one of these."""
    host = (urlparse(url or "").hostname or "").casefold()
    return next((site for site, base in SITES.items() if host == urlparse(base).hostname or host.endswith("." + str(urlparse(base).hostname))), None)


def _token_path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "avistaz_tokens.json"


def _stored_token(site: str) -> str | None:
    if site in _tokens:
        return _tokens[site]
    try:
        token = json.loads(_token_path().read_text(encoding="utf-8")).get(site)
    except (OSError, ValueError, AttributeError):
        token = None
    if token:
        _tokens[site] = str(token)
    return _tokens.get(site)


def _store_token(site: str, token: str | None) -> None:
    if token:
        _tokens[site] = token
    else:
        _tokens.pop(site, None)
    path = _token_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    if token:
        data[site] = token
    else:
        data.pop(site, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    # A bearer token downloads as the user: readable by the user alone.
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(temp, path)


def forget_token(site: str) -> None:
    """New login details: the next request logs in with them."""
    _refused.pop(site, None)
    _store_token(site, None)


def _check_refused(site: str) -> None:
    held = _refused.get(site)
    if held and time.time() < held[0]:
        minutes = max(1, round((held[0] - time.time()) / 60))
        raise AvistazError(f"{held[1]} -- bankai asks again in {minutes} min, or at once when the login is changed")


async def _wait_turn(site: str) -> None:
    lock = _locks.setdefault(site, asyncio.Lock())
    async with lock:
        delay = _last.get(site, 0.0) + SPACING - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        _last[site] = time.monotonic()


def _client(site: str) -> httpx.AsyncClient:
    headers = {"User-Agent": get_settings().scraper.user_agent, "Accept": "application/json"}
    return httpx.AsyncClient(base_url=SITES[site], headers=headers, timeout=30, follow_redirects=True)


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message") or response.reason_phrase)
    except ValueError:
        return response.reason_phrase


async def _authenticate(client: httpx.AsyncClient, site: str) -> str:
    username, password, pid = login(site)
    if not (username and password and pid):
        raise AvistazError(f"{LABEL[site]} is not set up: its name, password and PID are needed")
    _check_refused(site)
    await _wait_turn(site)
    response = await client.post("/api/v1/jackett/auth", data={"username": username, "password": password, "pid": pid})
    if response.status_code in (401, 403, 422):
        why = f"{LABEL[site]} refused the login: {_message(response)}"
        _refused[site] = (time.time() + REFUSED_WAIT, why)
        raise AvistazError(why)
    if response.status_code == 429:
        why = f"{LABEL[site]} asks to wait: too many logins"
        _refused[site] = (time.time() + BUSY_WAIT, why)
        raise AvistazError(why)
    response.raise_for_status()
    token = str(response.json().get("token") or "")
    if not token:
        raise AvistazError(f"{LABEL[site]} gave no token for the login")
    _store_token(site, token)
    return token


async def _get(site: str, path: str, params: list[tuple[str, str]]) -> httpx.Response:
    """A GET with the site's token, logged in again once when it has run out."""
    _check_refused(site)
    async with _client(site) as client:
        token = _stored_token(site) or await _authenticate(client, site)
        for attempt in (1, 2):
            await _wait_turn(site)
            response = await client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
            if response.status_code in (401, 412) and attempt == 1:
                token = await _authenticate(client, site)
                continue
            if response.status_code == 429:
                why = f"{LABEL[site]} asks to wait: too many requests"
                _refused[site] = (time.time() + BUSY_WAIT, why)
                raise AvistazError(why)
            if response.status_code == 404:
                return response
            response.raise_for_status()
            return response
    raise AvistazError(f"{LABEL[site]} kept refusing the token")


async def search(site: str, params: list[tuple[str, str]]) -> tuple[list[dict[str, Any]], str]:
    """One page of a site's search, raw, and the request it was (without a token)."""
    response = await _get(site, "/api/v1/jackett/torrents", params)
    sent = f"{SITES[site]}/api/v1/jackett/torrents?{urlencode(params)}"
    if response.status_code == 404:
        return [], sent
    body = response.json()
    rows = body.get("data") if isinstance(body, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)], sent


async def fetch_torrent(url: str) -> bytes:
    """A ``.torrent`` from one of the sites, with the user's token."""
    site = site_of(url)
    if site is None:
        raise ValueError("not an AvistaZ network link")
    response = await _get(site, url, [])
    if response.status_code == 404:
        raise AvistazError(f"{LABEL[site]} has no such torrent any more")
    data = response.content
    if not data.startswith(b"d"):
        raise AvistazError(f"{LABEL[site]} gave no torrent file for the link")
    return data


def language_code(name: str) -> str:
    key = name.strip().casefold()
    return LANGUAGE_CODES.get(key) or LANGUAGE_CODES.get(re.sub(r"\s*\(.*\)$", "", key)) or key


def _languages(raw: Any) -> list[str]:
    seen: list[str] = []
    for item in raw or []:
        name = item.get("language") if isinstance(item, dict) else item
        code = language_code(str(name or "")) if name else ""
        if code and code not in seen:
            seen.append(code)
    return seen


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


def _quality(row: dict) -> str | None:
    raw = str(row.get("video_quality") or "").casefold()
    if raw in {"2160p", "1080p", "720p", "480p"}:
        return raw
    if raw == "1080i":
        return "1080p"
    if raw in {"sd", "576p", "540p"}:
        return "480p"
    return None


def anime_row(row: dict) -> dict[str, Any]:
    """An AnimeZ row in the shape Search lists Nyaa's in."""
    from bankai.web import anime as anime_mod

    title = str(row.get("release_title") or row.get("file_name") or "")
    url = str(row.get("url") or "")
    torrent_id = re.search(r"/torrent/(\d+)", url)
    ident = torrent_id.group(1) if torrent_id else str(row.get("info_hash") or url)
    size_bytes = _int(row.get("file_size"))
    files = _int(row.get("file_count"))
    created = str(row.get("created_at_iso") or "")
    try:
        published = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(UTC).isoformat() if created else None
    except ValueError:
        published = None
    form = str(row.get("format") or "").upper()
    audio, subs = _languages(row.get("audio")), _languages(row.get("subtitle"))
    quality = _quality(row) or anime_mod._quality(title)
    freeleech = float(row.get("download_multiply") if row.get("download_multiply") is not None else 1) == 0
    if len(_DETAILS) >= _DETAILS_KEPT:
        _DETAILS.pop(next(iter(_DETAILS)))
    _DETAILS[ident] = {
        "title": title, "file_name": row.get("file_name"), "format": ANIME_FORMATS.get(form, form.title() or None),
        "audio": [str(item.get("language")) for item in row.get("audio") or [] if isinstance(item, dict)],
        "subtitle": [str(item.get("language")) for item in row.get("subtitle") or [] if isinstance(item, dict)],
        "files": files, "size": _size(size_bytes), "completed": _int(row.get("completed")),
        "freeleech": freeleech, "upload_multiply": row.get("upload_multiply"), "url": url,
    }
    return {
        "source": "animez",
        "id": ident,
        "title": title,
        "download_url": str(row.get("download") or ""),
        "detail_url": url,
        "magnet_uri": "",
        "info_hash": str(row.get("info_hash") or "").casefold(),
        "category_id": form,
        "category": ANIME_FORMATS.get(form, form.title()),
        "size": _size(size_bytes),
        "size_bytes": size_bytes,
        "seeders": _int(row.get("seed")),
        "leechers": _int(row.get("leech")),
        "downloads": _int(row.get("completed")),
        "comments": 0,
        "trusted": False,
        "remake": False,
        "published_at": published,
        "publisher": anime_mod._publisher(title),
        "groups": [],
        "quality": quality,
        "video_type": ANIME_FORMATS.get(form, form.title() or None),
        "codec": None,
        "audio_langs": audio,
        "sub_langs": subs,
        "fansub_langs": [],
        # A season's files, or one episode: more than one video is a batch.
        "batch": files > 1,
        "freeleech": freeleech,
        "description": "",
        "anilist": None,
    }


def description(ident: str) -> str:
    """What the search said of a release, as the drawer shows a description (the API gives none)."""
    found = _DETAILS.get(ident)
    if not found:
        raise ValueError("this AnimeZ release is no longer in memory: search again")
    lines = [f"**{found['title']}**", ""]
    if found.get("file_name") and found["file_name"] != found["title"]:
        lines += [f"File: `{found['file_name']}`", ""]
    rows = [
        ("Format", found.get("format") or "—"),
        ("Audio", ", ".join(found["audio"]) or "none listed"),
        ("Subtitles", ", ".join(found["subtitle"]) or "none listed"),
        ("Files", str(found["files"] or "—")),
        ("Size", found["size"]),
        ("Downloaded", f"{found['completed']} times"),
        ("Freeleech", "yes" if found["freeleech"] else "no"),
    ]
    lines += ["| | |", "|---|---|", *[f"| {name} | {value} |" for name, value in rows], ""]
    lines.append(
        "AnimeZ is a private tracker: a download seeds until its hit-and-run rule is met (74 h for 1 GB up to 472 h for 1000 GB, or 90% uploaded back), then goes."
    )
    if found.get("url"):
        lines += ["", f"[Open on AnimeZ]({found['url']})"]
    return "\n".join(lines)


async def anime_search(
    query: str = "",
    *,
    page: int = 0,
    quality: str | None = None,
    publisher: str | None = None,
    title_filters: str | None = None,
    subtitles: list[str] | None = None,
    audio: list[str] | None = None,
    batch: bool | None = None,
    min_seeders: int = 0,
    formats: list[str] | None = None,
    freeleech: bool = False,
) -> dict[str, Any]:
    """One page of AnimeZ, as Search lists it. Languages, quality, publisher, batches and
    seeders are filtered here; the format and freeleech are the site's own filters."""
    from bankai.web import anime as anime_mod

    if not configured("animez"):
        raise AvistazError("AnimeZ is not set up: add its name, password and PID in Anime Settings")
    wanted = [form for form in (formats or []) if form in ANIME_FORMATS] or list(ANIME_FORMATS)
    params: list[tuple[str, str]] = [("search", query.strip()), ("limit", str(PAGE_SIZE))]
    params += [("format[]", form) for form in wanted]
    if page:
        params.append(("page", str(page + 1)))
    if freeleech:
        params.append(("freeleech", "1"))
    raw, sent = await search("animez", params)
    rows = [anime_row(row) for row in raw]
    subtitles = [code.casefold() for code in subtitles or [] if code.strip()]
    audio = [code.casefold() for code in audio or [] if code.strip()]
    title_terms = anime_mod.split_filter_terms(title_filters)
    wanted_publisher = anime_mod._compact(publisher or "")

    def keep(row: dict) -> bool:
        if quality and quality.casefold() != "all" and (row["quality"] or "").casefold() != quality.casefold():
            return False
        if wanted_publisher and wanted_publisher not in anime_mod._compact(row["publisher"] or row["title"]):
            return False
        if title_terms and not any(term in row["title"].casefold() for term in title_terms):
            return False
        if subtitles and not set(subtitles) & set(row["sub_langs"]):
            return False
        if audio and not set(audio) & set(row["audio_langs"]):
            return False
        if batch is not None and row["batch"] != batch:
            return False
        return row["seeders"] >= min_seeders

    items = [row for row in rows if keep(row)]
    items.sort(key=lambda row: (row["seeders"], row["downloads"]), reverse=True)
    matches = await anime_mod.anilist_matches([row["title"] for row in items])
    for row, match in zip(items, matches):
        row["anilist"] = match
    return {"items": items, "page": page, "has_next": len(raw) >= PAGE_SIZE, "aliases": [], "sent": [sent]}


def candidate(row: dict) -> Any:
    """An AvistaZ row as a Prowlarr candidate (``torrent.prowlarr.TorrentCandidate``'s fields), with
    what only AvistaZ says: its audio and subtitle languages (codes) and TMDB id."""
    from types import SimpleNamespace

    title = str(row.get("release_title") or row.get("file_name") or "")
    quality = _quality(row)
    if quality and not re.search(r"\b(2160p|1080p|720p|480p|576p)\b", title, re.IGNORECASE):
        # Names here often leave the resolution out; the rest of bankai reads it from the name.
        title = f"{title} {quality}"
    created = str(row.get("created_at_iso") or "") or None
    tv = row.get("movie_tv") if isinstance(row.get("movie_tv"), dict) else {}
    return SimpleNamespace(
        title=title,
        indexer=LABEL["avistaz"],
        indexer_id=None,
        download_url=str(row.get("download") or ""),
        info_url=str(row.get("url") or "") or None,
        magnet_uri=None,
        info_hash=str(row.get("info_hash") or "").casefold() or None,
        size_bytes=_int(row.get("file_size")),
        seeders=_int(row.get("seed")),
        leechers=_int(row.get("leech")),
        publish_date=created,
        resolution=quality,
        codec=None,
        source=None,
        audio_langs=_languages(row.get("audio")),
        sub_langs=_languages(row.get("subtitle")),
        tmdb_id=_int(tv.get("tmdb")) or None,
        raw={},
    )


async def mas_search(query: str, kind: str, *, pages: int = 1) -> list[Any]:
    """AvistaZ's films (``movie``) or shows (``show``) for a query, empty for its newest uploads."""
    if not configured("avistaz"):
        return []
    found: list[Any] = []
    for page in range(pages):
        params = [("search", query.strip()), ("in", "1"), ("type", "1" if kind == "movie" else "2"), ("limit", str(PAGE_SIZE))]
        if page:
            params.append(("page", str(page + 1)))
        rows, _sent = await search("avistaz", params)
        found += [candidate(row) for row in rows]
        if len(rows) < PAGE_SIZE:
            break
    return found


__all__ = [
    "ANIME_FORMATS", "AvistazError", "SITES", "anime_search", "configured", "description", "fetch_torrent",
    "forget_token", "language_code", "mas_search", "search", "site_of",
]
