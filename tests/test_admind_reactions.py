"""Relay delta (2026-10-06): decisions by reply or reaction (R7, R8, R13, R27-R31; delta §7.1).

approve-bead is always the fake (tests/fakes/fake_approve_bead.py) over a bead file under tmp_path. Every ID
is an obvious fake. Waits are bounded and checked; nothing here sleeps to order events.
"""

import json

import pytest
from fakes.fake_wn_agent import ACCOUNT, GROUP

from heterodyne.admind import guard, verbs
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
