"""Relay delta (2026-10-06): decisions by reply or reaction (R7, R8, R13, R27-R31; delta §7.1).

approve-bead is always the fake (tests/fakes/fake_approve_bead.py) over a bead file under tmp_path. Every ID
is an obvious fake. Waits are bounded and checked; nothing here sleeps to order events.
"""

import json

from fakes.fake_wn_agent import ACCOUNT, GROUP

from heterodyne.marmot.control import ReactionAdded, decode_event

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
