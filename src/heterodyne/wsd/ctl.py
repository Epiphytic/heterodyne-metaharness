"""wsd's host control socket (ADR 0001 §4.3, §9): `wsd tick <job>` from the timers, and `wsctl pause`,
`resume` and `status`.

One JSON request per connection, one JSON reply. The socket is 0600 inside the 0700 wsd state directory;
anything able to use it can already act as wsd.
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
READ_SECONDS = 5.0


def _no_data() -> dict[str, dict[str, str]]:
    return {}


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["tick", "status", "pause", "resume"]
    job: Literal["pickup", "reconcile"] | None = None      # tick only
    ws: str | None = None       # None: every workstream (tick and status); pause and resume need one


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
    return ""


class CtlServer:
    def __init__(self, path: Path, handler: Handler) -> None:
        self.path = path
        self.handler = handler
        self.server: asyncio.Server | None = None
        self.created: tuple[int, int] | None = None

    async def start(self) -> None:
        """Bind the socket ourselves, so a symlink at the path is refused rather than followed. Only a
        stale real socket is replaced. The caller holds the instance lock, so no live wsd owns it."""
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if not stat.S_ISSOCK(os.lstat(self.path).st_mode):
                raise FileExistsError("the control socket path exists and is not a socket")
            self.path.unlink()
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(self.path))
            self.path.chmod(0o600)
            made = os.lstat(self.path)
            self.created = (made.st_dev, made.st_ino)
            sock.listen()
            sock.setblocking(False)
            self.server = await asyncio.start_unix_server(self._handle, sock=sock, limit=MAX_REQUEST + 2)
        except BaseException:
            sock.close()
            raise

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        with contextlib.suppress(OSError):
            current = os.lstat(self.path)
            if (current.st_dev, current.st_ino) == self.created:
                self.path.unlink()

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
                why = refusal(req)
                if why:
                    reply = CtlReply("refused", why)
                else:
                    try:
                        reply = await self.handler(req)
                    except Exception as exc:  # noqa: BLE001 - fixed wording; the type is enough
                        reply = CtlReply("failed", f"wsd hit an internal error ({type(exc).__name__})")
            writer.write(msgspec.json.encode(reply) + b"\n")
            await writer.drain()
        except Exception:  # noqa: BLE001, S110 - one bad client must not end the server
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def request(path: Path, req: CtlRequest, timeout: float = 600.0) -> CtlReply:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 5)
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
