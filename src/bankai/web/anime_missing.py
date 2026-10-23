"""Missing episodes of the anime library, looked for on Nyaa in the background.

The drawer lists each episode that has aired and is not on disk, with a
search button beside it. This pass does that search by itself, a few AniList
entries a pass, and keeps what it found: only a release carrying German
subtitles, by the rule the automation takes releases by. The library tags a
show "N available" from it, and the drawer offers them in one dialog. Nothing
is downloaded until they are picked there.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import anilist
from bankai.web import anime as anime_mod
from bankai.web import anime_files, erai

log = get_logger(__name__)

# How long what a search found is trusted: a release with German turns up
# within days of an episode airing, or not for months. Also how long an
# episode queued from the dialog is left alone: one whose download failed is
# looked for again after it.
_MAX_AGE = 7 * 86400
# Entries whose search failed (AniList refusing, Nyaa down), by when: tried
# again a day later, so they do not take the front of every pass.
_FAILED: dict[int, float] = {}
# Releases looked at per missing episode before it is given up for this pass:
# each German check can be a Nyaa page of its own.
_GERMAN_TRIES = 4
# Episodes of one entry searched for one by one, when the searches by name
# did not reach them: a long show's old episodes are pages deep.
_EPISODE_SEARCHES = 12
# What the pass is at, for the dashboard's scheduler card while it runs.
progress: dict[str, Any] = {}
_LOCK = threading.Lock()


def _checks_path() -> Path:
    return erai._state_path().with_name("anime_missing_checks.json")


def checked() -> dict[tuple[int, int], dict[str, Any]]:
    """Each missing episode a search looked at: when, and the release it found, if any.

    {(AniList id, episode): {"checked_at", and when found "info_hash",
    "title", "size_bytes", "seeders", "quality", "hevc", "first", "last",
    "magnet_uri", "torrent_url", "detail_url"; "queued_at" once downloaded
    from the dialog}}.
    """
    try:
        raw = json.loads(_checks_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found: dict[tuple[int, int], dict[str, Any]] = {}
    for aid, episodes in (raw or {}).items():
        for episode, row in (episodes or {}).items():
            if str(aid).isdigit() and str(episode).isdigit() and isinstance(row, dict):
                found[(int(aid), int(episode))] = row
    return found


def _save(rows: dict[tuple[int, int], dict[str, Any]]) -> None:
    out: dict[str, dict[str, dict]] = {}
    for (aid, episode), row in sorted(rows.items()):
        out.setdefault(str(aid), {})[str(episode)] = row
    path = _checks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _record(aid: int, found: dict[int, dict[str, Any]]) -> None:
    """An entry's missing episodes as just searched; the ones no longer missing are forgotten.

    An episode queued from the dialog a little while ago keeps its record:
    it was not searched again.
    """
    now = time.time()
    with _LOCK:
        rows = checked()
        for key in [key for key in rows if key[0] == aid]:
            row = rows[key]
            if key[1] in found or now - float(row.get("queued_at") or 0.0) >= _MAX_AGE:
                del rows[key]
        rows.update({(aid, episode): row for episode, row in found.items()})
        _save(rows)


def mark_queued(keys: list[tuple[int, int]]) -> None:
    """These episodes were queued from the dialog: no longer offered, not searched for a while."""
    now = time.time()
    with _LOCK:
        rows = checked()
        for key in keys:
            if key in rows:
                rows[key]["queued_at"] = now
        _save(rows)


def on_their_way(state: dict[str, Any]) -> set[tuple[int, int]]:
    """(AniList entry, episode) of each episode a download is under way for in the release state."""
    releases = state.get("releases") or {}
    found: set[tuple[int, int]] = set()
    for key, slot in (state.get("canonical") or {}).items():
        release = releases.get(str((slot or {}).get("info_hash") or ""))
        if release is None or release.get("status") not in erai._ACTIVE_RELEASE_STATES:
            continue
        head, _, episode = str(key).partition("|")
        aid = head.removeprefix("anilist:")
        if aid.isdigit() and episode.isdigit():
            found.add((int(aid), int(episode)))
    return found


def available(state: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
    """The missing episodes a release with German was found for, not yet queued: the library's "N available"."""
    busy = on_their_way(state)
    return {
        key: row
        for key, row in checked().items()
        if row.get("info_hash") and not row.get("queued_at") and key not in busy
    }


def _aired_missing(aid: int, have: set[int]) -> list[int]:
    """An entry's episodes that have aired and have no file; as the drawer counts them."""
    from bankai.web.anime_library import roster

    listed = roster(aid)
    if not listed:
        return []
    today = date.today().isoformat()
    ended = anilist.finished(aid)
    return [
        ep.number
        for ep in listed
        if ep.number >= 1
        and ep.number not in have
        and ((ep.aired and ep.aired[:10] <= today) or (not ep.aired and ended))
    ]


