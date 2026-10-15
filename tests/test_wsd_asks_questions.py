"""Plan 5i part 2 Task 5: questions at a Stop, and `ws-request question` (the accepted path).

A Stop that ends a turn without a close becomes one question for the operator: a question bead, the task
parked on it, and one card posted to admind (the fake ask.sock, which checks each request with admind's own
rules). The card is rebuilt from the bead, so a crash at any step resumes without a second bead or card.
"""

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fake_ask_sock import RECENT_TERMINAL, FakeAsk, FakeAskSock
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
from heterodyne.wsd import scheduler as scheduler_module
from heterodyne.wsd.asks import (
    ANSWER_CHOICE,
    ANSWER_CUT,
    ANSWER_FREE,
    ANSWER_WITHHELD,
    ASK_ENDED,
    ASK_POST_UNKNOWN,
    ASK_UNPOSTABLE,
    CARD_HEAD,
    CARD_REQUEST,
    CARD_ROUND,
    CUT_MARK,
    D4_CHOICES,
    ENDED_BODY,
    NO_TEXT,
    NOTE_BYTES,
    QUESTION_FRAMING,
    QUESTION_MAX,
    REBUILT_MARK,
    REBUILT_NONE,
    REBUILT_NOTE,
    REPOST,
    ROUNDS_BODY,
    ROUNDS_DETAIL,
    ROUNDS_TITLE,
    TITLE_SHOWN,
    TITLE_WITHHELD,
    UNKNOWN_BODY,
    UNPOSTABLE_TITLE,
    WITHHELD_TEXT,
    AskBridge,
    AsksPins,
    card_choices,
    question_card,
    reshown,
    shown_text,
)
from heterodyne.wsd.beads import ANSWER_MARK, NEEDS_HUMAN, QUESTION, BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.journal import (
    PIN_IN_TRANSACTION,
    PIN_NO_ASK,
    PIN_TAKEN,
    AskPinRefused,
    InboxStatus,
    Journal,
)
from heterodyne.wsd.notices import Notices
from heterodyne.wsd.recovery import recover
from heterodyne.wsd.runtime import SessionEvent
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.session_events import SURFACE
from heterodyne.wsd.settings import RelaySettings
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import NoAsks

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


def answered(rig: Rig, fake: FakeAskSock, ref: str, text: str = "Go with blue.") -> None:
    """The operator answers the question's card; the next pickups read it, close the question bead and
    resume the task in a new generation with the answer in its brief."""
    row = rig.journal.ask(ref)
    assert row is not None and row.ask_id is not None
    fake.answer(row.ask_id, text)
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


