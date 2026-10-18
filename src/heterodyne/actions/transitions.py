"""Closed durable transition checks; callbacks are proposals, never authority.

Rebuild request/decision proposals through the pure reducers. All observations are
still synthetic: the unavailable production ports owe their authentication/readback.
"""

from typing import Literal, cast

from heterodyne.actions import decisions as dec
from heterodyne.actions import requests as req
from heterodyne.actions.contracts import Record
from heterodyne.actions.dispatch import Clock, Dispatch, DispatchRecovery
from heterodyne.actions.policy import request_denial_key


class DecisionReadback(Record, frozen=True):
    """Independent synthetic native/policy read, not a production authentication token."""

    approval_id: str
    request_revision: int
    digest: str
    expected_version: int
    authority_current: bool = False
    current_check: bool = False
    native_read_ok: bool = False
    native_event: dec.DecisionObservation | None = None
    heart_readback: bool = False


class ReconciliationReadback(Record, frozen=True):
    """Explicit fake read-only worker result, independent of the proposed child state."""

    approval_id: str
    revision: int
    digest: str
    target: str
    attempt_id: str
    parent_attempt_id: str
    expected_version: int
    evidence: str
    effect: Literal["succeeded", "absent", "unknown"]
    read_only: bool = False
    invocation_finished: bool = False
    effect_cleanup_proven: bool = False
    cleanup_proven: bool = False


class DispatchReadback(Record, frozen=True):
    """Independent synthetic result from the exact consumed worker invocation."""

    approval_id: str
    revision: int
    digest: str
    target: str
    attempt_id: str
    claim_hash: str
    expected_version: int
    effect: Literal["succeeded", "absent", "unknown"]
    evidence: str | None
    cleanup_proven: bool = False


class CleanupReadback(Record, frozen=True):
    """Native terminal shell/blocker observations, never the proposed terminal state."""

    key: req.CreationKey
    origin: req.Origin
    expected_version: int
    cleaned_shells: tuple[str, ...]
    blockers_clear: bool = False
    native_terminal: bool = False


class TransitionObservation(Record, frozen=True):
    """Independent synthetic port input, never recovered from a proposed state.

    These are explicit attestations, NOT authenticated capabilities. A future admitted
    adapter must sample time and authenticate/read back recovery before calling here.
    """

    now: Clock | None = None
    allocation_elapsed_seconds: int | None = None
    allocation_recovery: req.AllocationRecovery | None = None
    abandonment_recovery: req.AbandonmentRecovery | None = None
    dispatch_recovery: DispatchRecovery | None = None
    reconciliation_key: str | None = None
    decision_readback: DecisionReadback | None = None
    reconciliation_readback: ReconciliationReadback | None = None
    # Supplied only from the journal's retained issuance observation, never caller time.
    allocation_started: Clock | None = None
    cancellation_recovery: req.CancellationRecovery | None = None
    dispatch_operation: (
        Literal[
            "report",
            "settle",
            "lookup",
            "finish-reconciliation",
            "ready",
            "consume-claim",
            "restart-preparation",
            "preparation-failed",
            "retire-unconsumed",
            "uncertain",
        ]
        | None
    ) = None
    dispatch_readback: DispatchReadback | None = None
    cleanup_readback: CleanupReadback | None = None
    reconciliation_started: Clock | None = None


EMPTY_OBSERVATION = TransitionObservation()


