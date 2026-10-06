"""admind's host sockets (ADR 0001 §8, revision 13; plan 2b B6; relay spec R1, R24).

`admind operators add|remove NAME` and `admind rearm` run on the host as the service user and ask the
running daemon, which owns the wn-agent connection, over `ctl.sock`. `admind ask …` uses a second socket,
`ask.sock`, with its own limits and request types. Both are one JSON request per connection, one JSON reply,
served by `JsonSocketServer`. Each socket is 0600 inside the 0700 state directory; anything able to use it
can already act as admind.

Every connection is bounded: a reply its peer hasn't read in full (flushed, not just drained to the
high-water mark) within WRITE_SECONDS is dropped, a hang-up that doesn't finish within CLOSE_SECONDS (or a
handler cancelled meanwhile) aborts the connection, and `close()` waits CLOSE_SECONDS for the accepted
connections' handlers before cancelling them and aborting their connections. A connection is registered
the moment it is accepted, and one accepted once closing has begun is aborted at once, so none outlives
`close()`, even a cancelled one. `close()` returns within about twice CLOSE_SECONDS whatever the clients do.
"""

import asyncio
import contextlib
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import UnionType
from typing import Any, Literal, NamedTuple, overload

import msgspec

from heterodyne import platform
from heterodyne.admind.audit import Audit
from heterodyne.admind.settings import MAX_NAME
from heterodyne.admind.settings import NAME_CONTROLS as CONTROL  # one contract with policy (B2)
from heterodyne.admind.store import private_dir

CTL_SOCKET = "ctl.sock"
MAX_REQUEST = 4096
CTL_MAX_REPLY = 65_536
READ_SECONDS = 5.0
WRITE_SECONDS = 30.0        # a client that doesn't read its reply in this time is dropped
CLOSE_SECONDS = 2.0
INTERNAL_ERROR = "admind hit an internal error; see the audit log."


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["add", "remove", "rearm"]
    name: str | None = None


class CtlReply(msgspec.Struct, frozen=True):
    result: str
    message: str


class CtlUnavailable(Exception):
    """No daemon is listening (or it did not answer)."""


class Peer(NamedTuple):
    """The connecting process, as far as the kernel says (audit only; R16)."""
    pid: int | None


Handler = Callable[[CtlRequest], Awaitable[CtlReply]]


