"""Live scenarios for deciding by reply or reaction (HZ_LIVE=1 only).

They follow the reviewed delta, docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md
(R7, R8, R13, R27-R31 and its section 7.2).

Each scenario makes its own throwaway beads and asks on the session's isolated stack, and checks what can
be observed from outside: what `approve-bead --json` reads back, the text admind sends and the message it
is threaded to, and that nothing reached the stub admin agent. Texts are compared with backticks removed,
since the spec quotes some names in them as code.
"""

import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from harness import WAIT, LiveError, Operator, Seen, Stack, wait_for

from heterodyne.marmot.control import ControlError

DECIDE = 120.0      # a decision runs approve-bead against the private dolt server
QUIET = 20.0        # how long "admind sent nothing" is watched for

QUESTION_BODY = ("This question comes from the admind live harness. It checks that a reaction to the card, "
                 "and a reply to it, are each stored as an answer.")


def plain(text: str) -> str:
    return text.replace("`", "")


@dataclass
class Card:
    bead: str
    ask_id: str
    digest: str         # the full digest approve-bead read before posting
    digest12: str
    ids: list[str]      # the card's chunk message IDs, as every operator received them


@pytest.fixture
def posted(stack: Stack) -> Iterator[list[str]]:
    """Ask IDs a scenario posted; any still open afterwards is cancelled, so a failing scenario neither
    leaves a card open for the next nor counts against admind's open-ask limit."""
    ids: list[str] = []
    yield ids
    for ask_id in ids:
        try:
            if stack.ask_get(ask_id)["ask"]["summary"]["status"] in ("open", "deciding"):
                stack.ask_cancel(ask_id)
        except LiveError:
            pass


def approval(stack: Stack, posted: list[str], title: str, pin: bool = False,
             description: str | None = None) -> Card:
    """A fresh approval bead, posted, with its card received by all three operators."""
    bead = stack.create_approval_bead(title, description)
    digest = stack.pin(bead) if pin else str(stack.approve_bead_json(bead)["digest"])
    ask_id = stack.post_approval(bead)
    posted.append(ask_id)
    digest12 = str(stack.ask_get(ask_id)["ask"]["summary"]["digest12"])
    assert digest.startswith(digest12)
    ids = stack.ops["tester"].wait_card(ask_id)
    for name in ("tester2", "outsider"):
        assert stack.ops[name].wait_card(ask_id) == ids
    return Card(bead, ask_id, digest, digest12, ids)


def threaded(op: Operator, to: str, contains: str, timeout: float = WAIT) -> Seen:
    """admind's message threaded to `to` whose text (backticks removed) contains `contains`."""
    return op.wait_message(lambda e: e.reply_to == to and contains in plain(e.text),
                           f"admind's message with {contains!r} threaded to the target", timeout)


def silent(op: Operator, pred: Callable[[Seen], bool], what: str, seconds: float = QUIET) -> None:
    """Watch for `seconds`: admind sends nothing matching `pred`. Absence can only be shown by waiting."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        hits = [e for e in op.from_admind() if pred(e)]
        assert not hits, f"admind sent {what}: {[plain(e.text)[:120] for e in hits]}"
        time.sleep(1)


def readback(stack: Stack, bead: str) -> dict[str, object]:
    return stack.approve_bead_json(bead)


def assert_approved(stack: Stack, card: Card, name: str, digest: str | None = None) -> None:
    got = readback(stack, card.bead)
    assert (got["status"], got["decision"], got["approved_by"], got["via"]) == (
        "closed", "approve", name, "marmot")
    assert got["approved_digest"] == (digest or card.digest)


def assert_undecided(stack: Stack, bead: str) -> None:
    got = readback(stack, bead)
    assert got["decided"] is False and got["status"] == "open", (got["status"], got["decision"])


# --- approve and deny on the card ------------------------------------------------------------------


def test_thumbs_up_approves(stack: Stack, posted: list[str]) -> None:
    """tester's 👍 on an approval card approves it: approve-bead reads back approved by tester via marmot
    with the card's digest, the confirmation is threaded to the reacted card, and nothing is pasted."""
    card = approval(stack, posted, "live: approve me with a thumbs up")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    tester.react(card.ids[0], "👍")
    text = plain(threaded(tester, card.ids[0], f"Approved {card.bead}", DECIDE).text)
    assert f"Approved {card.bead} as tester (digest {card.digest12}, via Marmot)." in text
    assert_approved(stack, card, "tester")
    assert stack.ask_get(card.ask_id)["ask"]["summary"]["status"] == "approved"
    assert stack.pastes() == pastes


