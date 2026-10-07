"""Relay delta (2026-10-06): decisions by reply or reaction (R7, R8, R13, R27-R31; delta §7.1).

approve-bead is always the fake (tests/fakes/fake_approve_bead.py) over a bead file under tmp_path. Every ID
is an obvious fake. Waits are bounded and checked; nothing here sleeps to order events.
"""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from admind_asks_fixture import sent_mid
from admind_waits import lock_waiters, stays, wait_until
from fakes.fake_wn_agent import ACCOUNT, GROUP
from fakes.settings import OPERATOR_HEX, SECOND_HEX
from test_admind_approvals import (
    APPROVAL,
    BEAD,
    D2,
    D12,
    DESCRIPTION,
    LINES,
    LONG,
    TOKEN,
    D,
    approve,
    ask_status,
    attempts,
    bead,
    card,
    closed_by,
    current,
    decisions,
    edit,
    go,
    go_file,
    legacy,
    queued,
    row_text,
    say,
    seed,
    send,
    settled,
    waiting,
)
from test_admind_asks import NONE, audited, joined, merge, posted, question, row
from test_admind_daemon import Harness, needs_tmux

from heterodyne.admind import asks, commands, guard, verbs
from heterodyne.admind.audit import ref_id
from heterodyne.admind.daemon import ASK_LATCHED, ASK_TOO_OFTEN, RECONCILE_STRANDED, RESTARTED_NOTICE
from heterodyne.admind.redact import redact
from heterodyne.admind.store import Store
from heterodyne.admind.verbs import Reading
from heterodyne.marmot.control import InboundMessage, Message, ReactionAdded, Sender, decode_event

EVENT = "e1" * 32
CARD = "c4" * 32
ACTOR = "d5" * 32


# --- the wire frame (R27) ------------------------------------------------------------------------
def frame(**body: object) -> bytes:
    """A `reaction_added` frame as spike S4 captured it, with fake IDs."""
    event: dict[str, object] = {
        "type": "reaction_added", "account_id_hex": ACCOUNT, "group_id_hex": GROUP, "event_id_hex": EVENT,
        "target_message_id_hex": CARD,
        "actor": {"account_id_hex": ACTOR, "display_name": "string", "is_self": False},
        "emoji": "👍", "recorded_at": 1790738646,
        "target": {"message_id_hex": CARD, "availability": "available"}, **body}
    event = {k: v for k, v in event.items() if v is not None}
    return json.dumps({"marmot_agent_control": "marmot.agent-control.v2", "id": "r1", **event}).encode()


def test_reaction_frame_decodes_with_its_event_id() -> None:
    ev = decode_event(frame(), "r1")
    assert isinstance(ev, ReactionAdded)
    assert ev.event_id_hex == EVENT and ev.target_message_id_hex == CARD and ev.emoji == "👍"
    assert ev.actor.account_id_hex == ACTOR and not ev.actor.is_self and ev.group_id_hex == GROUP


def test_reaction_frame_without_or_with_a_malformed_event_id_still_decodes() -> None:
    """The daemon drops these itself, audited (`malformed reaction id`); the decoder keeps them."""
    missing = decode_event(frame(event_id_hex=None), "r1")
    assert isinstance(missing, ReactionAdded) and missing.event_id_hex is None
    short = decode_event(frame(event_id_hex="e1" * 8), "r1")
    assert isinstance(short, ReactionAdded) and short.event_id_hex == "e1" * 8
    bare = decode_event(frame(target=None, recorded_at=None), "r1")     # optional fields absent
    assert isinstance(bare, ReactionAdded) and bare.event_id_hex == EVENT


# --- one guard for messages and reactions (R27) --------------------------------------------------
def as_message(sender: Sender, group: str) -> InboundMessage:
    return InboundMessage(account_id_hex=ACCOUNT, group_id_hex=group,
                          message=Message(message_id_hex=CARD, text="hi", recorded_at=1, sender=sender))


def as_reaction(actor: Sender, group: str) -> ReactionAdded:
    return ReactionAdded(account_id_hex=ACCOUNT, group_id_hex=group, target_message_id_hex=CARD, actor=actor,
                         emoji="👍", event_id_hex=EVENT)


@pytest.mark.parametrize(("sender", "group", "latched", "action", "why"), [
    (Sender(ACTOR, False), GROUP, False, "process", "operator message"),
    (Sender(ACTOR.upper(), False), GROUP.upper(), False, "process", "operator message"),
    (Sender(ACTOR, False), "f6" * 32, False, "drop", "message from another group"),
    (Sender(ACTOR, True), GROUP, False, "ignore", "admind's own message"),
    (Sender(ACTOR, True), "f6" * 32, False, "drop", "message from another group"),
    (Sender("e5" * 32, False), GROUP, False, "drop", "sender is not an operator"),
    (Sender("e5" * 32, False), GROUP, True, "drop", "sender is not an operator"),
    (Sender(ACTOR, False), GROUP, True, "drop", "admind is latched; run `admind rearm` on the host"),
])
def test_a_reaction_is_judged_on_its_actor_exactly_as_a_message(sender: Sender, group: str, latched: bool,
                                                                 action: str, why: str) -> None:
    ops = {ACTOR: "op"}
    by_message = guard.judge_message(as_message(sender, group), group_id=GROUP, operators=ops,
                                     latched=latched)
    by_reaction = guard.judge_message(as_reaction(sender, group), group_id=GROUP, operators=ops,
                                      latched=latched)
    assert by_reaction == by_message
    assert (by_reaction.action, by_reaction.reason) == (action, why)
    assert by_reaction.operator == ("op" if action == "process" or "latched" in why else None)


# --- reading a reply or an emoji (R27, R28) ------------------------------------------------------
TONES = ["", "\U0001f3fb", "\U0001f3fc", "\U0001f3fd", "\U0001f3fe", "\U0001f3ff"]
APPROVE_EMOJIS = [*("👍" + t for t in TONES), "✅", "❤️", "❤", "♥️"]
DENY_EMOJIS = [*("👎" + t for t in TONES), "❌"]
APPROVE_WORDS = ["approve", "approved", "yes", "y", "ok", "okay", "lgtm"]
DENY_WORDS = ["deny", "denied", "reject", "rejected"]


