"""Autonomous, safety-first Erai-raws anime ingestion.

Fresh releases come from the uploader RSS feed. Historical backfill completes
2160p and 1080p HEVC catalogue passes before allowing 720p HEVC for logical episodes
that never appeared in either high-quality pass. Nothing is queued until the release is
HEVC, the Nyaa detail page explicitly lists German subtitles, and AniList resolves without
ambiguity.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import html
import json
import os
import re
import shutil
import threading
import time
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import asdict, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from selectolax.parser import HTMLParser

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import anilist
from bankai.web import anime as anime_mod
from bankai.web import anime_collections, anime_files, erai_site, updates

log = get_logger(__name__)
_NYAA_BASE = "https://nyaa.si"
_RSS_URL = f"{_NYAA_BASE}/?page=rss&u=Erai-raws&c=1_2"
class _StateLock:
    """The release state's lock, held across processes as well as threads.

    The automation cycle can run in a worker process of its own (see
    ``_delegated``) while the web process answers the review and blacklist
    buttons, and both read, change and write the same files: a thread lock
    alone would let one side's write drop the other's. Re-entrant, like the
    RLock it wraps; only the outermost hold takes the file lock, since a
    second flock of the same file from this process would wait on itself.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        self._handle: Any = None

    def __enter__(self) -> _StateLock:
        self._lock.acquire()
        self._depth += 1
        if self._depth == 1:
            try:
                self._handle = _lock_file()
            except BaseException:
                self._depth -= 1
                self._lock.release()
                raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1
        if self._depth == 0 and self._handle is not None:
            handle, self._handle = self._handle, None
            with suppress(OSError):
                handle.close()  # closing releases the flock
        self._lock.release()


def _lock_file() -> Any:
    try:
        import fcntl
    except ImportError:  # Windows: one process there, the thread lock is enough
        return None
    path = _state_path().with_name("erai_state.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")  # held open until __exit__
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except BaseException:
        handle.close()
        raise
    return handle


_STATE_LOCK = _StateLock()
_CYCLE_LOCK = asyncio.Lock()
_STOP = asyncio.Event()
_GIB = 1024**3
_ADMISSION: ContextVar[dict | None] = ContextVar("erai_admission", default=None)
_DISK_INDEX: ContextVar[dict | None] = ContextVar("erai_disk_index", default=None)
_GERMAN_LINE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:german|deutsch)(?:\s*\([^\n)]*\))?\s*(?:[|:]|$)"
)
_CR_GERMAN = re.compile(r"(?i)\bCR[_ -]?German\b")


def _state_path() -> Path:
    return bgjobs.jobs_root().parent / "erai_automation.json"


def _load_retry_requests() -> dict[str, dict]:
    with _STATE_LOCK:
        try:
            result = json.loads(
                _state_path().with_name("erai_retry_requests.json").read_text(encoding="utf-8")
            )
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}