def test_thumbs_down_denies(stack: Stack, posted: list[str]) -> None:
    """tester's 👎 on an approval card denies it: approve-bead reads back denied by tester with the card's
    digest, and the confirmation is threaded to the card."""
    card = approval(stack, posted, "live: deny me with a thumbs down")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    tester.react(card.ids[0], "👎")
    assert f"Denied {card.bead} as tester" in plain(threaded(tester, card.ids[0], f"Denied {card.bead}",
                                                             DECIDE).text)
    got = readback(stack, card.bead)
    assert (got["status"], got["decision"], got["denied_by"], got["via"]) == (
        "closed", "deny", "tester", "marmot")
    assert got["denied_digest"] == card.digest
    assert stack.pastes() == pastes


@pytest.mark.parametrize("word", ["approve", "yes"])
def test_one_word_reply_approves(stack: Stack, posted: list[str], word: str) -> None:
    """A reply that is exactly one approve word, threaded to the card, approves it (R28); the confirmation
    is threaded to the reply."""
    card = approval(stack, posted, f"live: approve me with {word!r}")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    mid = tester.reply(card.ids[0], word)
    text = plain(threaded(tester, mid, f"Approved {card.bead}", DECIDE).text)
    assert f"Approved {card.bead} as tester (digest {card.digest12}, via Marmot)." in text
    assert_approved(stack, card, "tester")
    assert stack.pastes() == pastes


@pytest.mark.parametrize(("text", "reason"), [
    ("deny too risky", "too risky"), ("deny, too broad", "too broad"), ("deny - too broad", "too broad")])
def test_deny_reply_with_reason(stack: Stack, posted: list[str], text: str, reason: str) -> None:
    """A deny reply with a reason denies the card; the reason, without a lone separator after the deny
    word or one leading it, is the note approve-bead writes into the bead's decision comment."""
    card = approval(stack, posted, f"live: deny me with {text!r}")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    mid = tester.reply(card.ids[0], text)
    assert f"Denied {card.bead} as tester" in plain(threaded(tester, mid, f"Denied {card.bead}", DECIDE).text)
    got = readback(stack, card.bead)
    assert (got["decision"], got["denied_by"], got["denied_digest"]) == ("deny", "tester", card.digest)
    assert f"Note: {reason}" in stack.bead_text(card.bead)
    assert stack.pastes() == pastes


def test_question_reply_is_a_note(stack: Stack, posted: list[str]) -> None:
    """A reply that is not a decision ("what does this do?") is a note for the poster (R13): admind
    answers it in its thread, nothing is decided, and nothing is pasted."""
    card = approval(stack, posted, "live: ask me a question")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    mid = tester.reply(card.ids[0], "what does this do?")
    text = plain(threaded(tester, mid, "Noted on ask").text)
    assert f"Noted on ask {card.ask_id}; this is not a decision." in text
    assert_undecided(stack, card.bead)
    assert "what does this do?" in str(stack.ask_get(card.ask_id)["ask"])
    assert stack.pastes() == pastes


def test_approve_command_arguments(stack: Stack, posted: list[str]) -> None:
    """`!approve` replied to the card with a digest that is not the card's is refused (R7); `!approve`
    with no arguments then approves it."""
    card = approval(stack, posted, "live: !approve with and without arguments")
    tester = stack.ops["tester"]
    wrong = "0" * 12 if card.digest12 != "0" * 12 else "1" * 12
    mid = tester.reply(card.ids[0], f"!approve {card.bead} {wrong}")
    text = plain(threaded(tester, mid, "This card is for").text)
    assert f"This card is for {card.bead}." in text and "Nothing recorded." in text
    assert_undecided(stack, card.bead)

    mid = tester.reply(card.ids[0], "!approve")
    assert f"Approved {card.bead} as tester" in plain(threaded(tester, mid, f"Approved {card.bead}",
                                                               DECIDE).text)
    assert_approved(stack, card, "tester")


# --- a changed bead -----------------------------------------------------------------------------------


