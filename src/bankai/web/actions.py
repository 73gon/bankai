"""The user's own actions, run in the background: Discard, delete, Restore, Remove.

A decision that deletes files or rewrites the release state can take seconds;
held inside the HTTP request, the page waited on it and nothing showed what
was happening. Each is now a task here: the request answers at once with the
action's id, the page asks after it until it is done, and the dashboard lists
what is running and what just finished.

Kept in memory, in the web process: an action is seconds to minutes long,
and one that a restart interrupts is simply started again.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from typing import Any

from bankai.logging import get_logger

log = get_logger(__name__)

# Finished actions kept for the dashboard and for pages still asking after them.
_KEEP_FINISHED = 30
# A finished action is shown on the dashboard for this long.
RECENT_SECONDS = 15 * 60.0


@dataclass
class Action:
    id: str
    kind: str
    title: str
    detail: str | None = None
    status: str = "running"  # running | done | failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_ACTIONS: OrderedDict[str, Action] = OrderedDict()
_TASKS: dict[str, asyncio.Task] = {}


def _trim() -> None:
    finished = [key for key, action in _ACTIONS.items() if action.status != "running"]
    for key in finished[: max(0, len(finished) - _KEEP_FINISHED)]:
        _ACTIONS.pop(key, None)


def start(
    kind: str,
    title: str,
    work: Callable[[Action], Awaitable[dict[str, Any]]],
    *,
    on_done: Callable[[], None] | None = None,
) -> Action:
    """Run ``work`` in the background; it may set ``action.detail`` as it goes.

    ``on_done`` runs once the work is over and before the action reads as
    finished, so a page reloading on "done" sees the change.
    """
    action = Action(id=uuid.uuid4().hex[:12], kind=kind, title=title)
    _ACTIONS[action.id] = action
    _trim()

    async def run() -> None:
        status, result, error = "failed", None, None
        try:
            result = await work(action)
            status = "done"
        except Exception as exc:  # the page shows it; nothing is retried
            error = str(exc) or type(exc).__name__
            log.warning("%s failed: %s", title, error)
        finally:
            if on_done is not None:
                with suppress(Exception):
                    on_done()
            action.result, action.error = result, error
            action.detail = None
            action.finished_at = time.time()
            action.status = status

    task = asyncio.create_task(run(), name=f"action:{kind}")
    _TASKS[action.id] = task
    task.add_done_callback(lambda _task: _TASKS.pop(action.id, None))
    return action


async def wait(action: Action) -> Action:
    """Until the action is over, for a caller that wants its answer in the reply."""
    task = _TASKS.get(action.id)
    if task is not None:
        await asyncio.shield(task)
    return action


def get(action_id: str) -> Action | None:
    return _ACTIONS.get(action_id)


def listing(now: float | None = None) -> list[dict[str, Any]]:
    """Running actions, then those finished lately, newest first."""
    now = time.time() if now is None else now
    rows = [
        action.as_dict()
        for action in _ACTIONS.values()
        if action.status == "running" or now - (action.finished_at or 0) < RECENT_SECONDS
    ]
    return sorted(rows, key=lambda row: (row["status"] != "running", -row["started_at"]))
