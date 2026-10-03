"""admind's host control socket (ADR 0001 §8, revision 13; plan 2b B6).

`admind operators add|remove NAME` and `admind rearm` run on the host as the service user and ask the
running daemon, which owns the wn-agent connection. One JSON request per connection, one JSON reply.
The socket is 0600 inside the 0700 state directory; anything able to use it can already act as admind.
"""

import asyncio
import contextlib
import os
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

    async def start(self) -> None:
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISSOCK(os.lstat(self.path).st_mode):    # a stale socket from an earlier run
                self.path.unlink()
        self.server = await asyncio.start_unix_server(self._handle, path=str(self.path),
                                                      limit=MAX_REQUEST + 1)
        self.path.chmod(0o600)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

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
    except (OSError, TimeoutError, msgspec.DecodeError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
