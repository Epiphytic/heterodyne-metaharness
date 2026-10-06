"""Bounded cleanup of a child admind started in its own process group (summarizer, approve-bead; R23).

On every exit path the whole group is killed (a group already gone is fine), both pipes are drained and
the child is waited for, bounded at `seconds`; then the pipe transport is closed whatever happened. A
descendant that left the group and still holds a pipe costs `seconds`, not a hang. Nothing here reports
an exception's text: only fixed words and its type.
"""

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import Any

READ_CHUNK = 65536

Drain = Callable[[asyncio.subprocess.Process], Awaitable[None]]


def kill(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child's process group, unconditionally: the child may have exited while a descendant
    in its group (a `bd` it started) is still running (r3-1)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


async def _discard(stream: asyncio.StreamReader | None) -> None:
    if stream is not None:
        while await stream.read(READ_CHUNK):
            pass


async def drain(proc: asyncio.subprocess.Process) -> None:
    """Close stdin and read stdout and stderr to their ends, discarding them, then wait. asyncio stops
    reading a pipe that holds more than its buffer limit unread, and then never sees the pipe close:
    `wait()` would not return (r4-1)."""
    if proc.stdin is not None:
        proc.stdin.close()
    await asyncio.gather(_discard(proc.stdout), _discard(proc.stderr))
    await proc.wait()


def note(message: str, exc: BaseException) -> None:
    """Say what failed in cleanup: fixed words and the exception's type only, never its text."""
    with contextlib.suppress(Exception):  # best-effort: a broken stderr must not replace the real error
        print(f"{message} ({type(exc).__name__})", file=sys.stderr)


def close_transport(proc: asyncio.subprocess.Process, what: str) -> None:
    """Release the pipe descriptors even if a descendant still holds the other end. asyncio has no public
    call for this; `Process._transport` is CPython's (requires-python >= 3.12), so it is looked up
    defensively and a failure here never hides the error being handled."""
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        try:
            transport.close()
        except Exception as exc:  # noqa: BLE001 - as in reap
            note(f"{what} pipe close failed", exc)


async def reap(proc: asyncio.subprocess.Process, what: str, *, seconds: float, drain: Drain = drain) -> None:
    """Kill the group, then collect the child within `seconds`. The pipes are closed whatever happens; a
    cancellation propagates after that, and any other failure of the cleanup is noted and dropped so it
    can't replace the error being handled."""
    kill(proc)
    try:
        await asyncio.wait_for(drain(proc), seconds)
    except TimeoutError:
        pass  # expected when a descendant outside the group still holds a pipe; closed below
    except Exception as exc:  # noqa: BLE001 - cleanup must not replace the error being handled
        note(f"{what} cleanup failed", exc)
    finally:
        close_transport(proc, what)


async def spawn(what: str, argv: list[str], *, seconds: float, **kwargs: Any) -> asyncio.subprocess.Process:
    """`asyncio.create_subprocess_exec(*argv, **kwargs)`, owned through a cancellation. A cancellation that
    lands while the pipes are being set up would make asyncio kill only the direct child, leaving the rest
    of its group running; instead the creation is let finish, its group is reaped (`reap_shielded`, bounded
    by `seconds`), and then the cancellation propagates. A creation failure (OSError) propagates as it is,
    unless the caller was cancelled meanwhile."""
    task = asyncio.ensure_future(asyncio.create_subprocess_exec(*argv, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.wait({task})      # unlike awaiting the task, a cancellation here leaves it running
        except asyncio.CancelledError:
            cancelled = True
    if not cancelled:
        return task.result()
    if not task.cancelled() and task.exception() is None:
        await reap_shielded(task.result(), what, seconds=seconds)
    raise asyncio.CancelledError


async def reap_shielded(proc: asyncio.subprocess.Process, what: str, *, seconds: float,
                        drain: Drain = drain) -> None:
    """`reap`, finished even if the caller is cancelled (once or more) meanwhile; the cancellation is
    re-raised afterwards. It stays bounded: `reap` itself is bounded by `seconds`."""
    task = asyncio.ensure_future(reap(proc, what, seconds=seconds, drain=drain))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    task.result()
