"""The per-session socket (ADR 0001 §7 Session sockets), served by wsd for one session generation.

It is bound inside the generation's run directory (`/run/hz/s.sock` inside). It accepts only `hook_event`
and `ws_request`, each with the launch token, and refuses every other operation with the exact reply
`{"ok": false, "error": "forbidden"}`, which the self-test checks. Until plan 5 every hook event is
allowed and ws-request is unsupported (plan 4 D15).

Everything a session sends is untrusted. Accepted requests are spooled, capped, to the generation's
events file outside the sandbox. `TurnState` keeps only what the runtime reads: turn counts and the last
turn event (the lifetime stop's turn boundary, D13) and the first SessionStart's session ID if it is a
canonical UUID (the Codex thread ID, D8).
"""

import contextlib
import hmac
import json
import os
import re
import socket
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

MAX_LINE = 64 * 1024
CLIENT_SECONDS = 5.0
OK = {"ok": True}
FORBIDDEN = {"ok": False, "error": "forbidden"}
UNSUPPORTED = {"ok": False, "error": "unsupported"}
MALFORMED = {"ok": False, "error": "malformed"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TURN_EVENTS = ("UserPromptSubmit", "Stop")


@dataclass(frozen=True)
class TurnState:
    prompts: int = 0
    stops: int = 0
    last_event: str = ""
    thread_id: str | None = None

    @property
    def idle(self) -> bool:
        """At a turn boundary: the last turn event was a Stop."""
        return self.last_event == "Stop"


class SessionServer:
    def __init__(self, path: Path, token: str, events: Path, *, max_spool: int = 4 << 20) -> None:
        self.path = path
        self.token = token.encode()
        self.events = events
        self.max_spool = max_spool
        self.served = 0
        self._state = TurnState()
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

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
        threading.Thread(target=self._accept, args=(sock,), name=f"session-{self.path.parent.name}",
                         daemon=True).start()

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def turns(self) -> TurnState:
        with self._lock:
            return self._state

    def _accept(self, sock: socket.socket) -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(CLIENT_SECONDS)
            data = b""
            try:
                while b"\n" not in data and len(data) <= MAX_LINE:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                line = data.split(b"\n", 1)[0]
                reply = MALFORMED if len(line) > MAX_LINE else self._handle(line)
                conn.sendall((json.dumps(reply) + "\n").encode())
            except OSError:
                return
            with self._lock:
                self.served += 1

    def _handle(self, line: bytes) -> dict[str, Any]:
        try:
            req: Any = json.loads(line)
        except ValueError:
            return MALFORMED
        if not isinstance(req, dict):
            return MALFORMED
        body = cast(dict[str, Any], req)
        token = body.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self.token):
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
            return UNSUPPORTED
        self._record(fields)
        return OK

    def _record(self, payload: dict[str, Any]) -> None:
        event = payload.get("hook_event_name")
        with self._lock:
            s = self._state
            if event == "SessionStart" and s.thread_id is None:
                sid = payload.get("session_id")
                if isinstance(sid, str) and UUID.fullmatch(sid):
                    s = replace(s, thread_id=sid)
            elif event == "UserPromptSubmit":
                s = replace(s, prompts=s.prompts + 1, last_event=event)
            elif event == "Stop":
                s = replace(s, stops=s.stops + 1, last_event=event)
            self._state = s

    def _spool(self, kind: str, payload: dict[str, Any]) -> None:
        line = (json.dumps({"type": kind, "payload": payload}) + "\n").encode()
        with self._lock:
            try:
                size = self.events.stat().st_size
            except FileNotFoundError:
                size = 0
            if size + len(line) > self.max_spool:
                return
            fd = os.open(self.events, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as fh:
                fh.write(line)
