"""Live scenarios for `!asks bump` and `!asks repeat` (HZ_LIVE=1 only).

They follow docs/superpowers/specs/2026-10-07-admind-asks-bump-design.md (B1-B14, section 2.2). Each
scenario starts by cancelling every active ask, so `!asks bump` and `!asks repeat` act on its own asks
only, and checks what can be seen from outside: which message each reminder or repeat is threaded to (or
that it is top level), at every operator; admind's replies; `approve-bead --json`; and the stub agent's
paste count.
"""

import pytest
from harness import BUMP_HOURS, WAIT, Operator, Seen, Stack, wait_for

from heterodyne.admind.settings import resolve
from heterodyne.config import load

pytestmark = pytest.mark.xfail(strict=False, reason="awaits the asks bump/repeat daemon")

DECIDE = 120.0      # a decision runs approve-bead against the private dolt server
NAMES = ("tester", "tester2", "outsider")
LONG = ("This throwaway approval bead's description is long on purpose, so that its card is posted in more "
        "than one part and the reminders can be seen to thread to the first. ") * 60
QUESTION_BODY = ("This question comes from the admind live harness. It stays open so that `!asks bump` has "
                 "a question to remind the operators of, next to an approval.")


def plain(text: str) -> str:
    return text.replace("`", "")


def threaded(op: Operator, to: str, contains: str, timeout: float = WAIT) -> Seen:
    """admind's message threaded to `to` whose text (backticks removed) contains `contains`."""
    return op.wait_message(lambda e: e.reply_to == to and contains in plain(e.text),
                           f"admind's message with {contains!r} threaded to the target", timeout)


def received(op: Operator, mid: str) -> Seen:
    return op.wait_message(lambda e: e.message_id == mid, "a message admind's outbox shows as sent")


def approval(stack: Stack, title: str, description: str | None = None) -> tuple[str, str, list[str]]:
    """(bead, ask ID, card chunk IDs) of a fresh approval ask whose card every operator has received."""
    bead = stack.create_approval_bead(title, description)
    ask_id = stack.post_approval(bead)
    card = stack.ops["tester"].wait_card(ask_id)
    for name in NAMES[1:]:
        assert stack.ops[name].wait_card(ask_id) == card
    return bead, ask_id, card


def question(stack: Stack, title: str) -> tuple[str, list[str]]:
    ask_id = stack.post_question(title, QUESTION_BODY)
    card = stack.ops["tester"].wait_card(ask_id)
    for name in NAMES[1:]:
        assert stack.ops[name].wait_card(ask_id) == card
    return ask_id, card


def reminders(stack: Stack, ask_id: str, command: str) -> list[str]:
    """The sent chunk IDs of the reminder `!asks bump` message `command` posted for `ask_id`."""
    ids: list[str] = wait_for(lambda: stack.sent_ids(f"asknote:{ask_id}:bump:{command}:") or None, WAIT,
                              f"ask {ask_id}'s reminder to be sent")
    return ids


def readback(stack: Stack, bead: str) -> dict[str, object]:
    return stack.approve_bead_json(bead)


def test_bump_twice_threads_to_the_original_card(stack: Stack) -> None:
    """Two rounds of `!asks bump` over an approval (a card of several parts) and a question: each round's
    reminder for each ask reaches all three operators threaded to that ask's card part 0, never to the
    previous reminder, and names the ask; the summary is threaded to the command (B1-B5)."""
    stack.cancel_active_asks()
    bead, approval_id, approval_card = approval(stack, "live: bump me", description=LONG)
    assert len(approval_card) >= 2, "the card fits in one part; the scenario needs several"
    question_id, question_card = question(stack, "live: bump me too")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    seen: set[str] = set()
    for _ in range(2):
        command = tester.send("!asks bump")
        assert f"Bumped 2 asks: {approval_id}, {question_id}." in plain(
            threaded(tester, command, "Bumped").text)
        for ask_id, kind, card, tail in (
                (approval_id, "approval", approval_card, "React 👍 or 👎 on the card above"),
                (question_id, "question", question_card, "Reply to the card above to answer.")):
            ids = reminders(stack, ask_id, command)
            assert not seen & set(ids)
            seen |= set(ids)
            for name in NAMES:
                got = [received(stack.ops[name], mid) for mid in ids]
                assert [e.reply_to for e in got] == [card[0]] * len(ids), f"{name}: not threaded to part 0"
                text = plain("".join(e.text for e in got))
                assert text.startswith(f"Still outstanding: ask {ask_id} · {kind} · "), text[:120]
                assert tail in text
    assert readback(stack, bead)["decided"] is False
    assert stack.pastes() == pastes