def _save_retry_requests(requests: dict[str, dict]) -> None:
    with _STATE_LOCK:
        path = _state_path().with_name("erai_retry_requests.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _policy_path() -> Path:
    return _state_path().with_name("erai_series_policies.json")


def _load_policies() -> dict[str, dict[str, Any]]:
    with _STATE_LOCK:
        try:
            rows = json.loads(_policy_path().read_text(encoding="utf-8"))
            return rows if isinstance(rows, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}


def _save_policies(rows: dict[str, dict[str, Any]]) -> None:
    with _STATE_LOCK:
        path = _policy_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _series_policy(release_title: str) -> dict[str, Any] | None:
    return _load_policies().get(_mapping_key(release_title))


def _per_entry(row: dict[str, Any]) -> bool:
    """A decision on one AniList entry, rather than a whole show by name."""
    return str(row.get("anilist_id") or "").isdigit()


_SHOW_KEYS_CACHE: tuple[tuple, tuple[frozenset[str], frozenset[str]]] | None = None


def _policy_show_keys(
    policies: dict[str, Any] | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """The names blacklisted releases go by: (whole-show keys, exact AniList titles).

    An AniList entry is blocked by its own exact titles only -- stripping the
    season off would make "Oshi no Ko" and "Oshi no Ko 2nd Season" the same
    name, and blocking one season would block the other. Decisions made by
    name, before entries, keep matching the whole show by name. Rebuilt only
    when the blacklist changes: it is asked once per release, thousands of
    times a cycle.
    """
    global _SHOW_KEYS_CACHE
    rows = policies if policies is not None else _load_policies()
    signature = tuple(
        (key, row.get("source_title"), row.get("anilist_id"), tuple(row.get("anilist_titles") or ()))
        for key, row in sorted(rows.items())
        if row.get("mode") == "blacklisted"
    )
    if _SHOW_KEYS_CACHE is not None and _SHOW_KEYS_CACHE[0] == signature:
        return _SHOW_KEYS_CACHE[1]
    shows: set[str] = set()
    exact: set[str] = set()
    for key, source_title, anilist_id, titles in signature:
        if str(anilist_id or "").isdigit():
            exact.update(anilist.normalise(str(title)) for title in titles)
        else:
            shows.add(_show_name_key(str(source_title or key)))
    shows.discard("")
    exact.discard("")
    result = (frozenset(shows), frozenset(exact))
    _SHOW_KEYS_CACHE = (signature, result)
    return result


def _release_anilist_id(
    release_title: str,
    *,
    canonical: str = "",
    mappings: dict[str, Any] | None = None,
    table: Any = None,
) -> int | None:
    """Which AniList entry a release is, without the network.

    Its canonical key if it was identified on AniList, else the user's choice
    for its name, else the title index as last loaded.
    """
    if canonical.startswith("anilist:"):
        head = canonical.split("|", 1)[0].split(":", 1)[1]
        return int(head) if head.isdigit() else None
    parsed = _erai_identity_name(release_title)
    if parsed is None:
        return None
    saved = (mappings if mappings is not None else _load_mappings()).get(
        _mapping_key(release_title)
    ) or {}
    if str(saved.get("anilist_id") or "").isdigit():
        return int(saved["anilist_id"])
    index = table if table is not None else anilist.cached_index()
    if index is None:
        return None
    resolution = anilist.resolve_in(index, parsed[0])
    if resolution.anime is None:
        return None
    settled, _ = anilist.settle_in(resolution.anime, parsed[1])
    return settled.id


def _release_pairs(
    release_title: str, *, table: Any, mappings: dict[str, Any]
) -> list[tuple[int, int]] | None:
    """The (AniList id, episode) of every episode a release name covers; no network.

    Each episode settled on its own: a batch numbered straight through two
    cours is two entries' episodes. None for a name that says nothing
    certain, a season pack among them -- its last episode is not in it.
    """
    span = _release_range(release_title)
    parsed = _erai_identity_name(release_title)
    if span is None or parsed is None or span[1] >= SEASON_PACK_LAST:
        return None
    saved = mappings.get(_mapping_key(release_title)) or {}
    if str(saved.get("anilist_id") or "").isdigit():
        offset = int(saved.get("anilist_episode_offset") or 0)
        return [(int(saved["anilist_id"]), episode + offset) for episode in range(span[0], span[1] + 1)]
    entry = anilist.resolve_in(table, parsed[0]).anime if table is not None else None
    if entry is None:
        return None
    pairs = []
    for episode in range(span[0], span[1] + 1):
        settled, number = anilist.settle_in(entry, episode)
        pairs.append((settled.id, number))
    return pairs


def _title_blacklisted(
    release_title: str,
    policies: dict[str, Any] | None = None,
    *,
    mappings: dict[str, Any] | None = None,
) -> bool:
    """Is a release, by its name alone, of an anime the user blacklisted?"""
    rows = policies if policies is not None else _load_policies()
    policy = rows.get(_mapping_key(release_title))
    if policy and policy.get("mode") == "blacklisted":
        return True
    shows, exact = _policy_show_keys(rows)
    if _show_key(release_title) in shows:
        return True
    parsed = _erai_identity_name(release_title)
    if parsed and anilist.normalise(parsed[0]) in exact:
        return True
    blocked = _policy_anilist_ids(rows)
    if not blocked:
        return False
    return _release_anilist_id(release_title, mappings=mappings) in blocked


def _is_blacklisted_release(
    release: dict[str, Any],
    *,
    policies: dict[str, Any],
    mappings: dict[str, Any],
) -> bool:
    """Does this release belong to an anime the user has blacklisted?

    An AniList entry blocks exactly that entry -- one season or cour, as
    AniList splits them. Decisions made by name, before entries, block the
    show by name.
    """
    title = str(release.get("title") or "")
    if _title_blacklisted(title, policies, mappings=mappings):
        return True
    blocked = _policy_anilist_ids(policies)
    if blocked and release.get("canonical"):
        aid = _release_anilist_id(title, canonical=str(release["canonical"]), mappings=mappings)
        if aid in blocked:
            return True
    return False


def _card_key(
    release_title: str,
    *,
    canonical: str = "",
    mappings: dict[str, Any] | None = None,
    table: Any = None,
) -> str:
    """One review card per AniList entry -- a season or cour, as AniList splits them.

    A release no AniList entry answers for yet is a card of its own Erai
    name, which is again one season; choosing its AniList entry moves it over.
    """
    aid = _release_anilist_id(release_title, canonical=canonical, mappings=mappings, table=table)
    return f"anilist:{aid}" if aid else _mapping_key(release_title)


def review_items(
    state: dict[str, Any] | None = None, table: Any = None
) -> list[dict[str, Any]]:
    """Group held releases into one card per AniList entry."""
    state = _load_state() if state is None else state
    table = table if table is not None else anilist.cached_index()
    groups: dict[str, dict[str, Any]] = {}
    policies = _load_policies()
    mappings = _load_mappings()
    recent = {item.get("info_hash"): item for item in state.get("held", [])}
    for info_hash, release in state.get("releases", {}).items():
        if release.get("status") != "held" or not release.get("title"):
            continue
        if _is_blacklisted_release(release, policies=policies, mappings=mappings):
            continue
        saved_entry = release.get("entry") or {}
        item = {
            **saved_entry,
            **recent.get(info_hash, {}),
            "info_hash": info_hash,
            "title": release["title"],
            "reason": release.get("reason", "Held for review"),
        }
        policy = policies.get(_mapping_key(item["title"]))
        reason = str(item.get("reason", ""))
        if policy and policy.get("mode") == "blacklisted":
            continue
        if policy and policy.get("mode") == "german_allowed" and "German subtitles" in reason:
            continue
        key = _card_key(item["title"], mappings=mappings, table=table)
        source_title = anime_mod.clean_release_title(item["title"])
        row = groups.get(key)
        if row is None:
            row = groups[key] = {
                **item,
                "release_title": item["title"],
                "key": key,
                "source_title": source_title,
                "release_count": 0,
                "reasons": [],
                "keys": [],
            }
            if key.startswith("anilist:") and table is not None:
                entry = table.anime.get(int(key.split(":", 1)[1]))
                if entry is not None:
                    row.update(
                        anilist_id=entry.id,
                        anilist_title=entry.name,
                        english_title=entry.english,
                        anilist_poster_url=anilist.cover(entry.id),
                    )
        member = _mapping_key(item["title"])
        if member not in row["keys"]:
            row["keys"].append(member)
        row["release_count"] += 1
        if reason and reason not in row["reasons"]:
            row["reasons"].append(reason)
    return sorted(
        groups.values(),
        key=lambda row: str(row.get("anilist_title") or row["source_title"]).casefold(),
    )


def review_releases(key: str) -> list[dict[str, Any]]:
    """Every held release behind one review card.

    A group can hold dozens of releases that do not agree with each other --
    One Piece had sixty-three, some listing German and some not -- and the card
    only ever showed the first. Judging them needs all of them.
    """
    state = _load_state()
    mappings = _load_mappings()
    recent = {item.get("info_hash"): item for item in state.get("held", [])}
    rows: list[dict[str, Any]] = []
    for info_hash, release in state.get("releases", {}).items():
        title = str(release.get("title") or "")
        if release.get("status") != "held" or not title:
            continue
        if key not in {_card_key(title, mappings=mappings), _mapping_key(title)}:
            continue
        saved = release.get("entry") or {}
        rows.append(
            {
                "info_hash": info_hash,
                "title": title,
                "reason": release.get("reason", "Held for review"),
                "detail_url": saved.get("detail_url") or recent.get(info_hash, {}).get("detail_url"),
                "quality": saved.get("quality") or recent.get(info_hash, {}).get("quality"),
                # Shown so the disagreement inside a group is visible at a
                # glance rather than only in the release name.
                "german_in_title": title_lists_german_subtitles(title),
                "hevc": _is_hevc_title(title),
                # One card is one AniList entry: its own episode numbers.
                "season": None,
                "episode": (_erai_name_episode(title) or (None, None))[1],
                # A batch's first and last episode: it can be downloaded whole.
                "episodes": _release_range(title),
            }
        )
    rows.sort(
        key=lambda row: (
            row["season"] is None,
            row["season"] or 0,
            row["episode"] is None,
            row["episode"] or 0,
            row["title"],
        )
    )
    return rows


def mark_releases_owned(info_hashes: list[str]) -> dict[str, int]:
    """Dismiss held releases as already downloaded.

    Neither blacklisting nor deleting: the episode is in the library, the
    release is simply not wanted. Recorded as "existing" so discovery treats it
    the way it treats anything else it finds already on disk.
    """
    cleared = 0
    with _STATE_LOCK:
        state = _load_state()
        requests = _load_retry_requests()
        wanted = {str(value).casefold() for value in info_hashes}
        for info_hash, release in state.get("releases", {}).items():
            if info_hash.casefold() not in wanted or release.get("status") != "held":
                continue
            release.update(status="existing", reason="Already in the library")
            release.pop("retry_after", None)
            requests.pop(info_hash, None)
            cleared += 1
        if cleared:
            state["held"] = [
                row
                for row in state.get("held", [])
                if str(row.get("info_hash", "")).casefold() not in wanted
            ]
            _save_retry_requests(requests)
            _save_state(state)
    return {"ok": True, "cleared": cleared}


def mark_series_owned(key: str) -> dict[str, int]:
    """Dismiss every held release of one series as already downloaded."""
    return mark_releases_owned([row["info_hash"] for row in review_releases(key)])


def _policy_show_key(key: str, policy: dict[str, Any]) -> str:
    return _show_name_key(str(policy.get("source_title") or key))


def blacklist_items(policies: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One card per blacklisted AniList entry.

    An entry blocked twice -- under two Erai names, or from review and from
    the library -- is still one card. A decision made by name, per Erai
    season name, groups by show name as it always did until it is linked.
    """
    groups: dict[str, dict[str, Any]] = {}
    fields = ("anilist_id", "anilist_title", "anilist_poster_url", "english_title")
    for key, value in (_load_policies() if policies is None else policies).items():
        if value.get("mode") != "blacklisted":
            continue
        show = f"anilist:{value['anilist_id']}" if _per_entry(value) else _policy_show_key(key, value)
        row = groups.setdefault(show, {**value, "key": show, "keys": []})
        row["keys"].append(key)
        if len(str(value.get("source_title") or "")) < len(str(row.get("source_title") or "")):
            row["source_title"] = value.get("source_title")
        for field in fields:
            row[field] = row.get(field) or value.get(field)
    # Two names of one anime -- romaji and English, or two of its synonyms --
    # are still one show when they share an AniList id.
    merged: dict[str, dict[str, Any]] = {}
    for row in groups.values():
        identity = f"anilist:{row['anilist_id']}" if row.get("anilist_id") else f"name:{row['key']}"
        slot = merged.get(identity)
        if slot is None:
            merged[identity] = row
            continue
        slot["keys"] = sorted({*slot["keys"], *row["keys"]})
        if len(str(row.get("source_title") or "")) < len(str(slot.get("source_title") or "")):
            slot["source_title"] = row.get("source_title")
        for field in fields:
            slot[field] = slot.get(field) or row.get(field)
    for row in merged.values():
        row["linked"] = bool(row.get("anilist_id"))
        if row["linked"] and not row.get("anilist_poster_url"):
            row["anilist_poster_url"] = anilist.cover(row["anilist_id"])
    return sorted(
        merged.values(),
        key=lambda row: str(row.get("anilist_title") or row.get("source_title", "")).casefold(),
    )


def _blacklist_members(policies: dict[str, Any], key: str) -> set[str]:
    """Every policy key behind one blacklist card, however it was merged."""
    for row in blacklist_items(policies):
        if row["key"] == key or key in row["keys"]:
            return set(row["keys"])
    return {key} if key in policies else set()


def link_blacklist(key: str, anime: dict[str, Any]) -> dict[str, Any]:
    """Tie a blacklist card to its AniList entry.

    ``anime`` is the entry as ``anilist.Anime.as_dict()`` or
    ``anilist.as_choice()`` give it. From then on every release named after
    any of its titles is blacklisted, which is what makes a card that no
    title search recognised actually match something.
    """
    aid = int(anime["anilist_id"])
    english = anime.get("english") or anime.get("english_title")
    titles = list(anime.get("matching_titles") or ()) or [
        title for title in dict.fromkeys((anime.get("romaji"), english, anime.get("name"))) if title
    ]
    with _STATE_LOCK:
        policies = _load_policies()
        members = _blacklist_members(policies, key)
        if not members:
            raise ValueError("Blacklisted series was not found")
        for member in members:
            policies[member].update(
                anilist_id=aid,
                anilist_title=anime.get("name") or anime.get("title") or anime.get("romaji"),
                english_title=english or policies[member].get("english_title"),
                anilist_titles=titles,
                anilist_poster_url=anime.get("cover") or anime.get("poster_url") or anilist.cover(aid),
                updated_at=time.time(),
            )
        _save_policies(policies)
        # Anything already held that the new titles now cover goes too.
        state = _load_state()
        mappings = _load_mappings()
        caught = 0
        for row in state.get("releases", {}).values():
            if row.get("status") in {"held", "queued"} and _is_blacklisted_release(
                row, policies=policies, mappings=mappings
            ):
                row.update(status="blacklisted", reason="Series blacklisted by user")
                caught += 1
        if caught:
            _prune_holds(state)
            _save_state(state)
    return {"ok": True, "keys": sorted(members), "caught": caught}


async def blacklist_show(
    *,
    title: str,
    source_title: str,
    anilist_ids: list[int] | None,
    folders: list[Path],
    anime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Delete a show from the library, remove its torrents, and never fetch it again.

    A library card is a whole show, every season of it on disk, so every
    AniList entry of it is blocked -- each as its own blacklist card, which
    can be restored on its own. The entries are the card's own, plus
    ``anime`` if one was picked for it. Only a show neither can place is
    blocked by name.
    """
    name = source_title or title
    key = _show_name_key(name)
    if not key:
        raise ValueError("The show has no name to blacklist it under")
    wanted = [int(aid) for aid in anilist_ids or [] if str(aid).isdigit()]
    if anime and str(anime.get("anilist_id") or "").isdigit():
        wanted.append(int(anime["anilist_id"]))
    entries: dict[int, anilist.Anime] = {}
    for aid in dict.fromkeys(wanted):
        entry = await anilist.anime(aid)
        if entry is not None:
            entries[aid] = entry
    await asyncio.to_thread(_blacklist_show, key, name, title, entries)
    purged = await purge_series(key, english_title=title, delete_files=True, extra_folders=folders)
    return {"ok": True, "key": key, "entries": sorted(entries), **purged}


def _blacklist_show(key: str, name: str, title: str, entries: dict[int, Any]) -> None:
    """The state half of blacklist_show, off the event loop."""
    with _STATE_LOCK:
        policies = _load_policies()
        if entries:
            for entry in entries.values():
                policies[f"anilist:{entry.id}"] = _entry_policy(entry, origin="library")
        else:
            policies[key] = {
                "mode": "blacklisted",
                "source_title": name,
                "english_title": title,
                "origin": "library",
                "updated_at": time.time(),
            }
        _save_policies(policies)
        state = _load_state()
        mappings = _load_mappings()
        requests = _load_retry_requests()
        for info_hash, row in state.get("releases", {}).items():
            if row.get("status") not in {"blacklisted", "done", "existing"} and (
                _is_blacklisted_release(row, policies=policies, mappings=mappings)
            ):
                row.update(status="blacklisted", reason="Series blacklisted by user")
                requests.pop(info_hash, None)
        # Tracked, it would come back as an empty library card.
        for aid in entries:
            state.get("anime", {}).pop(str(aid), None)
        _prune_holds(state)
        _save_retry_requests(requests)
        _save_state(state)


def _request_series_retries(state: dict[str, Any], key: str) -> int:
    requests = _load_retry_requests()
    catalog = _catalog_entries(state)
    requested = 0
    for info_hash, release in state.get("releases", {}).items():
        if release.get("status") != "held" or _mapping_key(release.get("title", "")) != key:
            continue
        requests[info_hash] = {
            "title": release["title"],
            "entry": release.get("entry") or catalog.get(info_hash),
        }
        requested += 1
    _save_retry_requests(requests)
    return requested


async def review_action(info_hash: str, action: str) -> dict[str, Any]:
    """Persist a series decision and schedule the affected releases immediately.

    The work -- reading and writing the 20 MB release state, checking every
    release against the blacklist -- runs in a thread: done on the event loop
    it took seconds, during which every page of the web UI waited.
    """
    await anilist.index()  # the review card is an AniList entry; make sure it can be told
    result = await asyncio.to_thread(_review_action, info_hash, action)
    if result["requested"]:
        _start_retry_cycle()
    return result


def _review_action(info_hash: str, action: str) -> dict[str, Any]:
    with _STATE_LOCK:
        state = _load_state()
        release = state.get("releases", {}).get(info_hash)
        if not release or not release.get("title"):
            raise ValueError("Held release was not found")
        title = release["title"]
        key = _mapping_key(title)
        mappings = _load_mappings()
        card = _card_key(title, mappings=mappings)
        # A card is one AniList entry, so a decision is too: every release
        # held under it, whatever Erai name each carries.
        titles = {key: title}
        for row in state.get("releases", {}).values():
            row_title = str(row.get("title") or "")
            if (
                row.get("status") == "held"
                and row_title
                and _card_key(row_title, mappings=mappings) == card
            ):
                titles.setdefault(_mapping_key(row_title), row_title)
        policies = _load_policies()
        requested = 0
        blacklisted = 0
        if action == "recheck":
            requested = sum(_request_series_retries(state, member) for member in titles)
        elif action == "allow_german":
            for member, member_title in titles.items():
                policies[member] = {
                    "mode": "german_allowed",
                    "source_title": anime_mod.clean_release_title(member_title),
                    "updated_at": time.time(),
                }
            _save_policies(policies)
            requested = sum(_request_series_retries(state, member) for member in titles)
        elif action == "blacklist":
            entry = None
            if card.startswith("anilist:"):
                index = anilist.cached_index()
                entry = index.anime.get(int(card.split(":", 1)[1])) if index else None
            if entry is not None:
                # Exactly this AniList entry: one season or cour. Its sequels
                # and prequels are entries of their own and stay as they are.
                policies[card] = _entry_policy(entry, source_title=anime_mod.clean_release_title(title))
            else:
                # Not an AniList entry yet: the Erai name, which is one season too.
                for member, member_title in titles.items():
                    policies[member] = {
                        "mode": "blacklisted",
                        "source_title": anime_mod.clean_release_title(member_title),
                        "updated_at": time.time(),
                    }
            _save_policies(policies)
            requests = _load_retry_requests()
            matched: list[str] = []
            for release_hash, row in state.get("releases", {}).items():
                if _is_blacklisted_release(row, policies=policies, mappings=mappings):
                    row.update(status="blacklisted", reason="Series blacklisted by user")
                    requests.pop(release_hash, None)
                    matched.append(release_hash)
            held_lookup = state.get("releases", {})
            state["held"] = [
                row
                for row in state.get("held", [])
                if not _is_blacklisted_release(
                    held_lookup.get(row.get("info_hash"), {"title": row.get("title", "")}),
                    policies=policies,
                    mappings=mappings,
                )
            ]
            _save_retry_requests(requests)
            _save_state(state)
            blacklisted = len(matched)
        else:
            raise ValueError("Unknown review action")
    return {
        "ok": True,
        "requested": requested,
        "blacklisted": blacklisted,
        "key": key,
        "card": card,
        "keys": sorted(titles),
    }


async def review_card(info_hash: str) -> str:
    """The review card a held release sits on: its AniList entry, or its Erai name."""
    await anilist.index()

    def find() -> str:
        with _STATE_LOCK:
            release = (_load_state().get("releases", {}) or {}).get(info_hash)
        if not release or not release.get("title"):
            raise ValueError("Held release was not found")
        return _card_key(str(release["title"]))

    return await asyncio.to_thread(find)


def _entry_policy(entry: anilist.Anime, *, source_title: str = "", origin: str = "review") -> dict[str, Any]:
    """The blacklist decision for one AniList entry."""
    return {
        "mode": "blacklisted",
        "anilist_id": entry.id,
        "anilist_title": entry.name,
        "english_title": entry.english,
        # Its own names only, so a release under exactly one of them is
        # caught before it is resolved at all.
        "anilist_titles": [name for name in dict.fromkeys((entry.title, entry.english)) if name],
        "anilist_poster_url": anilist.cover(entry.id),
        "source_title": source_title or entry.title,
        "origin": origin,
        "updated_at": time.time(),
    }


def entry_files(aid: int) -> list[Path]:
    """The files of one AniList entry, for "Discard and delete".

    Every copy the file index holds of its episodes, in the library and in
    staging. Never the rest of its show's folder: the other seasons are
    entries of their own.
    """
    found: list[Path] = []
    for paths in _entry_episode_files(int(aid)).values():
        found.extend(path for path in paths if path not in found)
    return found


def _forget_deleted(paths: list[Path]) -> None:
    """Take files that are gone out of the file index.

    A key has a copy in the library and one in staging; it goes only once
    neither is left, so deleting an old copy never orphans the new one.
    """
    keys = {key for path in paths if (key := anime_files.key(path))}
    gone = [key for key in keys if not anime_files.existing(key)]
    if gone:
        with anime_files.editing() as rows:
            for key in gone:
                rows.pop(key, None)


def _series_roots() -> list[Path]:
    """Every directory a blacklisted series may legitimately occupy."""
    settings = get_settings()
    roots = [Path(settings.transfer.anime_shows_dir), Path(settings.output.directory)]
    return [root for root in roots if str(root)]


def _contained_by(path: Path, roots: list[Path]) -> bool:
    """Refuse to delete anything outside the configured library roots.

    Paths come from the file index and from records kept for months, so this
    is the check that keeps a stale record -- or a symlink out of the tree --
    from reaching the rest of the disk.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


# What the layout writes into a show's folders besides the episodes: once
# only these are left, the folder holds nothing of the show any more.
_LAYOUT_LEFTOVERS = frozenset({"tvshow.nfo", "season.nfo", "poster.jpg", "fanart.jpg"})


def _prune_emptied(folder: Path, roots: list[Path]) -> list[str]:
    """Remove a folder left with nothing but the layout's NFO files and pictures, and its parent if so too."""
    removed: list[str] = []
    for current in (folder, folder.parent):
        if not _strictly_inside(current, roots):
            break
        try:
            children = list(current.iterdir())
        except OSError:
            break
        if any(child.is_dir() or child.name.casefold() not in _LAYOUT_LEFTOVERS for child in children):
            break
        try:
            for child in children:
                child.unlink()
            current.rmdir()
        except OSError:
            break
        removed.append(str(current))
    return removed


def _strictly_inside(path: Path, roots: list[Path]) -> bool:
    """Inside a library root and not the root itself.

    _contained_by is true for a root too, and a show folder that resolved to
    the anime root would take the entire library with it.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            base = root.resolve()
        except OSError:
            continue
        if resolved != base and resolved.is_relative_to(base):
            return True
    return False


async def purge_series(
    key: str,
    *,
    english_title: str = "",
    delete_files: bool,
    extra_folders: list[Path] | None = None,
    extra_files: list[Path] | None = None,
) -> dict[str, Any]:
    """Remove a blacklisted series' torrents and, optionally, its episodes.

    Downloading an episode the user has rejected wastes the same scarce disk
    twice, so the torrents go regardless. Deleting what is already published is
    a separate decision and only happens when asked, and only what the caller
    names: a library card's folders, an entry's files. ``english_title`` is
    no longer used to find folders by name -- bankai lays the library out, so
    the card knows its folders.
    """
    from bankai.torrent.qbittorrent import QBittorrentClient

    def blacklisted_hashes() -> set[str]:
        with _STATE_LOCK:
            state = _load_state()
            policies = _load_policies()
            mappings = _load_mappings()
            return {
                info_hash.casefold()
                for info_hash, release in state.get("releases", {}).items()
                if _is_blacklisted_release(release, policies=policies, mappings=mappings)
            }

    hashes = await asyncio.to_thread(blacklisted_hashes)
    removed_torrents = 0
    if hashes:
        qbit = QBittorrentClient()
        try:
            await qbit.login()
            # Only what qBittorrent still has. Every blacklisted release ever
            # was asked for, one request each -- hundreds, long since removed.
            present = {
                torrent.hash.casefold()
                for torrent in await qbit.list_torrents(category=get_settings().qbittorrent.category)
            }
            for info_hash in sorted(hashes & present):
                try:
                    await qbit.remove(info_hash, delete_files=True)
                    removed_torrents += 1
                except Exception as exc:
                    log.warning("Could not remove torrent %s: %s", info_hash[:8], exc)
        except Exception as exc:
            log.warning("Could not reach qBittorrent while purging %s: %s", key, exc)
        finally:
            await qbit.aclose()

    deleted = await asyncio.to_thread(
        _delete_series_files,
        english_title=english_title,
        delete_files=delete_files,
        extra_folders=extra_folders,
        extra_files=extra_files,
    )
    return {"ok": True, "key": key, "removed_torrents": removed_torrents, **deleted}


def _delete_series_files(
    *,
    english_title: str = "",
    delete_files: bool,
    extra_folders: list[Path] | None,
    extra_files: list[Path] | None,
) -> dict[str, Any]:
    """The file half of purge_series; walking and deleting over 9p is slow.

    What is deleted leaves the file index too, so nothing counts it as on
    disk and the layout does not plan around it.
    """
    from bankai.web.anime_layout import _companions

    deleted_files = 0
    freed_bytes = 0
    deleted_folders: list[str] = []
    gone: list[Path] = []
    if delete_files and extra_folders:
        roots = _series_roots()
        folders: list[Path] = []
        for folder in extra_folders:
            if folder not in folders and folder.is_dir():
                folders.append(folder)
        for folder in folders:
            if not _strictly_inside(folder, roots):
                log.warning("Refusing to delete %s: not inside a library root", folder)
                continue
            count = size = 0
            inside: list[Path] = []
            for path in folder.rglob("*"):
                if path.is_file():
                    try:
                        size += path.stat().st_size
                        count += 1
                        inside.append(path)
                    except OSError:
                        pass
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                log.warning("Could not delete %s: %s", folder, exc)
                continue
            deleted_folders.append(str(folder))
            deleted_files += count
            freed_bytes += size
            gone.extend(inside)
    if delete_files and extra_files:
        roots = _series_roots()
        emptied: set[Path] = set()
        named = set(extra_files)
        for path in extra_files:
            if not path.is_file() or not _strictly_inside(path, roots):
                continue
            # Its subtitles, NFO and thumbnail go with it, unless named themselves.
            companions = [extra for extra in _companions(path) if extra not in named]
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError as exc:
                log.warning("Could not delete %s: %s", path, exc)
                continue
            deleted_files += 1
            freed_bytes += size
            gone.append(path)
            emptied.add(path.parent)
            for extra in companions:
                with suppress(OSError):
                    extra.unlink()
        for folder in emptied:
            # A season left with nothing goes too, and its show if that was
            # the last; a folder still holding other episodes stays.
            deleted_folders.extend(_prune_emptied(folder, roots))
    if gone:
        _forget_deleted(gone)
    return {
        "deleted_files": deleted_files,
        "deleted_folders": deleted_folders,
        "freed_bytes": freed_bytes,
    }


def remove_blacklist(key: str, *, schedule: bool = True) -> dict[str, Any]:
    with _STATE_LOCK:
        policies = _load_policies()
        # Everything behind the one card: one AniList entry, or -- for a
        # decision made by name -- the Erai names it was made under.
        members = _blacklist_members(policies, key)
        if not members:
            raise ValueError("Blacklisted series was not found")
        entries = {int(policies[m]["anilist_id"]) for m in members if _per_entry(policies[m])}
        names = {m for m in members if not _per_entry(policies[m])}
        for member in members:
            policies.pop(member)
        _save_policies(policies)
        state = _load_state()
        mappings = _load_mappings()
        restored: set[str] = set(names)
        for release in state.get("releases", {}).values():
            if release.get("status") != "blacklisted":
                continue
            title = str(release.get("title") or "")
            if _mapping_key(title) not in names and not (
                entries
                and _release_anilist_id(
                    title, canonical=str(release.get("canonical") or ""), mappings=mappings
                )
                in entries
            ):
                continue
            release["status"] = "held"
            release["reason"] = "Blacklist removed; release is ready for a fresh check"
            release["retry_after"] = 0
            restored.add(_mapping_key(title))
            saved_entry = release.get("entry")
            if saved_entry:
                _hold(state, _entry_from_dict(saved_entry), release["reason"])
        requested = sum(_request_series_retries(state, member) for member in restored)
        _save_state(state)
    # From a thread, the caller starts the retries on its own event loop.
    if requested and schedule:
        _start_retry_cycle()
    return {"ok": True, "requested": requested}


def retry_series(release_title: str) -> int:
    with _STATE_LOCK:
        state = _load_state()
        requested = _request_series_retries(state, _mapping_key(release_title))
    if requested:
        _start_retry_cycle()
    return requested


def _prune_holds(state: dict[str, Any]) -> None:
    state["held"] = [
        item
        for item in state.get("held", [])
        if state["releases"].get(item.get("info_hash"), {}).get("status") == "held"
    ]


def _catalog_entries(state: dict[str, Any]) -> dict[str, dict]:
    result = {}
    for index in [state["backfill"], *state.get("series_catalogs", {}).values()]:
        for name in ("catalog_1080", "catalog_720"):
            for row in index.get(name, {}).values():
                result[row["info_hash"]] = row
    return result


def _reserved_torrent_bytes(state: dict[str, Any], torrents: list[Any]) -> int:
    """Estimate only bytes qBittorrent has not downloaded yet.

    Completed torrents are already reflected in the filesystem free-space
    reading. Counting their full size again eventually made discovery think
    the 100 GiB reserve was exhausted even while terabytes remained free.
    """
    total = 0
    for item in torrents:
        progress = max(0.0, min(1.0, float(item.progress)))
        if progress >= 1.0:
            continue
        release_size = int(state["releases"].get(item.hash, {}).get("size_bytes", 0))
        size = max(int(item.size_bytes), release_size)
        total += int(size * (1.0 - progress))
    return total


def _verified_automation_hashes() -> set[str]:
    """Hashes whose autonomous Erai output is durably present in the library."""

    verified: set[str] = set()
    for job in bgjobs.list_jobs():
        args = job.args or []
        if (
            job.status != "done"
            or not args
            or args[0] != "anime-download"
            or not ({"--cleanup-torrent", "--require-german-subtitles"} & set(args))
            or not job.final_path
            or not Path(job.final_path).is_file()
        ):
            continue
        info_hash = bgjobs.argument_value(args, "--info-hash")
        if info_hash and re.fullmatch(r"[0-9a-fA-F]{40}", info_hash):
            verified.add(info_hash.casefold())
    return verified


# Holding a download slot without moving. qBittorrent's "don't count slow
# torrents" does not cover these: a torrent that never received metadata, or
# one with nobody to download from, still takes a slot.
_STUCK_STATES = {"stalledDL", "metaDL"}
_STUCK_SECONDS = 6 * 3600


async def _rotate_stuck_torrents(
    state: dict[str, Any], qbit: Any, torrents: list[Any], *, now: float | None = None
) -> int:
    """Send downloads that have sat in a slot for hours doing nothing to the back.

    Twenty dead swarms -- one old show nobody seeds -- held every active slot
    for two days while 477 queued downloads with seeders waited behind them.
    Nothing is removed: each gets another turn once the queue comes round.
    qBittorrent does not say when a torrent left the queue, so bankai notes
    when it first saw one stuck and counts from there.
    """
    now = time.time() if now is None else now
    stuck = {torrent.hash: torrent for torrent in torrents if torrent.state in _STUCK_STATES}
    # Only what is stuck right now is remembered; one that moved starts over.
    seen = {
        info_hash: since
        for info_hash, since in state.get("stuck_since", {}).items()
        if info_hash in stuck
    }
    rotate = []
    for info_hash, torrent in stuck.items():
        since = seen.setdefault(info_hash, now)
        if now - since >= _STUCK_SECONDS and torrent.last_activity < since:
            rotate.append(info_hash)
    if rotate:
        await qbit.bottom_priority(rotate)
        seen = {info_hash: since for info_hash, since in seen.items() if info_hash not in rotate}
        log.info("[anime] %d stuck download(s) moved to the back of the queue", len(rotate))
    state["stuck_since"] = seen
    return len(rotate)


async def _cleanup_verified_torrents(qbit: Any, torrents: list[Any]) -> int:
    """Remove only completed Erai downloads with a verified final publication."""

    verified = await asyncio.to_thread(_verified_automation_hashes)
    removed = 0
    for torrent in torrents:
        info_hash = str(torrent.hash).casefold()
        if float(torrent.progress) < 1.0 or info_hash not in verified:
            continue
        try:
            await qbit.remove(info_hash, delete_files=True)
            removed += 1
            log.info(
                "[anime] reclaimed completed Erai torrent %s + files: %s",
                info_hash[:8],
                torrent.name,
            )
        except Exception as exc:
            # Keep the cycle useful when one qBittorrent row is transiently
            # locked; the next scheduled cycle will retry the same verified row.
            log.warning("[anime] could not reclaim torrent %s: %s", info_hash[:8], exc)
    return removed


async def _retry_candidates(state: dict[str, Any], client: httpx.AsyncClient) -> list:
    candidates = []
    catalog = _catalog_entries(state)
    for info_hash, request in _load_retry_requests().items():
        if state["releases"].get(info_hash, {}).get("status") != "held":
            continue
        row = request.get("entry") or catalog.get(info_hash)
        if row:
            candidates.append(_entry_from_dict(row))
            continue
        # Legacy holds did not preserve the original trusted/remake flags.
        # Recover the exact hash from Nyaa rather than inventing those flags.
        try:
            response = await client.get(
                f"{_NYAA_BASE}/",
                params={"u": "Erai-raws", "c": "1_2", "q": request["title"]},
            )
            response.raise_for_status()
            candidates.extend(
                item for item in parse_listing(response.text) if item.info_hash == info_hash
            )
        except Exception as exc:
            log.warning("Held release recovery failed for %s: %s", info_hash, exc)
        await asyncio.sleep(get_settings().anime.backfill_request_delay_seconds)
    for item in candidates:
        anime_mod._DETAIL_CACHE.pop(item.detail_url, None)
    return sorted(candidates, key=_episode_order)


@functools.lru_cache(maxsize=65536)
def _mapping_key(release_title: str) -> str:
    structured = anime_mod.deconstruct_release(release_title)
    query = (
        anime_mod.release_part(release_title)[0]
        if structured
        else anime_mod.clean_release_title(release_title)
    )
    return " ".join(re.findall(r"\w+", query.casefold(), re.UNICODE))


# Whatever says which season, cour or part a release belongs to. Stripped
# repeatedly, so "Final Season Part 2" goes as well as "Season 2".
_SHOW_SEASON_SUFFIX = re.compile(
    r"(?:\s*-)?\s+(?:S\d{1,2}|Season\s+\d{1,2}|\d{1,2}(?:st|nd|rd|th)\s+Season"
    r"|(?:The\s+)?Final\s+Season|Part\s+\d{1,2}|Cour\s+\d{1,2})\s*$",
    re.IGNORECASE,
)


def _show_name_key(name: str) -> str:
    """The identity of an anime across all of its seasons, from a cleaned name."""
    value = name.strip()
    while True:
        stripped = _SHOW_SEASON_SUFFIX.sub("", value).strip()
        if not stripped or stripped == value:
            break
        value = stripped
    # What clean_release_title leaves of "- The Final Season" once it has
    # taken the "Final Season" itself.
    value = re.sub(r"\s*-\s*The$", "", value, flags=re.IGNORECASE) or value
    return " ".join(re.findall(r"\w+", value.casefold(), re.UNICODE))


def _show_key(release_title: str) -> str:
    """One review card per anime, not per season.

    Cards were grouped by the Erai name of each season, so every season of a
    show was its own card -- "S2" and "S3", "2nd Season" and "Final Season" --
    and the same season could split in two when a release name carried "END",
    "(Repack)" or "(AAC 2.0)" and lost its season marker in parsing. A
    decision made on one of those cards left the others waiting.
    """
    return _show_name_key(anime_mod.clean_release_title(release_title))


def _load_mappings() -> dict[str, dict[str, Any]]:
    with _STATE_LOCK:
        path = _state_path().with_name("erai_mappings.json")
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}


def save_anilist_mapping(release_title: str, anime: dict[str, Any], *, episode_offset: int = 0) -> str:
    """Remember which AniList entry a show name is, chosen by the user in review.

    ``anime`` carries ``anilist_id`` and its name, as ``anilist.Anime.as_dict()``
    or ``anilist.as_choice()`` give it.
    """
    with _STATE_LOCK:
        mappings = _load_mappings()
        key = _mapping_key(release_title)
        mappings[key] = {
            "anilist_id": int(anime["anilist_id"]),
            "anilist_title": anime.get("name") or anime.get("title") or anime.get("romaji"),
            "anilist_episode_offset": int(episode_offset),
        }
        path = _state_path().with_name("erai_mappings.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(mappings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return key


def _needs_consideration(state: dict[str, Any], entry: anime_mod.NyaaEntry) -> bool:
    if _title_blacklisted(entry.title):
        return False
    previous = state["releases"].get(entry.info_hash)
    if previous is None:
        return True
    if previous.get("status") != "held":
        return False
    if entry.info_hash in _load_retry_requests():
        return True
    reason = str(previous.get("reason", ""))
    # Identity holds of every era -- TVDB's and AniDB's included, until their
    # next look settles them on AniList -- and the German-subtitle ones.
    if not any(marker in reason for marker in ("TVDB", "AniDB", "AniList", "German subtitles")):
        return False
    return _mapping_key(entry.title) in _load_mappings() or time.time() >= float(
        previous.get("retry_after", 0)
    )


def _default_state() -> dict[str, Any]:
    return {
        "version": 5,
        "series": {},
        "series_catalogs": {},
        "last_poll": None,
        "last_success": None,
        "last_error": None,
        "last_enqueued": 0,
        "releases": {},
        "canonical": {},
        "held": [],
        "backfill": {
            "phase": "2160",
            "page": 1,
            "frontier": [{"include": [], "exclude": [], "page": 1}],
            "queries_completed": 0,
            "queries_split": 0,
            "catalog_1080": {},
            "catalog_720": {},
            "complete": False,
        },
    }


def _load_state() -> dict[str, Any]:
    with _STATE_LOCK:
        path = _state_path()
        if not path.exists():
            return _default_state()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return _default_state()
        state = _default_state()
        backfill = dict(state["backfill"])
        state.update(raw if isinstance(raw, dict) else {})
        backfill.update(raw.get("backfill", {}) if isinstance(raw, dict) else {})
        state["backfill"] = backfill
        if state.get("version", 1) < 2:
            for item in state["releases"].values():
                if item.get("status") == "held" and "TVDB" in item.get("reason", ""):
                    item["retry_after"] = 0
            state["version"] = 2
        for index in [state["backfill"], *state.get("series_catalogs", {}).values()]:
            if index.get("complete"):
                index.update({"phase": "ready", "frontier": []})
        if state.get("version", 1) < 4:
            for item in state["releases"].values():
                if item.get("status") == "held":
                    item["retry_after"] = 0
        if state.get("version", 1) < 5:
            # Older catalogues ranked HEVC above AVC but still admitted AVC.
            # Rebuild search indexes under the hard HEVC-only policy while
            # preserving release/job history and already downloaded episodes.
            state["backfill"] = _default_state()["backfill"]
            for index in state.get("series_catalogs", {}).values():
                preserved = {
                    key: index[key]
                    for key in (
                        "title_query",
                        "title_queries",
                        "title_query_index",
                        "parent_tvdb_id",
                        "high_only",
                    )
                    if key in index
                }
                index.clear()
                index.update({**_default_state()["backfill"], **preserved})
        state["version"] = 5
        return state


# The sections of the release state that are merged when a cycle saves.
_MERGED_SECTIONS = ("releases", "canonical", "anime")
# The state a running cycle loaded, as it was: {"id": id(state), "sections": {...}}.
_CYCLE_BASE: ContextVar[dict | None] = ContextVar("erai_cycle_base", default=None)


def _begin_cycle_state(state: dict[str, Any]) -> Any:
    """Remember what a cycle loaded, so its saves can merge; returns a reset token."""
    return _CYCLE_BASE.set(
        {"id": id(state), "sections": {name: copy.deepcopy(state.get(name) or {}) for name in _MERGED_SECTIONS}}
    )


def _merge_changes_made_elsewhere(state: dict[str, Any], base: dict[str, Any]) -> None:
    """Take what changed on disk since the cycle loaded, where the cycle left it alone.

    A cycle holds the state in memory for minutes. Written back whole, it
    undid every decision made meanwhile in the web process: releases the user
    discarded, or marked as already downloaded, came back as held. Per entry,
    three ways: changed on disk and not by the cycle, the disk wins; changed
    by the cycle, the cycle does; added or removed elsewhere, so be it.
    """
    disk = _load_state()
    for name in _MERGED_SECTIONS:
        mine = state.setdefault(name, {})
        original = base["sections"].get(name) or {}
        theirs = disk.get(name) or {}
        for key, value in theirs.items():
            if key not in original:
                mine.setdefault(key, value)
            elif value != original[key] and mine.get(key) == original[key]:
                mine[key] = value
        for key in original.keys() - theirs.keys():
            if key in mine and mine[key] == original[key]:
                del mine[key]
    _prune_holds(state)


def _save_state(state: dict[str, Any]) -> None:
    with _STATE_LOCK:
        base = _CYCLE_BASE.get()
        cycle = base is not None and base.get("id") == id(state)
        if cycle:
            _merge_changes_made_elsewhere(state, base)
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        # Compact: indented, the file was 25 MB and took 0.46 s to read back
        # -- on every page, every cycle step. Compact it is 20 MB and 0.25 s.
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        tmp.replace(path)
        if cycle:
            # What is on disk now is the base the next save merges against.
            base["sections"] = {name: copy.deepcopy(state.get(name) or {}) for name in _MERGED_SECTIONS}


# Erai-raws advertises subtitles two different ways. The AVC releases list
# them in the Nyaa description; the HEVC ones carry bracketed language tags in
# the release title itself and say nothing in the description. Reading only the
# description held 150 HEVC episodes that stated [GER] in their own name.
_GERMAN_TAG = re.compile(r"\[\s*(?:GER|DEU|GERMAN|DEUTSCH)(?:[-_][A-Z]{2})?\s*\]", re.I)


def title_lists_german_subtitles(title: str) -> bool:
    """Explicit German among a release title's bracketed language tags."""
    if not title:
        return False
    return _GERMAN_TAG.search(title) is not None


def has_explicit_german_subtitles(description: str) -> bool:
    """Require explicit positive subtitle evidence in Markdown or HTML."""
    if not description:
        return False
    description = html.unescape(description)
    description = re.sub(r"(?i)<br\s*/?>", "\n", description)
    description = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", description)
    description = re.sub(r"<[^>]+>", "", description)
    marker = re.search(r"(?i)subtitles?(?:\s+info)?\s*:?", description)
    section = description[marker.start() : marker.start() + 5000] if marker else description
    for line in section.splitlines():
        line = line.strip().strip("|").strip().replace("**", "").replace("__", "").replace("`", "")
        if re.search(r"(?i)\b(?:not|no|none|unavailable|missing|removed)\b", line):
            continue
        format_present = re.search(r"(?i)\b(?:ASS|SSA|SRT|VTT)\b", line)
        if _GERMAN_LINE.search(line) and (marker or format_present):
            return True
        if marker and re.search(r"(?i)\b(?:German|Deutsch|ger|deu)\b", line) and format_present:
            return True
        if _CR_GERMAN.search(line) and format_present:
            return True
    return False


def _resolution(entry: anime_mod.NyaaEntry) -> int:
    match = re.search(r"(?<!\d)(2160|1080|720|480)p\b", entry.quality or entry.title, re.I)
    return int(match.group(1)) if match else 0


def _is_hevc_title(title: str) -> bool:
    """Accept explicit HEVC/H.265/x265 release markers only."""
    return re.search(r"(?i)(?:\bhevc\b|\bx265\b|\bh[.\s-]?265\b)", title or "") is not None


def _is_hevc(entry: anime_mod.NyaaEntry) -> bool:
    return _is_hevc_title(entry.title)


def _release_key(entry: anime_mod.NyaaEntry) -> str | None:
    season, episode = anime_mod.release_episode_info(entry.title)
    if episode is None:
        return None
    structured = anime_mod.deconstruct_release(entry.title)
    title = structured[0] if structured else anime_mod.clean_release_title(entry.title)
    normalized = " ".join(re.findall(r"\w+", title.casefold(), re.UNICODE))
    return f"{normalized}|{season or 0}|{episode}"


def _rank(entry: anime_mod.NyaaEntry) -> tuple[int, int, int, int]:
    title = entry.title.casefold()
    source = 3 if "web-dl" in title or "web dl" in title else 2 if "webrip" in title else 1
    codec = 3 if any(x in title for x in ("hevc", "x265", "h265")) else 2
    return (_resolution(entry), source, codec, entry.seeders)


def _entry_dict(entry: anime_mod.NyaaEntry) -> dict[str, Any]:
    row = asdict(entry)
    row["anilist"] = None
    return row


def _entry_from_dict(row: dict[str, Any]) -> anime_mod.NyaaEntry:
    allowed = {field for field in anime_mod.NyaaEntry.__dataclass_fields__}
    return anime_mod.NyaaEntry(**{key: value for key, value in row.items() if key in allowed})


def parse_listing(html_text: str) -> list[anime_mod.NyaaEntry]:
    """Parse one normal Nyaa uploader-results page for historical backfill."""

    tree = HTMLParser(html_text)
    entries: list[anime_mod.NyaaEntry] = []
    for row in tree.css("tbody tr"):
        links = [
            node
            for node in row.css('a[href^="/view/"]')
            if re.fullmatch(r"/view/\d+", node.attributes.get("href", ""))
        ]
        title_node = next(
            (node for node in links if node.attributes.get("title")), links[-1] if links else None
        )
        magnet_node = row.css_first('a[href^="magnet:"]')
        torrent_node = row.css_first('a[href$=".torrent"]')
        if title_node is None or magnet_node is None or torrent_node is None:
            continue
        detail_path = title_node.attributes.get("href", "")
        id_match = re.search(r"/view/(\d+)", detail_path)
        magnet = html.unescape(magnet_node.attributes.get("href", ""))
        hash_match = re.search(r"(?i)urn:btih:([0-9a-f]{40})", magnet)
        if not id_match or not hash_match:
            continue
        cells = row.css("td")
        title = html.unescape(title_node.attributes.get("title") or title_node.text(strip=True))
        size = cells[3].text(strip=True) if len(cells) > 3 else ""
        timestamp_node = row.css_first("td[data-timestamp]")
        timestamp = timestamp_node.attributes.get("data-timestamp") if timestamp_node else None
        published = None
        if timestamp and timestamp.isdigit():
            published = datetime.fromtimestamp(int(timestamp), UTC).isoformat()
        numeric = []
        for cell in cells[-3:]:
            try:
                numeric.append(int(cell.text(strip=True).replace(",", "")))
            except ValueError:
                numeric.append(0)
        season, episode = anime_mod.release_episode_info(title)
        classes = set((row.attributes.get("class") or "").split())
        entries.append(
            anime_mod.NyaaEntry(
                id=int(id_match.group(1)),
                title=title,
                download_url=urljoin(_NYAA_BASE, torrent_node.attributes.get("href", "")),
                detail_url=urljoin(_NYAA_BASE, detail_path),
                magnet_uri=magnet,
                info_hash=hash_match.group(1).casefold(),
                category_id="1_2",
                category="Anime - English-translated",
                size=size,
                size_bytes=anime_mod._size_bytes(size),
                seeders=numeric[0] if numeric else 0,
                leechers=numeric[1] if len(numeric) > 1 else 0,
                downloads=numeric[2] if len(numeric) > 2 else 0,
                comments=0,
                trusted="success" in classes,
                remake="danger" in classes,
                published_at=published,
                publisher=anime_mod._publisher(title),
                quality=anime_mod._quality(title),
                season=season,
                episode=episode,
            )
        )
    return entries


def _existing_storage_root() -> Path | None:
    path = Path(get_settings().transfer.anime_shows_dir)
    return path if path.is_dir() else None


def free_space_gib() -> float | None:
    root = _existing_storage_root()
    if root is None:
        return None
    try:
        return shutil.disk_usage(root).free / _GIB
    except OSError:
        return None


def download_free_space_gib(state: dict[str, Any] | None = None) -> float | None:
    value = (state or _load_state()).get("download_free_space_gib")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _hold(state: dict[str, Any], entry: anime_mod.NyaaEntry, reason: str) -> None:
    state["releases"][entry.info_hash] = {
        "status": "held",
        "reason": reason,
        "title": entry.title,
        "retry_after": time.time() + 86400,
        "entry": _entry_dict(entry),
    }
    held = [item for item in state["held"] if item.get("info_hash") != entry.info_hash]
    held.insert(
        0,
        {
            "info_hash": entry.info_hash,
            "title": entry.title,
            "detail_url": entry.detail_url,
            "quality": entry.quality,
            "reason": reason,
            "updated_at": time.time(),
        },
    )
    state["held"] = held[:500]


def _published_timestamp(entry: anime_mod.NyaaEntry) -> float:
    if not entry.published_at:
        return 0.0
    try:
        return parsedate_to_datetime(entry.published_at).timestamp()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(entry.published_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0


# "Show Name - 05", tolerating what Erai puts after the number: "v2", "END",
# "(Repack)", "(AAC 2.0)", "(CA)", then the bracketed tags. deconstruct_release
# stops at any of those, which is how one season came to be two review cards.
_ERAI_EPISODE = re.compile(
    r"^(?P<name>.+?)\s+-\s+(?P<episode>\d{1,4})(?:v\d+)?(?:\s+END)?"
    r"(?:\s*\([^)]*\))*\s*(?:\[[^\]]*\]\s*)*$",
    re.IGNORECASE,
)


# A batch, a whole season in one torrent: "Date A Live V - 01 ~ 12 [1080p]".
# Taken beside the single releases it would download the season twice, so the
# automation holds it -- but an older season often exists only as a batch,
# so review offers to take it ("Download batch" on its card).
BATCH_REASON = "Whole-season batch: not taken automatically; use Download batch on its card"
# A season pack named by its season alone: "Fairy Tail - 100 Years Quest - S01".
_ERAI_SEASON_PACK = re.compile(
    r"^(?P<name>.+?)\s+-\s+S(?P<season>\d{1,2})(?:\s*\([^)]*\))*\s*(?:\[[^\]]*\]\s*)*$",
    re.IGNORECASE,
)
# The last episode of a season pack is not in its name: every episode it has.
SEASON_PACK_LAST = 999
_ERAI_BATCH = re.compile(
    r"^(?P<name>.+?)\s+-\s+(?P<episode>\d{1,4})\s*~\s*(?P<last>\d{1,4})(?:v\d+)?(?:\s+END)?"
    r"(?:\s*\([^)]*\))*\s*(?:\[[^\]]*\]\s*)*$",
    re.IGNORECASE,
)


def _erai_value(title: str) -> str:
    value = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", title).strip()
    return re.sub(r"\.(?:mkv|mp4|avi|m4v|mov|ts|webm)$", "", value, flags=re.IGNORECASE)


def _erai_name_episode(title: str) -> tuple[str, int] | None:
    """The show name and episode number of a single-episode Erai release."""
    match = _ERAI_EPISODE.match(_erai_value(title))
    if not match:
        return None
    return match["name"].strip(), int(match["episode"])


def _erai_identity_name(title: str) -> tuple[str, int] | None:
    """The show name of any Erai release, a batch included, and its first episode.

    For telling which AniList entry a release is, not which episode: a batch
    read as its first episode would be skipped whenever episode 1 is on disk.
    Unread, a season released whole never found its entry -- five seasons of
    Date A Live showed in review as five identical cards under one name and
    poster, and could not be blacklisted per entry.
    """
    if found := _erai_name_episode(title):
        return found
    value = _erai_value(title)
    if match := _ERAI_BATCH.match(value):
        return match["name"].strip(), int(match["episode"])
    if match := _ERAI_SEASON_PACK.match(value):
        return match["name"].strip(), 1
    return None


async def _resolve_entry(
    entry: anime_mod.NyaaEntry,
) -> tuple[anilist.Anime | None, int | None, str | None]:
    """The AniList entry and episode a release is, or why it cannot be told.

    The release's own episode number is the entry's: Erai numbers per season,
    as AniList splits them, and a number past the entry's end -- a second
    cour numbered straight on -- is settled into its sequel. A choice made in
    review for this name wins over the title lookup.
    """
    parsed = _erai_name_episode(entry.title)
    if parsed is None:
        value = _erai_value(entry.title)
        if _ERAI_BATCH.match(value) or _ERAI_SEASON_PACK.match(value):
            return None, None, BATCH_REASON
        return None, None, "AniList episode number was not found in the release title"
    name, episode = parsed
    saved = _load_mappings().get(_mapping_key(entry.title)) or {}
    if str(saved.get("anilist_id") or "").isdigit():
        chosen = await anilist.anime(int(saved["anilist_id"]))
        if chosen is None:
            return None, None, "The AniList entry chosen for this show is not known to AniList"
        return chosen, episode + int(saved.get("anilist_episode_offset") or 0), None
    resolution = await anilist.resolve(name)
    if resolution.anime is None:
        return None, None, resolution.error or "No AniList entry matches this title"
    settled, number = await anilist.settle(resolution.anime, episode)
    return settled, number, None


def _library_episodes() -> dict[int, dict[int, list[Path]]]:
    """Every episode file bankai has, by AniList entry and episode.

    Read from the file index, which says of each file which episode it is --
    written when a download is published, when the layout moves a file, when
    a folder is matched by hand -- and kept only where a copy is on disk, in
    the library or in staging. No folder names are read: a show under any
    name is found, which is what once had 56 episodes of "Demon Slayer"
    downloaded again beside "Kimetsu no Yaiba". Built once per cycle.
    """
    cache = _DISK_INDEX.get()
    if cache is not None and "episodes" in cache:
        return cache["episodes"]
    found: dict[int, dict[int, list[Path]]] = {}
    for rel, pairs in anime_files.load().items():
        paths = anime_files.existing(rel)
        if not paths:
            continue
        # A double episode's file is both.
        for aid, number in pairs:
            found.setdefault(int(aid), {}).setdefault(int(number), []).extend(paths)
    if cache is not None:
        cache["episodes"] = found
    return found


def _episode_on_disk(anilist_id: int, episode: int) -> bool:
    """Is this episode already in the library, or staged for it?"""
    return int(episode) in _library_episodes().get(int(anilist_id), {})


def _entry_args(
    entry: anime_mod.NyaaEntry, anime: anilist.Anime, episode: int
) -> list[str]:
    args = [
        "anime-download",
        "--release-title",
        entry.title,
        "--torrent-url",
        entry.download_url,
        "--detail-url",
        entry.detail_url,
        "--magnet-uri",
        entry.magnet_uri,
        "--info-hash",
        entry.info_hash,
        "--kind",
        "show",
        "--anilist-id",
        str(anime.id),
        "--anilist-title",
        anime.name,
        "--english-title",
        anime.name,
        "--episode",
        str(episode),
        "--require-german-subtitles",
        "--cleanup-torrent",
    ]
    return args


def _policy_anilist_ids(policies: dict[str, Any] | None = None) -> set[int]:
    rows = policies if policies is not None else _load_policies()
    return {
        int(row["anilist_id"])
        for row in rows.values()
        if row.get("mode") == "blacklisted" and str(row.get("anilist_id") or "").isdigit()
    }


def mark_blacklisted_shows(shows: list[dict[str, Any]]) -> None:
    """Flag library cards the user blacklisted; their files stay until deleted.

    Blacklisting stops new downloads and, from Review, deletes what was
    downloaded -- but a show already in the library before it was blocked is
    still on disk, and the grid should say so rather than look like a mistake.
    A card goes by its primary AniList entry, so blocking one sequel does not
    flag the show it is filed under. A card without one, files no AniList
    entry answers for, is flagged by name -- by an entry's exact title only
    when it holds a single season: blocking "Oshi no Ko" season one must not
    hide the other seasons in the same folder.
    """
    policies = _load_policies()
    ids = _policy_anilist_ids(policies)
    names, exact = _policy_show_keys(policies)
    for show in shows:
        titles = [
            str(show.get(field) or "") for field in ("key", "title", "source_title")
        ]
        own_id = show.get("anilist_id")
        show["blacklisted"] = bool(
            (own_id is not None and own_id in ids)
            or any(title and _show_name_key(title) in names for title in titles)
            or (
                own_id is None
                and (show.get("season_count") or 0) <= 1
                and any(title and anilist.normalise(title) in exact for title in titles)
            )
        )


async def _admit_entry(
    state: dict[str, Any], entry: anime_mod.NyaaEntry
) -> tuple[str, str, Any, dict[str, Any]] | None:
    """Identify a release on AniList; None once it has been held, skipped or filed.

    Returns the canonical key, the display title, an argument builder (the
    magnet can still change after the detail page is read) and the fields the
    release record carries. Nothing joins a collection here: the downloader
    files the episode, and its entry's collection, when it publishes.
    """
    anime, episode, error = await _resolve_entry(entry)
    if error or anime is None or episode is None:
        _hold(state, entry, error or "AniList resolution failed")
        return None
    if anime.id in _policy_anilist_ids():
        state["releases"][entry.info_hash] = {
            "status": "blacklisted",
            "title": entry.title,
            "reason": "Series blacklisted by user",
        }
        return None
    state.setdefault("anime", {})[str(anime.id)] = anime.as_dict()
    canonical = f"anilist:{anime.id}|{episode}"
    resolution = _resolution(entry)
    previous = state["canonical"].get(canonical)
    if previous and int(previous.get("resolution", 0)) >= resolution:
        state["releases"][entry.info_hash] = {
            "status": "duplicate",
            "title": entry.title,
            "canonical": canonical,
        }
        return None
    if _episode_on_disk(anime.id, episode):
        state["releases"][entry.info_hash] = {
            "status": "existing",
            "title": entry.title,
            "canonical": canonical,
        }
        return None
    display = f"{anime.name} - {episode:02d}"
    extras = {"anilist_id": anime.id, "episode": episode}
    return canonical, display, (lambda final: _entry_args(final, anime, episode)), extras


async def _german_alternative(
    state: dict[str, Any],
    entry: anime_mod.NyaaEntry,
    client: httpx.AsyncClient,
    *, limit: int = 3,
) -> anime_mod.NyaaEntry | None:
    """Another release of the same episode that does carry German subtitles.

    Erai-raws publishes an episode more than once -- typically an AVC encode
    and an HEVC one -- and they do not always advertise the same subtitles.
    Holding the first one we happened to look at put episodes in review while
    a perfectly good release of the same episode sat in the catalogue
    untouched, leaving a season with a hole in it.
    """
    key = _release_key(entry)
    if key is None:
        return None
    seen = state.get("releases", {})
    candidates = []
    for info_hash, row in _catalog_entries(state).items():
        if info_hash == entry.info_hash or info_hash in seen:
            continue
        try:
            other = _entry_from_dict(row)
        except Exception:
            continue
        if _release_key(other) != key or not _is_hevc(other):
            continue
        candidates.append(other)
    candidates.sort(key=_rank, reverse=True)
    for other in candidates[:limit]:
        says = erai_site.german(other.info_hash)
        if title_lists_german_subtitles(other.title) or says is True:
            return other
        if says is False:
            continue
        try:
            description, magnet, uploader = await anime_mod._detail_url(client, other.detail_url)
        except Exception as exc:
            log.warning("Alternative lookup failed for %s: %s", other.id, exc)
            continue
        if (uploader or "").casefold() != "erai-raws":
            continue
        if has_explicit_german_subtitles(description):
            return replace(other, magnet_uri=magnet or other.magnet_uri, description=description)
    return None


async def _erai_german(entry: anime_mod.NyaaEntry) -> bool | None:
    """What Erai-raws lists for this torrent: German or not, None if it has not said.

    A release the site-wide feed has not reached yet is looked up in its
    show's own feed, which holds the show's whole history.
    """
    says = erai_site.german(entry.info_hash)
    if says is None and erai_site.configured():
        parsed = _erai_identity_name(entry.title)
        if parsed is not None:
            with suppress(Exception):
                await erai_site.fill_show(parsed[0])
            says = erai_site.german(entry.info_hash)
    return says


async def _german_sibling(
    entry: anime_mod.NyaaEntry, client: httpx.AsyncClient
) -> anime_mod.NyaaEntry | None:
    """The Nyaa release of an encode of this episode that Erai-raws lists with German.

    Found by its own name on Nyaa and matched by info hash, so it is exactly
    the torrent Erai-raws described.
    """
    found = erai_site.german_sibling(entry.info_hash)
    if found is None:
        return None
    info_hash, row = found
    return await _nyaa_release(str(row.get("name") or ""), info_hash, client)


async def _nyaa_release(
    name: str, info_hash: str, client: httpx.AsyncClient
) -> anime_mod.NyaaEntry | None:
    """The Nyaa torrent Erai-raws lists under this name, matched by its info hash.

    Nyaa's search wants every word, and its title is not always Erai-raws'
    file name: "EAC3" on the site was "AAC" on Nyaa, and the checksum is not
    in it at all. So the full name first, then the show and its episode
    numbers only -- few enough results that the hash picks the right one.
    """
    name = re.sub(r"\.(?:mkv|mp4)$", "", name, flags=re.IGNORECASE)
    queries = [name]
    identity, span = _erai_identity_name(name), _release_range(name)
    if identity and span:
        numbers = f"{span[0]:02d}" if span[0] == span[1] else f"{span[0]:02d} {span[1]:02d}"
        queries.append(f"{identity[0]} {numbers}")

    async def ask(query: str) -> anime_mod.NyaaEntry | None:
        try:
            response = await client.get(
                f"{_NYAA_BASE}/", params={"u": "Erai-raws", "c": "1_2", "q": query}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Nyaa lookup of %s failed: %s", query, exc)
            return None
        return next(
            (
                item
                for item in parse_listing(response.text)
                if item.info_hash.casefold() == info_hash.casefold()
            ),
            None,
        )

    # Both at once: Nyaa takes seconds per search, and either may be the one.
    pending = {asyncio.ensure_future(ask(query)) for query in queries}
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.result() is not None:
                    return task.result()
    finally:
        for task in pending:
            task.cancel()
    return None


async def _recheck_german_holds(state: dict[str, Any], *, shows: int = 40) -> int:
    """Held because Nyaa did not say German: ask Erai-raws, which lists it per release.

    One Erai-raws says has German is retried like a recheck; one it says has
    none says so, which is a reason, not a guess. Up to ``shows`` shows not
    yet in the index are read from their own feeds each cycle.
    """
    if not erai_site.configured():
        return 0
    fallback = get_settings().anime.german_avc_fallback
    held = [
        (info_hash, release)
        for info_hash, release in state["releases"].items()
        if release.get("status") == "held"
        and "German subtitles" in str(release.get("reason") or "")
        and (
            "Erai-raws" not in str(release.get("reason") or "")
            # Settled as "no German" -- but another encode of it may have some.
            or (
                fallback
                and release.get("reason") == "Erai-raws lists no German subtitles"
                and erai_site.german_sibling(info_hash) is not None
            )
        )
    ]
    unknown: list[str] = []
    for info_hash, release in held:
        if erai_site.german(info_hash) is None:
            parsed = _erai_identity_name(str(release.get("title") or ""))
            if parsed is not None and parsed[0] not in unknown:
                unknown.append(parsed[0])
    for name in unknown[:shows]:
        with suppress(Exception):
            await erai_site.fill_show(name)
        await asyncio.sleep(get_settings().anime.backfill_request_delay_seconds)
    catalog = _catalog_entries(state)
    retries: dict[str, dict] = {}
    changed = 0
    for info_hash, release in held:
        says = erai_site.german(info_hash)
        if says is None:
            continue
        changed += 1
        sibling = fallback and not says and erai_site.german_sibling(info_hash) is not None
        if says or sibling:
            release["reason"] = (
                "Erai-raws lists German for another encode of it; waiting to be checked again"
                if sibling
                else "Erai-raws lists German subtitles; waiting to be checked again"
            )
            release["retry_after"] = 0
            retries[info_hash] = {
                "title": release.get("title"),
                "entry": release.get("entry") or catalog.get(info_hash),
            }
        else:
            release["reason"] = "Erai-raws lists no German subtitles"
    if retries:
        with _STATE_LOCK:
            requests = _load_retry_requests()
            requests.update(retries)
            _save_retry_requests(requests)
    if changed:
        log.info("Erai-raws settled %d German-subtitle holds (%d to retry)", changed, len(retries))
    return changed


async def _consider(
    state: dict[str, Any],
    entry: anime_mod.NyaaEntry,
    client: httpx.AsyncClient,
    *,
    allow_avc: bool = False,
) -> bool:
    """``allow_avc``: the German fallback, for an episode whose HEVC encode has none."""
    if not _needs_consideration(state, entry):
        return False
    if not _is_hevc(entry) and not allow_avc:
        state["releases"][entry.info_hash] = {
            "status": "filtered",
            "title": entry.title,
            "reason": "Release is not HEVC/H.265",
        }
        state["held"] = [
            item for item in state["held"] if item.get("info_hash") != entry.info_hash
        ]
        return False
    if (entry.publisher or "").casefold() != "erai-raws":
        _hold(state, entry, "Uploader is not exactly Erai-raws")
        return False
    if not entry.trusted or entry.remake:
        _hold(state, entry, "Release is not a trusted, original Nyaa upload")
        return False
    # Identity first, then ownership, and only then subtitles. The subtitle
    # check used to run before either, so an episode already sitting in the
    # library was held for review over subtitles it did not need -- which is
    # what put a finished Bleach arc in the review queue. Resolving first also
    # means an episode we already have costs no Nyaa request at all.
    admitted = await _admit_entry(state, entry)
    if admitted is None:
        return False
    canonical, title, build_args, extras = admitted
    resolution = _resolution(entry)

    try:
        description, magnet, uploader = await anime_mod._detail_url(client, entry.detail_url)
    except Exception as exc:
        log.warning("Erai detail lookup failed for %s: %s", entry.id, exc)
        return False
    if (uploader or "").casefold() != "erai-raws":
        _hold(state, entry, "Detail-page uploader is not Erai-raws")
        return False
    policy = _series_policy(entry.title)
    erai_says = await _erai_german(entry)
    if title_lists_german_subtitles(entry.title):
        has_german = True
    elif erai_says is not None:
        # Erai-raws' own subtitle list decides; Nyaa's description only
        # when the site has not said.
        has_german = erai_says
    else:
        has_german = has_explicit_german_subtitles(description)
    if not has_german and not (policy and policy.get("mode") == "german_allowed"):
        alternative = await _german_alternative(state, entry, client)
        if alternative is not None:
            log.info(
                "Using %s instead of %s, which does not list German subtitles",
                alternative.title,
                entry.title,
            )
            # The alternative was only accepted after its own German evidence
            # was verified, so this cannot bounce back into this branch.
            return await _consider(state, alternative, client)
        listed = (
            erai_says is False
            and get_settings().anime.german_avc_fallback
            and erai_site.german_sibling(entry.info_hash) is not None
        )
        sibling = await _german_sibling(entry, client) if listed else None
        if listed and sibling is None:
            _hold(
                state,
                entry,
                "Erai-raws lists German only for another encode of it, not found on Nyaa",
            )
            return False
        if sibling is not None:
            log.info(
                "Using %s: Erai-raws lists German for it, not for %s", sibling.title, entry.title
            )
            # Erai-raws lists German for it, so this cannot come back here either.
            taken = await _consider(state, sibling, client, allow_avc=True)
            if taken:
                # The episode is on its way; this encode is no decision to make.
                state["releases"][entry.info_hash] = {
                    "status": "filtered",
                    "title": entry.title,
                    "reason": "Its encode with German subtitles was downloaded instead",
                }
                state["held"] = [
                    item for item in state["held"] if item.get("info_hash") != entry.info_hash
                ]
                return True
            # Not taken yet -- no download space, most often. Saying "no German"
            # here, as it did, sent the user to the site to find it had some.
            other = state["releases"].get(sibling.info_hash) or {}
            _hold(
                state,
                entry,
                f"Its encode with German subtitles is held: {other['reason']}"
                if other.get("status") == "held" and other.get("reason")
                else "Erai-raws lists German for another encode of it; waiting for download space",
            )
            return False
        _hold(
            state,
            entry,
            "Erai-raws lists no German subtitles"
            if erai_says is False
            else "Nyaa description does not explicitly list German subtitles",
        )
        return False
    if magnet:
        entry = replace(entry, magnet_uri=magnet, description=description)
    admission = _ADMISSION.get()
    # Write the release down *before* handing the torrent to qBittorrent. The
    # old order added the torrent first and only then tried to create a job, so
    # anything that stopped the job being created -- a duplicate title, an
    # exception in between -- left a torrent downloading that nothing owned and
    # nothing would ever publish. The record is what the reconciler drives from,
    # so it has to exist first.
    state["releases"][entry.info_hash] = {
        "status": "queued",
        "title": entry.title,
        "display_title": title,
        "canonical": canonical,
        "resolution": resolution,
        "size_bytes": entry.size_bytes,
        # Kept so publishing never has to resolve the release a second time.
        "args": build_args(entry),
        "updated_at": time.time(),
        **extras,
    }
    state["held"] = [item for item in state["held"] if item.get("info_hash") != entry.info_hash]
    state["canonical"][canonical] = {
        "info_hash": entry.info_hash,
        "resolution": resolution,
    }
    if admission is not None:
        # Reserve the eventual library size of all submitted torrents, so a
        # large qBittorrent backlog cannot consume the 100 GiB floor later.
        required = max(entry.size_bytes, _GIB // 2)
        if required > admission["remaining"]:
            state["releases"].pop(entry.info_hash, None)
            state["canonical"].pop(canonical, None)
            return False
        try:
            await admission["qbit"].add(
                magnet=entry.magnet_uri or None,
                torrent_url=None if entry.magnet_uri else entry.download_url,
                category=get_settings().qbittorrent.category,
                save_path=Path(get_settings().qbittorrent.save_path)
                if get_settings().qbittorrent.save_path
                else None,
            )
        except Exception as exc:
            # The reconciler re-adds a queued release whose torrent is absent,
            # so a failed add is a retry rather than a lost episode.
            log.warning("Could not add %s to qBittorrent: %s", entry.info_hash[:8], exc)
            return False
        admission["remaining"] -= required
    return True


def _episode_key(title: str) -> tuple[str, Any, Any] | None:
    """Series and episode identity, for matching two encodes of one episode."""
    season, episode = anime_mod.release_episode_info(title)
    if episode is None:
        return None
    return (_mapping_key(title), season, episode)


def _hevc_twin(state: dict[str, Any], release: dict[str, Any]) -> anime_mod.NyaaEntry | None:
    """The best catalogued HEVC release of the same episode."""
    key = _episode_key(str(release.get("title") or ""))
    if key is None:
        return None
    seen = state.get("releases", {})
    candidates = []
    for info_hash, row in _catalog_entries(state).items():
        if info_hash in seen:
            continue
        title = str(row.get("title") or "")
        if not _is_hevc_title(title) or _episode_key(title) != key:
            continue
        try:
            candidates.append(_entry_from_dict(row))
        except Exception:
            continue
    if not candidates:
        return None
    return max(candidates, key=_rank)


async def _upgrade_to_hevc(
    state: dict[str, Any],
    client: httpx.AsyncClient,
    *,
    limit: int,
) -> int:
    """Replace queued AVC releases with the HEVC encode of the same episode.

    The codec policy only gates what discovery admits, so everything queued
    before it landed is still AVC -- on the live box that was 918 episodes and
    1.2 TiB of downloads, every one of which had an HEVC encode already sitting
    in the catalogue. HEVC is roughly half the size for the same episode, and
    the disk it lands on is the scarcest thing in the system.

    Bounded per cycle: each upgrade may cost a Nyaa detail fetch, and the
    backlog is worth converting steadily rather than in one burst.
    """
    if limit <= 0:
        return 0
    admission = _ADMISSION.get()
    qbit = admission["qbit"] if admission else None
    upgraded = 0
    stale = [
        (info_hash, release)
        for info_hash, release in list(state.get("releases", {}).items())
        # Only work not yet downloaded: swapping something already on disk
        # would throw away bytes that are paid for.
        if release.get("status") == "queued"
        and not _is_hevc_title(str(release.get("title") or ""))
    ]
    for info_hash, release in stale:
        if upgraded >= limit:
            break
        twin = _hevc_twin(state, release)
        if twin is None:
            continue
        if not await _carries_german(twin, client):
            continue
        # The AVC release holds this episode's canonical slot, and _consider
        # rejects anything that does not beat the slot's resolution. Release it
        # before the replacement is offered, or the upgrade reads as a
        # duplicate of the thing it is replacing.
        canonical = str(release.get("canonical") or "")
        if canonical and state.get("canonical", {}).get(canonical, {}).get(
            "info_hash"
        ) in (info_hash, None):
            state["canonical"].pop(canonical, None)
        state["releases"].pop(info_hash, None)
        if not await _consider(state, twin, client):
            # Put the original back rather than losing the episode entirely.
            state["releases"][info_hash] = release
            if canonical:
                state["canonical"][canonical] = {
                    "info_hash": info_hash,
                    "resolution": release.get("resolution", 0),
                }
            continue
        if qbit is not None:
            try:
                await qbit.remove(info_hash, delete_files=True)
            except Exception as exc:
                log.warning("Could not drop superseded torrent %s: %s", info_hash[:8], exc)
        log.info("Upgraded to HEVC: %s", twin.title)
        upgraded += 1
        await asyncio.sleep(get_settings().anime.backfill_request_delay_seconds)
    return upgraded


async def _carries_german(entry: anime_mod.NyaaEntry, client: httpx.AsyncClient) -> bool:
    """Confirm a replacement really does offer German before swapping to it."""
    if title_lists_german_subtitles(entry.title):
        return True
    says = await _erai_german(entry)
    if says is not None:
        return says
    try:
        description, _magnet, uploader = await anime_mod._detail_url(client, entry.detail_url)
    except Exception as exc:
        log.warning("Could not read %s while upgrading: %s", entry.id, exc)
        return False
    if (uploader or "").casefold() != "erai-raws":
        return False
    return has_explicit_german_subtitles(description)


def _upgrade_targets(entries: dict[int, Any]) -> dict[str, Any]:
    """Which episodes on disk an HEVC upgrade is for, read from the files themselves.

    Not from bankai's own download records: a show downloaded before the
    automation, or by hand, has none, and its AVC episodes were reported as
    already HEVC. An episode counts as AVC when a file of it was probed as
    AVC; one carrying a German dub is kept as it is.
    """
    from bankai.web.anime_library import _load_codec_cache

    probes = _load_codec_cache()
    targets: list[tuple[int, int, list[dict[str, Any]]]] = []
    dubs: dict[int, set[int]] = {}
    already = unprobed = 0
    for aid in entries:
        for episode, paths in sorted(_entry_episode_files(aid).items()):
            codecs = {(probes.get(str(path)) or {}).get("codec") for path in paths}
            if "avc" not in codecs:
                if None in codecs:
                    unprobed += 1
                else:
                    already += 1
                continue
            if any(_is_german_dub(path) for path in paths):
                dubs.setdefault(aid, set()).add(episode)
                continue
            rows = []
            for path in paths:
                with suppress(OSError):
                    stat = path.stat()
                    rows.append({"path": str(path), "episode": episode, "size": stat.st_size, "mtime": stat.st_mtime})
            targets.append((aid, episode, rows))
    return {
        "targets": targets,
        "dubs": dubs,
        "already": already,
        "dubbed": sum(len(episodes) for episodes in dubs.values()),
        "unprobed": unprobed,
    }


async def _hevc_releases(
    entries: dict[int, Any], wanted: set[tuple[int, int]], headers: dict[str, str]
) -> dict[tuple[int, int], tuple[anime_mod.NyaaEntry, int, int]]:
    """The HEVC Erai release to take for each wanted (entry, episode), with its range.

    From the catalogue already crawled, then Nyaa searched under each entry's
    names, every result identified as the automation would. A batch that
    covers more of the wanted episodes wins: one download for a season.
    """
    state = await asyncio.to_thread(_load_state)
    mappings = await asyncio.to_thread(_load_mappings)
    table = anilist.cached_index()
    pool: dict[str, anime_mod.NyaaEntry] = {}
    for row in _catalog_entries(state).values():
        with suppress(Exception):
            item = _entry_from_dict(row)
            pool[item.info_hash] = item
    names: set[str] = set()
    for aid, anime in entries.items():
        names.update(name for name in (anime.title, anime.english) if name)
        prefix = f"anilist:{aid}|"
        for release in (state.get("releases") or {}).values():
            if str(release.get("canonical") or "").startswith(prefix):
                parsed = _erai_identity_name(str(release.get("title") or ""))
                if parsed:
                    names.add(parsed[0])
    # Relative to Nyaa: _fetch_rss asks for "/".
    async with httpx.AsyncClient(base_url=_NYAA_BASE, headers=headers, timeout=30, follow_redirects=True) as nyaa:
        for name in sorted(names):
            for page in range(3):
                try:
                    rows = await anime_mod._fetch_rss(nyaa, f'"{name}" HEVC', "1_2", page)
                except Exception as exc:
                    log.warning("Nyaa search for %s failed: %s", name, exc)
                    break
                for item in rows:
                    pool.setdefault(item.info_hash, item)
                if len(rows) < 75:
                    break
                await asyncio.sleep(get_settings().anime.backfill_request_delay_seconds)
    best: dict[tuple[int, int], tuple[anime_mod.NyaaEntry, int, int, int]] = {}
    for item in pool.values():
        if not _is_hevc(item) or (item.publisher or "Erai-raws").casefold() != "erai-raws":
            continue
        span = _release_range(item.title)
        match = anime_mod.anilist_match(item.title, table=table, mappings=mappings)
        if not span or not match:
            continue
        aid = int(match["anilist_id"])
        first, last = span
        if first == last and match.get("episode") is not None:
            first = last = int(match["episode"])  # settled into the right part of a split season
        covered = [episode for episode in range(first, min(last, SEASON_PACK_LAST - 1) + 1) if (aid, episode) in wanted]
        for episode in covered:
            key = (aid, episode)
            score = (len(covered), *_rank(item))
            if key not in best or score > best[key][3]:
                best[key] = (item, first, last, score)
    return {key: (item, first, last) for key, (item, first, last, _score) in best.items()}


async def upgrade_show_to_hevc(anilist_ids: list[int]) -> dict[str, Any]:
    """Queue HEVC replacements for the AVC episodes on disk of a card's AniList entries.

    Unlike the backlog upgrade, these episodes are on disk and watchable. The
    replacement is downloaded first; once it is published the AVC files it
    takes the place of are removed -- wherever the file index has them -- so
    a failed upgrade costs bandwidth and nothing else. A season batch
    covering several episodes is one download. An episode with no HEVC
    release carrying German subtitles is left as it is.
    """
    settings = get_settings()
    entries: dict[int, anilist.Anime] = {}
    for aid in dict.fromkeys(int(aid) for aid in anilist_ids):
        entry = await anilist.anime(aid)
        if entry is not None:
            entries[aid] = entry
    if not entries:
        raise ValueError("None of these AniList entries is known")
    found = await asyncio.to_thread(_upgrade_targets, entries)
    targets = found["targets"]
    result = {
        "ok": True,
        "queued": 0,
        "downloads": 0,
        "no_replacement": 0,
        "already_hevc": found["already"],
        "german_dub_kept": found["dubbed"],
        "not_probed": found["unprobed"],
    }
    if not targets:
        return result
    from bankai.torrent.qbittorrent import QBittorrentClient

    headers = {"User-Agent": settings.scraper.user_agent}
    releases = await _hevc_releases(entries, {(aid, ep) for aid, ep, _rows in targets}, headers)
    # One download per release: a batch takes every episode it was picked for.
    plans: dict[str, dict[str, Any]] = {}
    for aid, episode, rows in targets:
        picked = releases.get((aid, episode))
        if picked is None:
            result["no_replacement"] += 1
            continue
        item, first, last = picked
        plan = plans.setdefault(
            item.info_hash, {"item": item, "aid": aid, "first": first, "last": last, "episodes": [], "rows": []}
        )
        plan["episodes"].append(episode)
        plan["rows"].extend(rows)
    qbit = QBittorrentClient()
    try:
        await qbit.login()
    except Exception as exc:
        await qbit.aclose()
        raise RuntimeError(f"qBittorrent is unavailable: {exc}") from exc
    try:
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            for plan in plans.values():
                twin, aid, episodes = plan["item"], plan["aid"], sorted(plan["episodes"])
                if not await _carries_german(twin, client):
                    result["no_replacement"] += len(episodes)
                    continue
                anime = entries[aid]
                # Publishing has to overwrite an episode filed where the new one
                # goes; a German dub in a batch's range is kept as it is.
                args = [*_entry_args(twin, anime, episodes[0]), "--replace-existing"]
                for episode in sorted(found["dubs"].get(aid, ())):
                    if plan["first"] <= episode <= plan["last"]:
                        args.extend(["--keep-episode", str(episode)])
                try:
                    await qbit.add(
                        magnet=twin.magnet_uri or None,
                        torrent_url=None if twin.magnet_uri else twin.download_url,
                        category=settings.qbittorrent.category,
                        save_path=Path(settings.qbittorrent.save_path)
                        if settings.qbittorrent.save_path
                        else None,
                    )
                except Exception as exc:
                    log.warning("Could not queue HEVC upgrade for %s: %s", twin.title, exc)
                    result["no_replacement"] += len(episodes)
                    continue
                span = f"{episodes[0]:02d}" + (f"-{episodes[-1]:02d}" if len(episodes) > 1 else "")
                with _STATE_LOCK:
                    state = _load_state()
                    state["releases"][twin.info_hash] = {
                        "status": "queued",
                        "title": twin.title,
                        "display_title": f"{anime.name} - {span}",
                        "canonical": f"anilist:{aid}|{episodes[0]}",
                        "resolution": _resolution(twin),
                        "size_bytes": twin.size_bytes,
                        "args": args,
                        "anilist_id": aid,
                        "episode": episodes[0],
                        # The AVC files go once the upgrade is published.
                        "replace_files": plan["rows"],
                        "updated_at": time.time(),
                    }
                    # The upgrade takes over the episodes' slots; the originals
                    # stay on disk and playable until the replacement lands.
                    for episode in episodes:
                        state["canonical"][f"anilist:{aid}|{episode}"] = {
                            "info_hash": twin.info_hash,
                            "resolution": _resolution(twin),
                        }
                    _save_state(state)
                result["queued"] += len(episodes)
                result["downloads"] += 1
                await asyncio.sleep(settings.anime.backfill_request_delay_seconds)
    finally:
        await qbit.aclose()
    return result


# --- Replacing a card's releases with one picked on Erai-raws --------------
#
# The automation takes each episode as its own release, and holds one whose
# Nyaa page does not list German. Erai-raws often has the same season as a
# batch that does, or another encode of one episode: picked from the Erai-raws
# dialog in review, it replaces the held releases it covers and the files of
# those episodes already in the library -- except a German dub, never
# replaced. The old files go only once the new ones are published.

REPLACED_STATUS = "replaced"


def _release_range(title: str) -> tuple[int, int] | None:
    """The episodes a release name covers: one, or a batch's first to last."""
    single = _erai_name_episode(title)
    if single:
        return single[1], single[1]
    value = _erai_value(title)
    if match := _ERAI_BATCH.match(value):
        first, last = int(match["episode"]), int(match["last"])
        return (first, last) if first <= last else None
    if _ERAI_SEASON_PACK.match(value):
        return 1, SEASON_PACK_LAST
    return None


def _entry_episode_files(aid: int) -> dict[int, list[Path]]:
    """The files of one AniList entry, by its own episode numbers: library and staging.

    Read fresh from the file index rather than the cycle's view of it: what
    is asked here is often a file that was published a moment ago.
    """
    return anime_files.on_disk(int(aid))


def _is_german_dub(path: Path) -> bool:
    from bankai.web.anime_library import _load_codec_cache, has_german_audio, probe_streams

    cached = _load_codec_cache().get(str(path))
    if cached is None:
        cached = probe_streams(path) or {}
    return has_german_audio(cached)


def _replacement_plan(key: str, info_hash: str) -> dict[str, Any]:
    """What taking this release for a review card would do.

    The release is one Erai-raws lists, or one held for the card itself -- a
    batch the automation did not take on its own.
    """
    state = _load_state()
    releases = state.get("releases") or {}
    own = releases.get(info_hash.casefold()) or {}
    row = erai_site.lookup(info_hash)
    show_title = ""
    if row is not None:
        name = str(row.get("name") or row.get("title") or "")
        german = "de" in (row.get("subs") or [])
        # Erai's own show title; the file name can be a short one ("Kaguya 3").
        show_title = str(row.get("title") or "")
    elif own.get("status") == "held" and own.get("title"):
        name = str(own["title"])
        german = title_lists_german_subtitles(name) or bool(erai_site.german(info_hash))
    else:
        raise ValueError("Erai-raws does not list this release")
    span = _release_range(name)
    if span is None:
        raise ValueError("The episode numbers could not be read from the release name")
    table = anilist.cached_index()
    head, _, tail = key.partition(":")
    card_aid = int(tail) if head == "anilist" and tail.isdigit() else None
    release_aid = (
        _release_anilist_id(show_title, table=table) if show_title else None
    ) or _release_anilist_id(name, table=table)
    if card_aid and release_aid and card_aid != release_aid:
        other = table.anime.get(release_aid) if table else None
        label = other.name if other else f"AniList {release_aid}"
        raise ValueError(f"This release is {label}, not the AniList entry of this card")
    aid = card_aid or release_aid
    anime = table.anime.get(aid) if table and aid else None
    if anime is None:
        raise ValueError("Choose the AniList entry for this card first")
    existing = own
    if existing.get("status") in {*_ACTIVE_RELEASE_STATES, "done"}:
        raise ValueError("This release is already downloading or in the library")
    first, last = span
    mappings = _load_mappings()
    held = []
    for held_hash, release in releases.items():
        title = str(release.get("title") or "")
        if release.get("status") != "held" or held_hash == info_hash.casefold():
            continue
        covered = _release_range(title)
        if covered is None or covered[0] < first or covered[1] > last:
            continue
        if key in {_card_key(title, mappings=mappings, table=table), _mapping_key(title)}:
            held.append(held_hash)
    files = _entry_episode_files(anime.id)
    dubs: list[int] = []
    replaced: list[dict[str, Any]] = []
    for episode in range(first, last + 1):
        paths = files.get(episode) or []
        if any(_is_german_dub(path) for path in paths):
            dubs.append(episode)
            continue
        for path in paths:
            with suppress(OSError):
                stat = path.stat()
                replaced.append(
                    {
                        "path": str(path),
                        "episode": episode,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
                )
    return {
        "info_hash": info_hash.casefold(),
        "name": name,
        "batch": first != last,
        "first": first,
        "last": last,
        "german": german,
        "anilist_id": anime.id,
        "anilist_title": anime.name,
        "held": held,
        "files": replaced,
        "german_dubs_kept": dubs,
    }


# Nyaa lookups started while the confirm dialog is read, by info hash: the
# Replace that follows finds its torrent already looked up.
_NYAA_LOOKUPS: dict[str, tuple[float, asyncio.Task]] = {}
_NYAA_LOOKUP_SECONDS = 600.0


def _find_on_nyaa(name: str, info_hash: str) -> asyncio.Task:
    """The lookup of this release on Nyaa, started now or earlier."""
    now = time.time()
    for key, (started, _) in list(_NYAA_LOOKUPS.items()):
        if now - started > _NYAA_LOOKUP_SECONDS:
            _NYAA_LOOKUPS.pop(key, None)
    held = _NYAA_LOOKUPS.get(info_hash)
    if held is not None:
        task = held[1]
        # Still running, or found it; one that found nothing or failed is asked again.
        if not task.done() or (
            not task.cancelled() and task.exception() is None and task.result() is not None
        ):
            return task

    async def look() -> anime_mod.NyaaEntry | None:
        settings = get_settings()
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.scraper.user_agent}, timeout=30, follow_redirects=True
        ) as client:
            return await _nyaa_release(name, info_hash, client)

    task = asyncio.create_task(look(), name=f"nyaa:{info_hash[:8]}")
    _NYAA_LOOKUPS[info_hash] = (now, task)
    return task


async def replacement_plan(key: str, info_hash: str) -> dict[str, Any]:
    """The plan, as the confirm dialog shows it; Nyaa is asked meanwhile."""
    await anilist.index()
    plan = await asyncio.to_thread(_replacement_plan, key, info_hash)
    _find_on_nyaa(plan["name"], plan["info_hash"])
    return {
        **{name: value for name, value in plan.items() if name not in {"held", "files"}},
        "held_count": len(plan["held"]),
        "file_count": len(plan["files"]),
    }


async def replace_from_erai(key: str, info_hash: str) -> dict[str, Any]:
    """Queue an Erai-raws release in place of what a review card holds."""
    from bankai.torrent.qbittorrent import QBittorrentClient

    await anilist.index()
    plan = await asyncio.to_thread(_replacement_plan, key, info_hash)
    settings = get_settings()
    saved = ((_load_state().get("releases") or {}).get(plan["info_hash"]) or {}).get("entry")
    if saved and saved.get("magnet_uri"):
        entry = _entry_from_dict(saved)  # a held release: the torrent it was found as
    else:
        entry = await _find_on_nyaa(plan["name"], plan["info_hash"])
    _NYAA_LOOKUPS.pop(plan["info_hash"], None)
    if entry is None:
        raise ValueError("The release was not found on Nyaa")
    anime = await anilist.anime(plan["anilist_id"])
    if anime is None:
        raise ValueError("Choose the AniList entry for this card first")
    # Chosen by hand, with what is listed for it in view: taken as it is,
    # without the automation's German check -- which, after the whole download,
    # would only refuse to publish what was knowingly picked.
    args = [
        arg for arg in _entry_args(entry, anime, plan["first"]) if arg != "--require-german-subtitles"
    ]
    args.append("--replace-existing")
    for episode in plan["german_dubs_kept"]:
        args.extend(["--keep-episode", str(episode)])
    qbit = QBittorrentClient()
    try:
        await qbit.login()
        await qbit.add(
            magnet=entry.magnet_uri or None,
            torrent_url=None if entry.magnet_uri else entry.download_url,
            category=settings.qbittorrent.category,
            save_path=Path(settings.qbittorrent.save_path) if settings.qbittorrent.save_path else None,
        )
    except Exception as exc:
        raise RuntimeError(f"qBittorrent did not take the release: {exc}") from exc
    finally:
        await qbit.aclose()

    def record() -> None:
        with _STATE_LOCK:
            state = _load_state()
            span = f"{plan['first']:02d}" + (f"-{plan['last']:02d}" if plan["batch"] else "")
            state["releases"][entry.info_hash] = {
                "status": "queued",
                "title": entry.title,
                "display_title": f"{anime.name} - {span}",
                "canonical": f"anilist:{anime.id}|{plan['first']}",
                "resolution": _resolution(entry),
                "size_bytes": entry.size_bytes,
                "args": args,
                "anilist_id": anime.id,
                "replace_files": plan["files"],
                "replaced_holds": plan["held"],
                "updated_at": time.time(),
            }
            # The episodes it brings are taken: the automation must not queue
            # their single releases beside it.
            last = plan["last"] if plan["last"] < SEASON_PACK_LAST else plan["first"] - 1
            for episode in range(plan["first"], last + 1):
                if episode not in plan["german_dubs_kept"]:
                    state["canonical"][f"anilist:{anime.id}|{episode}"] = {
                        "info_hash": entry.info_hash,
                        "resolution": _resolution(entry),
                    }
            for held_hash in plan["held"]:
                release = state["releases"].get(held_hash)
                if release and release.get("status") == "held":
                    release.update(status=REPLACED_STATUS, reason=f"Replaced by {entry.title}")
                    release.pop("retry_after", None)
            _prune_holds(state)
            _save_state(state)

    await asyncio.to_thread(record)
    return {
        "ok": True,
        "queued": entry.title,
        "held_replaced": len(plan["held"]),
        "files_to_replace": len(plan["files"]),
        "german_dubs_kept": plan["german_dubs_kept"],
    }


def _finish_replacement(release: dict[str, Any]) -> int:
    """Once the replacement is published: remove the files it took the place of.

    Only a file that is still the one recorded -- an old file at the new
    file's own path was simply overwritten, and its size and time say so --
    and whose episode now has another file beside it, by the file index.
    """
    old = release.pop("replace_files", None) or []
    release.pop("replaced_holds", None)
    if not old:
        return 0
    now = _entry_episode_files(int(release["anilist_id"]))
    recorded = {row["path"] for row in old}
    roots = _series_roots()
    removed = 0
    gone: list[Path] = []
    for row in old:
        path = Path(row["path"])
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size != row["size"] or stat.st_mtime != row["mtime"]:
            continue
        newer = [p for p in now.get(int(row["episode"])) or [] if str(p) not in recorded]
        if not newer or not _contained_by(path, roots):
            continue
        try:
            path.unlink()
            removed += 1
            gone.append(path)
            log.info("Replaced %s by %s", path.name, newer[0].name)
        except OSError as exc:
            log.warning("Could not remove replaced file %s: %s", path, exc)
    if removed:
        from bankai.web import library_walk

        # A key whose new copy is staged keeps its record; only a key left
        # with no file at all goes.
        _forget_deleted(gone)
        library_walk.mark_stale()
    return removed


def _undo_replacement(state: dict[str, Any], release: dict[str, Any]) -> None:
    """The replacement failed: its episodes go back to review as they were."""
    release.pop("replace_files", None)
    for held_hash in release.pop("replaced_holds", None) or []:
        held = state["releases"].get(held_hash)
        if held and held.get("status") == REPLACED_STATUS:
            held.update(
                status="held",
                reason=f"Its replacement failed ({release.get('reason') or 'publishing failed'})",
                retry_after=time.time() + 86400,
            )


async def _fetch_rss(client: httpx.AsyncClient) -> list[anime_mod.NyaaEntry]:
    response = await client.get(get_settings().anime.rss_url)
    response.raise_for_status()
    return anime_mod.parse_rss(response.text)


def _split_search_word(rows: list[anime_mod.NyaaEntry], node: dict[str, Any]) -> str | None:
    """Choose a reasonably balanced term for complementary query branches."""

    used = {str(word).casefold() for word in [*node["include"], *node["exclude"]]}
    ignored = {"erai", "raws", "multisub", "1080p", "2160p", "720p"}
    for cleaned in (True, False):
        counts: dict[str, int] = {}
        for entry in rows:
            title = anime_mod.clean_release_title(entry.title) if cleaned else entry.title
            words = set(re.findall(r"[a-z0-9]{2,}", title.casefold()))
            for word in words - used - ignored:
                counts[word] = counts.get(word, 0) + 1
        choices = [word for word, count in counts.items() if 0 < count < len(rows)]
        if choices:
            return min(
                choices, key=lambda word: (abs(counts[word] - len(rows) / 2), len(word), word)
            )
    return None


def _advance_backfill_phase(backfill: dict[str, Any]) -> None:
    if backfill.get("complete") or backfill.get("frontier"):
        return
    phase = backfill.get("phase", "2160")
    if phase == "1080" and backfill.get("high_only"):
        queries = backfill.get("title_queries", [])
        next_query = int(backfill.get("title_query_index", 0)) + 1
        if next_query < len(queries):
            backfill.update(
                {
                    "title_query_index": next_query,
                    "title_query": queries[next_query],
                    "phase": "2160",
                    "page": 1,
                    "frontier": [{"include": [], "exclude": [], "page": 1}],
                }
            )
            return
    if phase == "720" or (phase == "1080" and backfill.get("high_only")):
        backfill.update({"phase": "ready", "complete": True})
        return
    backfill.update(
        {
            "phase": "1080" if phase == "2160" else "720",
            "page": 1,
            "frontier": [{"include": [], "exclude": [], "page": 1}],
        }
    )


async def _crawl_backfill(state: dict[str, Any], client: httpx.AsyncClient) -> None:
    settings = get_settings().anime
    backfill = state["backfill"]
    if (not settings.backfill_enabled and not backfill.get("title_query")) or backfill.get(
        "complete"
    ):
        return
    # Broad Nyaa searches silently stop at 1,000 results. Complementary
    # positive/negative term shards cover the complete catalogue without an
    # unsupported date/ID cursor or an ever-growing global exclusion query.
    backfill.setdefault("frontier", [{"include": [], "exclude": [], "page": 1}])
    for _ in range(3):
        _advance_backfill_phase(backfill)
        if backfill.get("complete"):
            break
        phase = str(backfill.get("phase", "2160"))
        node = backfill["frontier"][0]
        terms = [f"{phase}p", "HEVC"]
        if backfill.get("title_query"):
            terms.append('"' + backfill["title_query"].replace('"', "") + '"')
        terms.extend(f'"{word}"' for word in node["include"])
        terms.extend(f'-"{word}"' for word in node["exclude"])
        query = " ".join(terms)
        if len(query) > 6000:
            raise RuntimeError(
                "Historical search shard is too broad; 720p fallback remains blocked"
            )
        page = max(1, int(node.get("page", 1)))
        params = {
            "f": "0",
            "c": "1_2",
            "q": query,
            "u": "Erai-raws",
            "p": page,
            "s": "id",
            "o": "desc",
        }
        response = await client.get(f"{_NYAA_BASE}/?{urlencode(params)}")
        response.raise_for_status()
        tree = HTMLParser(response.text)
        rows = parse_listing(response.text)
        info = tree.css_first(".pagination-page-info")
        total_match = re.search(
            r"out of\s+([\d,]+)\s+results", info.text(strip=True) if info else "", re.I
        )
        capped = total_match is not None and int(total_match.group(1).replace(",", "")) >= 1000
        if capped:
            word = _split_search_word(rows, node)
            if word is None:
                raise RuntimeError(
                    "Historical search cannot be split safely; 720p fallback remains blocked"
                )
            parent = backfill["frontier"].pop(0)
            backfill["frontier"][0:0] = [
                {
                    "include": [*parent["include"], word],
                    "exclude": list(parent["exclude"]),
                    "page": 1,
                },
                {
                    "include": list(parent["include"]),
                    "exclude": [*parent["exclude"], word],
                    "page": 1,
                },
            ]
            backfill["queries_split"] = int(backfill.get("queries_split", 0)) + 1
            await asyncio.sleep(settings.backfill_request_delay_seconds)
            continue
        catalog = backfill["catalog_1080" if phase in {"2160", "1080"} else "catalog_720"]
        high = backfill["catalog_1080"]
        for entry in rows:
            if (
                not entry.trusted
                or entry.remake
                or (entry.publisher or "").casefold() != "erai-raws"
                or not _is_hevc(entry)
            ):
                continue
            key = _release_key(entry)
            if key is None or (phase == "720" and key in high):
                continue
            current = catalog.get(key)
            if current is None or _rank(entry) > _rank(_entry_from_dict(current)):
                catalog[key] = _entry_dict(entry)
        if (
            rows
            and tree.css_first('.pagination li.next:not(.disabled) a[href], a[rel="next"]')
            is not None
        ):
            node["page"] = page + 1
            backfill["page"] = page + 1
        else:
            backfill["frontier"].pop(0)
            backfill["queries_completed"] = int(backfill.get("queries_completed", 0)) + 1
        await asyncio.sleep(settings.backfill_request_delay_seconds)
    _advance_backfill_phase(backfill)


def _episode_order(entry: anime_mod.NyaaEntry) -> tuple:
    season, episode = anime_mod.release_episode_info(entry.title)
    _, part = anime_mod.release_part(entry.title)
    return (
        _mapping_key(entry.title),
        season or part or 0,
        episode or 0,
        -_resolution(entry),
        entry.id,
    )


class _Shows:
    """Which show each release name belongs to: its entry's chain of sequels on AniList.

    A show is AniList's main line -- the entries its sequel and prequel
    relations chain together, its seasons and the movies between them --
    named by the first of them. One Nyaa catalogue is built per show, under
    the names of all its seasons, and its releases are taken in sequel
    order. Offline: the title index and the relations already kept. Asked
    for every release in the catalogue, so remembered per name and per chain.
    """

    def __init__(self, table: Any, mappings: dict[str, Any]) -> None:
        self.table = table
        self.mappings = mappings
        self._entries: dict[str, int | None] = {}
        self._chains: dict[int, list[int]] = {}

    def entry(self, title: str) -> int | None:
        """The entry a release name is -- the user's choice for it, else its title."""
        key = _mapping_key(title)
        if key not in self._entries:
            saved = self.mappings.get(key) or {}
            if str(saved.get("anilist_id") or "").isdigit():
                self._entries[key] = int(saved["anilist_id"])
            elif self.table is None:
                self._entries[key] = None
            else:
                parsed = _erai_identity_name(title)
                name = parsed[0] if parsed else anime_mod.clean_release_title(title)
                found = anilist.resolve_in(self.table, name).anime
                self._entries[key] = found.id if found is not None else None
        return self._entries[key]

    def chain(self, aid: int) -> list[int]:
        """The show's entries in sequel order, first season first."""
        if aid not in self._chains:
            members = anime_collections.chain(aid, anime_collections.Store())
            inside = set(members)
            links = [
                (member, other, kind)
                for member in members
                for other, kind, _form in anilist.relations(member)
                if other in inside
            ]
            order = anime_collections.sequel_order(members, links)
            for member in members:
                self._chains[member] = order
        return self._chains[aid]

    def root(self, title: str) -> int | None:
        aid = self.entry(title)
        return self.chain(aid)[0] if aid is not None else None

    def place(self, title: str) -> tuple[int, int] | None:
        """(the show, the entry's place in its sequel order), for ordering releases."""
        aid = self.entry(title)
        if aid is None:
            return None
        order = self.chain(aid)
        return order[0], order.index(aid)

    def titles(self, root: int) -> list[str]:
        """The romanised names of the show's seasons, as Erai names its releases."""
        names = []
        for aid in self.chain(root):
            if anilist.kind(aid) not in {"TV", "ONA"}:
                continue
            entry = self.table.anime.get(aid) if self.table is not None else None
            name = entry.title if entry is not None else anilist.media(aid).get("romaji")
            if name:
                names.append(name)
        return list(dict.fromkeys(names))


async def _ordered_candidates(
    state: dict[str, Any],
    fresh: dict[str, anime_mod.NyaaEntry],
    client: httpx.AsyncClient,
) -> list[anime_mod.NyaaEntry]:
    # Index each RSS show's full high-quality release set before starting with
    # its latest episode. Persist work when a long series needs query shards.
    shows = _Shows(await anilist.index(), await asyncio.to_thread(_load_mappings))
    groups: dict[str, anime_mod.NyaaEntry] = {}
    family_ids = {}
    discovered = {
        **state["backfill"].get("catalog_1080", {}),
        **{key: _entry_dict(value) for key, value in fresh.items()},
    }
    for entry in sorted(
        (_entry_from_dict(row) for row in discovered.values()),
        key=lambda item: item.id,
        reverse=True,
    ):
        parent = shows.root(entry.title)
        key = f"anilist:{parent}" if parent else _mapping_key(entry.title)
        family_ids[key] = parent
        index = state["series_catalogs"].get(key)
        needs_old = index and (
            not index.get("complete")
            or _release_key(entry) not in index["catalog_1080"]
            or any(
                _needs_consideration(state, _entry_from_dict(row))
                for row in index["catalog_1080"].values()
            )
        )
        if _needs_consideration(state, entry) or needs_old:
            groups.setdefault(key, entry)
    candidates = [
        _entry_from_dict(row)
        for index in state["series_catalogs"].values()
        if index.get("complete")
        for row in index["catalog_1080"].values()
    ]
    if state["backfill"].get("complete") or state["backfill"].get("phase") == "720":
        candidates.extend(
            _entry_from_dict(item)
            for item in [
                *state["backfill"]["catalog_1080"].values(),
                *state["backfill"]["catalog_720"].values(),
            ]
        )
    for key, entry in list(groups.items())[:4]:
        parent = family_ids[key]
        if key not in state["series_catalogs"]:
            titles = shows.titles(parent) if parent else []
            titles = titles or [anime_mod.clean_release_title(entry.title)]
            state["series_catalogs"][key] = {
                **_default_state()["backfill"],
                "title_query": titles[0],
                "title_queries": titles,
                "title_query_index": 0,
                "parent_anilist_id": parent,
                "high_only": True,
            }
        index = state["series_catalogs"][key]
        source_title = anime_mod.clean_release_title(entry.title)
        queries = []
        for title in index["title_queries"]:
            # Nyaa's quoted phrases distinguish punctuation even when the
            # title identity does not. Prefer the observed Erai spelling.
            observed = (
                source_title
                if anilist.normalise(title) == anilist.normalise(source_title)
                else title
            )
            for variant in (observed, observed.replace(".", "")):
                if variant not in queries:
                    queries.append(variant)
        if source_title not in queries:
            queries.append(source_title)
        if queries != index["title_queries"]:
            index.update(
                {
                    "title_queries": queries,
                    "title_query": queries[0],
                    "title_query_index": 0,
                    "phase": "2160",
                    "complete": False,
                    "frontier": [{"include": [], "exclude": [], "page": 1}],
                }
            )
        newest = max((row["id"] for row in index["catalog_1080"].values()), default=0)
        if index.get("complete") and entry.id > newest:
            index.update(
                {
                    "phase": "2160",
                    "complete": False,
                    "title_query_index": 0,
                    "title_query": index["title_queries"][0],
                    "frontier": [{"include": [], "exclude": [], "page": 1}],
                }
            )
        try:
            await _crawl_backfill({"backfill": index}, client)
            index["error"] = None
        except Exception as exc:
            index["error"] = f"{type(exc).__name__}: {exc}"
            log.warning("Erai series indexing paused for %s: %s", key, exc)
            continue
        if not index.get("complete"):
            continue
        if _release_key(entry) not in index["catalog_1080"]:
            # The feed proves a release exists. If indexing did not find it,
            # the query is incomplete, not proof that no older episodes exist.
            index.update(
                {
                    "error": "Known RSS release was not found in the catalogue; latest episode is blocked",
                    "complete": False,
                    "phase": "2160",
                    "title_query_index": 0,
                    "title_query": queries[0],
                    "frontier": [{"include": [], "exclude": [], "page": 1}],
                }
            )
            continue
        for release in [
            *(_entry_from_dict(row) for row in index["catalog_1080"].values()),
            *fresh.values(),
        ]:
            if parent:
                # A search by a season's name finds other shows too.
                if shows.root(release.title) == parent:
                    candidates.append(release)
            elif anime_mod.clean_release_title(release.title) == source_title:
                candidates.append(release)
    cutoff = time.time() - get_settings().anime.settle_minutes * 60
    by_hash = {
        entry.info_hash: entry
        for entry in candidates
        if _is_hevc(entry) and _published_timestamp(entry) <= cutoff
    }
    # The seasons of one show come in sequel order, not alphabetically:
    # "Kashin" before "Ketsubetsu" was the order of their names, not of the
    # story. One sample per name, found in one pass: searching every release
    # again for each name's first was quadratic, and over a catalogue of tens
    # of thousands it held the event loop -- and so every page -- for minutes.
    places = {}
    samples: dict[str, anime_mod.NyaaEntry] = {}
    for entry in by_hash.values():
        samples.setdefault(_mapping_key(entry.title), entry)
    for title, sample in samples.items():
        place = shows.place(sample.title)
        if place is not None:
            places[title] = place

    def order(entry: anime_mod.NyaaEntry) -> tuple:
        key, season, episode, quality, release_id = _episode_order(entry)
        if key in places:
            root, position = places[key]
            return (f"anilist:{root:010d}", position, episode, quality, release_id)
        return (
            anime_mod.clean_release_title(entry.title).casefold(),
            season,
            episode,
            quality,
            release_id,
        )

    return sorted(by_hash.values(), key=order)


async def run_cycle(*, prefill: bool = False, retries_only: bool = False) -> dict[str, Any]:
    """Run one RSS poll/backfill slice. Safe to call from the API or scheduler."""

    async with _CYCLE_LOCK:
        settings = get_settings()
        policy = settings.anime
        state = _load_state()
        base_token = _begin_cycle_state(state)
        try:
            _prune_holds(state)
            state["last_poll"] = time.time()
            state["last_enqueued"] = 0
            if not policy.enabled or updates.maintenance_active():
                _save_state(state)
                return status(state=state, running=False)
            free = free_space_gib()
            if free is None:
                state["last_error"] = "Anime destination is unavailable"
                _save_state(state)
                return status(state=state, running=False)
            if free <= policy.min_free_space_gib:
                state["last_error"] = f"Free space reserve reached ({free:.1f} GiB available)"
                _save_state(state)
                return status(state=state, running=False)
            headers = {"User-Agent": settings.scraper.user_agent}
            enqueued = 0
            _set_activity("Checking qBittorrent and free space")
            qbit = None
            admission_token = _ADMISSION.set(None)
            # A fresh look at the library each cycle; see _library_episodes.
            disk_token = _DISK_INDEX.set({})
            try:
                if prefill:
                    from bankai.torrent.qbittorrent import QBittorrentClient

                    qbit = QBittorrentClient()
                    await qbit.login()
                    torrents = await qbit.list_torrents(category=settings.qbittorrent.category)
                    cleaned = 0
                    if settings.paths.cleanup_after_success:
                        cleaned = await _cleanup_verified_torrents(qbit, torrents)
                        if cleaned:
                            torrents = await qbit.list_torrents(
                                category=settings.qbittorrent.category
                            )
                    state["last_cleanup_count"] = cleaned
                    try:
                        await _rotate_stuck_torrents(state, qbit, torrents)
                    except Exception as exc:
                        log.warning("[anime] could not rotate stuck downloads: %s", exc)
                    download_free_bytes = await qbit.free_space_bytes()
                    state["download_free_space_gib"] = (
                        round(download_free_bytes / _GIB, 1)
                        if download_free_bytes is not None
                        else None
                    )
                    reserved = _reserved_torrent_bytes(state, torrents)
                    download_budget = (
                        max(0, download_free_bytes - int(policy.min_free_space_gib * _GIB))
                        if download_free_bytes is not None
                        else int((free - policy.min_free_space_gib) * _GIB)
                    )
                    _ADMISSION.set(
                        {
                            "qbit": qbit,
                            "remaining": max(
                                0,
                                min(
                                    int((free - policy.min_free_space_gib) * _GIB),
                                    download_budget,
                                )
                                - reserved,
                            ),
                        }
                    )
                async with httpx.AsyncClient(
                    headers=headers, timeout=30, follow_redirects=True
                ) as client:
                    _set_activity("Clearing holds already in the library")
                    with suppress(Exception):
                        await asyncio.to_thread(_settle_holds_on_disk, state)
                    _set_activity("Asking Erai-raws about subtitles")
                    with suppress(Exception):
                        await _recheck_german_holds(state)
                    _set_activity("Gathering held releases to check again")
                    retries = await _retry_candidates(state, client)
                    if not retries_only:
                        _set_activity("Reading the Erai-raws feed")
                    rss = [] if retries_only else await _fetch_rss(client)
                    if not retries_only:
                        _set_activity("Indexing the Nyaa catalogue")
                        try:
                            await _crawl_backfill(state, client)
                            state["backfill"]["error"] = None
                        except Exception as exc:
                            state["backfill"]["error"] = f"{type(exc).__name__}: {exc}"
                            log.warning("Erai backfill paused; RSS ingestion continues: %s", exc)
                    fresh: dict[str, anime_mod.NyaaEntry] = {}
                    cutoff = time.time() - policy.settle_minutes * 60
                    for entry in rss:
                        key = _release_key(entry)
                        if (
                            key is None
                            or _resolution(entry) < 1080
                            or not _is_hevc(entry)
                            or _published_timestamp(entry) > cutoff
                        ):
                            continue
                        current = fresh.get(key)
                        if current is None or _rank(entry) > _rank(current):
                            fresh[key] = entry
                    if not retries_only:
                        _set_activity("Ordering the backlog")
                    normal = [] if retries_only else await _ordered_candidates(state, fresh, client)
                    candidates = list({item.info_hash: item for item in [*retries, *normal]}.values())
                    inspected = 0
                    # The loop stops at this many checks, or earlier at its enqueue cap.
                    budget = min(len(candidates), max(100, policy.max_enqueues_per_cycle * 3))
                    for entry in candidates:
                        if enqueued >= policy.max_enqueues_per_cycle or inspected >= max(
                            100, policy.max_enqueues_per_cycle * 3
                        ):
                            break
                        if not _needs_consideration(state, entry):
                            continue
                        if not get_settings().anime.enabled or updates.maintenance_active():
                            break
                        admission = _ADMISSION.get()
                        if admission is not None and admission["remaining"] <= 0:
                            break
                        inspected += 1
                        _set_activity(
                            "Checking releases",
                            done=inspected,
                            total=budget,
                            current=entry.title,
                            enqueued=enqueued,
                        )
                        if free_space_gib() is None or free_space_gib() <= policy.min_free_space_gib:
                            break
                        previous = dict(state["releases"].get(entry.info_hash, {}))
                        if await _consider(state, entry, client):
                            enqueued += 1
                        _prune_holds(state)
                        current = state["releases"].get(entry.info_hash, {})
                        if current != previous and entry.info_hash in _load_retry_requests():
                            # Commit the result before consuming its durable retry request.
                            _save_state(state)
                            with _STATE_LOCK:
                                requests = _load_retry_requests()
                                requests.pop(entry.info_hash, None)
                                _save_retry_requests(requests)
                        await asyncio.sleep(policy.backfill_request_delay_seconds)
                        if inspected % 10 == 0:
                            _save_state(state)
                    if prefill:
                        _set_activity("Swapping queued AVC episodes for HEVC")
                        upgraded = await _upgrade_to_hevc(
                            state, client, limit=policy.max_hevc_upgrades_per_cycle
                        )
                        if upgraded:
                            state["last_upgraded"] = upgraded
                            _save_state(state)
                state["last_success"] = time.time()
                state["last_error"] = None
                state["last_enqueued"] = enqueued
            except Exception as exc:
                state["last_error"] = f"{type(exc).__name__}: {exc}"
                log.warning("Erai automation cycle failed: %s", exc)
            finally:
                _set_activity(None)
                _ADMISSION.reset(admission_token)
                _DISK_INDEX.reset(disk_token)
                if qbit is not None:
                    await qbit.aclose()
            _save_state(state)
            return status(state=state, running=False)
        finally:
            # Always: an id left behind could be reused by the next cycle's state.
            _CYCLE_BASE.reset(base_token)


# ---------------------------------------------------------------------------
# Release reconciliation
#
# qBittorrent owns the download; bankai owns publishing. The reconciler reads
# the former and drives the latter, so a release's state is always derived from
# what is actually on disk rather than from a job that may have died.
#
#   queued -> downloading -> transferring -> deleting -> done
#
# Downloading costs bankai nothing, so it is deliberately uncapped -- the old
# design spawned a worker that blocked on wait_until_complete for hours, which
# is why three slots could not keep up with a thousand releases and why
# finished torrents sat unpublished.
# ---------------------------------------------------------------------------

_ACTIVE_RELEASE_STATES = {"queued", "downloading", "complete", "transferring", "deleting"}

# A publish that fails must not abandon a finished download. "failed" is
# otherwise terminal, so nothing retries it, and cleanup only removes torrents
# it can see published -- so the download keeps its disk space forever. Eighty
# eight of those once filled the staging drive, which then failed every
# further publish, which stranded more downloads: the queue stopped moving
# entirely. Retried on a widening backoff, and only a few times, so a release
# that genuinely cannot be published gives up rather than cycling.
_MAX_PUBLISH_ATTEMPTS = 5
_PUBLISH_BACKOFF_SECONDS = 900.0


def _worth_republishing(release: dict[str, Any], torrent: Any) -> bool:
    """Is this a finished download whose publish failed, ready to try again?"""
    if torrent is None or float(getattr(torrent, "progress", 0.0) or 0.0) < 1.0:
        return False
    if int(release.get("publish_attempts") or 0) >= _MAX_PUBLISH_ATTEMPTS:
        return False
    return time.time() >= float(release.get("publish_retry_after") or 0.0)
_STALE_READD_REASON = "Torrent could not be re-added"
_QBIT_ERROR_REASON = "qBittorrent reports"
# A release being deleted still has a job, but that job already reads "done"
# and is hidden from the queue by default, so the release row is what keeps
# the last step visible.
_PUBLISHABLE = {"queued", "downloading", "complete", "deleting"}


def _release_job_running(release: dict[str, Any]) -> bool:
    from bankai.cli import bgjobs

    job_id = release.get("job_id")
    if not job_id:
        return False
    job = bgjobs.get_job(str(job_id))
    return job is not None and job.status in {"running", "stopped"}


def _running_transfer_count(state: dict[str, Any]) -> int:
    return sum(
        1
        for release in state["releases"].values()
        if release.get("status") in {"transferring", "deleting"}
        and _release_job_running(release)
    )


def _torrent_phase(torrent: Any) -> str:
    """Map a qBittorrent row onto the release vocabulary."""
    state_name = str(getattr(torrent, "state", "")).casefold()
    if "error" in state_name or "missing" in state_name:
        return "error"
    if float(getattr(torrent, "progress", 0.0)) >= 1.0 or state_name.endswith("up"):
        return "complete"
    if "downloading" in state_name or "forceddl" in state_name or "metadl" in state_name:
        return "downloading"
    return "queued"


def _settle_holds_on_disk(state: dict[str, Any]) -> int:
    """Holds whose episodes are all in the library already: nothing to decide.

    A season can arrive by another route than the releases held for it --
    Kaguya-sama's third season came as a batch while twelve of its single
    episodes stayed in review, held for German the library already had.
    Holds from the TVDB and AniDB days are read here too, on AniList: one
    already on disk stops being held without waiting for its recheck.
    """
    table = anilist.cached_index()
    if table is None:
        return 0
    mappings = _load_mappings()
    settled = 0
    for release in (state.get("releases") or {}).values():
        if release.get("status") != "held":
            continue
        pairs = _release_pairs(str(release.get("title") or ""), table=table, mappings=mappings)
        if not pairs:
            continue
        if all(_episode_on_disk(aid, episode) for aid, episode in pairs):
            release.update(status="existing", reason="Already in the library")
            release.pop("retry_after", None)
            settled += 1
    if settled:
        _prune_holds(state)
        log.info("Cleared %d held releases already in the library", settled)
    return settled


def reconcile_stale_holds() -> dict[str, int]:
    """Clear holds that the current policy has already made meaningless.

    Two kinds accumulated. Some releases state German in their own title and
    were only held because the check read the Nyaa description; a hold
    otherwise waits a day before being looked at again, and an episode that
    always qualified should not wait. Others are not HEVC, so the codec policy
    rejects them the moment they are reconsidered -- leaving those in review
    implies a decision is wanted when there is none to make, and hides that
    the episode is still waiting for its HEVC release.
    """
    reopened = 0
    filtered = 0
    with _STATE_LOCK:
        state = _load_state()
        for release in state.get("releases", {}).values():
            if release.get("status") != "held":
                continue
            title = str(release.get("title") or "")
            if not _is_hevc_title(title):
                release["status"] = "filtered"
                release["reason"] = "Release is not HEVC/H.265"
                filtered += 1
                continue
            if "German subtitles" not in str(release.get("reason") or ""):
                continue
            if not title_lists_german_subtitles(title):
                continue
            release["retry_after"] = 0
            reopened += 1
        if reopened or filtered:
            releases = state.get("releases", {})
            state["held"] = [
                row
                for row in state.get("held", [])
                if releases.get(row.get("info_hash"), {}).get("status") == "held"
            ]
            _save_state(state)
    return {"reopened": reopened, "filtered": filtered}


def retire_download_pendings() -> dict[str, int]:
    """Drop pending jobs that only existed to wait for a download.

    Releases used to be pushed into the worker queue at discovery time, where
    they sat behind the pipeline slot limit for as long as the download took.
    The reconciler now spawns a job only when there are bytes to copy, so those
    entries are redundant -- but a pending job whose release is untracked is
    the only remaining record of that episode, so it is adopted rather than
    dropped.
    """
    from bankai.web import jobs as webjobs

    retired = 0
    adopted = 0
    kept = 0
    with _STATE_LOCK:
        state = _load_state()
        releases = state["releases"]
        by_hash = {key.casefold(): value for key, value in releases.items()}
        for item in webjobs.list_pending():
            if not item.args or item.args[0] != "anime-download":
                kept += 1
                continue
            info_hash = (_args_value(item.args, "--info-hash") or "").casefold()
            release = by_hash.get(info_hash) if info_hash else None
            if release is None:
                if not info_hash:
                    kept += 1
                    continue
                releases[info_hash] = {
                    "status": "queued",
                    "title": _args_value(item.args, "--release-title") or item.title,
                    "display_title": item.title,
                    "args": list(item.args),
                    "updated_at": time.time(),
                }
                adopted += 1
            elif not release.get("args"):
                # Older records predate storing the arguments, and without them
                # the reconciler could never publish the release.
                release["args"] = list(item.args)
                release.setdefault("display_title", item.title)
            webjobs.cancel_pending(item.id)
            retired += 1
        _save_state(state)
    return {"retired": retired, "adopted": adopted, "kept": kept}


def _rebuilt_args(
    state: dict[str, Any], info_hash: str, release: dict[str, Any]
) -> list[str] | None:
    """Reconstruct publishing arguments for a record written before they were kept.

    Releases tracked by older builds stored no arguments, so the reconciler had
    nothing to publish with and skipped them forever -- which is how finished
    downloads sat untouched. The canonical key carries the AniList entry and
    episode, and the entry is on record, so the arguments can be rebuilt.
    """
    canonical = str(release.get("canonical") or "")
    if not canonical.startswith("anilist:"):
        return None
    head, _, episode = canonical.partition("|")
    aid = head.split(":", 1)[1]
    record = (state.get("anime") or {}).get(aid) or {}
    name = record.get("name") or record.get("romaji")
    if not name or not aid.isdigit() or not episode.isdigit():
        return None
    return [
        "anime-download",
        "--release-title",
        str(release.get("title") or name),
        # The torrent is already in qBittorrent, so a magnet built from the
        # info hash re-adds nothing; these URLs only satisfy the nyaa.si check.
        "--torrent-url",
        "https://nyaa.si/",
        "--detail-url",
        "https://nyaa.si/",
        "--magnet-uri",
        f"magnet:?xt=urn:btih:{info_hash}",
        "--info-hash",
        info_hash,
        "--kind",
        "show",
        "--anilist-id",
        aid,
        "--anilist-title",
        str(name),
        "--english-title",
        str(name),
        "--episode",
        episode,
        "--require-german-subtitles",
    ]


def _publish_args(release: dict[str, Any], args: list[str]) -> list[str]:
    """The arguments a publishing job runs with, as the show's policy stands now.

    A show let in without German subtitles was downloaded on that word, but its
    stored arguments still asked the publish step to verify German -- which
    rejected every file, so each finished download sat seeding for good. The
    policy is read at publish time, so a decision made after the download
    counts too.
    """
    policy = _series_policy(str(release.get("title") or ""))
    if policy and policy.get("mode") == "german_allowed":
        return [arg for arg in args if arg != "--require-german-subtitles"]
    return list(args)


def _episode_already_published(
    state: dict[str, Any],
    release: dict[str, Any],
    cache: dict[str, set[tuple[int, int]]] | None = None,
) -> bool:
    """Is this episode already sitting in the library?"""
    canonical = str(release.get("canonical") or "")
    if not canonical.startswith("anilist:"):
        return False
    head, _, number = canonical.partition("|")
    aid = head.split(":", 1)[1]
    if not aid.isdigit() or not number.isdigit():
        return False
    return _episode_on_disk(int(aid), int(number))


async def _readd_torrent(qbit: Any, info_hash: str, release: dict[str, Any]) -> bool:
    """Put a tracked release back into qBittorrent.

    Records written by older builds stored no arguments, so keying this on
    them held two hundred releases with "could not be re-added" when there was
    nothing wrong with them. The info hash is the release's identity and is
    always present, and a magnet needs nothing else -- peers come from DHT.
    """
    args = release.get("args")
    magnet = _args_value(args, "--magnet-uri") or f"magnet:?xt=urn:btih:{info_hash}"
    torrent_url = _args_value(args, "--torrent-url")
    settings = get_settings()
    try:
        await qbit.add(
            magnet=magnet or None,
            torrent_url=None if magnet else torrent_url,
            category=settings.qbittorrent.category,
            save_path=Path(settings.qbittorrent.save_path)
            if settings.qbittorrent.save_path
            else None,
        )
    except Exception as exc:
        log.warning("Could not re-add %s: %s", str(release.get("title"))[:60], exc)
        return False
    return True


async def reconcile_releases() -> dict[str, int]:
    """Advance every tracked release one step. Safe to call on a timer.

    With automation off it still publishes finished downloads and removes
    their torrents, but starts, resumes or re-adds none.
    """
    try:
        await hold_downloads(hold=not get_settings().anime.enabled)
    except Exception as exc:
        log.warning("Could not pause or resume the waiting anime downloads: %s", exc)
    # Its own look at the library, not one left over from an earlier pass.
    token = _DISK_INDEX.set({})
    try:
        return await _reconcile_releases()
    finally:
        _DISK_INDEX.reset(token)


def _held_path() -> Path:
    return _state_path().with_name("erai_downloads_held.json")


def _asked_for_hashes() -> set[str]:
    """Unfinished releases started by hand rather than by the automation.

    Both an HEVC upgrade and a replacement picked on Erai-raws record the files
    they will replace, until they are published.
    """
    return {
        info_hash.casefold()
        for info_hash, release in _load_state()["releases"].items()
        if release.get("replace_files") is not None
        and release.get("status") in _ACTIVE_RELEASE_STATES
    }


async def hold_downloads(*, hold: bool) -> int:
    """Keep bankai's unfinished torrents paused while automatic downloads are off.

    Turning the automation off stops new releases, but the backlog already
    handed to qBittorrent would go on downloading into whatever space is
    freed. So while it is off, every unfinished torrent of bankai's category
    is paused, and which ones is written down; once it is on again, exactly
    those are resumed -- never one paused by hand. A download asked for by
    hand -- an HEVC upgrade, a replacement picked on Erai-raws -- is not
    backlog and goes on: pausing it made the button look broken. Returns how
    many changed.
    """
    from bankai.torrent.qbittorrent import QBittorrentClient

    path = _held_path()
    try:
        held = set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        held = set()
    if not hold and not held:
        return 0
    qbit = QBittorrentClient()
    try:
        await qbit.login()
        if hold:
            asked = _asked_for_hashes()
            torrents = await qbit.list_torrents(category=get_settings().qbittorrent.category)
            due = [
                t.hash
                for t in torrents
                if t.progress < 1.0
                and t.hash not in held
                and str(t.hash).casefold() not in asked
                and not t.state.startswith(("paused", "stopped"))
                and t.state not in {"error", "missingFiles"}
            ]
            # Paused before they were told apart from the backlog.
            freed = sorted(h for h in held if h.casefold() in asked)
            if not due and not freed:
                return 0
            if due:
                await qbit.pause("|".join(due))
                held.update(due)
                log.info("Automatic downloads are off: paused %d waiting anime downloads", len(due))
            if freed:
                await qbit.resume("|".join(freed))
                held.difference_update(freed)
                log.info("Resumed %d downloads asked for by hand", len(freed))
            changed = len(due) + len(freed)
        else:
            await qbit.resume("|".join(sorted(held)))
            log.info("Automatic downloads are on: resumed %d anime downloads", len(held))
            changed, held = len(held), set()
    finally:
        await qbit.aclose()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(sorted(held)), encoding="utf-8")
    tmp.replace(path)
    return changed


async def _reconcile_releases() -> dict[str, int]:
    from bankai.cli import bgjobs
    from bankai.torrent.qbittorrent import QBittorrentClient

    settings = get_settings()
    if updates.maintenance_active():
        return {}
    # Off, the pass still finishes what is already downloaded: publishing is a
    # hard link that needs no space, and removing the torrent afterwards leaves
    # the bytes to the library alone. It only stops bringing bytes in: no
    # torrent is re-added, and hold_downloads keeps the unfinished ones paused.
    downloading = settings.anime.enabled

    qbit = QBittorrentClient()
    counts: dict[str, int] = {}

    def tally(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    try:
        await qbit.login()
        torrents = await qbit.list_torrents(category=settings.qbittorrent.category)
    except Exception as exc:
        log.warning("Release reconciliation skipped; qBittorrent unreachable: %s", exc)
        await qbit.aclose()
        return {}

    try:
        with _STATE_LOCK:
            state = _load_state()
            by_hash = {str(item.hash).casefold(): item for item in torrents}
            limit = max(1, settings.anime.max_concurrent_transfers)
            running = _running_transfer_count(state)
            published: dict[str, set[tuple[int, int]]] = {}

            for info_hash, release in list(state["releases"].items()):
                status = str(release.get("status") or "")
                if status == "running":
                    # Written by the old model for a worker that no longer
                    # exists. The download is what matters, so re-drive it.
                    status = "queued"
                    release["status"] = status
                elif status == "failed" and _worth_republishing(
                    release, by_hash.get(info_hash.casefold())
                ):
                    # The bytes are already on disk, so this costs no download
                    # and frees the torrent's space as soon as it lands.
                    attempts = int(release.get("publish_attempts") or 0) + 1
                    release["publish_attempts"] = attempts
                    release["publish_retry_after"] = time.time() + (
                        _PUBLISH_BACKOFF_SECONDS * (2 ** (attempts - 1))
                    )
                    status = "queued"
                    release["status"] = status
                    release.pop("reason", None)
                elif (
                    downloading
                    and status == "held"
                    and str(release.get("reason") or "").startswith(_STALE_READD_REASON)
                ):
                    # Held by an earlier bug that refused to re-add a release
                    # unless its record stored the magnet. Nothing was ever
                    # wrong with these, so let them back into the pipeline.
                    status = "queued"
                    release["status"] = status
                    release.pop("reason", None)
                elif (
                    status == "held"
                    and str(release.get("reason") or "").startswith(_QBIT_ERROR_REASON)
                    and (current := by_hash.get(info_hash.casefold())) is not None
                    and _torrent_phase(current) != "error"
                ):
                    # Held while qBittorrent reported an error -- a full disk
                    # does it. The error is gone, so the release goes on.
                    status = "queued"
                    release["status"] = status
                    release.pop("reason", None)
                if status not in _ACTIVE_RELEASE_STATES:
                    continue
                torrent = by_hash.get(info_hash.casefold())

                if status in {"transferring", "deleting"}:
                    job_id = release.get("job_id")
                    job = bgjobs.get_job(str(job_id)) if job_id else None
                    if job is None or job.status in {"running", "stopped"}:
                        tally(status)
                        continue
                    if job.status == "done":
                        # download_anime removes the torrent itself once the
                        # file is published, so a torrent still present means
                        # the removal is the only step left.
                        release.pop("publish_attempts", None)
                        release.pop("publish_retry_after", None)
                        if getattr(job, "final_path", None):
                            # Where it was filed.
                            release["published_path"] = job.final_path
                        if release.get("replace_files") is not None:
                            # Here, not in a thread: it writes the file index,
                            # whose lock is the state lock this pass holds.
                            release["replaced_files"] = _finish_replacement(release)
                        release["status"] = "deleting" if torrent is not None else "done"
                        if torrent is not None:
                            try:
                                await qbit.remove(info_hash, delete_files=True)
                                release["status"] = "done"
                            except Exception as exc:
                                log.warning(
                                    "Could not remove published torrent %s: %s",
                                    info_hash[:8],
                                    exc,
                                )
                    else:
                        release["status"] = "failed"
                        release["reason"] = f"Publishing job {job.status}"
                        # Retried a few times; back to review once it gives up.
                        if release.get("replaced_holds") is not None and int(
                            release.get("publish_attempts") or 0
                        ) >= _MAX_PUBLISH_ATTEMPTS:
                            _undo_replacement(state, release)
                    release["updated_at"] = time.time()
                    tally(release["status"])
                    continue

                if torrent is None:
                    # Nothing else will ever re-add it: discovery skips a
                    # release it has already recorded, and a publishing job is
                    # only spawned once the download is complete. The record
                    # carries the magnet, so the reconciler re-adds it itself.
                    if _episode_already_published(state, release, published):
                        # Published before the reconciler existed. Re-downloading
                        # would spend the same scarce disk twice on a file that
                        # is already sitting in the library.
                        release["status"] = "done"
                        release["updated_at"] = time.time()
                        tally("already_published")
                        continue
                    if not downloading:
                        # Re-adding is downloading again; it waits for automation.
                        tally("waiting_for_automation")
                        continue
                    if await _readd_torrent(qbit, info_hash, release):
                        release["status"] = "queued"
                        release["updated_at"] = time.time()
                        tally("readded")
                    else:
                        release["status"] = "held"
                        release["reason"] = f"{_STALE_READD_REASON} to qBittorrent"
                        release["retry_after"] = 0
                        release["updated_at"] = time.time()
                        tally("held")
                    continue

                phase = _torrent_phase(torrent)
                if phase == "error":
                    release["status"] = "held"
                    release["reason"] = f"{_QBIT_ERROR_REASON} {torrent.state}"
                    release["retry_after"] = 0
                    release["updated_at"] = time.time()
                    tally("held")
                    continue
                if phase != "complete":
                    if release.get("status") != phase:
                        release["status"] = phase
                        release["updated_at"] = time.time()
                    tally(phase)
                    continue

                # The download is finished from here on, whether or not the
                # transfer lane has room. Saying so is the point: leaving it as
                # "downloading" while it waits reports something that is simply
                # not true any more.
                if release.get("status") != "complete":
                    release["status"] = "complete"
                    release["updated_at"] = time.time()
                args = release.get("args")
                if not args:
                    args = _rebuilt_args(state, info_hash, release)
                    if args:
                        release["args"] = args
                    else:
                        # Surface it rather than skipping every pass in silence.
                        release["status"] = "held"
                        release["reason"] = "Publishing arguments are missing and unrebuildable"
                        release["retry_after"] = 0
                        release["updated_at"] = time.time()
                        tally("unpublishable")
                        continue
                if running >= limit:
                    tally("complete")
                    continue
                job = bgjobs.spawn(
                    kind="show",
                    title=str(release.get("display_title") or release.get("title") or info_hash),
                    args=_publish_args(release, args),
                )
                release["status"] = "transferring"
                release["job_id"] = job.id
                release["updated_at"] = time.time()
                running += 1
                tally("transferring")

            _save_state(state)
    finally:
        await qbit.aclose()
    return counts


def release_queue_rows(state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Queue rows for releases that have no worker of their own yet.

    Most releases are waiting on qBittorrent and deliberately have no bankai
    job, so without these the queue would show only the handful of active
    publishes and none of the backlog.
    """
    state = _load_state() if state is None else state
    rows: list[dict[str, Any]] = []
    for info_hash, release in state["releases"].items():
        status = str(release.get("status") or "")
        if status not in _PUBLISHABLE:
            continue
        rows.append(
            {
                "id": info_hash[:8],
                "kind": "show",
                "title": str(release.get("display_title") or release.get("title") or info_hash),
                "status": "queued",
                "phase": status,
                "started_at": float(release.get("updated_at") or 0.0),
                "updated_at": float(release.get("updated_at") or 0.0),
                "finished_at": None,
                "exit_code": None,
                "final_path": None,
                "step": None,
                "total_steps": None,
                "step_key": None,
                "step_label": {
                    "downloading": "Downloading in qBittorrent",
                    "complete": "Downloaded, waiting to publish",
                    "deleting": "Removing the torrent",
                }.get(status, "Waiting for qBittorrent"),
                "overall_percent": 100.0 if status in {"complete", "deleting"} else 0.0,
                "transfer_percent": None,
                "pending": True,
                "action_required": False,
                "reason": None,
                "reason_detail": None,
                "queue_position": None,
                "queue_total": None,
                "anilist_id": _args_value(release.get("args"), "--anilist-id"),
                "german_source_url": None,
                "torrent_source_url": None,
                "torrent_source_title": release.get("title"),
            }
        )
    return rows


def _args_value(args: Any, flag: str) -> str | None:
    if not isinstance(args, list):
        return None
    try:
        return str(args[args.index(flag) + 1])
    except (ValueError, IndexError):
        return None


def status(*, state: dict[str, Any] | None = None, running: bool | None = None) -> dict[str, Any]:
    state = state or _load_state()
    _prune_holds(state)
    policy = get_settings().anime
    free = free_space_gib()
    download_free = download_free_space_gib(state)
    if not policy.enabled:
        pause_reason = "Automation is disabled"
    elif _delegated() and _worker_heartbeat() is None:
        pause_reason = "The automation worker is not running (bankai-automation)"
    elif free is None:
        pause_reason = "Anime destination is unavailable"
    elif free <= policy.min_free_space_gib:
        pause_reason = f"{policy.min_free_space_gib:g} GiB reserve reached ({free:.1f} GiB free)"
    elif download_free is not None and download_free <= policy.min_free_space_gib:
        pause_reason = (
            f"Download staging reserve reached ({download_free:.1f} GiB free on qBittorrent)"
        )
    elif state.get("last_error"):
        pause_reason = state["last_error"]
    else:
        pause_reason = None
    counts: dict[str, int] = {}
    for item in state.get("releases", {}).values():
        name = str(item.get("status", "unknown"))
        counts[name] = counts.get(name, 0) + 1
    backfill = state["backfill"]
    return {
        "enabled": policy.enabled,
        "running": _cycle_running() if running is None else running,
        "paused": pause_reason is not None,
        "pause_reason": pause_reason,
        "free_space_gib": round(free, 1) if free is not None else None,
        "download_free_space_gib": download_free,
        "min_free_space_gib": policy.min_free_space_gib,
        "poll_interval_seconds": policy.poll_interval_seconds,
        "rss_url": policy.rss_url,
        "feed_source": "Nyaa / Erai-raws",
        "series_indexes": [
            {
                "title": index["title_query"],
                "complete": index.get("complete", False),
                "phase": index.get("phase"),
                "error": index.get("error"),
            }
            for index in state.get("series_catalogs", {}).values()
        ],
        "settle_minutes": policy.settle_minutes,
        "last_poll": state.get("last_poll"),
        "last_success": state.get("last_success"),
        "last_error": state.get("last_error"),
        "last_enqueued": state.get("last_enqueued", 0),
        "counts": counts,
        "held": state.get("held", [])[:100],
        "retry_pending": sum(
            state["releases"].get(key, {}).get("status") == "held" for key in _load_retry_requests()
        ),
        "backfill": {
            "enabled": policy.backfill_enabled,
            "phase": backfill.get("phase", "2160"),
            "page": backfill.get("page", 1),
            "complete": bool(backfill.get("complete")),
            "found_1080": len(backfill.get("catalog_1080", {})),
            "fallback_720": len(backfill.get("catalog_720", {})),
            "queries_completed": backfill.get("queries_completed", 0),
            "queries_split": backfill.get("queries_split", 0),
            "queries_pending": len(backfill.get("frontier", [])),
            "error": backfill.get("error"),
        },
    }


_MANUAL_TASK: asyncio.Task | None = None
_RETRY_TASK: asyncio.Task | None = None


def retry_held(reason: str | None = None) -> dict:
    """Persist one fresh attempt for every held release and return immediately.

    ``reason`` limits it to holds whose reason contains that text -- "AniDB",
    say, for the releases held only because AniDB could not place them,
    which AniList now may -- sparing a Nyaa lookup for every other hold.
    """
    with _STATE_LOCK:
        state = _load_state()
        catalog = _catalog_entries(state)
        held = {item["info_hash"]: item for item in state.get("held", [])}
        requests = _load_retry_requests()
        requested = 0
        for info_hash, release in state["releases"].items():
            if release.get("status") != "held":
                continue
            if reason and reason not in str(release.get("reason") or ""):
                continue
            requests[info_hash] = {
                "title": release["title"],
                "entry": release.get("entry") or catalog.get(info_hash),
            }
            detail_url = held.get(info_hash, {}).get("detail_url")
            if detail_url:
                anime_mod._DETAIL_CACHE.pop(detail_url, None)
            requested += 1
        _save_retry_requests(requests)
    if requested:
        _start_retry_cycle()
    return {**status(), "requested": requested}


# -- Where the cycle runs ------------------------------------------------------
#
# By default in the web process, as tasks on its event loop. With
# BANKAI_ANIME_WORKER=external the web process leaves it to `bankai anime
# worker`, a process of its own: a cycle is CPU-heavy for minutes at a time,
# and on the web's loop every page waited for it -- 0.8 s for /api/health at
# its worst. The web side then asks for cycles through a request file and
# reads what the worker is doing from its heartbeat file.

_IS_WORKER = False
_WORKER_CYCLE: asyncio.Task | None = None
_HEARTBEAT_SECONDS = 10.0
# A worker not heard from in this long is taken to be down.
_HEARTBEAT_STALE_SECONDS = 180.0


# What the cycle is doing right now, for the dashboard: a phase, and while it
# goes through releases, how far it is and which one it is on.
_ACTIVITY: dict[str, Any] = {}
# When the next scheduled cycle starts (wall clock), wherever cycles run.
_NEXT_CYCLE_AT: float | None = None
_LAST_ACTIVITY_BEAT = 0.0


def _set_activity(phase: str | None, **detail: Any) -> None:
    global _LAST_ACTIVITY_BEAT
    if phase is None:
        _ACTIVITY.clear()
    else:
        started = _ACTIVITY.get("cycle_started_at") or time.time()
        _ACTIVITY.clear()
        _ACTIVITY.update({"phase": phase, "cycle_started_at": started, **detail})
    # The web process learns of it from the heartbeat; not more than every 2 s.
    if _IS_WORKER and time.monotonic() - _LAST_ACTIVITY_BEAT >= 2.0:
        _LAST_ACTIVITY_BEAT = time.monotonic()
        _write_heartbeat()


def _write_heartbeat(*, stopped: bool = False) -> None:
    with suppress(OSError):
        _write_json(
            _heartbeat_path(),
            {
                "pid": os.getpid(),
                "running": False if stopped else _CYCLE_LOCK.locked(),
                "activity": {} if stopped else dict(_ACTIVITY),
                "next_cycle_at": None if stopped else _NEXT_CYCLE_AT,
                "updated_at": 0 if stopped else time.time(),
                **({"stopped": True} if stopped else {}),
            },
        )


def automation_activity() -> dict[str, Any]:
    """What the automation is doing, wherever it runs; for the dashboard."""
    if _delegated():
        beat = _worker_heartbeat()
        if beat is None:
            return {"mode": "worker", "alive": False, "running": False, "activity": {}}
        return {
            "mode": "worker",
            "alive": True,
            "running": bool(beat.get("running")),
            "activity": beat.get("activity") or {},
            "next_cycle_at": beat.get("next_cycle_at"),
        }
    return {
        "mode": "inline",
        "alive": True,
        "running": _CYCLE_LOCK.locked(),
        "activity": dict(_ACTIVITY),
        "next_cycle_at": _NEXT_CYCLE_AT,
    }


def _delegated() -> bool:
    """Does a separate worker process run the cycle, rather than this one?"""
    if _IS_WORKER:
        return False
    return os.environ.get("BANKAI_ANIME_WORKER", "").strip().casefold() == "external"


def _cycle_request_path() -> Path:
    return _state_path().with_name("erai_cycle_request.json")


def _heartbeat_path() -> Path:
    return _state_path().with_name("erai_worker.json")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _request_cycle(*, retries_only: bool) -> None:
    """Ask the worker for a cycle. A full one wins over a retries-only one."""
    with _STATE_LOCK:
        pending = _read_json(_cycle_request_path())
        if pending is not None:
            retries_only = retries_only and bool(pending.get("retries_only", True))
        _write_json(
            _cycle_request_path(), {"retries_only": retries_only, "requested_at": time.time()}
        )


def _take_cycle_request() -> dict[str, Any] | None:
    with _STATE_LOCK:
        request = _read_json(_cycle_request_path())
        if request is not None:
            with suppress(OSError):
                _cycle_request_path().unlink()
        return request


def _worker_heartbeat() -> dict[str, Any] | None:
    """The worker's last report, or None when it has not reported lately."""
    beat = _read_json(_heartbeat_path())
    if beat is None:
        return None
    if time.time() - float(beat.get("updated_at") or 0) > _HEARTBEAT_STALE_SECONDS:
        return None
    return beat


def _cycle_running() -> bool:
    if _delegated():
        return bool((_worker_heartbeat() or {}).get("running"))
    return _CYCLE_LOCK.locked()


def _start_retry_cycle() -> None:
    global _RETRY_TASK
    if _delegated():
        _request_cycle(retries_only=True)
    elif _RETRY_TASK is None or _RETRY_TASK.done():
        _RETRY_TASK = asyncio.create_task(run_cycle(prefill=True, retries_only=True))


def trigger_cycle() -> dict:
    """Start a potentially long backlog check without keeping an HTTP request open."""
    global _MANUAL_TASK
    if _delegated():
        _request_cycle(retries_only=False)
    elif not _CYCLE_LOCK.locked() and (_MANUAL_TASK is None or _MANUAL_TASK.done()):
        _MANUAL_TASK = asyncio.create_task(run_cycle(prefill=True))
    return status(running=True)


async def scheduler() -> None:
    global _STOP, _NEXT_CYCLE_AT
    _STOP = asyncio.Event()
    if _delegated():
        log.info("Anime automation runs in its own worker process")
        return
    while not _STOP.is_set():
        try:
            await run_cycle(prefill=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Erai scheduler failed: %s", exc)
        timeout = max(60, get_settings().anime.poll_interval_seconds)
        _NEXT_CYCLE_AT = time.time() + timeout
        with suppress(TimeoutError):
            await asyncio.wait_for(_STOP.wait(), timeout=timeout)


async def worker(*, poll_seconds: float = 2.0) -> None:
    """Run the automation cycle as a process of its own: `bankai anime worker`.

    Scheduled cycles every poll interval, plus whatever the web process asks
    for through the request file, checked every ``poll_seconds``. Settings are
    re-read when the config file changes, since the web process is where they
    are edited.
    """
    global _IS_WORKER, _STOP, _WORKER_CYCLE, _NEXT_CYCLE_AT
    from bankai.config import active_config_path, reset_settings_cache

    _IS_WORKER = True
    _STOP = asyncio.Event()

    def beat() -> None:
        _write_heartbeat()

    async def heartbeat() -> None:
        while not _STOP.is_set():
            beat()
            with suppress(TimeoutError):
                await asyncio.wait_for(_STOP.wait(), timeout=_HEARTBEAT_SECONDS)

    def config_stamp() -> float:
        try:
            return active_config_path().stat().st_mtime
        except OSError:
            return 0.0

    stamp = config_stamp()
    pulse = asyncio.create_task(heartbeat())
    next_scheduled = 0.0  # a first cycle straight away, as the web process did
    log.info("Anime automation worker started (pid %d)", os.getpid())
    try:
        while not _STOP.is_set():
            if (current := config_stamp()) != stamp:
                stamp = current
                reset_settings_cache()
                log.info("Settings changed; reloaded")
            request = _take_cycle_request()
            scheduled = time.monotonic() >= next_scheduled
            if request is not None or scheduled:
                retries_only = (
                    not scheduled and request is not None and bool(request.get("retries_only"))
                )
                beat_task = asyncio.get_running_loop().call_later(0.5, beat)
                _WORKER_CYCLE = asyncio.create_task(
                    run_cycle(prefill=True, retries_only=retries_only)
                )
                try:
                    await _WORKER_CYCLE
                except asyncio.CancelledError:
                    if _STOP.is_set():
                        break  # stop_worker cancelled it
                    raise
                except Exception as exc:
                    log.warning("Erai worker cycle failed: %s", exc)
                finally:
                    beat_task.cancel()
                    beat()
                if scheduled:
                    interval = max(60, get_settings().anime.poll_interval_seconds)
                    next_scheduled = time.monotonic() + interval
                    _NEXT_CYCLE_AT = time.time() + interval
                    beat()
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(_STOP.wait(), timeout=poll_seconds)
    finally:
        _STOP.set()
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse
        _write_heartbeat(stopped=True)
        log.info("Anime automation worker stopped")


def stop_worker() -> None:
    """Stop the worker, cancelling a cycle in progress rather than waiting it out."""
    _STOP.set()
    if _WORKER_CYCLE is not None and not _WORKER_CYCLE.done():
        _WORKER_CYCLE.cancel()


async def shutdown() -> None:
    _STOP.set()
    for task in (_MANUAL_TASK, _RETRY_TASK):
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


__all__ = [
    "free_space_gib",
    "has_explicit_german_subtitles",
    "parse_listing",
    "run_cycle",
    "scheduler",
    "shutdown",
    "status",
]
