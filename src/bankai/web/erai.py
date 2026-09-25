"""Autonomous, safety-first Erai-raws anime ingestion.

Fresh releases come from the uploader RSS feed. Historical backfill completes
2160p and 1080p HEVC catalogue passes before allowing 720p HEVC for logical episodes
that never appeared in either high-quality pass. Nothing is queued until the release is
HEVC, the Nyaa detail page explicitly lists German subtitles, and TVDB resolves without
ambiguity.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import shutil
import threading
import time
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import asdict, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from selectolax.parser import HTMLParser

from bankai.cli import bgjobs
from bankai.config import get_settings
from bankai.logging import get_logger
from bankai.metadata import anime_mapping
from bankai.processor.anime import EpisodeIdentity
from bankai.web import anime as anime_mod
from bankai.web import updates

log = get_logger(__name__)
_NYAA_BASE = "https://nyaa.si"
_RSS_URL = f"{_NYAA_BASE}/?page=rss&u=Erai-raws&c=1_2"
_STATE_LOCK = threading.RLock()
_CYCLE_LOCK = asyncio.Lock()
_STOP = asyncio.Event()
_GIB = 1024**3
_ROSTERS: ContextVar[dict | None] = ContextVar("erai_rosters", default=None)
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
        tmp = path.with_suffix(".tmp")
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
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _series_policy(release_title: str) -> dict[str, Any] | None:
    return _load_policies().get(_mapping_key(release_title))


def _policy_tvdb_ids(policies: dict[str, Any] | None = None) -> set[str]:
    """TVDB ids of every blacklisted series."""
    rows = policies if policies is not None else _load_policies()
    return {
        str(row["tvdb_id"])
        for row in rows.values()
        if row.get("mode") == "blacklisted" and row.get("tvdb_id")
    }


def _release_tvdb_id(release: dict[str, Any], mappings: dict[str, Any]) -> str | None:
    """Best known TVDB id for a release, without going back to the network.

    A release resolved by discovery carries its identity in the canonical key.
    One held before resolution does not, but the series it belongs to has
    usually been resolved through some other release, so the saved mapping for
    its title covers it.
    """
    canonical = str(release.get("canonical") or "")
    head = canonical.split("|")[0]
    if head:
        return head
    saved = mappings.get(_mapping_key(str(release.get("title") or "")))
    tvdb_id = (saved or {}).get("tvdb_id")
    return str(tvdb_id) if tvdb_id else None


def _is_blacklisted_release(
    release: dict[str, Any],
    *,
    policies: dict[str, Any],
    mappings: dict[str, Any],
    blacklisted_ids: set[str] | None = None,
) -> bool:
    """Does this release belong to a series the user has blacklisted?

    Matching on the title key alone missed whole seasons: the key keeps the
    "2nd Season" qualifier, so blacklisting one season left the other sitting
    in review. The TVDB id is the same for every season, part and title
    variant, so it is the identity that actually matches intent.
    """
    key = _mapping_key(str(release.get("title") or ""))
    policy = policies.get(key)
    if policy and policy.get("mode") == "blacklisted":
        return True
    ids = _policy_tvdb_ids(policies) if blacklisted_ids is None else blacklisted_ids
    if not ids:
        return False
    tvdb_id = _release_tvdb_id(release, mappings)
    return bool(tvdb_id and tvdb_id in ids)


async def _resolve_series_tvdb_id(source_title: str, key: str) -> str | None:
    """Identify the series behind a held release, for the policy record."""
    saved = (_load_mappings().get(key) or {}).get("tvdb_id")
    if saved:
        return str(saved)
    try:
        from bankai.web.anime_library import show_metadata

        metadata = await show_metadata(source_title, None)
    except Exception as exc:
        log.warning("Could not identify %s for blacklisting: %s", source_title, exc)
        return None
    tvdb_id = (metadata or {}).get("tvdb_id")
    return str(tvdb_id) if tvdb_id else None


def review_items(state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Group held releases into one card per anime, every season of it together."""
    state = _load_state() if state is None else state
    groups: dict[str, dict[str, Any]] = {}
    policies = _load_policies()
    mappings = _load_mappings()
    blacklisted_ids = _policy_tvdb_ids(policies)
    recent = {item.get("info_hash"): item for item in state.get("held", [])}
    for info_hash, release in state.get("releases", {}).items():
        if release.get("status") != "held" or not release.get("title"):
            continue
        if _is_blacklisted_release(
            release, policies=policies, mappings=mappings, blacklisted_ids=blacklisted_ids
        ):
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
        key = _show_key(item["title"])
        source_title = anime_mod.clean_release_title(item["title"])
        row = groups.setdefault(
            key,
            {
                **item,
                "release_title": item["title"],
                "key": key,
                "source_title": source_title,
                "release_count": 0,
                "reasons": [],
                "seasons": [],
                "keys": [],
            },
        )
        member = _mapping_key(item["title"])
        if member not in row["keys"]:
            row["keys"].append(member)
        # The plainest name of the show names the card: "Oshi no Ko" rather
        # than "Oshi no Ko 2nd Season" when both are held.
        if len(source_title) < len(row["source_title"]):
            row["source_title"] = source_title
        row["release_count"] += 1
        if reason and reason not in row["reasons"]:
            row["reasons"].append(reason)
        season = anime_mod.release_episode_info(item["title"])[0]
        if season is not None and season not in row["seasons"]:
            row["seasons"].append(season)
    for row in groups.values():
        row["seasons"].sort()
    return sorted(groups.values(), key=lambda row: row["source_title"].casefold())


