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


async def cancel_all() -> None:
    """Cancel every outstanding background task and wait for them to unwind.

    Called from call shutdown: the late-match announcer polls the station for
    ~50s after a request, and if the caller hangs up mid-poll it would go on
    writing receipts to a record that was already finalised and never rewritten
    (0.10.57 review) — the receipts were silently lost and the polling was
    wasted. The worker process is one call, so this cancels only this call's
    tasks; the record write in _on_shutdown runs concurrently and captures the
    committed transcript regardless.
    """
    tasks = list(_background)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
