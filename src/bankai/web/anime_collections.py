"""Collections: the AniDB entries of one show, kept together as one library card.

AniDB has an entry per season, or per cour, so a show of four seasons is four
entries -- four cards, four shows in Jellyfin. A collection puts them back
together. It is a Shoko group: Shoko already keeps a show's seasons in one,
and Jellyfin shows them through Shokofin, so the library and the player agree.

What joins a collection on its own is AniDB's main line: sequels and
prequels, followed through entries that are not on disk, so a special filed
between two seasons does not break the chain. Side stories, movies and
recaps stay out unless Shoko already grouped them. Groups as Shoko made them
are kept: the entries placed here are only the ones it does not hold yet.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAIN_LINE = frozenset({"Sequel", "Prequel"})


@dataclass
class Collections:
    # AniDB id -> the Shoko group it belongs to: its own if Shoko holds it,
    # else the group of the nearest held entry along the main line.
    of: dict[int, int] = field(default_factory=dict)

    def members(self, group_id: int) -> list[int]:
        return sorted(aid for aid, group in self.of.items() if group == group_id)


def main_line(relations: list[tuple[int, int, str]]) -> dict[int, set[int]]:
    """Sequel and prequel links, both ways: the chain of a show's seasons."""
    graph: dict[int, set[int]] = {}
    for left, right, kind in relations:
        if kind in MAIN_LINE and left != right:
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)
    return graph


def assign(catalog: Any) -> Collections:
    """Every entry Shoko holds, and every main-line relative of one, by collection.

    Held entries keep the group Shoko put them in. The rest are reached from
    them breadth-first, so an entry between two collections joins the nearer;
    at equal distance the older group (lower id), for an answer that does
    not change from one page load to the next.
    """
    found = Collections()
    if catalog is None or not getattr(catalog, "groups", None):
        return found
    queue: deque[int] = deque()
    for aid in sorted(catalog.entries):
        group = catalog.entries[aid].group_id
        if group is not None:
            found.of[aid] = group
    graph = main_line(catalog.relations)
    for aid in sorted(found.of, key=lambda aid: (found.of[aid], aid)):
        queue.append(aid)
    while queue:
        aid = queue.popleft()
        for other in sorted(graph.get(aid, ())):
            if other not in found.of:
                found.of[other] = found.of[aid]
                queue.append(other)
    return found


# -- Changing a collection in Shoko --------------------------------------------
#
# Every change is made to Shoko's group, which is what Jellyfin shows. Before
# the first change of a run of them, every group and which series it holds is
# written down, so a change that turns out wrong can be put back by hand.

_SNAPSHOT_KEEP = 20


def _snapshot_dir() -> Path:
    from bankai.cli import bgjobs

    return bgjobs.jobs_root().parent / "shoko_groups"


async def snapshot() -> Path:
    """Write down every Shoko group and its series; returns the file."""
    from bankai.web import shoko

    groups = await shoko._get("/api/v3/Group", pageSize=0, includeEmpty="true")
    series = await shoko._get("/api/v3/Series", pageSize=0)
    rows = groups.get("List", groups) if isinstance(groups, dict) else groups
    members = series.get("List", series) if isinstance(series, dict) else series
    record = {
        "taken_at": time.time(),
        "groups": [
            {"ids": row.get("IDs"), "name": row.get("Name"), "has_custom_name": row.get("HasCustomName")}
            for row in rows or []
        ],
        "series": [
            {"ids": row.get("IDs"), "name": row.get("Name")} for row in members or []
        ],
    }
    folder = _snapshot_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / time.strftime("groups-%Y%m%d-%H%M%S.json")
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    for old in sorted(folder.glob("groups-*.json"))[:-_SNAPSHOT_KEEP]:
        old.unlink(missing_ok=True)
    return path


async def set_title(group_id: int, series_id: int) -> dict:
    """Name and picture a collection after one of its series, as Shoko's preferred series.

    Returns the group as Shoko has it afterwards.
    """
    from bankai.web import shoko

    await snapshot()
    result = await shoko._send(
        "PATCH",
        f"/api/v3/Group/{int(group_id)}",
        json_body=[{"op": "replace", "path": "/PreferredSeriesID", "value": int(series_id)}],
    )
    if result is None:
        raise ValueError("Shoko has no such group")
    return await shoko._get(f"/api/v3/Group/{int(group_id)}")
