"""The per-session socket (ADR 0001 §7 Session sockets), served by wsd for one session generation.

It is bound inside the generation's run directory (`/run/hz/s.sock` inside). It accepts only `hook_event`
and `ws_request`, each with the launch token, and refuses every other operation with the exact reply
`{"ok": false, "error": "forbidden"}`, which the self-test checks. Until plan 5 every hook event is
allowed and ws-request is unsupported (plan 4 D15).

Everything a session sends is untrusted. Accepted requests are spooled, capped, to the generation's
events file outside the sandbox. `TurnState` keeps only what the runtime reads: turn counts, the last
turn event and whether it ended a turn (one of the adapter's turn-end events, D13; plan 4b E14), and the
first SessionStart's session ID if it is a canonical UUID (the Codex thread ID, D8).

The session is untrusted with wsd's resources too: at most `max_handlers` connections are served at
once (more are closed unread), each request has one absolute deadline however its bytes trickle in, and
`close` returns only once no connection is left and nothing more can be recorded or spooled.
"""

import contextlib
import errno
import hmac
import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from heterodyne.session.control import ControlCapacity, ControlJournal
from heterodyne.session.shim import (
    CONTROL_SCHEMA,
    MAX_DEPTH,
    MAX_LINE,
    json_depth,
    strict_json,
)

