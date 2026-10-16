"""Disposable pure dispatch model: synthetic identities, nonce hashes and fake clocks only."""

import pytest

from heterodyne.actions.dispatch import (
    Clock,
    consume_claim,
    prepare,
    ready,
    report,
    restart_preparation,
    retire_unconsumed,
    uncertain,
)
from heterodyne.actions.requests import Refused

DIGEST = "a" * 64
START = Clock("boot-one", 100_000_000_000, 1_000_000_000_000)


def prepared():
    return prepare(
        "btq-ab123",
        1,
        DIGEST,
        DIGEST,
        "attempt-1",
        "resource-1",
        "b" * 64,
        START,
        fake_finalized_approval=True,
    )


def claimable():
    return ready(prepared(), "c" * 64, START, fresh_gates=True, startup_readback=True)


def test_one_use_claim_and_lost_ack_never_replays():
    value = claimable()
    claimed = consume_claim(value, "c" * 64, START, fresh_gates=True)
    assert claimed.state == "executing" and claimed.claim_consumed
    held = uncertain(claimed)
    for item in (claimed, held):
        with pytest.raises(Refused):
            consume_claim(item, "c" * 64, START, fresh_gates=True)
        with pytest.raises(Refused):
            retire_unconsumed(item, "c" * 64, launcher_exited=True, no_children=True)


@pytest.mark.parametrize(
    "clock",
    [
        Clock("other-boot", START.mono_ns, START.utc_ns),
        Clock("boot-one", START.mono_ns - 1, START.utc_ns),
        Clock("boot-one", START.mono_ns + 5_000_000_000, START.utc_ns + 5_000_000_000),
    ],
)
def test_claim_clock_expiry_reboot_and_rollback_hold(clock):
    with pytest.raises(Refused):
        consume_claim(claimable(), "c" * 64, clock, fresh_gates=True)


def test_retired_nonce_fenced_and_deadline_not_reset():
    value = retire_unconsumed(claimable(), "c" * 64, launcher_exited=True, no_children=True)
    assert value.state == "held" and value.retired_nonces == ("c" * 64,)
    with pytest.raises(Refused):
        ready(value, "c" * 64, START, fresh_gates=True, startup_readback=True)
    value = restart_preparation(value, "e" * 64, START)
    with pytest.raises(Refused):
        ready(value, "b" * 64, START, fresh_gates=True, startup_readback=True)
    newer = ready(value, "d" * 64, START, fresh_gates=True, startup_readback=True)
    assert newer.deadline_ns == prepared().deadline_ns
    with pytest.raises(Refused):
        consume_claim(newer, "c" * 64, START, fresh_gates=True)


def test_read_only_reconciliation_cannot_claim_initial_effect():
    from heterodyne.actions.dispatch import reconcile_lookup

    value = uncertain(consume_claim(claimable(), "c" * 64, START, fresh_gates=True))
    for elapsed in (1, 5, 30):
        value = reconcile_lookup(value, elapsed_seconds=elapsed, effect="absent", cleanup_proven=True)
        assert value.state == "uncertain"
    with pytest.raises(Refused):
        reconcile_lookup(value, elapsed_seconds=31, effect="absent", cleanup_proven=True)
    with pytest.raises(Refused):
        ready(value, "d" * 64, START, fresh_gates=True, startup_readback=True)


def test_unknown_cleanup_or_wrong_report_never_releases_resource():
    value = consume_claim(claimable(), "c" * 64, START, fresh_gates=True)
    for wrong in ("attempt-wrong",):
        with pytest.raises(Refused):
            report(
                value,
                attempt_id=wrong,
                claim_hash="c" * 64,
                digest=DIGEST,
                target=DIGEST,
                effect="succeeded",
                evidence=DIGEST,
                cleanup_proven=True,
            )
    held = report(
        value,
        attempt_id="attempt-1",
        claim_hash="c" * 64,
        digest=DIGEST,
        target=DIGEST,
        effect="succeeded",
        evidence=DIGEST,
        cleanup_proven=False,
    )
    assert held.state == "uncertain" and not held.settled
    done = report(
        value,
        attempt_id="attempt-1",
        claim_hash="c" * 64,
        digest=DIGEST,
        target=DIGEST,
        effect="succeeded",
        evidence=DIGEST,
        cleanup_proven=True,
    )
    assert done.state == "succeeded" and done.settled
    assert (
        report(
            done,
            attempt_id="attempt-1",
            claim_hash="c" * 64,
            digest=DIGEST,
            target=DIGEST,
            effect="succeeded",
            evidence=DIGEST,
            cleanup_proven=True,
        )
        == done
    )


def test_preparation_failures_count_toward_three_and_never_reset_deadline():
    from heterodyne.actions.dispatch import preparation_failed

    value = prepared()
    for nonce in ("d" * 64, "e" * 64):
        value = preparation_failed(value, launcher_exited=True, no_children=True)
        value = restart_preparation(value, nonce, START)
    assert value.launch_attempts == 3 and value.deadline_ns == prepared().deadline_ns
    value = preparation_failed(value, launcher_exited=True, no_children=True)
    with pytest.raises(Refused):
        restart_preparation(value, "f" * 64, START)


def test_startup_window_is_thirty_seconds_inside_unchanged_deadline():
    late = Clock(START.boot_id, START.mono_ns + 30_000_000_000, START.utc_ns + 30_000_000_000)
    with pytest.raises(Refused):
        ready(prepared(), "c" * 64, late, fresh_gates=True, startup_readback=True)


def test_fixed_authenticated_failed_repair_never_authorizes_initial_retry():
    import msgspec

    from heterodyne.actions.dispatch import DispatchRecovery, reconcile_lookup, settle_failed_dispatch

    value = uncertain(consume_claim(claimable(), "c" * 64, START, fresh_gates=True))
    for elapsed in (1, 5, 30):
        before = value.version
        value = reconcile_lookup(value, elapsed_seconds=elapsed, effect="absent", cleanup_proven=True)
        assert value.version == before + 1
    observation = DispatchRecovery(
        value.approval_id,
        value.revision,
        value.digest,
        value.target,
        value.attempt_id,
        value.version,
        "e" * 64,
        "marmot-fixture-repair",
        True,
        True,
        True,
        True,
        True,
    )
    for field in (
        "authenticated_current",
        "exact_repair_scope",
        "native_readback",
        "effect_definitively_absent",
        "cleanup_proven",
    ):
        with pytest.raises(Refused):
            settle_failed_dispatch(value, msgspec.structs.replace(observation, **{field: False}))
    done = settle_failed_dispatch(value, observation)
    assert done.state == "failed" and done.settled and done.claim_consumed
    assert done.recovery_operator_event == observation.operator_event
    with pytest.raises(Refused):
        restart_preparation(done, "f" * 64, START)
