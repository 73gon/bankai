"""The UI's own settings, kept on the server so every device has them.

A small store of key -> JSON value (``ui_prefs.json`` in the state directory):
where the library's split divider sits, and the like. The page keeps its own
copy in the browser and writes here in the background, so a slow answer never
holds it up; another device reads it on its next visit.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from bankai.cli import bgjobs

_LOCK = threading.Lock()
_KEY = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,79}$")
# A setting, not a document: anything bigger is a mistake.
MAX_VALUE_BYTES = 4096


def _path():
    return bgjobs.jobs_root().parent / "ui_prefs.json"


def load() -> dict[str, Any]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save(key: str, value: Any) -> None:
    """Keep one setting; ``None`` forgets it. Raises ValueError for a bad key or an oversized value."""
    if not _KEY.match(key):
        raise ValueError("A setting's name is lowercase letters, digits and . _ : -")
    if value is not None and len(json.dumps(value, separators=(",", ":"))) > MAX_VALUE_BYTES:
        raise ValueError("That setting is too large")
    with _LOCK:
        prefs = load()
        if value is None:
            prefs.pop(key, None)
        else:
            prefs[key] = value
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(prefs, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