def test_a_secret_in_the_tasks_title_is_withheld_from_the_card(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    rig.world.beads[B].title = f"Paint with {SECRET}"
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    [card] = cards(fake)
    withheld = TITLE_WITHHELD.format(kind="sk- API key")
    assert card.post.body.startswith(CARD_HEAD.format(bead=B, title=withheld))
    assert SECRET not in card.post.title + card.post.body
    assert all(SECRET.encode() not in f for f in fake.frames)


def test_a_key_cut_short_in_the_tasks_title_is_withheld_whole(tmp_path: Path) -> None:
    """A key cut short by TITLE_SHOWN is withheld whole, never shown in part."""
    title = "x" * (TITLE_SHOWN - 31) + " " + SECRET     # the cut would keep the key's first 30 characters
    post = question_card(WS, B, title, "btq-2", "Blue or green?", 1, 1, LIMIT, list(D4_CHOICES), "A")
    assert TITLE_WITHHELD.format(kind="sk- API key") in post.body and SECRET[:20] not in post.body


def test_progress_resets_the_round_count(tmp_path: Path, fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    answered(rig, fake, stop_ref())
    rig.runtime.event(WS, ev(rig, "stop", 1, gen=2, text="Which shade of blue?"))
    rig.pickup()
    assert [tail(c) for c in cards(fake)][-1] == CARD_ROUND.format(bead=B, turn=1, round=2, limit=LIMIT)
    answered(rig, fake, stop_ref(gen=2))
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
    answered(rig, fake, stop_ref())
    rig.runtime.event(WS, ev(rig, "stop", 1, gen=2, text="Blue or green, really?"))
    rig.pickup()
    answered(rig, fake, stop_ref(gen=2))
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


def unknown(rig: Rig, fake: FakeAskSock, child: str) -> None:
    """The question's post has an unknown outcome and is no longer in admind's list: never posted again;
    the row refused (ASK_POST_UNKNOWN), the task escalated needs-human, one unpostable:<ref> notice."""
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.why, row.uncertain) == ("refused", ASK_POST_UNKNOWN, 0)
    assert rig.state(B) == BeadState.STUCK.value and NEEDS_HUMAN in rig.world.beads[B].labels
    assert [(n.title, n.body) for n in fake.notices] == [(UNPOSTABLE_TITLE.format(bead=B),
                                                          UNKNOWN_BODY.format(bead=B, child=child))]
    refs = rig.journal.db.execute("SELECT ref FROM notices").fetchall()
    assert refs == [(f"unpostable:{stop_ref()}",)]
    for _ in range(3):
        rig.clock.advance(BACKOFF)
        rig.pickup()
    assert len(fake.notices) == 1


def test_an_uncertain_post_missing_from_the_list_is_never_posted_again(tmp_path: Path,
                                                                       fake: FakeAskSock) -> None:
    """A post whose outcome is unknown (here admind never read it) and that admind's list lacks fails
    closed: the list holds only the 20 newest finished asks, so absence does not prove it was never posted."""
    rig = asking(tmp_path, fake)
    fake.script.append("drop")
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    [child] = questions(rig)
    rig.clock.advance(BACKOFF)
    rig.pickup()
    unknown(rig, fake, child)
    assert cards(fake) == [] and posts(fake) == 1


def test_a_posted_question_aged_out_of_adminds_list_is_not_posted_twice(tmp_path: Path,
                                                                         fake: FakeAskSock) -> None:
    rig = asking(tmp_path, fake)
    fake.script.append("lost")                              # admind takes the post; its reply is lost
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    rig.pickup()
    [child] = questions(rig)
    [card] = cards(fake)
    fake.decide(card.ask_id, "cancelled")
    for i in range(RECENT_TERMINAL):                         # newer finished asks push it out of the list
        other = msgspec.structs.replace(card.post, kind="merge", bead=None)
        fake.asks[f"old{i}"] = FakeAsk(other, f"old{i}", status="approved")
    rig.clock.advance(BACKOFF)
    rig.pickup()
    unknown(rig, fake, child)
    assert cards(fake) == [card] and posts(fake) == 1


def test_the_fake_lists_active_asks_and_only_the_newest_finished_ones(fake: FakeAskSock) -> None:
    """Negative control on the fake, as admind's own list (RECENT_TERMINAL finished asks)."""
    sent = to_admind(question_card(WS, B, "t", "btq-2", "Blue or green?", 1, 1, LIMIT, list(D4_CHOICES), "A"))
    assert isinstance(sent, admind_asks.AskPost)
    for i in range(RECENT_TERMINAL + 2):
        fake.asks[f"done{i}"] = FakeAsk(sent, f"done{i}", status="approved")
    fake.asks["live"] = FakeAsk(sent, "live")
    listed = [a.ask_id for a in relay.list_asks(fake.path)]
    assert listed[0] == "live" and len(listed) == RECENT_TERMINAL + 1
    assert "done0" not in listed and "done1" not in listed and f"done{RECENT_TERMINAL + 1}" in listed


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


@pytest.mark.parametrize("point", [None, "notice.owed", "notice.sending", "notice.outcome",
                                   "escalate.labelled!", "escalate.done"])
def test_a_restart_in_or_after_the_unpostable_window_sends_one_notice(tmp_path: Path, fake: FakeAskSock,
                                                                      point: str | None) -> None:
    """Review c2: a crash between the unpostable notice and the escalation (or a plain restart after it)
    leaves one unpostable:<ref> notice row, the row refused and the task needs-human. A crash once the
    notice is `sending` (before or after admind took it) leaves it `sending`: T3 never sends it twice."""
    rig = asking(tmp_path, fake)
    if point is not None:
        rig.restart(CrashAt(point))
    fake.script.append("--title must be one line of 1-200 characters")
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    if point is None:
        rig.pickup()
    else:
        with pytest.raises(SimulatedCrash):
            rig.pickup()
    rig.restart()
    for _ in range(3):
        rig.pickup()
        rig.clock.advance(3600)
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.state == "refused" and row.why in (ASK_UNPOSTABLE, ASK_POST_UNKNOWN)
    assert rig.state(B) == BeadState.STUCK.value and NEEDS_HUMAN in rig.world.beads[B].labels
    sent = [] if point == "notice.sending" else [UNPOSTABLE_TITLE.format(bead=B)]
    assert [n.title for n in fake.notices] == sent and cards(fake) == []
    rows = rig.journal.db.execute("SELECT ref, state FROM notices").fetchall()
    state = "sending" if point in ("notice.sending", "notice.outcome") else "posted"
    assert rows == [(f"unpostable:{stop_ref()}", state)]


@pytest.mark.parametrize("point", ["asks.row", "asks.created!", "asks.parked", "asks.posted!"])
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


def test_a_crash_between_marking_a_post_uncertain_and_posting_fails_closed(tmp_path: Path,
                                                                          fake: FakeAskSock) -> None:
    """At asks.posting nothing was posted, but wsd can't tell that from a post whose reply was lost."""
    rig = asking(tmp_path, fake)
    rig.restart(CrashAt("asks.posting"))
    rig.runtime.event(WS, ev(rig, "stop", 1, text="Blue or green?"))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rig.pickup()
    [child] = questions(rig)
    unknown(rig, fake, child)
    assert cards(fake) == [] and posts(fake) == 0


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
    answered(rig, fake, row.ref)
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


# --- Task 6: answers, at each pickup ---------------------------------------------------------------------


def asked(tmp_path: Path, fake: FakeAskSock, text: str = "Blue or green?") -> tuple[Rig, str, FakeAsk]:
    """A rig whose task asked one question at a Stop: the question bead and the posted card."""
    rig = asking(tmp_path, fake)
    rig.runtime.event(WS, ev(rig, "stop", 1, text=text))
    rig.pickup()
    [child] = questions(rig)
    [card] = cards(fake)
    return rig, child, card


def briefs(rig: Rig) -> list[str]:
    return [spec.brief for spec in rig.runtime.launches if spec.bead == B]


def note_rows(rig: Rig) -> list[tuple[str, str, str | None]]:
    rows = rig.journal.db.execute("SELECT ref, text, delivered_at FROM resume_notes ORDER BY seq").fetchall()
    return [(str(r[0]), str(r[1]), r[2]) for r in rows]


def test_an_answer_closes_the_question_and_the_resume_brief_carries_it(tmp_path: Path,
                                                                     fake: FakeAskSock) -> None:
    rig, child, card = asked(tmp_path, fake)
    gets = sum(b'"get"' in f for f in fake.frames)
    fake.answer(card.ask_id, "Go with blue.")
    note = ANSWER_FREE.format(ask_id=card.ask_id, text="Go with blue.")
    assert bridge(rig).resume_gate(WS, B) is False
    rig.pickup()
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.answer, row.choice) == ("Go with blue.", None)
    made = rig.world.beads[child]
    assert made.status == "closed" and made.comments == [f"{ANSWER_MARK.format(ref=stop_ref())}\n{note}"]
    assert rig.state(B) == BeadState.RUNNING.value
    assert note in briefs(rig)[-1]
    assert [(ref, text) for ref, text, _ in note_rows(rig)] == [(f"answer:{stop_ref()}", note)]
    assert all(at is not None for _, _, at in note_rows(rig))
    rig.pickup()                                            # the note read back delivered: the ask is done
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.state == "delivered"
    assert fake.asks[card.ask_id].status == "cancelled"        # admind's active asks stay under MAX_OPEN
    rig.pickup()
    assert len(briefs(rig)) == 2 and sum(b'"get"' in f for f in fake.frames) == gets + 1


