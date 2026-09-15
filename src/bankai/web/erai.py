"""Autonomous, safety-first Erai-raws anime ingestion.

Fresh releases come from the uploader RSS feed. Historical backfill completes
2160p and 1080p catalogue passes before allowing 720p for logical episodes
that never appeared in either high-quality pass. Nothing is queued until the Nyaa detail
page explicitly lists German subtitles and TVDB resolves without ambiguity.
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
from bankai.processor.anime import episode_identity
from bankai.web import anime as anime_mod

log = get_logger(__name__)
_NYAA_BASE = "https://nyaa.si"
_RSS_URL = f"{_NYAA_BASE}/?page=rss&u=Erai-raws&c=1_2"
_STATE_LOCK = threading.RLock()
_CYCLE_LOCK = asyncio.Lock()
_STOP = asyncio.Event()
_GIB = 1024**3
_GERMAN_LINE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:german|deutsch)(?:\s*\([^\n)]*\))?\s*(?:[|:]|$)"
)
_CR_GERMAN = re.compile(r"(?i)\bCR[_ -]?German\b")


def _state_path() -> Path:
    return bgjobs.jobs_root().parent / "erai_automation.json"


def _mapping_key(release_title: str) -> str:
    query = anime_mod.clean_release_title(release_title)
    return " ".join(re.findall(r"\w+", query.casefold(), re.UNICODE))


def _load_mappings() -> dict[str, dict[str, Any]]:
    with _STATE_LOCK:
        path = _state_path().with_name("erai_mappings.json")
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}


def save_mapping(release_title: str, match: anime_mod.AnimeTVDBMatch) -> None:
    """Remember an explicit user TVDB selection for future Erai episodes."""

    if match.kind != "show" or match.tvdb_id <= 0:
        return
    with _STATE_LOCK:
        mappings = _load_mappings()
        mappings[_mapping_key(release_title)] = asdict(match)
        path = _state_path().with_name("erai_mappings.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(mappings, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _needs_consideration(state: dict[str, Any], entry: anime_mod.NyaaEntry) -> bool:
    previous = state["releases"].get(entry.info_hash)
    if previous is None:
        return True
    if previous.get("status") != "held":
        return False
    reason = str(previous.get("reason", ""))
    if "TVDB" not in reason:
        return False
    return _mapping_key(entry.title) in _load_mappings() or time.time() >= float(
        previous.get("retry_after", 0)
    )


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
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
        return state


def _save_state(state: dict[str, Any]) -> None:
    with _STATE_LOCK:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def has_explicit_german_subtitles(description: str) -> bool:
    """Require an explicit German subtitle row, never merely ``MultiSub``."""

    if not description:
        return False
    section = description
    marker = re.search(r"(?i)subtitles?\s+info\s*:?", description)
    if marker:
        section = description[marker.start() : marker.start() + 5000]
    for line in section.splitlines():
        line = line.strip().strip("|").strip().replace("**", "").replace("__", "")
        if re.search(r"(?i)\b(?:not|no|none|unavailable|missing|removed)\b", line):
            continue
        if _GERMAN_LINE.search(line):
            return True
        if _CR_GERMAN.search(line) and re.search(r"(?i)\b(?:ASS|SSA|SRT|VTT)\b", line):
            return True
    return False


def _resolution(entry: anime_mod.NyaaEntry) -> int:
    match = re.search(r"(?<!\d)(2160|1080|720|480)p\b", entry.quality or entry.title, re.I)
    return int(match.group(1)) if match else 0


def _release_key(entry: anime_mod.NyaaEntry) -> str | None:
    season, episode = anime_mod.release_episode_info(entry.title)
    if episode is None:
        return None
    title = anime_mod.clean_release_title(entry.title)
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


def _hold(state: dict[str, Any], entry: anime_mod.NyaaEntry, reason: str) -> None:
    state["releases"][entry.info_hash] = {
        "status": "held",
        "reason": reason,
        "title": entry.title,
        "retry_after": time.time() + 86400,
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
) -> tuple[anime_mod.AnimeTVDBMatch | None, Any | None, str | None]:
    query = anime_mod.clean_release_title(entry.title)
    saved = _load_mappings().get(_mapping_key(entry.title))
    if saved:
        match = anime_mod.AnimeTVDBMatch(**saved)
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

    episodes = await _tvdb_episode_map(match.tvdb_id)
    season, episode = anime_mod.release_episode_info(entry.title)
    identity = episode_identity(
        entry.title,
        release_title=entry.title,
        tvdb_episodes=episodes,
        season_override=season,
        episode_override=episode,
    )
    if identity is None or not any(
        item.season == identity.season and item.episode == identity.episode for item in episodes
    ):
        return None, None, "Episode could not be verified against TVDB ordering"
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
    ]
    if match.year is not None:
        args.extend(["--year", str(match.year)])
    return args


async def _consider(
    state: dict[str, Any],
    entry: anime_mod.NyaaEntry,
    client: httpx.AsyncClient,
) -> bool:
    if not _needs_consideration(state, entry):
        return False
    if (entry.publisher or "").casefold() != "erai-raws":
        _hold(state, entry, "Uploader is not exactly Erai-raws")
        return False
    if not entry.trusted or entry.remake:
        _hold(state, entry, "Release is not a trusted, original Nyaa upload")
        return False
    try:
        description, magnet, uploader = await anime_mod._detail_url(client, entry.detail_url)
    except Exception as exc:
        log.warning("Erai detail lookup failed for %s: %s", entry.id, exc)
        return False
    if (uploader or "").casefold() != "erai-raws":
        _hold(state, entry, "Detail-page uploader is not Erai-raws")
        return False
    if not has_explicit_german_subtitles(description):
        _hold(state, entry, "Nyaa description does not explicitly list German subtitles")
        return False
    if magnet:
        entry = replace(entry, magnet_uri=magnet, description=description)
    match, identity, error = await _resolve(entry)
    if error or match is None or identity is None:
        _hold(state, entry, error or "TVDB resolution failed")
        return False
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
    from bankai.backend.transfer import _existing_show_folder
    from bankai.torrent.matcher import parse_se

    folder = _existing_show_folder(
        match.english_title,
        cache={},
        roots=[Path(get_settings().transfer.anime_shows_dir)],
    )
    if folder is not None and any(
        parse_se(path.name) == (identity.season, identity.episode)
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".mkv", ".mp4", ".m4v", ".avi", ".webm"}
    ):
        state["releases"][entry.info_hash] = {
            "status": "existing",
            "title": entry.title,
            "canonical": canonical,
        }
        return False
    from bankai.web import jobs as webjobs

    title = f"{match.english_title} S{identity.season:02d}E{identity.episode:02d}"
    result = webjobs.enqueue(kind="show", title=title, args=_anime_args(entry, match, identity))
    if result.get("status") not in {"running", "queued", "duplicate"}:
        _hold(state, entry, f"Queue rejected release: {result.get('status', 'unknown')}")
        return False
    state["releases"][entry.info_hash] = {
        "status": result["status"],
        "title": entry.title,
        "canonical": canonical,
        "job_id": result.get("id"),
        "resolution": resolution,
    }
    state["held"] = [item for item in state["held"] if item.get("info_hash") != entry.info_hash]
    state["canonical"][canonical] = {
        "info_hash": entry.info_hash,
        "resolution": resolution,
        "job_id": result.get("id"),
    }
    return result.get("status") in {"running", "queued"}


async def _fetch_rss(client: httpx.AsyncClient) -> list[anime_mod.NyaaEntry]:
    response = await client.get(_RSS_URL)
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
    if backfill.get("frontier"):
        return
    phase = backfill.get("phase", "2160")
    if phase == "720":
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
    if not settings.backfill_enabled or backfill.get("complete"):
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
        terms = [f"{phase}p"]
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


async def run_cycle() -> dict[str, Any]:
    """Run one RSS poll/backfill slice. Safe to call from the API or scheduler."""

    async with _CYCLE_LOCK:
        settings = get_settings()
        policy = settings.anime
        state = _load_state()
        state["last_poll"] = time.time()
        state["last_enqueued"] = 0
        if not policy.enabled:
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
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=30, follow_redirects=True
            ) as client:
                rss = await _fetch_rss(client)
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
                        or _published_timestamp(entry) > cutoff
                    ):
                        continue
                    current = fresh.get(key)
                    if current is None or _rank(entry) > _rank(current):
                        fresh[key] = entry
                candidates = sorted(fresh.values(), key=lambda item: item.id, reverse=True)
                if state["backfill"].get("complete"):
                    historical = [
                        _entry_from_dict(item)
                        for item in [
                            *state["backfill"]["catalog_1080"].values(),
                            *state["backfill"]["catalog_720"].values(),
                        ]
                    ]
                    candidates.extend(sorted(historical, key=lambda item: item.id, reverse=True))
                inspected = 0
                for entry in candidates:
                    if enqueued >= policy.max_enqueues_per_cycle or inspected >= 12:
                        break
                    if not _needs_consideration(state, entry):
                        continue
                    inspected += 1
                    if await _consider(state, entry, client):
                        enqueued += 1
            state["last_success"] = time.time()
            state["last_error"] = None
            state["last_enqueued"] = enqueued
        except Exception as exc:
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            log.warning("Erai automation cycle failed: %s", exc)
        _save_state(state)
        return status(state=state, running=False)


def status(*, state: dict[str, Any] | None = None, running: bool | None = None) -> dict[str, Any]:
    state = state or _load_state()
    policy = get_settings().anime
    free = free_space_gib()
    if not policy.enabled:
        pause_reason = "Automation is disabled"
    elif not get_settings().metadata.tvdb_enabled or not get_settings().metadata.tvdb_api_key:
        pause_reason = "TVDB is not configured"
    elif free is None:
        pause_reason = "Anime destination is unavailable"
    elif free <= policy.min_free_space_gib:
        pause_reason = f"{policy.min_free_space_gib:g} GiB reserve reached ({free:.1f} GiB free)"
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
        "min_free_space_gib": policy.min_free_space_gib,
        "poll_interval_seconds": policy.poll_interval_seconds,
        "settle_minutes": policy.settle_minutes,
        "last_poll": state.get("last_poll"),
        "last_success": state.get("last_success"),
        "last_error": state.get("last_error"),
        "last_enqueued": state.get("last_enqueued", 0),
        "counts": counts,
        "held": state.get("held", [])[:100],
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


async def scheduler() -> None:
    global _STOP
    _STOP = asyncio.Event()
    while not _STOP.is_set():
        try:
            await run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Erai scheduler failed: %s", exc)
        timeout = max(60, get_settings().anime.poll_interval_seconds)
        with suppress(TimeoutError):
            await asyncio.wait_for(_STOP.wait(), timeout=timeout)


async def shutdown() -> None:
    _STOP.set()


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
