"""Fourth-review regressions: independent synthetic observations, no live adapters."""

from contextlib import closing

import msgspec
import pytest
import test_actions_p1 as f
import test_actions_p1_r2 as r2
import test_actions_p1_r3 as r3
from actions_journal_fixture import FixtureJournal

from heterodyne.actions import decisions as dec
from heterodyne.actions import dispatch as dis
from heterodyne.actions.journal import dispatch_key
from heterodyne.actions.requests import Refused, groom
from heterodyne.actions.transitions import TransitionObservation


def test_pending_denial_blocks_groom(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key, request = r2.persist_request(db, until="ask-pinned")
        decision = dec.begin_decision(
            request, f.event("deny"), expected_purpose="execution", current_digest=f.DIGEST
        )
        db.add_decision(decision)
        with pytest.raises(Refused, match="decision"):
            db.transition(
                "request",
                key,
                request.version,
                lambda old: groom(old, old.intent, old.resolved, old.requested_source, origin=old.origin),
            )


def test_decision_issue_requires_separate_observation(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        f.persist_filed_request(db)
        decision = f.begun()
        db.add_decision(decision)
        with pytest.raises(Refused, match="observation"):
            db.transition(
                "decision",
                decision.approval_id,
                0,
                lambda old: dec.issue_decision(old, fresh_authority=True, current_digest=f.DIGEST),
            )


def test_readonly_child_cannot_self_attest_result(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        child = dis.prepare_reconciliation(parent, "read-child", "f" * 64, f.reader_clock(db, parent))
        db.add_dispatch(child)
        with pytest.raises(Refused, match="observation"):
            db.transition(
                "dispatch",
                dispatch_key(child),
                0,
                lambda old: dis.finish_reconciliation(
                    old, evidence=f.DIGEST, cleanup_proven=True, effect="succeeded"
                ),
                observation=TransitionObservation(dispatch_operation="finish-reconciliation"),
            )


def test_held_parent_cannot_allocate_readonly_child(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = r2.held_parent(db)
        with pytest.raises(Refused):
            child = dis.prepare_reconciliation(parent, "read-child", "f" * 64, f.reader_clock(db, parent))
            db.add_dispatch(child)


@pytest.mark.parametrize("now", [dis.Clock("new-boot", 1, 100), dis.Clock("boot", 10**9, 10**12)])
def test_discontinuous_clock_has_exact_authenticated_no_replay_exit(tmp_path, now):
    samples = iter(
        [
            dis.Clock("allocation", 1, 1),
            dis.Clock("allocation", 10**9 + 1, 10**9 + 1),
            *([dis.Clock("boot", 1, 1)] * 4),
        ]
    )
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=lambda: next(samples))) as db:
        parent, _ = r3.ready_parent(db)
        db.clock = lambda: now
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        recovery = dis.DispatchRecovery(
            parent.approval_id,
            parent.revision,
            parent.digest,
            parent.target,
            parent.attempt_id,
            parent.version,
            f.DIGEST,
            "marmot-recovery",
            authenticated_current=True,
            exact_repair_scope=True,
            native_readback=True,
            effect_definitively_absent=True,
            cleanup_proven=True,
            clock_discontinuity=True,
            observed_now=now,
            clock_anchor=parent.started,
        )
        for bad in (
            msgspec.structs.replace(recovery, authenticated_current=False),
            msgspec.structs.replace(recovery, expected_version=parent.version + 1),
            msgspec.structs.replace(recovery, observed_now=parent.started),
        ):
            with pytest.raises(Refused):
                db.transition(
                    "dispatch",
                    parent.approval_id,
                    parent.version,
                    lambda old, bad=bad: dis.settle_dispatch(old, bad),
                    observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=bad),
                )
        done = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old: dis.settle_dispatch(old, recovery),
            observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
        )
        assert done.settled and done.state == "failed" and done.claim_consumed
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
        with pytest.raises(Refused):
            dis.restart_preparation(done, "f" * 64, now)


