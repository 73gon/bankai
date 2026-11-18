"""Uploaders followed for German audio their release names do not mention.

QxR on kickasstorrents uploads German dual-audio encodes whose names say only
"English" or nothing (Nov 2026): Prowlarr gives no description and no uploader,
so the German review never saw them. An uploader followed here has his
listing read (``/user/<name>/<page>/``, 20 uploads a page): the newest page
every pass, the older ones a few at a time until the first upload is reached.
Each upload's page is read once for its MediaInfo -- ``Audio: German / E-AC-3
/ ...``, one line a track -- and its magnet, size and seeders; those with a
German audio track are offered to the review (``german_candidates``, taken in
by ``mas_releases.discover`` like AvistaZ's, on the uploader's own word).

Gently: one page every ``SPACING`` seconds, at most ``PAGES_PER_PASS`` a pass.
Store: ``mas_uploaders.json`` (followed, every upload read, where the reading
back has got to).
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from bankai.logging import get_logger

log = get_logger(__name__)

SITES = {"kickass": "https://kickasstorrents.to", "tpb": "https://apibay.org"}
LABEL = {"kickass": "kickasstorrents", "tpb": "The Pirate Bay"}
# The Pirate Bay's API (apibay): ``q.php?q=user:<name>`` lists an uploader's newest 50 (no older ones),
# ``t.php?id=`` gives one torrent with its description. Behind Cloudflare it answered 429 to every
# t.php for days (Nov 2026): read one every ``TPB_SPACING`` s, a few a pass, and on a 429 not again
# for ``TPB_BACKOFF`` s, doubling to a day.
TPB_SPACING = 30.0
TPB_PER_PASS = 10
TPB_BACKOFF = 6 * 3600.0
TPB_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce", "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce", "udp://exodus.desync.com:6969/announce",
)
SPACING = 2.0
PAGES_PER_PASS = 60
# A German find's seeders are read again after this long.
REFRESH_SECONDS = 3 * 24 * 3600
_LOCK = threading.Lock()

_LINK = re.compile(r'href="/([a-z0-9-]+-t(\d+))\.html"')
_TITLE = re.compile(r'class="cellMainLink">\s*(.*?)\s*</a>', re.S)
_MAGNET = re.compile(r"magnet:\?xt=urn:btih:[0-9A-Za-z]{32,40}[^\"'<\s]*")
# MediaInfo as uploaders paste it: "<strong>Audio #2: </strong> German / E-AC-3 / 6 channels / ...".
_AUDIO = re.compile(r"Audio(?:\s*#\s*\d+)?\s*:\s*(?:</strong>)?\s*(?:<[^>]+>\s*)*([A-Za-z][A-Za-z ()-]{1,40}?)\s*/", re.I)
_SIZE = re.compile(r'class="widgetSize"[^>]*>\s*(?:<[^>]+>\s*)*([\d.,]+)\s*(KB|MB|GB|TB)', re.I)
_SEEDERS = re.compile(r'class="seedBlock"[^>]*>.*?<strong>\s*(\d+)', re.S)
_LEECHERS = re.compile(r'class="leechBlock"[^>]*>.*?<strong>\s*(\d+)', re.S)
_UNITS = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def _path() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "mas_uploaders.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("follow", [])
    data.setdefault("seen", {})
    data.setdefault("reading", {})
    return data


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _who(site: str, name: str) -> str:
    return f"{site}:{name.casefold()}"


def follow(site: str, name: str) -> dict[str, Any]:
    if site not in SITES:
        raise ValueError(f"no such site: {site} (known: {', '.join(SITES)})")
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
        raise ValueError("not an uploader name")
    with _LOCK:
        data = load()
        if not any(_who(row["site"], row["name"]) == _who(site, name) for row in data["follow"]):
            data["follow"].append({"site": site, "name": name, "added": time.time()})
            _save(data)
    return listing()


def unfollow(site: str, name: str) -> dict[str, Any]:
    with _LOCK:
        data = load()
        data["follow"] = [row for row in data["follow"] if _who(row["site"], row["name"]) != _who(site, name)]
        _save(data)
    return listing()


def listing() -> dict[str, Any]:
    """Who is followed, and how far each is read."""
    data = load()
    rows = []
    for row in data["follow"]:
        who = _who(row["site"], row["name"])
        mine = [item for item in data["seen"].values() if item.get("who") == who]
        reading = data["reading"].get(who) or {}
        rows.append({
            **row,
            "label": LABEL.get(row["site"], row["site"]),
            "read": len(mine),
            "german": sum(1 for item in mine if item.get("german")),
            "back_to_page": reading.get("page"),
            "pages": reading.get("pages"),
            "done": bool(reading.get("done")),
            # Descriptions not read yet, and until when the site asked to wait (The Pirate Bay).
            "unread": sum(1 for item in mine if item.get("described") is False),
            "blocked_until": reading.get("blocked_until") if float(reading.get("blocked_until") or 0) > time.time() else None,
        })
    return {"uploaders": rows}


def size_bytes(number: str, unit: str) -> int:
    return int(float(number.replace(",", "")) * _UNITS[unit.upper()])


_SECTION = re.compile(r"^\s*(General|Video|Audio|Text|Menu|Image|Chapters?)\b(?:\s*#\s*\d+)?\s*$", re.I | re.M)
_LANGUAGE_LINE = re.compile(r"^\s*Language\s*:\s*([A-Za-z][A-Za-z ()-]{1,40})\s*$", re.I | re.M)


def audio_languages(page: str) -> list[str]:
    """The audio tracks' languages a description's MediaInfo names, in order: the short form
    ("Audio #2: German / E-AC-3 / ...", kickass's) and the full one (an "Audio #2" section
    with a "Language : German" line, as pasted as text on The Pirate Bay)."""
    found: list[str] = []

    def add(raw: str) -> None:
        language = re.sub(r"\s*\(.*\)$", "", raw).strip().title()
        if language and language not in found:
            found.append(language)

    for match in _AUDIO.finditer(page):
        add(match.group(1))
    text = html.unescape(re.sub(r"<br\s*/?>", "\n", page))
    sections = list(_SECTION.finditer(text))
    for index, section in enumerate(sections):
        if section.group(1).casefold() != "audio":
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        language = _LANGUAGE_LINE.search(text, section.end(), end)
        if language:
            add(language.group(1))
    return found


def read_upload(page: str) -> dict[str, Any]:
    """An upload's page: its name, magnet, size, seeders and audio languages."""
    title = re.search(r"<h1[^>]*>\s*(?:<[^>]+>\s*)*(.*?)\s*<", page, re.S)
    size = _SIZE.search(page)
    seeders, leechers = _SEEDERS.search(page), _LEECHERS.search(page)
    magnet = _MAGNET.search(page)
    audio = audio_languages(page)
    return {
        "title": html.unescape(title.group(1)).strip() if title else "",
        "magnet": html.unescape(magnet.group(0)) if magnet else None,
        "size_bytes": size_bytes(size.group(1), size.group(2)) if size else 0,
        "seeders": int(seeders.group(1)) if seeders else 0,
        "leechers": int(leechers.group(1)) if leechers else 0,
        "audio": audio,
        "german": "German" in audio,
    }


