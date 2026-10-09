"""Children admind starts in their own process group (summarizer, approve-bead; R23), owned from the launch.

`spawn` launches synchronously (`subprocess.Popen` with `start_new_session=True`), so no await can come
between a caller's last check and the launch (R11), and the moment it returns the child's PID is its
group's ID: the whole group is ours to kill. If it raises OSError there is no child: either nothing was
forked, or the exec failed and Popen collected that child itself. Everything after `spawn` belongs in one
try whose finally is `reap_shielded`: `Child.connect` (attaching the pipes to the loop) may fail or be
cancelled with the child already running, and the cleanup is the same on every path.

`reap` kills the group, closes the pipes (so a descendant that left the group and holds one costs nothing)
and waits for the child's exit, bounded at `seconds`. Nothing here reports an exception's text: only fixed
words and its type.
"""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import IO

READ_CHUNK = 65536


def _settle(exited: "asyncio.Future[int]", code: int) -> None:
    if not exited.done():
        exited.set_result(code)


class Child:
    """A child `spawn` started, in its own process group. Its streams exist once `connect` returned."""

    def __init__(self, popen: "subprocess.Popen[bytes]") -> None:
        self.popen = popen
        self.pid = popen.pid
        self.stdin: asyncio.StreamWriter | None = None
        self.stdout: asyncio.StreamReader | None = None
        self.stderr: asyncio.StreamReader | None = None
        self.transports: list[asyncio.BaseTransport] = []
        self._exited: asyncio.Future[int] | None = None

    @property
    def returncode(self) -> int | None:
        return self.popen.returncode

    def _watch(self) -> "asyncio.Future[int]":
        """The child's exit, as a future set by a thread that waits for it (portable: no pidfd, no child
        watcher). The thread is a daemon, so a child that never exits can't hold up admind's exit."""
        if self._exited is None:
            loop = asyncio.get_running_loop()
            exited: asyncio.Future[int] = loop.create_future()

            def wait() -> None:
                code = self.popen.wait()
                with contextlib.suppress(RuntimeError):     # the loop has closed meanwhile
                    loop.call_soon_threadsafe(_settle, exited, code)
            threading.Thread(target=wait, name=f"wait-{self.pid}", daemon=True).start()
            self._exited = exited
        return self._exited

    async def connect(self) -> None:
        """Attach the pipes to the loop as streams. It awaits, so it can fail or be cancelled midway with the
        child running: call it inside the try whose finally reaps."""
        self._watch()
        loop = asyncio.get_running_loop()
        if self.popen.stdin is not None:
            transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin,
                                                                self.popen.stdin)
            self.transports.append(transport)
            self.stdin = asyncio.StreamWriter(transport, protocol, None, loop)
        self.stdout = await self._reader(loop, self.popen.stdout)
        self.stderr = await self._reader(loop, self.popen.stderr)

    async def _reader(self, loop: asyncio.AbstractEventLoop,
                      pipe: IO[bytes] | None) -> asyncio.StreamReader | None:
        if pipe is None:
            return None
        reader = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), pipe)
        self.transports.append(transport)
        return reader

    async def wait(self) -> int:
        """The child's exit status. Cancelling this leaves the wait for its exit running (for `reap`)."""
        return await asyncio.shield(self._watch())


Wait = Callable[[Child], Awaitable[object]]


def spawn(argv: list[str], *, cwd: Path | None = None, feed: bool = False, stderr: bool = True,
          env: Mapping[str, str] | None = None) -> Child:
    """Launch `argv` in a new session (its own process group): stdin a pipe if `feed`, else /dev/null;
    stdout a pipe; stderr a pipe, or /dev/null; `env` its whole environment, or admind's if None.
    Synchronous; OSError means there is no child.

    The launch runs before any run timeout starts and blocks the event loop until the exec has completed
    (Popen waits for it), so an executable on a stalled filesystem can stall admind here. That is accepted:
    Python's own subprocess timeouts don't cover the initial process creation either, and a synchronous
    launch is what keeps any await from coming between the last check and the launch (R11)."""
    pipe, null = subprocess.PIPE, subprocess.DEVNULL
    return Child(subprocess.Popen(argv, cwd=cwd, start_new_session=True, stdin=pipe if feed else null,
                                  stdout=pipe, stderr=pipe if stderr else null, env=env))


def kill(child: Child) -> None:
    """SIGKILL the child's process group, unconditionally: the child may have exited while a descendant
    in its group (a `bd` it started) is still running (r3-1)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(child.pid, signal.SIGKILL)


def note(message: str, exc: BaseException) -> None:
    """Say what failed in cleanup: fixed words and the exception's type only, never its text."""
    with contextlib.suppress(Exception):  # best-effort: a broken stderr must not replace the real error
        print(f"{message} ({type(exc).__name__})", file=sys.stderr)


def close_pipes(child: Child, what: str) -> None:
    """Release every pipe descriptor, whoever holds the other ends: the loop's transports (dropped from the
    loop at once, unflushed input discarded), then any pipe no transport took because `connect` failed or
    was cancelled before it. A failure is noted, never raised: it can't hide the error being handled."""
    for transport in child.transports:
        try:
            if isinstance(transport, asyncio.WriteTransport):
                if transport.get_write_buffer_size() or not transport.is_closing():   # else closed already
                    transport.abort()
            else:
                transport.close()
        except Exception as exc:  # noqa: BLE001 - as above
            note(f"{what} pipe close failed", exc)
    for pipe in (child.popen.stdin, child.popen.stdout, child.popen.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except Exception as exc:  # noqa: BLE001 - as above
                note(f"{what} pipe close failed", exc)


async def wait_exit(child: Child) -> None:
    await child.wait()


async def reap(child: Child, what: str, *, seconds: float, wait: Wait = wait_exit) -> None:
    """Kill the group, close the pipes, then wait for the child's exit within `seconds`. A cancellation
    propagates; any other failure of the cleanup (the wait running out included) is noted and dropped so it
    can't replace the error being handled."""
    kill(child)
    close_pipes(child, what)
    try:
        await asyncio.wait_for(wait(child), seconds)
    except Exception as exc:  # noqa: BLE001 - cleanup must not replace the error being handled
        note(f"{what} cleanup failed", exc)


async def reap_shielded(child: Child, what: str, *, seconds: float, wait: Wait = wait_exit) -> None:
    """`reap`, finished even if the caller is cancelled (once or more) meanwhile; the cancellation is
    re-raised afterwards. It stays bounded: `reap` itself is bounded by `seconds`."""
    task = asyncio.ensure_future(reap(child, what, seconds=seconds, wait=wait))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    task.result()