def _release_row(item: anime_mod.NyaaEntry, first: int, last: int) -> dict[str, Any]:
    resolution = erai._resolution(item)
    return {
        "checked_at": time.time(),
        "info_hash": item.info_hash.casefold(),
        "title": item.title,
        "size_bytes": int(item.size_bytes or 0),
        "seeders": int(item.seeders or 0),
        "quality": item.quality or (f"{resolution}p" if resolution else None),
        "hevc": erai._is_hevc(item),
        "first": first,
        "last": last,
        # The magnet is what qBittorrent is given; an RSS row without one is the hash's.
        "magnet_uri": item.magnet_uri or f"magnet:?xt=urn:btih:{item.info_hash.casefold()}",
        "torrent_url": item.download_url,
        "detail_url": item.detail_url,
    }


async def _find_releases(
    entries: dict[int, anilist.Anime], wanted: set[tuple[int, int]], headers: dict[str, str]
) -> dict[tuple[int, int], dict[str, Any]]:
    """The release to take for each wanted (entry, episode) that has one with German.

    From the catalogue already crawled, then Nyaa searched under each entry's
    names, and an episode none of that reached searched by its number, as
    the drawer's search does. Every result is identified as the automation
    would. 1080p first, then HEVC; a batch covering more of the wanted
    episodes is one download for them; then seeders. Each candidate is
    checked for German in that order, and a release without seeders is none.
    """
    settings = get_settings()
    state = await asyncio.to_thread(erai.state_for_reading)
    mappings = await asyncio.to_thread(erai._load_mappings)
    table = await anilist.index()
    pool: dict[str, anime_mod.NyaaEntry] = {}
    for row in erai._catalog_entries(state).values():
        try:
            item = erai._entry_from_dict(row)
        except Exception:
            continue
        pool[item.info_hash] = item
    names: dict[int, set[str]] = {}
    for aid, entry in entries.items():
        names[aid] = {name for name in (entry.title, entry.english) if name}
    for release in (state.get("releases") or {}).values():
        head, _, _episode = str(release.get("canonical") or "").partition("|")
        aid = head.removeprefix("anilist:")
        if aid.isdigit() and int(aid) in names:
            parsed = erai._erai_identity_name(str(release.get("title") or ""))
            if parsed:
                names[int(aid)].add(parsed[0])

    def candidates() -> dict[tuple[int, int], list[tuple[tuple, anime_mod.NyaaEntry, int, int]]]:
        found: dict[tuple[int, int], list[tuple[tuple, anime_mod.NyaaEntry, int, int]]] = {}
        for item in pool.values():
            if int(item.seeders or 0) <= 0:
                continue
            match = anime_mod.anilist_match(item.title, table=table, mappings=mappings)
            if not match:
                continue
            aid = int(match["anilist_id"])
            span = erai._release_range(item.title)
            if span is None:
                if match.get("episode") is None:
                    continue
                span = (int(match["episode"]), int(match["episode"]))
            first, last = span
            if first == last and match.get("episode") is not None:
                first = last = int(match["episode"])  # settled into the right part of a split season
            covered = [
                episode
                for episode in range(first, min(last, erai.SEASON_PACK_LAST - 1) + 1)
                if (aid, episode) in wanted
            ]
            score = (erai._resolution(item) == 1080, erai._is_hevc(item), len(covered), int(item.seeders or 0))
            for episode in covered:
                found.setdefault((aid, episode), []).append((score, item, first, last))
        for rows in found.values():
            rows.sort(key=lambda row: row[0], reverse=True)
        return found

    async def search(nyaa: httpx.AsyncClient, term: str, pages: int) -> None:
        for page in range(pages):
            try:
                rows = await anime_mod._fetch_rss(nyaa, term, "1_2", page)
            except Exception as exc:
                log.warning("Nyaa search for %s failed: %s", term, exc)
                return
            for item in rows:
                pool.setdefault(item.info_hash, item)
            # Every request spaced, as the backfill spaces them: Nyaa throttles.
            await asyncio.sleep(settings.anime.backfill_request_delay_seconds)
            if len(rows) < 75:
                return

    # Relative to Nyaa: _fetch_rss asks for "/".
    async with httpx.AsyncClient(base_url=erai._NYAA_BASE, headers=headers, timeout=30, follow_redirects=True) as nyaa:
        for aid in entries:
            for name in sorted(names[aid]):
                await search(nyaa, f'"{name}"', 3)
        reached = candidates()
        for aid in entries:
            unreached = sorted(episode for a, episode in wanted if a == aid and (a, episode) not in reached)
            for episode in unreached[:_EPISODE_SEARCHES]:
                for name in sorted(names[aid]):
                    await search(nyaa, f'"{name}" "- {episode:02d}"', 1)
    ranked = candidates()

    german: dict[str, anime_mod.NyaaEntry | None] = {}

    async def with_german(item: anime_mod.NyaaEntry, client: httpx.AsyncClient) -> anime_mod.NyaaEntry | None:
        """The release itself if it carries German; else the encode of it Erai-raws lists German for."""
        if item.info_hash in german:
            return german[item.info_hash]
        taken: anime_mod.NyaaEntry | None = None
        if await erai._carries_german(item, client):
            taken = item
        elif settings.anime.german_avc_fallback and erai.erai_site.german_sibling(item.info_hash) is not None:
            sibling = await erai._german_sibling(item, client)
            if sibling is not None and int(sibling.seeders or 0) > 0:
                taken = sibling
        german[item.info_hash] = taken
        return taken

    picked: dict[tuple[int, int], dict[str, Any]] = {}
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        for key in sorted(wanted):
            for _score, item, first, last in ranked.get(key, [])[:_GERMAN_TRIES]:
                taken = await with_german(item, client)
                if taken is not None:
                    picked[key] = _release_row(taken, first, last)
                    break
    return picked