def test_the_sets_are_the_spec_lists() -> None:
    assert verbs.APPROVE_WORDS == frozenset(APPROVE_WORDS)
    assert verbs.DENY_WORDS == frozenset(DENY_WORDS)
    assert verbs.DENY_ALONE == frozenset({"no", "n"})


@pytest.mark.parametrize("emoji", APPROVE_EMOJIS)
def test_approve_emojis(emoji: str) -> None:
    assert verbs.emoji_action(emoji) == "approve"
    assert verbs.read_reply(emoji) == Reading("approve")
    assert verbs.read_reply(f"  {emoji} \n") == Reading("approve")


@pytest.mark.parametrize("emoji", DENY_EMOJIS)
def test_deny_emojis(emoji: str) -> None:
    assert verbs.emoji_action(emoji) == "deny"
    assert verbs.read_reply(emoji) == Reading("deny", "")
    assert verbs.read_reply(f"{emoji} too broad") == Reading("deny", "too broad")


@pytest.mark.parametrize("emoji", ["😀", "👀", "🎉", "👍👍", "👍 ✅", "💔", "♡", "", " ", "👌", "🙏",
                                   "👍\U0001f3fb\U0001f3fb", "\U0001f3fb", "x"])
def test_other_emojis_decide_nothing(emoji: str) -> None:
    assert verbs.emoji_action(emoji) is None
    assert verbs.read_reply(emoji) is None


@pytest.mark.parametrize("word", APPROVE_WORDS)
def test_approve_words(word: str) -> None:
    for text in (word, word.upper(), word.title(), f"  {word}  ", f"{word}.", f"{word}!", f"{word}!!",
                 f"{word} .", f"{word}.!\n"):
        assert verbs.read_reply(text) == Reading("approve"), text


@pytest.mark.parametrize("word", DENY_WORDS)
def test_deny_words_with_and_without_a_reason(word: str) -> None:
    for text in (word, word.upper(), f" {word}. ", f"{word}!"):
        assert verbs.read_reply(text) == Reading("deny", ""), text
    assert verbs.read_reply(f"{word} the scope is too wide") == Reading("deny", "the scope is too wide")
    assert verbs.read_reply(f"{word.upper()}:  Too Wide.\n") is None       # "deny:" is not the word
    assert verbs.read_reply(f"{word}. Too wide, see the ADR.") == Reading("deny", "Too wide, see the ADR.")
    assert verbs.read_reply(f"{word}\n\nline one\nline two ") == Reading("deny", "line one\nline two")


@pytest.mark.parametrize("text", ["no", "No", "NO.", "n", "N!", " no "])
def test_no_alone_denies(text: str) -> None:
    assert verbs.read_reply(text) == Reading("deny", "")


@pytest.mark.parametrize("text", [
    "yes but what about X?", "yes, but", "yes but", "ok? then go", "ok?", "okay then", "approve it later",
    "approve later", "lgtm with nits", "y?", "yes please", "approved?", "no idea", "no way", "n/a", "nope",
    "not approved", "I approve", "please approve", "👍 but wait", "✅ later", "denying", "rejects",
    "deny-this", "ok ok", "yes yes", "", "   ", "approve\u200b", "+1", "sure", "👍?", "y e s", "o.k.",
])
def test_near_misses_are_notes(text: str) -> None:
    assert verbs.read_reply(text) is None, text


def test_a_presentation_selector_is_ignored() -> None:
    """U+FE0F only asks for emoji presentation: `✅️` is `✅`, and `♥` is `♥️` (a superset of the spec's
    list, recorded as a deviation)."""
    for emoji in ("✅\ufe0f", "♥", "👍\ufe0f", "👍\U0001f3fd\ufe0f"):
        assert verbs.emoji_action(emoji) == "approve", emoji
    for emoji in ("❌\ufe0f", "👎\ufe0f"):
        assert verbs.emoji_action(emoji) == "deny", emoji
    assert verbs.emoji_action("\ufe0f") is None and verbs.emoji_action("👍\ufe0f\ufe0f") is None


def test_casefold_is_unicode() -> None:
    assert verbs.read_reply("OKAY") == Reading("approve")
    assert verbs.read_reply("DENİED") is None           # a dotted capital I folds to "i̇", not "i"
    assert verbs.read_reply("ﬁne") is None


# --- the store (delta §5) ------------------------------------------------------------------------
# The two tables as PR #18 created them, before `asks.refreshed_from` and `inbound.reply_to`.
OLD_TABLES = """
CREATE TABLE inbound (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('received', 'dispatched', 'executing', 'done', 'dropped')),
    received_at TEXT NOT NULL);
CREATE TABLE asks (
    ask_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('question', 'merge', 'approval')),
    poster TEXT NOT NULL, peer_pid INTEGER, title TEXT NOT NULL, body TEXT NOT NULL,
    pr_url TEXT, head_sha TEXT, bead TEXT, digest TEXT,
    truncated INTEGER NOT NULL DEFAULT 0, card_parts INTEGER NOT NULL, attempt TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'deciding', 'approved', 'denied', 'stale',
                                           'superseded', 'cancelled', 'blocked', 'uncertain')),
    outcome TEXT, decided_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO inbound VALUES ('c4c4', 'done', '2026-10-05T00:00:00+00:00');
INSERT INTO asks(ask_id, kind, poster, title, body, bead, digest, truncated, card_parts, status, created_at,
                 updated_at)
    VALUES ('p4xw', 'approval', 'controller', 'T', '{}', 'btq-ab12c', 'ab', 1, 3, 'open',
            '2026-10-05T00:00:00+00:00', '2026-10-05T00:00:00+00:00');
"""


def columns(store: Store, table: str) -> set[str]:
    return {str(r[1]) for r in store.db.execute(f"PRAGMA table_info({table})")}


