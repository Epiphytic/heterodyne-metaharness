"""Third-review integrity regressions; private offline journals and synthetic observations."""

from contextlib import closing

import msgspec
import pytest
import test_actions_p1 as f
import test_actions_p1_r1 as r1
import test_actions_p1_r2 as r2

from heterodyne.actions import dispatch as dis
from heterodyne.actions.journal import FixtureJournal, dispatch_key, request_key
from heterodyne.actions.requests import Refused, changed, settle_request
from heterodyne.actions.transitions import TransitionObservation


def test_insert_cannot_approve_unfiled_request(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        r2.persist_request(db, until="ask-pinned")
        with pytest.raises(Refused):
            db.add_decision(f.begun())


def test_unissued_claim_cannot_become_uncertain_or_successful():
    value = dis.prepare(
        "btq-ab123",
        1,
        f.DIGEST,
        f.DIGEST,
        "attempt",
        "resource",
        "c" * 64,
        dis.Clock("boot", 1, 1),
        fake_finalized_approval=True,
    )
    with pytest.raises(Refused):
        dis.uncertain(value)
    forged = msgspec.structs.replace(value, state="uncertain")
    with pytest.raises(Refused):
        dis.reconcile_lookup(
            forged, elapsed_seconds=1, effect="succeeded", evidence=f.DIGEST, cleanup_proven=True
        )


def test_failed_settlement_can_finish_request_without_claim_replay():
    value = dis.prepare(
        "btq-ab123",
        1,
        f.DIGEST,
        f.DIGEST,
        "attempt",
        "resource",
        "c" * 64,
        dis.Clock("boot", 1, 1),
        fake_finalized_approval=True,
    )
    settled = msgspec.structs.replace(
        value,
        state="failed",
        settled=True,
        cleanup_proven=True,
        recovery_operator_event="marmot-fixture",
        evidence=f.DIGEST,
    )
    done = settle_request(f.filed(), "completed", dispatch=settled, **r1.cleanup(f.filed()))
    assert done.cleanup_confirmed


def test_committed_deny_cannot_cancel():
    request = f.filed()
    request = msgspec.structs.replace(request, phase="ask-pinned")
    with pytest.raises(Refused):
        settle_request(request, "cancelled", decision=f.committed("deny"), **r1.cleanup(request))


def test_denial_cannot_poison_non_durable_request(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        _, request = r2.persist_request(db)
        decision = f.persist_decision(db, "deny")
        forged = msgspec.structs.replace(
            request, origin=msgspec.structs.replace(request.origin, session_key="unrelated-session")
        )
        from heterodyne.actions.policy import request_denial_key

        forged = msgspec.structs.replace(
            forged,
            denial_identity=request_denial_key(forged.intent, forged.origin, forged.resolved, forged.config),
        )
        with pytest.raises(Refused):
            db.record_denial(forged, decision)


def test_recovery_fields_are_not_recovery_authority(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r2.held_parent(db)
        with pytest.raises(Refused):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: changed(
                    old,
                    state="failed",
                    settled=True,
                    cleanup_proven=True,
                    evidence=f.DIGEST,
                    recovery_operator_event="invented",
                ),
            )


@pytest.mark.parametrize("field", ["current_principal", "surface_bound", "authority_current"])
def test_insert_revalidates_current_event_flags(tmp_path, field):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        f.persist_filed_request(db)
        value = f.begun()
        value = msgspec.structs.replace(value, event=msgspec.structs.replace(value.event, **{field: False}))
        with pytest.raises(Refused):
            db.add_decision(value)
        assert db.db.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("digest", "a" * 64), ("request_revision", 2)])
def test_insert_revalidates_whole_request_binding(tmp_path, field, value):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        f.persist_filed_request(db)
        with pytest.raises(Refused):
            db.add_decision(msgspec.structs.replace(f.begun(), **{field: value}))


def ready_parent(db, *, consumed=True):
    parent, clock = r2.held_parent(db)
    for reducer in (
        lambda old: dis.restart_preparation(old, "d" * 64, clock),
        lambda old: dis.ready(old, "e" * 64, clock, fresh_gates=True, startup_readback=True),
    ):
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            reducer,
            observation=TransitionObservation(now=clock),
        )
    if consumed:
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old: dis.consume_claim(old, "e" * 64, clock, fresh_gates=True),
            observation=TransitionObservation(now=clock),
        )
    return parent, clock


