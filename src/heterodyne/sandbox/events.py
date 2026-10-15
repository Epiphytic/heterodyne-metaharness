"""Plan 5i part 1: one session generation's accepted events, on the host.

A Stop (once the brief is pasted) and each `ws-request` the runtime accepts are appended here, every byte
written and fsync'd, before the agent is answered, so "accepted" survives a wsd crash. The line number is
the event's ID (`<key>:g<gen>:e<n>`) and the Stop count its turn, both read back after a restart. A
request's `rid` (made by the shim per invocation) answers a resend as the first event, without logging it
again. A write that fails is cut back so the next starts a fresh line; an fsync that fails stops the log
(its outcome is unknown); opening the log to append cuts a torn last line first. Every string came from
the session and is untrusted; this module checks shape and size only.
"""

import contextlib
import errno
import json
import logging
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from heterodyne.session.server import UNAVAILABLE
from heterodyne.session.shim import CLOSE_EVIDENCE_MAX, REQUEST_BODY_MAX, RID
from heterodyne.wsd.runtime import SessionEvent, SessionEventKind

LOG_MAX = 4 << 20               # bytes per generation: a flooding session is refused, never unbounded
STOP_TEXT_MAX = 16000           # characters of a Stop's last message kept
UNSUPPORTED = {"ok": False, "error": "unsupported"}
MALFORMED = {"ok": False, "error": "malformed"}
FULL = {"ok": False, "error": "full"}
ACCEPTED: Mapping[str, str] = {
    "close": "accepted: your close request is recorded. End your turn now; the bead is closed only after "
             "a review passes.",
    "question": "accepted: your question is recorded, not answered. End your turn now. The task is parked "
                "and resumed with the answer; if the question is refused, you are told why after your turn "
                "ends.",
    "ask": "accepted: your request is recorded, not decided. End your turn now. The task is parked and "
           "resumed with the decision; if the request is refused, you are told why after your turn ends.",
}
KINDS = frozenset({"stop", *ACCEPTED})
DIAG_EVENT_LOG = "the session event log could not be written"
DIAG_EVENT_LOG_TORN = ("the session event log ended in a torn line, which was cut "
                       "(no agent was answered for it)")
DIAG_EVENT_LOG_DAMAGED = "the session event log has damaged lines; each is delivered as a damaged event"
FLAGS = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
_write_fd = os.write            # the log's own write and fsync, so a test can fail them alone
_fsync = os.fsync
log_ = logging.getLogger(__name__)


def reply(event: SessionEvent) -> dict[str, Any]:
    return {"ok": True, "ref": event.event_id, "message": ACCEPTED[event.kind]}


