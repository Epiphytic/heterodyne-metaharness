"""Offline dispatch contract. No subprocess, credentials, broker or effect adapter exists.

These reducers describe conditional native transitions a later admitted adapter must implement.
Booleans are fake-port observations; they are not authentication or approval capabilities.
"""

from typing import Literal

import msgspec

from heterodyne.actions.codec import BEAD_ID, safe_label
from heterodyne.actions.contracts import Clock, Record
from heterodyne.actions.requests import Refused, changed, digest_syntax

DispatchState = Literal[
    "preparing", "ready", "held", "executing", "uncertain", "succeeded", "failed", "denied"
]


class Dispatch(Record, frozen=True):
    approval_id: str
    revision: int
    digest: str
    target: str
    attempt_id: str
    resource: str
    startup_hash: str
    started: Clock
    deadline_ns: int
    preparation_deadline_ns: int
    state: DispatchState = "preparing"
    version: int = 0
    claim_hash: str | None = None
    claim_deadline_ns: int | None = None
    claim_consumed: bool = False
    launch_attempts: int = 1
    retired_startups: tuple[str, ...] = ()
    retired_nonces: tuple[str, ...] = ()
    reconciliation_lookups: int = 0
    evidence: str | None = None
    cleanup_proven: bool = False
    settled: bool = False
    recovery_operator_event: str | None = None
    dispatch_kind: Literal["initial", "reconcile"] = "initial"
    parent_attempt_id: str | None = None
    reconciliation_attempts: tuple[str, ...] = ()
    reconciliation_effect: Literal["succeeded", "absent", "unknown"] | None = None
    reconciliation_cleanup_proven: bool = False


def _clock(value: Clock) -> None:
    if (
        type(value.boot_id) is not str
        or not 1 <= len(value.boot_id) <= 128
        or not value.boot_id.isascii()
        or not all(c.isalnum() or c in "-_." for c in value.boot_id)
        or type(value.mono_ns) is not int
        or type(value.utc_ns) is not int
        or not 0 <= value.mono_ns < 2**63
        or not 0 <= value.utc_ns < 2**63
    ):
        raise Refused("invalid-clock")


def elapsed_seconds(value: Dispatch, now: Clock) -> int:
    return clock_elapsed(value.started, now)


def clock_elapsed(started: Clock, now: Clock) -> int:
    """Elapsed time is valid only on one continuous independently sampled clock."""
    _clock(started)
    _clock(now)
    if (
        now.boot_id != started.boot_id
        or now.mono_ns < started.mono_ns
        or now.utc_ns < started.utc_ns
        or abs((now.utc_ns - started.utc_ns) - (now.mono_ns - started.mono_ns)) > 1_000_000_000
    ):
        raise Refused("dispatch-clock-or-deadline")
    return (now.mono_ns - started.mono_ns) // 1_000_000_000


def _within(value: Dispatch, now: Clock) -> None:
    elapsed_seconds(value, now)
    if now.mono_ns >= value.deadline_ns:
        raise Refused("dispatch-clock-or-deadline")


def prepare(
    approval_id: str,
    revision: int,
    digest: str,
    target: str,
    attempt_id: str,
    resource: str,
    startup_hash: str,
    now: Clock,
    *,
    fake_finalized_approval: bool,
) -> Dispatch:
    """Hypothetical preparation only; production admission is always adapter-unavailable.

    A future native adapter must derive approval/target/resource from its authoritative record,
    serialize conflicting resources, and verify all fresh facts before using this model.
    """
    _clock(now)
    if (
        not fake_finalized_approval
        or not BEAD_ID.fullmatch(approval_id)
        or type(revision) is not int
        or not 1 <= revision <= 2**31 - 1
        or not all(digest_syntax(v) for v in (digest, target, startup_hash))
        or not 1 <= len(attempt_id) <= 128
        or not attempt_id.isascii()
        or not 1 <= len(resource) <= 256
        or not resource.isascii()
        or not all(c.isalnum() or c in "-_.:" for c in attempt_id + resource)
    ):
        raise Refused("dispatch-preparation-binding")
    return Dispatch(
        approval_id,
        revision,
        digest,
        target,
        attempt_id,
        resource,
        startup_hash,
        now,
        now.mono_ns + 60_000_000_000,
        now.mono_ns + 30_000_000_000,
    )


def ready(
    value: Dispatch, claim_hash: str, now: Clock, *, fresh_gates: bool, startup_readback: bool
) -> Dispatch:
    _within(value, now)
    if (
        value.dispatch_kind != "initial"
        or value.state != "preparing"
        or value.claim_consumed
        or not fresh_gates
        or not startup_readback
        or not digest_syntax(claim_hash)
        or claim_hash == value.startup_hash
        or claim_hash in value.retired_nonces
        or claim_hash in value.retired_startups
        or now.mono_ns >= value.preparation_deadline_ns
    ):
        raise Refused("dispatch-not-ready")
    return changed(
        value,
        state="ready",
        claim_hash=claim_hash,
        claim_deadline_ns=min(now.mono_ns + 5_000_000_000, value.deadline_ns),
    )