@pytest.mark.parametrize("seconds", [5, 61])
def test_journal_checks_independent_actual_clock_not_reducer_clock(tmp_path, seconds):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = ready_parent(db, consumed=False)
        now = dis.Clock(clock.boot_id, clock.mono_ns + seconds * 10**9, clock.utc_ns + seconds * 10**9)
        with pytest.raises(Refused):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.consume_claim(old, "e" * 64, clock, fresh_gates=True),
                observation=TransitionObservation(now=now),
            )
        assert not db.get("dispatch", parent.approval_id).claim_consumed
        with pytest.raises(Refused, match="clock-observation-required"):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.consume_claim(old, "e" * 64, clock, fresh_gates=True),
            )


def test_allocation_backoff_requires_independent_elapsed_observation(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key = db.add_request(f.initial())
        value = db.transition(
            "request",
            key,
            0,
            f.issue_allocation,
            observation=TransitionObservation(now=f.Clock("allocation", 1, 1)),
        )

        def proposal(old):
            return f.observe_allocation(old, ("btq-ab123",), lookup_ok=True, elapsed_seconds=1)

        for observation in (
            TransitionObservation(),
            TransitionObservation(
                allocation_elapsed_seconds=0, now=f.Clock("allocation", 1 + (0) * 10**9, 1 + (0) * 10**9)
            ),
        ):
            with pytest.raises(Refused):
                db.transition("request", key, value.version, proposal, observation=observation)
        assert db.get("request", key) == value


def test_parent_readback_requires_settled_child_and_joins_exact_outcome(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = ready_parent(db)
        parent = db.transition("dispatch", parent.approval_id, parent.version, dis.uncertain)
        now = dis.Clock(clock.boot_id, clock.mono_ns + 10**9, clock.utc_ns + 10**9)

        def proposal(old):
            return dis.reconcile_lookup(
                old, elapsed_seconds=1, effect="succeeded", evidence=f.DIGEST, cleanup_proven=True
            )

        with pytest.raises(Refused):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                proposal,
                observation=TransitionObservation(now=now),
            )
        child = dis.prepare_reconciliation(parent, "read", "f" * 64, now)
        db.add_dispatch(child)
        observation = TransitionObservation(now=now, reconciliation_key=dispatch_key(child))
        with pytest.raises(Refused):
            db.transition("dispatch", parent.approval_id, parent.version, proposal, observation=observation)
        db.transition(
            "dispatch",
            dispatch_key(child),
            0,
            lambda old: dis.finish_reconciliation(
                old, evidence=f.DIGEST, cleanup_proven=True, effect="unknown"
            ),
            observation=f.reconciliation_observation(child, effect="unknown"),
        )
        with pytest.raises(Refused):
            db.transition("dispatch", parent.approval_id, parent.version, proposal, observation=observation)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old: dis.reconcile_lookup(
                old, elapsed_seconds=1, effect="unknown", evidence=f.DIGEST, cleanup_proven=False
            ),
            observation=observation,
        )
        assert parent.reconciliation_attempts == (child.attempt_id,)
        assert parent.reconciliation_lookups == 1 and not parent.settled
        with pytest.raises(Refused):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.reconcile_lookup(
                    old, elapsed_seconds=5, effect="unknown", evidence=f.DIGEST, cleanup_proven=False
                ),
                observation=TransitionObservation(
                    now=dis.Clock(clock.boot_id, clock.mono_ns + 5 * 10**9, clock.utc_ns + 5 * 10**9),
                    reconciliation_key=dispatch_key(child),
                ),
            )


def test_parent_backoff_uses_observed_clock_not_proposal_elapsed(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = ready_parent(db)
        parent = db.transition("dispatch", parent.approval_id, parent.version, dis.uncertain)
        child = dis.prepare_reconciliation(parent, "read", "f" * 64, clock)
        db.add_dispatch(child)
        db.transition(
            "dispatch",
            dispatch_key(child),
            0,
            lambda old: dis.finish_reconciliation(old, evidence=f.DIGEST, cleanup_proven=True),
            observation=f.reconciliation_observation(child, effect="unknown"),
        )
        with pytest.raises(Refused, match="not-due"):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.reconcile_lookup(
                    old, elapsed_seconds=1, effect="unknown", evidence=f.DIGEST, cleanup_proven=False
                ),
                observation=TransitionObservation(now=clock, reconciliation_key=dispatch_key(child)),
            )