def test_an_existing_database_gains_the_new_columns(tmp_path: Path) -> None:
    db = sqlite3.connect(tmp_path / "admind.db")
    db.executescript(OLD_TABLES)
    db.close()
    store = Store(tmp_path / "admind.db")
    try:
        assert "refreshed_from" in columns(store, "asks") and "reply_to" in columns(store, "inbound")
        old = store.ask("p4xw")
        assert old is not None and old.truncated and old.refreshed_from is None and old.card_parts == 3
        assert store.inbound_status("c4c4") == "done" and store.reply_target("c4c4") == "c4c4"
    finally:
        store.close()
    again = Store(tmp_path / "admind.db")      # the migration runs once; a second open changes nothing
    try:
        assert "refreshed_from" in columns(again, "asks")
    finally:
        again.close()


def test_a_reaction_row_keeps_its_reply_target(tmp_path: Path) -> None:
    """A reaction's inbound key is `r:<event id>`; every reply to it threads to the reacted message, and no
    post ever uses the `r:` key as a reply target (delta §5)."""
    store = Store(tmp_path / "admind.db")
    try:
        assert store.claim_inbound("r:" + EVENT, reply_to=CARD)
        assert not store.claim_inbound("r:" + EVENT, reply_to=CARD)        # a replay
        assert store.reply_target("r:" + EVENT) == CARD
        assert store.claim_inbound(EVENT) and store.reply_target(EVENT) == EVENT      # a message: itself
        assert store.reply_target("r:" + "f0" * 32) is None             # unknown: unthreaded, never `r:`
    finally:
        store.close()


# --- the daemon: reactions (R27) -----------------------------------------------------------------
HOW = "React 👍 to approve or 👎 to deny, or reply approve / deny <reason>."
BOTH = {BEAD: bead(approvers=["op", "llctest"])}


def event_id(h: Harness) -> str:
    """A fresh, obviously fake reaction event ID."""
    h.seq += 1
    return f"e{h.seq:063x}"


async def react(h: Harness, emoji: str, target: str, sender: str = OPERATOR_HEX, *, event: str | None = "",
                **kw: Any) -> str:
    """Push a reaction; return its inbound key `r:<event id>`. `event=None` leaves the ID out."""
    eid = event_id(h) if event == "" else event
    await h.fake.push_event(h.fake.reaction_event(emoji, sender, eid, target, **kw))
    return f"r:{(eid or '').lower()}"


async def reacted(h: Harness, emoji: str, target: str, sender: str = OPERATOR_HEX) -> tuple[str, str]:
    """React, wait until admind settled the reaction, and return (its key, the reply admind queued)."""
    mid = await react(h, emoji, target, sender)
    await settled(h, mid)
    return mid, queued(h, mid)


def threads(h: Harness, mid: str, tag: str = "ask") -> set[str | None]:
    """The reply targets of every chunk admind queued for inbound `mid`."""
    return {r[0] for r in h.store.db.execute("SELECT reply_to FROM outbox WHERE key LIKE ?",
                                             (f"{tag}:{mid}:%",))}


def outbox_count(h: Harness, like: str) -> int:
    return h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE ?", (like,)).fetchone()[0]


