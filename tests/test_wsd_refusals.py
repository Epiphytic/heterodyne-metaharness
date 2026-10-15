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
    AskBridge,
    steer_text,
)
from heterodyne.wsd.journal import InboxStatus, Journal
from heterodyne.wsd.runtime import SessionEvent, SteerRefused
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.session_events import SURFACE
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


def bridge_of(rig: Rig) -> AskBridge:
    return AskBridge(rig.journal, rig.beads, rig.runtime, None, None, rig.clock, ws=WS, cp=rig.cp)


def started(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.asks_for = bridge_of
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


def test_a_crash_during_the_steer_counts_an_attempt(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.restart(CrashAt("asks.steered!"))
    question = ev(rig, "question", 1, body={})
    rig.runtime.event(WS, question, ev(rig, "stop", 2))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    ref = f"refused:{question.event_id}"
    assert len(rig.runtime.steers) == 1                     # pasted, then wsd died before marking it
    rig.restart()
    assert not delivered(rig, ref) and attempts(rig, ref) == 1
    rig.pickup()
    assert len(rig.runtime.steers) == 2 and rig.runtime.steers[0] == rig.runtime.steers[1]
    assert delivered(rig, ref) and attempts(rig, ref) == 2


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