class JsonSocketServer[Req, Rep: msgspec.Struct]:
    """One JSON request per connection, one JSON reply. Every request-specific part is passed in: the
    request type and its limit, the refusal check, the replies for a refusal and a failure, the audit kind
    and the audit fields of a request, and the reply limit (a reply over it is replaced by `failed()`)."""

    def __init__(self, path: Path, handler: Callable[[Req, Peer], Awaitable[Rep]], audit: Audit, *,
                 kind: str, max_request: int, max_reply: int, request_type: type[Req] | UnionType,
                 check: Callable[[Req], str | None], refused: Callable[[str], Rep], failed: Callable[[], Rep],
                 describe: Callable[[Req], dict[str, object]]) -> None:
        self.path = path
        self.handler = handler
        self.audit = audit
        self.kind = kind
        self.max_request = max_request
        self.max_reply = max_reply
        self.request_type = request_type
        self.check = check
        self.refused = refused
        self.failed = failed
        self.describe = describe
        self.server: asyncio.Server | None = None
        self.created: tuple[int, ...] | None = None     # identity of the socket this instance bound
        self.handlers: dict[asyncio.Task[Any], asyncio.StreamWriter] = {}     # accepted connections
        self.closing = False

    async def start(self) -> None:
        """Bind the socket ourselves: asyncio's path-based start would follow a symlink at the path and
        remove a socket it points to. Only a stale real socket is replaced; anything else is refused."""
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if not stat.S_ISSOCK(os.lstat(self.path).st_mode):      # lstat: a symlink is not a socket
                raise FileExistsError(f"{self.path} exists and is not a socket")
            self.path.unlink()                                      # a stale socket from an earlier run
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(self.path))
            self.path.chmod(0o600)      # no umask change (it is process-wide); the 0700 parent covers the gap
            made = os.lstat(self.path)
            self.created = self.identity(made)
            sock.listen()
            sock.setblocking(False)
            self.closing = False
            self.server = await asyncio.start_unix_server(self._accept, sock=sock, limit=self.max_request + 2)
        except BaseException:
            sock.close()
            self.remove_own_socket()
            raise

    @staticmethod
    def identity(st: os.stat_result) -> tuple[int, ...]:
        """Device and inode, plus the change and modify times: a freed inode number is soon reused by the
        next socket bound at the same path, so the inode alone can't tell two instances' sockets apart."""
        return (st.st_dev, st.st_ino, st.st_ctime_ns, st.st_mtime_ns)

    def remove_own_socket(self) -> None:
        """Unlink the path only if it is still the socket this instance bound."""
        if self.created is None:
            return
        with contextlib.suppress(OSError):
            current = os.lstat(self.path)
            if stat.S_ISSOCK(current.st_mode) and self.identity(current) == self.created:
                self.path.unlink()
        self.created = None

    async def close(self) -> None:
        """Stop listening, give the accepted connections CLOSE_SECONDS, then cancel what is left and abort
        its connections. The cleanup runs even if `close()` itself is cancelled (it then re-raises)."""
        self.closing = True
        if self.server is not None:
            self.server.close()
        try:
            if self.handlers:
                await asyncio.wait(set(self.handlers), timeout=CLOSE_SECONDS)
        finally:
            self.drop_handlers()        # synchronous, so a cancelled close() still does it
            self.remove_own_socket()
        if self.handlers:
            await asyncio.wait(set(self.handlers), timeout=CLOSE_SECONDS)
        if self.server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), CLOSE_SECONDS)
        self.drop_handlers()            # any registered late

    def drop_handlers(self) -> None:
        for task, writer in list(self.handlers.items()):
            task.cancel()
            writer.transport.abort()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """The connection callback, run as the connection is made: register its handler before anything
        can await, so `close()` always sees it. Once closing has begun a new connection is aborted."""
        if self.closing:
            writer.transport.abort()
            return
        task = asyncio.get_running_loop().create_task(self._handle(reader, writer))
        self.handlers[task] = writer
        task.add_done_callback(lambda done: self.handlers.pop(done, None))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_SECONDS)
                if len(line.rstrip(b"\n")) > self.max_request:      # the delimiter does not count
                    raise ValueError("request too long")
                req: Req = msgspec.json.decode(line, type=self.request_type)
            except (TimeoutError, ValueError, msgspec.DecodeError):
                reply = self.refused("malformed request")
            else:
                refusal = self.check(req)
                if refusal is not None:
                    reply = self.refused(refusal)
                else:
                    fields = self.describe(req)
                    self.audit.write(self.kind, **fields)     # redacted centrally (B16)
                    try:
                        peer = Peer(platform.peer_pid(writer.get_extra_info("socket")))
                        reply = await self.handler(req, peer)
                    except Exception as exc:  # noqa: BLE001 - fixed wording; the type goes to the audit
                        self.audit.write(self.kind, action="handler-failed", op=fields.get("op"),
                                         error=type(exc).__name__)
                        reply = self.failed()
            data = msgspec.json.encode(reply)
            if len(data) > self.max_reply:
                self.audit.write(self.kind, action="reply-too-large", size=len(data))
                data = msgspec.json.encode(self.failed())
            writer.transport.set_write_buffer_limits(high=0)   # drain() returns once all of it is flushed
            writer.write(data + b"\n")
            await asyncio.wait_for(writer.drain(), WRITE_SECONDS)
        except Exception as exc:  # noqa: BLE001 - one bad client must not end the server
            with contextlib.suppress(Exception):     # the log may be what failed
                self.audit.write(self.kind, action="failed", error=type(exc).__name__)
        finally:
            await hang_up(writer)


async def hang_up(writer: asyncio.StreamWriter) -> None:
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


def check(req: CtlRequest) -> str | None:
    """Why a request is refused before it is logged or handled; None if it may go on. Any policy
    name is allowed (the daemon looks it up exactly), apart from control characters and length."""
    if req.op == "rearm":
        return None if req.name is None else "rearm takes no NAME"
    if req.name is None or not 1 <= len(req.name) <= MAX_NAME or CONTROL.search(req.name):
        return f"NAME must be an operator name from policy.toml (1-{MAX_NAME} printable characters)"
    return None


def CtlServer(path: Path, handler: Handler, audit: Audit) -> JsonSocketServer[CtlRequest, CtlReply]:  # noqa: N802 - it keeps the class's name for its callers
    """Exactly today's ctl.sock: kind "ctl", 4096 bytes, CtlRequest, the existing check, refusal and
    failure wording, and audit fields op/name. The handler ignores the peer."""
    async def handle(req: CtlRequest, _peer: Peer) -> CtlReply:
        return await handler(req)
    return JsonSocketServer(path, handle, audit, kind="ctl", max_request=MAX_REQUEST, max_reply=CTL_MAX_REPLY,
                            request_type=CtlRequest, check=check,
                            refused=lambda message: CtlReply("refused", message),
                            failed=lambda: CtlReply("failed", INTERNAL_ERROR),
                            describe=lambda req: {"op": req.op, "name": req.name})


@overload
async def request(path: Path, req: msgspec.Struct, timeout: float = ..., *,
                  max_reply: int = ...) -> CtlReply: ...
@overload
async def request[R](path: Path, req: msgspec.Struct, timeout: float = ..., *, reply_type: type[R],
                     max_reply: int = ...) -> R: ...
async def request(path: Path, req: msgspec.Struct, timeout: float = 300.0, *, reply_type: Any = CtlReply,
                  max_reply: int = CTL_MAX_REPLY) -> Any:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(path), limit=max_reply + 2), 5)
    except (OSError, TimeoutError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    try:
        writer.write(msgspec.json.encode(req) + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return msgspec.json.decode(line, type=reply_type)
    except (OSError, TimeoutError, ValueError, msgspec.DecodeError) as exc:     # ValueError: framing
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        await hang_up(writer)
