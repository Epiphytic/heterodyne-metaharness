"""admind's host control socket (ADR 0001 §8, revision 13; plan 2b B6).

`admind operators add|remove NAME` and `admind rearm` run on the host as the service user and ask the
running daemon, which owns the wn-agent connection. One JSON request per connection, one JSON reply.
The socket is 0600 inside the 0700 state directory; anything able to use it can already act as admind.
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

from heterodyne.admind.audit import Audit
from heterodyne.admind.settings import MAX_NAME
from heterodyne.admind.settings import NAME_CONTROLS as CONTROL  # one contract with policy (B2)
from heterodyne.admind.store import private_dir

CTL_SOCKET = "ctl.sock"
MAX_REQUEST = 4096
READ_SECONDS = 5.0
INTERNAL_ERROR = "admind hit an internal error; see the audit log."


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["add", "remove", "rearm"]
    name: str | None = None


class CtlReply(msgspec.Struct, frozen=True):
    result: str
    message: str


class CtlUnavailable(Exception):
    """No daemon is listening (or it did not answer)."""


Handler = Callable[[CtlRequest], Awaitable[CtlReply]]


class CtlServer:
    def __init__(self, path: Path, handler: Handler, audit: Audit) -> None:
        self.path = path
        self.handler = handler
        self.audit = audit
        self.server: asyncio.Server | None = None
        self.created: tuple[int, ...] | None = None     # identity of the socket this instance bound

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
            self.server = await asyncio.start_unix_server(self._handle, sock=sock, limit=MAX_REQUEST + 2)
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
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        self.remove_own_socket()

    @staticmethod
    def check(req: CtlRequest) -> str | None:
        """Why a request is refused before it is logged or handled; None if it may go on. Any policy
        name is allowed (the daemon looks it up exactly), apart from control characters and length."""
        if req.op == "rearm":
            return None if req.name is None else "rearm takes no NAME"
        if req.name is None or not 1 <= len(req.name) <= MAX_NAME or CONTROL.search(req.name):
            return f"NAME must be an operator name from policy.toml (1-{MAX_NAME} printable characters)"
        return None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_SECONDS)
                if len(line.rstrip(b"\n")) > MAX_REQUEST:      # the delimiter does not count
                    raise ValueError("request too long")
                req = msgspec.json.decode(line, type=CtlRequest)
            except (TimeoutError, ValueError, msgspec.DecodeError):
                reply = CtlReply("refused", "malformed request")
            else:
                refusal = self.check(req)
                if refusal is not None:
                    reply = CtlReply("refused", refusal)
                else:
                    self.audit.write("ctl", op=req.op, name=req.name)     # redacted centrally (B16)
                    try:
                        reply = await self.handler(req)
                    except Exception as exc:  # noqa: BLE001 - fixed wording; the type goes to the audit
                        self.audit.write("ctl", action="handler-failed", op=req.op, error=type(exc).__name__)
                        reply = CtlReply("failed", INTERNAL_ERROR)
            writer.write(msgspec.json.encode(reply) + b"\n")
            await writer.drain()
        except Exception as exc:  # noqa: BLE001 - one bad client must not end the server
            with contextlib.suppress(Exception):     # the log may be what failed
                self.audit.write("ctl", action="failed", error=type(exc).__name__)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def request(path: Path, req: CtlRequest, timeout: float = 300.0) -> CtlReply:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 5)
    except (OSError, TimeoutError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    try:
        writer.write(msgspec.json.encode(req) + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return msgspec.json.decode(line, type=CtlReply)
    except (OSError, TimeoutError, ValueError, msgspec.DecodeError) as exc:     # ValueError: framing
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