def test_allocation_ignores_unanchored_integer_and_caller_start(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key = db.add_request(f.initial())
        start = dis.Clock("allocation", 100, 100)
        value = db.transition(
            "request", key, 0, f.issue_allocation, observation=TransitionObservation(now=start)
        )
        forged = TransitionObservation(now=start, allocation_started=dis.Clock("allocation", 0, 0))
        with pytest.raises(Refused, match="not-due"):
            db.transition(
                "request",
                key,
                value.version,
                lambda old: f.observe_allocation(old, (), lookup_ok=False, elapsed_seconds=30),
                observation=forged,
            )
        for elapsed in (1, 5, 30):
            now = dis.Clock(
                start.boot_id,
                start.mono_ns + {1: 1, 5: 6, 30: 36}[elapsed] * 10**9,
                start.utc_ns + {1: 1, 5: 6, 30: 36}[elapsed] * 10**9,
            )
            value = db.transition(
                "request",
                key,
                value.version,
                lambda old, elapsed=elapsed: f.observe_allocation(
                    old, (), lookup_ok=False, elapsed_seconds=elapsed
                ),
                observation=TransitionObservation(now=now),
            )
        assert value.allocation_lookups == 3


@pytest.mark.parametrize("invalidated", [False, True])
def test_filed_withdrawal_requires_independent_native_cleanup(tmp_path, invalidated):
    import test_actions_p1_r1 as r1

    from heterodyne.actions.requests import CancellationRecovery, settle_request

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key, request = r2.persist_request(db)
        decision = None
        if invalidated:
            decision = f.begun()
            db.add_decision(decision)
            decision = db.transition(
                "decision",
                decision.approval_id,
                decision.version,
                lambda old: dec.invalidate_decision(old, reason="authority-revoked", current_check=True),
                observation=f.decision_observation(decision, authority_current=False),
            )
        recovery = CancellationRecovery(
            request.key,
            request.origin,
            request.version,
            request.shell_ids,
            f.DIGEST,
            "marmot-withdrawal",
            authenticated_current=True,
            exact_repair_scope=True,
            native_withdrawal_readback=True,
            cleanup_proven=True,
        )

        def proposal(old):
            return settle_request(old, "cancelled", decision=decision, recovery=recovery, **r1.cleanup(old))

        with pytest.raises(Refused):
            db.transition("request", key, request.version, proposal)
        done = db.transition(
            "request",
            key,
            request.version,
            proposal,
            observation=f.cleanup_observation(request, cancellation_recovery=recovery),
        )
        assert done.phase == "cancelled" and done.cleanup_confirmed
        assert done.recovery_operator_event == recovery.operator_event
        assert db.db.execute("SELECT outstanding FROM requests WHERE key=?", (key,)).fetchone() == (0,)


def test_request_completion_reports_absent_instead_of_implicit_success():
    import test_actions_p1_r1 as r1

    from heterodyne.actions.requests import settle_request

    request = f.filed()
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
    value = msgspec.structs.replace(
        value, state="failed", settled=True, cleanup_proven=True, recovery_operator_event="marmot-recovery"
    )
    done = settle_request(request, "completed", dispatch=value, **r1.cleanup(request))
    assert done.phase == "completed" and done.completion_effect == "absent"


def test_absent_parent_evidence_survives_unknown_and_absent_reads():
    parent = dis.prepare(
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
    parent = msgspec.structs.replace(
        parent, state="failed", claim_consumed=True, evidence="a" * 64, cleanup_proven=True
    )
    observations: tuple[tuple[int, f.Effect], ...] = ((1, "unknown"), (5, "absent"))
    for elapsed, effect in observations:
        parent = dis.reconcile_lookup(
            parent, elapsed_seconds=elapsed, effect=effect, evidence="d" * 64, cleanup_proven=True
        )
        assert parent.state == "failed" and parent.evidence == "a" * 64 and not parent.settled


def test_current_durable_revision_checked_even_with_fresh_observation(tmp_path):
    from heterodyne.actions.codec import encode_record
    from heterodyne.actions.journal import request_key

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        request = f.persist_filed_request(db)
        decision = f.begun()
        db.add_decision(decision)
        # Independent stale-state integrity probe; not a supported request mutation API.
        changed = msgspec.structs.replace(request, request_revision=2, context_digest="a" * 64)
        db.db.execute(
            "UPDATE requests SET data=? WHERE key=?", (encode_record(changed), request_key(request))
        )
        with pytest.raises(Refused, match="current-request"):
            db.transition(
                "decision",
                decision.approval_id,
                decision.version,
                lambda old: dec.issue_decision(old, fresh_authority=True, current_digest=f.DIGEST),
                observation=f.decision_observation(decision),
            )


def test_committed_deny_records_history_before_request_settlement(tmp_path):
    import test_actions_p1_r1 as r1

    from heterodyne.actions.journal import request_key
    from heterodyne.actions.requests import settle_request

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        decision = f.persist_decision(db, "deny")
        request = db.get("request", request_key(f.initial()))
        assert db.denied(f.required(request.denial_identity), request.origin.session_key)
        with pytest.raises(Refused, match="denied"):
            db.add_request(f.initial(2))
        done = db.transition(
            "request",
            request_key(request),
            request.version,
            lambda old: settle_request(old, "denied", decision=decision, **r1.cleanup(old)),
            observation=f.cleanup_observation(request),
        )
        assert done.phase == "denied"
        assert db.db.execute("SELECT count(*) FROM denials").fetchone() == (1,)


def test_pending_deny_cannot_be_cancelled_to_evade_history(tmp_path):
    import test_actions_p1_r1 as r1

    from heterodyne.actions.requests import settle_request

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key, request = r2.persist_request(db, until="ask-pinned")
        decision = dec.begin_decision(
            request, f.event("deny"), expected_purpose="execution", current_digest=f.DIGEST
        )
        db.add_decision(decision)
        with pytest.raises(Refused, match="decision"):
            db.transition(
                "request",
                key,
                request.version,
                lambda old: settle_request(old, "cancelled", recovery=f.withdrawal(old), **r1.cleanup(old)),
                observation=f.cleanup_observation(request, cancellation_recovery=f.withdrawal(request)),
            )


@pytest.mark.parametrize(
    "change",
    [
        {"read_only": False},
        {"invocation_finished": False},
        {"cleanup_proven": False},
        {"attempt_id": "other"},
        {"expected_version": 1},
        {"effect": "unknown"},
    ],
)
def test_readonly_child_requires_exact_separate_finished_read(tmp_path, change):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        child = dis.prepare_reconciliation(parent, "read-child", "f" * 64, f.reader_clock(db, parent))
        db.add_dispatch(child)
        independent = f.reconciliation_observation(child, effect="succeeded", effect_cleanup=True)
        bad = msgspec.structs.replace(
            independent,
            reconciliation_readback=msgspec.structs.replace(
                f.required(independent.reconciliation_readback), **change
            ),
        )

        def proposal(old):
            return dis.finish_reconciliation(
                old, evidence=f.DIGEST, cleanup_proven=True, effect="succeeded", effect_cleanup_proven=True
            )

        with pytest.raises(Refused):
            db.transition("dispatch", dispatch_key(child), 0, proposal, observation=bad)
        done = db.transition("dispatch", dispatch_key(child), 0, proposal, observation=independent)
        assert done.settled and done.reconciliation_effect == "succeeded"
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 3


def test_denial_history_commit_and_decision_rollback_together(tmp_path, monkeypatch):
    from heterodyne.actions import journal

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        f.persist_filed_request(db)
        value = f.begun("deny")
        db.add_decision(value)
        steps = (
            lambda old: dec.issue_decision(old, fresh_authority=True, current_digest=f.DIGEST),
            lambda old: dec.read_decision(old, f.event("deny"), read_ok=True),
            lambda old: dec.finalize_decision(
                old, "close", readback=f.event("deny"), current_digest=f.DIGEST, native_readback=True
            ),
            lambda old: dec.finalize_decision(
                old, "pins", readback=f.event("deny"), current_digest=f.DIGEST, native_readback=True
            ),
        )
        for reducer in steps:
            value = db.transition(
                "decision",
                value.approval_id,
                value.version,
                reducer,
                observation=f.decision_observation(value),
            )
        last = db.db.execute("SELECT max(seq) FROM events").fetchone()[0]
        # One event slot permits denial insert, then decision-event fails: both must rollback.
        monkeypatch.setattr(journal, "MAX_EVENTS", last + 1)
        with pytest.raises(Refused, match="capacity"):
            db.transition(
                "decision",
                value.approval_id,
                value.version,
                lambda old: dec.finalize_decision(
                    old, "finish", readback=f.event("deny"), current_digest=f.DIGEST, native_readback=True
                ),
                observation=f.decision_observation(value),
            )
        assert db.get("decision", value.approval_id) == value
        assert db.db.execute("SELECT count(*) FROM denials").fetchone() == (0,)
