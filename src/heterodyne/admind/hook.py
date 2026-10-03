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
optional field, the fallback is the assistant text of the turn's span in the session's own transcript
file (plan decision D2, plan 2b B13). The screen is never scraped.

Trust boundary: the socket is 0600 in a 0700 directory, so only the service user can write to it, and
events for any session but the current one are dropped. Each launch of the agent has its own nonce in its
hook command (`--launch`); the daemon treats an event from any other launch, or with no nonce, as stale.
A process already running as the service user can still forge a reply event; that is the same-user
residual risk ADR §3.4 accepts.
"""

import argparse
import asyncio
import contextlib
import contextvars
import hashlib
import json
import os
import secrets
import select
import shlex
import socket
import stat
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import msgspec

from heterodyne.admind.audit import Audit
from heterodyne.admind.redact import redact, unique_key

MAX_HOOK_FRAME = 1024 * 1024
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
ACK_SECONDS = 8.0       # the hook's total wait for admind's answer
HOOK_TIMEOUT = 15       # Claude Code kills the hook only after the client above has given up
ACCEPT_BATCH = 64        # accept() attempts (successes and aborts alike) per reader callback
ACCEPT_ABORTS = 8        # aborted accepts within one callback before backing off
ACCEPT_BACKOFF = 0.1     # pause before accepting again after an accept() error such as EMFILE
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


class _Conn:
    """One accepted connection: its socket, arrival index and ordering slot."""
    __slots__ = ("sock", "index", "slot", "released", "queued", "wrapped", "previous", "held", "settled",
                 "counted")

    def __init__(self, sock: socket.socket, index: int, slot: "asyncio.Future[None]",
                 previous: "asyncio.Future[None] | None") -> None:
        self.sock = sock
        self.index = index
        self.slot = slot
        self.previous = previous    # the predecessor's slot: ours resolves only after it
        self.held = False           # the lost-hook hold was applied for this connection
        self.settled = False        # the finish path ran (the release may still wait for the predecessor)
        self.counted = False        # it is included in `pending_hooks`; only then does its release decrement
        self.released = False
        self.wrapped = False    # a stream transport owns the socket
        self.queued = False     # its event reached the daemon's queue (or it was fully handled)


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


def transcript_size(ev: HookEvent) -> int | None:
    """The size of this session's transcript now: at UserPromptSubmit, where the turn's bytes begin; at
    Stop, where they end (plan 2b B19). Same path rules as the reply fallback; never follows a symlink or
    blocks on a FIFO."""
    if not ev.transcript_path:
        return None
    path = Path(ev.transcript_path)
    if path.name != f"{ev.session_id}.jsonl" or not path.is_absolute():
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        return st.st_size if stat.S_ISREG(st.st_mode) else None
    except OSError:
        return None
    finally:
        os.close(fd)


READ_WINDOW = 1024 * 1024               # a turn's bytes are read this much at a time
MAX_RECORD = 64 * 1024 * 1024           # a longer JSONL record fails the read: memory (plan 2b B13)
MAX_REPLY = MAX_RECORD                  # a turn's reply, in UTF-8 bytes of its final text (plan 2b B13)
MAX_BLOCKS = 100_000                    # text blocks per turn, empty ones too
SEPARATOR = "\n\n"                      # between a turn's text blocks
# Memory, honestly. The accepted result is bounded by MAX_REPLY (UTF-8 bytes, redacted, checked in
# prepare_reply), but the transient peak is not: prepare_reply redacts the whole text BEFORE it checks the
# size, so the intermediates are not bounded by the accepted result. They are, at least:
#   - the record buffer (up to MAX_RECORD) and the objects json parses from it;
#   - the parts gathered so far, then the joined text;
#   - in redaction: control escaping grows the text (a control character becomes `\xNN`), and each
#     substitution pass allocates a copy;
#   - the UTF-8 encode copy of the redacted text, for the size check.
# Each of these can be larger than its byte count suggests: parsed Python objects take more than the bytes
# they came from, and a Python string can take more than its UTF-8 size (or less, for CJK text). No
# multiple is claimed, and this is not an RSS guarantee. The extraction threads run one at a time (B20),
# so the peaks do not stack across turns.
READ_CANCEL: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "READ_CANCEL", default=None)         # set by the caller of a threaded read: it gave up on it


class RecordTooLarge(OSError):
    """A record in the turn's span is over MAX_RECORD. It can't be parsed, so nobody can tell whether it is
    metadata, an answer or the next turn's prompt; reading on could mix in another turn's content (Codex
    r7 finding 1), so the whole read fails."""


def _lines(fd: int, start: int, end: int) -> Iterator[bytes]:
    """The complete lines in bytes [start, end), read READ_WINDOW at a time. A line longer than MAX_RECORD
    raises RecordTooLarge as soon as it passes the limit, so memory stays bounded. The span was measured at
    the turn's edges, so it ends on a newline: a file shorter than `end`, or a last line without its
    newline, means the transcript changed under us, and raises OSError (the caller reports a failed read,
    never a partial one)."""
    pos, buf = start, bytearray()
    while pos < end:
        data = os.pread(fd, min(READ_WINDOW, end - pos), pos)
        if not data:
            raise OSError("the transcript is shorter than the turn's span")
        pos += len(data)
        while data:
            nl = data.find(b"\n")
            piece, data = (data, b"") if nl < 0 else (data[:nl], data[nl + 1:])
            buf += piece
            if len(buf) > MAX_RECORD:
                raise RecordTooLarge(f"a transcript record is over {MAX_RECORD} bytes")
            if nl >= 0:
                yield bytes(buf)
                buf = bytearray()
    if buf:
        raise OSError("the turn's span ends inside a record")


def _is_prompt(record: dict[str, Any]) -> bool:
    """A real user prompt: text from the user, as opposed to a record that carries tool results or one
    Claude Code adds itself (`isMeta`, such as a caveat or a local command's output). A record that
    carries a tool result is never a prompt, even if it also has text (Codex r4 finding 2)."""
    blocks = _blocks(record)
    return record.get("type") == "user" and not record.get("isMeta") and not _has_result(record) and (
        isinstance(blocks, str) or bool(blocks and any(b.get("type") == "text" for b in blocks)))


def _has_result(record: dict[str, Any]) -> bool:
    blocks = _blocks(record)
    return (record.get("type") == "user" and isinstance(blocks, list)
            and any(b.get("type") == "tool_result" for b in blocks))


def turn_records(path: Path, start: int, end: int) -> Iterator[dict[str, Any]]:
    """The content records of one turn: transcript bytes [start, end), from its UserPromptSubmit to its
    Stop (plan 2b B13, B19). The turn begins at its first content (an assistant record or a tool result);
    a real prompt before that is the turn's own and is skipped, one after it is the next turn's and ends
    this one. Anything else is metadata (such as `queue-operation`) and skipped. Raises RecordTooLarge for
    a record over MAX_RECORD, and OSError if the file can't be read, or if a complete record in the span is
    not a JSON object: a corrupt record could be the error or question the operator needs, so the read
    fails rather than skip it (Codex r5 finding 2). Never follows a symlink or blocks on a FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        begun = False
        for item in _lines(fd, start, end):
            if _abandoned():
                raise OSError("the read was abandoned")
            try:
                record: Any = json.loads(item)
            except (ValueError, RecursionError):    # ValueError includes UnicodeDecodeError
                raise OSError("a transcript record is not valid JSON") from None
            if not isinstance(record, dict):
                raise OSError("a transcript record is not a JSON object")
            rec = cast(dict[str, Any], record)
            if _is_prompt(rec):
                if begun:
                    return                  # the next turn's prompt
                continue                    # this turn's own prompt
            if rec.get("type") == "assistant" or _has_result(rec):
                begun = True
                yield rec
    finally:
        os.close(fd)


def _block_text(block: Any) -> str:
    """One block of a tool result, deterministically. Text is shown as it is; an image is named by its
    type, size and digest (a text chat can't show it; flagged, B13); anything else is its JSON, keys
    sorted, with every string redacted first (`_json`)."""
    if isinstance(block, dict):
        b = cast(dict[str, Any], block)
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            return b["text"]
        source = b.get("source")
        if b.get("type") == "image" and isinstance(source, dict):
            src = cast(dict[str, Any], source)
            data = str(src.get("data", ""))
            digest = hashlib.sha256(data.encode("utf-8", "surrogatepass")).hexdigest()[:12]
            media = _label(src.get("media_type", "?"))
            return f"[image {media}, {len(data)} base64 characters, sha256 {digest}]"
    return _json(block)


def _redacted(value: Any) -> Any:
    """`value` with every string in it (keys too) redacted, before it is serialized: JSON escapes can put
    a digit right before a secret (`\\u0001ghp_…`), which the scanner's start rule then refuses (B1)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in cast(dict[Any, Any], value).items():
            out[unique_key(redact(str(k)), out)] = _redacted(v)     # keys that redact alike: both kept
        return out
    if isinstance(value, list):
        return [_redacted(v) for v in cast(list[Any], value)]
    return value


def _json(value: Any) -> str:
    return json.dumps(_redacted(value), ensure_ascii=False, sort_keys=True)


def _label(value: Any) -> str:
    """An untrusted field shown inline (a tool's name, an image's media type): a string is redacted, anything
    else goes through `_json`, never through str/repr/format, where a "\\n" becomes a letter n and defeats
    the scanner's lookbehind (B1)."""
    return redact(value) if isinstance(value, str) else _json(value)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_block_text(b) for b in cast(list[Any], content))
    return _json(content)