def test_notes_are_delivered_only_after_the_started_receipt(tmp_path: Path, fake: FakeAskSock) -> None:
    rig, _child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "Go with blue.")
    rig.runtime.launch_failures = 1                         # the resume's launch fails: nothing started
    rig.pickup()
    assert [at for _, _, at in note_rows(rig)] == [None]
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.state == "answered" and fake.asks[card.ask_id].status == "answered"
    for _ in range(3):
        rig.pickup()
        rig.clock.advance(BACKOFF)
    assert rig.state(B) == BeadState.RUNNING.value
    note = ANSWER_FREE.format(ask_id=card.ask_id, text="Go with blue.")
    assert note in briefs(rig)[-1] and [at is not None for _, _, at in note_rows(rig)] == [True]


@pytest.mark.parametrize(("choice", "line"), [("A", f"A: {D4_CHOICES[0]}"), ("B", f"B: {D4_CHOICES[1]}"),
                                              ("C", None)])
def test_a_choice_answer_carries_the_choice_line(tmp_path: Path, fake: FakeAskSock, choice: str,
                                                 line: str | None) -> None:
    """A letter the card did not offer (C of two) is a free answer."""
    rig, _child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "👍" if choice == "A" else choice, choice=choice)
    rig.pickup()
    [(_, text, _)] = note_rows(rig)
    if line is None:
        assert text == ANSWER_FREE.format(ask_id=card.ask_id, text="C")
    else:
        assert text == ANSWER_CHOICE.format(ask_id=card.ask_id, line=line)
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.choice == choice


