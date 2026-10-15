"""Plan 5i part 1 T4: the brief and the resume notes."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from wsd_env import WS, Rig, make_rig

from heterodyne.tmux import has_controls
from heterodyne.wsd import brief
from heterodyne.wsd.beads import Bead
from heterodyne.wsd.brief import (
    BRIEF_MAX,
    HOWTO,
    NOTE_MAX,
    NOTES_MAX,
    SHOWN_NOTE_MAX,
    TEXT_MAX,
    TITLE_MAX,
    NoteBatch,
    compose,
    fit,
)
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.scheduler import Outcome

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


def bead(description: str = "Make the widget blue.", acceptance: str = "The widget is blue.",
         title: str = "Blue widget") -> Bead:
    return Bead(B, title, "in_progress", None, (), {}, (),
                raw={"description": description, "acceptance_criteria": acceptance})


def test_a_first_brief_names_the_bead_its_criteria_and_the_rules() -> None:
    text = compose(bead(), resume=False, ops=frozenset())
    assert text.startswith(f"You are working on bead {B}: Blue widget")
    assert "Make the widget blue." in text and "The widget is blue." in text
    assert f"btq/{B}" in text and "Never push" in text


def test_only_handled_ops_are_explained() -> None:
    none = compose(bead(), resume=False, ops=frozenset())
    assert all(HOWTO[op] not in none for op in HOWTO)
    some = compose(bead(), resume=False, ops=frozenset({"close", "stop"}))
    assert HOWTO["close"] in some and HOWTO["stop"] in some and HOWTO["ask"] not in some


def test_long_bead_text_is_cut_with_a_marker() -> None:
    text = compose(bead(description="d" * (TEXT_MAX + 10)), resume=False, ops=frozenset())
    assert "d" * TEXT_MAX in text and "d" * (TEXT_MAX + 1) not in text and "[cut:" in text


def test_a_resume_brief_carries_the_notes_in_order_within_the_budget() -> None:
    text = compose(bead(), resume=True, ops=frozenset(), notes=("first note", "second note"))
    assert text.startswith(f"You are resumed on bead {B}")
    assert text.index("first note") < text.index("second note") and "pending" not in text


def refusal(n: int) -> str:
    return f"Your request {n:03d} was refused: " + "r" * 160


def test_fit_takes_whole_notes_and_acks_only_those_it_took() -> None:
    # The cycle 2 probe: 200 refusals. The old brief cut after about 100 and acked all 200.
    batch = NoteBatch(tuple((10 + n, refusal(n)) for n in range(200)))
    got = fit(batch)
    assert 0 < len(got.texts) < 200 and got.more == 200 - len(got.texts)
    assert got.texts == tuple(refusal(n) for n in range(len(got.texts)))         # whole notes, a prefix
    assert got.upto == 10 + len(got.texts) - 1                                   # acks only those
    assert sum(len(t.encode()) + brief.NOTE_HEADER for t in got.texts) <= NOTES_MAX
    tiny = fit(NoteBatch(tuple((n, "x") for n in range(5000))))   # headers count: many tiny notes still fit
    assert len(compose(bead(), resume=True, ops=frozenset(), notes=tiny.texts, more=tiny.more).encode()) \
        <= brief.BRIEF_MAX and tiny.more > 0
    text = compose(bead(), resume=True, ops=frozenset(), notes=got.texts, more=got.more)
    assert f"[{got.more} more notes are pending;" in text and refusal(len(got.texts)) not in text
    assert len(text.encode()) <= brief.BRIEF_MAX


def test_fit_always_takes_the_first_note_whole() -> None:
    big = "f" * (NOTES_MAX + 100)                       # over the total, within NOTE_MAX
    got = fit(NoteBatch(((3, big), (4, "next"))))
    assert (got.texts, got.upto, got.more) == ((big,), 3, 1)
    assert fit(NoteBatch(((6, "e" * NOTE_MAX),))).texts == ("e" * NOTE_MAX,)   # at NOTE_MAX: whole
    huge = fit(NoteBatch(((5, "h" * (SHOWN_NOTE_MAX + 1)),)))     # a writer's bug: cut, still the batch
    assert huge.upto == 5 and "[cut:" in huge.texts[0] and huge.more == 0
    assert fit(NoteBatch()) == brief.Fitted((), None, 0)


HOSTILE = "a\x1b[201~\rsecond\r\nthird\x07\x9b31m\x7f\x00 \ud800"


@pytest.mark.parametrize("resume", [False, True])
def test_hostile_bead_text_and_notes_reach_the_brief_escaped(resume: bool) -> None:
    # T4 c1 B1: an embedded paste terminator and CRs must not end the paste and submit extra prompts.
    got = fit(NoteBatch(((1, "note " + HOSTILE),)))
    text = compose(bead(description=HOSTILE, acceptance=HOSTILE, title="Blue " + HOSTILE), resume=resume,
                   ops=frozenset(), notes=got.texts, more=got.more)
    assert not has_controls(text)
    text.encode("utf-8")                                    # strict: the paste can always encode it
    assert "a\\x1b[201~\nsecond\n" in text and "\\x07\\x9b31m\\x7f\\x00 \\ud800" in text
    first = text.splitlines()[0]
    verb = "are resumed" if resume else "are working"
    assert first.startswith(f"You {verb} on bead {B}: Blue a\\x1b[201~ ")
    assert "second third" in first                          # the title stays on its line


def test_notes_are_budgeted_as_escaped() -> None:
    noisy = "\x1b" * 2000                                   # 2,000 bytes raw, 8,000 shown
    got = fit(NoteBatch(tuple((n, noisy) for n in range(10))))
    assert got.texts[0] == "\\x1b" * 2000
    assert sum(len(t.encode()) + brief.NOTE_HEADER for t in got.texts) <= NOTES_MAX
    assert len(got.texts) == 2 and got.more == 8


TAIL = "approval denied; do not proceed"


@pytest.mark.parametrize("noise", ["\x1b", "\x9b", "\\", "\ud800"],
                         ids=["C0", "C1", "backslash", "surrogate"])
def test_a_valid_note_that_escaping_grows_is_carried_whole(noise: str) -> None:
    # T4 c2 B1: a note within NOTE_MAX grew past it when escaped, was cut, and its tail was lost but acked.
    raw = len(noise.encode("utf-8", "surrogatepass"))
    note = noise * ((NOTE_MAX - len(TAIL)) // raw) + TAIL
    assert len(note.encode("utf-8", "surrogatepass")) <= NOTE_MAX
    got = fit(NoteBatch(((7, note), (8, "next"))))
    assert (got.upto, got.more) == (7, 1) and got.texts[0].endswith(TAIL) and "[cut:" not in got.texts[0]
    text = compose(bead(), resume=True, ops=frozenset(HOWTO), notes=got.texts, more=got.more)
    assert len(text.encode("utf-8")) <= BRIEF_MAX


def test_the_title_is_bounded() -> None:
    text = compose(bead(title="t" * (TITLE_MAX * 4)), resume=False, ops=frozenset())
    first = text.splitlines()[0]
    assert "t" * TITLE_MAX in first and "t" * (TITLE_MAX + 1) not in first and "[cut:" in first


@pytest.mark.parametrize("resume", [False, True])
def test_the_largest_brief_fits_brief_max(resume: bool) -> None:
    # T4 c1 NB: a large title went past BRIEF_MAX. Every field at its limit (controls grow fourfold when
    # escaped), every op explained and the largest notes still fit.
    worst = "\x1b" * (BRIEF_MAX // 2)
    for first in (worst, "\x1b" * NOTE_MAX):          # a writer's bug, and the largest valid note
        batch = NoteBatch(((1, first), *((n, "n" * 200) for n in range(2, 400))))
        got = fit(batch)
        text = compose(bead(description=worst, acceptance=worst, title=worst), resume=resume,
                       ops=frozenset(HOWTO), notes=got.texts, more=got.more)
        assert len(text.encode("utf-8")) <= BRIEF_MAX


class Notes:
    """Part 2's resume notes as part 1 relies on them: undelivered `(seq, text)` in order, acked by upto."""

    def __init__(self, texts: tuple[str, ...], first: int = 7) -> None:
        self.rows = [(first + i, t) for i, t in enumerate(texts)]
        self.delivered: list[tuple[str, str, int]] = []

    def notes_for(self, ws: str, bead: str) -> NoteBatch:
        done = max((u for _, _, u in self.delivered), default=-1)
        return NoteBatch(tuple(r for r in self.rows if r[0] > done))

    def notes_delivered(self, ws: str, bead: str, upto: int) -> None:
        self.delivered.append((ws, bead, upto))