@needs_tmux
def test_an_approve_reaction_approves_in_the_thread_with_one_canonical_ref(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        mid, text = await reacted(h, "👍", first)
        hexid = mid.removeprefix("r:")
        ref = ref_id(hexid)
        assert text == f"Approved {BEAD} as op (digest {D12}, via Marmot). btq's design gate accepts it."
        assert threads(h, mid) == {first}                   # threaded to the reacted message, never `r:`
        assert decisions(h) == [[BEAD, "--as=op", "--yes", f"--expect-digest={D}", "--via=marmot",
                                 f"--via-ref=marmot:{ref}"]]
        assert attempts(h, ask_id) == [(mid, "approve", "op", f"marmot:{ref}", D, None, 0, "recorded")]
        assert ask_status(h, ask_id) == "approved"
        inbound = [r for r in records(h) if r.get("kind") == "inbound" and r.get("what") == "reaction"]
        assert len(inbound) == 1 and inbound[0]["ref"] == ref                   # the audit's ref ...
        assert attempts(h, ask_id)[0][3].removeprefix("marmot:") == inbound[0]["ref"]   # ... is via_ref's
        assert inbound[0]["message_id"] == f"r:{ref}" and hexid not in json.dumps(records(h))
        assert "echo: 👍" not in h.texts()                  # never pasted to the admin agent
    go(tmp_path, scenario)


def records(h: Harness) -> list[dict[str, Any]]:
    path = h.settings.state_dir / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@needs_tmux
@pytest.mark.parametrize("emoji", ["👎", "👎🏾", "❌"])
def test_a_deny_reaction_denies_with_no_reason(tmp_path: Path, emoji: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        mid, text = await reacted(h, emoji, first)
        assert text == f"Denied {BEAD} as op (via Marmot)."
        assert decisions(h)[0][-2:] == ["--deny", "--note="]
        assert ask_status(h, ask_id) == "denied" and threads(h, mid) == {first}
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("emoji", ["🎉", "👀", "😂", "👍👍"])
def test_another_emoji_on_an_approval_card_is_ignored_without_a_reply(tmp_path: Path, emoji: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        mid, text = await reacted(h, emoji, first)
        assert text == "" and outbox_count(h, f"%{mid}%") == 0
        assert h.store.inbound_status(mid) == "done" and ask_status(h, ask_id) == "open"
        assert audited(h, kind="ask", action="reaction-ignored", ask_id=ask_id)
        assert decisions(h) == [] and attempts(h, ask_id) == []
    go(tmp_path, scenario)


@needs_tmux
def test_reactions_that_are_dropped_or_ignored_before_the_claim(tmp_path: Path) -> None:
    """From a non-operator, from another group, while latched, `is_self`, without or with a malformed event
    ID: no inbound row, no reply, no decision; each drop audited as a message's would be."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        stranger = "f7" * 32
        mid = await react(h, "👍", first, stranger)
        await wait_until(lambda: audited(h, kind="drop", sender_prefix=stranger[:8], what="reaction",
                                         reason="sender is not an operator"))
        assert h.store.inbound_status(mid) is None
        mid = await react(h, "👍", first, group="f6" * 32)
        await wait_until(lambda: audited(h, kind="drop", what="reaction",
                                         reason="message from another group"))
        assert h.store.inbound_status(mid) is None
        mid = await react(h, "👍", first, is_self=True)
        await react(h, "👍", first, event=None)
        await wait_until(lambda: audited(h, kind="drop", what="reaction", reason="malformed reaction id"))
        assert h.store.inbound_status(mid) is None          # is_self: ignored, ahead of the ID-less one
        await react(h, "👍", first, event="e1" * 8)
        await wait_until(lambda: sum(r.get("reason") == "malformed reaction id" for r in records(h)) == 2)
        h.daemon.latch("test latch")
        mid = await react(h, "👍", first)
        await wait_until(lambda: audited(h, kind="drop", operator="op", what="reaction", emoji="👍"))
        await stays(lambda: decisions(h) == [] and attempts(h, ask_id) == [])
        assert h.store.inbound_status(mid) is None and ask_status(h, ask_id) == "open"
        assert not [r for r in records(h) if r.get("kind") == "inbound" and r.get("what") == "reaction"]
    go(tmp_path, scenario)


@needs_tmux
def test_a_replayed_reaction_is_dropped(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        eid = "E2" * 32         # upper case on the wire: lower-cased before the claim
        mid = await react(h, "👍", first, event=eid)
        await settled(h, mid)
        assert mid == "r:" + eid.lower()
        await react(h, "👎", first, event=eid.lower())
        await wait_until(lambda: audited(h, kind="drop", reason="replayed reaction id",
                                         ref=ref_id(eid.lower())))
        assert len(decisions(h)) == 1 and ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
def test_a_reaction_on_a_details_chunk_decides(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        request = await send(h, "!details", first)
        await wait_until(lambda: sent_mid(h, f"askd:{ask_id}:{request}:0") is not None)
        details = sent_mid(h, f"askd:{ask_id}:{request}:0")
        assert details is not None
        mid, text = await reacted(h, "✅", details)
        assert text.startswith(f"Approved {BEAD} as op") and threads(h, mid) == {details}
    go(tmp_path, scenario)


@needs_tmux
def test_a_reaction_on_anything_else_is_ignored(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        echo = next(r["_message_id"] for r in h.fake.sent if r["text"] == "echo: hello")
        mid, text = await reacted(h, "👍", echo)
        assert text == "" and outbox_count(h, f"%{mid}%") == 0 and h.store.inbound_status(mid) == "done"
        assert audited(h, kind="event", action="ignored", what="reaction", message_id=f"r:{ref_id(mid[2:])}")
        mid, _ = await reacted(h, "👍", "f8" * 32)          # a message admind never saw
        assert outbox_count(h, f"%{mid}%") == 0
        assert "echo: 👍" not in h.texts() and decisions(h) == []
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("kind", ["question", "merge"])
def test_a_reaction_answers_a_question_or_merge_card(tmp_path: Path, kind: str) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question() if kind == "question" else merge())
        await wait_until(lambda: sent_mid(h, f"ask:{ask_id}:0") is not None)
        first = sent_mid(h, f"ask:{ask_id}:0")
        assert first is not None
        mid, text = await reacted(h, "🎉", first)
        assert text == f"Answer recorded for ask {ask_id}." and threads(h, mid) == {first}
        assert [(a.kind, a.operator, a.text) for a in h.store.answers(ask_id)] == [("answer", "op", "🎉")]
        assert ask_status(h, ask_id) == "answered" and "echo: 🎉" not in h.texts()
    go(tmp_path, scenario)


@needs_tmux
def test_a_reaction_before_the_card_is_delivered(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        gate = asyncio.Event()

        def hold_after_first(req: dict[str, Any]) -> None:
            if req["idempotency_key"].startswith("ask:") and req["idempotency_key"].endswith(":0"):
                h.fake.send_gate = gate
        h.fake.on_send = hold_after_first
        ask_id = await posted(h, APPROVAL)
        await wait_until(lambda: sent_mid(h, f"ask:{ask_id}:0") is not None)
        first = sent_mid(h, f"ask:{ask_id}:0")
        assert first is not None
        wording = (f"Ask {ask_id} has not been fully delivered yet. Wait for every part, then react or reply "
                   "again. Nothing recorded.")
        assert (await reacted(h, "👍", first))[1] == wording
        assert (await say(h, "approve", first)) == wording
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
        assert not h.store.card_delivered(ask_id)
        denied = (await reacted(h, "👎", first))[1]          # deny needs no delivery check
        assert denied == f"Denied {BEAD} as op (via Marmot)."
        assert ask_status(h, ask_id) == "denied"
        h.fake.on_send = None
        h.fake.send_gate = None
        gate.set()
    go(tmp_path, scenario, chunk_chars=200)


@needs_tmux
def test_two_operators_the_first_decision_wins(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await reacted(h, "👍", first))[1].startswith(f"Approved {BEAD} as op")
        mid, text = await reacted(h, "👍", first, SECOND_HEX)
        assert text == f"Ask {ask_id} is already approved by op. Nothing recorded."
        assert threads(h, mid) == {first}
        assert (await say(h, "approve", first, SECOND_HEX)) == text
        assert (await say(h, "looks good", first, SECOND_HEX)) == text      # a note, too late
        assert len(decisions(h)) == 1
    go(tmp_path, scenario, BOTH)


@needs_tmux
def test_two_operators_racing_one_card(tmp_path: Path) -> None:
    """Both devices react while the first decision is still running: the second is told, not queued."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="wait")
        one = await react(h, "👍", first)
        await waiting(h)
        two = await react(h, "👎", first, SECOND_HEX)
        go_file(h).touch()
        await settled(h, one)
        await settled(h, two)
        assert queued(h, one).startswith(f"Approved {BEAD} as op")
        assert queued(h, two) == f"Ask {ask_id} is already approved by op. Nothing recorded."
        assert len(decisions(h)) == 1
    go(tmp_path, scenario, BOTH)


@needs_tmux
def test_not_a_btq_approver_by_reaction(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        _, text = await reacted(h, "👍", first, SECOND_HEX)
        assert text == "llctest is not a btq approver. Nothing recorded."
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)


@needs_tmux
def test_a_long_readout_is_posted_whole_and_decided(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        stored = h.store.ask(ask_id)
        assert stored is not None and stored.card_parts > 5 and not stored.truncated
        whole = "".join(r[0] for r in h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ? "
                                                         "ORDER BY seq", (f"ask:{ask_id}:%",)))
        assert all(f"  │ line {i} of the plan" in whole for i in range(60))
        last = sent_mid(h, f"ask:{ask_id}:{stored.card_parts - 1}")
        assert last is not None
        assert (await reacted(h, "👍", last))[1].startswith(f"Approved {BEAD}")     # any part of the card
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})}, chunk_chars=200)