def review_releases(key: str) -> list[dict[str, Any]]:
    """Every held release behind one review card.

    A group can hold dozens of releases that do not agree with each other --
    One Piece had sixty-three, some listing German and some not -- and the card
    only ever showed the first. Judging them needs all of them.
    """
    state = _load_state()
    recent = {item.get("info_hash"): item for item in state.get("held", [])}
    rows: list[dict[str, Any]] = []
    for info_hash, release in state.get("releases", {}).items():
        title = str(release.get("title") or "")
        if release.get("status") != "held" or not title:
            continue
        # The card key is the show; a per-season key still answers for
        # anything holding one from before the merge.
        if key not in {_show_key(title), _mapping_key(title)}:
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
                "season": anime_mod.release_episode_info(title)[0],
                "episode": anime_mod.release_episode_info(title)[1],
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


def blacklist_items() -> list[dict[str, Any]]:
    """One card per blacklisted anime, however many seasons it was discarded for.

    Discarding a show writes a decision for each of its seasons, so listing the
    decisions themselves would put the same show here once per season.
    """
    groups: dict[str, dict[str, Any]] = {}
    for key, value in _load_policies().items():
        if value.get("mode") != "blacklisted":
            continue
        show = _policy_show_key(key, value)
        row = groups.setdefault(show, {**value, "key": show, "keys": []})
        row["keys"].append(key)
        if len(str(value.get("source_title") or "")) < len(str(row.get("source_title") or "")):
            row["source_title"] = value.get("source_title")
        row["tvdb_id"] = row.get("tvdb_id") or value.get("tvdb_id")
    return sorted(groups.values(), key=lambda row: str(row.get("source_title", "")).casefold())


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
    """Persist a series decision and schedule the affected releases immediately."""
    global _RETRY_TASK
    with _STATE_LOCK:
        release = (_load_state().get("releases", {}) or {}).get(info_hash)
    if not release or not release.get("title"):
        raise ValueError("Held release was not found")
    key = _mapping_key(release["title"])
    tvdb_id = (
        await _resolve_series_tvdb_id(anime_mod.clean_release_title(release["title"]), key)
        if action == "blacklist"
        else None
    )
    with _STATE_LOCK:
        state = _load_state()
        release = state.get("releases", {}).get(info_hash)
        if not release or not release.get("title"):
            raise ValueError("Held release was not found")
        title = release["title"]
        # A card is a whole show, so a decision is too: every season held
        # under it, each of which carries its own per-season key.
        titles = {key: title}
        for row in state.get("releases", {}).values():
            row_title = str(row.get("title") or "")
            if (
                row.get("status") == "held"
                and row_title
                and _show_key(row_title) == _show_key(title)
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
            for member, member_title in titles.items():
                policies[member] = {
                    "mode": "blacklisted",
                    "source_title": anime_mod.clean_release_title(member_title),
                    # Recorded so every other season, part and title variant of
                    # the same series is covered, including ones not held yet.
                    "tvdb_id": tvdb_id,
                    "updated_at": time.time(),
                }
            _save_policies(policies)
            mappings = _load_mappings()
            blacklisted_ids = _policy_tvdb_ids(policies)
            requests = _load_retry_requests()
            matched: list[str] = []
            for release_hash, row in state.get("releases", {}).items():
                if _is_blacklisted_release(
                    row, policies=policies, mappings=mappings, blacklisted_ids=blacklisted_ids
                ):
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
                    blacklisted_ids=blacklisted_ids,
                )
            ]
            _save_retry_requests(requests)
            _save_state(state)
            blacklisted = len(matched)
        else:
            raise ValueError("Unknown review action")
    if requested and (_RETRY_TASK is None or _RETRY_TASK.done()):
        _RETRY_TASK = asyncio.create_task(run_cycle(prefill=True, retries_only=True))
    return {
        "ok": True,
        "requested": requested,
        "blacklisted": blacklisted,
        "key": key,
        "keys": sorted(titles),
    }


_VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".webm"}


def _series_roots() -> list[Path]:
    """Every directory a blacklisted series may legitimately occupy."""
    settings = get_settings()
    roots = [Path(settings.transfer.anime_shows_dir), Path(settings.output.directory)]
    return [root for root in roots if str(root)]


def _contained_by(path: Path, roots: list[Path]) -> bool:
    """Refuse to delete anything outside the configured library roots.

    The show folder is found by name, so this is the check that keeps a bad
    name -- or a symlink out of the tree -- from reaching the rest of the disk.
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


def series_files(english_title: str) -> list[Path]:
    """Folders already holding episodes of a series, library and staging."""
    from bankai.backend.transfer import _existing_show_folder

    roots = _series_roots()
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        folder = _existing_show_folder(english_title, cache={}, roots=[root])
        if folder is not None and _contained_by(folder, roots) and folder not in found:
            found.append(folder)
    return found


async def purge_series(
    key: str, *, english_title: str, delete_files: bool
) -> dict[str, Any]:
    """Remove a blacklisted series' torrents and, optionally, its episodes.

    Downloading an episode the user has rejected wastes the same scarce disk
    twice, so the torrents go regardless. Deleting what is already published is
    a separate decision and only happens when asked.
    """
    from bankai.torrent.qbittorrent import QBittorrentClient

    with _STATE_LOCK:
        state = _load_state()
        policies = _load_policies()
        mappings = _load_mappings()
        blacklisted_ids = _policy_tvdb_ids(policies)
        hashes = [
            info_hash
            for info_hash, release in state.get("releases", {}).items()
            if _is_blacklisted_release(
                release, policies=policies, mappings=mappings, blacklisted_ids=blacklisted_ids
            )
        ]

    removed_torrents = 0
    if hashes:
        qbit = QBittorrentClient()
        try:
            await qbit.login()
            for info_hash in hashes:
                try:
                    await qbit.remove(info_hash, delete_files=True)
                    removed_torrents += 1
                except Exception as exc:
                    log.warning("Could not remove torrent %s: %s", info_hash[:8], exc)
        except Exception as exc:
            log.warning("Could not reach qBittorrent while purging %s: %s", key, exc)
        finally:
            await qbit.aclose()

    deleted_files = 0
    freed_bytes = 0
    deleted_folders: list[str] = []
    if delete_files and english_title:
        roots = _series_roots()
        for folder in series_files(english_title):
            if not _contained_by(folder, roots):
                continue
            for path in folder.rglob("*"):
                if path.is_file():
                    try:
                        freed_bytes += path.stat().st_size
                        deleted_files += 1
                    except OSError:
                        pass
            try:
                shutil.rmtree(folder)
                deleted_folders.append(str(folder))
            except OSError as exc:
                log.warning("Could not delete %s: %s", folder, exc)
                deleted_files = 0
                freed_bytes = 0
    return {
        "ok": True,
        "key": key,
        "removed_torrents": removed_torrents,
        "deleted_files": deleted_files,
        "deleted_folders": deleted_folders,
        "freed_bytes": freed_bytes,
    }


def remove_blacklist(key: str) -> dict[str, Any]:
    global _RETRY_TASK
    with _STATE_LOCK:
        policies = _load_policies()
        # A blacklist card is a whole show: restore every season of it.
        members = {
            member
            for member, row in policies.items()
            if row.get("mode") == "blacklisted"
            and (member == key or _policy_show_key(member, row) == key)
        }
        if not members:
            raise ValueError("Blacklisted series was not found")
        for member in members:
            policies.pop(member)
        _save_policies(policies)
        state = _load_state()
        for release in state.get("releases", {}).values():
            if (
                release.get("status") == "blacklisted"
                and _mapping_key(release.get("title", "")) in members
            ):
                release["status"] = "held"
                release["reason"] = "Blacklist removed; release is ready for a fresh check"
                release["retry_after"] = 0
                saved_entry = release.get("entry")
                if saved_entry:
                    _hold(state, _entry_from_dict(saved_entry), release["reason"])
        requested = sum(_request_series_retries(state, member) for member in members)
        _save_state(state)
    if requested and (_RETRY_TASK is None or _RETRY_TASK.done()):
        _RETRY_TASK = asyncio.create_task(run_cycle(prefill=True, retries_only=True))
    return {"ok": True, "requested": requested}


def retry_series(release_title: str) -> int:
    global _RETRY_TASK
    with _STATE_LOCK:
        state = _load_state()
        requested = _request_series_retries(state, _mapping_key(release_title))
    if requested and (_RETRY_TASK is None or _RETRY_TASK.done()):
        _RETRY_TASK = asyncio.create_task(run_cycle(prefill=True, retries_only=True))
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
    if candidates:
        anime_mod._TVDB_CACHE.clear()
    for item in candidates:
        anime_mod._DETAIL_CACHE.pop(item.detail_url, None)
    return sorted(candidates, key=_episode_order)


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


def save_mapping(
    release_title: str,
    match: anime_mod.AnimeTVDBMatch,
    *,
    season: int | None = None,
    episode_offset: int = 0,
    clear_episode_mapping: bool = False,
) -> None:
    """Remember an explicit user TVDB selection for future Erai episodes."""

    if match.kind != "show" or match.tvdb_id <= 0:
        return
    with _STATE_LOCK:
        mappings = _load_mappings()
        key = _mapping_key(release_title)
        previous = mappings.get(key, {})
        episode_mapping = (
            {field: previous[field] for field in ("season", "episode_offset") if field in previous}
            if previous.get("tvdb_id") == match.tvdb_id and not clear_episode_mapping
            else {}
        )
        if season is not None:
            episode_mapping = {"season": season, "episode_offset": episode_offset}
        mappings[key] = {**asdict(match), **episode_mapping}
        path = _state_path().with_name("erai_mappings.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(mappings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _needs_consideration(state: dict[str, Any], entry: anime_mod.NyaaEntry) -> bool:
    policy = _series_policy(entry.title)
    if policy and policy.get("mode") == "blacklisted":
        return False
    previous = state["releases"].get(entry.info_hash)
    if previous is None:
        return True
    if previous.get("status") != "held":
        return False
    if entry.info_hash in _load_retry_requests():
        return True
    reason = str(previous.get("reason", ""))
    if "TVDB" not in reason and "German subtitles" not in reason:
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


def _save_state(state: dict[str, Any]) -> None:
    with _STATE_LOCK:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


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
    row["tvdb"] = None
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


async def _resolve(
    entry: anime_mod.NyaaEntry,
    episodes: list | None = None,
) -> tuple[anime_mod.AnimeTVDBMatch | None, Any | None, str | None]:
    query = anime_mod.clean_release_title(entry.title)
    saved = _load_mappings().get(_mapping_key(entry.title))
    if saved:
        match = anime_mod.AnimeTVDBMatch(
            **{
                key: value
                for key, value in saved.items()
                if key in anime_mod.AnimeTVDBMatch.__dataclass_fields__
            }
        )
    else:
        candidates = await anime_mod.tvdb_candidates(query, limit=6)
        scored = sorted(
            ((anime_mod._match_score(query, item), item) for item in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not scored or scored[0][0] < 0.999:
            return None, None, "No confident TVDB match"
        if (
            len(scored) > 1
            and scored[1][0] >= 0.999
            and scored[0][1].tvdb_id != scored[1][1].tvdb_id
        ):
            return None, None, "TVDB match is ambiguous"
        match = scored[0][1]
    if match.kind != "show":
        return None, None, "Automatic ingestion currently requires a TVDB show match"
    from bankai.processor.anime import _tvdb_episode_map

    if episodes is None:
        cache = _ROSTERS.get()
        if cache is not None and match.tvdb_id in cache:
            episodes = cache[match.tvdb_id]
        else:
            episodes = await _tvdb_episode_map(match.tvdb_id)
            if cache is not None:
                cache[match.tvdb_id] = episodes
    season, episode = anime_mod.release_episode_info(entry.title)
    if episode is None:
        return None, None, "TVDB episode number was not found in the release title"
    regular = [item for item in episodes if item.season > 0]
    seasons = {item.season for item in regular}
    target = None
    _, part = anime_mod.release_part(entry.title)
    structured = anime_mod.deconstruct_release(entry.title)
    scene_title = structured[0] if structured else query
    if saved and saved.get("season") is not None:
        target_number = episode + int(saved.get("episode_offset", 0))
        target = next(
            (
                item
                for item in regular
                if (item.season, item.episode) == (int(saved["season"]), target_number)
            ),
            None,
        )
    elif part and part > 1:
        mapped, known = await anime_mapping.mapped_episode(
            scene_title, episode, match.tvdb_id, episodes
        )
        if known:
            target = next((item for item in regular if (item.season, item.episode) == mapped), None)
        elif len(seasons) == 1:
            # An explicit Part N plus TVDB's split-cour air-date boundary.
            from datetime import date

            ordered = sorted(regular, key=lambda item: item.episode)
            starts = [ordered[0].episode] if ordered else []
            for before, after in pairwise(ordered):
                if before.aired and after.aired:
                    try:
                        gap = (
                            date.fromisoformat(after.aired[:10])
                            - date.fromisoformat(before.aired[:10])
                        ).days
                    except ValueError:
                        continue
                    if gap >= 28 and after.episode == before.episode + 1:
                        starts.append(after.episode)
            if len(starts) >= part:
                target_number = starts[part - 1] + episode - 1
                target = next((item for item in regular if item.episode == target_number), None)
            elif regular and len(regular) % part == 0:
                # TVDB sometimes schedules a split cour without an air-date gap.
                # A terminal "Part N" is continuation metadata, never title text.
                segment = len(regular) // part
                target_number = (part - 1) * segment + episode
                target = next((item for item in regular if item.episode == target_number), None)
        if target is None:
            return (
                None,
                None,
                "TVDB continuation boundary is not verified; select a season and episode offset manually",
            )
    elif season is not None:
        target = next(
            (item for item in regular if (item.season, item.episode) == (season, episode)), None
        )
    elif len(seasons) == 1:
        target = next((item for item in regular if item.episode == episode), None)
    else:
        mapped, known_part = await anime_mapping.mapped_episode(
            query, episode, match.tvdb_id, episodes
        )
        if known_part:
            if mapped is None:
                return None, None, "TVDB/AniDB/TheXEM part mapping does not verify this episode"
            target = next((item for item in regular if (item.season, item.episode) == mapped), None)
        elif anime_mapping.normalise(query) in {
            anime_mapping.normalise(match.english_title),
            anime_mapping.normalise(match.japanese_title or ""),
        }:
            absolute = [item for item in regular if item.absolute_number == episode]
            if len(absolute) == 1:
                target = absolute[0]
    if target is None:
        return (
            None,
            None,
            "TVDB ordering needs an AniDB/TheXEM episode mapping; no season was guessed",
        )
    identity = EpisodeIdentity(target.season, target.episode, target.name)
    return match, identity, None


def _anime_args(
    entry: anime_mod.NyaaEntry, match: anime_mod.AnimeTVDBMatch, identity: Any
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
        "--tvdb-id",
        str(match.tvdb_id),
        "--english-title",
        match.english_title,
        "--season",
        str(identity.season),
        "--episode",
        str(identity.episode),
        "--require-german-subtitles",
        "--cleanup-torrent",
    ]
    if match.year is not None:
        args.extend(["--year", str(match.year)])
    return args


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
        if title_lists_german_subtitles(other.title):
            return other
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


def _episode_on_disk(english_title: str, season: int, episode: int) -> bool:
    """Is this episode already in the library?

    Cached for the cycle: a season pack asks the same question for every
    episode it contains, and the answer is one directory walk over a slow
    library disk.
    """
    from bankai.backend.transfer import _existing_show_folder
    from bankai.torrent.matcher import parse_se

    if not english_title:
        return False
    cache = _DISK_INDEX.get()
    if cache is None:
        cache = {}
        _DISK_INDEX.set(cache)
    have = cache.get(english_title)
    if have is None:
        have = set()
        folder = _existing_show_folder(
            english_title,
            cache={},
            roots=[Path(get_settings().transfer.anime_shows_dir)],
        )
        if folder is not None:
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.casefold() in _VIDEO_SUFFIXES:
                    identity = parse_se(path.name)
                    if identity:
                        have.add(identity)
        cache[english_title] = have
    return (season, episode) in have


async def _consider(
    state: dict[str, Any],
    entry: anime_mod.NyaaEntry,
    client: httpx.AsyncClient,
) -> bool:
    if not _needs_consideration(state, entry):
        return False
    if not _is_hevc(entry):
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
    match, identity, error = await _resolve(entry)
    if error or match is None or identity is None:
        _hold(state, entry, error or "TVDB resolution failed")
        return False
    if str(match.tvdb_id) in _policy_tvdb_ids():
        # The title key check above only sees the words in this release's name;
        # the series behind it is only known once TVDB has resolved it.
        state["releases"][entry.info_hash] = {
            "status": "blacklisted",
            "title": entry.title,
            "reason": "Series blacklisted by user",
        }
        return False
    state["series"][str(match.tvdb_id)] = asdict(match)
    canonical = f"{match.tvdb_id}|{identity.season}|{identity.episode}"
    previous = state["canonical"].get(canonical)
    resolution = _resolution(entry)
    if previous and int(previous.get("resolution", 0)) >= resolution:
        state["releases"][entry.info_hash] = {
            "status": "duplicate",
            "title": entry.title,
            "canonical": canonical,
        }
        return False
    if _episode_on_disk(match.english_title, identity.season, identity.episode):
        state["releases"][entry.info_hash] = {
            "status": "existing",
            "title": entry.title,
            "canonical": canonical,
        }
        return False

    try:
        description, magnet, uploader = await anime_mod._detail_url(client, entry.detail_url)
    except Exception as exc:
        log.warning("Erai detail lookup failed for %s: %s", entry.id, exc)
        return False
    if (uploader or "").casefold() != "erai-raws":
        _hold(state, entry, "Detail-page uploader is not Erai-raws")
        return False
    policy = _series_policy(entry.title)
    if (
        not has_explicit_german_subtitles(description)
        and not title_lists_german_subtitles(entry.title)
        and not (policy and policy.get("mode") == "german_allowed")
    ):
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
        _hold(state, entry, "Nyaa description does not explicitly list German subtitles")
        return False
    if magnet:
        entry = replace(entry, magnet_uri=magnet, description=description)
    admission = _ADMISSION.get()
    title = f"{match.english_title} S{identity.season:02d}E{identity.episode:02d}"
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
        # Kept so publishing never has to resolve against TVDB a second time.
        "args": _anime_args(entry, match, identity),
        "updated_at": time.time(),
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
    try:
        description, _magnet, uploader = await anime_mod._detail_url(client, entry.detail_url)
    except Exception as exc:
        log.warning("Could not read %s while upgrading: %s", entry.id, exc)
        return False
    if (uploader or "").casefold() != "erai-raws":
        return False
    return has_explicit_german_subtitles(description)


def _german_dubbed_episodes(english_title: str) -> set[tuple[int, int]]:
    """Episodes of a show whose file carries a German dub."""
    from bankai.backend.transfer import _existing_show_folder
    from bankai.torrent.matcher import parse_se
    from bankai.web.anime_library import german_dubbed_episodes

    root = Path(get_settings().transfer.anime_shows_dir)
    folder = _existing_show_folder(english_title, cache={}, roots=[root])
    if folder is None:
        return set()
    files = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in _VIDEO_SUFFIXES:
            continue
        identity = parse_se(path.name)
        if identity:
            files.append(
                {"path": str(path), "season_number": identity[0], "episode": identity[1]}
            )
    return german_dubbed_episodes(files)


async def upgrade_show_to_hevc(tvdb_id: int, english_title: str) -> dict[str, Any]:
    """Queue HEVC replacements for a show's already-published AVC episodes.

    Unlike the backlog upgrade, these episodes are on disk and watchable. The
    replacement is downloaded first and published over the original only once
    it succeeds, so a failed upgrade costs bandwidth and nothing else. An
    episode with no HEVC release available is left exactly as it is.
    """
    settings = get_settings()
    queued = 0
    skipped = 0
    already = 0
    dubbed = 0
    # An episode carrying a German dub is irreplaceable: Erai-raws ships
    # Japanese audio only, so upgrading one would trade a track that cannot be
    # got back for a smaller file.
    protected = _german_dubbed_episodes(english_title)
    from bankai.torrent.qbittorrent import QBittorrentClient

    qbit = QBittorrentClient()
    headers = {"User-Agent": settings.scraper.user_agent}
    try:
        await qbit.login()
    except Exception as exc:
        await qbit.aclose()
        raise RuntimeError(f"qBittorrent is unavailable: {exc}") from exc

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=30, follow_redirects=True
        ) as client:
            with _STATE_LOCK:
                state = _load_state()
                targets = []
                for canonical, row in state.get("canonical", {}).items():
                    parts = str(canonical).split("|")
                    if len(parts) != 3 or parts[0] != str(tvdb_id):
                        continue
                    release = state.get("releases", {}).get(str(row.get("info_hash") or ""))
                    if not release or release.get("status") != "done":
                        continue
                    if _is_hevc_title(str(release.get("title") or "")):
                        already += 1
                        continue
                    targets.append((canonical, parts[1], parts[2], release))

            for canonical, season, episode, release in targets:
                if (int(season), int(episode)) in protected:
                    dubbed += 1
                    continue
                twin = _hevc_twin(state, release)
                if twin is None or not await _carries_german(twin, client):
                    skipped += 1
                    continue
                args = [
                    "anime-download",
                    "--release-title",
                    twin.title,
                    "--torrent-url",
                    twin.download_url,
                    "--detail-url",
                    twin.detail_url,
                    "--magnet-uri",
                    twin.magnet_uri,
                    "--info-hash",
                    twin.info_hash,
                    "--kind",
                    "show",
                    "--tvdb-id",
                    str(tvdb_id),
                    "--english-title",
                    english_title,
                    "--season",
                    str(int(season)),
                    "--episode",
                    str(int(episode)),
                    "--require-german-subtitles",
                    # Publishing has to overwrite the episode being replaced.
                    "--replace-existing",
                ]
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
                    skipped += 1
                    continue
                with _STATE_LOCK:
                    state = _load_state()
                    state["releases"][twin.info_hash] = {
                        "status": "queued",
                        "title": twin.title,
                        "display_title": f"{english_title} S{int(season):02d}E{int(episode):02d}",
                        "canonical": canonical,
                        "resolution": _resolution(twin),
                        "size_bytes": twin.size_bytes,
                        "args": args,
                        "replaces": release.get("title"),
                        "updated_at": time.time(),
                    }
                    # The upgrade takes over the episode's slot; the original
                    # stays on disk and playable until the replacement lands.
                    state["canonical"][canonical] = {
                        "info_hash": twin.info_hash,
                        "resolution": _resolution(twin),
                    }
                    _save_state(state)
                queued += 1
                await asyncio.sleep(settings.anime.backfill_request_delay_seconds)
    finally:
        await qbit.aclose()
    return {
        "ok": True,
        "queued": queued,
        "no_replacement": skipped,
        "already_hevc": already,
        "german_dub_kept": dubbed,
    }


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


async def _ordered_candidates(
    state: dict[str, Any],
    fresh: dict[str, anime_mod.NyaaEntry],
    client: httpx.AsyncClient,
) -> list[anime_mod.NyaaEntry]:
    # Index each RSS show's full high-quality release set before starting with
    # its latest episode. Persist work when a long series needs query shards.
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
        parts = await anime_mapping.anidb_parts(anime_mod.clean_release_title(entry.title))
        ids = {part.tvdb_id for part in parts}
        parent = next(iter(ids)) if len(ids) == 1 else None
        key = f"tvdb:{parent}" if parent else _mapping_key(entry.title)
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
            titles = await anime_mapping.related_titles(parent) if parent else []
            titles = titles or [anime_mod.clean_release_title(entry.title)]
            state["series_catalogs"][key] = {
                **_default_state()["backfill"],
                "title_query": titles[0],
                "title_queries": titles,
                "title_query_index": 0,
                "parent_tvdb_id": parent,
                "high_only": True,
            }
        index = state["series_catalogs"][key]
        source_title = anime_mod.clean_release_title(entry.title)
        queries = []
        for title in index["title_queries"]:
            # Nyaa's quoted phrases distinguish punctuation even when AniDB's
            # title identity does not. Prefer the observed Erai spelling.
            observed = (
                source_title
                if anime_mapping.normalise(title) == anime_mapping.normalise(source_title)
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
                parts = await anime_mapping.anidb_parts(
                    (
                        anime_mod.deconstruct_release(release.title)
                        or (anime_mod.clean_release_title(release.title), 0)
                    )[0]
                )
                if {part.tvdb_id for part in parts} == {parent}:
                    candidates.append(release)
            elif anime_mod.clean_release_title(release.title) == source_title:
                candidates.append(release)
    cutoff = time.time() - get_settings().anime.settle_minutes * 60
    by_hash = {
        entry.info_hash: entry
        for entry in candidates
        if _is_hevc(entry) and _published_timestamp(entry) <= cutoff
    }
    # Named AniDB parts share one TVDB parent. Order by published season and
    # offset rather than alphabetically sorting "Kashin" before "Ketsubetsu".
    part_keys = {}
    for title in {_mapping_key(entry.title) for entry in by_hash.values()}:
        sample = next(entry for entry in by_hash.values() if _mapping_key(entry.title) == title)
        parts = await anime_mapping.anidb_parts(anime_mod.clean_release_title(sample.title))
        if len(parts) == 1 and (parts[0].record.get("defaulttvdbseason") or "").isdigit():
            part = parts[0]
            part_keys[title] = (
                part.tvdb_id,
                int(part.record.get("defaulttvdbseason")),
                int(part.record.get("episodeoffset") or 0),
            )

    def order(entry: anime_mod.NyaaEntry) -> tuple:
        key, season, episode, quality, release_id = _episode_order(entry)
        if key in part_keys:
            parent, mapped_season, offset = part_keys[key]
            return (f"tvdb:{parent:010d}", mapped_season, episode + offset, quality, release_id)
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
        _prune_holds(state)
        state["last_poll"] = time.time()
        state["last_enqueued"] = 0
        if not policy.enabled or updates.maintenance_active():
            _save_state(state)
            return status(state=state, running=False)
        if not settings.metadata.tvdb_enabled or not settings.metadata.tvdb_api_key:
            state["last_error"] = "TVDB is not configured"
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
        qbit = None
        roster_token = _ROSTERS.set({})
        admission_token = _ADMISSION.set(None)
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
                retries = await _retry_candidates(state, client)
                rss = [] if retries_only else await _fetch_rss(client)
                if not retries_only:
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
                normal = [] if retries_only else await _ordered_candidates(state, fresh, client)
                candidates = list({item.info_hash: item for item in [*retries, *normal]}.values())
                inspected = 0
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
            _ROSTERS.reset(roster_token)
            _ADMISSION.reset(admission_token)
            if qbit is not None:
                await qbit.aclose()
        _save_state(state)
        return status(state=state, running=False)


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
    downloads sat untouched. The canonical key carries the TVDB identity and
    the series is already resolved, so the arguments can be rebuilt.
    """
    parts = str(release.get("canonical") or "").split("|")
    if len(parts) != 3 or not all(parts):
        return None
    tvdb_id, season, episode = parts
    series = (state.get("series") or {}).get(tvdb_id) or {}
    english_title = str(series.get("english_title") or "")
    if not english_title:
        return None
    args = [
        "anime-download",
        "--release-title",
        str(release.get("title") or english_title),
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
        "--tvdb-id",
        tvdb_id,
        "--english-title",
        english_title,
        "--season",
        season,
        "--episode",
        episode,
        "--require-german-subtitles",
    ]
    if series.get("year"):
        args.extend(["--year", str(series["year"])])
    return args


