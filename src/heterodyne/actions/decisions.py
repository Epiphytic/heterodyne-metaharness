"""Pure first-decision and staged native finalization model; no installed decision writer.

A hypothetical authenticated observation remains caller-supplied data. The future all-kinds
sole writer owes current principal, purpose, surface/event and native context checks.
"""

import unicodedata
from typing import Literal

from heterodyne.actions.codec import safe_label
from heterodyne.actions.contracts import Record
from heterodyne.actions.requests import Refused, Request, changed, digest_syntax

Purpose = Literal["design", "execution", "permission", "confirm", "recovery"]
DecisionPhase = Literal[
    "pending", "write-issued", "native-written", "native-closed", "pins-finished", "committed", "invalid"
]


class DecisionObservation(Record, frozen=True):
    event_id: str
    surface_id: str
    operator_id: str
    via_ref: str
    at: str
    approval_id: str
    purpose: Purpose
    digest: str
    value: Literal["approve", "deny"]
    denial_reason: str = ""
    current_principal: bool = False
    surface_bound: bool = False
    authority_current: bool = False


class Decision(Record, frozen=True):
    approval_id: str
    purpose: Purpose
    digest: str
    event: DecisionObservation
    phase: DecisionPhase = "pending"
    version: int = 0
    native_event_id: str | None = None
    heart_proposal_pending: bool = False
    request_revision: int = 1
    invalid_reason: str | None = None
    observed_native_event: DecisionObservation | None = None


def _emoji_base(ch: str) -> bool:
    return unicodedata.category(ch) == "So" and (0x2300 <= ord(ch) <= 0x27BF or 0x1F000 <= ord(ch) <= 0x1FAFF)


def _metadata_controls_safe(reason: str) -> bool:
    """Finite visible metadata grammar, including emoji adornments and flag tags."""
    index = 0
    while index < len(reason):
        ch = reason[index]
        code = ord(ch)
        if ch == "\r":
            if reason[index : index + 2] != "\r\n":
                return False
            index += 2
            continue
        if ch == "🏴" and index + 1 < len(reason) and 0xE0000 <= ord(reason[index + 1]) <= 0xE007F:
            end = index + 1
            while end < len(reason) and (
                0xE0061 <= ord(reason[end]) <= 0xE007A or 0xE0030 <= ord(reason[end]) <= 0xE0039
            ):
                end += 1
            if end == index + 1 or end == len(reason) or ord(reason[end]) != 0xE007F:
                return False
            index = end + 1
            continue
        if ch == "\u200d":
            left = index - 1
            while left >= 0 and (reason[left] == "\ufe0f" or 0x1F3FB <= ord(reason[left]) <= 0x1F3FF):
                left -= 1
            if (
                left < 0
                or index + 1 == len(reason)
                or not (_emoji_base(reason[left]) and _emoji_base(reason[index + 1]))
            ):
                return False
        elif (
            unicodedata.category(ch) in {"Cc", "Cs", "Cf"}
            or ch in "\u2028\u2029\u115f\u1160\u3164\uffa0\u2800\u034f"
            or 0x180B <= code <= 0x180E
        ):
            return False
        index += 1
    return True


def _validate_denial_reason(reason: str) -> None:
    """Metadata permits visible emoji and CRLF without relaxing identifier rules."""
    from heterodyne.config.secret_scan import secret_value

    if (
        not _metadata_controls_safe(reason)
        or (
            reason
            and not any(
                not ch.isspace() and unicodedata.category(ch) not in {"Cf", "Cc", "Mn", "Me"} for ch in reason
            )
        )
        or secret_value(reason)
    ):
        raise Refused("decision-reason-unsafe")