def request_transition(
    old: req.Request,
    value: req.Request,
    *,
    decision: object = None,
    dispatch: object = None,
    observation: TransitionObservation = EMPTY_OBSERVATION,
) -> None:
    if value.denial_identity != request_denial_key(value.intent, value.origin, value.resolved, value.config):
        raise req.Refused("denial-identity-not-derived")
    candidate = None
    if value.phase in req.TERMINAL_REQUESTS:
        cleanup = observation.cleanup_readback
        if cleanup is None or (cleanup.key, cleanup.origin, cleanup.expected_version) != (
            old.key,
            old.origin,
            old.version,
        ):
            raise req.Refused("request-cleanup-observation-required")
        recovery = (
            observation.cancellation_recovery
            if value.phase == "cancelled"
            else observation.abandonment_recovery
        )
        candidate = req.settle_request(
            old,
            cast(Literal["completed", "denied", "cancelled", "reconciled-abandoned"], value.phase),
            key=old.key,
            origin=old.origin,
            cleaned_shells=cleanup.cleaned_shells,
            blockers_clear=cleanup.blockers_clear,
            native_terminal=cleanup.native_terminal,
            decision=decision,
            dispatch=dispatch,
            recovery=recovery,
        )
    elif old.phase == "allocation-intent":
        from heterodyne.actions.dispatch import clock_elapsed

        now = _observed_clock(observation)
        clock_elapsed(now, now)
        candidate = req.issue_allocation(old)
    elif old.phase == "allocation-unknown":
        if value.recovery_operator_event is not None:
            if observation.allocation_recovery is None:
                raise req.Refused("allocation-recovery-observation-required")
            candidate = req.recover_allocation(old, observation.allocation_recovery)
        else:
            candidate = req.observe_allocation(
                old,
                value.shell_ids,
                lookup_ok=value.phase == "allocated",
                elapsed_seconds=_allocation_elapsed(observation),
            )
    elif old.phase in {"ask-pinned", "sufficient"} and value.phase == "blocker-pending":
        if isinstance(decision, dec.Decision) and (
            decision.phase != "invalid" or decision.observed_native_event is not None
        ):
            raise req.Refused("groom-decision-unsettled")
        candidate = req.groom(old, value.intent, value.resolved, value.requested_source, origin=value.origin)
    elif old.phase in {"allocated", "blocker-pending", "parked"}:
        operation = {"allocated": "blocker", "blocker-pending": "park", "parked": "land"}[old.phase]
        candidate = req.observe_stage(
            old,
            cast(Literal["blocker", "park", "land"], operation),
            key=old.key,
            approval_id=old.approval_id or "",
            origin=old.origin,
            source=old.requested_source,
            readback_ok=True,
        )
    elif old.phase == "landed":
        candidate = req.pin_ask(
            old, value.ask_bytes or b"", value.context_digest or "", revision=value.request_revision
        )
    elif old.phase == "ask-pinned":
        candidate = req.observe_sufficient(
            old,
            digest=value.context_digest or "",
            policy_revision=value.policy_revision or "",
            source=value.sufficiency_source or "",
            sufficient=True,
        )
    elif old.phase == "sufficient":
        candidate = req.observe_filed(
            old, digest=value.context_digest or "", revision=value.request_revision, native_readback=True
        )
    if candidate != value:
        raise req.Refused("request-transition-not-closed")


def _allocation_elapsed(observation: TransitionObservation) -> int:
    from heterodyne.actions.dispatch import clock_elapsed

    if observation.allocation_started is None:
        raise req.Refused("allocation-time-observation-required")
    return clock_elapsed(observation.allocation_started, _observed_clock(observation))


def decision_transition(
    old: dec.Decision,
    value: dec.Decision,
    *,
    request: req.Request,
    observation: TransitionObservation = EMPTY_OBSERVATION,
) -> None:
    read = observation.decision_readback
    if read is None or (read.approval_id, read.request_revision, read.digest, read.expected_version) != (
        old.approval_id,
        old.request_revision,
        old.digest,
        old.version,
    ):
        raise req.Refused("decision-observation-required")
    candidate = None
    if value.phase == "invalid":
        if value.invalid_reason == "authority-revoked" and read.authority_current:
            raise req.Refused("decision-authority-not-revoked")
        if value.invalid_reason == "digest-stale" and request.context_digest == old.digest:
            raise req.Refused("decision-digest-not-stale")
        candidate = dec.invalidate_decision(
            old,
            reason=cast(
                Literal["authority-revoked", "digest-stale", "native-conflict"], value.invalid_reason
            ),
            current_check=read.current_check,
            readback=read.native_event if value.invalid_reason == "native-conflict" else None,
            native_read_ok=read.native_read_ok,
        )
    else:
        if (request.approval_id, request.request_revision, request.context_digest) != (
            old.approval_id,
            old.request_revision,
            old.digest,
        ) or request.phase not in (
            {"filed"} if old.event.value == "approve" else {"filed", "ask-pinned", "sufficient"}
        ):
            raise req.Refused("decision-current-request-mismatch")
        if old.phase == "pending":
            candidate = dec.issue_decision(
                old, fresh_authority=read.authority_current, current_digest=request.context_digest or ""
            )
        elif old.phase == "write-issued":
            candidate = dec.read_decision(old, read.native_event, read_ok=read.native_read_ok)
        elif old.phase in {"native-written", "native-closed", "pins-finished"}:
            if read.native_event is None:
                raise req.Refused("decision-native-readback-required")
            operation = {"native-written": "close", "native-closed": "pins", "pins-finished": "finish"}[
                old.phase
            ]
            candidate = dec.finalize_decision(
                old,
                cast(Literal["close", "pins", "finish"], operation),
                readback=read.native_event,
                current_digest=request.context_digest or "",
                native_readback=read.native_read_ok and read.authority_current,
            )
        elif old.phase == "committed":
            candidate = dec.settle_heart_proposal(old, proposal_readback=read.heart_readback)
    if candidate != value:
        raise req.Refused("decision-transition-not-closed")