@needs_tmux
def test_a_readout_over_the_cap_is_refused_at_post(tmp_path: Path) -> None:
    huge = {"description": ["description:", *(f"  │ {i:05} " + "plan " * 40 for i in range(130))]}

    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(APPROVAL, NONE)
        assert reply == asks.refused("this bead is too long to decide from the phone; decide it at the "
                                     "terminal")
        assert audited(h, kind="ask", action="refused", bead=BEAD, reason="too long")
        assert h.store.asks_with_status(*asks.ACTIVE) == [] and outbox_count(h, "ask:%") == 0
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **huge})})


@needs_tmux
def test_legacy_ask_by_reaction_needs_full_details(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        legacy(h, ask_id)
        shortened = f"Ask {ask_id} was shortened. Reply !details to it and read it first. Nothing recorded."
        assert (await reacted(h, "👍", first))[1] == shortened
        gate = asyncio.Event()

        def hold_after_first(req: dict[str, Any]) -> None:
            if req["idempotency_key"].startswith("askd:") and req["idempotency_key"].endswith(":0"):
                h.fake.send_gate = gate
        h.fake.on_send = hold_after_first
        request = await send(h, "!details", first)
        await wait_until(lambda: sent_mid(h, f"askd:{ask_id}:{request}:0") is not None)
        details = sent_mid(h, f"askd:{ask_id}:{request}:0")
        assert details is not None
        assert (await reacted(h, "👍", details))[1] == shortened          # partial !details
        h.fake.on_send = None
        h.fake.send_gate = None
        gate.set()
        await wait_until(lambda: h.store.details_delivered(ask_id, "op"))
        assert decisions(h) == []
        assert (await reacted(h, "👍", details))[1].startswith(f"Approved {BEAD}")
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})}, chunk_chars=200)


# --- recovery of reaction-started work (R26, delta §5) ------------------------------------------
CARD_ROW = "ask:d222:0"


def with_card(tmp_path: Path, mid: str, status: str = "deciding") -> Callable[[Harness], None]:
    """Before the store opens: ask d222's card sent as message CARD; reaction `mid` (reacting to it)
    claimed, and, if `status` is "deciding", left mid-decision as a crash would leave it."""
    def before_store(h: Harness) -> None:
        if status == "deciding":
            seed(tmp_path, ("d222", BEAD, mid, "deciding"))(h)
        store = Store(h.settings.state_dir / "admind.db")
        if status != "deciding":
            store.insert_ask(row("d222", kind="approval", body="", bead=BEAD, digest=D), None)
        store.enqueue(CARD_ROW, "the card", None)
        seq = store.db.execute("SELECT seq FROM outbox WHERE key = ?", (CARD_ROW,)).fetchone()[0]
        store.mark_sent(seq, CARD)
        assert store.claim_inbound(mid, reply_to=CARD)
        store.set_inbound(mid, "executing")
        store.close()
    return before_store


@needs_tmux
def test_restart_mid_decision_started_by_a_reaction(tmp_path: Path) -> None:
    mid = "r:" + EVENT

    async def scenario(h: Harness) -> None:
        await wait_until(lambda: ask_status(h, "d222") == "approved")
        assert h.store.inbound_status(mid) == "done" and row_text(h, f"restarted:{mid}") is None
        note = h.store.db.execute("SELECT text, reply_to FROM outbox "
                                  "WHERE key LIKE 'asknote:d222:reconciled:%'").fetchone()
        assert note is not None and note[1] == CARD and note[0].startswith(f"Approved {BEAD} as op")
    go(tmp_path, scenario, {BEAD: closed_by(mid)}, before_store=with_card(tmp_path, mid))


@needs_tmux
def test_restart_after_a_reaction_was_claimed_before_begin_attempt(tmp_path: Path) -> None:
    mid = "r:" + EVENT

    async def scenario(h: Harness) -> None:
        await wait_until(lambda: h.store.inbound_status(mid) == "dropped")
        notice = h.store.db.execute("SELECT text, reply_to FROM outbox WHERE key = ?",
                                    (f"restarted:{mid}",)).fetchone()
        assert notice is not None and tuple(notice) == (RESTARTED_NOTICE, CARD)    # threaded to the card
        assert ask_status(h, "d222") == "open" and decisions(h) == []
    go(tmp_path, scenario, before_store=with_card(tmp_path, mid, "executing"))


# --- replies and commands on an approval card (R7, R13, R28) -------------------------------------
@needs_tmux
@pytest.mark.parametrize("text", ["approve", "Approved.", "YES", "y", "ok!", "okay", "LGTM", " lgtm ", "👍🏻",
                                  "✅", "❤️"])
def test_an_approve_reply_approves(tmp_path: Path, text: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await say(h, text, first)).startswith(f"Approved {BEAD} as op (digest {D12}")
        assert ask_status(h, ask_id) == "approved" and h.store.answers(ask_id) == []
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize(("text", "note"), [("deny", ""), ("Denied.", ""), ("reject too broad", "too broad"),
                                            ("rejected wrong runtime", "wrong runtime"), ("no", ""),
                                            ("n", ""), ("👎 not yet", "not yet"),
                                            ("deny the key is " + TOKEN,
                                             "the key is <redacted GitHub token>")])
def test_a_deny_reply_denies_with_its_reason(tmp_path: Path, text: str, note: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await say(h, text, first)) == f"Denied {BEAD} as op (via Marmot)."
        assert decisions(h)[0][-2:] == ["--deny", f"--note={note}"]
        assert ask_status(h, ask_id) == "denied"
    go(tmp_path, scenario)


