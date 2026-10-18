"""R8 regression probes: private fixture journals, no real adapters or consent."""

from contextlib import closing

import msgspec
import pytest
import test_actions_p1 as f
import test_actions_p1_r3 as r3

from heterodyne.actions import dispatch as dis
from heterodyne.actions import requests as req
from heterodyne.actions.journal import FixtureJournal, dispatch_key
from heterodyne.actions.transitions import TransitionObservation


def later(clock, seconds):
    return dis.Clock(clock.boot_id, clock.mono_ns + seconds * 10**9, clock.utc_ns + seconds * 10**9)


def uncertain(db, consumed=True):
    parent, clock = r3.ready_parent(db, consumed=consumed)
    return db.transition(
        "dispatch",
        parent.approval_id,
        parent.version,
        dis.uncertain,
        observation=TransitionObservation(dispatch_operation="uncertain"),
    ), clock


def test_real_advancing_sampler_is_injected_once_without_caller_prediction(tmp_path):
    from heterodyne.actions.journal import ObservedReducer

    calls = []

    def clock():
        calls.append(1)
        return dis.Clock("boot", len(calls) * 10**9, len(calls) * 10**9)

    with closing(FixtureJournal.create(tmp_path / "db", clock=clock)) as db:
        key = db.add_request(f.initial())
        value = db.transition("request", key, 0, ObservedReducer(lambda old, obs: req.issue_allocation(old)))
        assert len(calls) == 1
        data = db.db.execute(
            "SELECT data FROM events WHERE kind='request-observation' AND key=?", (key,)
        ).fetchone()[0]
        assert msgspec.json.decode(data, type=TransitionObservation).now == dis.Clock("boot", 10**9, 10**9)
        assert value.phase == "allocation-unknown"


def test_preparation_failure_needs_independent_launcher_cleanup(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "db")) as db:
        parent, clock = uncertain(db)
        child = dis.prepare_reconciliation(parent, "read", "f" * 64, later(clock, 1))
        db.add_dispatch(child)
        with pytest.raises(req.Refused, match="launcher.*observation"):
            db.transition(
                "dispatch",
                dispatch_key(child),
                child.version,
                lambda old: dis.preparation_failed(old, launcher_exited=True, no_children=True),
                observation=TransitionObservation(dispatch_operation="preparation-failed"),
            )
        assert db.get("dispatch", dispatch_key(child)) == child


def test_launch_backoff_applies_even_if_preparation_has_not_started(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "db")) as db:
        parent, clock = uncertain(db)
        child = dis.prepare_reconciliation(parent, "too-soon", "f" * 64, clock)
        with pytest.raises(req.Refused, match="not-due"):
            db.add_dispatch(child)
        assert db.db.execute("SELECT count(*) FROM dispatches").fetchone() == (1,)


@pytest.mark.parametrize("consumed", [True, False])
def test_conflicting_child_has_exact_attested_exit_without_three_fake_reads(tmp_path, consumed):
    with closing(FixtureJournal.create(tmp_path / "db")) as db:
        parent, clock = uncertain(db, consumed)
        if consumed:
            parent = db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.report(
                    old,
                    attempt_id=old.attempt_id,
                    claim_hash=old.claim_hash or "",
                    digest=old.digest,
                    target=old.target,
                    effect="absent",
                    evidence=f.DIGEST,
                    cleanup_proven=True,
                ),
                observation=f.report_observation(parent, effect="absent", evidence=f.DIGEST, cleanup=True),
            )
        child = dis.prepare_reconciliation(parent, "conflicting-reader", "f" * 64, later(clock, 1))
        db.add_dispatch(child)
        child = db.transition(
            "dispatch",
            dispatch_key(child),
            child.version,
            lambda old: dis.finish_reconciliation(
                old, evidence=f.DIGEST, cleanup_proven=True, effect="succeeded", effect_cleanup_proven=True
            ),
            observation=f.reconciliation_observation(child, effect="succeeded", effect_cleanup=True),
        )
        from heterodyne.actions.dispatch import ReconciliationDisposition

        facts = ReconciliationDisposition(
            child.attempt_id,
            child.version,
            child.reconciliation_effect,
            child.evidence,
            child.cleanup_proven,
            child.settled,
            False,
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
            authenticated_current=True,
            exact_repair_scope=True,
            native_readback=True,
            effect_definitively_absent=True,
            cleanup_proven=True,
            observed_reconciliations=(facts,),
        )
        for bad in (
            msgspec.structs.replace(recovery, observed_reconciliations=()),
            msgspec.structs.replace(
                recovery, observed_reconciliations=(msgspec.structs.replace(facts, evidence="d" * 64),)
            ),
        ):
            with pytest.raises(req.Refused):
                db.transition(
                    "dispatch",
                    parent.approval_id,
                    parent.version,
                    lambda old, observed=bad: dis.settle_dispatch(old, observed),
                    observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=bad),
                )
        done = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old, observed=recovery: dis.settle_dispatch(old, observed),
            observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
        )
        assert done.settled and done.state == "failed" and done.reconciliation_lookups == 0
        assert db.db.execute("SELECT count(*) FROM resources").fetchone() == (0,)
        assert db.get("dispatch", dispatch_key(child)).reconciliation_effect == "succeeded"
        with pytest.raises(req.Refused):
            db.add_dispatch(dis.prepare_reconciliation(parent, "again", "a" * 64, later(clock, 99)))