def _tool_lines(record: dict[str, Any]) -> list[str]:
    """The tool calls and results in one record. Thinking blocks are never read out."""
    blocks = _blocks(record)
    if not isinstance(blocks, list):
        return []
    if record.get("type") == "user":
        return [f"◂ {'(error) ' if b.get('is_error') else ''}{_result_text(b.get('content'))}"
                for b in blocks if b.get("type") == "tool_result"]
    if record.get("type") == "assistant":
        return [f"▸ {_label(b.get('name'))} {_json(b.get('input'))}"
                for b in blocks if b.get("type") == "tool_use"]
    return []


TOO_LARGE = (f"(this turn's transcript holds a record over {MAX_RECORD // (1024 * 1024)} MiB; "
             "its tool calls can't be shown)")


def turn_tool_calls(path: Path, start: int, end: int) -> str:
    """The tool calls and results of one turn (plan 2b B13, B19), read with `turn_records`. Nothing is
    shortened: §8 puts no cap on `!details`. A record over MAX_RECORD makes the turn unreadable: without
    parsing it, the turn's end can't be found (B13)."""
    out: list[str] = []
    try:
        for rec in turn_records(path, start, end):
            out.extend(_tool_lines(rec))
    except RecordTooLarge:
        return TOO_LARGE
    except (OSError, RecursionError):   # a structure nested deeper than the stack allows is unreadable too
        return "(the transcript could not be read)"
    # A lone surrogate (JSON `\\ud800`) can't be encoded, so it can't be stored or sent: it becomes `?`.
    if not out:
        return "(no tool calls in this turn)"
    return "\n".join(out).encode("utf-8", "replace").decode("utf-8")