def restart_preparation(value: Dispatch, startup_hash: str, now: Clock) -> Dispatch:
    """A new bounded launcher attempt needs its own startup nonce and original fixed deadline."""
    _within(value, now)
    if (
        value.state != "held"
        or value.claim_consumed
        or value.launch_attempts >= 3
        or not digest_syntax(startup_hash)
        or startup_hash == value.startup_hash
        or startup_hash in value.retired_startups
        or startup_hash in value.retired_nonces
    ):
        raise Refused("preparation-retry-refused")
    return changed(
        value,
        state="preparing",
        startup_hash=startup_hash,
        retired_startups=(*value.retired_startups, value.startup_hash),
        preparation_deadline_ns=min(now.mono_ns + 30_000_000_000, value.deadline_ns),
        launch_attempts=value.launch_attempts + 1,
    )


def preparation_failed(value: Dispatch, *, launcher_exited: bool, no_children: bool) -> Dispatch:
    if value.state != "preparing" or not launcher_exited or not no_children or value.claim_consumed:
        raise Refused("preparation-cleanup-unproved")
    return changed(value, state="held", cleanup_proven=True)


def consume_claim(value: Dispatch, claim_hash: str, now: Clock, *, fresh_gates: bool) -> Dispatch:
    """CAS this state DURABLY before returning claim success. Lost acknowledgement is uncertain."""
    _within(value, now)
    if (
        value.dispatch_kind != "initial"
        or value.state != "ready"
        or value.claim_consumed
        or not fresh_gates
        or claim_hash != value.claim_hash
        or claim_hash in value.retired_nonces
        or value.claim_deadline_ns is None
        or now.mono_ns >= value.claim_deadline_ns
    ):
        raise Refused("claim-refused")
    return changed(value, state="executing", claim_consumed=True)


def retire_unconsumed(
    value: Dispatch, claim_hash: str, *, launcher_exited: bool, no_children: bool
) -> Dispatch:
    """Race with claim must be one native conditional transaction; a failed CAS releases nothing."""
    if (
        value.state != "ready"
        or value.claim_consumed
        or claim_hash != value.claim_hash
        or not launcher_exited
        or not no_children
        or value.claim_hash is None
    ):
        raise Refused("claim-retirement-unproved")
    return changed(
        value,
        state="held",
        claim_hash=None,
        claim_deadline_ns=None,
        retired_nonces=(*value.retired_nonces, claim_hash),
    )


def uncertain(value: Dispatch) -> Dispatch:
    if value.state == "uncertain" and not value.settled:
        return value
    if value.settled or value.state not in {"ready", "executing", "uncertain"}:
        raise Refused("settled-or-unissued-dispatch-conflict")
    return changed(value, state="uncertain")


def report(
    value: Dispatch,
    *,
    attempt_id: str,
    claim_hash: str,
    digest: str,
    target: str,
    effect: Literal["succeeded", "absent", "unknown"],
    evidence: str | None,
    cleanup_proven: bool,
) -> Dispatch:
    """No result can re-enable an initial effect. Exact repeated successful report is idempotent."""
    if (
        not value.claim_consumed
        or value.claim_hash != claim_hash
        or attempt_id != value.attempt_id
        or digest != value.digest
        or target != value.target
        or effect not in {"succeeded", "absent", "unknown"}
        or (evidence is not None and not digest_syntax(evidence))
    ):
        raise Refused("dispatch-report-binding")
    if value.settled:
        if (
            value.state == "succeeded"
            and effect == "succeeded"
            and cleanup_proven
            and evidence == value.evidence
        ):
            return value
        raise Refused("settled-report-conflict")
    if value.state not in {"executing", "uncertain"}:
        raise Refused("report-outside-execution")
    if value.evidence is not None and evidence != value.evidence:
        raise Refused("retained-report-evidence-conflict")
    if effect == "succeeded" and evidence is not None and cleanup_proven:
        return changed(value, state="succeeded", evidence=evidence, cleanup_proven=True, settled=True)
    # An absent effect after consumption is NOT permission to perform it again.
    if effect == "absent" and evidence is not None and cleanup_proven:
        return changed(value, state="failed", evidence=evidence, cleanup_proven=True)
    return changed(value, state="uncertain", evidence=evidence, cleanup_proven=cleanup_proven)


def reconcile_lookup(
    value: Dispatch,
    *,
    elapsed_seconds: int,
    effect: Literal["succeeded", "absent", "unknown"],
    cleanup_proven: bool,
    evidence: str | None = None,
) -> Dispatch:
    if (
        value.dispatch_kind != "initial"
        or (effect == "succeeded" and not value.claim_consumed)
        or value.state not in {"uncertain", "failed"}
        or value.settled
        or value.reconciliation_lookups >= 3
    ):
        raise Refused("reconciliation-needs-authenticated-recovery")
    if elapsed_seconds < (1, 5, 30)[value.reconciliation_lookups]:
        raise Refused("reconciliation-not-due")
    if effect not in {"succeeded", "absent", "unknown"}:
        raise Refused("invalid-reconciliation")
    if effect == "succeeded" and cleanup_proven and digest_syntax(evidence or ""):
        return changed(
            value,
            reconciliation_lookups=value.reconciliation_lookups + 1,
            state="succeeded",
            settled=True,
            evidence=evidence,
            cleanup_proven=True,
        )
    return changed(
        value,
        reconciliation_lookups=value.reconciliation_lookups + 1,
        state="failed"
        if value.state == "failed"
        or (effect == "absent" and cleanup_proven and digest_syntax(evidence or ""))
        else "uncertain",
        cleanup_proven=value.cleanup_proven if value.state == "failed" else cleanup_proven,
        evidence=value.evidence if value.state == "failed" else evidence,
    )


