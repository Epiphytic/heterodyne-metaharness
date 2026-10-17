"""Second HIGH regressions; synthetic facts and private disposable SQLite only."""

import msgspec
import pytest
import test_actions_p1 as f
import test_actions_p1_r1 as r1

from heterodyne.actions import decisions as dec
from heterodyne.actions import dispatch as dis
from heterodyne.actions.journal import FixtureJournal, dispatch_key
from heterodyne.actions.requests import Refused, changed, issue_allocation, settle_request


def persist_request(db, sequence=1, until="filed"):
    key = db.add_request(f.initial(sequence))
    value = db.transition("request", key, 0, issue_allocation)
    value = db.transition(
        "request",
        key,
        value.version,
        lambda v: f.observe_allocation(v, ("btq-ab123",), lookup_ok=True, elapsed_seconds=1),
    )
    for op in ("blocker", "park", "land"):
        value = db.transition(
            "request",
            key,
            value.version,
            lambda v, op=op: f.observe_stage(
                v, op, key=v.key, approval_id=v.approval_id, origin=v.origin, source=f.SHA, readback_ok=True
            ),
        )
    value = db.transition(
        "request", key, value.version, lambda v: f.pin_ask(v, b"whole fixture ask", f.DIGEST, revision=1)
    )
    if until == "ask-pinned":
        return key, value
    value = db.transition(
        "request",
        key,
        value.version,
        lambda v: f.observe_sufficient(
            v, digest=f.DIGEST, policy_revision=f.DIGEST, source=f.SHA, sufficient=True
        ),
    )
    return key, db.transition(
        "request",
        key,
        value.version,
        lambda v: f.observe_filed(v, digest=f.DIGEST, revision=1, native_readback=True),
    )


def held_parent(db):
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
    )
    return value, clock


