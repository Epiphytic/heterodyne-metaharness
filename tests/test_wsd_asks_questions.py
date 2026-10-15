"""Plan 5i part 2 Task 5: questions at a Stop, and `ws-request question` (the accepted path).

A Stop that ends a turn without a close becomes one question for the operator: a question bead, the task
parked on it, and one card posted to admind (the fake ask.sock, which checks each request with admind's own
rules). The card is rebuilt from the bead, so a crash at any step resumes without a second bead or card.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fake_ask_sock import FakeAsk, FakeAskSock
from fakes.checkpoints import CrashAt, SimulatedCrash
from fakes.fake_btq import World, factory
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sandbox_env import short_dir
from wsd_env import WS, Rig, make_rig

from heterodyne.admind import asks as admind_asks
from heterodyne.admind import daemon as admind_daemon
from heterodyne.wsd import asks as asks_module
from heterodyne.wsd import relay
from heterodyne.wsd.asks import (
    ASK_UNPOSTABLE,
    CARD_HEAD,
    CARD_REQUEST,
    CARD_ROUND,
    CUT_MARK,
    D4_CHOICES,
    NO_TEXT,
    QUESTION_FRAMING,
    QUESTION_MAX,
    ROUNDS_BODY,
    ROUNDS_DETAIL,
    ROUNDS_TITLE,
    UNPOSTABLE_TITLE,
    WITHHELD_TEXT,
    AskBridge,
    AsksPins,
    card_choices,
    question_card,
    reshown,
    shown_text,
)
from heterodyne.wsd.beads import NEEDS_HUMAN, QUESTION, BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.journal import (
    PIN_IN_TRANSACTION,
    PIN_NO_ASK,
    PIN_TAKEN,
    AskPinRefused,
    InboxStatus,
    Journal,
)
from heterodyne.wsd.notices import Notices
from heterodyne.wsd.runtime import SessionEvent
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.session_events import SURFACE
from heterodyne.wsd.settings import RelaySettings
from heterodyne.wsd.states import BeadState

B = "btq-1"
SECRET = "sk-" + "a" * 40                   # built here, so no secret is committed
LIMIT = 2                                   # question_rounds_before_human
BACKOFF = 90


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


@pytest.fixture
def fake() -> Iterator[FakeAskSock]:
    with short_dir() as d:
        f = FakeAskSock(d / "ask.sock").start()
        try:
            yield f
        finally:
            f.stop()


def asking(tmp_path: Path, fake: FakeAskSock) -> Rig:
    """A rig whose AskBridge posts to `fake` and sends its notices there too; B is started."""
    relay_settings = RelaySettings(True, fake.path, LIMIT, 6, float(BACKOFF))

    def build(rig: Rig) -> AskBridge:
        notices = Notices(rig.journal, fake.path, clock=rig.clock, cp=rig.cp)
        return AskBridge(rig.journal, rig.beads, rig.runtime, notices, relay_settings, rig.clock, ws=WS,
                         cp=rig.cp)

    rig = make_rig(tmp_path)
    rig.asks_for = build
    rig.restart()
    rig.world.add(B, title="Paint the widget")
    assert rig.pickup() is Outcome.STARTED
    return rig


def bridge(rig: Rig) -> AskBridge:
    assert rig.bridge is not None
    return rig.bridge


def ev(rig: Rig, kind: str, n: int, gen: int = 1, turn: int = 1, **kw: Any) -> SessionEvent:
    key = rig.key(B)
    return SessionEvent(f"{key}:g{gen}:e{n}", kind, WS, B, key, gen, turn, **kw)  # type: ignore[arg-type]


def cards(fake: FakeAskSock) -> list[FakeAsk]:
    return [a for a in fake.asks.values() if a.post.kind == "question"]


def questions(rig: Rig) -> list[str]:
    return [b.id for b in rig.world.beads.values() if QUESTION in b.labels]


def blockers(rig: Rig) -> set[str]:
    return {d.id for d in rig.beads.show(WS, B).open_blockers()}


def stop_ref(gen: int = 1, turn: int = 1) -> str:
    return f"q:{WS}:{B}:{gen}:{turn}"


def tail(card: FakeAsk) -> str:
    return card.post.body.rsplit("\n\n", 1)[1]


def posts(fake: FakeAskSock) -> int:
    return sum(b'"post"' in f for f in fake.frames)


def answered(rig: Rig, ref: str) -> None:
    """Task 6's part, done by hand: the answer delivered and the question bead closed; the next pickups
    resume the task in a new generation."""
    row = rig.journal.ask(ref)
    assert row is not None and row.child is not None
    with rig.journal.transaction():
        rig.journal.ask_set(ref, state="delivered")
    rig.world.beads[row.child].status = "closed"
    for _ in range(3):
        rig.pickup()
    assert rig.state(B) == BeadState.RUNNING.value


def commit(rig: Rig, name: str) -> None:
    """A commit on btq/<B> that changes its tree: the agent's progress."""
    wt = rig.worktree(B)
    (wt / name).write_text(name)
    for args in (["add", name], ["-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m",
                                 name]):
        subprocess.run(["git", "-C", str(wt), *args], check=True, capture_output=True)


