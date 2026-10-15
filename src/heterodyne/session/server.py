"""The per-session socket (ADR 0001 §7 Session sockets), served by wsd for one session generation.

It is bound inside the generation's run directory (`/run/hz/s.sock` inside). It accepts only `hook_event`
and `ws_request`, each with the launch token, and refuses every other operation with the exact reply
`{"ok": false, "error": "forbidden"}`, which the self-test checks. Until plan 5 every hook event is
allowed (plan 4 D15); ws-request is unsupported unless the runtime passes `on_request` (plan 5i).

Everything a session sends is untrusted. Accepted requests are spooled, capped, to the generation's
events file outside the sandbox. `TurnState` keeps only what the runtime reads: turn counts, the last
turn event and whether it ended a turn (one of the adapter's turn-end events, D13; plan 4b E14), and the
first SessionStart's session ID if it is a canonical UUID (the Codex thread ID, D8).

A steer (plan 5i D4) delivers at a turn boundary through `at_boundary`, which spends the boundary before
delivering and holds the turn gate while it does: a turn event that arrives meanwhile is recorded only once
the delivery is over, so it never slips between the idle check and the paste, and two steers never share a
boundary. A hook's shim gives up after its own wait and lets the turn go on, so a delivery must end well
within that wait (the runtime bounds a steer's paste to half of the generation's installed wait). The spent
boundary belongs to this server, so a rebind or a new generation starts with none.

A delivery that raised may still land: tmux can't cancel a command whose client timed out, so its paste
or its Enter may run after the gate is gone. Nothing is delivered for this generation again
(BoundaryUncertain), so no later Enter of ours submits what a late paste left. The mark is durable when the
runtime passes `inflight` and `uncertain` paths: a delivery creates `inflight` (synced) before it starts and
removes it once it is confirmed or nothing reached the session, and an uncertain one renames it to
`uncertain`. A server built while either exists, as by a rebind after a restart or a crash mid-delivery,
delivers nothing (BoundaryUncertain); a new generation has its own paths.

What the gate can't cover: a late Enter from a timed-out send-keys, which submits that one delivery once,
maybe mid-turn; text a late paste-buffer left in the input line, which waits there until an operator
presses Enter in the pane; a hook already in flight before the boundary was spent; and text an operator
types into the pane.

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

from heterodyne.session.shim import MAX_DEPTH, MAX_LINE, json_depth  # stdlib-only, shared with the shim

CLIENT_SECONDS = 5.0            # one request's whole deadline, from its accept
MAX_HANDLERS = 32
CLOSE_SECONDS = 5.0             # how long close waits for the handlers it has woken
OK = {"ok": True}
FORBIDDEN = {"ok": False, "error": "forbidden"}
UNSUPPORTED = {"ok": False, "error": "unsupported"}
MALFORMED = {"ok": False, "error": "malformed"}
UNAVAILABLE = {"ok": False, "error": "unavailable"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TURN_EVENTS = ("UserPromptSubmit", "Stop")


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class BoundaryBusy(Exception):
    """No turn boundary to deliver at: mid-turn, already spent, another delivery under way, or closed."""


class BoundaryUncertain(BoundaryBusy):
    """An earlier delivery raised, or was never confirmed, so it may still land: nothing is delivered for
    this generation again."""


class BoundaryUnmarked(BoundaryBusy):
    """The in-flight mark couldn't be made durable, so nothing was delivered and the boundary is kept."""


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
                 on_request: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 inflight: Path | None = None, uncertain: Path | None = None) -> None:
        self.path = path
        self.token = token.encode()
        self.events = events
        self.max_spool = max_spool
        self.client_seconds = client_seconds
        self.clock = clock
        self.turn_ends = turn_ends
        self.on_event = on_event
        self.on_request = on_request
        self.inflight = inflight
        self.uncertain = uncertain
        self.served = 0
        self._state = TurnState()
        self._lock = threading.Lock()
        self._turn_gate = threading.Lock()  # held by a delivery; taken before _lock by turn events
        self._spent: int | None = None      # the turn-end count whose boundary a delivery used
        self._uncertain = self._marked()    # a delivery raised: it may yet land, so no other one runs
        self._slots = threading.BoundedSemaphore(max_handlers)
        self._closed = threading.Event()
        self._calling: dict[int, int] = {}      # thread -> its on_request calls running (Codex T2 c1 B1)
        self._idle = threading.Condition(self._lock)
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
        """Stop admitting, wake every connection, then wait for the request callbacks already running and
        each handler; CLOSE_SECONDS bounds those two waits together. Close begins under the lock, so a hook
        callback (`on_event`, which runs under it) can delay its start. No new request callback starts
        once close has begun, and once this returns nothing a session sent is recorded or spooled, even by
        a handler still unwinding. A request callback still running then (blocked, perhaps on a lock its
        closer holds, or closing this server itself) is never waited on further, and this server can't
        stop its effect: that is fenced by its owner at commit, as the runtime's teardown closes the
        generation's event log after this, which then refuses every append (plan 5i D2)."""
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
        me, end = threading.get_ident(), time.monotonic() + CLOSE_SECONDS
        with self._lock:
            while any(n for t, n in self._calling.items() if t != me):
                left = end - time.monotonic()
                if left <= 0:
                    break                       # never waited on further: the owner refuses its effect
                self._idle.wait(left)
        try:
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

    def at_boundary(self, deliver: Callable[[], bool]) -> None:
        """Run `deliver` at the current turn boundary, spending it first; raise BoundaryBusy if there is
        none to spend. No turn event is recorded until `deliver` returns. If it returns False (nothing
        reached the session) the boundary is given back; if it raises, the boundary stays spent and every
        later call raises BoundaryUncertain, touching nothing: what it sent may still land. So does every
        call once a durable mark says a delivery is in flight or uncertain. If the in-flight mark can't be
        made, BoundaryUnmarked: nothing is delivered and the boundary is given back."""
        if not self._turn_gate.acquire(blocking=False):
            raise BoundaryBusy
        try:
            with self._lock:
                if self._uncertain or self._marked():
                    self._uncertain = True
                    raise BoundaryUncertain
                stops = self._state.stops
                if self._closed.is_set() or not self._state.idle or self._spent == stops:
                    raise BoundaryBusy
                self._spent = stops
            try:
                self._mark()
            except OSError:
                with self._lock:
                    self._spent = None
                raise BoundaryUnmarked from None
            try:
                delivered = deliver()
            except BaseException:
                with self._lock:
                    self._uncertain = True
                self._settle(uncertain=True)
                raise
            self._settle(uncertain=False)
            if not delivered:
                with self._lock:
                    self._spent = None
        finally:
            self._turn_gate.release()

    def _marked(self) -> bool:
        """A durable mark is there, or can't be ruled out (fail closed)."""
        for mark in (self.inflight, self.uncertain):
            if mark is None:
                continue
            try:
                os.lstat(mark)
            except FileNotFoundError:
                continue
            except OSError:
                pass                        # can't be ruled out
            return True
        return False

    def _mark(self) -> None:
        """Create the in-flight mark and sync it and its directory before anything is delivered."""
        if self.inflight is None:
            return
        try:
            fd = os.open(self.inflight, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            _sync_dir(self.inflight.parent)
        except OSError:
            with contextlib.suppress(OSError):
                self.inflight.unlink()      # never delivered: a mark left half-made only refuses more
            raise

    def _settle(self, *, uncertain: bool) -> None:
        """Clear the in-flight mark once the delivery is confirmed or reached nothing, or turn it into the
        uncertain mark. Failing to is conservative: a mark left in place refuses every later delivery."""
        if self.inflight is None:
            return
        with contextlib.suppress(OSError):
            if uncertain and self.uncertain is not None:
                self.inflight.replace(self.uncertain)
            elif not uncertain:
                self.inflight.unlink()
            _sync_dir(self.inflight.parent)

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
            req: Any = json.loads(line)
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
        self._spool(kind, fields)
        if kind == "ws_request":
            return self._request(fields)
        self._record(fields)
        return OK

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Plan 5i: a request naming an `op` goes to the runtime's callback, outside the lock (it writes
        the event log and must not hold up hooks), and only while open: it is counted in under the lock
        that close takes to begin, and close waits (bounded) until it is counted out. Anything else, or no
        callback, is unsupported as before."""
        if self.on_request is None or not isinstance(payload.get("op"), str):
            return UNSUPPORTED
        me = threading.get_ident()
        with self._lock:
            if self._closed.is_set():
                return UNSUPPORTED
            self._calling[me] = self._calling.get(me, 0) + 1
        try:
            return self.on_request(payload)
        except Exception:  # noqa: BLE001 - the session learns only that it can retry
            return UNAVAILABLE
        finally:
            with self._lock:
                left = self._calling.pop(me) - 1
                if left:
                    self._calling[me] = left
                self._idle.notify_all()

    def _record(self, payload: dict[str, Any]) -> None:
        """Turn state from the event name and session ID alone, then the runtime's callback, both under
        the lock and only while open, so nothing is recorded or reported once close has returned. A
        callback that fails drops its own report: the reply and the turn state are unaffected."""
        event = payload.get("hook_event_name")
        turn = event == "UserPromptSubmit" or (isinstance(event, str) and event in self.turn_ends)
        with self._turn_gate if turn else contextlib.nullcontext(), self._lock:
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