def begin_decision(
    request: Request,
    event: DecisionObservation,
    *,
    expected_purpose: Purpose,
    current_digest: str,
    heart: bool = False,
) -> Decision:
    if (
        (
            request.phase != "filed"
            and not (event.value == "deny" and request.phase in {"ask-pinned", "sufficient"})
        )
        or request.context_digest != current_digest
        or event.digest != current_digest
        or event.approval_id != request.approval_id
        or event.purpose != expected_purpose
        or expected_purpose not in {"design", "execution", "permission", "confirm", "recovery"}
        or event.value not in {"approve", "deny"}
        or not digest_syntax(current_digest)
        or not event.current_principal
        or not event.surface_bound
        or not event.authority_current
        or not all(
            safe_label(v, 256) for v in (event.event_id, event.surface_id, event.operator_id, event.via_ref)
        )
        or not safe_label(event.at, 64)
        or type(event.denial_reason) is not str
        or len(event.denial_reason) > 1024
        or (event.value == "approve" and event.denial_reason != "")
    ):
        raise Refused("decision-observation-binding")
    _validate_denial_reason(event.denial_reason)
    return Decision(
        event.approval_id,
        expected_purpose,
        current_digest,
        event,
        heart_proposal_pending=heart and event.value == "approve",
        request_revision=request.request_revision,
    )


def issue_decision(value: Decision, *, fresh_authority: bool, current_digest: str) -> Decision:
    if value.phase != "pending" or not fresh_authority or current_digest != value.digest:
        raise Refused("decision-write-not-admitted")
    return changed(value, phase="write-issued")


def read_decision(value: Decision, readback: DecisionObservation | None, *, read_ok: bool) -> Decision:
    """Missing/uncertain write response only permits exact readback, never a second write."""
    if value.phase != "write-issued" or not read_ok or readback is None or readback != value.event:
        raise Refused("decision-write-unresolved-or-conflicting")
    return changed(value, phase="native-written", native_event_id=readback.event_id)


_FINALIZE = {
    "native-written": ("native-closed", "close"),
    "native-closed": ("pins-finished", "pins"),
    "pins-finished": ("committed", "finish"),
}


def finalize_decision(
    value: Decision,
    operation: Literal["close", "pins", "finish"],
    *,
    readback: DecisionObservation,
    current_digest: str,
    native_readback: bool,
) -> Decision:
    if (
        value.phase not in _FINALIZE
        or _FINALIZE[value.phase][1] != operation
        or readback != value.event
        or current_digest != value.digest
        or not native_readback
    ):
        raise Refused("decision-finalization-binding")
    return changed(value, phase=_FINALIZE[value.phase][0])


def settle_heart_proposal(value: Decision, *, proposal_readback: bool) -> Decision:
    """Proposal recovery changes no ordinary decision or dispatch permission."""
    if value.phase != "committed" or not proposal_readback:
        raise Refused("heart-proposal-unresolved")
    if not value.heart_proposal_pending:
        return value
    return changed(value, heart_proposal_pending=False)


def invalidate_decision(
    value: Decision,
    *,
    reason: Literal["authority-revoked", "digest-stale", "native-conflict"],
    current_check: bool,
    readback: DecisionObservation | None = None,
    native_read_ok: bool = False,
) -> Decision:
    """Audit invalid prewrite event or exact conflicting native first event; never replay unknown writes."""
    if (
        not current_check
        or value.phase not in {"pending", "write-issued"}
        or reason not in {"authority-revoked", "digest-stale", "native-conflict"}
    ):
        raise Refused("decision-invalidation-not-bound")
    if value.phase == "write-issued":
        if (
            reason != "native-conflict"
            or not native_read_ok
            or readback is None
            or readback == value.event
            or readback.approval_id != value.approval_id
            or readback.digest != value.digest
            or readback.purpose != value.purpose
            or not readback.current_principal
            or not readback.surface_bound
            or not readback.authority_current
        ):
            raise Refused("decision-write-still-uncertain")
    elif reason == "native-conflict":
        raise Refused("decision-conflict-without-issued-write")
    return changed(value, phase="invalid", invalid_reason=reason, observed_native_event=readback)
