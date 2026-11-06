"""Library files that took another's place by hand (Replace with magnet link), for the Dashboard.

Recently added marks them "Replaced", dated when the new file came in. Written by the anime
publish (erai._finish_replacement) and the Movies & Shows download (processor.direct), which
runs in a process of its own: so a small file, replaced whole.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bankai.cli import bgjobs

_KEPT = 500


def _path() -> Path:
    return bgjobs.jobs_root().parent / "replaced_files.json"


def load() -> dict[str, dict[str, Any]]:
    """Path -> {"at", "source"}: the newest replacements."""
    try:
        rows = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rows if isinstance(rows, dict) else {}


def record(paths: Iterable[str | Path], source: str) -> None:
    """These files came in in place of others now; ``source`` is the release that brought them."""
    rows = load()
    now = time.time()
    for path in paths:
        rows.pop(str(path), None)
        rows[str(path)] = {"at": now, "source": source}
    kept = dict(list(rows.items())[-_KEPT:])
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


__all__ = ["load", "record"]
