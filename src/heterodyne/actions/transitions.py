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
        recovery = observation.abandonment_recovery
        candidate = req.settle_request(
            old,
            cast(Literal["completed", "denied", "cancelled", "reconciled-abandoned"], value.phase),
            key=old.key,
            origin=old.origin,
            cleaned_shells=old.shell_ids,
            blockers_clear=True,
            native_terminal=True,
            decision=decision,
            dispatch=dispatch,
            recovery=recovery,
        )
    elif old.phase == "allocation-intent":
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
    value = observation.allocation_elapsed_seconds
    if type(value) is not int or value < 0:
        raise req.Refused("allocation-time-observation-required")
    return value


def decision_transition(old: dec.Decision, value: dec.Decision) -> None:
    candidate = None
    if value.phase == "invalid":
        candidate = dec.invalidate_decision(
            old,
            reason=cast(
                Literal["authority-revoked", "digest-stale", "native-conflict"], value.invalid_reason
            ),
            current_check=True,
            readback=value.observed_native_event,
            native_read_ok=True,
        )
    elif old.phase == "pending":
        candidate = dec.issue_decision(old, fresh_authority=True, current_digest=old.digest)
    elif old.phase == "write-issued":
        candidate = dec.read_decision(old, value.event, read_ok=True)
    elif old.phase in {"native-written", "native-closed", "pins-finished"}:
        operation = {"native-written": "close", "native-closed": "pins", "pins-finished": "finish"}[old.phase]
        candidate = dec.finalize_decision(
            old,
            cast(Literal["close", "pins", "finish"], operation),
            readback=old.event,
            current_digest=old.digest,
            native_readback=True,
        )
    elif old.phase == "committed":
        candidate = dec.settle_heart_proposal(old, proposal_readback=True)
    if candidate != value:
        raise req.Refused("decision-transition-not-closed")


def dispatch_transition(
    old: Dispatch,
    value: Dispatch,
    *,
    observation: TransitionObservation = EMPTY_OBSERVATION,
    reconciliation: Dispatch | None = None,
) -> None:
    """Select a closed operation by its recorded delta, then require its exact result."""
    from heterodyne.actions import dispatch as dis

    if old.dispatch_kind == "reconcile":
        candidate = dis.finish_reconciliation(
            old,
            evidence=value.evidence or "",
            cleanup_proven=value.cleanup_proven,
            effect=value.reconciliation_effect or "unknown",
        )
    elif value.recovery_operator_event is not None:
        recovery = observation.dispatch_recovery
        if recovery is None:
            raise req.Refused("dispatch-recovery-observation-required")
        candidate = dis.settle_dispatch(old, recovery)
    elif value.reconciliation_lookups != old.reconciliation_lookups:
        if old.reconciliation_lookups >= 3:
            raise req.Refused("dispatch-readback-exhausted")
        now = _observed_clock(observation)
        elapsed = dis.elapsed_seconds(old, now)
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
            or reconciliation.evidence != value.evidence
            or (value.state == "succeeded") != (reconciliation.reconciliation_effect == "succeeded")
        ):
            raise req.Refused("settled-reconciliation-provenance-required")
        candidate = dis.reconcile_lookup(
            old,
            elapsed_seconds=elapsed,
            effect="succeeded" if value.state == "succeeded" else "unknown",
            evidence=value.evidence,
            cleanup_proven=value.cleanup_proven,
        )
    elif value.state == "ready" and value.claim_deadline_ns is not None:
        candidate = dis.ready(
            old, value.claim_hash or "", _observed_clock(observation), fresh_gates=True, startup_readback=True
        )
    elif value.state == "executing":
        candidate = dis.consume_claim(
            old, old.claim_hash or "", _observed_clock(observation), fresh_gates=True
        )
    elif value.state == "preparing":
        candidate = dis.restart_preparation(old, value.startup_hash, _observed_clock(observation))
    elif value.state == "held" and old.state == "preparing":
        candidate = dis.preparation_failed(old, launcher_exited=True, no_children=True)
    elif value.state == "held":
        candidate = dis.retire_unconsumed(old, old.claim_hash or "", launcher_exited=True, no_children=True)
    elif (
        value.state == "uncertain"
        and value.evidence == old.evidence
        and value.cleanup_proven == old.cleanup_proven
    ):
        candidate = dis.uncertain(old)
    else:
        candidate = dis.report(
            old,
            attempt_id=old.attempt_id,
            claim_hash=old.claim_hash or "",
            digest=old.digest,
            target=old.target,
            effect=cast(
                Literal["succeeded", "absent", "unknown"],
                {"succeeded": "succeeded", "failed": "absent"}.get(value.state, "unknown"),
            ),
            evidence=value.evidence,
            cleanup_proven=value.cleanup_proven,
        )
    if candidate != value:
        raise req.Refused("dispatch-transition-not-closed")


def _observed_clock(observation: TransitionObservation) -> Clock:
    if observation.now is None:
        raise req.Refused("dispatch-clock-observation-required")
    return observation.now
