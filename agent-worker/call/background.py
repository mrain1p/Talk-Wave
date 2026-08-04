"""Fire-and-forget tasks that actually survive.

`asyncio.create_task` alone is not enough: the event loop keeps only a weak
reference, so a task with no other reference can be garbage-collected
mid-execution. For us that meant an action card or an on-air state change
going missing at random — worse than one that never existed, because it looks
like the feature works.
"""

from __future__ import annotations

import asyncio

_background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """Run a coroutine in the background, holding a reference until it ends."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task