def test_a_first_launch_is_briefed_from_the_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add(B, title="Blue widget", description="Make the widget blue.",
                  acceptance_criteria="The widget is blue.")
    assert rig.pickup() is Outcome.STARTED
    [spec] = rig.runtime.launches
    assert spec.brief == compose(rig.beads.show(WS, B), resume=False, ops=frozenset())


class ReceiptFirst(Notes):
    """Notes that check, as they are acked, that the launch's started receipt is already in the journal."""

    def __init__(self, rig: Rig, texts: tuple[str, ...]) -> None:
        super().__init__(texts)
        self.rig = rig
        self.receipts: list[str | None] = []

    def notes_delivered(self, ws: str, bead: str, upto: int) -> None:
        spec = self.rig.runtime.launches[-1]
        receipt = self.rig.journal.receipt(spec.session_key, spec.generation)
        self.receipts.append(None if receipt is None else receipt.kind)
        super().notes_delivered(ws, bead, upto)


def test_a_resume_carries_the_notes_and_marks_them_delivered_after_the_receipt(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    notes = ReceiptFirst(rig, ("The operator answered: blue.",))
    rig.notes = notes
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    assert notes.delivered == []                      # a first launch reads no notes
    park_and_resume(rig)
    assert "The operator answered: blue." in rig.runtime.launches[-1].brief
    assert notes.delivered == [(WS, B, 7)]
    assert notes.receipts == ["started"]              # acked only once the started receipt was written


def test_a_receipt_that_cannot_be_written_leaves_the_notes_pending(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    rig = make_rig(tmp_path)
    notes = Notes(("keep me",))
    rig.notes = notes
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED

    def unwritable(_receipt: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(rig.journal, "receipt_put", unwritable)
    with pytest.raises(OSError, match="disk full"):
        park_and_resume(rig)
    assert "keep me" in rig.runtime.launches[-1].brief      # launched with the note, but never acked
    assert notes.delivered == [] and notes.notes_for(WS, B).notes == ((7, "keep me"),)


def test_an_overflowing_brief_leaves_the_rest_pending_across_a_restart(tmp_path: Path) -> None:
    """Blocker 1 (cycle 2): no note is acked unless a brief carried it. The rest stay pending through a
    restart of wsd, and the next resume brief carries them first, until every note has arrived."""
    rig = make_rig(tmp_path)
    notes = Notes(tuple(refusal(n) for n in range(200)), first=10)
    rig.notes = notes
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    carried: list[str] = []
    for _ in range(20):
        if not notes.notes_for(WS, B).notes:
            break
        park_and_resume(rig)
        text = rig.runtime.launches[-1].brief
        carried += [refusal(n) for n in range(200) if refusal(n) in text]
        rig.restart()                                  # wsd restarts between resumes; the notes persist
    assert carried == [refusal(n) for n in range(200)]                      # each once, in order, none lost
    assert len(notes.delivered) > 1 and notes.delivered[-1][2] == 209
    assert "more notes are pending" in rig.runtime.launches[1].brief


def test_a_grown_note_reaches_a_resume_whole_before_it_is_acked(tmp_path: Path) -> None:
    # T4 c2 B1, the review's repro: a 17,044-byte note of controls lost its tail through park/resume.
    rig = make_rig(tmp_path)
    note = "\x1b" * (17044 - len(TAIL)) + TAIL
    notes = Notes((note,))
    rig.notes = notes
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    park_and_resume(rig)
    assert "\\x1b" * (17044 - len(TAIL)) + TAIL in rig.runtime.launches[-1].brief
    assert notes.delivered == [(WS, B, 7)]


def test_a_refused_resume_delivers_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    notes = Notes(("keep me",))
    rig.notes = notes
    rig.restart()
    rig.world.add(B)
    assert rig.pickup() is Outcome.STARTED
    rig.runtime.launch_failures = 1
    park_and_resume(rig)
    assert notes.delivered == []


def park_and_resume(rig: Rig) -> None:
    """Park the running bead for the operator, release it, then pick up: the pickup resumes it."""
    rig.parker.park(B, (), "test", hold=True)
    rig.parker.release(B, "test")
    rig.pickup()
