"""Closed durable transition checks; callbacks are proposals, never authority.

Rebuild request/decision proposals through the pure reducers. All observations are
still synthetic: the unavailable production ports owe their authentication/readback.
"""

from typing import Literal, cast

from heterodyne.actions import decisions as dec
from heterodyne.actions import requests as req
from heterodyne.actions.dispatch import Dispatch
from heterodyne.actions.policy import request_denial_key


def request_transition(
    old: req.Request, value: req.Request, *, decision: object = None, dispatch: object = None
) -> None:
    if value.denial_identity != request_denial_key(value.intent, value.origin, value.resolved, value.config):
        raise req.Refused("denial-identity-not-derived")
    candidate = None
    if value.phase in req.TERMINAL_REQUESTS:
        recovery = None
        if value.phase == "reconciled-abandoned":
            recovery = req.AbandonmentRecovery(
                old.key,
                old.origin,
                old.version,
                old.shell_ids,
                value.recovery_evidence or "",
                value.recovery_operator_event or "",
                True,
                True,
                True,
            )
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
            candidate = req.recover_allocation(
                old,
                req.AllocationRecovery(
                    old.key,
                    old.origin,
                    value.approval_id or "",
                    value.shell_ids,
                    value.recovery_closed_orphans,
                    old.version,
                    value.recovery_evidence or "",
                    value.recovery_operator_event,
                    True,
                    True,
                    True,
                ),
            )
        else:
            candidate = req.observe_allocation(
                old,
                value.shell_ids,
                lookup_ok=value.phase == "allocated",
                elapsed_seconds=(1, 5, 30)[min(old.allocation_lookups, 2)],
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


def dispatch_transition(old: Dispatch, value: Dispatch) -> None:
    """Select a closed operation by its recorded delta, then require its exact result."""
    from heterodyne.actions import dispatch as dis

    if old.dispatch_kind == "reconcile":
        candidate = dis.finish_reconciliation(
            old, evidence=value.evidence or "", cleanup_proven=value.cleanup_proven
        )
    elif value.recovery_operator_event is not None:
        recovery = dis.DispatchRecovery(
            old.approval_id,
            old.revision,
            old.digest,
            old.target,
            old.attempt_id,
            old.version,
            value.evidence or "",
            value.recovery_operator_event,
            authenticated_current=True,
            exact_repair_scope=True,
            native_readback=True,
            effect_definitively_absent=value.state == "failed",
            cleanup_proven=True,
            effect_attested_succeeded=value.state == "succeeded",
        )
        candidate = dis.settle_dispatch(old, recovery)
    elif value.reconciliation_lookups != old.reconciliation_lookups:
        if old.reconciliation_lookups >= 3:
            raise req.Refused("dispatch-readback-exhausted")
        candidate = dis.reconcile_lookup(
            old,
            elapsed_seconds=(1, 5, 30)[old.reconciliation_lookups],
            effect="succeeded" if value.state == "succeeded" else "unknown",
            evidence=value.evidence,
            cleanup_proven=value.cleanup_proven,
        )
    elif value.state == "ready" and value.claim_deadline_ns is not None:
        mono = max(old.started.mono_ns, value.claim_deadline_ns - 5_000_000_000)
        now = dis.Clock(old.started.boot_id, mono, old.started.utc_ns + mono - old.started.mono_ns)
        candidate = dis.ready(old, value.claim_hash or "", now, fresh_gates=True, startup_readback=True)
    elif value.state == "executing":
        candidate = dis.consume_claim(old, old.claim_hash or "", old.started, fresh_gates=True)
    elif value.state == "preparing":
        mono = max(old.started.mono_ns, value.preparation_deadline_ns - 30_000_000_000)
        now = dis.Clock(old.started.boot_id, mono, old.started.utc_ns + mono - old.started.mono_ns)
        candidate = dis.restart_preparation(old, value.startup_hash, now)
    elif value.state == "held" and old.state == "preparing":
        candidate = dis.preparation_failed(old, launcher_exited=True, no_children=True)
    elif value.state == "held":
        candidate = dis.retire_unconsumed(old, old.claim_hash or "", launcher_exited=True, no_children=True)
    elif value == dis.uncertain(old):
        candidate = value
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