def test_reminder_decides_nothing(stack: Stack) -> None:
    """tester2's 👍 and a reply "approve" on a reminder each get the hint and decide nothing, and nothing
    is pasted to the agent (B6); tester's 👍 on the card itself then approves."""
    stack.cancel_active_asks()
    bead, ask_id, card = approval(stack, "live: a reminder is not a card")
    tester, tester2 = stack.ops["tester"], stack.ops["tester2"]
    command = tester.send("!asks bump")
    threaded(tester, command, "Bumped 1 asks")
    reminder = reminders(stack, ask_id, command)[0]
    received(tester2, reminder)
    pastes = stack.pastes()
    hint = (f"That was a reminder. React or reply on ask {ask_id}'s card "
            "(the message the reminder replies to).")

    tester2.react(reminder, "👍")
    first = threaded(tester2, reminder, "That was a reminder")
    assert hint in plain(first.text)
    mid = tester2.reply(reminder, "approve")
    # As delta section 5 has it: a reply's hint threads to the reply, a reaction's to the reacted chunk.
    assert hint in plain(threaded(tester2, mid, "That was a reminder").text)
    hints = [e for e in tester2.from_admind() if "That was a reminder" in e.text]
    assert len(hints) == 2 and {e.reply_to for e in hints} == {reminder, mid}, [e.reply_to for e in hints]
    assert readback(stack, bead)["decided"] is False
    assert stack.pastes() == pastes

    tester.react(card[0], "👍")
    assert f"Approved {bead} as tester" in plain(threaded(tester, card[0], f"Approved {bead}", DECIDE).text)
    got_back = readback(stack, bead)
    assert (got_back["decision"], got_back["approved_by"], got_back["via"]) == ("approve", "tester", "marmot")
    assert stack.pastes() == pastes


def test_repeat_reposts_cards_top_level(stack: Stack) -> None:
    """`!asks repeat` reposts each open ask's card as new top-level messages at all three operators, with
    the card's own text (B11). tester's 👎 on a repeated part denies one ask; a reply "yes" on the other's
    repeat approves it (B12)."""
    stack.cancel_active_asks()
    bead_a, ask_a, card_a = approval(stack, "live: repeat me, then deny", description=LONG)
    bead_b, ask_b, card_b = approval(stack, "live: repeat me, then approve")
    tester = stack.ops["tester"]
    pastes = stack.pastes()
    command = tester.send("!asks repeat")
    assert f"Repeated 2 asks: {ask_a}, {ask_b}." in plain(threaded(tester, command, "Repeated").text)
    repeats: dict[str, list[str]] = {}
    for ask_id, card in ((ask_a, card_a), (ask_b, card_b)):
        ids: list[str] = wait_for(lambda ask_id=ask_id: stack.repeat_message_ids(ask_id, command), WAIT,
                                  f"ask {ask_id}'s repeat to be sent")
        assert not set(ids) & set(card)
        original = [received(tester, mid).text for mid in card]
        for name in NAMES:
            got = [received(stack.ops[name], mid) for mid in ids]
            assert all(e.reply_to is None for e in got), f"{name}: a repeated part is threaded"
            assert "".join(e.text for e in got) == "".join(original)
        repeats[ask_id] = ids

    tester.react(repeats[ask_a][-1], "👎")
    assert f"Denied {bead_a} as tester" in plain(
        threaded(tester, repeats[ask_a][-1], f"Denied {bead_a}", DECIDE).text)
    got = readback(stack, bead_a)
    assert (got["decision"], got["denied_by"]) == ("deny", "tester")

    mid = tester.reply(repeats[ask_b][0], "yes")
    assert f"Approved {bead_b} as tester" in plain(threaded(tester, mid, f"Approved {bead_b}", DECIDE).text)
    got = readback(stack, bead_b)
    assert (got["decision"], got["approved_by"]) == ("approve", "tester")
    assert stack.pastes() == pastes


def test_usage_and_nothing_outstanding(stack: Stack) -> None:
    """`!asks foo` gets the usage; `!asks bump` with no open ask gets "No outstanding asks." (B1, B5)."""
    stack.cancel_active_asks()
    tester = stack.ops["tester"]
    mid = tester.send("!asks foo")
    assert "Usage: !asks [bump|repeat]." in plain(threaded(tester, mid, "Usage").text)
    mid = tester.send("!asks bump")
    assert "No outstanding asks." in plain(threaded(tester, mid, "No outstanding asks.").text)


def test_bump_hours_setting_is_read(stack: Stack) -> None:
    """The automatic bump cannot be timed live (`ask_bump_hours` is at least 1); its clock is covered
    offline. Live, the setting written to the isolated config is the one admind's settings resolve (B14)."""
    settings = resolve(load(env=stack.env), stack.env)
    assert getattr(settings, "ask_bump_hours", None) == BUMP_HOURS
