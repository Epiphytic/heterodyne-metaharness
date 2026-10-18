"""Seventh review: synthetic private-clock and bounded reconciliation regressions."""

from contextlib import closing

import msgspec
import pytest
import test_actions_p1 as f
import test_actions_p1_r1 as r1
import test_actions_p1_r3 as r3
from actions_journal_fixture import FixtureJournal

from heterodyne.actions import dispatch as dis
from heterodyne.actions import requests as req
from heterodyne.actions.transitions import TransitionObservation


def abandonment(value, now):
    return req.AbandonmentRecovery(
        value.key, value.origin, value.version, (), f.DIGEST, "marmot-fixture", True, True, True, True, now
    )


def test_forged_issuance_clock_cannot_create_discontinuity(tmp_path):
    actual = dis.Clock("real", 1, 1)
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=lambda: actual)) as db:
        key = db.add_request(f.initial())
        value = db.transition(
            "request",
            key,
            0,
            req.issue_allocation,
            observation=TransitionObservation(now=dis.Clock("fake", 1, 1)),
        )
        row = db.db.execute(
            "SELECT data FROM events WHERE kind='request-clock' AND key=? AND version=?", (key, value.version)
        ).fetchone()
        assert msgspec.json.decode(row[0], type=dis.Clock) == actual
        row = db.db.execute(
            "SELECT data FROM events WHERE kind='request-observation' AND key=? AND version=?",
            (key, value.version),
        ).fetchone()
        assert msgspec.json.decode(row[0], type=TransitionObservation).now == actual


@pytest.mark.parametrize("legacy", [False, True])
def test_only_journal_proven_issuance_allows_reboot_recovery(tmp_path, legacy):
    now = dis.Clock("old", 1, 1)
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=lambda: now)) as db:
        key = db.add_request(f.initial())
        value = db.transition(
            "request", key, 0, req.issue_allocation, observation=TransitionObservation(now=now)
        )
        if legacy:
            db.db.execute("DELETE FROM events WHERE kind='request-clock'")
        now = dis.Clock("new", 1, 100)
        db.clock = lambda: now
        recovery = abandonment(value, now)

        def settle(old):
            return req.settle_request(old, "reconciled-abandoned", recovery=recovery, **r1.cleanup(old))

        obs = f.cleanup_observation(value, abandonment_recovery=recovery)
        if legacy:
            with pytest.raises(req.Refused, match="provenance"):
                db.transition("request", key, value.version, settle, observation=obs)
        else:
            assert db.transition("request", key, value.version, settle, observation=obs).cleanup_confirmed


def test_extra_recovery_cannot_forge_terminal_audit():
    value = msgspec.structs.replace(f.filed(), phase="landed")
    recovery = abandonment(value, dis.Clock("fake", 1, 1))
    with pytest.raises(req.Refused, match="recovery"):
        req.settle_request(value, "cancelled", recovery=recovery, **r1.cleanup(value))


def test_cancellation_participates_in_exclusive_recovery_guard(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key = db.add_request(f.initial())
        value = db.get("request", key)
        with pytest.raises(req.Refused, match="multiple-recovery"):
            db.transition(
                "request",
                key,
                0,
                lambda old: old,
                observation=TransitionObservation(
                    abandonment_recovery=abandonment(value, dis.Clock("b", 1, 1)),
                    cancellation_recovery=f.withdrawal(value),
                ),
            )


def test_definitive_absence_cannot_be_overwritten_by_later_success(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
    failed = dis.report(
        parent,
        attempt_id=parent.attempt_id,
        claim_hash=parent.claim_hash or "",
        digest=parent.digest,
        target=parent.target,
        effect="absent",
        evidence=f.DIGEST,
        cleanup_proven=True,
    )
    with pytest.raises(req.Refused, match="conflict"):
        dis.reconcile_lookup(
            failed, elapsed_seconds=1, effect="succeeded", evidence="e" * 64, cleanup_proven=True
        )


@pytest.mark.parametrize("mode", ["failed", "mixed", "live"])
def test_three_failed_preparations_allow_only_scoped_operator_settlement(tmp_path, mode):
    from heterodyne.actions.journal import dispatch_key

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        children = []
        retired = parent
        for n in range(3):
            child = dis.prepare_reconciliation(
                retired, f"reader-{n}", str(n + 1) * 64, f.reader_clock(db, retired)
            )
            db.add_dispatch(child)
            if mode == "mixed" and n == 2:
                child = db.transition(
                    "dispatch",
                    dispatch_key(child),
                    child.version,
                    lambda old: dis.finish_reconciliation(old, evidence=f.DIGEST, cleanup_proven=True),
                    observation=f.reconciliation_observation(child),
                )
                now = dis.Clock("boot", 10**9 + 1, 10**9 + 1)
                parent = db.transition(
                    "dispatch",
                    parent.approval_id,
                    parent.version,
                    lambda old: dis.reconcile_lookup(
                        old, elapsed_seconds=1, effect="unknown", evidence=f.DIGEST, cleanup_proven=False
                    ),
                    observation=TransitionObservation(
                        dispatch_operation="lookup", now=now, reconciliation_key=dispatch_key(child)
                    ),
                )
            else:
                child = db.transition(
                    "dispatch",
                    dispatch_key(child),
                    child.version,
                    lambda old: dis.preparation_failed(old, launcher_exited=True, no_children=True),
                    observation=f.launcher_observation(child),
                )
            if mode != "mixed" or n != 2:
                with pytest.raises(req.Refused):
                    dis.restart_preparation(child, "a" * 64, clock)
            if not (mode == "live" and n == 2) and not (mode == "mixed" and n == 2):
                recovery = dis.DispatchRecovery(
                    child.approval_id,
                    child.revision,
                    child.digest,
                    child.target,
                    child.attempt_id,
                    child.version,
                    f.DIGEST,
                    "marmot-fixture",
                    authenticated_current=True,
                    exact_repair_scope=True,
                    native_readback=True,
                    effect_definitively_absent=True,
                    cleanup_proven=True,
                )
                child = db.transition(
                    "dispatch",
                    dispatch_key(child),
                    child.version,
                    lambda old, recovery=recovery: dis.settle_dispatch(old, recovery),
                    observation=TransitionObservation(
                        dispatch_operation="settle", dispatch_recovery=recovery
                    ),
                )
            children.append(child)
            retired = msgspec.structs.replace(
                parent, retired_startups=tuple({*retired.retired_startups, child.startup_hash})
            )
        assert parent.reconciliation_lookups == int(mode == "mixed")
        attempts = tuple(child.attempt_id for child in children)
        recovery = dis.DispatchRecovery(
            parent.approval_id,
            parent.revision,
            parent.digest,
            parent.target,
            parent.attempt_id,
            parent.version,
            f.DIGEST,
            "marmot-parent",
            authenticated_current=True,
            exact_repair_scope=True,
            native_readback=True,
            effect_definitively_absent=True,
            cleanup_proven=True,
            completed_reconciliation_attempts=attempts,
            observed_reconciliations=tuple(
                dis.reconciliation_disposition(parent, child) for child in children
            ),
        )

        def settle(old):
            return dis.settle_dispatch(old, recovery)

        if mode == "live":
            with pytest.raises(req.Refused, match="reconciliation"):
                db.transition(
                    "dispatch",
                    parent.approval_id,
                    parent.version,
                    settle,
                    observation=TransitionObservation(
                        dispatch_operation="settle", dispatch_recovery=recovery
                    ),
                )
        else:
            bad = msgspec.structs.replace(recovery, completed_reconciliation_attempts=("fake",) * 3)
            with pytest.raises(req.Refused, match="reconciliation"):
                db.transition(
                    "dispatch",
                    parent.approval_id,
                    parent.version,
                    lambda old: dis.settle_dispatch(old, bad),
                    observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=bad),
                )
            done = db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                settle,
                observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
            )
            assert done.settled and done.reconciliation_lookups == int(mode == "mixed")
            assert done.recovery_operator_event == "marmot-parent"
            assert db.db.execute("SELECT count(*) FROM resources").fetchone() == (0,)