def test_discontinuous_recovery_reuses_one_actual_transaction_sample(tmp_path):
    from heterodyne.actions.journal import ObservedReducer

    now = dis.Clock("boot", 1, 1)
    with closing(FixtureJournal.create(tmp_path / "db")) as db:
        parent, _ = sampled_uncertain(db, lambda: now)
        calls = []

        def advancing():
            calls.append(1)
            return dis.Clock("new-boot", len(calls), 1000 + len(calls))

        db.clock = advancing  # Explicit trusted fixture port replacement, not an authentication capability.
        recovery = dis.DispatchRecovery(
            parent.approval_id,
            parent.revision,
            parent.digest,
            parent.target,
            parent.attempt_id,
            parent.version,
            f.DIGEST,
            "marmot-fixture",
            authenticated_current=True,
            exact_repair_scope=True,
            native_readback=True,
            effect_definitively_absent=True,
            cleanup_proven=True,
            clock_discontinuity=True,
            clock_anchor=parent.started,
        )

        def recover(old, obs):
            assert obs.dispatch_recovery is not None
            return dis.settle_dispatch(old, obs.dispatch_recovery)

        done = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            ObservedReducer(recover),
            observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
        )
        assert done.settled and len(calls) == 1
        assert db.db.execute("SELECT count(*) FROM resources").fetchone() == (0,)


@pytest.mark.parametrize(
    "field,value",
    [("attempt_id", "wrong"), ("expected_version", 999), ("launcher_exited", False), ("no_children", False)],
)
def test_cleanup_attestation_is_bound_and_cannot_hide_live_launcher(tmp_path, field, value):
    with closing(FixtureJournal.create(tmp_path / "db")) as db:
        parent, clock = uncertain(db)
        child = dis.prepare_reconciliation(parent, "read", "f" * 64, later(clock, 1))
        db.add_dispatch(child)
        observation = f.launcher_observation(child)
        assert observation.launcher_readback is not None
        observation = msgspec.structs.replace(
            observation,
            launcher_readback=msgspec.structs.replace(observation.launcher_readback, **{field: value}),
        )
        with pytest.raises(req.Refused):
            db.transition(
                "dispatch",
                dispatch_key(child),
                0,
                lambda old: dis.preparation_failed(old, launcher_exited=True, no_children=True),
                observation=observation,
            )
        assert not db.get("dispatch", dispatch_key(child)).cleanup_proven


