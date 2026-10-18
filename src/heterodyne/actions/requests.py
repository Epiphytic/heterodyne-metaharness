"""Pure staged request reducer. Inputs are hypothetical observations, not authentication.

No native queue/session/ask API is installed here. In particular FILED is an offline model
state, never a credential or consent token. Production adapters remain unavailable.
"""

from typing import Literal

import msgspec

from heterodyne.actions.codec import (
    BEAD_ID,
    InvalidAction,
    assemble_envelope,
    encode_record,
    safe_label,
    validate_request_inputs,
)
from heterodyne.actions.contracts import ActionConfig, Clock, Envelope, Intent, Origin, Record, Resolved
from heterodyne.actions.policy import denial_key, request_denial_key

RequestPhase = Literal[
    "allocation-intent",
    "allocation-unknown",
    "allocated",
    "blocker-pending",
    "parked",
    "landed",
    "ask-pinned",
    "sufficient",
    "filed",
    "completed",
    "denied",
    "cancelled",
    "reconciled-abandoned",
]
TERMINAL_REQUESTS = frozenset({"completed", "denied", "cancelled", "reconciled-abandoned"})


class Refused(ValueError):
    """Fixed safe diagnostic; no caller strings or native output are echoed."""


class CreationKey(Record, frozen=True):
    workstream: str
    session_key: str
    bead: str
    sequence: int


class Request(Record, frozen=True):
    key: CreationKey
    origin: Origin
    intent: Intent
    resolved: Resolved
    config: ActionConfig
    requested_source: str
    denial_identity: str | None = None
    prior_denials: tuple[str, ...] = ()
    phase: RequestPhase = "allocation-intent"
    version: int = 0
    create_issued: bool = False
    allocation_lookups: int = 0
    shell_ids: tuple[str, ...] = ()
    approval_id: str | None = None
    envelope: Envelope | None = None
    request_revision: int = 1
    groom_revisions: int = 0
    ask_bytes: bytes | None = None
    context_digest: str | None = None
    policy_revision: str | None = None
    sufficiency_source: str | None = None
    cleanup_confirmed: bool = False
    recovery_operator_event: str | None = None
    recovery_evidence: str | None = None
    recovery_closed_orphans: tuple[str, ...] = ()
    # "completed" means terminal lifecycle, never an implicit business-success assertion.
    completion_effect: Literal["succeeded", "absent"] | None = None
    enrichment_base_ask: str | None = None
    enrichment_base_context: str | None = None
    enrichment_candidate: str | None = None


def changed[T: Record](value: T, **updates: object) -> T:
    """Immutability plus monotone CAS version, shared by the three pure reducers."""
    return msgspec.structs.replace(value, version=value.version + 1, **updates)  # type: ignore


def digest_syntax(value: str) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def source_syntax(value: str) -> bool:
    return type(value) is str and len(value) in {40, 64} and all(c in "0123456789abcdef" for c in value)


def new_request(
    origin: Origin,
    sequence: int,
    intent: Intent,
    resolved: Resolved,
    config: ActionConfig,
    source: str,
    prior_denials: tuple[str, ...] = (),
) -> Request:
    validate_request_inputs(intent, origin, resolved, config)
    if type(sequence) is not int or not 1 <= sequence <= 2**31 - 1 or not source_syntax(source):
        raise Refused("invalid-creation-key-or-source")
    if len(prior_denials) > 64 or any(not digest_syntax(d) for d in prior_denials):
        raise Refused("invalid-denial-history")
    return Request(
        CreationKey(origin.workstream, origin.session_key, origin.bead, sequence),
        origin,
        intent,
        resolved,
        config,
        source,
        denial_identity=request_denial_key(intent, origin, resolved, config),
        prior_denials=prior_denials,
    )


def issue_allocation(value: Request) -> Request:
    """Persist this transition before even the fake create port is called; never issue twice."""
    if value.phase != "allocation-intent" or value.create_issued:
        raise Refused("allocation-already-issued-or-held")
    return changed(value, create_issued=True, phase="allocation-unknown")