def test_dispatch_prepare_and_lookup_cannot_admit_forged_anchor(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
        actual = dis.Clock("real", 1, 1)
        db.clock = lambda: actual
        child = msgspec.structs.replace(parent, version=0, state="preparing", started=dis.Clock("fake", 1, 1))
        with pytest.raises(req.Refused, match="already-exists"):
            db.add_dispatch(child)
        before = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
        assert (
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: old,
                observation=TransitionObservation(dispatch_operation="lookup", now=dis.Clock("fake", 1, 1)),
            )
            == parent
        )
        assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == before
        assert db.get("dispatch", parent.approval_id).started.boot_id == "boot"


def test_settled_preparation_failure_does_not_block_next_bounded_reader(tmp_path):
    from heterodyne.actions.journal import dispatch_key

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        child = dis.prepare_reconciliation(parent, "failed-reader", "1" * 64, f.reader_clock(db, parent))
        db.add_dispatch(child)
        child = db.transition(
            "dispatch",
            dispatch_key(child),
            child.version,
            lambda old: dis.preparation_failed(old, launcher_exited=True, no_children=True),
            observation=f.launcher_observation(child),
        )
        recovery = dis.DispatchRecovery(
            child.approval_id,
            child.revision,
            child.digest,
            child.target,
            child.attempt_id,
            child.version,
            f.DIGEST,
            "marmot-fixture",
            authenticated_current=True,
            exact_repair_scope=True,
            native_readback=True,
            effect_definitively_absent=True,
            cleanup_proven=True,
        )
        child = db.transition(
            "dispatch",
            dispatch_key(child),
            child.version,
            lambda old: dis.settle_dispatch(old, recovery),
            observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
        )
        retained = msgspec.structs.replace(
            parent,
            retired_startups=(*parent.retired_startups, child.startup_hash),
        )
        second = dis.prepare_reconciliation(retained, "second-reader", "2" * 64, f.reader_clock(db, retained))
        db.add_dispatch(second)
        assert db.get("dispatch", dispatch_key(second)) == second
        assert db.get("dispatch", parent.approval_id).reconciliation_lookups == 0
        assert not db.get("dispatch", parent.approval_id).settled


@pytest.mark.parametrize("operation", ["issuance", "initial-dispatch"])
def test_new_effect_work_cannot_spend_terminal_reserve(tmp_path, monkeypatch, operation):
    import heterodyne.actions.journal as journal

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key = ""
        if operation == "issuance":
            key = db.add_request(f.initial())
        else:
            f.persist_decision(db)
        before = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
        monkeypatch.setattr(journal, "MAX_EVENTS", before + db._reserved())
        with pytest.raises(req.Refused, match="capacity"):
            if operation == "issuance":
                db.transition(
                    "request",
                    key,
                    0,
                    req.issue_allocation,
                    observation=TransitionObservation(now=dis.Clock("boot", 1, 1)),
                )
            else:
                db.add_dispatch(
                    dis.prepare(
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
                )
        assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == before
        assert db.db.execute("SELECT count(*) FROM dispatches").fetchone()[0] == 0
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