def reply_text(ev: HookEvent, start: int | None, end: int | None) -> str | None:
    """A current Stop's reply (B9, B19). The event's own text if it has one; otherwise every assistant text
    of the turn's span, in order, so an early question or error is kept. None when it can't be read: no
    span, a path that is not this session's transcript, a read error, a record too large to read
    (RecordTooLarge is an OSError), text a database can't hold, or more than MAX_REPLY UTF-8 bytes of
    joined text (separators included) or MAX_BLOCKS blocks, which stops the read at once. "" is a turn
    that said nothing."""
    if ev.last_assistant_message:
        size = _size(ev.last_assistant_message)
        return ev.last_assistant_message if size is not None and size <= MAX_REPLY else None
    if start is None or end is None or not ev.transcript_path:
        return None
    path = Path(ev.transcript_path)
    if path.name != f"{ev.session_id}.jsonl" or not path.is_absolute():
        return None
    parts: list[str] = []
    total = 0                               # UTF-8 bytes of the final joined text
    try:
        for rec in turn_records(path, start, end):
            blocks = _blocks(rec)
            if rec.get("type") == "assistant" and isinstance(blocks, list):
                for b in blocks:
                    if _abandoned():
                        raise OSError("the read was abandoned")
                    text = b.get("text")
                    if b.get("type") != "text" or not isinstance(text, str):
                        continue
                    size = _size(text)
                    if size is None:
                        return None         # text no database can hold
                    total += size + (len(SEPARATOR) if parts else 0)
                    if total > MAX_REPLY or len(parts) >= MAX_BLOCKS:
                        return None         # too much text in all, however it is split
                    parts.append(text)
    except OSError:
        return None
    return SEPARATOR.join(parts)


def _abandoned() -> bool:
    cancel = READ_CANCEL.get()
    return cancel is not None and cancel.is_set()