class EventLog:
    def __init__(self, path: Path, ws: str, bead: str, key: str, generation: int) -> None:
        self.path, self.ws, self.bead, self.key, self.generation = path, ws, bead, key, generation
        self.lock = threading.Lock()
        self.forwarding = False         # set once the brief is pasted: the probe's turn is never an event
        self.writable = False           # only a log opened to append, until a write's outcome is unknown
        self.damaged = False            # a complete line didn't decode, or a number was missing
        self._events: list[SessionEvent] = []
        self._rids: dict[str, SessionEvent] = {}
        self._size = 0                  # bytes of complete, parsed lines: the durable length
        self._fd: int | None = None
        self._dir_synced = False

    @classmethod
    def read(cls, path: Path, ws: str, bead: str, key: str, generation: int) -> "EventLog":
        """The log's complete lines, for reading only: an incomplete last line (a write no agent was
        answered for) is ignored, and nothing is written."""
        log = cls(path, ws, bead, key, generation)
        log.forwarding = True
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except FileNotFoundError:
            return log
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read()
        log._parse(raw[: raw.rfind(b"\n") + 1])
        return log

    @classmethod
    def open(cls, path: Path, ws: str, bead: str, key: str, generation: int) -> "EventLog":
        """The log, to append to (a launch, or a rebind after a restart). An incomplete last line is cut,
        and the cut fsync'd, before anything is appended. Damaged lines stay; appends go on after the last
        line. Raises OSError when the repair can't be made."""
        log = cls.read(path, ws, bead, key, generation)
        log.forwarding = False
        with contextlib.suppress(FileNotFoundError):
            fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                if os.fstat(fd).st_size != log._size:
                    os.ftruncate(fd, log._size)
                    os.fsync(fd)
                    log_.warning("%s: %s g%d", DIAG_EVENT_LOG_TORN, key, generation)
            finally:
                os.close(fd)
            log._dir_synced = True      # it outlived a restart: its directory entry is durable
        log.writable = True
        return log

    @property
    def last(self) -> int:
        with self.lock:
            return len(self._events)

    def events(self) -> list[SessionEvent]:
        with self.lock:
            return list(self._events)

    def after(self, n: int, limit: int) -> list[SessionEvent]:
        """The events numbered n+1 on, at most `limit`."""
        with self.lock:
            return self._events[n:n + limit]

    def close(self) -> None:
        with self.lock:
            self.writable = False
            if self._fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._fd)
                self._fd = None

    def stop(self, payload: Mapping[str, Any]) -> SessionEvent | None:
        """The Stop's event, or None when it isn't forwarded (before the brief) or can't be logged (D3)."""
        text = payload.get("last_assistant_message")
        kept = text[:STOP_TEXT_MAX] if isinstance(text, str) else None
        with self.lock:
            if not self.forwarding:
                return None
            ev = self._append("stop", self._turn() + 1, kept, {}, self._closing(), None)
            return ev if isinstance(ev, SessionEvent) else None

    def request(self, payload: Mapping[str, Any], ops: frozenset[str]
                ) -> tuple[SessionEvent | None, dict[str, Any]]:
        """(the new event, its reply), or (None, the first event's reply) for a resent `rid`, or (None, a
        refusal). Only a new event is delivered."""
        op, rid = payload.get("op"), payload.get("rid")
        if op not in ACCEPTED or op not in ops:
            return None, UNSUPPORTED
        if not isinstance(rid, str) or not RID.fullmatch(rid):
            return None, MALFORMED
        text: str | None = None
        body: dict[str, Any] = {}
        if op == "close":
            evidence = payload.get("evidence")
            if (not isinstance(evidence, str) or not evidence
                    or not _utf8_within(evidence, CLOSE_EVIDENCE_MAX)):
                return None, MALFORMED
            text = evidence
        else:
            raw = payload.get("body")
            if not isinstance(raw, dict):
                return None, MALFORMED
            body = cast(dict[str, Any], raw)
            if not _json_within(body, REQUEST_BODY_MAX):
                return None, MALFORMED
        with self.lock:
            if not self.forwarding:
                return None, UNSUPPORTED
            if rid in self._rids:
                return None, reply(self._rids[rid])
            got = self._append(cast(SessionEventKind, op), self._turn() + 1, text, body, False, rid)
        return (got, reply(got)) if isinstance(got, SessionEvent) else (None, got)

    # --- under self.lock ---

    def _turn(self) -> int:
        """The last Stop's turn: a damaged line may have been a Stop, so this is read, not counted."""
        return max((e.turn for e in self._events if e.kind == "stop"), default=0)

    def _closing(self) -> bool:
        return any(e.kind == "close" for e in self._events)

    def _append(self, kind: SessionEventKind, turn: int, text: str | None, body: dict[str, Any],
                closing: bool, rid: str | None) -> SessionEvent | dict[str, Any]:
        if not self.writable:
            return UNAVAILABLE
        n = len(self._events) + 1
        row = {"n": n, "kind": kind, "turn": turn, "text": text, "body": body, "close_requested": closing,
               "rid": rid}
        line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        if self._size + len(line) > LOG_MAX:
            return FULL
        try:
            self._write(line)
        except OSError as exc:
            log_.warning("%s: %s", DIAG_EVENT_LOG, type(exc).__name__)
            return UNAVAILABLE
        return self._add(n, kind, turn, text, body, closing, rid)

    def _write(self, line: bytes) -> None:
        """Every byte, then fsync; the event exists only once this returns."""
        if self._fd is None:
            self._fd = os.open(self.path, FLAGS, 0o600)
        if not self._dir_synced:
            _sync_dir(self.path.parent)         # a new file's entry is durable before its first reply
            self._dir_synced = True
        try:
            view = memoryview(line)
            while view:
                done = _write_fd(self._fd, view)
                if done <= 0:
                    raise OSError(errno.EIO, "no progress writing the session event log")
                view = view[done:]
        except OSError:
            self._cut()
            raise
        try:
            _fsync(self._fd)
        except OSError:
            self.writable = False               # what reached the disk is unknown: never fsync'd again
            self._cut()
            raise
        self._size += len(line)

    def _cut(self) -> None:
        """Back to the last durable line, so a later append starts a fresh one. If even that fails, the
        log takes no more appends."""
        if self._fd is None:
            return
        try:
            os.ftruncate(self._fd, self._size)
            _fsync(self._fd)
        except OSError:
            self.writable = False

    def _parse(self, raw: bytes) -> None:
        """Every complete line, in order. A line that doesn't decode, or repeats a number, is a damaged
        event at the next number; a number that skips ahead leaves a damaged event for each one skipped."""
        for line in raw.splitlines(keepends=True):
            self._size += len(line)
            expect = len(self._events) + 1
            try:
                row = json.loads(line)
                if (not isinstance(row["n"], int) or row["n"] < expect or row["kind"] not in KINDS
                        or not isinstance(row["turn"], int) or not isinstance(row["body"], dict)
                        or not (row["text"] is None or isinstance(row["text"], str))
                        or not (row["rid"] is None or isinstance(row["rid"], str))
                        or not isinstance(row["close_requested"], bool)):
                    raise ValueError("not a log line")
            except (ValueError, KeyError, TypeError):
                self._damage(expect)
                continue
            for missing in range(expect, row["n"]):
                self._damage(missing)
            self._add(row["n"], row["kind"], row["turn"], row["text"], row["body"],
                      row["close_requested"], row["rid"])

    def _damage(self, n: int) -> None:
        if not self.damaged:
            log_.warning("%s: %s g%d", DIAG_EVENT_LOG_DAMAGED, self.key, self.generation)
        self.damaged = True
        self._add(n, "damaged", 0, None, {}, False, None)

    def _add(self, n: int, kind: str, turn: int, text: str | None, body: dict[str, Any], closing: bool,
             rid: str | None) -> SessionEvent:
        ev = SessionEvent(f"{self.key}:g{self.generation}:e{n}", cast(SessionEventKind, kind), self.ws,
                          self.bead, self.key, self.generation, turn, text, body, closing)
        self._events.append(ev)
        if rid is not None:
            self._rids[rid] = ev
        return ev


def _utf8_within(text: str, limit: int) -> bool:
    try:
        return len(text.encode("utf-8")) <= limit
    except UnicodeEncodeError:          # a lone surrogate
        return False


def _json_within(value: object, limit: int) -> bool:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= limit
    except (UnicodeEncodeError, ValueError):
        return False


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