@needs_tmux
def test_a_deny_reason_over_the_limit_is_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        text = await say(h, "deny " + "x" * (commands.MAX_REASON + 1), first)
        assert text == "Not recorded: a deny reason is at most 1,000 characters."
        assert (await say(h, "deny " + "x" * commands.MAX_REASON, first)).startswith(f"Denied {BEAD}")
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("text", ["yes but", "yes, but what about X?", "ok? then ship it", "no idea",
                                  "approve it later", "lgtm once CI passes", "nope", "denial is a river"])
def test_anything_else_is_a_note(tmp_path: Path, text: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await say(h, text, first)) == f"Noted on ask {ask_id}; this is not a decision. {HOW}"
        assert [(a.kind, a.text) for a in h.store.answers(ask_id)] == [("note", text)]
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("command", ["!approve", f"!approve {BEAD}", f"!approve {BEAD} {D12}",
                                     f"!approve {BEAD} {D12.upper()}"])
def test_approve_command_without_or_with_matching_arguments(tmp_path: Path, command: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await say(h, command, first)).startswith(f"Approved {BEAD} as op")
        assert ask_status(h, ask_id) == "approved"
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize(("command", "note"), [("!deny", ""), (f"!deny {BEAD}", ""),
                                               (f"!deny {BEAD} too broad", "too broad")])
def test_deny_command_without_or_with_matching_arguments(tmp_path: Path, command: str, note: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await say(h, command, first)) == f"Denied {BEAD} as op (via Marmot)."
        assert decisions(h)[0][-1] == f"--note={note}" and ask_status(h, ask_id) == "denied"
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("command", ["!approve btq-cd34e", f"!approve btq-cd34e {D12}",
                                     f"!approve {BEAD} {D2[:12]}", f"!approve {BEAD} {D12} now",
                                     "!deny btq-cd34e", "!deny btq-cd34e no", "!deny too broad"])