def read_listing(page: str) -> list[tuple[str, str, str]]:
    """A listing page's uploads: (path, id, name), newest first."""
    out: dict[str, tuple[str, str, str]] = {}
    for row in re.split(r"<tr ", page):
        link = _LINK.search(row)
        name = _TITLE.search(row)
        if link and name and link.group(2) not in out:
            out[link.group(2)] = (link.group(1), link.group(2), html.unescape(re.sub(r"<[^>]+>", "", name.group(1))).strip())
    return list(out.values())


async def _get(client: httpx.AsyncClient, url: str) -> str | None:
    await asyncio.sleep(SPACING)
    response = await client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.text


def _tpb_magnet(info_hash: str, name: str) -> str:
    from urllib.parse import quote

    return f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}" + "".join(f"&tr={quote(url)}" for url in TPB_TRACKERS)


async def _tpb(client: httpx.AsyncClient, data: dict[str, Any], row: dict[str, Any]) -> tuple[int, int]:
    """An uploader on The Pirate Bay: his newest 50 listed, then a few descriptions read, gently."""
    name, who = row["name"], _who("tpb", row["name"])
    reading = data["reading"].setdefault(who, {"done": True})
    await asyncio.sleep(SPACING)
    response = await client.get(f"{SITES['tpb']}/q.php", params={"q": f"user:{name}"})
    response.raise_for_status()
    listed = [item for item in response.json() if str(item.get("id")) != "0"]
    read = german = 0
    blocked = float(reading.get("blocked_until") or 0)
    for item in listed:
        key = f"tpb:{item['id']}"
        known = data["seen"].get(key)
        title = str(item.get("name") or "")
        # The listing's own facts are kept fresh, the description is read once.
        base = {
            "title": title, "who": who, "url": f"https://thepiratebay.org/description.php?id={item['id']}",
            "magnet": _tpb_magnet(str(item["info_hash"]).casefold(), title),
            "size_bytes": int(item.get("size") or 0), "seeders": int(item.get("seeders") or 0),
            "leechers": int(item.get("leechers") or 0),
        }
        if known:
            known.update(base)
            continue
        data["seen"][key] = {**base, "audio": [], "german": False, "described": False, "found_at": time.time()}
    unread = [key for key, item in data["seen"].items() if item.get("who") == who and not item.get("described")]
    if time.time() < blocked or not unread:
        return read, german
    for key in unread[:TPB_PER_PASS]:
        await asyncio.sleep(TPB_SPACING)
        answer = await client.get(f"{SITES['tpb']}/t.php", params={"id": key.split(":", 1)[1]})
        if answer.status_code == 429:
            wait = min(24 * 3600.0, float(reading.get("backoff") or TPB_BACKOFF) * (2 if reading.get("backoff") else 1))
            reading.update(blocked_until=time.time() + wait, backoff=wait)
            log.info("[uploaders] The Pirate Bay asks to wait (429): descriptions again in %.0f h", wait / 3600)
            break
        answer.raise_for_status()
        body = answer.json()
        audio = audio_languages(str(body.get("descr") or ""))
        item = data["seen"][key]
        item.update(audio=audio, german="German" in audio, described=True, checked_at=time.time())
        reading.pop("backoff", None)
        read += 1
        german += item["german"]
    return read, german