async def check_missing_episodes(*, entries: int = 3) -> dict[str, int]:
    """Look, without downloading, for releases of a few entries' missing episodes.

    An entry counts while it has files in the library and is not
    blacklisted; its missing episodes are the ones that have aired and have
    no file, less those a download is already on its way for. The entries
    whose missing episodes were looked at longest ago -- or never -- go
    first, a few a pass, each costing a handful of Nyaa searches.
    """
    rows = await asyncio.to_thread(anime_files.load)
    looked = await asyncio.to_thread(checked)
    state = await asyncio.to_thread(erai.state_for_reading)
    blocked = erai._policy_anilist_ids(await asyncio.to_thread(erai._load_policies))
    busy = on_their_way(state)
    held = anime_files.by_entry(rows)
    now = time.time()
    oldest: dict[int, float] = {}
    for aid in sorted(held):
        if aid in blocked or now - _FAILED.get(aid, 0.0) < 86400:
            continue
        for episode in _aired_missing(aid, set(held[aid])):
            if (aid, episode) in busy:
                continue
            row = looked.get((aid, episode)) or {}
            at = max(float(row.get("checked_at") or 0.0), float(row.get("queued_at") or 0.0))
            if now - at >= _MAX_AGE:
                oldest[aid] = min(oldest.get(aid, at), at)
    due = sorted(oldest, key=oldest.__getitem__)[:entries]
    found = done = 0
    headers = {"User-Agent": get_settings().scraper.user_agent}
    try:
        for index, aid in enumerate(due):
            media = anilist.media(aid)
            name = media.get("name") or media.get("romaji") or f"AniList {aid}"
            progress.update(step=f"Looking for missing episodes: {name}", done=index, total=len(due))
            try:
                entry = await anilist.anime(aid)
                if entry is None:
                    raise ValueError("not in the title index")
                # Exact for the entries searched: the index says what the files
                # are, the disk whether they are still there.
                have = set(await asyncio.to_thread(anime_files.on_disk, aid, rows=rows))
                wanted = {
                    (aid, episode)
                    for episode in _aired_missing(aid, have)
                    if (aid, episode) not in busy
                    and now - float((looked.get((aid, episode)) or {}).get("queued_at") or 0.0) >= _MAX_AGE
                }
                picked = await _find_releases({aid: entry}, wanted, headers) if wanted else {}
            except Exception as exc:
                log.warning("Missing-episode search for AniList %s failed: %s", aid, exc)
                _FAILED[aid] = time.time()
                continue
            stamp = time.time()
            result = {episode: picked.get((aid, episode)) or {"checked_at": stamp} for _aid, episode in wanted}
            await asyncio.to_thread(_record, aid, result)
            done += 1
            found += len(picked)
    finally:
        progress.clear()
    return {"checked": done, "episodes_found": found, "remaining": max(0, len(oldest) - done)}


def releases_for(episodes: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """What was found for a show's missing episodes, as the dialog lists it: a batch once.

    ``episodes`` are the drawer's rows; each release carries the episodes
    it covers there, by the drawer's own season and episode labels.
    """
    found = available(state)
    out: dict[str, dict[str, Any]] = {}
    for row in episodes:
        if not row.get("missing") or row.get("tba"):
            continue
        key = (row.get("anilist_id"), row.get("anilist_episode"))
        release = found.get(key)  # type: ignore[arg-type]
        if release is None:
            continue
        item = out.setdefault(
            release["info_hash"],
            {
                **{name: value for name, value in release.items() if name not in {"checked_at", "queued_at"}},
                "anilist_id": key[0],
                "batch": release.get("first") != release.get("last"),
                "episodes": [],
            },
        )
        item["episodes"].append(
            {
                "anilist_id": key[0],
                "anilist_episode": key[1],
                "season": row.get("season"),
                "season_number": row.get("season_number"),
                "episode": row.get("episode"),
            }
        )
    return sorted(out.values(), key=lambda item: (item["episodes"][0]["season_number"] or 0, item["episodes"][0]["episode"] or 0))
