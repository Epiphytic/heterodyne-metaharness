"""The admin agent's hooks reach admind over a private Unix socket (ADR 0001 §2, §8).

Claude Code runs `python -m heterodyne.admind hook --socket <path>` on SessionStart, UserPromptSubmit
and Stop. The hook connects first (nothing else runs before `connect`), forwards its stdin JSON as one
line, waits (up to 8s) for admind's answer, and **always exits 0** with nothing on stdout: a Stop hook that
exits 2 would block the agent from stopping, a UserPromptSubmit hook's stdout would be added to the prompt,
and admind being down must never wedge the admin agent (it is the recovery path).

Ordering is Claude Code's, not a counter's. Claude Code runs these command hooks synchronously: it waits
for the hook to exit (or kills it at its `timeout`) before it processes the prompt or finishes the turn,
and it does not run Stop on a user interrupt. admind processes events strictly one at a time in the order
the kernel accepted their connections, and answers `ok` only after the event's effects are applied, so the
hook exits only then. A hook is never configured as async (the generated settings entry has none).
The agent's reply is the Stop hook's `last_assistant_message`. If a Claude Code build omits that
optional field, the fallback is the last assistant text in the session's own transcript file
(plan decision D2). The screen is never scraped.

Trust boundary: the socket is 0600 in a 0700 directory, so only the service user can write to it, and
events for any session but the current one are dropped. Each launch of the agent has its own nonce in its
hook command (`--launch`); the daemon treats an event from any other launch, or with no nonce, as stale.
A process already running as the service user can still forge a reply event; that is the same-user
residual risk ADR §3.4 accepts.
"""

import argparse
import asyncio
import contextlib
import json
import os
import secrets
import select
import shlex
import socket
import stat
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import msgspec

from heterodyne.admind.audit import Audit

MAX_HOOK_FRAME = 1024 * 1024
MAX_TRANSCRIPT = 8 * 1024 * 1024    # the fallback reads at most this much from the end of the file
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
ACK_SECONDS = 8.0       # the hook's total wait for admind's answer
HOOK_TIMEOUT = 15       # Claude Code kills the hook only after the client above has given up
FRAME_SECONDS = 5.0     # admind's wait for one connection's frame before it drops that connection


class HookEvent(msgspec.Struct, frozen=True):
    hook_event_name: str
    session_id: str
    transcript_path: str | None = None
    last_assistant_message: str | None = None
    prompt: str | None = None
    source: str | None = None      # SessionStart only: startup, resume, clear or compact
    # Set by hook_main, never by the agent's own payload. Its shape is enforced when a frame is decoded, so
    # a malformed value drops that frame instead of reaching a comparison. (A frame from an older hook may
    # carry a `seq` field; unknown fields are ignored by the decoder.)
    launch: Annotated[str, msgspec.Meta(pattern=r"\A[0-9a-f]{32}\Z")] | None = None   # the launch nonce


@dataclass
class Delivery:
    """An event handed to hook_loop together with the future the connection handler waits on: it resolves
    True once the event's effects are applied, False if processing failed."""
    event: HookEvent
    done: "asyncio.Future[bool]"
    arrival: int | None = None      # the connection's accept-order index; never taken from the frame


def same_prompt(a: str, b: str) -> bool:
    """Whether a UserPromptSubmit `prompt` is the text admind pasted (whitespace-insensitive)."""
    return a.split() == b.split()


def new_launch_nonce() -> str:
    """A fresh identifier for one agent launch: 128 random bits as 32 hex characters (not 64, which the
    identifier scanner would redact). It correlates hook events with the launch that produced them."""
    return secrets.token_hex(16)


def hook_command(sock: Path, launch: str) -> str:
    return (f"{shlex.quote(sys.executable)} -m heterodyne.admind hook --socket {shlex.quote(str(sock))} "
            f"--launch {shlex.quote(launch)}")


