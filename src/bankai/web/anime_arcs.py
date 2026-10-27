"""Arcs: an anime entry that is one long season, split by hand into named seasons.

Bleach is one AniList entry of 366 episodes, so the layout made it one season.
Its arcs make it 16 (anime_collections.Collection.arcs): each arc a season of
its own, named in its season.nfo, the episodes keeping their numbers (episode
21 is S02E21). Suggestions for well-known shows -- each arc's first episode and
its name -- fill the drawer's Seasons dialog, to be checked and adjusted.
"""

from __future__ import annotations

from typing import Any

from bankai.metadata import anilist
from bankai.web import anime_collections, anime_files

# AniList id -> the arcs' first episodes and names, as the show's official season split has them.
SUGGESTED: dict[int, list[tuple[int, str]]] = {
    # Bleach (2004), 366 episodes: the 16 seasons of the Viz release and TVDB.
    269: [
        (1, "The Substitute"),
        (21, "The Entry"),
        (42, "The Rescue"),
        (64, "The Bount"),
        (92, "The Assault"),
        (110, "The Arrancar"),
        (132, "The Hueco Mundo"),
        (152, "The Fierce Fight"),
        (168, "The New Captain Shūsuke Amagai"),
        (190, "The Arrancar vs. Shinigami"),
        (206, "The Past"),
        (213, "The Fake Karakura Town"),
        (230, "The Zanpakutō Unknown Tales"),
        (266, "The Arrancar: Downfall"),
        (317, "Gotei 13 Invading Army"),
        (343, "The Lost Agent"),
    ],
}


def overview(cid: int) -> dict[str, Any]:
    """A collection's seasons that can be split into arcs -- each an entry that is a season of its
    own -- with their episodes, arcs as set, and a suggestion where one is known."""
    store = anime_collections.load()
    row = store.collections.get(cid)
    if row is None:
        raise ValueError("there is no such collection")
    held = anime_files.by_entry()
    plan = anime_collections.plan(row, episodes_on_disk=lambda aid: max(held.get(aid, {0: []}), default=0), store=store)
    seasons = []
    for entries in plan.season_lists():
        if len(entries) != 1:
            continue
        aid = entries[0]
        seasons.append({
            "anilist_id": aid,
            "title": anime_collections.entry_name(aid),
            "season": plan.seasons[aid],
            "episodes": max(anilist.episode_count(aid) or 0, max(held.get(aid, {0: []}), default=0)),
            "on_disk": len(held.get(aid, {})),
            "arcs": [{"from": first, "name": name} for first, name in row.arcs.get(aid, [])],
            "suggested": [{"from": first, "name": name} for first, name in SUGGESTED.get(aid, [])],
        })
    return {"collection_id": cid, "seasons": seasons}