def _episode_already_published(
    state: dict[str, Any],
    release: dict[str, Any],
    cache: dict[str, set[tuple[int, int]]],
) -> bool:
    """Is this episode already sitting in the library?

    The walk is cached per series for the pass; doing it per release meant one
    directory scan each over a spinning library disk.
    """
    parts = str(release.get("canonical") or "").split("|")
    if len(parts) != 3 or not all(parts):
        return False
    tvdb_id, season, episode = parts
    english_title = str(((state.get("series") or {}).get(tvdb_id) or {}).get("english_title") or "")
    if not english_title:
        return False
    try:
        wanted = (int(season), int(episode))
    except ValueError:
        return False
    if english_title not in cache:
        from bankai.backend.transfer import _existing_show_folder
        from bankai.torrent.matcher import parse_se

        found: set[tuple[int, int]] = set()
        folder = _existing_show_folder(
            english_title,
            cache={},
            roots=[Path(get_settings().transfer.anime_shows_dir)],
        )
        if folder is not None:
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.casefold() in _VIDEO_SUFFIXES:
                    identity = parse_se(path.name)
                    if identity:
                        found.add(identity)
        cache[english_title] = found
    return wanted in cache[english_title]


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
    """Advance every tracked release one step. Safe to call on a timer."""
    from bankai.cli import bgjobs
    from bankai.torrent.qbittorrent import QBittorrentClient

    settings = get_settings()
    if not settings.anime.enabled or updates.maintenance_active():
        return {}

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
                elif status == "held" and str(release.get("reason") or "").startswith(
                    _STALE_READD_REASON
                ):
                    # Held by an earlier bug that refused to re-add a release
                    # unless its record stored the magnet. Nothing was ever
                    # wrong with these, so let them back into the pipeline.
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
                    release["reason"] = f"qBittorrent reports {torrent.state}"
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
                    args=list(args),
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
                "tvdb_id": _args_value(release.get("args"), "--tvdb-id"),
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
    elif not get_settings().metadata.tvdb_enabled or not get_settings().metadata.tvdb_api_key:
        pause_reason = "TVDB is not configured"
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
        "running": _CYCLE_LOCK.locked() if running is None else running,
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


