"""Plan 5i part 1 T6: session events by cursor through the inbox to their handlers."""

import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fakes.checkpoints import SimulatedCrash
from wsd_env import WS, Rig, make_rig

from heterodyne.wsd import scheduler as scheduler_mod
from heterodyne.wsd import session_events
from heterodyne.wsd.journal import InboxStatus, Journal, OpConflict
from heterodyne.wsd.launches import LAUNCHED
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import LimitSeen, SessionEvent
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.session_events import SURFACE, ingest
from heterodyne.wsd.workstream import NoticeKind

B = "btq-1"


@pytest.fixture(autouse=True)
def _close_journals(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    opened: list[Journal] = []
    real = Journal.__init__

    def tracked(self: Journal, *args: Any, **kwargs: Any) -> None:
        opened.append(self)
        real(self, *args, **kwargs)

    monkeypatch.setattr(Journal, "__init__", tracked)
    yield
    for each in opened:
        each.close()


class Handler:
    def __init__(self, fail: Sequence[BaseException] = (), seen: list[str] | None = None) -> None:
        self.seen: list[str] = [] if seen is None else seen
        self.fail = list(fail)

    def __call__(self, parker: Parker, event: SessionEvent) -> None:
        if self.fail:
            raise self.fail.pop(0)
        self.seen.append(event.event_id)


def started(tmp_path: Path, **handlers: Handler) -> Rig:
    rig = make_rig(tmp_path)
    rig.handlers = dict(handlers)
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    return rig


def ev(rig: Rig, kind: str = "stop", n: int = 1, gen: int = 1, **kw: Any) -> SessionEvent:
    key = rig.key(B)
    return SessionEvent(f"{key}:g{gen}:e{n}", kind, WS, B, key, gen, n, **kw)  # type: ignore[arg-type]


def io_error() -> sqlite3.OperationalError:
    """A disk I/O error as SQLite raises it: the journal reads its code to tell contention apart."""
    exc = sqlite3.OperationalError("disk I/O error")
    exc.sqlite_errorcode = sqlite3.SQLITE_IOERR
    exc.sqlite_errorname = "SQLITE_IOERR"
    return exc


def ids(rig: Rig) -> list[str]:
    rows = rig.journal.db.execute("SELECT event_id FROM inbox WHERE surface = ? ORDER BY seq", (SURFACE,))
    return [r[0] for r in rows]


def statuses(rig: Rig) -> list[str]:
    rows = rig.journal.db.execute("SELECT status FROM inbox WHERE surface = ? ORDER BY seq", (SURFACE,))
    return [r[0] for r in rows]


def test_events_reach_their_handlers_in_order_once(tmp_path: Path) -> None:
    order: list[str] = []
    stop, close = Handler(seen=order), Handler(seen=order)
    rig = started(tmp_path, stop=stop, close=close)
    rig.runtime.report(WS, ev(rig, "stop", 2, close_requested=True), ev(rig, "close", 1, text="done"))
    rig.pickup()
    rig.runtime.report(WS, ev(rig, "stop", 2, close_requested=True))      # sent again: logged once
    rig.pickup()
    assert order == [f"{rig.key(B)}:g1:e1", f"{rig.key(B)}:g1:e2"]        # log order, not report order
    assert statuses(rig) == [InboxStatus.COMMITTED.value] * 2
    assert rig.journal.session_cursors(WS) == {rig.key(B): (1, 2)}


def test_an_event_that_reaches_wsd_before_the_one_it_follows_waits_for_it(tmp_path: Path) -> None:
    # P1: e2 (the Stop) is readable one pickup before e1 (its request); e1 still runs first.
    order: list[str] = []
    ask, stop = Handler(seen=order), Handler(seen=order)
    rig = started(tmp_path, ask=ask, stop=stop)
    rig.runtime.event(WS, ev(rig, "stop", 2))
    rig.pickup()
    assert order == [] and ids(rig) == [] and rig.journal.session_cursors(WS) == {}
    rig.runtime.event(WS, ev(rig, "ask", 1, body={"effect": "x"}))
    rig.pickup()
    assert order == [f"{rig.key(B)}:g1:e1", f"{rig.key(B)}:g1:e2"]


def test_a_held_bead_holds_only_itself(tmp_path: Path) -> None:
    # P3: btq-1's close waits on an OpConflict; btq-2's events run meanwhile.
    seen: list[str] = []
    close, stop = Handler([OpConflict("busy")] * 2, seen=seen), Handler(seen=seen)
    rig = started(tmp_path, close=close, stop=stop)
    k1, k2 = rig.key(B), "sk-other"
    rig.runtime.report(WS, ev(rig, "close", 1, text="done"), ev(rig, "stop", 2))
    # A workstream runs one bead at a time, so btq-2's row is one left from its own (parked) session.
    other = SessionEvent(f"{k2}:g1:e1", "stop", WS, "btq-2", k2, 1, 1)
    rig.journal.inbox_add(SURFACE, other.event_id, "stop", msgspec.json.encode(other).decode(), WS, "btq-2")
    rig.pickup()
    rig.pickup()
    assert seen == [f"{k2}:g1:e1"]                                 # btq-1's stop waits behind its close
    rig.pickup()
    assert seen == [f"{k2}:g1:e1", f"{k1}:g1:e1", f"{k1}:g1:e2"]


def test_a_damaged_event_needs_a_human_and_the_beads_later_events_run(tmp_path: Path) -> None:
    # The team-lead's ruling: a real gap fails closed for its bead, with fixed text, and is consumed.
    damaged, stop = Handler(), Handler()
    rig = started(tmp_path, damaged=damaged, stop=stop)
    rig.runtime.report(WS, SessionEvent(f"{rig.key(B)}:g1:e1", "damaged", WS, B, rig.key(B), 1, 0),
                       ev(rig, "stop", 2))
    rig.pickup()
    assert damaged.seen == [] and stop.seen == [f"{rig.key(B)}:g1:e2"]      # no handler ever sees it
    assert statuses(rig) == [InboxStatus.NEEDS_HUMAN.value, InboxStatus.COMMITTED.value]
    [note] = [e for e in rig.journal.events_since(0) if e.kind == "session_event_damaged"]
    assert note.detail == session_events.DAMAGED_TEXT.format(event_id=f"{rig.key(B)}:g1:e1")


class Sink:
    """Part 2's `Notices.send` as part 1 relies on it: never raises on transport, and tries each ref at
    most once (it journals the ref in flight first, and never retries an in-flight one). `lose` is a crash
    mid-post: the ref is taken and nothing is posted."""

    def __init__(self, lose: bool = False) -> None:
        self.lose = lose
        self.calls: list[str] = []
        self.taken: set[str] = set()
        self.sent: list[tuple[str, str | None, str, str, str, str]] = []

    def send(self, ws: str, bead: str | None, kind: NoticeKind, title: str, body: str, ref: str) -> None:
        self.calls.append(ref)
        if ref in self.taken:
            return
        self.taken.add(ref)
        if not self.lose:
            self.sent.append((ws, bead, kind, title, body, ref))


def with_sink(tmp_path: Path, sink: Sink | None) -> Rig:
    rig = make_rig(tmp_path)
    rig.notices = sink
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    return rig


def test_a_damaged_event_sends_at_most_one_stuck_notice_with_fixed_text(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = Sink()
    rig = with_sink(tmp_path, sink)
    event_id = f"{rig.key(B)}:g1:e1"
    rig.runtime.report(WS, SessionEvent(event_id, "damaged", WS, B, rig.key(B), 1, 0))
    real = rig.journal.inbox_finish
    crashed: list[int] = []

    def crash_once(seq: int, status: InboxStatus) -> None:
        if not crashed:
            crashed.append(seq)
            raise SimulatedCrash("after the send, before the row's commit")
        real(seq, status)

    monkeypatch.setattr(rig.journal, "inbox_finish", crash_once)
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.pickup()                                                   # dispatched again: the same ref
    rig.pickup()                                                   # consumed: nothing more
    assert sink.calls == [f"damaged:{event_id}"] * 2
    assert sink.sent == [(WS, B, "stuck", f"{B} lost a session event",
                          session_events.DAMAGED_TEXT.format(event_id=event_id), f"damaged:{event_id}")]
    assert statuses(rig) == [InboxStatus.NEEDS_HUMAN.value]


def test_a_stuck_notice_lost_mid_post_leaves_the_journal_entry_and_the_row(tmp_path: Path) -> None:
    """Part 2's notices are at most once (cycle 2, blocker 2): a crash mid-post can mean no notice. The
    loss is still recorded: the row is needs_human and the journal entry stands, and nothing is posted
    later."""
    sink = Sink(lose=True)
    rig = with_sink(tmp_path, sink)
    event_id = f"{rig.key(B)}:g1:e1"
    rig.runtime.report(WS, SessionEvent(event_id, "damaged", WS, B, rig.key(B), 1, 0))
    rig.pickup()
    rig.pickup()
    assert sink.sent == [] and sink.calls == [f"damaged:{event_id}"]
    assert statuses(rig) == [InboxStatus.NEEDS_HUMAN.value]
    assert [e.detail for e in rig.journal.events_since(0) if e.kind == "session_event_damaged"] == [
        session_events.DAMAGED_TEXT.format(event_id=event_id)]


def test_with_no_notice_client_a_damaged_event_is_still_journaled(tmp_path: Path) -> None:
    rig = with_sink(tmp_path, None)
    event_id = f"{rig.key(B)}:g1:e1"
    rig.runtime.report(WS, SessionEvent(event_id, "damaged", WS, B, rig.key(B), 1, 0))
    rig.pickup()
    assert statuses(rig) == [InboxStatus.NEEDS_HUMAN.value]
    assert [e.detail for e in rig.journal.events_since(0) if e.kind == "session_event_damaged"] == [
        session_events.DAMAGED_TEXT.format(event_id=event_id)]


def test_an_event_without_a_handler_is_rejected(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.report(WS, ev(rig))
    rig.pickup()
    assert statuses(rig) == [InboxStatus.REJECTED.value]


def test_an_op_conflict_waits_and_holds_back_the_beads_later_events(tmp_path: Path) -> None:
    close, stop = Handler([OpConflict("busy")]), Handler()
    rig = started(tmp_path, close=close, stop=stop)
    rig.runtime.report(WS, ev(rig, "close", 1, text="done"), ev(rig, "stop", 2))
    rig.pickup()
    assert stop.seen == [] and statuses(rig) == ["pending", "pending"]
    rig.pickup()
    assert len(close.seen) == 1 and len(stop.seen) == 1


def test_a_failing_handler_escalates_after_the_limit(tmp_path: Path) -> None:
    stop = Handler([RuntimeError("x")] * 3)
    rig = started(tmp_path, stop=stop)
    rig.runtime.report(WS, ev(rig))
    for _ in range(3):
        rig.pickup()
    assert statuses(rig) == [InboxStatus.NEEDS_HUMAN.value]
    assert any(e.kind == "session_event_needs_human" for e in rig.journal.events_since(0))


def test_an_event_for_another_launch_is_passed(tmp_path: Path) -> None:
    stop = Handler()
    rig = started(tmp_path, stop=stop)
    rig.runtime.report(WS, ev(rig, gen=9), SessionEvent("sk-x:g1:e1", "stop", WS, B, "sk-x", 1, 1))
    rig.pickup()
    assert stop.seen == [] and statuses(rig) == []
    assert rig.journal.session_cursors(WS) == {rig.key(B): (9, 1), "sk-x": (1, 1)}     # passed, not stuck
    rig.runtime.report(WS, ev(rig, gen=9, n=2))
    rig.pickup()
    assert statuses(rig) == []


def test_an_event_whose_launch_has_no_outcome_yet_waits_for_it(tmp_path: Path) -> None:
    stop = Handler()
    rig = started(tmp_path, stop=stop)
    first = rig.journal.launch(rig.key(B), 1)
    assert first is not None
    rig.journal.launch_insert(msgspec.structs.replace(first, generation=2, outcome=None, native_id=None))
    rig.runtime.report(WS, ev(rig, gen=2))
    rig.pickup()
    assert statuses(rig) == [] and rig.journal.session_cursors(WS) == {rig.key(B): (2, 0)}
    rig.journal.launch_set(rig.key(B), 2, "outcome", LAUNCHED)
    rig.pickup()
    assert stop.seen == [f"{rig.key(B)}:g2:e1"]


def test_a_gap_stops_the_key_and_is_reported_once(tmp_path: Path) -> None:
    stop = Handler()
    rig = started(tmp_path, stop=stop)
    rig.runtime.report(WS, ev(rig, n=1), ev(rig, n=3))
    rig.pickup()
    rig.pickup()
    assert stop.seen == [f"{rig.key(B)}:g1:e1"]
    assert [e.kind for e in rig.journal.events_since(0)].count("session_event_gap") == 1


def test_a_final_generation_rolls_the_cursor_on(tmp_path: Path) -> None:
    stop = Handler()
    rig = started(tmp_path, stop=stop)
    rig.runtime.report(WS, ev(rig, n=1))
    rig.runtime.end_log(rig.key(B), 1)
    rig.pickup()
    assert rig.journal.session_cursors(WS) == {rig.key(B): (2, 0)}


@pytest.mark.parametrize("fail_at", [1, 2000])
def test_a_failed_page_loses_nothing_however_many_events_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             fail_at: int) -> None:
    # Codex cycle 1 #4: 4,500 events (more than the old 4,096 bound), one failure, every event once in order.
    rig = started(tmp_path)
    rig.runtime.report(WS, *(ev(rig, n=n) for n in range(1, 4501)))
    real = rig.journal.inbox_add

    def flaky(surface: str, event_id: str, *args: Any, **kwargs: Any) -> int | None:
        if event_id.endswith(f":e{fail_at}"):
            monkeypatch.setattr(rig.journal, "inbox_add", real)
            raise io_error()
        return real(surface, event_id, *args, **kwargs)

    monkeypatch.setattr(rig.journal, "inbox_add", flaky)
    with pytest.raises(sqlite3.OperationalError):
        ingest(rig.journal, rig.runtime, WS)
    kept = (fail_at - 1) // session_events.PAGE * session_events.PAGE      # whole pages before the failure
    assert len(ids(rig)) == kept and rig.journal.session_cursors(WS).get(rig.key(B), (1, 0)) == (1, kept)
    ingest(rig.journal, rig.runtime, WS)
    assert ids(rig) == [f"{rig.key(B)}:g1:e{n}" for n in range(1, 4501)]


def test_a_journal_failure_in_a_pickup_keeps_the_events_for_the_next(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    stop = Handler()
    rig = started(tmp_path, stop=stop)
    rig.runtime.report(WS, ev(rig))
    real = rig.journal.session_cursor_put

    def once(*args: Any) -> None:
        monkeypatch.setattr(rig.journal, "session_cursor_put", real)
        raise io_error()

    monkeypatch.setattr(rig.journal, "session_cursor_put", once)
    with pytest.raises(sqlite3.OperationalError):
        rig.pickup()
    assert statuses(rig) == []                              # the row rolled back with the cursor
    rig.pickup()
    assert stop.seen == [f"{rig.key(B)}:g1:e1"]


def test_events_go_in_after_the_usage_signals_and_before_the_sweep(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    class Recording(Handler):
        def __call__(self, parker: Parker, event: SessionEvent) -> None:
            order.append("handler")

    rig = started(tmp_path, stop=Recording())
    real_signal, real_sweep = rig.sched._signal, scheduler_mod.sweep

    def signal(s: Any, now: int) -> None:
        order.append("signal")
        real_signal(s, now)

    def swept(parker: Parker) -> Any:
        order.append("sweep")
        return real_sweep(parker)

    monkeypatch.setattr(rig.sched, "_signal", signal)
    monkeypatch.setattr(scheduler_mod, "sweep", swept)
    rig.runtime.report(WS, LimitSeen(rig.key(B), 1, None), ev(rig))
    rig.pickup()
    assert order[:3] == ["signal", "handler", "sweep"]
