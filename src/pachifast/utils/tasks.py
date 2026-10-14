"""Background tasks that nobody awaits."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_running: set[asyncio.Task[Any]] = set()  # strong references: the event loop keeps only weak ones


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """``asyncio.create_task`` that keeps the task alive until it ends.

    A bare ``create_task`` result that nobody holds can be garbage collected while it is still running, and the
    work (an event broadcast, an audit row) silently never finishes.
    """
    task = asyncio.create_task(coro)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task
