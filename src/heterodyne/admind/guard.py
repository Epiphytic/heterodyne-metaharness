"""Ingress decisions for admind (ADR 0001 §3.4, §8). Pure functions; the daemon acts on the verdicts.

- Only MLS-authenticated messages from an operator's exact key, in admind's own group, are processed. A
  reaction is judged by the same rules on its actor (relay delta R27).
- Any membership or admin change, or a member count other than the trusted expected count, latches
  admind (plan decision D4): `group_info` reports a count, not a member list, so a swap that keeps the
  count is visible only as an event.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from heterodyne.marmot.control import GroupStateChanged, InboundMessage, ReactionAdded

MEMBERSHIP_CHANGES = frozenset({"member_added", "member_removed", "member_left", "admin_added",
                                "admin_removed"})


@dataclass(frozen=True)
class Verdict:
    action: Literal["process", "drop", "ignore", "latch"]
    reason: str
    operator: str | None = None


def judge_message(ev: InboundMessage | ReactionAdded, *, group_id: str, operators: Mapping[str, str],
                  latched: bool) -> Verdict:
    """A message is judged on its sender, a reaction on its actor, by the same rules (R27)."""
    if ev.group_id_hex.lower() != group_id:
        return Verdict("drop", "message from another group")
    sender = ev.message.sender if isinstance(ev, InboundMessage) else ev.actor
    if sender.is_self:
        return Verdict("ignore", "admind's own message")
    name = operators.get(sender.account_id_hex.lower())
    if name is None:
        return Verdict("drop", "sender is not an operator")
    if latched:     # an authenticated operator: the message is still audited in full, under their name
        return Verdict("drop", "admind is latched; run `admind rearm` on the host", name)
    return Verdict("process", "operator message", name)


def judge_group_change(ev: GroupStateChanged, *, group_id: str) -> Verdict:
    if ev.group_id_hex.lower() != group_id:
        return Verdict("ignore", "change in another group")
    if ev.change in MEMBERSHIP_CHANGES:
        return Verdict("latch", f"group membership changed ({ev.change})")
    return Verdict("ignore", f"group change {ev.change}")


def judge_member_count(count: int, expected: int) -> Verdict:
    if count == expected:
        return Verdict("process", f"{count} members, as expected")
    return Verdict("latch", f"group has {count} members, expected {expected}")