def settings_json(command: str) -> str:
    """The hook settings for the agent. The ordering guarantee depends on these hooks being synchronous:
    never add `"async": true`. `timeout` is above the client's own bound, so Claude Code kills the hook
    only after the hook has given up."""
    entry = [{"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}]}]
    return json.dumps({"hooks": {event: entry for event in HOOK_EVENTS}}, indent=2)


def hook_main(argv: list[str], stdin: bytes) -> int:
    parser = argparse.ArgumentParser(prog="admind hook", add_help=False)
    parser.add_argument("--socket")
    parser.add_argument("--launch")
    try:
        args, _ = parser.parse_known_args(argv)     # (an old command's --seq-file is ignored)
    except SystemExit:      # argparse exits 2 on a malformed flag; a hook never fails the agent
        args = argparse.Namespace(socket=None, launch=None)
    if not args.socket:
        print("admind hook: --socket is required", file=sys.stderr)
        return 0
    deadline = time.monotonic() + ACK_SECONDS
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(ACK_SECONDS)
            conn.connect(args.socket)   # first: admind orders events by when their connections arrive
            payload: Any = json.loads(stdin)
            if isinstance(payload, dict):
                # The nonce comes from the command line admind wrote, never from the agent's own
                # payload; a frame without one is treated as stale by the daemon.
                frame = cast(dict[str, Any], payload)
                frame.pop("launch", None)
                if args.launch:
                    frame["launch"] = args.launch
            line = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
            conn.settimeout(max(0.001, deadline - time.monotonic()))
            conn.sendall(line)
            conn.settimeout(max(0.001, deadline - time.monotonic()))
            conn.recv(16)   # admind's answer, sent after the event was processed (or a timeout)
    except (OSError, ValueError) as exc:
        print(f"admind hook: not delivered ({type(exc).__name__})", file=sys.stderr)
    return 0


def _blocks(record: dict[str, Any]) -> list[dict[str, Any]] | str | None:
    message: Any = record.get("message")
    content: Any = cast(dict[str, Any], message).get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [cast(dict[str, Any], b) for b in cast(list[Any], content) if isinstance(b, dict)]
    return None


def _read_tail(path: Path, limit: int) -> str | None:
    """The last `limit` bytes of a regular file, or None. The path comes from a hook payload, so it is
    opened without following symlinks and without blocking (a FIFO would hang a plain open)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        start = max(0, st.st_size - limit)
        os.lseek(fd, start, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while data := os.read(fd, min(1 << 20, 2 * limit - total)):  # the file may grow while we read
            chunks.append(data)
            total += len(data)
            if total >= 2 * limit:
                break
    except OSError:
        return None
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    if start:
        raw = raw.partition(b"\n")[2]      # drop the line the cut landed in
    return raw.decode("utf-8", errors="replace")


def last_assistant_text(path: Path, limit: int = MAX_TRANSCRIPT) -> str:
    """The last assistant text of the **current turn**: text before the latest real user prompt belongs
    to an earlier turn and is never returned (a silent turn must not resend an old reply)."""
    content = _read_tail(path, limit)
    if content is None:
        return ""
    lines = content.splitlines()
    text = ""
    for line in lines:
        try:
            record: Any = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        kind = cast(dict[str, Any], record).get("type")
        blocks = _blocks(cast(dict[str, Any], record))
        if kind == "user":
            # A tool result is also a "user" record; only a prompt (text) starts a new turn.
            if isinstance(blocks, str) or (blocks and any(b.get("type") == "text" for b in blocks)):
                text = ""
        elif kind == "assistant" and isinstance(blocks, list):
            parts = [b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)]
            if parts:
                text = "\n".join(parts)
    return text


def reply_text(ev: HookEvent, fallback: bool = True) -> str:
    """The reply of a Stop. `fallback=False` (a stale Stop) never reads the transcript: it would hold the
    text of whatever turn ran last, not necessarily this one."""
    if ev.last_assistant_message:
        return ev.last_assistant_message
    if fallback and ev.transcript_path:
        path = Path(ev.transcript_path)
        if path.name == f"{ev.session_id}.jsonl":
            return last_assistant_text(path)
    return ""


class HookServer:
    """Accepts hook connections and hands their events to the consumer of `queue` strictly in the order
    the connections were accepted, one at a time. Connection i+1's event is queued only after connection
    i's was processed or dropped (EOF, a partial, oversized or invalid frame, or a frame that took longer
    than FRAME_SECONDS). The handler answers `ok` only after the event was processed, `err` if processing
    failed. Every accepted connection is counted in `pending_hooks` from the moment it is accepted until
    its slot completes, so the daemon can tell that a hook is still on its way."""

    def __init__(self, path: Path, queue: "asyncio.Queue[HookEvent | Delivery]", audit: Audit,
                 on_idle: Callable[[], None] | None = None,
                 on_lost: Callable[[], Awaitable[None]] | None = None,
                 on_drop: Callable[[], None] | None = None) -> None:
        self.path = path
        self.queue = queue
        self.audit = audit
        self.on_idle = on_idle      # called when `pending_hooks` returns to zero
        self.on_lost = on_lost      # awaited, in order, for an accepted frame that was dropped
        self.on_drop = on_drop      # called synchronously, first, for a dropped frame (cannot fail)
        self.accepted = 0           # the arrival index of the newest accepted connection (first is 1)
        self.pending_hooks = 0      # accepted connections whose slot has not completed yet
        self._server: asyncio.Server | None = None
        self._tail: asyncio.Future[None] | None = None   # the newest connection's slot in the order

    def _audit(self, **fields: object) -> None:
        with contextlib.suppress(Exception):    # the log failing must not skip what follows it
            self.audit.write("hook", **fields)

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._accept, path=str(self.path),
                                                           limit=MAX_HOOK_FRAME + 1)
        finally:
            os.umask(old)
        self.path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)
        self.path.unlink(missing_ok=True)

    def backlog_waiting(self) -> bool:
        """A connection is queued on the listening socket and not accepted yet (readable with timeout 0).
        Checked by the dispatcher immediately before a paste. Inherent residual: a terminal prompt typed at
        the same instant as the paste races at the agent itself, which no admind check can order."""
        server = self._server
        if server is None:
            return False
        for sock in server.sockets:
            try:
                ready, _, _ = select.select([sock], [], [], 0)
            except (OSError, ValueError):
                return True     # cannot tell: fail closed (a later flush retries)
            if ready:
                return True
        return False

    def _accept(self, reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter) -> Coroutine[Any, Any, None]:
        """The client-connected callback, deliberately synchronous: StreamReaderProtocol calls it from
        connection_made, so the count and the ordering slot are taken in the acceptance callback itself,
        before any task (a dispatcher already scheduled included) can run. It returns the coroutine that
        processes the connection."""
        loop = asyncio.get_running_loop()
        self.accepted += 1
        index = self.accepted
        self.pending_hooks += 1     # counted from accept, so the dispatch gate sees a frame still arriving
        previous = self._tail
        slot: asyncio.Future[None] = loop.create_future()
        self._tail = slot
        return self._handle(reader, writer, index, previous, slot)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, index: int,
                      previous: "asyncio.Future[None] | None", slot: "asyncio.Future[None]") -> None:
        loop = asyncio.get_running_loop()
        try:
            event: HookEvent | None = None
            try:
                line = await asyncio.wait_for(reader.readline(), FRAME_SECONDS)
                event = msgspec.json.decode(line, type=HookEvent)
            except Exception as exc:  # noqa: BLE001 - one bad frame (decode or shape) is dropped, by type only
                if self.on_drop is not None:
                    with contextlib.suppress(Exception):
                        self.on_drop()      # before the audit: a failing log cannot skip the hold
                self._audit(result="dropped", error=type(exc).__name__)
            if previous is not None:
                await previous      # dropped or not, the slot is released only after the earlier ones
            if event is None and self.on_lost is not None:
                await self.on_lost()    # it might have been a turn start: the daemon holds dispatch
            if event is not None:
                delivery = Delivery(event, loop.create_future(), index)
                await self.queue.put(delivery)
                ok = await delivery.done
                writer.write(b"ok\n" if ok else b"err\n")
                await writer.drain()
        except Exception as exc:  # noqa: BLE001 - a failed answer (the hook gave up and left) is by type only
            self._audit(result="not-answered", error=type(exc).__name__)
        finally:
            if not slot.done():
                slot.set_result(None)
            self.pending_hooks -= 1     # processed or dropped; after the answer was written
            if self.pending_hooks == 0 and self.on_idle is not None:
                self.on_idle()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
