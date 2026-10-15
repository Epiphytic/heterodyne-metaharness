"""Plan 5i part 2 Task 5: refusals delivered late, and the pending notes `advance` steers (part 1's D1).

A request is answered `accepted` before any handler runs, so a refused one is owed a note: steered into
the session once its turn ends, or carried by its next resume brief.
"""

import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes.checkpoints import CrashAt, SimulatedCrash
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wsd_env import WS, Rig, make_rig

from heterodyne.sandbox.runtime import STEER_MAX
from heterodyne.wsd import asks
from heterodyne.wsd.asks import (
    ASK_WITHHELD,
    OPEN_ASK,
    QUESTION_INCOMPLETE,
    REFUSED_NOTE,
    STEER_UNCERTAIN,
    STEER_UNCERTAIN_BODY,
    AskBridge,
    steer_text,
)
from heterodyne.wsd.journal import InboxStatus, Journal
from heterodyne.wsd.notices import Notices
from heterodyne.wsd.runtime import SessionEvent, SteerRefused, SteerUncertain
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.session_events import SURFACE
from heterodyne.wsd.settings import RelaySettings
from heterodyne.wsd.states import BeadState

B = "btq-1"
SECRET = "sk-" + "a" * 40                   # built here, so no secret is committed


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


def relay_of(rig: Rig) -> RelaySettings:
    """admind's socket, never started here: a question these tests make is owed, never posted."""
    return RelaySettings(True, rig.root / "admind" / "ask.sock", 2, 6, 90.0)


def bridge_of(rig: Rig) -> AskBridge:
    return AskBridge(rig.journal, rig.beads, rig.runtime, None, relay_of(rig), rig.clock, ws=WS, cp=rig.cp)