def _size(text: str) -> int | None:
    """The UTF-8 length of `text`, or None for text with a lone surrogate (JSON `\\ud800`): it can't be
    encoded, so it can't be stored."""
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


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
        self._listen: socket.socket | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reading = False       # the listening socket is registered with the loop
        self._backoff: asyncio.TimerHandle | None = None
        self._closing = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._conns: set[_Conn] = set()     # accepted, slot not yet released
        self._tail: asyncio.Future[None] | None = None   # the newest connection's slot in the order

    def _audit(self, **fields: object) -> None:
        with contextlib.suppress(Exception):    # the log failing must not skip what follows it
            self.audit.write("hook", **fields)

    async def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            sock.setblocking(False)
            sock.bind(str(self.path))
            sock.listen(100)
        except BaseException:
            sock.close()
            raise
        finally:
            os.umask(old)
        self.path.chmod(0o600)
        self._loop = loop
        self._listen = sock
        self._closing = False
        try:
            loop.add_reader(sock.fileno(), self._on_readable)
        except BaseException:
            self._listen = None
            sock.close()
            self.path.unlink(missing_ok=True)
            raise
        self._reading = True

    async def close(self) -> None:
        self._closing = True
        self._stop_reading()
        if self._backoff is not None:
            self._backoff.cancel()
            self._backoff = None
        if self._listen is not None:
            self._listen.close()
            self._listen = None
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()       # each task's done callback releases its slot and closes its socket
        if tasks:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        for state in list(self._conns):      # shutting down: no order left to keep
            self._settle(state)
            self._release(state)
        self.path.unlink(missing_ok=True)

    def _stop_reading(self) -> None:
        if self._reading and self._loop is not None and self._listen is not None:
            self._loop.remove_reader(self._listen.fileno())
        self._reading = False

    def backlog_waiting(self) -> bool:
        """A connection is queued on the listening socket and not accepted yet (readable with timeout 0).
        Checked by the dispatcher immediately before a paste. It also covers an accept that failed (for
        example out of descriptors) and is being retried: the connection stays in the backlog. Inherent
        residual: a terminal prompt typed at the same instant as the paste races at the agent itself,
        which no admind check can order."""
        sock = self._listen
        if sock is None:
            return False
        try:
            ready, _, _ = select.select([sock], [], [], 0)
        except (OSError, ValueError):
            return True     # cannot tell: fail closed (a later flush retries)
        return bool(ready)

    def _accept_conn(self) -> socket.socket:
        if self._listen is None:
            raise BlockingIOError
        return self._listen.accept()[0]

    def _on_readable(self) -> None:
        """The listening socket's reader, a plain synchronous callback. For each connection it accepts, in
        this one callback with no await between: make it non-blocking, count it (`pending_hooks`), take its
        ordering slot, and schedule its processing. An accepted connection is therefore always counted
        before control returns to the loop; until accepted it is in the backlog, which the dispatcher
        polls."""
        loop = self._loop
        if loop is None or self._closing:
            return
        aborts = 0
        for _ in range(ACCEPT_BATCH):       # bounded: the reader fires again for the rest of the backlog
            try:
                conn = self._accept_conn()
            except (BlockingIOError, InterruptedError):
                return
            except ConnectionAbortedError:      # the peer left before accept
                aborts += 1
                if aborts >= ACCEPT_ABORTS:
                    self._back_off(loop)
                    return
                continue
            except OSError as exc:      # e.g. EMFILE: do not spin; the backlog keeps the gate closed
                self._audit(result="accept-error", error=type(exc).__name__)
                self._back_off(loop)
                return
            self.accepted += 1
            state: _Conn | None = None
            try:
                previous = self._tail
                slot: asyncio.Future[None] = loop.create_future()
                state = _Conn(conn, self.accepted, slot, previous)
                self._conns.add(state)
                self.pending_hooks += 1     # counted from accept, so the gate sees a frame still arriving
                state.counted = True        # (never counted without a releasable record, see _release)
                self._tail = slot
                conn.setblocking(False)
                coro = self._handle(state, previous)
                try:
                    task = loop.create_task(coro)
                except BaseException:
                    coro.close()
                    raise
            except BaseException as exc:
                if state is not None:
                    self._settle(state)     # hold, close, and release after the predecessor
                else:       # not even a record could be made: nothing was counted
                    conn.close()
                    if self.on_drop is not None:
                        with contextlib.suppress(Exception):
                            self.on_drop()
                self._audit(result="accept-error", error=type(exc).__name__)
                if not isinstance(exc, Exception):
                    raise
                self._back_off(loop)
                return
            self._tasks.add(task)
            task.add_done_callback(lambda t, st=state: self._finished(t, st))

    def _back_off(self, loop: asyncio.AbstractEventLoop) -> None:
        self._stop_reading()
        if self._backoff is None:
            self._backoff = loop.call_later(ACCEPT_BACKOFF, self._resume_reading)

    def _resume_reading(self) -> None:
        self._backoff = None
        if self._closing or self._listen is None or self._loop is None or self._reading:
            return
        try:
            self._loop.add_reader(self._listen.fileno(), self._on_readable)
        except Exception as exc:  # noqa: BLE001 - e.g. the selector's watch limit: try again, never stay deaf
            self._audit(result="accept-error", error=type(exc).__name__)
            self._back_off(self._loop)
            return
        self._reading = True

    def _finished(self, task: asyncio.Task[None], state: "_Conn") -> None:
        """Backstop: a task cancelled before it ever ran never executes its own cleanup."""
        self._tasks.discard(task)
        self._settle(state)

    def _hold(self, state: "_Conn") -> None:
        """The lost-hook hold, once per connection, for one whose event never reached the daemon."""
        if state.held or state.queued:
            return
        state.held = True
        if self.on_drop is not None:
            with contextlib.suppress(Exception):
                self.on_drop()

    def _settle(self, state: "_Conn") -> None:
        """The one finish path. Every accepted connection ends here, applied (its event was routed) or lost
        (anything else): a lost one applies the hold first; the socket is closed unless a transport owns
        it; and the slot and pending count are released only after the predecessor's slot has resolved.
        Synchronous and idempotent, so it is safe from a finally block, a done callback or the accept."""
        if state.settled:
            return
        state.settled = True
        self._hold(state)
        if not state.wrapped:
            state.sock.close()      # no transport ever took the socket
        previous = state.previous
        if previous is None or previous.done():
            self._release(state)
        else:
            previous.add_done_callback(lambda _f, st=state: self._release_after(st))

    def _release_after(self, state: "_Conn") -> None:
        """The ordered release of a connection that waited for its predecessor. The predecessor may have
        processed a Stop that lifted the hold this (lost) connection applied, so it is applied again before
        the count drops. `on_drop` is idempotent apart from the notice-once rule."""
        if state.held and not state.released and self.on_drop is not None:
            with contextlib.suppress(Exception):
                self.on_drop()
        self._release(state)

    def _release(self, state: "_Conn") -> None:
        if state.released:
            return
        state.released = True
        self._conns.discard(state)
        if not state.slot.done():
            state.slot.set_result(None)
        if state.counted:
            self.pending_hooks -= 1     # processed or dropped; after the answer was written
        if self.pending_hooks == 0 and self.on_idle is not None and not self._closing:
            try:
                self.on_idle()
            except Exception as exc:  # noqa: BLE001 - the release is done; the handler's cleanup must go on
                self._audit(result="idle-error", error=type(exc).__name__)

    async def _handle(self, state: "_Conn", previous: "asyncio.Future[None] | None") -> None:
        loop = asyncio.get_running_loop()
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_unix_connection(sock=state.sock, limit=MAX_HOOK_FRAME + 1)
            state.wrapped = True
            event: HookEvent | None = None
            try:
                line = await asyncio.wait_for(reader.readline(), FRAME_SECONDS)
                event = msgspec.json.decode(line, type=HookEvent)
            except Exception as exc:  # noqa: BLE001 - one bad frame (decode or shape) is dropped, by type only
                self._hold(state)       # before the audit: a failing log cannot skip the hold
                self._audit(result="dropped", error=type(exc).__name__)
            if previous is not None:
                await previous      # dropped or not, the slot is released only after the earlier ones
            if event is None and self.on_lost is not None:
                await self.on_lost()    # it might have been a turn start: the daemon holds dispatch
            if event is not None:
                delivery = Delivery(event, loop.create_future(), state.index)
                await self.queue.put(delivery)
                state.queued = True
                ok = await delivery.done
                writer.write(b"ok\n" if ok else b"err\n")
                await writer.drain()
            state.queued = True
        except Exception as exc:  # noqa: BLE001 - a failed answer (the hook gave up and left) is by type only
            self._audit(result="not-answered", error=type(exc).__name__)
        finally:
            self._settle(state)
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