def observe_allocation(
    value: Request, ids: tuple[str, ...], *, lookup_ok: bool, elapsed_seconds: int
) -> Request:
    """Three bounded read-only lookups; zero, duplicates and failures never authorize another create."""
    if value.phase != "allocation-unknown" or value.allocation_lookups >= 3:
        raise Refused("allocation-needs-authenticated-recovery")
    if elapsed_seconds < (1, 5, 30)[value.allocation_lookups]:
        raise Refused("allocation-lookup-not-due")
    if len(ids) > 8 or len(set(ids)) != len(ids) or any(not BEAD_ID.fullmatch(i) for i in ids):
        raise Refused("invalid-allocation-readback")
    if not lookup_ok and ids:
        raise Refused("contradictory-allocation-readback")
    if lookup_ok and len(ids) == 1 and set(value.shell_ids).issubset(set(ids)):
        approval = ids[0]
        envelope = assemble_envelope(
            value.intent, value.origin, value.resolved, approval, value.request_revision, value.config
        )
        return changed(
            value,
            allocation_lookups=value.allocation_lookups + 1,
            shell_ids=ids,
            approval_id=approval,
            envelope=envelope,
            phase="allocated",
            denial_identity=denial_key(envelope, value.config),
        )
    # Remember all observed shells: later cleanup cannot silently abandon a duplicate.
    shells = tuple(sorted(set(value.shell_ids) | set(ids)))
    if len(shells) > 8:
        raise Refused("allocation-shell-capacity")
    return changed(value, allocation_lookups=value.allocation_lookups + 1, shell_ids=shells)


# Exact ordered readback stages. A bool in a fake observation is not native ownership proof.
_STAGE = {
    "allocated": ("blocker-pending", "blocker"),
    "blocker-pending": ("parked", "park"),
    "parked": ("landed", "land"),
}


def observe_stage(
    value: Request,
    operation: Literal["blocker", "park", "land"],
    *,
    key: CreationKey,
    approval_id: str,
    origin: Origin,
    source: str,
    readback_ok: bool,
) -> Request:
    if (
        _STAGE.get(value.phase, (None, None))[1] != operation
        or not readback_ok
        or key != value.key
        or approval_id != value.approval_id
        or origin != value.origin
        or source != value.requested_source
    ):
        raise Refused("request-stage-binding")
    return changed(value, phase=_STAGE[value.phase][0])


def pin_ask(value: Request, whole_ask: bytes, context_digest: str, *, revision: int) -> Request:
    """Retain opaque whole native ask bytes and native digest. Never implement a substitute digest.

    The future native adapter must prove these bytes include the whole canonical action_data,
    title/description/refs and recompute its genuine context_digest. P1 has no such adapter.
    """
    if (
        value.phase != "landed"
        or revision != value.request_revision
        or not digest_syntax(context_digest)
        or type(whole_ask) is not bytes
        or not 1 <= len(whole_ask) <= 65_536
    ):
        raise Refused("ask-pin-binding")
    return changed(value, phase="ask-pinned", ask_bytes=whole_ask, context_digest=context_digest)


def repin_enriched_ask(
    value: Request, whole_ask: bytes, context_digest: str, candidate_digest: str
) -> Request:
    """Pure host-observed enrichment repin; no native ask update or semantic proof.

    P2 checks the enrichment proposal before using this transition. Like pin_ask,
    caller-supplied opaque bytes are not authenticated by this reducer.
    """
    import hashlib

    if (
        value.phase != "ask-pinned"
        or value.ask_bytes is None
        or value.context_digest is None
        or value.approval_id is None
        or value.enrichment_candidate is not None
        or type(whole_ask) is not bytes
        or not 1 <= len(whole_ask) <= 65536
        or whole_ask == value.ask_bytes
        or not digest_syntax(context_digest)
        or context_digest == value.context_digest
        or not digest_syntax(candidate_digest)
    ):
        raise Refused("enrichment-repin-refused")
    revision = value.request_revision + 1
    return changed(
        value,
        request_revision=revision,
        ask_bytes=whole_ask,
        context_digest=context_digest,
        envelope=assemble_envelope(
            value.intent, value.origin, value.resolved, value.approval_id, revision, value.config
        ),
        policy_revision=None,
        sufficiency_source=None,
        enrichment_base_ask=hashlib.sha256(value.ask_bytes).hexdigest(),
        enrichment_base_context=value.context_digest,
        enrichment_candidate=candidate_digest,
    )


def observe_sufficient(
    value: Request, *, digest: str, policy_revision: str, source: str, sufficient: bool
) -> Request:
    if (
        value.phase != "ask-pinned"
        or not sufficient
        or digest != value.context_digest
        or source != value.requested_source
        or not digest_syntax(policy_revision)
    ):
        raise Refused("sufficiency-missing-or-stale")
    return changed(value, phase="sufficient", policy_revision=policy_revision, sufficiency_source=source)


def observe_filed(value: Request, *, digest: str, revision: int, native_readback: bool) -> Request:
    if (
        value.phase != "sufficient"
        or digest != value.context_digest
        or revision != value.request_revision
        or not native_readback
    ):
        raise Refused("filing-incomplete")
    return changed(value, phase="filed")


def same_session(previous: Origin, current: Origin) -> bool:
    """A generation changes; the actual originating logical identity does not."""
    return (previous.workstream, previous.bead, previous.session_key, previous.repo_alias) == (
        current.workstream,
        current.bead,
        current.session_key,
        current.repo_alias,
    )


