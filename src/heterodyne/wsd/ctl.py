"""wsd's host control socket (ADR 0001 §4.3, §9): `wsd tick <job>` from the timers, and `wsctl pause`,
`resume`, `status` and `reload` (AU-4 §5.2).

One JSON request per connection, one JSON reply, each a single newline-terminated line. A request is at
most MAX_REQUEST bytes and a reply at most MAX_REPLY bytes; the client reads up to MAX_REPLY, and a reply
that would be longer is replaced by a "failed" reply saying so (TOO_LARGE), never cut off. The socket is
0600 inside the 0700 wsd state directory; anything able to use it can already act as wsd.

Closing stops listening, refuses any request still to be read, and waits up to CLOSE_SECONDS for the
accepted connections' handlers before cancelling them, so none outlives `close()`. Hanging up is bounded
too: a connection whose peer stops reading is aborted after CLOSE_SECONDS, so `close()` returns within
about twice CLOSE_SECONDS whatever the clients do.
"""

import asyncio
import contextlib
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.fsutil import private_dir

MAX_REQUEST = 4096
MAX_REPLY = 1 << 20         # 1 MiB; status leaves finished beads out unless asked, to stay well inside it
READ_SECONDS = 5.0
CLOSE_SECONDS = 2.0
WRITE_SECONDS = 30.0        # a client that doesn't read its reply in this time is dropped
TOO_LARGE = "reply too large"


def _no_data() -> dict[str, dict[str, str]]:
    return {}


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["tick", "status", "pause", "resume", "reload"]
    job: Literal["pickup", "reconcile"] | None = None      # tick only
    ws: str | None = None       # None: every workstream (tick and status); pause and resume need one,
    #                             and reload takes none
    all: bool = False           # status only: list closed and dropped beads too


class CtlReply(msgspec.Struct, frozen=True):
    result: Literal["ok", "refused", "failed"]
    message: str
    data: dict[str, dict[str, str]] = msgspec.field(default_factory=_no_data)


class CtlUnavailable(Exception):
    """No wsd is listening (or it did not answer)."""


Handler = Callable[[CtlRequest], Awaitable[CtlReply]]


def refusal(req: CtlRequest) -> str:
    """Why a well-formed request can't be served, or "" if it can. The handler checks again: plan 6 may
    call it without the socket."""
    if (req.op == "tick") != (req.job is not None):
        return "tick needs a job; nothing else takes one"
    if req.op in ("pause", "resume") and req.ws is None:
        return f"{req.op} needs a workstream"
    if req.op == "reload" and req.ws is not None:
        return "reload takes no workstream; it reloads them all"
    if req.all and req.op != "status":
        return "only status takes all"
    return ""


class SocketPathTaken(FileExistsError):
    """Something other than a socket is at the control socket's path; wsd won't remove it."""


class CtlServer:
    def __init__(self, path: Path, handler: Handler) -> None:
        self.path = path
        self.handler = handler
        self.server: asyncio.Server | None = None
        self.created: tuple[int, int] | None = None
        self.closing = False
        self.handlers: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Bind the socket ourselves, so a symlink at the path is refused rather than followed. Only a
        stale real socket is replaced. The caller holds the instance lock, so no live wsd owns it."""
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if not stat.S_ISSOCK(os.lstat(self.path).st_mode):
                raise SocketPathTaken(f"{self.path} exists and is not a socket")
            self.path.unlink()
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(self.path))
            self.path.chmod(0o600)
            made = os.lstat(self.path)
            self.created = (made.st_dev, made.st_ino)
            sock.listen()
            sock.setblocking(False)
            self.server = await asyncio.start_unix_server(self._track, sock=sock, limit=MAX_REQUEST + 2)
        except BaseException:
            sock.close()
            raise

    def stop_accepting(self) -> None:
        """Stop listening and refuse every request not yet read; accepted handlers carry on."""
        self.closing = True
        if self.server is not None:
            self.server.close()

    async def close(self) -> None:
        self.stop_accepting()
        if self.handlers:
            await asyncio.wait(set(self.handlers), timeout=CLOSE_SECONDS)
        for task in set(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        with contextlib.suppress(OSError):
            current = os.lstat(self.path)
            if (current.st_dev, current.st_ino) == self.created:
                self.path.unlink()

    async def _track(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()       # the server runs each connection in its own task
        if task is not None:
            self.handlers.add(task)
        try:
            await self._handle(reader, writer)
        finally:
            self.handlers.discard(task)  # pyright: ignore[reportArgumentType]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_SECONDS)
                if len(line.rstrip(b"\n")) > MAX_REQUEST:
                    raise ValueError("request too long")
                req = msgspec.json.decode(line, type=CtlRequest)
            except (TimeoutError, ValueError, msgspec.DecodeError):
                reply = CtlReply("refused", "malformed request")
            else:
                why = "wsd is stopping; nothing more was started" if self.closing else refusal(req)
                if why:
                    reply = CtlReply("refused", why)
                else:
                    try:
                        reply = await self.handler(req)
                    except Exception as exc:  # noqa: BLE001 - fixed wording; the type is enough
                        reply = CtlReply("failed", f"wsd hit an internal error ({type(exc).__name__})")
            writer.write(frame(reply))
            await asyncio.wait_for(writer.drain(), WRITE_SECONDS)
        except Exception:  # noqa: BLE001, S110 - one bad client must not end the server
            pass
        finally:
            await _hang_up(writer)


async def _hang_up(writer: asyncio.StreamWriter) -> None:
    """Close a connection within CLOSE_SECONDS. A peer that stops reading would hold a graceful close
    (which flushes what is buffered) open forever, so one that doesn't finish in time, or a handler
    cancelled meanwhile, aborts the connection and drops what is left unsent."""
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), CLOSE_SECONDS)
    except asyncio.CancelledError:
        writer.transport.abort()
        raise
    except Exception:  # noqa: BLE001 - timed out or failed: either way the peer is dropped
        writer.transport.abort()


def frame(reply: CtlReply) -> bytes:
    """One reply line, never over MAX_REPLY bytes: a longer one is refused as a whole, not truncated."""
    line = msgspec.json.encode(reply) + b"\n"
    if len(line) > MAX_REPLY:
        line = msgspec.json.encode(CtlReply(
            "failed", f"{TOO_LARGE}: {len(line)} bytes, more than {MAX_REPLY}; ask about one workstream, "
                      "or leave out --all")) + b"\n"
    return line


async def request(path: Path, req: CtlRequest, timeout: float = 600.0) -> CtlReply:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(path), limit=MAX_REPLY + 1), 5)
    except (OSError, TimeoutError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    try:
        writer.write(msgspec.json.encode(req) + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return msgspec.json.decode(line, type=CtlReply)
    except (OSError, TimeoutError, ValueError, msgspec.DecodeError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