class DispatchRecovery(Record, frozen=True):
    """Fake exact-scope operator observation; real admission adapter is unavailable."""

    approval_id: str
    revision: int
    digest: str
    target: str
    attempt_id: str
    expected_version: int
    evidence: str
    operator_event: str
    authenticated_current: bool = False
    exact_repair_scope: bool = False
    native_readback: bool = False
    effect_definitively_absent: bool = False
    cleanup_proven: bool = False
    effect_attested_succeeded: bool = False
    # Explicit recovery of a discontinuous clock; never permits initial-effect replay.
    clock_discontinuity: bool = False
    observed_now: Clock | None = None
    clock_anchor: Clock | None = None


def settle_dispatch(value: Dispatch, observation: DispatchRecovery) -> Dispatch:
    """Exact operator-attested outcome and cleanup; never machine attribution or retry."""
    discontinuous = False
    if observation.clock_discontinuity and observation.observed_now is not None:
        _clock(observation.observed_now)
        try:
            clock_elapsed(observation.clock_anchor or value.started, observation.observed_now)
        except Refused:
            discontinuous = True
    if (
        value.state not in {"held", "uncertain", "failed"}
        or value.settled
        or (value.claim_consumed and value.reconciliation_lookups != 3 and not discontinuous)
        or (
            not value.claim_consumed
            and value.state != "held"
            and value.reconciliation_lookups != 3
            and not discontinuous
        )
        or (
            observation.approval_id,
            observation.revision,
            observation.digest,
            observation.target,
            observation.attempt_id,
            observation.expected_version,
        )
        != (value.approval_id, value.revision, value.digest, value.target, value.attempt_id, value.version)
        or not all(
            (
                observation.authenticated_current,
                observation.exact_repair_scope,
                observation.native_readback,
                observation.cleanup_proven,
            )
        )
        or observation.effect_definitively_absent == observation.effect_attested_succeeded
        or (observation.effect_attested_succeeded and not value.claim_consumed)
        or not digest_syntax(observation.evidence)
        or not safe_label(observation.operator_event)
    ):
        raise Refused("dispatch-recovery-not-bound")
    return changed(
        value,
        state="succeeded" if observation.effect_attested_succeeded else "failed",
        settled=True,
        cleanup_proven=True,
        evidence=observation.evidence,
        recovery_operator_event=observation.operator_event,
    )


def finish_reconciliation(
    value: Dispatch,
    *,
    evidence: str,
    cleanup_proven: bool,
    effect: Literal["succeeded", "absent", "unknown"] = "unknown",
    effect_cleanup_proven: bool = False,
) -> Dispatch:
    """Retire a read-only invocation; its evidence does not itself settle the parent effect."""
    if (
        value.dispatch_kind != "reconcile"
        or value.state != "preparing"
        or value.settled
        or value.claim_consumed
        or value.claim_hash is not None
        or not cleanup_proven
        or not digest_syntax(evidence)
        or effect not in {"succeeded", "absent", "unknown"}
    ):
        raise Refused("reconcile-cleanup-unproved")
    return changed(
        value,
        state="failed",
        settled=True,
        cleanup_proven=True,
        evidence=evidence,
        reconciliation_effect=effect,
        reconciliation_cleanup_proven=effect_cleanup_proven,
    )


def prepare_reconciliation(value: Dispatch, attempt_id: str, startup_hash: str, now: Clock) -> Dispatch:
    """Separate read-only invocation identity; never a new initial-effect record."""
    if (
        value.dispatch_kind != "initial"
        or value.settled
        or value.state not in {"uncertain", "failed"}
        or attempt_id == value.attempt_id
        or startup_hash in (value.startup_hash, value.claim_hash)
        or startup_hash in value.retired_startups
        or startup_hash in value.retired_nonces
    ):
        raise Refused("reconcile-invocation-not-bound")
    result = prepare(
        value.approval_id,
        value.revision,
        value.digest,
        value.target,
        attempt_id,
        value.resource,
        startup_hash,
        now,
        fake_finalized_approval=True,
    )
    return msgspec.structs.replace(
        result,
        dispatch_kind="reconcile",
        parent_attempt_id=value.attempt_id,
        retired_startups=tuple(dict.fromkeys((*value.retired_startups, value.startup_hash))),
        retired_nonces=tuple(
            dict.fromkeys((*value.retired_nonces, *((value.claim_hash,) if value.claim_hash else ())))
        ),
    )