def retry_held() -> dict:
    """Persist one fresh attempt for every held release and return immediately."""
    global _RETRY_TASK
    with _STATE_LOCK:
        state = _load_state()
        catalog = _catalog_entries(state)
        held = {item["info_hash"]: item for item in state.get("held", [])}
        requests = _load_retry_requests()
        requested = 0
        for info_hash, release in state["releases"].items():
            if release.get("status") != "held":
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
    if requested and (_RETRY_TASK is None or _RETRY_TASK.done()):
        _RETRY_TASK = asyncio.create_task(run_cycle(prefill=True, retries_only=True))
    return {**status(), "requested": requested}


def trigger_cycle() -> dict:
    """Start a potentially long backlog check without keeping an HTTP request open."""
    global _MANUAL_TASK
    if not _CYCLE_LOCK.locked() and (_MANUAL_TASK is None or _MANUAL_TASK.done()):
        _MANUAL_TASK = asyncio.create_task(run_cycle(prefill=True))
    return status(running=True)


async def scheduler() -> None:
    global _STOP
    _STOP = asyncio.Event()
    while not _STOP.is_set():
        try:
            await run_cycle(prefill=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Erai scheduler failed: %s", exc)
        timeout = max(60, get_settings().anime.poll_interval_seconds)
        with suppress(TimeoutError):
            await asyncio.wait_for(_STOP.wait(), timeout=timeout)


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
    "save_mapping",
    "scheduler",
    "shutdown",
    "status",
]