def groom(value: Request, intent: Intent, resolved: Resolved, source: str, *, origin: Origin) -> Request:
    """Same logical origin, monotone generation; require fresh park and landing readbacks."""
    if (
        value.phase not in {"ask-pinned", "sufficient"}
        or value.groom_revisions >= 2
        or not same_session(value.origin, origin)
        or origin.generation < value.origin.generation
        or not source_syntax(source)
        or value.approval_id is None
    ):
        raise Refused("groom-held")
    envelope = assemble_envelope(
        intent, origin, resolved, value.approval_id, value.request_revision + 1, value.config
    )
    identity = denial_key(envelope, value.config)
    if identity in value.prior_denials:
        raise Refused("same-session-denied-intent")
    return changed(
        value,
        phase="blocker-pending",
        origin=origin,
        intent=intent,
        resolved=resolved,
        requested_source=source,
        denial_identity=identity,
        envelope=envelope,
        request_revision=value.request_revision + 1,
        groom_revisions=value.groom_revisions + 1,
        ask_bytes=None,
        context_digest=None,
        policy_revision=None,
        sufficiency_source=None,
        enrichment_base_ask=None,
        enrichment_base_context=None,
        enrichment_candidate=None,
    )


def settle_request(
    value: Request,
    phase: Literal["completed", "denied", "cancelled", "reconciled-abandoned"],
    *,
    key: CreationKey,
    origin: Origin,
    cleaned_shells: tuple[str, ...],
    blockers_clear: bool,
    native_terminal: bool,
    decision: object = None,
    dispatch: object = None,
    recovery: object = None,
) -> Request:
    needs_withdrawal = phase == "cancelled" and value.phase in {"ask-pinned", "sufficient", "filed"}
    if recovery is not None and not (
        (phase == "reconciled-abandoned" and isinstance(recovery, AbandonmentRecovery))
        or (needs_withdrawal and isinstance(recovery, CancellationRecovery))
    ):
        raise Refused("unexpected-settlement-recovery")
    if (
        phase not in TERMINAL_REQUESTS
        or key != value.key
        or origin != value.origin
        or not blockers_clear
        or not native_terminal
        or set(cleaned_shells) != set(value.shell_ids)
        or (not value.shell_ids and phase != "reconciled-abandoned")
    ):
        raise Refused("request-cleanup-unproved")
    if value.phase in TERMINAL_REQUESTS:
        if value.phase == phase and value.cleanup_confirmed:
            return value
        raise Refused("terminal-request-conflict")
    # These typed observations are fake-port facts; the durable journal also
    # corroborates its committed decision/dispatch in the same transaction.
    from heterodyne.actions.decisions import Decision
    from heterodyne.actions.dispatch import Dispatch

    if phase == "denied":
        if (
            value.phase not in {"filed", "ask-pinned", "sufficient"}
            or not isinstance(decision, Decision)
            or decision.phase != "committed"
            or decision.event.value != "deny"
            or (decision.approval_id, decision.digest, decision.request_revision)
            != (value.approval_id, value.context_digest, value.request_revision)
        ):
            raise Refused("request-denial-not-committed")
    elif phase == "completed":
        if (
            value.phase != "filed"
            or not isinstance(dispatch, Dispatch)
            or not dispatch.settled
            or dispatch.state not in {"succeeded", "failed"}
            or not dispatch.cleanup_proven
            or (dispatch.state == "succeeded" and not dispatch.claim_consumed)
            or (dispatch.state == "failed" and not dispatch.recovery_operator_event)
            or dispatch.dispatch_kind != "initial"
            or (dispatch.approval_id, dispatch.digest, dispatch.revision)
            != (value.approval_id, value.context_digest, value.request_revision)
        ):
            raise Refused("request-completion-not-proved")
    elif phase == "cancelled":
        if (
            isinstance(decision, Decision)
            and decision.phase == "committed"
            and decision.event.value == "deny"
        ):
            raise Refused("committed-denial-requires-denied-settlement")
        if isinstance(decision, Decision) and (
            decision.phase != "invalid" or decision.observed_native_event is not None
        ):
            raise Refused("request-cancellation-decision-unresolved")
        if value.phase not in {
            "allocated",
            "blocker-pending",
            "parked",
            "landed",
            "ask-pinned",
            "sufficient",
            "filed",
        }:
            raise Refused("request-cancellation-not-unposted")
        if value.phase in {"ask-pinned", "sufficient", "filed"}:
            if (
                not isinstance(recovery, CancellationRecovery)
                or (recovery.key, recovery.origin, recovery.expected_version, recovery.shell_ids)
                != (value.key, value.origin, value.version, value.shell_ids)
                or not recovery.authenticated_current
                or not recovery.exact_repair_scope
                or not recovery.native_withdrawal_readback
                or not recovery.cleanup_proven
                or not digest_syntax(recovery.evidence)
                or not safe_label(recovery.operator_event)
                or (
                    isinstance(decision, Decision)
                    and (decision.phase != "invalid" or decision.observed_native_event is not None)
                )
                or dispatch is not None
            ):
                raise Refused("filed-cancellation-not-authenticated")
    else:
        if (
            value.phase != "allocation-unknown"
            or not isinstance(recovery, AbandonmentRecovery)
            or (value.allocation_lookups != 3 and not recovery.clock_discontinuity)
            or (recovery.key, recovery.origin, recovery.expected_version, recovery.shell_ids)
            != (value.key, value.origin, value.version, value.shell_ids)
            or not recovery.authenticated_current
            or not recovery.exact_repair_scope
            or not recovery.native_readback
            or not digest_syntax(recovery.evidence)
            or not safe_label(recovery.operator_event)
        ):
            raise Refused("request-abandonment-not-authenticated")
    return changed(
        value,
        phase=phase,
        cleanup_confirmed=True,
        completion_effect=(
            "succeeded" if isinstance(dispatch, Dispatch) and dispatch.state == "succeeded" else "absent"
        )
        if phase == "completed"
        else value.completion_effect,
        recovery_operator_event=recovery.operator_event
        if isinstance(recovery, (AbandonmentRecovery, CancellationRecovery))
        else value.recovery_operator_event,
        recovery_evidence=recovery.evidence
        if isinstance(recovery, (AbandonmentRecovery, CancellationRecovery))
        else value.recovery_evidence,
    )


