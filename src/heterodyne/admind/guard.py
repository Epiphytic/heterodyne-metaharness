"""Ingress decisions for admind (ADR 0001 §3.4, §8). Pure functions; the daemon acts on the verdicts.

- Only MLS-authenticated messages from the operator's exact key, in admind's own group, are processed.
- Any membership or admin change, or a member count other than two, latches admind (plan decision D4):
  `group_info` reports a count, not a member list, so a swap that keeps the count is visible only as
  an event.
"""

from dataclasses import dataclass
from typing import Literal

from heterodyne.marmot.control import GroupStateChanged, InboundMessage

MEMBERSHIP_CHANGES = frozenset({"member_added", "member_removed", "member_left", "admin_added",
                                "admin_removed"})


@dataclass(frozen=True)
class Verdict:
    action: Literal["process", "drop", "ignore", "latch"]
    reason: str


def judge_message(ev: InboundMessage, *, group_id: str, operator_hex: str, latched: bool) -> Verdict:
    if ev.group_id_hex.lower() != group_id:
        return Verdict("drop", "message from another group")
    sender = ev.message.sender
    if sender.is_self:
        return Verdict("ignore", "admind's own message")
    if sender.account_id_hex.lower() != operator_hex:
        return Verdict("drop", "sender is not the operator")
    if latched:
        return Verdict("drop", "admind is latched; run `admind rearm` on the host")
    return Verdict("process", "operator message")


def judge_group_change(ev: GroupStateChanged, *, group_id: str) -> Verdict:
    if ev.group_id_hex.lower() != group_id:
        return Verdict("ignore", "change in another group")
    if ev.change in MEMBERSHIP_CHANGES:
        return Verdict("latch", f"group membership changed ({ev.change})")
    return Verdict("ignore", f"group change {ev.change}")


def judge_member_count(count: int) -> Verdict:
    if count == 2:
        return Verdict("process", "two members")
    return Verdict("latch", f"group has {count} members, not 2")
