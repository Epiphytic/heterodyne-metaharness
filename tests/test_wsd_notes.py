"""Plan 5i part 2 Task 2: the resume notes and the refusals in the journal (schema 5)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from wsd_env import WS

from heterodyne.wsd.journal import Journal, Refusal
from heterodyne.wsd.states import BeadState

B = "btq-1"


@pytest.fixture(autouse=True)
def _close_journals(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    opened: list[Journal] = []
    real = Journal.__init__

    def tracked(self: Journal, *args: object, **kwargs: object) -> None:
        real(self, *args, **kwargs)  # type: ignore[arg-type]
        opened.append(self)

    monkeypatch.setattr(Journal, "__init__", tracked)
    yield
    for j in opened:
        j.close()


@pytest.fixture
def j(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "wsd.db")


class Crash(Exception):
    pass


def test_note_add_dedupes_by_ref_and_rolls_back_with_its_transaction(j: Journal) -> None:
    assert j.note_add(WS, B, "refused:e1", "one") is True
    assert j.note_add(WS, B, "refused:e1", "changed") is False      # the first text stands
    assert [t for _, t in j.notes(WS, B)] == ["one"]
    with pytest.raises(Crash), j.transaction():
        j.set_state(WS, B, BeadState.CLAIMING)
        assert j.note_add(WS, B, "refused:e2", "two") is True
        raise Crash
    assert j.state(WS, B) is None and [t for _, t in j.notes(WS, B)] == ["one"]
    with j.transaction():
        j.set_state(WS, B, BeadState.CLAIMING)
        j.note_add(WS, B, "refused:e2", "two")
    assert [t for _, t in j.notes(WS, B)] == ["one", "two"]


def test_a_delivered_ref_is_never_added_again(j: Journal) -> None:
    j.note_add(WS, B, "r1", "one")
    [(seq, _)] = j.notes(WS, B)
    assert j.notes_delivered(WS, B, seq) == 1
    assert j.note_add(WS, B, "r1", "one") is False
    assert j.notes(WS, B) == []


def test_notes_returns_undelivered_in_seq_order(tmp_path: Path, j: Journal) -> None:
    for ref in ("c", "a", "b"):
        j.note_add(WS, B, ref, f"text {ref}")
    j.note_add(WS, "btq-2", "x", "other bead")
    j.note_add("beta", B, "y", "other workstream")
    got = j.notes(WS, B)
    assert [t for _, t in got] == ["text c", "text a", "text b"]
    seqs = [s for s, _ in got]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    j.close()
    assert Journal(tmp_path / "wsd.db").notes(WS, B) == got        # durable, seqs included


def test_notes_delivered_marks_only_up_to_upto(j: Journal) -> None:
    j.note_add(WS, B, "a", "one")
    j.note_add(WS, B, "b", "two")
    j.note_add(WS, "btq-2", "c", "other bead")
    brief = j.notes(WS, B)                                          # what a brief was composed from
    j.note_add(WS, B, "d", "added after the brief")
    upto = brief[0][0]                                              # the brief carried the first only
    assert j.notes_delivered(WS, B, upto) == 1
    assert [t for _, t in j.notes(WS, B)] == ["two", "added after the brief"]
    assert j.notes_delivered(WS, B, brief[-1][0]) == 1
    assert [t for _, t in j.notes(WS, B)] == ["added after the brief"]
    assert j.notes_delivered(WS, B, brief[-1][0]) == 0              # a replay marks nothing
    assert [t for _, t in j.notes(WS, "btq-2")] == ["other bead"]


def test_note_delivered_marks_one_note_by_ref(j: Journal) -> None:
    j.note_add(WS, B, "a", "one")
    j.note_add(WS, B, "b", "two")
    j.note_add(WS, "btq-2", "c", "three")
    assert j.note_delivered(WS, B, "b") is True
    assert j.note_delivered(WS, B, "b") is False                    # already delivered
    assert j.note_delivered(WS, B, "c") is False                    # another bead's ref
    assert j.note_delivered(WS, B, "zz") is False
    assert [t for _, t in j.notes(WS, B)] == ["one"]
    assert [t for _, t in j.notes(WS, "btq-2")] == ["three"]


def test_steerable_notes_are_bounded_by_bytes_and_attempts(j: Journal) -> None:
    j.note_add(WS, B, "a", "é" * 5)                                # 10 bytes, 5 characters
    j.note_add(WS, B, "b", "x" * 11)
    j.note_add(WS, B, "c", "y" * 3)
    j.note_add(WS, "btq-2", "d", "z")
    assert [ref for _, ref, _ in j.steerable_notes(WS, B, 10)] == ["a", "c"]
    j.note_steer_attempt(["a"], 1)
    j.note_steer_attempt(["a"], 1)
    assert [ref for _, ref, _ in j.steerable_notes(WS, B, 10)] == ["a", "c"]
    j.note_steer_attempt(["a"], 1)                                  # the third attempt: no more steers
    assert [ref for _, ref, _ in j.steerable_notes(WS, B, 100)] == ["b", "c"]
    j.note_steer_attempt(["a"], -1)                                 # a refused steer undoes its increment
    assert [ref for _, ref, _ in j.steerable_notes(WS, B, 100)] == ["a", "b", "c"]
    j.note_steer_attempt(["c"], -1)
    j.note_steer_attempt(["c"], 1)
    j.note_steer_attempt(["c"], 1)
    j.note_steer_attempt(["c"], 1)                                  # never below 0, so this is the third
    assert [ref for _, ref, _ in j.steerable_notes(WS, B, 100)] == ["a", "b"]
    j.note_delivered(WS, B, "b")
    rows = j.steerable_notes(WS, B, 100)
    assert [(ref, text) for _, ref, text in rows] == [("a", "é" * 5)]
    assert rows[0][0] == j.notes(WS, B)[0][0]                       # the seq is the note's own


def test_refusals_insert_is_idempotent_by_event_id(tmp_path: Path, j: Journal) -> None:
    assert j.refusal_add(WS, B, "k:g1:e3", "k", 1, 2, "fixed text") is True
    assert j.refusal_add(WS, B, "k:g1:e3", "k", 1, 2, "fixed text") is False
    assert j.refusal_add(WS, B, "k:g1:e3", "k", 9, 9, "other") is False   # the first row stands
    assert j.refusal("k:g1:e3") == Refusal(WS, B, "k", 1, 2, "fixed text")
    assert j.refusal("k:g1:e4") is None
    with pytest.raises(Crash), j.transaction():
        j.refusal_add(WS, B, "k:g1:e4", "k", 1, 3, "fixed text")
        j.note_add(WS, B, "refused:k:g1:e4", "fixed text")
        raise Crash
    assert j.refusal("k:g1:e4") is None and j.notes(WS, B) == []
    j.close()
    assert Journal(tmp_path / "wsd.db").refusal("k:g1:e3") == Refusal(WS, B, "k", 1, 2, "fixed text")