def started(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.asks_for = bridge_of
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    return rig


class Posts:
    """admind's notice post, recorded: each call is one notice on the operator's phone."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, sock: Path, *, poster: str, title: str, body: str = "", timeout: float = 30.0) -> str:
        self.sent.append((title, body))
        return f"n{len(self.sent)}"


def with_notices(tmp_path: Path, posts: Posts) -> Rig:
    def build(rig: Rig) -> AskBridge:
        notices = Notices(rig.journal, rig.root / "ask.sock", clock=rig.clock, cp=rig.cp, post=posts)
        return AskBridge(rig.journal, rig.beads, rig.runtime, notices, relay_of(rig), rig.clock, ws=WS,
                         cp=rig.cp)

    rig = make_rig(tmp_path)
    rig.asks_for = build
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    return rig


def bridge(rig: Rig) -> AskBridge:
    assert rig.bridge is not None
    return rig.bridge


def ev(rig: Rig, kind: str, n: int, gen: int = 1, turn: int = 1, **kw: Any) -> SessionEvent:
    key = rig.key(B)
    return SessionEvent(f"{key}:g{gen}:e{n}", kind, WS, B, key, gen, turn, **kw)  # type: ignore[arg-type]


def refused(event_id: str, why: str) -> str:
    return REFUSED_NOTE.format(ref=event_id, why=why)


def attempts(rig: Rig, ref: str) -> int:
    rows = rig.journal.db.execute("SELECT steer_attempts FROM resume_notes WHERE ref = ?", (ref,)).fetchall()
    [(n,)] = rows
    return int(n)


def delivered(rig: Rig, ref: str) -> bool:
    [(at,)] = rig.journal.db.execute("SELECT delivered_at FROM resume_notes WHERE ref = ?", (ref,)).fetchall()
    return at is not None


def nothing_created(rig: Rig) -> None:
    """No bead, no ask and no park: a refusal creates nothing but its row and its note. Every session
    event was handled, none failed (a Stop taken for a question would fail here, before the question half)."""
    assert set(rig.world.beads) == {B}
    rows = rig.journal.db.execute("SELECT status FROM inbox WHERE surface = ?", (SURFACE,)).fetchall()
    assert {r[0] for r in rows} <= {InboxStatus.COMMITTED.value}
    assert rig.journal.db.execute("SELECT count(*) FROM asks").fetchone() == (0,)
    assert rig.state(B) == BeadState.RUNNING.value
    assert rig.journal.ops_open(WS) == []


def park_and_resume(rig: Rig) -> None:
    """Park the running bead for the operator, release it, then pick up: the pickup resumes it."""
    rig.parker.park(B, (), "test", hold=True)
    rig.parker.release(B, "test")
    rig.pickup()


def test_a_refused_request_is_steered_after_its_turn_ends(tmp_path: Path) -> None:
    rig = started(tmp_path)
    key = rig.key(B)
    question, stop = ev(rig, "question", 1, body={}), ev(rig, "stop", 2, text="done for now")
    rig.runtime.event(WS, question, stop)
    rig.pickup()
    note = refused(question.event_id, QUESTION_INCOMPLETE)
    assert rig.runtime.steers == [(key, 1, steer_text([note]))]
    assert delivered(rig, f"refused:{question.event_id}")
    found = rig.journal.refusal(question.event_id)
    assert found is not None and (found.text, found.generation, found.turn) == (note, 1, 1)
    nothing_created(rig)
    rig.pickup()
    assert len(rig.runtime.steers) == 1                     # delivered: never steered again


def test_the_stop_after_a_refused_request_posts_no_question(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.steer_error = SteerRefused("mid-turn")      # the refusal stays owed past this pickup
    question = ev(rig, "question", 1, body={"text": ""})
    rig.runtime.event(WS, question, ev(rig, "stop", 2, text="What colour should the widget be?"))
    rig.pickup()
    assert rig.runtime.steers == [] and not delivered(rig, f"refused:{question.event_id}")
    nothing_created(rig)


def test_a_refused_steer_is_retried_at_the_next_pickup(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.steer_error = SteerRefused("mid-turn")
    question = ev(rig, "question", 1, body={"text": 7})
    rig.runtime.event(WS, question, ev(rig, "stop", 2))
    rig.pickup()
    ref = f"refused:{question.event_id}"
    assert rig.runtime.steers == [] and not delivered(rig, ref) and attempts(rig, ref) == 0
    nothing_created(rig)
    rig.pickup()
    note = refused(question.event_id, QUESTION_INCOMPLETE)
    assert rig.runtime.steers == [(rig.key(B), 1, steer_text([note]))]
    assert delivered(rig, ref) and attempts(rig, ref) == 1


def test_an_uncertain_steer_is_delivered_uncertain_and_never_sent_again(tmp_path: Path) -> None:
    """Part 1's contract (the lead's ruling, against the plan's three retries): one uncertain steer marks
    its notes delivered, sends one stuck notice, and nothing is steered into that generation again."""
    posts = Posts()
    rig = with_notices(tmp_path, posts)
    rig.runtime.steer_error = SteerUncertain("the paste may have landed")
    question = ev(rig, "question", 1, body={})
    rig.runtime.event(WS, question, ev(rig, "stop", 2))
    rig.pickup()
    ref = f"refused:{question.event_id}"
    assert rig.runtime.steers == [] and delivered(rig, ref) and attempts(rig, ref) == 1
    assert posts.sent == [(STEER_UNCERTAIN.format(bead=B), STEER_UNCERTAIN_BODY)]
    nothing_created(rig)
    rig.journal.note_add(WS, B, "answer:later", "a later note")
    rig.pickup()
    rig.pickup()                                            # the runtime's mark refuses every later steer
    assert rig.runtime.steers == [] and not delivered(rig, "answer:later")
    assert attempts(rig, "answer:later") == 0               # refused, so given back: never a reason to resend
    assert len(posts.sent) == 1
    park_and_resume(rig)
    brief = rig.runtime.launches[-1].brief
    assert "a later note" in brief and refused(question.event_id, QUESTION_INCOMPLETE) not in brief
    rig.pickup()
    assert rig.runtime.steers == [] and len(posts.sent) == 1


def test_the_uncertain_steer_mark_survives_a_restart(tmp_path: Path) -> None:
    """An overflowing brief's remainder, steered uncertainly: after a restart of wsd on the reopened
    journal, it is neither steered again nor carried by the next brief, and the operator heard once."""
    posts = Posts()
    rig = with_notices(tmp_path, posts)
    texts = [f"note {n}: " + "n" * 6000 for n in range(6)]
    for n, text in enumerate(texts):
        rig.journal.note_add(WS, B, f"answer:{n}", text)
    rig.runtime.steer_error = SteerRefused("mid-turn")
    rig.pickup()
    park_and_resume(rig)                                    # the brief carries notes 0 and 1
    rig.runtime.steer_error = SteerUncertain("the paste may have landed")
    rig.pickup()                                            # 2 and 3: uncertain
    assert [delivered(rig, f"answer:{n}") for n in range(6)] == [True] * 4 + [False] * 2
    rig.restart()
    for _ in range(3):
        rig.pickup()                                        # 4 and 5 are refused by the mark, given back
    assert rig.runtime.steers == [] and attempts(rig, "answer:4") == 0
    park_and_resume(rig)
    brief = rig.runtime.launches[-1].brief
    assert [n for n, t in enumerate(texts) if t in brief] == [4, 5]
    assert all(delivered(rig, f"answer:{n}") for n in range(6))
    rig.pickup()
    assert rig.runtime.steers == [] and len(posts.sent) == 1


def test_a_notice_a_crash_cut_off_is_sent_at_the_next_pickup(tmp_path: Path) -> None:
    posts = Posts()
    rig = with_notices(tmp_path, posts)
    rig.restart(CrashAt("asks.uncertain"))                  # the mark committed, the notice not yet sent
    rig.runtime.steer_error = SteerUncertain("the paste may have landed")
    rig.journal.note_add(WS, B, "answer:1", "one")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    assert posts.sent == [] and delivered(rig, "answer:1")
    rig.restart()
    rig.pickup()
    rig.pickup()
    assert posts.sent == [(STEER_UNCERTAIN.format(bead=B), STEER_UNCERTAIN_BODY)]
    assert rig.runtime.steers == []


def uncertain_events(rig: Rig) -> int:
    [(n,)] = rig.journal.db.execute("SELECT count(*) FROM events WHERE kind = ?",
                                    (asks.STEER_UNCERTAIN_EVENT,)).fetchall()
    return int(n)


def crashed_after_the_steer(tmp_path: Path, posts: Posts) -> tuple[Rig, str]:
    """A refusal steered, then wsd died before journaling the steer's outcome; wsd restarted."""
    rig = with_notices(tmp_path, posts)
    rig.restart(CrashAt("asks.steered!"))
    question = ev(rig, "question", 1, body={})
    rig.runtime.event(WS, question, ev(rig, "stop", 2))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    assert len(rig.runtime.steers) == 1 and posts.sent == []      # pasted, then wsd died before marking it
    rig.restart()
    return rig, question.event_id


def test_a_steer_a_crash_cut_off_is_delivered_uncertain_and_never_resent(tmp_path: Path) -> None:
    """The lead's B1 ruling: a steer whose outcome was never journaled may have arrived. The next pickup
    marks its notes delivered-uncertain and tells the operator once; nothing sends them again."""
    posts = Posts()
    rig, event_id = crashed_after_the_steer(tmp_path, posts)
    ref = f"refused:{event_id}"
    assert not delivered(rig, ref)
    rig.pickup()
    rig.pickup()
    assert len(rig.runtime.steers) == 1 and delivered(rig, ref)
    assert posts.sent == [(STEER_UNCERTAIN.format(bead=B), STEER_UNCERTAIN_BODY)]
    assert uncertain_events(rig) == 1 and rig.journal.steers_unended(WS) == []
    rig.restart()
    park_and_resume(rig)
    assert refused(event_id, QUESTION_INCOMPLETE) not in rig.runtime.launches[-1].brief
    rig.pickup()
    assert len(rig.runtime.steers) == 1 and len(posts.sent) == 1 and uncertain_events(rig) == 1


def test_a_brief_before_the_next_pickup_never_carries_a_steer_a_crash_cut_off(tmp_path: Path) -> None:
    """Startup recovery may compose a resume brief before any pickup: the steer is reconciled there too."""
    posts = Posts()
    rig, event_id = crashed_after_the_steer(tmp_path, posts)
    batch = bridge(rig).notes_for(WS, B)
    assert refused(event_id, QUESTION_INCOMPLETE) not in [t for _, t in batch.notes]
    assert delivered(rig, f"refused:{event_id}") and uncertain_events(rig) == 1
    assert posts.sent == []                                 # the notice goes out at the next pickup
    rig.pickup()
    rig.pickup()
    assert posts.sent == [(STEER_UNCERTAIN.format(bead=B), STEER_UNCERTAIN_BODY)]
    assert len(rig.runtime.steers) == 1 and uncertain_events(rig) == 1


def test_an_ended_steer_leaves_nothing_to_reconcile(tmp_path: Path) -> None:
    """Negative control: every outcome (delivered, refused, uncertain) ends the started record."""
    rig = started(tmp_path)
    rig.journal.note_add(WS, B, "answer:1", "one")
    rig.runtime.steer_error = SteerRefused("mid-turn")
    rig.pickup()
    assert rig.journal.steers_unended(WS) == [] and not delivered(rig, "answer:1")
    rig.pickup()
    assert rig.journal.steers_unended(WS) == [] and delivered(rig, "answer:1")
    rig.journal.note_add(WS, B, "answer:2", "two")
    rig.runtime.steer_error = SteerUncertain("the paste may have landed")
    rig.pickup()
    assert rig.journal.steers_unended(WS) == [] and uncertain_events(rig) == 1
    rig.restart()
    rig.pickup()
    assert uncertain_events(rig) == 1


def inbox_status(rig: Rig, event_id: str) -> list[str]:
    rows = rig.journal.db.execute("SELECT status FROM inbox WHERE event_id = ?", (event_id,)).fetchall()
    return [str(r[0]) for r in rows]


def test_the_stop_of_a_refused_turn_is_held_after_its_note_is_steered(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """The lead's B2 ordering: the refusal, then its turn's Stop logged after the pickup's ingest and
    before its advance, which steers the note. The next pickup takes that Stop: held, no question."""
    rig = started(tmp_path)
    question, stop = ev(rig, "question", 1, body={}), ev(rig, "stop", 2, text="Which colour?")
    real = bridge(rig).advance

    def late_stop(now: int) -> None:
        rig.runtime.report(WS, stop)                        # logged after this pickup's ingest
        real(now)

    monkeypatch.setattr(bridge(rig), "advance", late_stop)
    rig.runtime.event(WS, question)
    rig.pickup()
    note = refused(question.event_id, QUESTION_INCOMPLETE)
    assert rig.runtime.steers == [(rig.key(B), 1, steer_text([note]))]
    assert delivered(rig, f"refused:{question.event_id}")
    rig.pickup()
    assert inbox_status(rig, stop.event_id) == [InboxStatus.COMMITTED.value]
    nothing_created(rig)
    later = ev(rig, "stop", 3, turn=2, text="Which colour?")   # the steered turn's own end: not held
    rig.runtime.event(WS, later)
    rig.pickup()
    row = rig.journal.ask(f"q:{WS}:{B}:1:2")
    assert row is not None and row.child is not None        # a question: owed, as admind isn't running
    assert rig.state(B) == BeadState.WAITING_INPUT.value


def test_a_steered_refusal_is_not_repeated_in_the_resume_brief(tmp_path: Path) -> None:
    rig = started(tmp_path)
    question = ev(rig, "question", 1, body={})
    rig.runtime.event(WS, question, ev(rig, "stop", 2))
    rig.pickup()
    note = refused(question.event_id, QUESTION_INCOMPLETE)
    assert rig.runtime.steers == [(rig.key(B), 1, steer_text([note]))]
    rig.runtime.event(WS, ev(rig, "stop", 3, turn=2, text="Which colour?"))
    rig.pickup()                                            # a question: the task parks on its bead
    ref = f"q:{WS}:{B}:1:2"
    row = rig.journal.ask(ref)
    assert row is not None and row.child is not None
    assert rig.state(B) == BeadState.WAITING_INPUT.value
    # Task 6's part, by hand: the answer as a note, the ask delivered, the question bead closed.
    rig.journal.note_add(WS, B, f"answer:{ref}", "Paint it blue.")
    with rig.journal.transaction():
        rig.journal.ask_set(ref, state="delivered")
    rig.world.beads[row.child].status = "closed"
    for _ in range(3):
        rig.pickup()
    assert len(rig.runtime.launches) == 2
    brief = rig.runtime.launches[-1].brief
    assert "Paint it blue." in brief and note not in brief


def test_a_refusal_for_a_session_that_ended_rides_the_next_brief(tmp_path: Path) -> None:
    rig = started(tmp_path)
    question = ev(rig, "question", 1, body={})
    rig.runtime.lifetime(rig.key(B))                        # stopped before its Stop reached wsd
    rig.runtime.event(WS, question)
    rig.pickup()
    note = refused(question.event_id, QUESTION_INCOMPLETE)
    assert rig.runtime.steers == []
    assert len(rig.runtime.launches) == 2 and note in rig.runtime.launches[-1].brief
    assert delivered(rig, f"refused:{question.event_id}")
    rig.pickup()
    assert rig.runtime.steers == []                         # the brief carried it: nothing more to steer


def test_an_overflowing_briefs_remainder_is_steered_after_the_resume(tmp_path: Path) -> None:
    rig = started(tmp_path)
    texts = [f"note {n}: " + "n" * 6000 for n in range(6)]
    for n, text in enumerate(texts):
        rig.journal.note_add(WS, B, f"answer:{n}", text)
    rig.runtime.steer_error = SteerRefused("mid-turn")      # the running session takes none of them
    rig.pickup()
    assert rig.runtime.steers == []
    park_and_resume(rig)
    brief = rig.runtime.launches[-1].brief
    carried = [n for n, t in enumerate(texts) if t in brief]
    assert carried == [0, 1]                                # what fit; the rest stay pending
    assert [delivered(rig, f"answer:{n}") for n in range(6)] == [True, True, False, False, False, False]
    rig.pickup()
    [(key, gen, first)] = rig.runtime.steers
    assert (key, gen) == (rig.key(B), 2) and first == steer_text(texts[2:4])
    assert [delivered(rig, f"answer:{n}") for n in range(6)] == [True] * 4 + [False] * 2   # exactly those
    rig.pickup()
    assert [t for _, _, t in rig.runtime.steers] == [steer_text(texts[2:4]), steer_text(texts[4:])]
    assert all(len(t.encode()) <= STEER_MAX for _, _, t in rig.runtime.steers)
    assert all(delivered(rig, f"answer:{n}") for n in range(6))
    rig.pickup()
    assert len(rig.runtime.steers) == 2


def test_a_note_longer_than_steer_max_is_never_steered(tmp_path: Path) -> None:
    rig = started(tmp_path)
    long = "x" * STEER_MAX
    rig.journal.note_add(WS, B, "answer:long", long)
    rig.journal.note_add(WS, B, "answer:short", "short")
    rig.pickup()
    rig.pickup()
    assert [t for _, _, t in rig.runtime.steers] == [steer_text(["short"])]
    assert not delivered(rig, "answer:long") and attempts(rig, "answer:long") == 0
    park_and_resume(rig)
    assert long in rig.runtime.launches[-1].brief           # the next brief carries it
    assert delivered(rig, "answer:long")


def test_a_replayed_request_event_refuses_once(tmp_path: Path) -> None:
    rig = started(tmp_path)
    question = ev(rig, "question", 1, body={"text": ""})
    for _ in range(2):
        bridge(rig).on_question(rig.parker, question)
    assert rig.journal.db.execute("SELECT count(*) FROM refusals").fetchone() == (1,)
    assert [t for _, t in rig.journal.notes(WS, B)] == [refused(question.event_id, QUESTION_INCOMPLETE)]
    nothing_created(rig)


@pytest.mark.parametrize(("body", "why"), [
    ({}, QUESTION_INCOMPLETE),
    ({"text": None}, QUESTION_INCOMPLETE),
    ({"text": ["a list"]}, QUESTION_INCOMPLETE),
    ({"text": ""}, QUESTION_INCOMPLETE),
    ({"text": " \n\t "}, QUESTION_INCOMPLETE),
    ({"text": "q" * 12001}, QUESTION_INCOMPLETE),
    ({"text": f"Which key, {SECRET}?"}, ASK_WITHHELD),
    ({"text": "npub1" + "q" * 58}, ASK_WITHHELD),
])
def test_a_question_that_fails_its_form_is_refused_with_its_reason(tmp_path: Path, body: dict[str, Any],
                                                                   why: str) -> None:
    rig = started(tmp_path)
    question = ev(rig, "question", 1, body=body)
    bridge(rig).on_question(rig.parker, question)
    assert [t for _, t in rig.journal.notes(WS, B)] == [refused(question.event_id, why)]
    nothing_created(rig)


def test_a_question_from_a_generation_not_running_is_refused(tmp_path: Path) -> None:
    rig = started(tmp_path)
    stale = ev(rig, "question", 1, gen=2, body={"text": "Blue or green?"})     # not the running generation
    bridge(rig).on_question(rig.parker, stale)
    assert [t for _, t in rig.journal.notes(WS, B)] == [refused(stale.event_id, OPEN_ASK)]
    rig.journal.set_state(WS, B, BeadState.PARKING)
    parked = ev(rig, "question", 2, body={"text": "Blue or green?"})          # the bead isn't RUNNING
    bridge(rig).on_question(rig.parker, parked)
    assert [t for _, t in rig.journal.notes(WS, B)][1:] == [refused(parked.event_id, OPEN_ASK)]


_ids = itertools.count(100)
_values = (st.none() | st.integers() | st.booleans() | st.lists(st.text(max_size=20), max_size=3)
           | st.dictionaries(st.text(max_size=5), st.text(max_size=20), max_size=2))
_texts = (st.just("") | st.text(alphabet=" \t\n\r", max_size=20) | _values
          | st.text(max_size=200).map(lambda t: t + "q" * 12001)
          | st.text(max_size=200).map(lambda t: f"{t} {SECRET} {t}"))


def test_refusal_texts_never_hold_the_agents_text(tmp_path: Path) -> None:
    rig = started(tmp_path)                 # one rig for every example: a rig each is too slow

    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(text=_texts, extra=st.dictionaries(st.sampled_from(["choices", "recommended", "why", "note"]),
                                              _values | st.text(max_size=200), max_size=3),
           has_text=st.booleans())
    def check(text: object, extra: dict[str, object], has_text: bool) -> None:
        body: dict[str, Any] = {**extra, "text": text} if has_text else dict(extra)
        question = ev(rig, "question", next(_ids), body=body)
        bridge(rig).on_question(rig.parker, question)
        found = rig.journal.refusal(question.event_id)
        assert found is not None
        assert found.text in {refused(question.event_id, why) for why in asks.REFUSALS}
        notes = dict(rig.journal.db.execute("SELECT ref, text FROM resume_notes").fetchall())
        assert notes[f"refused:{question.event_id}"] == found.text

    check()


def test_a_request_that_reaches_wsd_after_its_stop_is_still_handled_first(tmp_path: Path) -> None:
    rig = started(tmp_path)
    question, stop = ev(rig, "question", 1, body={}), ev(rig, "stop", 2, text="Which colour?")
    rig.runtime.event(WS, stop)                             # e2 is logged; e1 isn't drained yet
    rig.pickup()
    assert rig.runtime.steers == [] and rig.journal.refusal(question.event_id) is None
    assert rig.journal.db.execute("SELECT count(*) FROM asks").fetchone() == (0,)
    rig.runtime.event(WS, question)
    rig.pickup()
    note = refused(question.event_id, QUESTION_INCOMPLETE)
    assert rig.runtime.steers == [(rig.key(B), 1, steer_text([note]))]
    nothing_created(rig)
    rig.pickup()
    assert len(rig.runtime.steers) == 1
