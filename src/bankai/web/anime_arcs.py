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
    # Naruto (2002), 220 episodes: its canon arcs; the missions from 136 on (anime-only, no official name) one season.
    20: [
        (1, "Prologue: Land of Waves"),
        (20, "Chūnin Exams"),
        (68, "Konoha Crush"),
        (81, "Search for Tsunade"),
        (101, "Land of Tea Escort Mission"),
        (107, "Sasuke Recovery Mission"),
        (136, "Konoha Missions"),
    ],
    # Naruto: Shippuden, 500 episodes: its arcs, those that stay together (one place, one war
    # stretch, the filler between) joined into 19 seasons.
    1735: [
        (1, "Kazekage Rescue"),
        (33, "Long-Awaited Reunion"),
        (54, "Twelve Guardian Ninja"),
        (72, "Hidan and Kakuzu"),
        (89, "The Three-Tailed Demon Turtle"),
        (113, "Master's Prophecy and Vengeance"),
        (152, "Pain's Assault"),
        (176, "Past Arc: The Locus of Konoha"),
        (197, "The Gathering of the Five Kage"),
        (222, "Paradise Island: Nine-Tails Taming"),
        (276, "Swordsmen and Power"),
        (296, "The Fourth Great Ninja War: Reanimation"),
        (349, "Kakashi: Shadow of the ANBU Black Ops"),
        (362, "The Fourth Great Ninja War: Obito and Madara"),
        (394, "In Naruto's Footsteps: Friends' Paths"),
        (414, "Infinite Tsukuyomi"),
        (432, "Legends: Jiraiya and Itachi"),
        (459, "Kaguya and the Final Battle"),
        (480, "Hiden: After the War"),
    ],
    # One Piece: its arcs, short fillers folded into the arc before, Wano's three acts one season.
    21: [
        (1, "Romance Dawn"),
        (5, "Orange Town"),
        (9, "Syrup Village"),
        (19, "Baratie"),
        (31, "Arlong Park"),
        (45, "Loguetown"),
        (62, "Reverse Mountain and Whiskey Peak"),
        (70, "Little Garden"),
        (78, "Drum Island"),
        (92, "Alabasta"),
        (144, "Jaya"),
        (153, "Skypiea"),
        (207, "Long Ring Long Land"),
        (229, "Water 7"),
        (264, "Enies Lobby"),
        (337, "Thriller Bark"),
        (385, "Sabaody Archipelago"),
        (408, "Amazon Lily"),
        (422, "Impel Down"),
        (459, "Marineford"),
        (517, "Fish-Man Island"),
        (579, "Punk Hazard"),
        (629, "Dressrosa"),
        (751, "Zou"),
        (783, "Whole Cake Island"),
        (892, "Wano Country"),
        (1089, "Egghead"),
        (1156, "Elbaph"),
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