async def run(budget: int = PAGES_PER_PASS) -> dict[str, int]:
    """One pass: every followed uploader's newest page, then older pages, each new upload read once."""
    from bankai.config import get_settings

    data = await asyncio.to_thread(load)
    read = german = 0
    headers = {"User-Agent": get_settings().scraper.user_agent}
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        for row in list(data["follow"]):
            site, name = row["site"], row["name"]
            base, who = SITES[site], _who(site, name)
            if site == "tpb":
                try:
                    done, found = await _tpb(client, data, row)
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("[uploaders] The Pirate Bay could not be read for %s: %s", name, exc)
                    done = found = 0
                read, german = read + done, german + found
                with _LOCK:
                    fresh = load()
                    fresh["seen"].update(data["seen"])
                    fresh["reading"][who] = data["reading"].get(who) or {}
                    _save(fresh)
                    data = fresh
                continue
            reading = data["reading"].setdefault(who, {"page": 1, "done": False})
            # The newest page every pass; then, while the budget lasts, the older ones.
            pages = [1] + ([] if reading.get("done") else [max(2, int(reading.get("page") or 1))])
            while pages and budget > 0:
                number = pages.pop(0)
                listing_page = await _get(client, f"{base}/user/{name}/{number}/" if number > 1 else f"{base}/user/{name}/")
                budget -= 1
                uploads = read_listing(listing_page or "")
                if number > 1 and not uploads:
                    reading["done"] = True
                    continue
                complete = True
                for path, ident, title in uploads:
                    key = f"{site}:{ident}"
                    known = data["seen"].get(key)
                    stale = known and known.get("german") and time.time() - float(known.get("checked_at") or 0) > REFRESH_SECONDS
                    if known and not stale:
                        continue
                    if budget <= 0:
                        complete = False
                        break
                    page = await _get(client, f"{base}/{path}.html")
                    budget -= 1
                    if page is None:
                        continue
                    found = read_upload(page)
                    data["seen"][key] = {
                        **found, "title": found["title"] or title, "who": who, "url": f"{base}/{path}.html",
                        "checked_at": time.time(), "found_at": (known or {}).get("found_at") or time.time(),
                    }
                    read += 1
                    german += found["german"]
                if number > 1 and complete:
                    reading["page"] = number + 1
                    if budget > 0 and not reading.get("done"):
                        pages.append(number + 1)
                reading["pages_read_at"] = time.time()
            with _LOCK:
                fresh = load()
                fresh["seen"].update({key: value for key, value in data["seen"].items()})
                fresh["reading"][who] = reading
                _save(fresh)
                data = fresh
    if read:
        log.info("[uploaders] %d uploads read, %d with German audio", read, german)
    return {"read": read, "german": german}


def german_candidates() -> list[Any]:
    """Every German upload read, as Prowlarr candidates (``mas_releases.discover`` takes them)."""
    from bankai.torrent.worker import torrent_info_hash

    data = load()
    followed = {_who(row["site"], row["name"]) for row in data["follow"]}
    out = []
    for item in data["seen"].values():
        if not item.get("german") or not item.get("magnet") or item.get("who") not in followed:
            continue
        out.append(SimpleNamespace(
            title=item["title"], indexer=f"{LABEL.get(item['who'].split(':')[0], '')} · {item['who'].split(':', 1)[1]}",
            indexer_id=None, download_url=None, info_url=item.get("url"), magnet_uri=item["magnet"],
            info_hash=torrent_info_hash(item["magnet"], None), size_bytes=int(item.get("size_bytes") or 0),
            seeders=int(item.get("seeders") or 0), leechers=int(item.get("leechers") or 0),
            publish_date=None, audio_langs=["de"], raw={},
        ))
    return out