def test_stale_card_gets_a_fresh_one(stack: Stack, posted: list[str]) -> None:
    """An unpinned bead edited after its card was posted: tester's 👍 records nothing, and the reply
    threaded to the card names a fresh ask (R29). tester2's 👍 on the stale card is told to see the fresh ask
    (R31). The fresh card shows the new digest, and a 👍 on it approves the new content."""
    card = approval(stack, posted, "live: edit me after posting")
    tester = stack.ops["tester"]
    stack.edit_bead(card.bead, description="This throwaway bead was edited after its card was posted, so "
                    "the card's digest no longer matches. A fresh card should follow. " * 4)
    now = str(readback(stack, card.bead)["digest"])
    assert now != card.digest
    pastes = stack.pastes()

    tester.react(card.ids[0], "👍")
    text = plain(threaded(tester, card.ids[0], "changed after this card was posted", DECIDE).text)
    assert (f"{card.bead} changed after this card was posted (shown {card.digest12}, now {now[:12]}). "
            "Nothing recorded. A fresh card follows: ask ") in text
    found = re.search(r"A fresh card follows: ask (\S+?)\.", text)
    assert found, text
    fresh_id = found.group(1)
    posted.append(fresh_id)
    assert_undecided(stack, card.bead)
    assert stack.ask_get(card.ask_id)["ask"]["summary"]["status"] == "stale"

    tester2 = stack.ops["tester2"]      # a second decider on the stale card is pointed at the fresh one
    tester2.react(card.ids[0], "👍")
    late = plain(threaded(tester2, card.ids[0], f"Ask {card.ask_id} is already stale").text)
    assert f"Ask {card.ask_id} is already stale; see ask {fresh_id}. Nothing recorded." in late

    fresh = tester.wait_card(fresh_id)
    assert stack.ask_get(fresh_id)["ask"]["summary"]["digest12"] == now[:12]
    shown = " ".join(e.text for e in tester.from_admind() if e.message_id in fresh)
    assert f"digest {now[:12]}" in shown and f"digest {card.digest12}" not in shown

    tester.react(fresh[0], "👍")
    assert f"Approved {card.bead} as tester (digest {now[:12]}, via Marmot)." in plain(
        threaded(tester, fresh[0], f"Approved {card.bead}", DECIDE).text)
    assert_approved(stack, card, "tester", digest=now)
    assert stack.pastes() == pastes


def test_pinned_bead_edited_gets_undecidable_content(stack: Stack, posted: list[str]) -> None:
    """A bead whose `context_digest` pin was set before posting, then edited: tester's 👍 records nothing
    and no fresh card can exist (btq refuses the pin). The updated content is delivered in the card's
    thread as chunks under the stale ask's `askd:` keys, keyed by the reaction's bare event ID, headed as
    undecidable. A 👍 or an approve reply on those chunks decides nothing and is never pasted."""
    card = approval(stack, posted, "live: pinned, then edited", pin=True)
    assert readback(stack, card.bead)["posted_digest"] == card.digest
    tester, tester2 = stack.ops["tester"], stack.ops["tester2"]
    stack.edit_bead(card.bead, description="This pinned throwaway bead was edited after its card was "
                    "posted, so its stored pin no longer matches its content. " * 4)
    now = str(readback(stack, card.bead)["digest"])
    assert now != card.digest
    pastes = stack.pastes()

    event = tester.react(card.ids[0], "👍").lower()
    text = plain(threaded(tester, card.ids[0], "changed after this card was posted", DECIDE).text)
    assert (f"{card.bead} changed after this card was posted (shown {card.digest12}, now {now[:12]}). "
            "Nothing recorded, and admind could not post a fresh card: ") in text
    head = threaded(tester, card.ids[0], f"Updated content of {card.bead} (now {now[:12]})")
    assert "Reactions and replies here decide nothing." in plain(head.text)
    chunks: list[str] = wait_for(lambda: stack.sent_ids(f"askd:{card.ask_id}:{event}:") or None, WAIT,
                                 "the updated content's outbox rows")
    assert head.message_id in chunks
    assert_undecided(stack, card.bead)
    assert stack.ask_get(card.ask_id)["ask"]["summary"]["status"] == "stale"

    tester2.react(chunks[0], "👍")
    assert f"Ask {card.ask_id} is already stale" in plain(
        threaded(tester2, chunks[0], f"Ask {card.ask_id} is already stale").text)
    mid = tester.reply(chunks[0], "approve")
    assert f"Ask {card.ask_id} is already stale" in plain(
        threaded(tester, mid, f"Ask {card.ask_id} is already stale").text)
    assert_undecided(stack, card.bead)
    assert stack.pastes() == pastes


