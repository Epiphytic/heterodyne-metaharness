"""Review R1 regression scenarios; fake facts and disposable SQLite only."""

from typing import TypedDict

import msgspec
import pytest
import test_actions_p1 as f

from heterodyne.actions import decisions as dec
from heterodyne.actions import dispatch as dis
from heterodyne.actions.journal import FixtureJournal
from heterodyne.actions.requests import (
    CreationKey,
    Origin,
    Refused,
    Request,
    groom,
    issue_allocation,
    observe_allocation,
    settle_request,
)
from heterodyne.actions.transitions import TransitionObservation


def test_unknown_allocation_cannot_be_settled_by_cleanup_booleans():
    value = issue_allocation(f.initial())
    for elapsed in (1, 5, 30):
        value = observe_allocation(value, ("btq-ab123", "btq-cd456"), lookup_ok=True, elapsed_seconds=elapsed)
    with pytest.raises(Refused):
        settle_request(
            value,
            "reconciled-abandoned",
            key=value.key,
            origin=value.origin,
            cleaned_shells=value.shell_ids,
            blockers_clear=True,
            native_terminal=True,
        )


def test_dispatch_requires_same_transaction_committed_approve(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    from test_actions_p1_dispatch import prepared

    with pytest.raises(Refused, match="decision"):
        db.add_dispatch(prepared())
    db.close()


def test_groom_cannot_reintroduce_session_denied_identity():
    value = msgspec.structs.replace(f.staged("parked"), phase="ask-pinned")
    value = msgspec.structs.replace(value, prior_denials=(value.denial_identity,))
    with pytest.raises(Refused, match="denied"):
        groom(value, value.intent, value.resolved, f.SHA, origin=value.origin)


def test_three_keyed_shells_reach_bounded_recovery():
    value = issue_allocation(f.initial())
    ids = ("btq-ab123", "btq-cd456", "btq-ef789")
    for elapsed in (1, 5, 30):
        value = observe_allocation(value, ids, lookup_ok=True, elapsed_seconds=elapsed)
    assert value.allocation_lookups == 3 and value.shell_ids == ids


def test_groom_new_generation_requires_park_readback():
    value = msgspec.structs.replace(f.staged(), phase="ask-pinned")
    origin = msgspec.structs.replace(value.origin, generation=2)
    result = groom(value, value.intent, value.resolved, f.SHA, origin=origin)
    assert result.origin == origin and result.phase == "blocker-pending"


def test_duplicate_delivery_returns_advanced_record(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    key = db.add_request(f.initial())
    advanced = db.transition(
        "request",
        key,
        0,
        issue_allocation,
        observation=TransitionObservation(now=f.Clock("allocation", 1, 1)),
    )
    assert db.add_request(f.initial()) == key
    assert db.get("request", key) == advanced
    db.close()


def test_noop_durable_transition_acknowledges_without_event(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    key = db.add_request(f.initial())
    before = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
    assert db.transition("request", key, 0, lambda value: value) == f.initial()
    assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == before
    db.close()


def test_denial_reason_counts_unicode_scalars():
    observation = msgspec.structs.replace(f.event("deny"), denial_reason="界" * 1024)
    assert dec.begin_decision(f.filed(), observation, expected_purpose="execution", current_digest=f.DIGEST)


def test_dispatch_persists_initial_kind():
    from test_actions_p1_dispatch import prepared

    assert prepared().dispatch_kind == "initial"


class CleanupFacts(TypedDict):
    key: CreationKey
    origin: Origin
    cleaned_shells: tuple[str, ...]
    blockers_clear: bool
    native_terminal: bool


def cleanup(value: Request) -> CleanupFacts:
    return dict(
        key=value.key,
        origin=value.origin,
        cleaned_shells=value.shell_ids,
        blockers_clear=True,
        native_terminal=True,
    )


@pytest.mark.parametrize("phase", ["completed", "denied", "cancelled", "reconciled-abandoned"])
def test_filed_request_cleanup_alone_never_frees_capacity(phase):
    value = f.filed()
    with pytest.raises(Refused):
        settle_request(value, phase, **cleanup(value))


def test_denial_settlement_and_durable_session_denial_are_atomic(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    # Persist the filing through the same versioned reducer stream as production fixtures.
    key = db.add_request(f.initial())
    value = db.transition(
        "request",
        key,
        0,
        issue_allocation,
        observation=TransitionObservation(now=f.Clock("allocation", 1, 1)),
    )
    value = db.transition(
        "request",
        key,
        value.version,
        lambda v: observe_allocation(v, ("btq-ab123",), lookup_ok=True, elapsed_seconds=1),
        observation=TransitionObservation(
            allocation_elapsed_seconds=1, now=f.Clock("allocation", 1 + (1) * 10**9, 1 + (1) * 10**9)
        ),
    )
    for operation in ("blocker", "park", "land"):
        value = db.transition(
            "request",
            key,
            value.version,
            lambda v, operation=operation: f.observe_stage(
                v,
                operation,
                key=v.key,
                approval_id=v.approval_id,
                origin=v.origin,
                source=f.SHA,
                readback_ok=True,
            ),
        )
    value = db.transition(
        "request", key, value.version, lambda v: f.pin_ask(v, b"whole fixture ask", f.DIGEST, revision=1)
    )
    value = db.transition(
        "request",
        key,
        value.version,
        lambda v: f.observe_sufficient(
            v, digest=f.DIGEST, policy_revision=f.DIGEST, source=f.SHA, sufficient=True
        ),
    )
    value = db.transition(
        "request",
        key,
        value.version,
        lambda v: f.observe_filed(v, digest=f.DIGEST, revision=1, native_readback=True),
    )
    with pytest.raises(Refused):
        db.transition(
            "request",
            key,
            value.version,
            lambda v: settle_request(v, "denied", decision=f.committed("deny"), **cleanup(v)),
            observation=f.cleanup_observation(value),
        )
    assert not db.denied(value.denial_identity, value.origin.session_key)
    assert db.get("request", key) == value
    decision = f.persist_decision(db, "deny")
    done = db.transition(
        "request",
        key,
        value.version,
        lambda v: settle_request(v, "denied", decision=decision, **cleanup(v)),
        observation=f.cleanup_observation(value),
    )
    assert done.phase == "denied" and db.denied(value.denial_identity, value.origin.session_key)
    assert db.db.execute("SELECT outstanding FROM requests WHERE key=?", (key,)).fetchone()[0] == 0
    assert (
        db.transition(
            "request",
            key,
            value.version,
            lambda v: settle_request(v, "denied", **cleanup(v)),
            observation=f.cleanup_observation(value),
        )
        == done
    )
    db.close()


@pytest.mark.parametrize("choice", ["approve", "deny"])
def test_dispatch_rejects_pending_and_committed_deny(tmp_path, choice):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    f.persist_filed_request(db)
    value = dis.prepare(
        "btq-ab123",
        1,
        f.DIGEST,
        f.DIGEST,
        "initial",
        "resource",
        "c" * 64,
        dis.Clock("boot", 1, 1),
        fake_finalized_approval=True,
    )
    if choice == "deny":
        f.persist_decision(db, choice)
    else:
        db.add_decision(f.begun())
    with pytest.raises(Refused, match="committed-approve"):
        db.add_dispatch(value)
    assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
    db.close()


def test_invalid_prewrite_replaced_but_all_inbox_events_remain(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    f.persist_filed_request(db)
    original = f.begun()
    assert db.add_decision(original) == "recorded"
    invalid = db.transition(
        "decision",
        original.approval_id,
        0,
        lambda v: dec.invalidate_decision(v, reason="authority-revoked", current_check=True),
        observation=f.decision_observation(original, authority_current=False),
    )
    next_event = msgspec.structs.replace(f.event("deny"), event_id="event-2")
    replacement = dec.begin_decision(
        f.filed(), next_event, expected_purpose="execution", current_digest=f.DIGEST
    )
    assert db.add_decision(replacement) == "recorded"
    current = db.get("decision", original.approval_id)
    assert current.event == next_event and current.version == invalid.version + 1
    conflict = msgspec.structs.replace(
        f.begun(), event=msgspec.structs.replace(f.event(), event_id="event-3")
    )
    assert db.add_decision(conflict) == "superseded"
    assert db.add_decision(conflict) == "superseded"
    assert db.get("decision", original.approval_id) == current
    assert db.db.execute("SELECT count(*) FROM events WHERE kind='decision-inbox'").fetchone()[0] == 3
    db.close()


def test_issued_unknown_decision_cannot_be_superseded_without_exact_native_conflict(tmp_path):
    value = dec.issue_decision(f.begun(), fresh_authority=True, current_digest=f.DIGEST)
    with pytest.raises(Refused):
        dec.invalidate_decision(value, reason="authority-revoked", current_check=True)
    other = msgspec.structs.replace(f.event("deny"), event_id="native-first")
    invalid = dec.invalidate_decision(
        value, reason="native-conflict", current_check=True, readback=other, native_read_ok=True
    )
    assert invalid.observed_native_event == other
    for field in ("current_principal", "surface_bound", "authority_current"):
        with pytest.raises(Refused):
            dec.invalidate_decision(
                value,
                reason="native-conflict",
                current_check=True,
                readback=msgspec.structs.replace(other, **{field: False}),
                native_read_ok=True,
            )


def test_unconsumed_held_recovery_releases_resources_without_effect_replay(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    f.persist_decision(db)
    clock = dis.Clock("boot", 1, 1)
    value = dis.prepare(
        "btq-ab123",
        1,
        f.DIGEST,
        f.DIGEST,
        "initial",
        "resource",
        "c" * 64,
        clock,
        fake_finalized_approval=True,
    )
    db.add_dispatch(value)
    value = db.transition(
        "dispatch",
        value.approval_id,
        0,
        lambda v: dis.preparation_failed(v, launcher_exited=True, no_children=True),
        observation=f.launcher_observation(value),
    )
    recovery = dis.DispatchRecovery(
        value.approval_id,
        value.revision,
        value.digest,
        value.target,
        value.attempt_id,
        value.version,
        "e" * 64,
        "fixture-recovery",
        True,
        True,
        True,
        True,
        True,
    )
    with pytest.raises(Refused):
        dis.settle_dispatch(value, msgspec.structs.replace(recovery, authenticated_current=False))
    value = db.transition(
        "dispatch",
        value.approval_id,
        value.version,
        lambda v: dis.settle_dispatch(v, recovery),
        observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
    )
    assert value.settled and not value.claim_consumed
    assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
    with pytest.raises(Refused):
        dis.restart_preparation(value, "f" * 64, clock)
    db.close()


def test_reconciliation_has_separate_persisted_kind_identity_and_no_initial_effect(tmp_path):
    from heterodyne.actions.fake_ports import FakeEffect
    from heterodyne.actions.journal import dispatch_key

    path = tmp_path / "fixture.sqlite"
    db = FixtureJournal.create(path)
    from test_actions_p1_r3 import ready_parent

    parent, clock = ready_parent(db, consumed=False)
    parent = db.transition(
        "dispatch",
        parent.approval_id,
        parent.version,
        dis.uncertain,
        observation=TransitionObservation(dispatch_operation="uncertain"),
    )
    reconciler = dis.prepare_reconciliation(parent, "read-only-1", "f" * 64, f.reader_clock(db, parent))
    db.add_dispatch(reconciler)
    key = dispatch_key(reconciler)
    assert key != parent.approval_id
    with pytest.raises(Refused):
        dis.ready(reconciler, parent.startup_hash, clock, fresh_gates=True, startup_readback=True)
    with pytest.raises(Refused):
        db.add_dispatch(
            dis.prepare_reconciliation(parent, "read-only-2", "f" * 64, f.reader_clock(db, parent))
        )
    db.close()
    db = FixtureJournal(path)
    assert db.get("dispatch", key).dispatch_kind == "reconcile"
    assert db.get("dispatch", parent.approval_id) == parent
    with pytest.raises(Refused):
        FakeEffect().perform(reconciler.attempt_id, claim_consumed_readback=True, dispatch_kind="reconcile")
    assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 3
    db.close()


def test_unconsumed_ambiguous_claim_requires_exhausted_readbacks_then_exact_recovery():
    from test_actions_p1_dispatch import claimable

    value = dis.uncertain(claimable())
    assert not value.claim_consumed

    def observation(v):
        return dis.DispatchRecovery(
            v.approval_id,
            v.revision,
            v.digest,
            v.target,
            v.attempt_id,
            v.version,
            "e" * 64,
            "fixture-recovery",
            True,
            True,
            True,
            True,
            True,
        )

    with pytest.raises(Refused):
        dis.settle_dispatch(value, observation(value))
    for elapsed in (1, 5, 30):
        value = dis.reconcile_lookup(value, elapsed_seconds=elapsed, effect="absent", cleanup_proven=True)
    done = dis.settle_dispatch(value, observation(value))
    assert done.settled and done.state == "failed" and not done.claim_consumed
    with pytest.raises(Refused):
        dis.consume_claim(done, done.claim_hash, done.started, fresh_gates=True)


def test_report_ack_loss_is_noop_after_restart_without_duplicate_events(tmp_path):
    path = tmp_path / "fixture.sqlite"
    db = FixtureJournal.create(path)
    f.persist_decision(db)
    clock = dis.Clock("boot", 1, 1)
    value = dis.prepare(
        "btq-ab123",
        1,
        f.DIGEST,
        f.DIGEST,
        "initial",
        "resource",
        "c" * 64,
        clock,
        fake_finalized_approval=True,
    )
    db.add_dispatch(value)
    for operation, reducer in zip(
        ("ready", "consume-claim"),
        (
            lambda v: dis.ready(v, "d" * 64, clock, fresh_gates=True, startup_readback=True),
            lambda v: dis.consume_claim(v, "d" * 64, clock, fresh_gates=True),
        ),
        strict=True,
    ):
        value = db.transition(
            "dispatch",
            value.approval_id,
            value.version,
            reducer,
            observation=TransitionObservation(dispatch_operation=operation, now=clock),
        )
    old_version = value.version

    def success(v):
        return dis.report(
            v,
            attempt_id="initial",
            claim_hash="d" * 64,
            digest=f.DIGEST,
            target=f.DIGEST,
            effect="succeeded",
            evidence="e" * 64,
            cleanup_proven=True,
        )

    done = db.transition(
        "dispatch",
        value.approval_id,
        old_version,
        success,
        observation=f.report_observation(value, effect="succeeded", evidence="e" * 64, cleanup=True),
    )
    db.close()
    db = FixtureJournal(path)
    count = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
    assert db.transition("dispatch", value.approval_id, old_version, success) == done
    assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == count
    assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
    db.close()


def test_new_generation_groom_journal_keeps_stable_session_and_requires_repark(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    original = f.initial()
    key = db.add_request(original)
    value = db.transition(
        "request",
        key,
        0,
        issue_allocation,
        observation=TransitionObservation(now=f.Clock("allocation", 1, 1)),
    )
    value = db.transition(
        "request",
        key,
        value.version,
        lambda v: observe_allocation(v, ("btq-ab123",), lookup_ok=True, elapsed_seconds=1),
        observation=TransitionObservation(
            allocation_elapsed_seconds=1, now=f.Clock("allocation", 1 + (1) * 10**9, 1 + (1) * 10**9)
        ),
    )
    for operation in ("blocker", "park", "land"):
        value = db.transition(
            "request",
            key,
            value.version,
            lambda v, operation=operation: f.observe_stage(
                v,
                operation,
                key=v.key,
                approval_id=v.approval_id,
                origin=v.origin,
                source=f.SHA,
                readback_ok=True,
            ),
        )
    value = db.transition(
        "request", key, value.version, lambda v: f.pin_ask(v, b"whole fixture ask", f.DIGEST, revision=1)
    )
    new_origin = msgspec.structs.replace(value.origin, generation=2)
    value = db.transition(
        "request", key, value.version, lambda v: groom(v, v.intent, v.resolved, f.SHA, origin=new_origin)
    )
    assert value.origin.generation == 2 and value.key == original.key
    with pytest.raises(Refused):
        f.observe_stage(
            value,
            "land",
            key=value.key,
            approval_id=value.approval_id,
            origin=new_origin,
            source=f.SHA,
            readback_ok=True,
        )
    parked = f.observe_stage(
        value,
        "park",
        key=value.key,
        approval_id=value.approval_id,
        origin=new_origin,
        source=f.SHA,
        readback_ok=True,
    )
    assert parked.phase == "parked"
    db.close()


@pytest.mark.parametrize("count", [1025, 1100])
def test_overlong_denial_reason_refuses_in_scalars(count):
    with pytest.raises(Refused):
        dec.begin_decision(
            f.filed(),
            msgspec.structs.replace(f.event("deny"), denial_reason="界" * count),
            expected_purpose="execution",
            current_digest=f.DIGEST,
        )


def test_abandonment_recovery_binds_all_shells_and_exact_current_operator_scope():
    from heterodyne.actions.requests import AbandonmentRecovery

    value = issue_allocation(f.initial())
    for elapsed in (1, 5, 30):
        value = observe_allocation(
            value, ("btq-ab123", "btq-cd456", "btq-ef789"), lookup_ok=True, elapsed_seconds=elapsed
        )
    recovery = AbandonmentRecovery(
        value.key,
        value.origin,
        value.version,
        value.shell_ids,
        "e" * 64,
        "fixture-marmot-abandonment",
        True,
        True,
        True,
    )
    for override in (
        {"authenticated_current": False},
        {"exact_repair_scope": False},
        {"native_readback": False},
        {"expected_version": value.version - 1},
        {"shell_ids": value.shell_ids[:2]},
        {"operator_event": "bad\nlabel"},
    ):
        with pytest.raises(Refused):
            settle_request(
                value,
                "reconciled-abandoned",
                recovery=msgspec.structs.replace(recovery, **override),
                **cleanup(value),
            )
    done = settle_request(value, "reconciled-abandoned", recovery=recovery, **cleanup(value))
    assert done.cleanup_confirmed and done.phase == "reconciled-abandoned"


def test_native_conflict_replacement_must_match_observed_first_event(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    f.persist_filed_request(db)
    first = f.begun()
    db.add_decision(first)
    value = db.transition(
        "decision",
        first.approval_id,
        0,
        lambda v: dec.issue_decision(v, fresh_authority=True, current_digest=f.DIGEST),
        observation=f.decision_observation(first),
    )
    observed = msgspec.structs.replace(f.event("deny"), event_id="actual-first")
    db.transition(
        "decision",
        first.approval_id,
        value.version,
        lambda v: dec.invalidate_decision(
            v, reason="native-conflict", current_check=True, readback=observed, native_read_ok=True
        ),
        observation=f.decision_observation(value, native=observed),
    )
    wrong = dec.begin_decision(
        f.filed(),
        msgspec.structs.replace(observed, event_id="other"),
        expected_purpose="execution",
        current_digest=f.DIGEST,
    )
    assert db.add_decision(wrong) == "superseded"
    correct = dec.begin_decision(f.filed(), observed, expected_purpose="execution", current_digest=f.DIGEST)
    assert db.add_decision(correct) == "recorded"
    assert db.get("decision", first.approval_id).event == observed
    db.close()


def test_full_audit_store_still_acknowledges_duplicates_without_mutation(tmp_path, monkeypatch):
    from heterodyne.actions import journal

    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    key = db.add_request(f.initial())
    monkeypatch.setattr(journal, "MAX_EVENTS", 1)
    assert db.add_request(f.initial()) == key
    assert db.transition("request", key, 0, lambda v: v) == f.initial()
    with pytest.raises(Refused, match="capacity"):
        db.transition(
            "request",
            key,
            0,
            issue_allocation,
            observation=TransitionObservation(now=f.Clock("allocation", 1, 1)),
        )
    assert db.get("request", key) == f.initial()
    assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    db.close()


def test_authenticated_proven_zero_shell_abandonment_frees_unknown_allocation():
    from heterodyne.actions.requests import AbandonmentRecovery

    value = issue_allocation(f.initial())
    for elapsed in (1, 5, 30):
        value = observe_allocation(value, (), lookup_ok=True, elapsed_seconds=elapsed)
    recovery = AbandonmentRecovery(
        value.key, value.origin, value.version, (), "e" * 64, "fixture-zero-shell-repair", True, True, True
    )
    done = settle_request(value, "reconciled-abandoned", recovery=recovery, **cleanup(value))
    assert done.phase == "reconciled-abandoned" and done.cleanup_confirmed