def dispatch_transition(
    old: Dispatch,
    value: Dispatch,
    *,
    observation: TransitionObservation = EMPTY_OBSERVATION,
    reconciliation: Dispatch | None = None,
) -> None:
    """Require an explicit closed operation and its independently observed exact result."""
    from heterodyne.actions import dispatch as dis

    candidate = None
    operation = observation.dispatch_operation
    if operation == "finish-reconciliation" and old.dispatch_kind == "reconcile":
        read = observation.reconciliation_readback
        if (
            read is None
            or (
                read.approval_id,
                read.revision,
                read.digest,
                read.target,
                read.attempt_id,
                read.parent_attempt_id,
                read.expected_version,
            )
            != (
                old.approval_id,
                old.revision,
                old.digest,
                old.target,
                old.attempt_id,
                old.parent_attempt_id,
                old.version,
            )
            or not read.read_only
            or not read.invocation_finished
        ):
            raise req.Refused("reconciliation-observation-required")
        candidate = dis.finish_reconciliation(
            old,
            evidence=read.evidence,
            cleanup_proven=read.cleanup_proven,
            effect=read.effect,
            effect_cleanup_proven=read.effect_cleanup_proven,
        )
    elif operation == "settle":
        recovery = observation.dispatch_recovery
        if recovery is None:
            raise req.Refused("dispatch-recovery-observation-required")
        candidate = dis.settle_dispatch(old, recovery)
    elif operation == "lookup":
        if old.reconciliation_lookups >= 3:
            raise req.Refused("dispatch-readback-exhausted")
        now = _observed_clock(observation)
        if observation.reconciliation_started is None:
            raise req.Refused("reconciliation-clock-history-required")
        elapsed = dis.clock_elapsed(observation.reconciliation_started, now)
        if (
            reconciliation is None
            or not reconciliation.settled
            or reconciliation.dispatch_kind != "reconcile"
            or reconciliation.parent_attempt_id != old.attempt_id
            or (
                reconciliation.approval_id,
                reconciliation.revision,
                reconciliation.digest,
                reconciliation.target,
                reconciliation.resource,
            )
            != (old.approval_id, old.revision, old.digest, old.target, old.resource)
            or reconciliation.attempt_id in old.reconciliation_attempts
            or not reconciliation.cleanup_proven
            or reconciliation.reconciliation_effect is None
        ):
            raise req.Refused("settled-reconciliation-provenance-required")
        candidate = dis.reconcile_lookup(
            old,
            elapsed_seconds=elapsed,
            effect=reconciliation.reconciliation_effect,
            evidence=reconciliation.evidence,
            cleanup_proven=reconciliation.reconciliation_cleanup_proven,
        )
    elif operation == "ready":
        candidate = dis.ready(
            old, value.claim_hash or "", _observed_clock(observation), fresh_gates=True, startup_readback=True
        )
    elif operation == "consume-claim":
        candidate = dis.consume_claim(
            old, old.claim_hash or "", _observed_clock(observation), fresh_gates=True
        )
    elif operation == "restart-preparation":
        candidate = dis.restart_preparation(old, value.startup_hash, _observed_clock(observation))
    elif operation == "preparation-failed":
        candidate = dis.preparation_failed(old, launcher_exited=True, no_children=True)
    elif operation == "retire-unconsumed":
        candidate = dis.retire_unconsumed(old, old.claim_hash or "", launcher_exited=True, no_children=True)
    elif operation == "uncertain":
        candidate = dis.uncertain(old)
    elif operation == "report":
        read = observation.dispatch_readback
        if read is None or (
            read.approval_id,
            read.revision,
            read.digest,
            read.target,
            read.attempt_id,
            read.claim_hash,
            read.expected_version,
        ) != (
            old.approval_id,
            old.revision,
            old.digest,
            old.target,
            old.attempt_id,
            old.claim_hash,
            old.version,
        ):
            raise req.Refused("dispatch-report-observation-required")
        candidate = dis.report(
            old,
            attempt_id=read.attempt_id,
            claim_hash=read.claim_hash,
            digest=read.digest,
            target=read.target,
            effect=read.effect,
            evidence=read.evidence,
            cleanup_proven=read.cleanup_proven,
        )
    if candidate != value:
        raise req.Refused("dispatch-transition-not-closed")


def _observed_clock(observation: TransitionObservation) -> Clock:
    if observation.now is None:
        raise req.Refused("dispatch-clock-observation-required")
    return observation.now