@pytest.mark.parametrize("read_index,unread", [(0, False), (1, False), (2, False), (2, True)])
def test_full_three_attempt_terminal_budget_and_unapplied_read_disposition(
    tmp_path, monkeypatch, unread, read_index
):
    import test_actions_p1_r1 as r1

    import heterodyne.actions.journal as journal
    from heterodyne.actions.journal import request_key

    now = dis.Clock("boot", 1, 1)
    with closing(FixtureJournal.create(tmp_path / "db")) as db:
        parent, _ = sampled_uncertain(db, lambda: now)
        count = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
        monkeypatch.setattr(journal, "MAX_EVENTS", count + db._reserved())
        children = []
        retired = parent
        for n, spacing in enumerate((1, 5, 30)):
            premature = dis.prepare_reconciliation(retired, f"early-{n}", str(n + 1) * 64, now)
            before = db.db.execute("SELECT count(*) FROM events").fetchone()
            with pytest.raises(req.Refused, match="not-due"):
                db.add_dispatch(premature)
            assert db.db.execute("SELECT count(*) FROM events").fetchone() == before
            now = later(now, spacing)
            child = dis.prepare_reconciliation(retired, f"read-{n}", str(n + 1) * 64, now)
            db.add_dispatch(child)
            if n == read_index:
                child = db.transition(
                    "dispatch",
                    dispatch_key(child),
                    child.version,
                    lambda old: dis.finish_reconciliation(old, evidence=f.DIGEST, cleanup_proven=True),
                    observation=msgspec.structs.replace(f.reconciliation_observation(child), now=now),
                )
                if not unread:
                    parent = db.transition(
                        "dispatch",
                        parent.approval_id,
                        parent.version,
                        lambda old: dis.reconcile_lookup(
                            old,
                            elapsed_seconds=1,
                            effect="unknown",
                            evidence=f.DIGEST,
                            cleanup_proven=False,
                        ),
                        observation=TransitionObservation(
                            dispatch_operation="lookup",
                            now=now,
                            reconciliation_key=dispatch_key(child),
                        ),
                    )
            else:
                child = db.transition(
                    "dispatch",
                    dispatch_key(child),
                    child.version,
                    lambda old: dis.preparation_failed(old, launcher_exited=True, no_children=True),
                    observation=msgspec.structs.replace(f.launcher_observation(child), now=now),
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
                    lambda old, observed=recovery: dis.settle_dispatch(old, observed),
                    observation=TransitionObservation(
                        dispatch_operation="settle", dispatch_recovery=recovery, now=now
                    ),
                )
            children.append(child)
            retired = msgspec.structs.replace(
                parent, retired_startups=tuple(item.startup_hash for item in children)
            )
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
        )
        with pytest.raises(req.Refused, match="reconciliation|recovery"):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old, observed=recovery: dis.settle_dispatch(old, observed),
                observation=TransitionObservation(
                    dispatch_operation="settle", dispatch_recovery=recovery, now=now
                ),
            )
        recovery = msgspec.structs.replace(
            recovery,
            observed_reconciliations=tuple(
                dis.reconciliation_disposition(parent, child) for child in children
            ),
        )
        done = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old, observed=recovery: dis.settle_dispatch(old, observed),
            observation=TransitionObservation(
                dispatch_operation="settle", dispatch_recovery=recovery, now=now
            ),
        )
        request = db._request_for_approval(parent.approval_id)
        final = db.transition(
            "request",
            request_key(request),
            request.version,
            lambda old: req.settle_request(old, "completed", dispatch=done, **r1.cleanup(old)),
            observation=msgspec.structs.replace(f.cleanup_observation(request), now=now),
        )
        assert final.cleanup_confirmed
        assert db.db.execute("SELECT count(*) FROM resources").fetchone() == (0,)
        used = db._reserve_used(request_key(final))
        assert used <= journal.TERMINAL_RESERVE == 96
        assert used <= 47  # Includes an applied read and its retained sampled observations.


def test_three_arbitrary_strings_are_not_pure_settlement_proof(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "db")) as db:
        parent, _ = uncertain(db)
    recovery = dis.DispatchRecovery(
        parent.approval_id,
        parent.revision,
        parent.digest,
        parent.target,
        parent.attempt_id,
        parent.version,
        f.DIGEST,
        "marmot-fixture",
        authenticated_current=True,
        exact_repair_scope=True,
        native_readback=True,
        effect_definitively_absent=True,
        cleanup_proven=True,
        completed_reconciliation_attempts=("a", "b", "c"),
    )
    with pytest.raises(req.Refused):
        dis.settle_dispatch(parent, recovery)


def sampled_uncertain(db, clock):
    from heterodyne.actions.journal import ObservedReducer

    f.persist_decision(db)
    db.clock = clock
    now = clock()
    value = dis.prepare(
        "btq-ab123", 1, f.DIGEST, f.DIGEST, "initial", "resource", "c" * 64, now, fake_finalized_approval=True
    )
    db.add_dispatch(value)
    value = db.get("dispatch", value.approval_id)
    for op, fn in (
        (
            "ready",
            lambda old, obs: dis.ready(old, "e" * 64, obs.now, fresh_gates=True, startup_readback=True),
        ),
        ("consume-claim", lambda old, obs: dis.consume_claim(old, "e" * 64, obs.now, fresh_gates=True)),
    ):
        value = db.transition(
            "dispatch",
            value.approval_id,
            value.version,
            ObservedReducer(fn),
            observation=TransitionObservation(
                dispatch_operation="ready" if op == "ready" else "consume-claim"
            ),
        )
    return db.transition(
        "dispatch",
        value.approval_id,
        value.version,
        dis.uncertain,
        observation=TransitionObservation(dispatch_operation="uncertain"),
    ), now
