"""Membership changes as journaled transitions (ADR 0001 §8, revision 13). Pure; the daemon acts."""

import json
from dataclasses import asdict, dataclass
from typing import Literal

Reported = Literal["ok", "failed", "unknown"]
Outcome = Literal["commit", "abort", "latch"]


@dataclass(frozen=True)
class Pending:
    op: Literal["add", "remove"]
    name: str
    member_hex: str
    from_count: int
    to_count: int
    started: str
    change_id: str = ""     # unique per change: two changes in one second must not share a notice key

    def dump(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def load(text: str) -> "Pending":
        raw = json.loads(text)
        return Pending(raw["op"], raw["name"], raw["member_hex"], int(raw["from_count"]),
                       int(raw["to_count"]), raw["started"], raw.get("change_id", ""))


def settle(reported: Reported, count: int | None, pending: Pending) -> Outcome:
    """Commit only on reported success with the `to` count; abort only on reported failure with the
    `from` count; anything else latches. A count alone never confirms a change (step 4)."""
    if reported == "ok" and count == pending.to_count:
        return "commit"
    if reported == "failed" and count == pending.from_count:
        return "abort"
    return "latch"