CLIENT_SECONDS = 5.0            # one request's whole deadline, from its accept
MAX_HANDLERS = 32
CLOSE_SECONDS = 5.0             # how long close waits for the handlers it has woken
OK = {"ok": True}
FORBIDDEN = {"ok": False, "error": "forbidden"}
UNSUPPORTED = {"ok": False, "error": "unsupported"}
MALFORMED = {"ok": False, "error": "malformed"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TURN_EVENTS = ("UserPromptSubmit", "Stop")


@dataclass(frozen=True)
class TurnState:
    prompts: int = 0
    stops: int = 0                  # turn ends of any of the session's turn-end events
    last_event: str = ""
    thread_id: str | None = None
    boundary: bool = False          # the last turn event ended a turn

    @property
    def idle(self) -> bool:
        """At a turn boundary: the last turn event was one of the session's turn-end events."""
        return self.boundary


class SessionServer:
    def __init__(self, path: Path, token: str, events: Path, *, max_spool: int = 4 << 20,
                 max_handlers: int = MAX_HANDLERS, client_seconds: float = CLIENT_SECONDS,
                 clock: Callable[[], float] = time.monotonic, turn_ends: tuple[str, ...] = ("Stop",),
                 on_event: Callable[[dict[str, Any]], None] | None = None,
                 control: ControlJournal | None = None) -> None:
        self.control = control
        self.path = path
        self.token = token.encode()
        self.events = events
        self.max_spool = max_spool
        self.client_seconds = client_seconds
        self.clock = clock
        self.turn_ends = turn_ends
        self.on_event = on_event
        self.served = 0
        self._state = TurnState()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_handlers)
        self._closed = threading.Event()
        self._sock: socket.socket | None = None
        self._acceptor: threading.Thread | None = None
        self._handlers: dict[socket.socket, threading.Thread] = {}

    def start(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        # The sandbox's user is wsd's own uid (OpenShell run_as_user); the run directory (0700) keeps
        # other host users out.
        self.path.chmod(0o777)
        sock.listen(16)
        self._sock = sock
        acceptor = threading.Thread(target=self._accept, args=(sock,),
                                    name=f"session-{self.path.parent.name}", daemon=True)
        acceptor.start()
        self._acceptor = acceptor           # only a started thread is ever joined

    def close(self) -> None:
        """Stop admitting, wake every connection, and wait (bounded) for its handler. Once this returns,
        nothing a session sent is recorded or spooled, even by a handler still unwinding."""
        with self._lock:
            self._closed.set()
            sock, self._sock = self._sock, None
            handlers = dict(self._handlers)
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        for conn in handlers:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
        try:
            end = time.monotonic() + CLOSE_SECONDS
            for thread in [self._acceptor, *handlers.values()]:
                if thread is not None and thread is not threading.current_thread():
                    thread.join(max(0.0, end - time.monotonic()))
        finally:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()

    @property
    def active(self) -> int:
        """Connections being served now."""
        with self._lock:
            return len(self._handlers)

    def turns(self) -> TurnState:
        with self._lock:
            return self._state

    def _accept(self, sock: socket.socket) -> None:
        while not self._closed.is_set():
            try:
                conn, _ = sock.accept()
            except OSError as exc:
                if self._closed.is_set() or exc.errno not in (errno.EMFILE, errno.ENFILE, errno.ENOBUFS,
                                                              errno.ENOMEM):
                    return
                self._closed.wait(0.1)      # out of descriptors or memory for now: never spin, never die
                continue
            self._admit(conn)

    def _admit(self, conn: socket.socket) -> None:
        """Serve `conn` on its own thread if a slot is free; otherwise, or once closed, close it unread."""
        if not self._slots.acquire(blocking=False):
            conn.close()
            return
        thread = threading.Thread(target=self._serve, args=(conn,),
                                  name=f"session-{self.path.parent.name}-conn", daemon=True)
        # Started and registered under the lock, so close sees a handler only once it runs (the handler's
        # own cleanup waits for the lock too). A thread that fails to start is never registered.
        with self._lock:
            started = False
            if not self._closed.is_set():
                try:
                    thread.start()
                    started = True
                except RuntimeError:        # no thread to be had: refuse this one, keep accepting
                    pass
                else:
                    self._handlers[conn] = thread
        if started:
            return
        self._slots.release()
        conn.close()

    def _serve(self, conn: socket.socket) -> None:
        try:
            with conn:
                line = self._read(conn)
                if line is None:
                    return
                try:
                    reply = MALFORMED if len(line) > MAX_LINE else self._handle(line)
                except RecursionError:      # nested too deeply to parse or spool
                    reply = MALFORMED
                with contextlib.suppress(OSError):
                    conn.sendall((json.dumps(reply) + "\n").encode())
                    with self._lock:
                        self.served += 1
        finally:
            with self._lock:
                self._handlers.pop(conn, None)
            self._slots.release()

    def _read(self, conn: socket.socket) -> bytes | None:
        """The request line (cut at MAX_LINE + 1 bytes), or None if the deadline passes, the client goes
        away or close wakes the connection first. One deadline for the whole request."""
        deadline = self.clock() + self.client_seconds
        data = b""
        while b"\n" not in data and len(data) <= MAX_LINE:
            left = deadline - self.clock()
            if left <= 0 or self._closed.is_set():
                return None
            try:
                conn.settimeout(left)
                chunk = conn.recv(8192)
            except OSError:
                return None
            if not chunk:
                break
            data += chunk
        if self._closed.is_set() or self.clock() > deadline:     # its last bytes came too late
            return None
        return data.split(b"\n", 1)[0]

    def _handle(self, line: bytes) -> dict[str, Any]:
        if json_depth(line) > MAX_DEPTH:
            return MALFORMED
        try:
            req: Any = strict_json(line)
        except UnicodeEncodeError:
            return FORBIDDEN  # preserve refusal of an escaped, unencodable launch token
        except ValueError:
            return MALFORMED
        if not isinstance(req, dict):
            return MALFORMED
        body = cast(dict[str, Any], req)
        token = body.get("token")
        if not isinstance(token, str):
            return FORBIDDEN
        try:
            offered = token.encode()
        except UnicodeEncodeError:          # a lone surrogate: never the launch token
            return FORBIDDEN
        if not hmac.compare_digest(offered, self.token):
            return FORBIDDEN
        kind = body.get("type")
        if kind not in ("hook_event", "ws_request"):
            return FORBIDDEN
        payload = body.get("payload")
        if not isinstance(payload, dict):
            return MALFORMED
        fields = cast(dict[str, Any], payload)
        if kind == "ws_request" and fields.get("schema") == CONTROL_SCHEMA:
            if self.control is None:
                return FORBIDDEN
            if set(body) != {"token", "type", "payload"} or len(line) + 1 > MAX_LINE:
                return MALFORMED
            raw = _payload_bytes(line)
            try:
                return self.control.intake(raw)
            except ControlCapacity:
                return {"ok": False, "state": "refused", "error": "manager-request-capacity"}
            except (OSError, ValueError, RuntimeError):
                return {"ok": False, "state": "refused", "error": "control-intake-refused"}
        self._spool(kind, fields)
        if kind == "ws_request":
            return UNSUPPORTED
        self._record(fields)
        return OK

    def _record(self, payload: dict[str, Any]) -> None:
        """Turn state from the event name and session ID alone, then the runtime's callback, both under
        the lock and only while open, so nothing is recorded or reported once close has returned. A
        callback that fails drops its own report: the reply and the turn state are unaffected."""
        event = payload.get("hook_event_name")
        with self._lock:
            if self._closed.is_set():
                return
            s = self._state
            if event == "SessionStart" and s.thread_id is None:
                sid = payload.get("session_id")
                if isinstance(sid, str) and UUID.fullmatch(sid):
                    s = replace(s, thread_id=sid)
            elif event == "UserPromptSubmit":
                s = replace(s, prompts=s.prompts + 1, last_event=event, boundary=False)
            elif isinstance(event, str) and event in self.turn_ends:
                s = replace(s, stops=s.stops + 1, last_event=event, boundary=True)
            self._state = s
            if self.on_event is not None:
                try:
                    self.on_event(payload)
                except Exception:  # noqa: BLE001, S110 - an untrusted report is dropped, never retried
                    pass

    def _spool(self, kind: str, payload: dict[str, Any]) -> None:
        line = (json.dumps({"type": kind, "payload": payload}) + "\n").encode()
        with self._lock:
            if self._closed.is_set():
                return
            try:
                size = self.events.stat().st_size
            except FileNotFoundError:
                size = 0
            if size + len(line) > self.max_spool:
                return
            fd = os.open(self.events, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as fh:
                fh.write(line)



def _payload_bytes(line: bytes) -> bytes:
    """Preserve the exact inner request bytes after strict outer-object validation."""
    text = line.decode("utf-8", "strict")
    decoder = json.JSONDecoder()
    cursor = text.index("{") + 1
    while True:
        while text[cursor].isspace():
            cursor += 1
        key, cursor = decoder.raw_decode(text, cursor)
        while text[cursor].isspace():
            cursor += 1
        cursor += 1  # colon, already checked by the strict full parse
        while text[cursor].isspace():
            cursor += 1
        start = cursor
        _, cursor = decoder.raw_decode(text, cursor)
        if key == "payload":
            return text[start:cursor].encode("utf-8")
        while text[cursor].isspace():
            cursor += 1
        cursor += 1  # comma; a validated control envelope always has payload
