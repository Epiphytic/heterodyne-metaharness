"""Live round trips through an isolated admind over the real Marmot relays (HZ_LIVE=1 only).

Each test drives the throwaway operators `tester` (a btq approver) and `outsider` (an admind operator who
is not a btq approver) against the session's isolated stack; see harness.py for what is isolated.
"""

import re

from harness import Stack

QUESTION_BODY = ("This question comes from the admind live harness. It checks that a card reaches every "
                 "operator over the real relays and that a reply to it is stored as the answer.")


def answers(stack: Stack, ask_id: str) -> list[dict[str, str]]:
    return stack.ask_get(ask_id)["ask"]["answers"]


def test_question_card_and_reply(stack: Stack) -> None:
    """A question card reaches both operators, and tester's reply to it is the answer `ask get` returns."""
    ask_id = stack.post_question("live: reply to the card", QUESTION_BODY)
    tester, outsider = stack.ops["tester"], stack.ops["outsider"]
    card = tester.wait_card(ask_id)
    assert outsider.wait_card(ask_id) == card
    mid = tester.reply(card[0], "a reply from tester")
    assert f"Answer recorded for ask `{ask_id}`" in tester.wait_reply(to=mid, contains="Answer recorded")
    got = stack.ask_get(ask_id)["ask"]
    assert got["summary"]["status"] == "answered"
    assert [(a["operator"], a["text"]) for a in got["answers"]] == [("tester", "a reply from tester")]


def test_answer_command_multiline(stack: Stack) -> None:
    """`!answer <id> line1\\nline2` from outsider, sent top-level, is stored with its newline intact."""
    ask_id = stack.post_question("live: multi-line !answer", QUESTION_BODY)
    outsider = stack.ops["outsider"]
    outsider.wait_card(ask_id)
    mid = outsider.send(f"!answer {ask_id} line1\nline2")
    outsider.wait_reply(to=mid, contains=f"ask `{ask_id}`")
    assert [(a["operator"], a["text"]) for a in answers(stack, ask_id)] == [("outsider", "line1\nline2")]


def test_asks_lists_and_cancel_notice(stack: Stack) -> None:
    """`!asks` lists an open ask by ID and title; `admind ask cancel` then posts the cancelled notice as a
    reply in that card's thread, and the ask's status is `cancelled`."""
    ask_id = stack.post_question("live: list me then cancel me", QUESTION_BODY)
    tester = stack.ops["tester"]
    card = tester.wait_card(ask_id)
    listing = tester.wait_reply(to=tester.send("!asks"), contains=ask_id)
    assert re.search(rf"(?m)^{ask_id} question · .* · live: list me then cancel me", listing), listing
    assert stack.ask_cancel(ask_id).returncode == 0
    notice = tester.wait_message(lambda e: e.reply_to in card and "was cancelled by its poster" in e.text,
                                 "the cancelled notice in the card's thread")
    assert f"Ask `{ask_id}` was cancelled by its poster." in notice.text
    assert stack.ask_get(ask_id)["ask"]["summary"]["status"] == "cancelled"


def test_approve_by_reply(stack: Stack) -> None:
    """On an isolated approval bead, `!approve <bead> <digest12>` replied to the card by outsider is refused
    as "not a btq approver" with nothing recorded; the same from tester records the approval, which
    `approve-bead --json` reads back as approved by tester via marmot."""
    bead = stack.create_approval_bead("live: approve me by reply")
    before = stack.approve_bead_json(bead)
    assert before["gaps"] == [] and before["digest"], "the throwaway bead is not approvable"
    ask_id = stack.post_approval(bead)
    digest12 = stack.ask_get(ask_id)["ask"]["summary"]["digest12"]
    assert before["digest"].startswith(digest12)
    tester, outsider = stack.ops["tester"], stack.ops["outsider"]
    card = outsider.wait_card(ask_id)
    assert tester.wait_card(ask_id) == card

    refused = outsider.reply(card[0], f"!approve {bead} {digest12}")
    assert "outsider is not a btq approver. Nothing recorded." in outsider.wait_reply(
        to=refused, contains="not a btq approver")
    assert stack.approve_bead_json(bead)["decided"] is False

    mid = tester.reply(card[0], f"!approve {bead} {digest12}")
    reply = tester.wait_reply(to=mid, contains=f"Approved {bead}", timeout=180)
    assert f"Approved {bead} as tester (digest {digest12}, via Marmot)." in reply
    after = stack.approve_bead_json(bead)
    assert (after["status"], after["decision"], after["approved_by"], after["via"]) == (
        "closed", "approve", "tester", "marmot")
    assert stack.ask_get(ask_id)["ask"]["summary"]["status"] == "approved"


def test_redacted_bead_refused_at_post(stack: Stack) -> None:
    """A bead whose readout holds a token-shaped string is refused at post with "decide it at the
    terminal", and no card is sent."""
    fake = "gh" + "p_" + "Q7" * 18      # token-shaped, built at run time so no literal is committed
    bead = stack.create_approval_bead("live: holds a token-shaped string",
                                      extra=f" Pasted by mistake: {fake}.")
    reply = stack.post_approval_reply(bead)
    assert reply["result"] == "refused"
    assert "decide it at the terminal" in reply["message"]


def test_audit_holds_no_identifiers(stack: Stack) -> None:
    """After the scenarios above, admind's audit log names no npub and holds no 64-hex run."""
    text = stack.audit_text()
    assert stack.audit_records(), "the audit log is empty"
    assert "npub1" not in text
    assert not re.search(r"(?i)[0-9a-f]{64}", text)