# --- who may decide, and how often -----------------------------------------------------------------


def test_two_approvers_at_once(stack: Stack, posted: list[str]) -> None:
    """tester and tester2 react 👍 to the same card at about the same moment: exactly one decision is
    recorded (the first wins, R31), and the other is told in the thread that it is already approved."""
    card = approval(stack, posted, "live: two approvers at once")
    tester, tester2 = stack.ops["tester"], stack.ops["tester2"]
    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def react(op: Operator) -> None:
        try:
            start.wait(10)
            op.react(card.ids[0], "👍")
        except BaseException as exc:    # surfaced below, not lost in the thread
            errors.append(exc)

    threads = [threading.Thread(target=react, args=(op,)) for op in (tester, tester2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors

    won = threaded(tester, card.ids[0], f"Approved {card.bead} as", DECIDE)
    winner = "tester2" if "as tester2 " in plain(won.text) else "tester"
    assert_approved(stack, card, winner)
    late = plain(threaded(tester, card.ids[0], f"is already approved by {winner}", DECIDE).text)
    assert f"Ask {card.ask_id} is already approved by {winner}. Nothing recorded." in late
    silent(tester, lambda e: e.reply_to == card.ids[0] and e.message_id != won.message_id
           and f"Approved {card.bead} as" in plain(e.text), "a second approval")


def test_outsider_reaction_refused(stack: Stack, posted: list[str]) -> None:
    """outsider (an admind operator, not a btq approver) reacts 👍: refused in the card's thread, nothing
    recorded."""
    card = approval(stack, posted, "live: outsider reacts")
    outsider = stack.ops["outsider"]
    pastes = stack.pastes()
    outsider.react(card.ids[0], "👍")
    assert "outsider is not a btq approver. Nothing recorded." in plain(
        threaded(outsider, card.ids[0], "not a btq approver").text)
    assert_undecided(stack, card.bead)
    assert stack.pastes() == pastes


# --- reactions that are answers, or nothing ---------------------------------------------------------


def test_question_answered_by_reaction_and_reply(stack: Stack, posted: list[str]) -> None:
    """On a question card, tester's 👍 is stored as an answer whose text is the emoji (R27), with the
    acknowledgement threaded to the card; tester2's reply is stored as a second answer."""
    ask_id = stack.post_question("live: answer me with a reaction", QUESTION_BODY)
    posted.append(ask_id)
    tester, tester2 = stack.ops["tester"], stack.ops["tester2"]
    card = tester.wait_card(ask_id)
    assert tester2.wait_card(ask_id) == card
    pastes = stack.pastes()
    tester.react(card[0], "👍")
    threaded(tester, card[0], f"Answer recorded for ask {ask_id}")
    mid = tester2.reply(card[0], "an answer by reply")
    threaded(tester2, mid, f"ask {ask_id}")
    got = stack.ask_get(ask_id)["ask"]
    assert [(a["operator"], a["text"]) for a in got["answers"]] == [
        ("tester", "👍"), ("tester2", "an answer by reply")]
    assert stack.pastes() == pastes


@pytest.mark.parametrize("emoji", ["❤", "♥"])
def test_heart_variants_approve(stack: Stack, posted: list[str], emoji: str) -> None:
    """❤ without its U+FE0F, and a bare ♥, approve like ❤️ (R27)."""
    card = approval(stack, posted, f"live: approve me with U+{ord(emoji):04X}")
    tester = stack.ops["tester"]
    tester.react(card.ids[0], emoji)
    threaded(tester, card.ids[0], f"Approved {card.bead} as tester", DECIDE)
    assert_approved(stack, card, "tester")


def test_reaction_on_non_card_ignored(stack: Stack) -> None:
    """A 👍 on admind's ready notice, which is no card, gets no reply and is not pasted to the agent."""
    tester = stack.ops["tester"]
    notice = tester.wait_message(lambda e: e.text.startswith("admind is listening"), "admind's ready notice")
    pastes = stack.pastes()
    tester.react(notice.message_id, "👍")
    silent(tester, lambda e: e.reply_to == notice.message_id, "a reply to a reaction on a non-card")
    assert stack.pastes() == pastes


def test_ignored_emoji_on_approval_card(stack: Stack, posted: list[str]) -> None:
    """🎉 on an approval card decides nothing and gets no reply; it is audited as an ignored reaction."""
    card = approval(stack, posted, "live: an emoji that means nothing")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    tester.react(card.ids[0], "🎉")
    silent(tester, lambda e: e.reply_to == card.ids[0], "a reply to an ignored emoji")
    assert_undecided(stack, card.bead)
    assert "reaction-ignored" in stack.audit_text()
    assert stack.pastes() == pastes


def test_reaction_removed_is_ignored(stack: Stack, posted: list[str]) -> None:
    """tester approves with 👍, then removes the reaction: the decision stands and admind says nothing
    more. Skipped if this wn-agent cannot remove a reaction or never delivers `reaction_removed`."""
    card = approval(stack, posted, "live: approve, then remove the reaction")
    tester, tester2 = stack.ops["tester"], stack.ops["tester2"]
    tester.react(card.ids[0], "👍")
    threaded(tester, card.ids[0], f"Approved {card.bead} as tester", DECIDE)
    try:
        tester.unreact(card.ids[0], "👍")
    except ControlError as exc:
        pytest.skip(f"wn-agent cannot remove a reaction: {type(exc).__name__}")
    try:
        wait_for(lambda: tester2.raw_frames("reaction_removed") or None, 30, "a reaction_removed frame")
    except LiveError:
        pytest.skip("wn-agent accepted remove_reaction but delivered no reaction_removed frame")
    removed_at = time.monotonic()
    silent(tester, lambda e: e.reply_to == card.ids[0] and e.at >= removed_at, "a reply to the removal")
    assert_approved(stack, card, "tester")


def test_legacy_truncated_ask_needs_details(stack: Stack, posted: list[str]) -> None:
    """An ask stored before the delta (`asks.truncated = 1`, set in admind's store right after posting)
    keeps the old rule (R8): tester's 👍 is refused until tester's `!details` for it is delivered, and
    approves after."""
    card = approval(stack, posted, "live: a legacy shortened ask")
    # Simulates an ask stored before the delta: nothing the daemon does now sets truncated = 1.
    stack.mark_legacy_truncated(card.ask_id)
    tester = stack.ops["tester"]
    tester.react(card.ids[0], "👍")
    assert f"Ask {card.ask_id} was shortened" in plain(
        threaded(tester, card.ids[0], f"Ask {card.ask_id} was shortened").text)
    assert_undecided(stack, card.bead)

    mid = tester.reply(card.ids[0], "!details")
    tester.wait_details(card.ask_id, request=mid)
    tester.react(card.ids[0], "✅")
    threaded(tester, card.ids[0], f"Approved {card.bead} as tester", DECIDE)
    assert_approved(stack, card, "tester")


LONG = ("This throwaway approval bead's description is long on purpose, so that its card is posted in more "
        "than one part and a reaction can target a part other than the first. ") * 60


def test_restart_during_decision_threads_to_reacted_part(stack: Stack, posted: list[str]) -> None:
    """tester reacts 👍 to the last part of a card that is several parts long. admind is killed (SIGKILL,
    with its wn-agent) as soon as the ask is `deciding`, then restarted. The startup reconcile settles the
    attempt, and its notice is threaded to the part tester reacted to, not to the first (R26, section 5).
    If nothing was recorded, a fresh 👍 then approves."""
    card = approval(stack, posted, "live: restart mid-decision", description=LONG)
    assert len(card.ids) >= 2, "the card fits in one part; the scenario needs several"
    tester = stack.ops["tester"]
    target = card.ids[-1]
    tester.react(target, "👍")
    wait_for(lambda: stack.ask_status(card.ask_id) != "open" or None, DECIDE, "the decision to start",
             every=0.02)
    caught = stack.ask_status(card.ask_id) == "deciding"
    stack.kill_admind()
    stack.start_admind()
    assert caught, f"the decision ended before admind was killed (status {stack.ask_status(card.ask_id)})"

    notice = tester.wait_message(
        lambda e: any(t in e.text for t in ("admind restarted while recording", f"Approved {card.bead}")),
        "the reconcile notice", DECIDE)
    parts = {mid: f"part {i + 1} of {len(card.ids)}" for i, mid in enumerate(card.ids)}
    where = parts.get(notice.reply_to or "", "a message outside the card")
    assert where == parts[target], f"the reconcile notice is threaded to {where}, not the reacted part"
    if "admind restarted while recording" in notice.text:
        assert_undecided(stack, card.bead)
        tester.react(target, "✅")
        threaded(tester, target, f"Approved {card.bead} as tester", DECIDE)
    assert_approved(stack, card, "tester")