def test_the_task_never_resumes_before_the_note_exists(tmp_path: Path, fake: FakeAskSock) -> None:
    """The question bead closed by hand on the host while the ask is still posted: the gate holds."""
    rig, child, card = asked(tmp_path, fake)
    rig.world.beads[child].status = "closed"
    for _ in range(3):
        rig.pickup()
    assert rig.state(B) != BeadState.RUNNING.value and len(briefs(rig)) == 1
    assert bridge(rig).resume_gate(WS, B) is False
    fake.answer(card.ask_id, "Go with blue.")
    rig.pickup()
    assert rig.state(B) == BeadState.RUNNING.value
    assert ANSWER_FREE.format(ask_id=card.ask_id, text="Go with blue.") in briefs(rig)[-1]
    assert rig.world.beads[child].comments[0].startswith(ANSWER_MARK.format(ref=stop_ref()))


def test_the_resume_gate_without_an_ask_bridge_is_open() -> None:
    assert NoAsks().resume_gate(WS, B) is True


def test_an_answer_with_a_secret_stays_off_the_bead(tmp_path: Path, fake: FakeAskSock) -> None:
    rig, child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, f"Use {SECRET} for it.")
    rig.pickup()
    made = rig.world.beads[child]
    assert made.comments == [f"{ANSWER_MARK.format(ref=stop_ref())}\n"
                             f"{ANSWER_WITHHELD.format(kind='sk- API key')}"]
    assert all(SECRET not in c for c in made.comments) and made.status == "closed"
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.answer == f"Use {SECRET} for it."      # the journal keeps it
    assert SECRET in briefs(rig)[-1]                                       # the agent gets the answer


def test_a_long_answer_is_cut_to_the_note_budget(tmp_path: Path, fake: FakeAskSock) -> None:
    rig, _child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "é" * 9000)                     # 18,000 bytes of UTF-8
    rig.pickup()
    [(_, text, _)] = note_rows(rig)
    assert len(text.encode("utf-8")) <= NOTE_BYTES and text.endswith(ANSWER_CUT)
    assert text.startswith(ANSWER_FREE.format(ask_id=card.ask_id, text="é"))
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.answer == "é" * 9000