class AbandonmentRecovery(Record, frozen=True):
    """Fake observation of exact authenticated abandonment, never raw cleanup consent."""

    key: CreationKey
    origin: Origin
    expected_version: int
    shell_ids: tuple[str, ...]
    evidence: str
    operator_event: str
    authenticated_current: bool = False
    exact_repair_scope: bool = False
    native_readback: bool = False
    clock_discontinuity: bool = False
    observed_now: Clock | None = None


def request_bytes(value: Request) -> bytes:
    result = encode_record(value)
    if len(result) > 262_144:
        raise InvalidAction("request-record-size")
    return result


class AllocationRecovery(Record, frozen=True):
    """Fake observation of a genuine future fixed Marmot repair; not an auth token."""

    key: CreationKey
    origin: Origin
    canonical_id: str
    associated_ids: tuple[str, ...]
    closed_orphans: tuple[str, ...]
    expected_version: int
    evidence_digest: str
    operator_event: str
    authenticated_current: bool = False
    exact_repair_scope: bool = False
    native_readback: bool = False
    clock_discontinuity: bool = False
    observed_now: Clock | None = None


def recover_allocation(value: Request, observation: AllocationRecovery) -> Request:
    """Adopt one proven canonical shell without recreating or forgetting the orphan history."""
    ids = observation.associated_ids
    if (
        value.phase != "allocation-unknown"
        or (value.allocation_lookups != 3 and not observation.clock_discontinuity)
        or observation.key != value.key
        or observation.origin != value.origin
        or observation.expected_version != value.version
        or not observation.authenticated_current
        or not observation.exact_repair_scope
        or not observation.native_readback
        or not digest_syntax(observation.evidence_digest)
        or not safe_label(observation.operator_event)
        or not 1 <= len(ids) <= 8
        or len(set(ids)) != len(ids)
        or any(not BEAD_ID.fullmatch(i) for i in ids)
        or observation.canonical_id not in ids
        or not set(value.shell_ids).issubset(set(ids))
        or set(observation.closed_orphans) != set(ids) - {observation.canonical_id}
    ):
        raise Refused("allocation-repair-not-bound")
    envelope = assemble_envelope(
        value.intent,
        value.origin,
        value.resolved,
        observation.canonical_id,
        value.request_revision,
        value.config,
    )
    return changed(
        value,
        phase="allocated",
        approval_id=observation.canonical_id,
        envelope=envelope,
        denial_identity=denial_key(envelope, value.config),
        shell_ids=tuple(sorted(ids)),
        recovery_operator_event=observation.operator_event,
        recovery_evidence=observation.evidence_digest,
        recovery_closed_orphans=tuple(sorted(observation.closed_orphans)),
    )


class CancellationRecovery(Record, frozen=True):
    """Synthetic observation of exact authenticated withdrawal and native cleanup."""

    key: CreationKey
    origin: Origin
    expected_version: int
    shell_ids: tuple[str, ...]
    evidence: str
    operator_event: str
    authenticated_current: bool = False
    exact_repair_scope: bool = False
    native_withdrawal_readback: bool = False
    cleanup_proven: bool = False