@pytest.mark.parametrize("field", ["authenticated_current", "exact_repair_scope", "native_readback"])
def test_journal_recovery_requires_supplied_current_scope_observation(tmp_path, field):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r2.held_parent(db)
        good = dis.DispatchRecovery(
            parent.approval_id,
            parent.revision,
            parent.digest,
            parent.target,
            parent.attempt_id,
            parent.version,
            f.DIGEST,
            "marmot-fixture",
            True,
            True,
            True,
            True,
            True,
        )

        def proposal(old):
            return dis.settle_dispatch(old, good)

        with pytest.raises(Refused):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                proposal,
                observation=TransitionObservation(
                    dispatch_recovery=msgspec.structs.replace(good, **{field: False})
                ),
            )
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 3
        done = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            proposal,
            observation=TransitionObservation(dispatch_recovery=good),
        )
        assert done.settled and not done.claim_consumed


def test_failed_durable_dispatch_completes_request_and_frees_capacity(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r2.held_parent(db)
        request = db.get(
            "request",
            request_key(f.initial()),
        )
        recovery = dis.DispatchRecovery(
            parent.approval_id,
            parent.revision,
            parent.digest,
            parent.target,
            parent.attempt_id,
            parent.version,
            f.DIGEST,
            "marmot-fixture",
            True,
            True,
            True,
            True,
            True,
        )
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old: dis.settle_dispatch(old, recovery),
            observation=TransitionObservation(dispatch_recovery=recovery),
        )

        done = db.transition(
            "request",
            request_key(request),
            request.version,
            lambda old: settle_request(old, "completed", dispatch=parent, **r1.cleanup(old)),
        )
        assert done.phase == "completed"
        assert db.db.execute("SELECT outstanding FROM requests").fetchone()[0] == 0
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
        with pytest.raises(Refused):
            db.add_dispatch(msgspec.structs.replace(parent, version=0, state="preparing"))


def test_journal_cancel_after_deny_refuses_and_preserves_atomic_denial_route(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key, request = r2.persist_request(db, until="ask-pinned")
        decision = f.persist_decision(db, "deny")
        with pytest.raises(Refused):
            db.transition(
                "request",
                key,
                request.version,
                lambda old: settle_request(old, "cancelled", **r1.cleanup(old)),
            )
        assert db.denied(request.denial_identity, request.origin.session_key)
        done = db.transition(
            "request",
            key,
            request.version,
            lambda old: settle_request(old, "denied", decision=decision, **r1.cleanup(old)),
        )
        assert done.phase == "denied" and db.denied(request.denial_identity, request.origin.session_key)
        with pytest.raises(Refused, match="same-session-denied"):
            db.add_request(f.initial(2))


def test_explicit_observation_retained_atomically_with_transition(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key = db.add_request(f.initial())
        value = db.transition(
            "request",
            key,
            0,
            f.issue_allocation,
            observation=TransitionObservation(now=f.Clock("allocation", 1, 1)),
        )
        observation = TransitionObservation(
            allocation_elapsed_seconds=0, now=f.Clock("allocation", 1 + (0) * 10**9, 1 + (0) * 10**9)
        )
        with pytest.raises(Refused):
            db.transition(
                "request",
                key,
                value.version,
                lambda old: f.observe_allocation(old, ("btq-ab123",), lookup_ok=True, elapsed_seconds=1),
                observation=observation,
            )
        assert (
            db.db.execute("SELECT count(*) FROM events WHERE kind='request-observation'").fetchone()[0] == 1
        )
        observation = TransitionObservation(
            allocation_elapsed_seconds=1, now=f.Clock("allocation", 1 + (1) * 10**9, 1 + (1) * 10**9)
        )
        result = db.transition(
            "request",
            key,
            value.version,
            lambda old: f.observe_allocation(old, ("btq-ab123",), lookup_ok=True, elapsed_seconds=1),
            observation=observation,
        )
        row = db.db.execute(
            "SELECT version,data FROM events WHERE kind='request-observation' AND key=? "
            "ORDER BY version DESC LIMIT 1",
            (key,),
        ).fetchone()
        assert row[0] == result.version
        assert msgspec.json.decode(row[1], type=TransitionObservation) == observation


def test_exact_decision_duplicate_remains_idempotent_after_request_settlement(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        decision = f.persist_decision(db, "deny")
        value = db.get("request", request_key(f.initial()))
        db.transition(
            "request",
            request_key(f.initial()),
            value.version,
            lambda old: settle_request(old, "denied", decision=decision, **r1.cleanup(old)),
        )
        events = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
        assert db.add_decision(f.begun("deny")) == "duplicate"
        assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == events