def test_a_question_ended_without_an_answer_is_reposted_once_then_escalated(tmp_path: Path,
                                                                          fake: FakeAskSock) -> None:
    rig, child, card = asked(tmp_path, fake)
    fake.decide(card.ask_id, "stale")
    rig.pickup()
    first = rig.journal.ask(stop_ref())
    again = rig.journal.ask(stop_ref() + REPOST)
    assert first is not None and first.state == "ended"
    assert again is not None and (again.state, again.child) == ("posted", child)
    [_, card2] = cards(fake)
    assert card2.post.bead == child and card2.post.body == card.post.body
    assert rig.state(B) == BeadState.WAITING_INPUT.value and bridge(rig).resume_gate(WS, B) is False
    fake.decide(card2.ask_id, "cancelled")
    rig.pickup()
    again = rig.journal.ask(stop_ref() + REPOST)
    assert again is not None and (again.state, again.why) == ("ended", ASK_ENDED)
    assert rig.state(B) == BeadState.STUCK.value and NEEDS_HUMAN in rig.world.beads[B].labels
    assert [(n.title, n.body) for n in fake.notices] == [(UNPOSTABLE_TITLE.format(bead=B),
                                                          ENDED_BODY.format(bead=B, child=child))]
    assert rig.world.beads[child].status == "open"            # left for the operator's release
    for _ in range(2):
        rig.clock.advance(3600)
        rig.pickup()
    assert len(cards(fake)) == 2 and len(fake.notices) == 1


def test_an_answered_question_is_not_ended(tmp_path: Path, fake: FakeAskSock) -> None:
    """Negative control on ENDED_STATUSES: an answer that reaches a finished ask is still the answer."""
    rig, _child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "Go with blue.")
    fake.decide(card.ask_id, "cancelled")
    for _ in range(2):
        rig.pickup()
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.state == "delivered" and rig.journal.ask(stop_ref() + REPOST) is None
    assert rig.state(B) == BeadState.RUNNING.value and len(cards(fake)) == 1


def lose_journal(rig: Rig) -> None:
    rig.journal.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{rig.root / 'state' / 'wsd.db'}{suffix}").unlink(missing_ok=True)
    rig.restart()


@pytest.mark.parametrize("secret", [False, True], ids=["answer", "withheld"])
def test_journal_loss_rebuilds_from_the_question_beads_comment(tmp_path: Path, fake: FakeAskSock,
                                                               secret: bool) -> None:
    rig, child, card = asked(tmp_path, fake)
    answer = f"Use {SECRET} for it." if secret else "Go with blue."
    fake.answer(card.ask_id, answer)
    rig.runtime.launch_failures = 1                         # the resume fails: the note is not delivered
    rig.pickup()
    assert rig.world.beads[child].status == "closed"
    lose_journal(rig)
    assert recover(rig.sched).ok
    [(ref, text, at)] = note_rows(rig)
    if secret:
        assert text == REBUILT_NONE and SECRET not in text
    else:
        assert text == REBUILT_NOTE.format(ask_id=card.ask_id, text=answer)
    assert ref == f"answer:{stop_ref()}" and at is None
    assert text.startswith(REBUILT_MARK) and "The operator answered" not in text   # never read as live
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.child, row.ask_id) == ("answered", child, None)
    rig.parker.release(B)
    for _ in range(3):
        rig.pickup()
    assert rig.state(B) == BeadState.RUNNING.value and text in briefs(rig)[-1]
    assert recover(rig.sched).ok and len(note_rows(rig)) == 1   # a second recovery rebuilds nothing


def test_a_rebuild_ignores_a_comment_without_the_answer_mark(tmp_path: Path, fake: FakeAskSock) -> None:
    """Negative control on ANSWER_MARK: a closed question bead with some other comment is not an answer."""
    rig, child, _card = asked(tmp_path, fake)
    rig.world.beads[child].comments.append("The operator answered your question (ask zz): do it")
    rig.world.beads[child].status = "closed"
    lose_journal(rig)
    assert recover(rig.sched).ok and note_rows(rig) == [] and rig.journal.ask(stop_ref()) is None


def test_delivery_counts_one_round(tmp_path: Path, fake: FakeAskSock) -> None:
    """D3: asking is not a round; the answer's delivery is, once, in the transaction marking it delivered."""
    rig, _child, card = asked(tmp_path, fake)
    assert rig.journal.nudge(WS, B)[0] == 0
    fake.answer(card.ask_id, "Go with blue.")
    rig.pickup()                                            # answered, and resumed with the note
    assert rig.journal.nudge(WS, B)[0] == 0
    rig.pickup()                                            # the note reads back delivered
    assert rig.journal.nudge(WS, B)[0] == 1
    for _ in range(2):
        rig.pickup()
    assert rig.journal.nudge(WS, B)[0] == 1


