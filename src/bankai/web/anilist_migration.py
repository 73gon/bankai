"""Moving the anime library and its state from AniDB and Shoko to AniList.

Run once, with the automation stopped: ``bankai anime migrate-anilist
--dry-run`` first, which works everything out, writes a report and changes
nothing; then without ``--dry-run``, which backs every state file up and
does it.

What it does:

1. Which AniList entry each AniDB one is, from anime-offline-database. Where
   AniList keeps as one entry what AniDB splits in two, the second AniDB
   entry's episodes are numbered on after the first's; where AniList splits
   what AniDB keeps as one, an episode past the end moves on into the sequel.
2. Which AniList episode each file is: Shoko's link for it (from hashing the
   file), else the AniDB entry its folder was linked to by hand, else its
   folder's name looked up on AniList, with the number in its name. A file
   none of these places is left where it is, on an "Unmatched" card.
3. Collections from Shoko's groups, so shows stay grouped as they were; the
   choices made in the relation graph and the names given by hand come
   along. Entries in none join one by their AniList sequels and prequels.
4. The automation's state -- releases, canonical episodes, followed entries,
   blacklist, name mappings, queued jobs -- by AniList id.
5. The library laid out for Jellyfin: renames only, logged so they can be
   undone (``bankai anime layout-undo``), and NFO files.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs
from bankai.config import active_config_path
from bankai.logging import get_logger
from bankai.metadata import anilist, offline
from bankai.torrent.matcher import parse_se
from bankai.web import anime_collections, anime_files, anime_layout

log = get_logger(__name__)

_VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".webm"}
# "Season 02 - Sennen Kessen Hen - Ketsubetsu Tan" -> the entry's own title.
_SEASON_FOLDER = re.compile(r"^\s*Season\s*(?P<number>\d+)\s*(?:[-–:]\s*(?P<title>.+?))?\s*$", re.IGNORECASE)
_YEAR = re.compile(r"\s*\(\d{4}\)$")
_NUMBER = re.compile(r"(?:\b(?:E|EP|Episode)\s*|\s+-\s+)(?P<episode>\d{1,4})(?:v\d+)?\b", re.IGNORECASE)
_STATE_FILES = (
    "erai_automation.json",
    "erai_series_policies.json",
    "erai_mappings.json",
    "erai_retry_requests.json",
    "web_pending.json",
    "anime_files.json",
    "anime_collections.json",
    "shoko_file_links.json",
    "anime_library_prefs.json",
    "anime_collection_places.json",
    "anime_collection_titles.json",
    "anime_collection_names.json",
)


def _state_dir() -> Path:
    return bgjobs.jobs_root().parent


def _read(name: str, default: Any) -> Any:
    try:
        return json.loads((_state_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(name: str, value: Any) -> None:
    path = _state_dir() / name
    tmp = path.with_suffix(".migration.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# -- AniDB -> AniList ----------------------------------------------------------------


@dataclass
class Translator:
    """AniDB entry and episode -> AniList entry and episode."""

    # AniDB id -> episodes of the AniDB entries before it that AniList keeps in the same entry.
    offsets: dict[int, int] = field(default_factory=dict)
    # AniDB entries anime-offline-database does not link yet, placed by learn_unmapped.
    extra: dict[int, int] = field(default_factory=dict)

    def entry(self, anidb_id: object) -> int | None:
        found = offline.anilist_of_anidb(anidb_id)
        if found is None and str(anidb_id).isdigit():
            found = self.extra.get(int(anidb_id))  # type: ignore[arg-type]
        return found

    def episode(self, anidb_id: object, number: int) -> tuple[int, int] | None:
        aid = self.entry(anidb_id)
        if aid is None:
            return None
        number += self.offsets.get(int(anidb_id), 0)  # type: ignore[arg-type]
        found = anilist.cached_index()
        entry = found.anime.get(aid) if found is not None else None
        if entry is not None:
            settled, number = anilist.settle_in(entry, number)
            aid = settled.id
        return aid, number


def learn_unmapped(
    translator: Translator,
    unmapped: set[int],
    *,
    names: dict[int, list[str]],
    relations: dict[int, list[tuple[int, str]]],
    counts: dict[int, int] | None = None,
    taken: set[int] | None = None,
) -> Counter:
    """Place AniDB entries anime-offline-database does not link to AniList yet.

    Mostly the newest seasons. Through AniDB's own relations first: the
    sequel of an entry that is placed is that entry's sequel on AniList, when
    AniList lists exactly one; an OVA or side story of a placed entry is the
    one entry related to it on AniList with as many episodes (``counts``,
    AniDB's). Else by name, with a trailing year dropped ("Release That Witch
    (2026)"), when the name finds exactly one entry that no other AniDB entry
    of the library is (``taken``) -- a name like "Attack on Titan" is the
    show's, not its OVA's. Repeated, so a chain of new seasons is placed one
    link at a time.
    """
    learned: Counter = Counter()
    index = anilist.cached_index()
    # An entry another AniDB entry of the library already is: a second one is
    # a recap, an OVA under the show's name -- never the same season again.
    if taken is None:
        try:
            taken = set(json.loads(offline._cross_store().read_text(encoding="utf-8")).values())
        except (OSError, ValueError):
            taken = set()
    taken = set(taken)
    for _ in range(4):
        progress = False
        for anidb in sorted(unmapped):
            if translator.entry(anidb) is not None:
                continue
            aid, how = None, "relation"
            for other, kind in relations.get(anidb, []):
                near = translator.entry(other)
                want = {"Prequel": "SEQUEL", "Sequel": "PREQUEL"}.get(kind)
                if near is None or want is None:
                    continue
                options = [o for o, k, _f in anilist.relations(near) if k == want]
                seasons = [o for o, k, f in anilist.relations(near) if k == want and f in anilist.SEASON_FORMATS]
                pick = seasons if len(seasons) == 1 else options
                if len(pick) == 1:
                    aid = pick[0]
                    break
            episodes = (counts or {}).get(anidb)
            if aid is None and episodes:
                for other, _kind in relations.get(anidb, []):
                    near = translator.entry(other)
                    if near is None:
                        continue
                    same = [
                        o for o, _k, _f in anilist.relations(near)
                        if anilist.episode_count(o) == episodes and o not in taken
                    ]
                    if len(set(same)) == 1:
                        aid = same[0]
                        break
            if aid is None and index is not None:
                how = "name"
                for name in names.get(anidb, []):
                    for variant in dict.fromkeys((name, _YEAR.sub("", name))):
                        result = anilist.resolve_in(index, variant)
                        if result.anime is not None and result.anime.id not in taken:
                            aid = result.anime.id
                            break
                    if aid is not None:
                        break
            if aid is not None:
                translator.extra[anidb] = aid
                taken.add(aid)
                learned[how] += 1
                progress = True
        if not progress:
            break
    return learned


def offsets_from_shoko(series: list[dict]) -> dict[int, int]:
    """Where AniDB splits what AniList keeps as one entry, how far each part is numbered on.

    The parts are ordered by when they aired (else by id), each one's episodes
    following the episodes of the parts before it.
    """
    parts: dict[int, list[tuple[str, int, int]]] = {}
    for row in series:
        anidb = row.get("anidb")
        aid = offline.anilist_of_anidb(anidb) if anidb else None
        if aid is None:
            continue
        parts.setdefault(aid, []).append((str(row.get("aired") or "9999"), int(anidb), int(row.get("episodes") or 0)))
    offsets: dict[int, int] = {}
    for rows in parts.values():
        if len(rows) < 2:
            continue
        before = 0
        for _aired, anidb, count in sorted(rows):
            offsets[anidb] = before
            before += count
    return offsets


# -- Shoko, read one last time ---------------------------------------------------------


async def shoko_groups() -> tuple[list[dict], list[dict]]:
    """Shoko's groups and series, as {id, name, custom, main_anidb} and {anidb, group, episodes, aired}.

    Asked of Shoko itself when it answers; else the last snapshot bankai took
    before changing a group.
    """
    import httpx

    try:
        config = tomllib.loads(active_config_path().read_text(encoding="utf-8")).get("shoko") or {}
    except (OSError, ValueError):
        config = {}
    url = str(config.get("url") or "http://127.0.0.1:8111").rstrip("/")
    key = str(config.get("api_key") or "")
    groups_raw: list = []
    series_raw: list = []
    if key:
        try:
            async with httpx.AsyncClient(base_url=url, timeout=120, headers={"apikey": key}) as client:
                answer = await client.get("/api/v3/Group", params={"pageSize": 0, "includeEmpty": "false"})
                answer.raise_for_status()
                groups_raw = (answer.json() or {}).get("List") or []
                answer = await client.get("/api/v3/Series", params={"pageSize": 0, "includeDataFrom": "AniDB"})
                answer.raise_for_status()
                series_raw = (answer.json() or {}).get("List") or []
        except Exception as exc:
            log.warning("Shoko could not be read (%s); using the last group snapshot", exc)
            groups_raw, series_raw = [], []
    if not groups_raw:
        snapshots = sorted((_state_dir() / "shoko_groups").glob("groups-*.json"))
        if snapshots:
            raw = json.loads(snapshots[-1].read_text(encoding="utf-8"))
            groups_raw = [{"IDs": row.get("ids") or {}, "Name": row.get("name"), "HasCustomName": row.get("has_custom_name")} for row in raw.get("groups") or []]
            series_raw = [{"IDs": row.get("ids") or {}, "Name": row.get("name")} for row in raw.get("series") or []]
    groups = [
        {
            "id": int((row.get("IDs") or {}).get("ID")),
            "name": row.get("Name"),
            "custom": bool(row.get("HasCustomName")),
            "main_anidb": (row.get("IDs") or {}).get("MainAnime"),
            # A series picked by hand to name the group ("Shown as").
            "preferred_series": (row.get("IDs") or {}).get("PreferredSeries"),
        }
        for row in groups_raw
        if str((row.get("IDs") or {}).get("ID") or "").isdigit()
    ]
    series = []
    for row in series_raw:
        ids = row.get("IDs") or {}
        anidb = row.get("AniDB") if isinstance(row.get("AniDB"), dict) else {}
        sizes = ((row.get("Sizes") or {}).get("Total") or {}) if isinstance(row.get("Sizes"), dict) else {}
        if not str(ids.get("AniDB") or "").isdigit():
            continue
        series.append(
            {
                "id": ids.get("ID"),
                "name": row.get("Name"),
                "anidb": int(ids["AniDB"]),
                "group": ids.get("ParentGroup"),
                "episodes": anidb.get("EpisodeCount") or sizes.get("Episodes") or 0,
                "aired": anidb.get("AirDate") or (row.get("AirDate") if isinstance(row.get("AirDate"), str) else None),
            }
        )
    return groups, series


async def shoko_relations(series_ids: dict[int, int]) -> dict[int, list[tuple[int, str]]]:
    """AniDB's relations of some entries, as Shoko keeps them: AniDB id -> [(related AniDB id, kind)].

    ``series_ids`` maps AniDB id -> Shoko series id. Also reads what bankai
    learned of AniDB relations before (anidb_relations.json).
    """
    import httpx

    found: dict[int, list[tuple[int, str]]] = {}
    cached = (_read("anidb_relations.json", {}) or {}).get("aids") or {}
    for raw, row in cached.items():
        for left, right, kind in (row or {}).get("relations") or []:
            if str(left) == str(raw):
                found.setdefault(int(left), []).append((int(right), str(kind)))
    try:
        config = tomllib.loads(active_config_path().read_text(encoding="utf-8")).get("shoko") or {}
    except (OSError, ValueError):
        config = {}
    key = str(config.get("api_key") or "")
    if not key or not series_ids:
        return found
    url = str(config.get("url") or "http://127.0.0.1:8111").rstrip("/")
    async with httpx.AsyncClient(base_url=url, timeout=60, headers={"apikey": key}) as client:
        for anidb, series_id in series_ids.items():
            try:
                answer = await client.get(f"/api/v3/Series/{series_id}/AniDB/Relations")
                answer.raise_for_status()
            except Exception:
                continue
            for row in answer.json() or []:
                right = (row.get("RelatedIDs") or {}).get("AniDB")
                if isinstance(right, int) and row.get("Type"):
                    found.setdefault(anidb, []).append((right, str(row["Type"])))
    return found


# -- Files ----------------------------------------------------------------------------


def _number(name: str) -> int | None:
    numbered = parse_se(name)
    if numbered and numbered[1]:
        return int(numbered[1])
    match = _NUMBER.search(Path(name).stem)
    return int(match["episode"]) if match else None


def _season_of(name: str) -> int | None:
    numbered = parse_se(name)
    return int(numbered[0]) if numbered and numbered[0] is not None else None


@dataclass
class Files:
    rows: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    how: Counter = field(default_factory=Counter)
    unmatched: list[str] = field(default_factory=list)


def identify(
    paths: list[Path],
    *,
    links: dict[str, list],
    folder_links: dict[str, int],
    translator: Translator,
) -> Files:
    """Which AniList episode each file is; see the module's docstring for the order asked."""
    found = Files()
    index = anilist.cached_index()
    memo: dict[str, int | None] = {}

    def by_name(*names: str) -> int | None:
        for value in names:
            value = value.strip()
            if not value or index is None:
                continue
            if value not in memo:
                result = anilist.resolve_in(index, value)
                memo[value] = result.anime.id if result.anime else None
            if memo[value] is not None:
                return memo[value]
        return None

    for path in paths:
        rel = anime_files.key(path)
        if rel is None:
            continue
        parts = Path(rel).parts
        folder = parts[0] if len(parts) > 1 else ""
        season_dir = parts[1] if len(parts) > 2 else ""
        pairs: list[tuple[int, int]] = []
        for anidb, number in links.get(str(path)) or []:
            translated = translator.episode(anidb, int(number))
            if translated and translated not in pairs:
                pairs.append(translated)
        how = "shoko" if pairs else ""
        number = _number(path.name)
        if not pairs and number and folder in folder_links:
            translated = translator.episode(folder_links[folder], number)
            if translated:
                pairs, how = [translated], "folder link"
        season = _SEASON_FOLDER.match(season_dir) if season_dir else None
        special = (
            (season is not None and int(season["number"]) == 0)
            or season_dir.casefold() == "specials"
            or _season_of(path.name) == 0
        )
        # A special is never told by the show's name: it would be taken for
        # the regular episode of its number.
        if not pairs and number and not special:
            names: list[str] = []
            if season and season["title"]:
                base = re.split(r"\s+-\s+|:\s*", folder, maxsplit=1)[0]
                names += [season["title"], f"{base} {season['title']}", f"{folder} {season['title']}"]
            ordinal = int(season["number"]) if season else _season_of(path.name)
            if ordinal and ordinal > 1:
                names.append(f"{_YEAR.sub('', folder)} Season {ordinal}")
            # "Tomb Raider King (2026)": the year was added for the folder, not the name.
            names += list(dict.fromkeys([folder, _YEAR.sub("", folder)]))
            aid = by_name(*names)
            if aid is not None and index is not None:
                settled, episode = anilist.settle_in(index.anime[aid], number)
                pairs, how = [(settled.id, episode)], "folder name"
        if pairs:
            found.rows[rel] = pairs
            found.how[how] += 1
        else:
            found.unmatched.append(rel)
    return found


def library_paths() -> list[Path]:
    paths: list[Path] = []
    for root in (anime_files.anime_root(), anime_files.staging_root()):
        if root.is_dir():
            paths += [path for path in root.rglob("*") if path.suffix.casefold() in _VIDEO and path.is_file()]
    return sorted(paths)


def _referenced(
    series: list[dict],
    links: dict[str, list],
    folder_links: dict[str, int],
    state: dict,
    policies: dict,
    mappings: dict,
) -> tuple[set[int], dict[int, list[str]]]:
    """Every AniDB id anything points at, and the names each is known by."""
    ids: set[int] = set()
    names: dict[int, list[str]] = {}

    def note(raw: object, *titles: object) -> None:
        if not str(raw or "").isdigit():
            return
        anidb = int(raw)  # type: ignore[arg-type]
        ids.add(anidb)
        for title in titles:
            if isinstance(title, str) and title.strip() and title not in names.setdefault(anidb, []):
                names[anidb].append(title)

    for row in series:
        note(row["anidb"], row.get("name"))
    for pairs in links.values():
        for anidb, _episode in pairs:
            note(anidb)
    for anidb in folder_links.values():
        note(anidb)
    for raw, record in (state.get("anime") or {}).items():
        note(raw, (record or {}).get("english_title"), (record or {}).get("title"))
    for row in policies.values():
        if isinstance(row, dict):
            note(row.get("anidb_id"), row.get("english_title"), row.get("anidb_title"), *(row.get("anidb_titles") or []))
    for row in mappings.values():
        if isinstance(row, dict):
            note(row.get("anidb_id"), row.get("anidb_title"))
    for key in state.get("canonical") or {}:
        if key.startswith("anidb:"):
            note(key[len("anidb:"):].split("|", 1)[0])
    for release in (state.get("releases") or {}).values():
        args = release.get("args") if isinstance(release, dict) else None
        if isinstance(args, list) and "--anidb-id" in args:
            title = args[args.index("--anidb-title") + 1] if "--anidb-title" in args else None
            english = args[args.index("--english-title") + 1] if "--english-title" in args else None
            note(args[args.index("--anidb-id") + 1], english, title)
    return ids, names


# -- Collections ------------------------------------------------------------------------


def collections_from_shoko(
    groups: list[dict],
    series: list[dict],
    translator: Translator,
    *,
    titles: dict[str, int],
    bankai_names: dict[str, str],
    places: dict[str, int | None],
    chosen: set[int] | None = None,
) -> anime_collections.Store:
    """Shoko's groups as collections: members, title entry, and a name given by hand.

    ``chosen`` collects the collections whose title entry the user picked;
    the rest are named after their show's first season once it is known.
    """
    preferred = {row.get("id"): row["anidb"] for row in series if row.get("id") is not None}
    store = anime_collections.Store()
    members: dict[int, list[int]] = {}
    for row in series:
        aid = translator.entry(row["anidb"])
        if aid is not None and str(row.get("group") or "").isdigit():
            members.setdefault(int(row["group"]), []).append(aid)
    taken: set[int] = set()
    by_group: dict[int, int] = {}
    for group in sorted(groups, key=lambda row: row["id"]):
        wanted = [aid for aid in dict.fromkeys(members.get(group["id"], [])) if aid not in taken]
        if not wanted:
            continue
        taken.update(wanted)
        picked = titles.get(str(group["id"])) or preferred.get(group.get("preferred_series"))
        title = translator.entry(picked or group.get("main_anidb"))
        row = store.create(wanted, title=title if title in wanted else wanted[0])
        if picked and title in wanted and chosen is not None:
            chosen.add(row.id)
        # A name given in Shoko by hand is the user's; one bankai gave it is AniList's again.
        if group.get("custom") and group.get("name") and bankai_names.get(str(group["id"])) != group["name"]:
            row.name = str(group["name"])
        by_group[group["id"]] = row.id
    for anidb, group in places.items():
        aid = translator.entry(anidb)
        if aid is None or aid in store.of():
            continue
        if group is None:
            store.alone.add(aid)
        elif int(group) in by_group:
            store.collections[by_group[int(group)]].members.append(aid)
    return store


# -- State ------------------------------------------------------------------------------


def convert_args(args: list[str], translator: Translator) -> list[str] | None:
    """A download's arguments by AniList: --anidb-id/--anidb-title and the episode. None if it cannot be placed."""
    if "--anilist-id" in args:
        return list(args)
    if "--anidb-id" not in args:
        return None
    anidb = args[args.index("--anidb-id") + 1]
    episode = args[args.index("--episode") + 1] if "--episode" in args else None
    if not str(anidb).isdigit():
        return None
    if episode is not None and str(episode).isdigit():
        translated = translator.episode(int(anidb), int(episode))
        if translated is None:
            return None
        aid, number = translated
    else:
        aid = translator.entry(int(anidb))
        number = None
        if aid is None:
            return None
    out: list[str] = []
    skip = False
    for position, value in enumerate(args):
        if skip:
            skip = False
            continue
        if value == "--anidb-id":
            out += ["--anilist-id", str(aid)]
            skip = True
        elif value == "--anidb-title":
            out += ["--anilist-title", anilist.name(aid) or args[position + 1]]
            skip = True
        elif value == "--episode" and number is not None:
            out += ["--episode", str(number)]
            skip = True
        else:
            out.append(value)
    return out


def anilist_job_args(args: list[str]) -> list[str] | None:
    """An anime job from before AniList, by AniList; None when it cannot be placed (a TVDB-era job)."""
    return convert_args(list(args), Translator())


def _canonical(key: str, translator: Translator) -> str | None:
    if not key.startswith("anidb:"):
        return key
    head, _, episode = key[len("anidb:"):].partition("|")
    if not head.isdigit() or not episode.isdigit():
        return None
    translated = translator.episode(int(head), int(episode))
    return f"anilist:{translated[0]}|{translated[1]}" if translated else None


def _entry_fields(aid: int) -> dict[str, Any]:
    found = anilist.cached_index()
    entry = found.anime.get(aid) if found is not None else None
    return {
        "anilist_id": aid,
        "anilist_title": entry.title if entry else anilist.name(aid),
        "english_title": (entry.english if entry else None) or anilist.name(aid),
        "anilist_titles": list(entry.titles) if entry else [],
        "anilist_poster_url": anilist.cover(aid),
    }


def convert_state(state: dict, translator: Translator, notes: Counter) -> dict:
    canonical: dict[str, Any] = {}
    for key, value in (state.get("canonical") or {}).items():
        new = _canonical(key, translator)
        if new is None:
            notes["canonical episodes AniList cannot place (dropped)"] += 1
            continue
        if new != key:
            notes["canonical episodes moved to AniList"] += 1
        canonical.setdefault(new, value)
    state["canonical"] = canonical
    for release in (state.get("releases") or {}).values():
        if not isinstance(release, dict):
            continue
        if str(release.get("canonical") or "").startswith("anidb:"):
            new = _canonical(release["canonical"], translator)
            if new is None:
                notes["releases AniList cannot place"] += 1
                release.pop("canonical", None)
            else:
                release["canonical"] = new
                aid, _, number = new[len("anilist:"):].partition("|")
                release["anilist_id"] = int(aid)
                if "episode" in release and number.isdigit():
                    release["episode"] = int(number)
        release.pop("anidb_id", None)
        if isinstance(release.get("args"), list) and "--anidb-id" in release["args"]:
            converted = convert_args(release["args"], translator)
            if converted is None:
                notes["release arguments AniList cannot place"] += 1
            else:
                release["args"] = converted
                notes["release arguments moved to AniList"] += 1
    followed: dict[str, Any] = {}
    for raw, record in (state.get("anime") or {}).items():
        aid = translator.entry(raw) if str(raw).isdigit() else None
        if aid is None:
            notes["followed entries AniList cannot place (dropped)"] += 1
            continue
        found = anilist.cached_index()
        entry = found.anime.get(aid) if found is not None else None
        followed[str(aid)] = {
            "anilist_id": aid,
            "title": entry.title if entry else (record or {}).get("title"),
            "english_title": anilist.name(aid) or (record or {}).get("english_title"),
            "matching_titles": list(entry.titles) if entry else list((record or {}).get("matching_titles") or []),
        }
    state["anime"] = followed
    return state


def convert_policies(policies: dict, translator: Translator, notes: Counter) -> dict:
    out: dict[str, Any] = {}
    for key, row in policies.items():
        if not isinstance(row, dict) or not str(row.get("anidb_id") or "").isdigit():
            out[key] = row
            continue
        aid = translator.entry(row["anidb_id"])
        clean = {k: v for k, v in row.items() if not k.startswith("anidb_")}
        if aid is None:
            # Kept as a decision on the show's name, which is how it still matches.
            notes["blacklist entries kept by name (no AniList entry)"] += 1
            out[key] = clean
            continue
        out[f"anilist:{aid}"] = {**clean, **_entry_fields(aid)}
        notes["blacklist entries moved to AniList"] += 1
    return out


def convert_mappings(mappings: dict, translator: Translator, notes: Counter) -> dict:
    out: dict[str, Any] = {}
    for key, row in mappings.items():
        if not isinstance(row, dict) or not str(row.get("anidb_id") or "").isdigit():
            out[key] = row
            continue
        aid = translator.entry(row["anidb_id"])
        if aid is None:
            notes["name mappings dropped (no AniList entry)"] += 1
            continue
        offset = int(row.get("anidb_episode_offset") or 0) + translator.offsets.get(int(row["anidb_id"]), 0)
        out[key] = {"anilist_id": aid, "anilist_title": anilist.name(aid), "anilist_episode_offset": offset}
        notes["name mappings moved to AniList"] += 1
    return out


def _preview(layout: anime_layout.Layout) -> list[dict]:
    """Each collection with files: its folder, its seasons and specials, and how many files each has."""
    held = anime_files.by_entry(layout.rows)
    rows = []
    for cid, row in sorted(layout.store.collections.items(), key=lambda item: layout.folders.get(item[0], "")):
        if not any(aid in held for aid in row.members):
            continue
        plan = layout.plans[cid]
        rows.append(
            {
                "folder": layout.folders.get(cid),
                "seasons": [
                    [number, anime_collections.entry_name(aid), aid, len(held.get(aid, {}))]
                    for aid, number in sorted(plan.seasons.items(), key=lambda item: item[1])
                ],
                "specials": [
                    [start + 1, anime_collections.entry_name(aid), aid, len(held.get(aid, {}))]
                    for aid, start in sorted(plan.specials.items(), key=lambda item: item[1])
                ],
            }
        )
    return rows


# -- The whole run ------------------------------------------------------------------------


async def migrate(*, dry_run: bool) -> dict[str, Any]:
    started = time.time()
    if not dry_run and (_state_dir() / "anilist_migration_report.json").exists():
        # A second run would read the AniList ids it wrote as AniDB ones.
        raise RuntimeError(
            "the anime library was moved to AniList already (anilist_migration_report.json); "
            "restore the backup in anilist_migration_backup/ before running it again"
        )
    notes: Counter = Counter()
    await offline.refresh()
    if not offline.entries():
        raise RuntimeError("anime-offline-database could not be fetched; nothing can be moved without it")

    groups, series = await shoko_groups()
    translator = Translator(offsets=offsets_from_shoko(series))
    links = {str(path): pairs for path, pairs in (_read("shoko_file_links.json", {}) or {}).items()}
    if "files" in links and isinstance(links["files"], dict):
        links = links["files"]
    prefs = _read("anime_library_prefs.json", {})
    folder_links = {
        name.split(":", 1)[1]: int(row["anidb_id"])
        for name, row in prefs.items()
        if name.startswith("folder:") and str((row or {}).get("anidb_id") or "").isdigit()
    }
    state = _read("erai_automation.json", {})
    old_policies = _read("erai_series_policies.json", {})
    old_mappings = _read("erai_mappings.json", {})
    referenced, names = _referenced(series, links, folder_links, state, old_policies, old_mappings)

    # Every AniList entry anything points at, fetched before anything is placed:
    # sequels decide where an episode past an entry's end goes.
    every = {aid for anidb in referenced if (aid := translator.entry(anidb))}
    await anilist.ensure(every, refresh=False, max_batches=200)
    near = {other for aid in every for other, _kind, _form in anilist.relations(aid)}
    await anilist.ensure(near - every, refresh=False, max_batches=200)
    await asyncio.to_thread(anilist.table)
    unmapped = {anidb for anidb in referenced if translator.entry(anidb) is None}
    on_shoko = {row["anidb"]: row["id"] for row in series if row["anidb"] in unmapped and row.get("id")}
    learned = learn_unmapped(
        translator,
        unmapped,
        names=names,
        relations=await shoko_relations(on_shoko),
        counts={row["anidb"]: int(row.get("episodes") or 0) for row in series},
        taken={aid for anidb in referenced if (aid := translator.entry(anidb)) is not None},
    )
    await anilist.ensure(set(translator.extra.values()), refresh=False, max_batches=20)
    await asyncio.to_thread(anilist.table)

    paths = await asyncio.to_thread(library_paths)
    files = await asyncio.to_thread(identify, paths, links=links, folder_links=folder_links, translator=translator)

    chosen: set[int] = set()
    store = collections_from_shoko(
        groups,
        series,
        translator,
        titles={str(k): int(v) for k, v in _read("anime_collection_titles.json", {}).items()},
        bankai_names={str(k): str(v) for k, v in _read("anime_collection_names.json", {}).items()},
        places=_read("anime_collection_places.json", {}),
        chosen=chosen,
    )
    from_shoko = len(store.collections)
    followed = {translator.entry(raw) for raw in (state.get("anime") or {})} - {None}
    on_disk = {aid for pairs in files.rows.values() for aid, _ in pairs}
    await anime_layout.ensure_metadata(on_disk | followed)
    await asyncio.to_thread(anilist.table)
    joined = anime_collections.join(sorted((on_disk | followed) - set(store.of())), store)
    grown = anime_collections.extend(store)
    for row in store.collections.values():
        # Named after the show's first season -- "Dorohedoro", not "Dorohedoro
        # Season 2" -- even when only a later one is on disk.
        if row.id not in chosen and row.title is not None:
            row.title = anime_collections.first_of_show(row.members, row.title)
        anime_collections.settle_specials(row)

    layout = anime_layout.Layout.build(store, files.rows)
    moves = anime_layout.plan_moves(layout)
    folders_now = {Path(rel).parts[0] for rel in files.rows}
    folders_after = {Path(move.new).parts[0] for move in moves.moves} | {
        Path(rel).parts[0] for rel in files.rows if rel not in {move.old for move in moves.moves}
    }
    renamed = Counter((Path(move.old).parts[0], Path(move.new).parts[0]) for move in moves.moves)

    policies = convert_policies(old_policies, translator, notes)
    mappings = convert_mappings(old_mappings, translator, notes)
    state = convert_state(state, translator, notes)
    pending = _read("web_pending.json", [])
    for job in pending if isinstance(pending, list) else []:
        if isinstance(job, dict) and isinstance(job.get("args"), list) and "--anidb-id" in job["args"]:
            converted = convert_args(job["args"], translator)
            if converted is None:
                notes["queued jobs AniList cannot place"] += 1
            else:
                job["args"] = converted
                notes["queued jobs moved to AniList"] += 1

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "files": {
            "total": len(paths),
            "matched": len(files.rows),
            "how": dict(files.how),
            "unmatched": len(files.unmatched),
            "unmatched_files": files.unmatched[:500],
        },
        "anidb_entries_placed_by": dict(learned),
        "anidb_entries_not_on_anilist": {
            str(anidb): (names.get(anidb) or [None])[0]
            for anidb in sorted(referenced)
            if translator.entry(anidb) is None
        },
        "collections": {
            "total": len(store.collections),
            "from_shoko_groups": from_shoko,
            "made_or_joined_by_relations": len(joined),
            "grown_by_sequels": len(grown),
        },
        "layout": {
            "moves": len(moves.moves),
            "already_in_place": len(files.rows) - len(moves.moves) - len(moves.duplicates) - len(moves.unplaced),
            "duplicates": [list(pair) for pair in moves.duplicates[:200]],
            "duplicate_count": len(moves.duplicates),
            "unplaced": moves.unplaced[:200],
            "folders_before": len(folders_now),
            "folders_after": len(folders_after),
            "folder_moves": [
                {"from": old, "to": new, "files": count} for (old, new), count in sorted(renamed.items())
            ],
        },
        "state": dict(notes),
        "preview": _preview(layout),
        "free_bytes": shutil.disk_usage(anime_files.anime_root()).free if anime_files.anime_root().exists() else None,
    }
    if not dry_run:
        backup = _state_dir() / "anilist_migration_backup" / time.strftime("%Y%m%d-%H%M%S")
        backup.mkdir(parents=True, exist_ok=True)
        for name in _STATE_FILES:
            source = _state_dir() / name
            if source.exists():
                shutil.copy2(source, backup / name)
        from bankai.web import erai

        with erai._STATE_LOCK:
            erai._save_state(state)
            erai._save_policies(policies)
            _write("erai_mappings.json", mappings)
            if isinstance(pending, list):
                _write("web_pending.json", pending)
            with anime_files.editing() as rows:
                rows.clear()
                rows.update(files.rows)
            with anime_collections.editing() as current:
                current.collections = store.collections
                current.alone = store.alone
                current.next = store.next
        report["backup"] = str(backup)
        report["applied"] = await anime_layout.relayout(None, label="migration")
        report["applied"]["duplicates"] = len(report["applied"].get("duplicates") or [])
        report["applied"]["unplaced"] = len(report["applied"].get("unplaced") or [])
    report["seconds"] = round(time.time() - started, 1)
    name = "anilist_migration_report.json" if not dry_run else "anilist_migration_dry_run.json"
    (_state_dir() / name).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    report["report_file"] = str(_state_dir() / name)
    return report


__all__ = ["Translator", "anilist_job_args", "convert_args", "identify", "migrate"]