def test_a_stop_without_close_posts_one_question_and_parks_on_its_bead(tmp_path: Path,
                                                                       fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?\nI lean blue."))
    rig.pickup()
    [child] = questions(rig)
    [card] = cards(fake)
    assert card.post.bead == child and card.post.poster == relay.poster_for(WS)
    assert (card.post.form, card.post.choices, card.post.recommended) == ("choices", list(D4_CHOICES), "A")
    assert card.post.title == f"{WS}/{B} asks: Blue or green?"
    assert card.post.body.startswith(CARD_HEAD.format(bead=B, title="Paint the widget"))
    assert "Blue or green?\nI lean blue." in card.post.body
    assert tail(card) == CARD_ROUND.format(bead=B, turn=1, round=1, limit=LIMIT)
    assert rig.state(B) == BeadState.WAITING_INPUT.value and blockers(rig) == {child}
    made = rig.world.beads[child]
    assert made.title == f"Question from {B}: Blue or green?" and "agent:wsd" not in made.labels
    assert made.description == "Blue or green?\nI lean blue." + QUESTION_FRAMING.format(bead=B)
    row = rig.journal.ask(stop_ref())
    assert row is not None
    assert (row.state, row.child, row.ask_id) == ("posted", child, card.ask_id)
    assert (row.uncertain, row.round) == (0, 1)
    rig.pickup()
    rig.pickup()
    assert len(cards(fake)) == 1 and posts(fake) == 1 and questions(rig) == [child]


def test_a_stop_with_close_requested_is_ignored(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    stop = ev(rig, "stop", 1, text="All done.", close_requested=True)
    rig.runtime.event(WS, stop)
    rig.pickup()
    assert cards(fake) == [] and questions(rig) == [] and rig.journal.ask(stop_ref()) is None
    assert rig.state(B) == BeadState.RUNNING.value
    rows = rig.journal.db.execute("SELECT status FROM inbox WHERE event_id = ?", (stop.event_id,)).fetchall()
    assert rows == [(InboxStatus.COMMITTED.value,)]


def test_a_second_stop_while_a_question_is_open_posts_nothing(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    rig.runtime.event(WS, ev(rig, "stop", 2, turn=2, text="Hello?"))
    rig.pickup()
    assert len(cards(fake)) == 1 and len(questions(rig)) == 1


@pytest.mark.parametrize("state", ["creating", "owed", "posted", "answered"])
def test_an_open_ask_holds_a_stop_of_the_running_task(tmp_path: Path, fake: FakeAskSock, state: str) -> None:
    """Step 2 itself: the task runs on the Stop's generation, and an ask open on it means no new round."""
    rig = asking(tmp_path, fake)
    with rig.journal.transaction():
        rig.journal.ask_add("q:earlier", WS, B, "question", 1)
        rig.journal.ask_set("q:earlier", state=state)
    bridge(rig).on_stop(rig.parker, ev(rig, "stop", 1, text="Anyone?"))
    assert rig.journal.ask(stop_ref()) is None and cards(fake) == [] and questions(rig) == []
    with rig.journal.transaction():
        rig.journal.ask_set("q:earlier", state="delivered")      # negative control: closed, not open
    bridge(rig).on_stop(rig.parker, ev(rig, "stop", 1, text="Anyone?"))
    assert len(cards(fake)) == 1


def test_a_replayed_stop_event_posts_one_card(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    stop = ev(rig, "stop", 1, text="Blue or green?")
    for _ in range(2):
        bridge(rig).on_stop(rig.parker, stop)
    assert len(cards(fake)) == 1 and len(questions(rig)) == 1 and posts(fake) == 1


_awkward = st.characters(categories=["Cc", "Cs", "Zl", "Zp", "Zs", "Cf"])
_texts = (st.none() | st.just("") | st.text(alphabet=" \t\n\r \x0b\x0c", max_size=30)
          | st.text(max_size=300) | st.text(_awkward, max_size=300)
          | st.text(max_size=40).map(lambda t: (t or "x") * (16000 // max(len(t), 1) + 1))
          | st.text(max_size=100).map(lambda t: t + "\\x1b" * 5000))


def to_admind(post: relay.AskPost) -> admind_asks.AskRequest:
    return msgspec.json.decode(msgspec.json.encode(post), type=admind_asks.AskRequest)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(text=_texts, title=st.text(max_size=300), turn=st.integers(1, 10_000), round_=st.integers(0, 50))
def test_the_body_always_passes_admind_question_rules(text: str | None, title: str, turn: int,
                                                      round_: int) -> None:
    shown = shown_text(text)
    assert len(shown) <= QUESTION_MAX
    post = question_card(WS, B, title, "btq-2", shown, turn, round_, LIMIT, list(D4_CHOICES), "A")
    assert admind_asks.check(to_admind(post)) is None


def test_the_card_text_rules(tmp_path: Path) -> None:
    """The fixed texts step 5 uses, each by its constant (negative controls of the property above)."""
    assert shown_text(None) == shown_text(" \n\t") == NO_TEXT
    assert shown_text(f"Use {SECRET}?") == WITHHELD_TEXT.format(kind="sk- API key")
    cut = shown_text("q" * (QUESTION_MAX + 1))
    assert len(cut) == QUESTION_MAX and cut.endswith(CUT_MARK)
    assert shown_text("a\x1bb\r\nc") == "a\\x1bb\nc"
    # A control escaped next to hex digits can't make a 64-hex run the raw text didn't have.
    assert shown_text("\x1b" + "a" * 62) == WITHHELD_TEXT.format(kind="hex key")


_choice = st.text(max_size=90) | st.sampled_from(["Go left", "Go right", "x = 1", "a · b", "npub1" + "q" * 58,
                                                  SECRET, "-----BEGIN RSA PRIVATE KEY-----", "a" * 64])


@settings(max_examples=300, deadline=None)
@given(choices=st.lists(_choice, max_size=7) | st.none() | st.integers()
       | st.lists(st.integers(), max_size=3),
       recommended=st.sampled_from([*"ABCDEFG", "a", "", "AB", None, 1]))
def test_the_choice_rules_mirror_admind(choices: object, recommended: object) -> None:
    picked, rec = card_choices(choices, recommended)
    post = question_card(WS, B, "Paint the widget", "btq-2", "Which way should the widget face?", 1, 1,
                         LIMIT, picked, rec)
    assert admind_asks.check(to_admind(post)) is None
    if isinstance(choices, list) and (picked, rec) != (list(D4_CHOICES), "A"):
        assert (picked, rec) == (choices, recommended)


def test_secret_text_is_withheld_everywhere(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    rig.runtime.event(WS, ev(rig, "stop", 1, text=f"Should I use {SECRET} for the deploy?"))
    rig.pickup()
    [child] = questions(rig)
    [card] = cards(fake)
    withheld = WITHHELD_TEXT.format(kind="sk- API key")
    assert withheld in card.post.body and withheld in rig.world.beads[child].description
    made = rig.world.beads[child]
    assert SECRET not in made.description + made.title + card.post.title + card.post.body
    assert all(SECRET.encode() not in f for f in fake.frames)
    # Every table T5 writes; part 1's inbox keeps each event as it arrived (reported to the lead).
    for table in ("asks", "nudges", "resume_notes", "refusals", "events", "notices", "beads", "ops"):
        dump = repr(rig.journal.db.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
        assert SECRET not in dump, table


def test_progress_resets_the_round_count(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    answered(rig, stop_ref())
    rig.runtime.event(WS, ev(rig, "stop", 1, gen=2, text="Which shade of blue?"))
    rig.pickup()
    assert [tail(c) for c in cards(fake)][-1] == CARD_ROUND.format(bead=B, turn=1, round=2, limit=LIMIT)
    answered(rig, stop_ref(gen=2))
    commit(rig, "progress.txt")
    rig.runtime.event(WS, ev(rig, "stop", 1, gen=3, text="Which font?"))
    rig.pickup()
    assert len(cards(fake)) == 3 and rig.state(B) == BeadState.WAITING_INPUT.value
    assert tail(cards(fake)[-1]) == CARD_ROUND.format(bead=B, turn=1, round=1, limit=LIMIT)


def test_the_round_after_the_limit_escalates_and_sends_a_stuck_notice(tmp_path: Path,
                                                                     fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    answered(rig, stop_ref())
    rig.runtime.event(WS, ev(rig, "stop", 1, gen=2, text="Blue or green, really?"))
    rig.pickup()
    answered(rig, stop_ref(gen=2))
    sessions = rig.runtime.sessions(WS)
    rig.runtime.event(WS, ev(rig, "stop", 1, gen=3, text="Blue or green, honestly?"))
    rig.pickup()
    assert len(cards(fake)) == 2 and len(questions(rig)) == 2
    assert rig.journal.ask(stop_ref(gen=3)) is None
    assert rig.state(B) == BeadState.STUCK.value and NEEDS_HUMAN in rig.world.beads[B].labels
    row = rig.journal.state(WS, B)
    assert row is not None and row.detail == ROUNDS_DETAIL
    assert [(n.title, n.body) for n in fake.notices] == [
        (ROUNDS_TITLE.format(bead=B, rounds=LIMIT), ROUNDS_BODY.format(bead=B, rounds=LIMIT))]
    assert rig.runtime.sessions(WS) == sessions             # the session is left as it is
    rig.pickup()
    assert len(fake.notices) == 1 and len(cards(fake)) == 2


def test_admind_down_leaves_the_question_owed_and_the_bead_waiting(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    fake.stop()
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    [child] = questions(rig)
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.uncertain, row.attempts) == ("owed", 0, 1)
    assert row.next_at == rig.clock() + BACKOFF
    assert rig.state(B) == BeadState.WAITING_INPUT.value and blockers(rig) == {child}
    fake.start()
    rig.clock.advance(BACKOFF - 1)
    rig.pickup()
    assert cards(fake) == []                                # not yet due
    rig.clock.advance(1)
    rig.pickup()
    [card] = cards(fake)
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.ask_id) == ("posted", card.ask_id) and card.post.bead == child


# The question bead's text, edited between the park and the post, is read back untrusted (lead's ruling on
# call 1): (bead's text without the framing, the shown text the card must hold, a text it must never hold).
EDITS = [
    ("Blue \x1b[31mor green?", "Blue \\x1b[31mor green?", "\x1b"),
    ("Blue\x07 or green?", "Blue\\x07 or green?", "\x07"),
    ("Blue\r\nor green?", "Blue\nor green?", "\r"),
    ("Use SECRET for blue?", WITHHELD_TEXT.format(kind="sk- API key"), "SECRET"),
    ("x" * (QUESTION_MAX + 50), "x" * (QUESTION_MAX - len(CUT_MARK)) + CUT_MARK, "x" * (QUESTION_MAX + 1)),
    ("Blue \\ green?", "Blue \\ green?", "Blue \\\\ green?"),   # step 5's own text: not escaped twice
]


@settings(max_examples=300, deadline=None)
@given(text=_texts)
def test_step_5s_own_text_reads_back_unchanged(text: str | None) -> None:
    assert reshown(shown_text(text)) == shown_text(text)


@pytest.mark.parametrize(("edited", "shown", "never"), EDITS,
                         ids=["esc", "bel", "crlf", "secret", "long", "backslash"])
@pytest.mark.parametrize("framed", [True, False], ids=["framed", "unframed"])
def test_a_question_beads_text_read_back_is_scanned_and_escaped_again(tmp_path: Path, fake: FakeAskSock,
                                                                       edited: str, shown: str, never: str,
                                                                       framed: bool) -> None:
    edited, never = edited.replace("SECRET", SECRET), never.replace("SECRET", SECRET)
    rig = asking(tmp_path, fake)
    fake.stop()
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    [child] = questions(rig)
    rig.world.beads[child].description = edited + (QUESTION_FRAMING.format(bead=B) if framed else "")
    fake.start()
    rig.clock.advance(BACKOFF)
    rig.pickup()
    [card] = cards(fake)
    assert card.post.body.split("\n\n")[1] == shown
    assert never not in card.post.body + card.post.title
    assert admind_asks.check(card.post) is None


def test_an_uncertain_post_is_found_by_list_before_posting_again(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    fake.script.append("lost")                              # admind takes the post; its reply is lost
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    [card] = cards(fake)
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.uncertain, row.ask_id) == ("owed", 1, None)
    rig.clock.advance(BACKOFF)
    rig.pickup()
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.uncertain, row.ask_id) == ("posted", 0, card.ask_id)
    assert len(cards(fake)) == 1 and posts(fake) == 1


def test_a_post_that_was_not_taken_is_posted_after_the_list(tmp_path: Path, fake: FakeAskSock) -> None:
    """Negative control: an uncertain post admind never took (dropped unread) is posted again, once."""
    rig = asking(tmp_path, fake)
    fake.script.append("drop")
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    assert cards(fake) == []
    rig.clock.advance(BACKOFF)
    rig.pickup()
    [card] = cards(fake)
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.ask_id) == ("posted", card.ask_id)


def test_a_retryable_refusal_leaves_the_question_owed(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    fake.script.append(admind_daemon.ASK_LATCHED)
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.uncertain) == ("owed", 0)
    assert rig.state(B) == BeadState.WAITING_INPUT.value


def test_a_permanent_refusal_escalates(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    fake.script.append("--title must be one line of 1-200 characters")
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    [child] = questions(rig)
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.why, row.uncertain) == ("refused", ASK_UNPOSTABLE, 0)
    assert rig.state(B) == BeadState.STUCK.value and NEEDS_HUMAN in rig.world.beads[B].labels
    assert [n.title for n in fake.notices] == [UNPOSTABLE_TITLE.format(bead=B)]
    assert child in fake.notices[0].body
    rig.clock.advance(3600)
    rig.pickup()
    assert cards(fake) == [] and len(fake.notices) == 1


@pytest.mark.parametrize("point", ["asks.row", "asks.created!", "asks.parked", "asks.posting",
                                   "asks.posted!"])
def test_a_crash_at_each_step_is_resumed_without_a_second_bead_or_card(tmp_path: Path, fake: FakeAskSock,
                                                                       point: str) -> None:
    rig = asking(tmp_path, fake)
    rig.restart(CrashAt(point))
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    for _ in range(3):
        rig.pickup()
        rig.clock.advance(BACKOFF)
    [child] = questions(rig)
    [card] = cards(fake)
    assert posts(fake) == 1
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.child, row.ask_id) == ("posted", child, card.ask_id)
    assert card.post.bead == child and rig.state(B) == BeadState.WAITING_INPUT.value
    assert blockers(rig) == {child}


def test_ws_request_question_uses_the_agents_choices_when_valid_and_d4_otherwise(tmp_path: Path,
                                                                                fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    own = {"text": "Which colour?", "choices": ["Blue", "Green", "Red"], "recommended": "B"}
    rig.runtime.event(WS, ev(rig, "question", 1, body=own), ev(rig, "stop", 2, text="Asked."))
    rig.pickup()
    [card] = cards(fake)
    assert (card.post.choices, card.post.recommended) == (["Blue", "Green", "Red"], "B")
    assert tail(card) == CARD_REQUEST.format(bead=B, turn=1)
    row = rig.journal.ask(f"q:{rig.key(B)}:g1:e1")
    assert row is not None and row.state == "posted" and row.round == 0
    answered(rig, row.ref)
    bad = {"text": "Which size?", "choices": ["Small", "a = b"], "recommended": "A"}
    rig.runtime.event(WS, ev(rig, "question", 1, gen=2, body=bad))
    rig.pickup()
    assert (cards(fake)[-1].post.choices, cards(fake)[-1].post.recommended) == (list(D4_CHOICES), "A")
    assert len(cards(fake)) == 2


def test_the_stop_after_an_accepted_question_posts_nothing_more(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    question = ev(rig, "question", 1, body={"text": "Which colour?"})
    rig.runtime.event(WS, question, ev(rig, "stop", 2, text="Which colour? I asked above."))
    rig.pickup()
    rig.pickup()
    [card] = cards(fake)
    assert card.post.body.count("Which colour?") == 1 and len(questions(rig)) == 1
    assert rig.journal.ask(stop_ref()) is None
    assert rig.journal.refusal(question.event_id) is None
    rows = rig.journal.db.execute("SELECT status FROM inbox WHERE surface = ?", (SURFACE,)).fetchall()
    assert {r[0] for r in rows} == {InboxStatus.COMMITTED.value}


def test_an_accepted_question_that_reaches_wsd_after_its_stop_posts_one_card(tmp_path: Path,
                                                                             fake: FakeAskSock) -> None:
    """P1 with an accepted request: the Stop e2 is drained a pickup before the question e1, and is not
    handled before it: one card, the request's."""
    rig = asking(tmp_path, fake)
    question = ev(rig, "question", 1, body={"text": "Which colour?"})
    rig.runtime.event(WS, ev(rig, "stop", 2, text="Which colour? I asked above."))
    rig.pickup()
    assert cards(fake) == [] and questions(rig) == []
    rig.runtime.event(WS, question)
    rig.pickup()
    rig.pickup()
    [card] = cards(fake)
    assert tail(card) == CARD_REQUEST.format(bead=B, turn=1) and rig.journal.ask(stop_ref()) is None


# --- the pin store (AsksPins) and the journal's asks and nudges ---

def test_asks_pins_fail_closed_without_an_asks_row(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    j = Journal(tmp_path / "wsd.db")
    pins = AsksPins(j)
    for call in (lambda: pins.pinned(WS, "a:approval"), lambda: pins.pin(WS, "a:approval", "btq-9")):
        with pytest.raises(AskPinRefused) as caught:
            call()
        assert str(caught.value) == PIN_NO_ASK
    assert [r.getMessage() for r in caplog.records if r.name == asks_module.__name__] == [PIN_NO_ASK] * 2
    with j.transaction():
        j.ask_add("q:1", "other-ws", B, "question", 1)
    with pytest.raises(AskPinRefused, match=PIN_NO_ASK):
        pins.pinned(WS, "q:1")                          # another workstream's row is no pin store here
    assert pins.pinned("other-ws", "q:1") is None       # negative control: its own workstream's is


def test_a_create_for_a_ref_without_an_asks_row_is_beads_unavailable(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    j = Journal(tmp_path / "wsd.db")
    beads = BeadsAdapter(factory(world), pins=AsksPins(j))
    with pytest.raises(BeadsUnavailable):
        beads.create(WS, ref="a:approval", title="Approve", description="d", labels=("kind:approval",),
                     metadata={})
    assert world.created == []
    with j.transaction():
        j.ask_add("q:1", WS, B, "question", 1)
    child = beads.create(WS, ref="q:1", title="Question", description="d", labels=(QUESTION,), metadata={})
    assert j.ask_pinned(WS, "q:1") == child and beads.holder(WS, "q:1") == child


def test_a_pin_inside_an_open_transaction_is_refused(tmp_path: Path) -> None:
    j = Journal(tmp_path / "wsd.db")
    with j.transaction():
        j.ask_add("q:1", WS, B, "question", 1)
    with j.transaction(), pytest.raises(AskPinRefused, match=PIN_IN_TRANSACTION):
        AsksPins(j).pin(WS, "q:1", "btq-2")
    assert j.ask_pinned(WS, "q:1") is None
    j.db.execute("BEGIN")                               # a transaction not opened through the journal
    try:
        with pytest.raises(AskPinRefused, match=PIN_IN_TRANSACTION):
            j.ask_pin(WS, "q:1", "btq-2")
    finally:
        j.db.execute("ROLLBACK")
    AsksPins(j).pin(WS, "q:1", "btq-2")                 # negative control: outside one, it commits
    assert j.ask_pinned(WS, "q:1") == "btq-2"


def test_a_pin_is_set_once(tmp_path: Path) -> None:
    j = Journal(tmp_path / "wsd.db")
    with j.transaction():
        j.ask_add("q:1", WS, B, "question", 1)
    j.ask_pin(WS, "q:1", "btq-2")
    j.ask_pin(WS, "q:1", "btq-2")                       # the same bead again: nothing changes
    with pytest.raises(AskPinRefused, match=PIN_TAKEN):
        j.ask_pin(WS, "q:1", "btq-3")
    row = j.ask("q:1")
    assert row is not None and row.child == "btq-2"
    with pytest.raises(ValueError, match="no such field"):
        j.ask_set("q:1", child="btq-3")                 # only ask_pin sets the child


def test_journal_asks_and_nudges(tmp_path: Path) -> None:
    j = Journal(tmp_path / "wsd.db")
    with j.transaction():
        assert j.ask_add("q:1", WS, B, "question", 1)
        assert not j.ask_add("q:1", WS, B, "question", 7)      # once per ref; the row is kept
        assert j.ask_add("q:2", WS, "btq-2", "question", 0)
    with j.transaction():
        j.ask_set("q:1", state="owed", attempts=2, next_at=5, uncertain=1)
    row = j.ask("q:1")
    assert row is not None
    assert (row.state, row.round, row.attempts, row.next_at, row.uncertain) == ("owed", 1, 2, 5, 1)
    assert [r.ref for r in j.asks_in(WS, ("creating", "owed"))] == ["q:1", "q:2"]
    assert [r.ref for r in j.asks_in(WS, ("owed",))] == ["q:1"] and j.asks_in("other", ("owed",)) == []
    with pytest.raises(KeyError):
        j.ask_set("q:none", state="owed")
    assert j.nudge(WS, B) == (0, None)
    with j.transaction():
        j.nudge_set(WS, B, 2, "tree-1")
        j.nudge_set(WS, B, 3, "tree-2")
    assert j.nudge(WS, B) == (3, "tree-2") and j.nudge(WS, "btq-2") == (0, None)