def test_commands_with_mismatching_arguments_are_refused(tmp_path: Path, command: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert (await say(h, command, first)) == f"This card is for {BEAD}. {HOW} Nothing recorded."
        assert decisions(h) == [] and ask_status(h, ask_id) == "open" and attempts(h, ask_id) == []
    go(tmp_path, scenario)


@needs_tmux
def test_commands_not_on_a_card_are_refused(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        echo = next(r["_message_id"] for r in h.fake.sent if r["text"] == "echo: hello")
        refused = "To decide, react to the approval card or reply to it. Nothing recorded."
        for command in ("!approve", "!deny", approve()):
            assert (await say(h, command, None)) == refused
            assert (await say(h, command, echo)) == refused
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
        assert (await say(h, f"!answer {ask_id} yes", None, tag="cmd")) == (
            f"Ask {ask_id} is an approval ask. To decide, react to its card or reply to it. "
            "Nothing recorded.")
    go(tmp_path, scenario)


# --- stale: a fresh card (R29, R31) --------------------------------------------------------------
def fresh_of(h: Harness, ask_id: str) -> str:
    newer = h.store.newer_ask(ask_id)
    assert newer is not None and newer.refreshed_from == ask_id
    return newer.ask_id


def refreshed(ask_id: str, new: str, now: str = D2[:12]) -> str:
    return (f"{BEAD} changed after this card was posted (shown {D12}, now {now}). Nothing recorded. A fresh "
            f"card follows: ask {new}.")


def not_refreshed(why: str, now: str = D2[:12]) -> str:
    return (f"{BEAD} changed after this card was posted (shown {D12}, now {now}). Nothing recorded, and "
            f"admind could not post a fresh card: {why}.")


@needs_tmux
@pytest.mark.parametrize("by", ["reply", "reaction"])
def test_a_stale_card_gets_a_fresh_card(tmp_path: Path, by: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2)
        if by == "reply":
            mid = await send(h, "approve", first)
            await settled(h, mid)
        else:
            mid, _ = await reacted(h, "👍", first)
        new = fresh_of(h, ask_id)
        assert queued(h, mid) == refreshed(ask_id, new)
        assert threads(h, mid) == ({mid} if by == "reply" else {first})     # a reply threads to the reply
        assert ask_status(h, ask_id) == "stale" and attempts(h, ask_id)[0][-1] == "refused"
        fresh = h.store.ask(new)
        assert fresh is not None and fresh.status == "open" and fresh.digest == D2
        assert fresh.poster == "local" and fresh.bead == BEAD
        assert audited(h, kind="ask", action="posted", ask_id=new, refreshed_from=ask_id)
        assert audited(h, kind="ask", action="refused", ask_id=ask_id, reason="stale", fresh=new)
        await wait_until(lambda: h.store.card_delivered(new))
        new_first = sent_mid(h, f"ask:{new}:0")
        assert new_first is not None
        assert f"digest {D2[:12]}" in (row_text(h, f"ask:{new}:0") or "")
        assert decisions(h) == []           # the decision is never carried over
        stale = f"Ask {ask_id} is already stale; see ask {new}. Nothing recorded."
        assert (await reacted(h, "👍", first))[1] == stale
        assert (await say(h, "approve", first)) == stale
        assert (await reacted(h, "👍", new_first))[1].startswith(f"Approved {BEAD} as op (digest {D2[:12]}")
        assert decisions(h)[0][3] == f"--expect-digest={D2}"
    go(tmp_path, scenario)


@needs_tmux
def test_a_superseded_card_names_the_newer_one(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        old, old_first = await card(h)
        new, _ = await card(h, join=False)
        assert (await reacted(h, "👍", old_first))[1] == (f"Ask {old} is already superseded; see ask {new}. "
                                                          "Nothing recorded.")
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize(("fields", "why"), [
    ({"status": "closed"}, "the bead is closed, not open"),
    ({"gaps": ["no effect line"]}, "the bead is not approvable as written; send it back for grooming: no "
                                   "effect line"),
    ({"readout": {**LINES, "description": [*DESCRIPTION, f"  │ the key is {TOKEN}"]}},
     "this bead holds text admind would redact; decide it at the terminal"),
    ({"readout": {**LINES, "description": ["description:", *("  │ " + "plan " * 50 for _ in range(100))]}},
     "this bead is too long to decide from the phone; decide it at the terminal"),
])
def test_a_fresh_card_that_cannot_be_posted(tmp_path: Path, fields: dict[str, Any], why: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2, **fields)
        mid, text = await reacted(h, "👍", first)
        assert text == not_refreshed(why)
        assert ask_status(h, ask_id) == "stale" and h.store.newer_ask(ask_id) is None
        cards = outbox_count(h, "ask:%") - outbox_count(h, f"ask:{mid}:%")
        assert cards == h.store.ask(ask_id).card_parts and decisions(h) == []  # type: ignore[union-attr]
        assert outbox_count(h, f"askd:{ask_id}:%") == 0
    go(tmp_path, scenario)


@needs_tmux
def test_a_fresh_card_over_the_hourly_limit(tmp_path: Path) -> None:
    def before_store(h: Harness) -> None:
        store = Store(h.settings.state_dir / "admind.db")
        at = asks.stamp(asks.now())
        for i in range(asks.MAX_PER_HOUR - 1):
            store.insert_ask(row(f"h{asks.ID_ALPHABET[i]}22", status="cancelled", created_at=at), None)
        store.close()

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2)
        assert (await say(h, "approve", first)) == not_refreshed(ASK_TOO_OFTEN.rstrip("."))
        assert ask_status(h, ask_id) == "stale" and h.store.newer_ask(ask_id) is None
    go(tmp_path, scenario, before_store=before_store)


@needs_tmux
def test_a_fresh_card_while_latched(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2, read="wait")
        mid = await send(h, "approve", first)
        await waiting(h)
        h.daemon.latch("test latch")
        go_file(h).touch()
        await settled(h, mid)
        assert queued(h, mid) == not_refreshed(ASK_LATCHED.rstrip("."))
        assert ask_status(h, ask_id) == "stale" and h.store.newer_ask(ask_id) is None
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("status", ["deciding", "uncertain"])
def test_a_fresh_card_refused_while_another_ask_for_the_bead_is_busy(tmp_path: Path, status: str) -> None:
    """The retiring ask is excluded only as the exact (ask, attempt) pair; any other busy ask refuses."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        h.store.insert_ask(row("u222", kind="approval", body="", bead=BEAD, digest=D), None)
        other = "dd" * 32
        assert h.store.begin_attempt(approvals_attempt("u222", other))
        if status == "uncertain":
            assert h.store.close_attempt("u222", other, expect_status="deciding", settled="uncertain",
                                         exit_status=0, new_status="uncertain", outcome=None, decided_by=None)
        edit(tmp_path, digest=D2)
        why = f"ask u222 for {BEAD} is being decided or awaits a read-back; post again once it settles"
        assert (await say(h, "approve", first)) == not_refreshed(why)
        assert ask_status(h, ask_id) == "stale" and ask_status(h, "u222") == status
    go(tmp_path, scenario)


def approvals_attempt(ask_id: str, mid: str) -> Any:
    from heterodyne.admind.approvals import Attempt
    return Attempt(ask_id, mid, "approve", "op", "marmot:" + ref_id(mid), D, None)


@needs_tmux
def test_a_changed_pinned_bead_gets_its_updated_content_marked_undecidable(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2)               # the stored pin (posted_digest = D) no longer matches
        mid, text = await reacted(h, "👍", first)
        why = redact(f"the bead's ask changed after it was posted (posted {D12}, now {D2[:12]}); re-post it")
        assert text == not_refreshed(why) and h.store.newer_ask(ask_id) is None
        hexid = mid.removeprefix("r:")
        rows = h.store.db.execute("SELECT key, text, reply_to, lane FROM outbox WHERE key LIKE ? "
                                  "ORDER BY seq", (f"askd:{ask_id}:%",)).fetchall()
        assert rows and all(r[0].startswith(f"askd:{ask_id}:{hexid}:") for r in rows)     # bare hex, no r:
        assert all(r[2] == first and r[3] == 2 for r in rows)
        content = "".join(r[1] for r in rows)
        assert content.startswith(f"Updated content of {BEAD} (now {D2[:12]}). It cannot be decided yet: its "
                                  "stored pin no longer matches, so its originator must renew the pin and "
                                  "post it again. Reactions and replies here decide nothing.")
        assert "OpenShell does; bubblewrap does not." in content and "approve · 👎" not in content
        await wait_until(lambda: sent_mid(h, f"askd:{ask_id}:{hexid}:0") is not None)
        updated = sent_mid(h, f"askd:{ask_id}:{hexid}:0")
        assert updated is not None and h.store.ask_for_message(updated) == ask_id     # the card recognizer
        stale = f"Ask {ask_id} is already stale. Nothing recorded."
        assert (await reacted(h, "👍", updated))[1] == stale
        assert (await say(h, "approve", updated)) == stale
        assert decisions(h) == [] and "echo: approve" not in h.texts()
    go(tmp_path, scenario, {BEAD: bead(posted_digest=D)})


@needs_tmux
def test_a_changed_pinned_bead_whose_update_would_be_redacted(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2, readout={**LINES, "description": [*DESCRIPTION, f"  │ key {TOKEN}"]})
        text = await say(h, "approve", first)
        assert text.endswith(". Its updated content is not shown: this bead holds text admind would redact; "
                             "decide it at the terminal.")
        assert outbox_count(h, f"askd:{ask_id}:%") == 0 and TOKEN not in json.dumps(h.fake.sent)
    go(tmp_path, scenario, {BEAD: bead(posted_digest=D)})


@needs_tmux
def test_a_bead_changed_during_the_decision_run_gets_a_fresh_card(tmp_path: Path) -> None:
    """Between the step-5 read and the decision run: approve-bead refuses, nothing is written, and the
    `untouched` read-back with a new digest posts the fresh card with no further operator action."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, decide="edit-before")
        mid, text = await reacted(h, "👍", first)
        new = fresh_of(h, ask_id)
        fresh = h.store.ask(new)
        assert fresh is not None and fresh.digest not in (None, D)
        assert text == refreshed(ask_id, new, (fresh.digest or "")[:12]) + (
            f'\napprove-bead said: "The ask is not the one you were shown (expected {D12}, now changed); '
            'nothing written."')
        assert attempts(h, ask_id)[0][-2:] == (3, "untouched") and ask_status(h, ask_id) == "stale"
        assert len(decisions(h)) == 1 and "approved_by" not in json.loads(
            (tmp_path / "btq.json").read_text())[BEAD]
        assert audited(h, kind="ask", action="decided", ask_id=ask_id, outcome="untouched", status="stale",
                       fresh=new)
        assert threads(h, mid) == {first}
    go(tmp_path, scenario)


@needs_tmux
def test_a_socket_post_racing_a_refresh_is_refused_under_the_post_lock(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2, read="wait")
        post = asyncio.create_task(h.daemon.on_ask(APPROVAL, NONE))     # holds ask_post_lock in its read
        await waiting(h)
        mid = await send(h, "approve", first)
        await wait_until(lambda: ask_status(h, ask_id) == "deciding")
        go_file(h).touch()
        await wait_until(lambda: lock_waiters(h.daemon.ask_post_lock) >= 1 or post.done())
        reply = await asyncio.wait_for(post, 30)
        assert reply == asks.refused(f"ask {ask_id} for {BEAD} is being decided or awaits a read-back; post "
                                     "again once it settles.")
        await settled(h, mid)
        assert queued(h, mid) == refreshed(ask_id, fresh_of(h, ask_id))
    go(tmp_path, scenario)


@needs_tmux
def test_a_socket_post_after_a_refresh_supersedes_it(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2)
        await say(h, "approve", first)
        new = fresh_of(h, ask_id)
        later = await posted(h, APPROVAL)
        assert ask_status(h, new) == "superseded" and ask_status(h, later) == "open"
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("where", ["after the attempt closes", "after the fresh ask is inserted",
                                   "before the reply is queued"])
def test_a_failure_inside_the_refresh_rolls_everything_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            where: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        store, daemon = h.daemon.store, h.daemon
        real_close, real_insert, real_finish = store.close_attempt, store.insert_ask, daemon.finish

        def close_attempt(*args: Any, **kw: Any) -> bool:
            done = real_close(*args, **kw)
            if where == "after the attempt closes" and kw.get("new_status") == "stale":
                raise RuntimeError("injected")
            return done

        def insert_ask(*args: Any, **kw: Any) -> None:
            real_insert(*args, **kw)
            if where == "after the fresh ask is inserted":
                raise RuntimeError("injected")

        def finish(mid: str, status: str, text: str, tag: str) -> None:
            if where == "before the reply is queued" and text.startswith(f"{BEAD} changed"):
                raise RuntimeError("injected")
            real_finish(mid, status, text, tag)
        monkeypatch.setattr(store, "close_attempt", close_attempt)
        monkeypatch.setattr(store, "insert_ask", insert_ask)
        monkeypatch.setattr(daemon, "finish", finish)
        edit(tmp_path, digest=D2)
        mid = await send(h, "approve", first)
        await wait_until(lambda: audited(h, kind="handler", action="failed", error="RuntimeError"))
        assert ask_status(h, ask_id) == "deciding" and current(h, ask_id) == mid      # nothing committed
        assert h.store.newer_ask(ask_id) is None and queued(h, mid) == ""
        assert len(h.store.asks_with_status(*asks.ACTIVE, *asks.TERMINAL)) == 1
        monkeypatch.undo()
        backstop = await send(h, "!asks", None)                 # recovery: the backstop reconciles it
        await settled(h, backstop)
        assert ask_status(h, ask_id) == "open"
        assert row_text(h, f"asknote:{ask_id}:reconciled:1:0") == RECONCILE_STRANDED.format(ask_id=ask_id)
        again = await send(h, "approve", first)                 # and the next decision refreshes
        await settled(h, again)
        assert queued(h, again) == refreshed(ask_id, fresh_of(h, ask_id))
    go(tmp_path, scenario)


@needs_tmux
def test_a_failed_compare_and_set_refreshes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        real_close = h.daemon.store.close_attempt

        def close_attempt(*args: Any, **kw: Any) -> bool:
            return False if kw.get("new_status") == "stale" else real_close(*args, **kw)
        monkeypatch.setattr(h.daemon.store, "close_attempt", close_attempt)
        edit(tmp_path, digest=D2)
        mid = await send(h, "approve", first)
        await wait_until(lambda: audited(h, kind="ask", action="conflict", ask_id=ask_id))
        assert h.store.newer_ask(ask_id) is None and queued(h, mid) == ""
        assert outbox_count(h, "ask:%") == h.store.ask(ask_id).card_parts  # type: ignore[union-attr]
    go(tmp_path, scenario)


@needs_tmux
def test_a_crash_before_the_refresh_reopens_and_the_next_decision_refreshes(tmp_path: Path) -> None:
    """Up to the settlement transaction the attempt is still `deciding` (prepare and commit have no await
    between them), so a crash in the decision read leaves it for the R26 reconcile: back to `open`, nothing
    recorded and no fresh card, and the next decision goes through the refresh again."""
    ids: dict[str, str] = {}

    async def crash(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2, read="wait")
        mid = await react(h, "👍", first)
        await waiting(h)
        assert ask_status(h, ask_id) == "deciding" and current(h, ask_id) == mid
        ids.update(ask=ask_id, card=first, mid=mid)       # the run ends here, mid-read
    go(tmp_path, crash)

    async def restarted(h: Harness) -> None:
        await wait_until(lambda: ask_status(h, ids["ask"]) == "open")
        assert h.store.newer_ask(ids["ask"]) is None and h.store.inbound_status(ids["mid"]) == "done"
        notes = h.store.db.execute("SELECT reply_to FROM outbox WHERE key LIKE ?",
                                   (f"asknote:{ids['ask']}:reconciled:%",)).fetchall()
        assert [r[0] for r in notes] == [ids["card"]]       # threaded to the reacted card, not `r:`
        mid, text = await reacted(h, "👍", ids["card"])
        assert text == refreshed(ids["ask"], fresh_of(h, ids["ask"]))
        assert decisions(h) == []
    edit(tmp_path, read=None)
    go(tmp_path, restarted, fresh=False)