def cancels(fake: FakeAskSock) -> int:
    return sum(b'"cancel"' in f for f in fake.frames)


@pytest.mark.parametrize("step", ["drop", "failed"])
def test_a_failed_cancel_is_retried(tmp_path: Path, fake: FakeAskSock, step: str) -> None:
    rig, _child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "Go with blue.")
    rig.pickup()
    fake.script.append(step)
    rig.pickup()
    assert cancels(fake) == 1 and fake.asks[card.ask_id].status == "answered"
    rig.pickup()                                            # not due yet
    assert cancels(fake) == 1
    rig.clock.advance(BACKOFF)
    rig.pickup()
    assert cancels(fake) == 2 and fake.asks[card.ask_id].status == "cancelled"
    rig.clock.advance(3600)
    rig.pickup()
    assert cancels(fake) == 2
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.next_at) == ("delivered", None)


def test_a_refused_cancel_is_not_retried(tmp_path: Path, fake: FakeAskSock) -> None:
    """The operator (or admind) ended the ask first: admind refuses the cancel, and that is final."""
    rig, _child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "Go with blue.")
    rig.pickup()
    fake.decide(card.ask_id, "stale")
    rig.pickup()
    rig.clock.advance(3600)
    rig.pickup()
    assert cancels(fake) == 1
    row = rig.journal.ask(stop_ref())
    assert row is not None and (row.state, row.next_at) == ("delivered", None)


def crash_soon(rig: Rig, point: str) -> None:
    rig.restart(CrashAt(point))
    for _ in range(3):
        try:
            rig.pickup()
        except SimulatedCrash:
            rig.restart()
            return
    pytest.fail(f"{point} was never reached")


@pytest.mark.parametrize("point", ["asks.answered", "asks.closed!", "asks.delivered"])
def test_a_crash_in_the_answer_steps_gives_one_note_and_one_comment(tmp_path: Path, fake: FakeAskSock,
                                                                    point: str) -> None:
    rig, child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "Go with blue.")
    crash_soon(rig, point)
    for _ in range(3):
        rig.pickup()
    assert len(note_rows(rig)) == 1 and len(rig.world.beads[child].comments) == 1
    assert rig.state(B) == BeadState.RUNNING.value and rig.journal.nudge(WS, B)[0] == 1
    assert sum(ANSWER_FREE.format(ask_id=card.ask_id, text="Go with blue.") in b for b in briefs(rig)) == 1
    row = rig.journal.ask(stop_ref())
    assert row is not None and row.state == "delivered" and fake.asks[card.ask_id].status == "cancelled"


def test_the_pickup_runs_replays_then_advance_then_the_resume_pass_then_the_flush(
        tmp_path: Path, fake: FakeAskSock, monkeypatch: pytest.MonkeyPatch) -> None:
    rig, _child, card = asked(tmp_path, fake)
    fake.answer(card.ask_id, "Go with blue.")
    calls: list[str] = []
    sched, asks = rig.sched, bridge(rig)

    def spy(name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def called(*args: Any) -> Any:
            calls.append(name)
            return real(*args)
        return called

    monkeypatch.setattr(scheduler_module, "sweep", spy("sweep", scheduler_module.sweep))
    monkeypatch.setattr(rig.journal, "ops_open", spy("replays", rig.journal.ops_open))
    monkeypatch.setattr(sched, "_candidates", spy("resume pass", sched._candidates))  # noqa: SLF001
    monkeypatch.setattr(asks, "advance", spy("advance", asks.advance))
    monkeypatch.setattr(asks, "flush", spy("flush", asks.flush))
    rig.pickup()
    assert [c for i, c in enumerate(calls) if c not in calls[:i]] == ["sweep", "replays", "advance",
                                                                      "resume pass", "flush"]
    assert calls[-1] == "flush" and rig.state(B) == BeadState.RUNNING.value