def test_unrelated_later_denial_does_not_freeze_existing_cleanup(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    _, denied = persist_request(db)
    key, other = persist_request(db, 2, "ask-pinned")
    decision = f.persist_decision(db, "deny")
    db.record_denial(denied, decision)
    done = db.transition(
        "request", key, other.version, lambda v: settle_request(v, "cancelled", **r1.cleanup(v))
    )
    assert done.cleanup_confirmed


def test_superseded_inbox_candidate_replayed_after_invalid_first(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    first = f.begun()
    second = msgspec.structs.replace(first, event=msgspec.structs.replace(first.event, event_id="second"))
    assert db.add_decision(first) == "recorded"
    assert db.add_decision(second) == "superseded"
    db.transition(
        "decision",
        first.approval_id,
        0,
        lambda v: dec.invalidate_decision(v, reason="authority-revoked", current_check=True),
    )
    assert db.add_decision(second) == "recorded"
    assert db.get("decision", first.approval_id).event == second.event
    assert db.db.execute("SELECT count(*) FROM events WHERE kind='decision-inbox'").fetchone()[0] == 2


def test_groom_failure_authenticated_deny_is_durable(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    key, value = persist_request(db, until="ask-pinned")
    for _ in range(2):
        value = db.transition(
            "request", key, value.version, lambda v: f.groom(v, v.intent, v.resolved, f.SHA, origin=v.origin)
        )
        for op in ("park", "land"):
            value = db.transition(
                "request",
                key,
                value.version,
                lambda v, op=op: f.observe_stage(
                    v,
                    op,
                    key=v.key,
                    approval_id=v.approval_id,
                    origin=v.origin,
                    source=f.SHA,
                    readback_ok=True,
                ),
            )
        value = db.transition(
            "request",
            key,
            value.version,
            lambda v: f.pin_ask(v, b"groomed ask", f.DIGEST, revision=v.request_revision),
        )
    assert value.groom_revisions == 2
    with pytest.raises(Refused):
        f.groom(value, value.intent, value.resolved, f.SHA, origin=value.origin)
    decision = dec.begin_decision(
        value, f.event("deny"), expected_purpose="execution", current_digest=f.DIGEST
    )
    db.add_decision(decision)
    if decision.phase == "pending":
        decision = db.transition(
            "decision",
            decision.approval_id,
            decision.version,
            lambda v: dec.issue_decision(v, fresh_authority=True, current_digest=f.DIGEST),
        )
        decision = db.transition(
            "decision",
            decision.approval_id,
            decision.version,
            lambda v: dec.read_decision(v, v.event, read_ok=True),
        )
        for op in ("close", "pins", "finish"):
            decision = db.transition(
                "decision",
                decision.approval_id,
                decision.version,
                lambda v, op=op: dec.finalize_decision(
                    v, op, readback=v.event, current_digest=f.DIGEST, native_readback=True
                ),
            )
    done = db.transition(
        "request",
        key,
        value.version,
        lambda v: settle_request(v, "denied", decision=decision, **r1.cleanup(v)),
    )
    assert done.phase == "denied" and db.denied(value.denial_identity, value.origin.session_key)
    with pytest.raises(Refused):
        dec.begin_decision(value, f.event(), expected_purpose="execution", current_digest=f.DIGEST)


@pytest.mark.parametrize("until", ["filed", "allocation-unknown"])
def test_arbitrary_reducer_cannot_free_outstanding_slot(tmp_path, until):
    db = FixtureJournal.create(tmp_path / "journal")
    if until == "filed":
        key, value = persist_request(db)
    else:
        key = db.add_request(f.initial())
        value = db.transition("request", key, 0, issue_allocation)
    with pytest.raises(Refused):
        db.transition(
            "request", key, value.version, lambda v: changed(v, phase="cancelled", cleanup_confirmed=True)
        )
    assert db.get("request", key) == value


def test_journal_recomputes_initial_denial_identity(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    with pytest.raises(Refused):
        db.add_request(msgspec.structs.replace(f.initial(), denial_identity="f" * 64))


def test_reconcile_cannot_issue_effect_claim_even_with_fresh_nonce(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    parent, clock = held_parent(db)
    value = dis.prepare_reconciliation(parent, "read-1", "d" * 64, clock)
    with pytest.raises(Refused):
        dis.ready(value, "e" * 64, clock, fresh_gates=True, startup_readback=True)
    forged = msgspec.structs.replace(
        value, state="ready", claim_hash="e" * 64, claim_deadline_ns=clock.mono_ns + 1_000_000
    )
    with pytest.raises(Refused):
        dis.consume_claim(forged, "e" * 64, clock, fresh_gates=True)


def test_native_conflict_adopted_without_another_native_write(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    first = f.begun()
    native = msgspec.structs.replace(f.event("deny"), event_id="native-first")
    candidate = dec.begin_decision(f.filed(), native, expected_purpose="execution", current_digest=f.DIGEST)
    db.add_decision(first)
    assert db.add_decision(candidate) == "superseded"
    value = db.transition(
        "decision",
        first.approval_id,
        0,
        lambda v: dec.issue_decision(v, fresh_authority=True, current_digest=f.DIGEST),
    )
    db.transition(
        "decision",
        first.approval_id,
        value.version,
        lambda v: dec.invalidate_decision(
            v, reason="native-conflict", current_check=True, readback=native, native_read_ok=True
        ),
    )
    assert db.add_decision(candidate) == "recorded"
    adopted = db.get("decision", first.approval_id)
    assert adopted.phase == "native-written" and adopted.native_event_id == native.event_id
    with pytest.raises(Refused):
        dec.issue_decision(adopted, fresh_authority=True, current_digest=f.DIGEST)


def test_only_one_reconcile_invocation_may_be_unsettled(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    parent, clock = held_parent(db)
    first = dis.prepare_reconciliation(parent, "read-1", "d" * 64, clock)
    db.add_dispatch(first)
    second = msgspec.structs.replace(
        dis.prepare_reconciliation(parent, "read-2", "e" * 64, clock),
        retired_startups=(*first.retired_startups, first.startup_hash),
    )
    with pytest.raises(Refused):
        db.add_dispatch(second)


def test_audit_capacity_uses_primary_key_not_full_table_count(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    statements = []
    db.db.set_trace_callback(statements.append)
    db.add_request(f.initial())
    assert not any("count(*) FROM events" in sql for sql in statements)


def test_attested_success_after_unknown_cleanup_is_bound_and_never_replays(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    parent, clock = held_parent(db)
    parent = db.transition(
        "dispatch", parent.approval_id, parent.version, lambda v: dis.restart_preparation(v, "d" * 64, clock)
    )
    parent = db.transition(
        "dispatch",
        parent.approval_id,
        parent.version,
        lambda v: dis.ready(v, "e" * 64, clock, fresh_gates=True, startup_readback=True),
    )
    parent = db.transition(
        "dispatch",
        parent.approval_id,
        parent.version,
        lambda v: dis.consume_claim(v, "e" * 64, clock, fresh_gates=True),
    )
    parent = db.transition(
        "dispatch",
        parent.approval_id,
        parent.version,
        lambda v: dis.report(
            v,
            attempt_id=v.attempt_id,
            claim_hash=v.claim_hash,
            digest=v.digest,
            target=v.target,
            effect="succeeded",
            evidence="f" * 64,
            cleanup_proven=False,
        ),
    )
    for elapsed in (1, 5, 30):
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda v, elapsed=elapsed: dis.reconcile_lookup(
                v, elapsed_seconds=elapsed, effect="unknown", cleanup_proven=False
            ),
        )
    recovery = dis.DispatchRecovery(
        parent.approval_id,
        parent.revision,
        parent.digest,
        parent.target,
        parent.attempt_id,
        parent.version,
        "a" * 64,
        "authenticated-fixture-A",
        authenticated_current=True,
        exact_repair_scope=True,
        native_readback=True,
        effect_attested_succeeded=True,
        cleanup_proven=True,
    )
    for field in ("authenticated_current", "exact_repair_scope", "native_readback", "cleanup_proven"):
        with pytest.raises(Refused):
            dis.settle_dispatch(parent, msgspec.structs.replace(recovery, **{field: False}))
    with pytest.raises(Refused):
        dis.settle_dispatch(parent, msgspec.structs.replace(recovery, effect_definitively_absent=True))
    done = db.transition(
        "dispatch", parent.approval_id, parent.version, lambda v: dis.settle_dispatch(v, recovery)
    )
    assert done.state == "succeeded" and done.settled and done.claim_consumed
    assert done.recovery_operator_event == recovery.operator_event
    assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
    with pytest.raises(Refused):
        dis.restart_preparation(done, "b" * 64, clock)


def test_reconciliations_are_serial_and_total_three_across_reopen(tmp_path):
    path = tmp_path / "journal"
    db = FixtureJournal.create(path)
    parent, clock = held_parent(db)
    history = ()
    for ordinal, nonce in enumerate(("d", "e", "f"), 1):
        value = dis.prepare_reconciliation(parent, f"read-{ordinal}", nonce * 64, clock)
        value = msgspec.structs.replace(
            value, retired_startups=tuple(dict.fromkeys((*value.retired_startups, *history)))
        )
        db.add_dispatch(value)
        db.transition(
            "dispatch",
            dispatch_key(value),
            0,
            lambda v: dis.finish_reconciliation(v, evidence="a" * 64, cleanup_proven=True),
        )
        history = (*history, value.startup_hash)
        db.close()
        db = FixtureJournal(path)
    fourth = dis.prepare_reconciliation(parent, "read-4", "a" * 64, clock)
    fourth = msgspec.structs.replace(fourth, retired_startups=(*fourth.retired_startups, *history))
    with pytest.raises(Refused, match="recovery"):
        db.add_dispatch(fourth)
    assert not db.get("dispatch", parent.approval_id).settled
    assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 3


@pytest.mark.parametrize("kind", ["decision", "dispatch"])
def test_arbitrary_reducer_cannot_skip_decision_or_dispatch_gates(tmp_path, kind):
    db = FixtureJournal.create(tmp_path / "journal")
    if kind == "decision":
        value = f.begun()
        db.add_decision(value)

        def forge(v):
            return changed(v, phase="committed", native_event_id=v.event.event_id)
    else:
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

        def forge(v):
            return changed(v, state="succeeded", settled=True, cleanup_proven=True, evidence="e" * 64)

    with pytest.raises(Refused):
        db.transition(kind, value.approval_id, 0, forge)
    assert db.get(kind, value.approval_id) == value


def test_unrelated_denial_does_not_freeze_groom_and_history_is_refreshed(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    _, denied = persist_request(db)
    key, other = persist_request(db, 2, "ask-pinned")
    decision = f.persist_decision(db, "deny")
    db.record_denial(denied, decision)
    intent = msgspec.structs.replace(
        other.intent, action=msgspec.structs.replace(f.PUSH, to_ref="refs/heads/other")
    )
    resolved = msgspec.structs.replace(f.FACTS, destination_ref="refs/heads/other")
    result = db.transition(
        "request", key, other.version, lambda v: f.groom(v, intent, resolved, f.SHA, origin=v.origin)
    )
    assert denied.denial_identity in result.prior_denials
    assert result.phase == "blocker-pending"


def test_invalidated_candidate_never_resurrects_from_inbox_replay(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    first = f.begun()
    db.add_decision(first)
    invalid = db.transition(
        "decision",
        first.approval_id,
        0,
        lambda v: dec.invalidate_decision(v, reason="authority-revoked", current_check=True),
    )
    assert db.add_decision(first) == "superseded"
    assert db.get("decision", first.approval_id) == invalid


def test_transition_and_denial_record_reject_forged_identity(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    key, value = persist_request(db, until="ask-pinned")
    with pytest.raises(Refused):
        db.transition("request", key, value.version, lambda v: changed(v, denial_identity="f" * 64))
    decision = f.persist_decision(db, "deny")
    with pytest.raises(Refused):
        db.record_denial(msgspec.structs.replace(value, denial_identity="f" * 64), decision)
    assert not db.denied("f" * 64, value.origin.session_key)


@pytest.mark.parametrize(
    "change",
    [
        {"expected_version": -1},
        {"attempt_id": "other"},
        {"digest": "e" * 64},
        {"revision": 2},
        {"target": "e" * 64},
        {"approval_id": "btq-zz999"},
    ],
)
def test_attested_success_rejects_stale_or_other_attempt(change):
    from test_actions_p1_dispatch import START, claimable

    value = dis.uncertain(dis.consume_claim(claimable(), "c" * 64, START, fresh_gates=True))
    for elapsed in (1, 5, 30):
        value = dis.reconcile_lookup(value, elapsed_seconds=elapsed, effect="unknown", cleanup_proven=False)
    observation = dis.DispatchRecovery(
        value.approval_id,
        value.revision,
        value.digest,
        value.target,
        value.attempt_id,
        value.version,
        "f" * 64,
        "fixture-current-A",
        authenticated_current=True,
        exact_repair_scope=True,
        native_readback=True,
        cleanup_proven=True,
        effect_attested_succeeded=True,
    )
    with pytest.raises(Refused):
        dis.settle_dispatch(value, msgspec.structs.replace(observation, **change))


def test_declared_cleanup_cannot_forge_abandonment_or_alter_request_fields(tmp_path):
    db = FixtureJournal.create(tmp_path / "journal")
    key = db.add_request(f.initial())
    value = db.transition("request", key, 0, issue_allocation)
    for elapsed in (1, 5, 30):
        value = db.transition(
            "request",
            key,
            value.version,
            lambda v, elapsed=elapsed: f.observe_allocation(v, (), lookup_ok=False, elapsed_seconds=elapsed),
        )
    for updates in (
        {"phase": "reconciled-abandoned", "cleanup_confirmed": True},
        {"requested_source": "e" * 40},
        {"prior_denials": ("e" * 64,)},
    ):
        with pytest.raises(Refused):
            db.transition("request", key, value.version, lambda v, updates=updates: changed(v, **updates))
    assert db.get("request", key) == value


def test_unsafe_denial_reason_has_fixed_reducer_refusal():
    with pytest.raises(Refused, match="decision-reason-unsafe"):
        dec.begin_decision(
            f.filed(),
            msgspec.structs.replace(f.event("deny"), denial_reason="a\u200db"),
            expected_purpose="execution",
            current_digest=f.DIGEST,
        )
