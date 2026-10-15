"""Plan 5i part 1: session events (Stops and ws-requests) from the runtime's event logs, through the
journal's inbox, to the handlers parts 2 and 3 register by kind.

`ingest` reads what follows each session's cursor and, a page per transaction, inserts the attributable
events into the inbox (surface `session`) and moves the cursors: a failure moves nothing, so the next
pickup reads the same events, and nothing is held in memory. `dispatch` runs the workstream's pending
session rows in order: one bead's rows never run out of order, an open operation on the bead
(`OpConflict`) waits for the next pickup, and a handler that keeps failing leaves its row for a human.
Handlers are idempotent by event ID: a crash between a handler and its row's mark runs it again.
"""

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

import msgspec

from heterodyne.wsd.beads import BeadsUnavailable
from heterodyne.wsd.journal import InboxStatus, Journal, OpConflict
from heterodyne.wsd.launches import LAUNCHED
from heterodyne.wsd.runtime import AgentRuntime, EventBatch, RuntimeUnavailable, SessionEvent
from heterodyne.wsd.workstream import SessionHandler

if TYPE_CHECKING:
    from heterodyne.wsd.park import Parker

SURFACE = "session"
PAGE = 1024                     # events per key per transaction
DAMAGED_TEXT = ("session event {event_id} was accepted but its log line is damaged; what it asked for is "
                "unknown and needs an operator")
DAMAGED_TITLE = "{bead} lost a session event"      # one line, well within admind's 200: a bead ID is a slug
log = logging.getLogger(__name__)


def attributable(j: Journal, ws: str, ev: SessionEvent) -> bool | None:
    """True if the journal launched this generation for this workstream and bead, False if it never
    will, None while its launch has no outcome yet."""
    entry = j.launch(ev.session_key, ev.generation)
    if entry is None or entry.ws != ws or entry.bead != ev.bead:
        return False
    if entry.outcome is None:
        return None
    return entry.outcome == LAUNCHED


def ingest(j: Journal, runtime: AgentRuntime, ws: str) -> None:
    """Every event logged past the cursors, into the inbox, a page at a time. Raises what the runtime
    (RuntimeUnavailable) or the journal raises, with the failed page's cursors unmoved."""
    while True:
        cursors = j.session_cursors(ws)
        batch = runtime.session_events(ws, cursors, PAGE)
        with j.transaction():
            moved = _take(j, ws, cursors, batch)
        if not (batch.more and moved):
            return


def _roll(key: str, at: tuple[int, int], ends: Mapping[tuple[str, int], int]) -> tuple[int, int]:
    gen, n = at
    while ends.get((key, gen)) == n:
        gen, n = gen + 1, 0
    return gen, n


def _take(j: Journal, ws: str, cursors: Mapping[str, tuple[int, int]], batch: EventBatch) -> bool:
    by_key: dict[str, list[SessionEvent]] = {}
    for ev in batch.events:
        by_key.setdefault(ev.session_key, []).append(ev)
    moved = False
    for key in sorted(set(by_key) | {k for k, _ in batch.ends}):
        start = cursors.get(key, (1, 0))
        cursor = _roll(key, start, batch.ends)
        for ev in by_key.get(key, []):
            at = (ev.generation, ev.seq)
            if at <= cursor:
                continue                    # taken before: read again after a failure or a crash
            if at != (cursor[0], cursor[1] + 1):
                j.emit_once(ws, ev.bead, "session_event_gap", f"{key}: g{cursor[0]}:e{cursor[1]} then "
                            f"g{at[0]}:e{at[1]}", f"gap:{key}:{cursor[0]}:{cursor[1]}")
                break
            ok = attributable(j, ws, ev)
            if ok is None:
                break                       # its launch has no outcome yet: the key waits
            if ok:
                j.inbox_add(SURFACE, ev.event_id, ev.kind, msgspec.json.encode(ev).decode(), ws, ev.bead)
            cursor = _roll(key, at, batch.ends)
        if cursor != start:
            j.session_cursor_put(ws, key, *cursor)
            moved = True
    return moved


def dispatch(parker: "Parker", handlers: Mapping[str, SessionHandler], limit: int) -> None:
    j, ws = parker.d.journal, parker.ws.name
    waiting: set[str] = set()
    for row in j.inbox_pending(ws, SURFACE):
        bead = row.bead or ""
        if bead in waiting:
            continue
        if row.kind == "damaged":           # a line lost after it was accepted: a human looks; later rows run
            text = DAMAGED_TEXT.format(event_id=row.event_id)
            if parker.d.notices is not None:
                # Before the row's commit: a crash in between sends the same ref again, which `send` ignores.
                parker.d.notices.send(ws, bead, "stuck", DAMAGED_TITLE.format(bead=bead), text,
                                      f"damaged:{row.event_id}")
            with j.transaction():
                j.inbox_finish(row.seq, InboxStatus.NEEDS_HUMAN)
                j.emit(ws, bead, "session_event_damaged", text)
            continue
        handler = handlers.get(row.kind)
        if handler is None:
            j.inbox_finish(row.seq, InboxStatus.REJECTED)
            continue
        ev = msgspec.json.decode(row.payload, type=SessionEvent)
        try:
            handler(parker, ev)
        except OpConflict:
            waiting.add(bead)
            continue
        except (BeadsUnavailable, RuntimeUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 - counted against the event, never lost
            waiting.add(bead)
            if j.inbox_failed(row.seq, limit) is InboxStatus.NEEDS_HUMAN:
                j.emit(ws, bead, "session_event_needs_human",
                       f"{row.kind} {row.event_id}: {type(exc).__name__}")
            else:
                log.warning("session event %s failed: %s", row.event_id, type(exc).__name__)
            continue
        j.inbox_finish(row.seq, InboxStatus.COMMITTED)
