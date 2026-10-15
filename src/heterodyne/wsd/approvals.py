"""A btq approval wsd asks for (plan 4c E13; shared with plan 5i part 2): `compose` builds the bead's title,
description and `metadata.ask`, and `verdict` applies E13's predicates 2-10 to approve-bead's readout and the
bead as `show` returns it. Predicate 1 (`Busy`) is the caller's: a Busy reply is never given to `verdict`.

Every predicate checks its field's type as well as its value, so a field that is missing (UNSET), null or of
another type fails the predicate that reads it, and none can pass vacuously."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from heterodyne.wsd.btq import Readout

DECISIONS = ("approve", "deny")
REASON_MAX = 500                # characters of a deny reason kept
NOTE = ". Note: "               # approve-bead's decision_comment, before a deny's --note


@dataclass(frozen=True)
class AskFields:
    """The ask's four text fields; `refs` is passed apart."""
    effect: str
    excludes: str
    why: str
    risks: str


@dataclass(frozen=True)
class Ref:
    kind: str
    repo: str
    id: str


@dataclass(frozen=True)
class Composed:
    title: str
    description: str
    ask: dict[str, Any]         # exactly btq's ASK_FIELDS


@dataclass(frozen=True)
class Expected:
    """What the journal says the decision must be about: the bead, the ask's digest as created, and a
    check of `metadata.ask.effect` (predicate 10)."""
    bead: str
    context_digest: str
    effect: Callable[[Any], bool]


@dataclass(frozen=True)
class Approved:
    pass


@dataclass(frozen=True)
class Denied:
    """`reason` is the denier's own note, raw and untrusted: whatever shows it escapes it. Its one reader is
    Task 7's resume note, and part 1 T4's `brief.fit` escapes every note before the brief shows it."""
    reason: str | None = None


@dataclass(frozen=True)
class Waiting:
    """The bead is open and holds no decision."""


@dataclass(frozen=True)
class Unverified:
    n: int                      # the first predicate that failed


Verdict = Approved | Denied | Waiting | Unverified


def compose(title: str, fields: AskFields, context: str, refs: Sequence[Ref],
            purpose: str | None) -> Composed:
    """The bead's text. btq's ask has no purpose field, so the purpose is the description's last line."""
    description = context if purpose is None else f"{context}\n\nPurpose: {purpose}."
    ask: dict[str, Any] = {"effect": fields.effect, "excludes": fields.excludes, "why": fields.why,
                           "risks": fields.risks,
                           "refs": [{"kind": r.kind, "repo": r.repo, "id": r.id} for r in refs]}
    return Composed(title, description, ask)


def _text(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _field(shown: Any, name: str) -> Any:
    return cast(dict[str, Any], shown).get(name) if isinstance(shown, dict) else None


def _ask(shown: Any) -> dict[str, Any] | None:
    metadata = _field(shown, "metadata")
    ask = cast(dict[str, Any], metadata).get("ask") if isinstance(metadata, dict) else None
    if isinstance(ask, str):
        try:
            ask = json.loads(ask)
        except (ValueError, RecursionError):
            return None
    return cast(dict[str, Any], ask) if isinstance(ask, dict) else None


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def _deny_reason(shown: Any, by: Any, digest: Any) -> str | None:
    """The note in approve-bead's decision comment, which `bd close --reason` keeps as the bead's
    `close_reason`: "Denied by <by> at <at> via <source>; context_digest=<digest>[; adr_revision=<r>].
    Note: <text>". Only the denier's own decision on this digest counts; anything else is None."""
    text = _field(shown, "close_reason")
    if not (isinstance(text, str) and _text(by) and _text(digest) and text.startswith(f"Denied by {by} at ")):
        return None
    mark = f"; context_digest={digest}"
    at = text.find(mark)
    if at < 0:
        return None
    rest = text[at + len(mark):]
    if rest.startswith("; adr_revision="):
        cut = rest.find(NOTE)
    elif rest.startswith(NOTE):
        cut = 0
    else:
        return None
    if cut < 0:
        return None
    return rest[cut + len(NOTE):].strip()[:REASON_MAX] or None


def verdict(readout: Readout, shown: Any, recomputed: tuple[str | None, list[str]], expect: Expected,
            view: Any) -> Verdict:
    """E13's predicates 2-10, in order. `shown` is the bead from `queue.show`, and `recomputed` btq's
    `ask_digest` of it. A Denied's reason comes from `shown`'s close_reason; `view` is unused (None)."""
    r = readout
    if not (isinstance(r.id, str) and r.id == expect.bead):
        return Unverified(2)
    if not (r.kind_approval is True and "kind:approval" in _list(_field(shown, "labels"))):
        return Unverified(3)
    if r.status == "open" and r.decided is False:
        return Waiting()
    if not (r.status == "closed" and r.decided is True):
        return Unverified(4)
    if (not isinstance(r.decision, str) or r.decision not in DECISIONS
            or any(v is not None and not isinstance(v, str) for v in (r.denied_by, r.denied_digest))):
        return Unverified(5)                    # UNSET is neither None nor a str
    if r.decision == "deny" or r.denied_by is not None or r.denied_digest is not None:
        return Denied(_deny_reason(shown, r.denied_by, r.denied_digest))
    digest, gaps = recomputed
    if not (_text(r.digest) and r.digest == digest and r.gaps == []
            and gaps == []):
        return Unverified(6)
    if not (isinstance(r.posted_digest, str) and r.posted_digest == r.digest
            and r.posted_digest == expect.context_digest):
        return Unverified(7)
    if not (isinstance(r.approved_digest, str) and r.approved_digest == r.digest):
        return Unverified(8)
    approvers = _list(r.approvers)
    if not (approvers and all(_text(a) for a in approvers) and _text(r.approved_by)
            and r.approved_by in approvers):
        return Unverified(9)
    ask = _ask(shown)
    if ask is None or not expect.effect(ask.get("effect")):
        return Unverified(10)
    return Approved()
