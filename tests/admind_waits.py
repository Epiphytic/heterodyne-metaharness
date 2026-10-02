"""Bounded waits for the admind tests: a predicate wait that fails loudly on timeout, and a bounded
observation that a condition keeps holding. Neither depends on how many scheduler turns a callback needs."""

import asyncio
from collections.abc import Callable


async def wait_until(pred: Callable[[], object], timeout: float = 10.0) -> None:
    """Return once `pred()` is true; fail (not hang) after `timeout` real seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        assert loop.time() < end, "condition not reached in time"
        await asyncio.sleep(0.001)


async def stays(pred: Callable[[], object], seconds: float = 0.05) -> None:
    """Assert that `pred()` holds for the whole of a short real-time window. Call it only after a
    `wait_until` has shown the system reached the state whose consequences are being ruled out."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    assert pred(), "condition does not hold"        # checked even if the window has already passed
    while loop.time() < end:
        await asyncio.sleep(0.001)
        assert pred(), "condition stopped holding"


def lock_waiters(lock: asyncio.Lock) -> int:
    """How many tasks are parked on `lock` (asyncio keeps them in a private deque)."""
    return len(getattr(lock, "_waiters", None) or ())
