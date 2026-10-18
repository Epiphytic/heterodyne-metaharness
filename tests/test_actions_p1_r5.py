"""Fifth-review regressions: independent synthetic facts, never live authority."""

from contextlib import closing

import msgspec
import pytest
import test_actions_p1 as f
import test_actions_p1_r1 as r1
import test_actions_p1_r2 as r2
import test_actions_p1_r3 as r3

from heterodyne.actions import dispatch as dis
from heterodyne.actions import requests as req
from heterodyne.actions.journal import FixtureJournal, request_key
from heterodyne.actions.transitions import TransitionObservation


def test_uncertain_proposal_cannot_become_its_own_success_evidence(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        with pytest.raises(req.Refused):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.report(
                    old,
                    attempt_id=old.attempt_id,
                    claim_hash=old.claim_hash or "",
                    digest=old.digest,
                    target=old.target,
                    effect="succeeded",
                    evidence=f.DIGEST,
                    cleanup_proven=True,
                ),
                observation=TransitionObservation(dispatch_operation="report"),
            )
        assert not db.get("dispatch", parent.approval_id).settled
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 3


def test_definitive_absence_cannot_be_downgraded_to_uncertain(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
    absent = dis.report(
        parent,
        attempt_id=parent.attempt_id,
        claim_hash=parent.claim_hash or "",
        digest=parent.digest,
        target=parent.target,
        effect="absent",
        evidence=f.DIGEST,
        cleanup_proven=True,
    )
    with pytest.raises(req.Refused):
        dis.uncertain(absent)


def test_terminal_candidate_is_not_native_cleanup_readback(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key, request = r2.persist_request(db, until="ask-pinned")
        with pytest.raises(req.Refused):
            db.transition(
                "request",
                key,
                request.version,
                lambda old: req.settle_request(old, "cancelled", **r1.cleanup(old)),
            )
        assert not db.get("request", key).cleanup_confirmed


def test_visible_ask_cannot_be_cancelled_without_authenticated_withdrawal():
    request = msgspec.structs.replace(f.filed(), phase="ask-pinned")
    with pytest.raises(req.Refused):
        req.settle_request(request, "cancelled", **r1.cleanup(request))


def test_clock_discontinuity_recovery_requires_journal_clock_sampler(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
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
            "marmot-repair",
            authenticated_current=True,
            exact_repair_scope=True,
            native_readback=True,
            effect_definitively_absent=True,
            cleanup_proven=True,
            clock_discontinuity=True,
            observed_now=dis.Clock("forged-boot", 1, 1),
        )
        with pytest.raises(req.Refused):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.settle_dispatch(old, recovery),
                observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
            )


def test_allocation_lookup_spacing_is_since_last_retained_observation(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key = db.add_request(f.initial())
        start = dis.Clock("boot", 1, 1)
        value = db.transition(
            "request", key, 0, req.issue_allocation, observation=TransitionObservation(now=start)
        )
        now = dis.Clock("boot", 30 * 10**9 + 1, 30 * 10**9 + 1)
        value = db.transition(
            "request",
            key,
            value.version,
            lambda old: req.observe_allocation(old, (), lookup_ok=False, elapsed_seconds=30),
            observation=TransitionObservation(now=now),
        )
        with pytest.raises(req.Refused):
            db.transition(
                "request",
                key,
                value.version,
                lambda old: req.observe_allocation(old, (), lookup_ok=False, elapsed_seconds=30),
                observation=TransitionObservation(now=now),
            )


@pytest.mark.parametrize("field", ["expected_version", "attempt_id", "digest", "claim_hash"])
def test_worker_readback_requires_exact_dispatch_binding(tmp_path, field):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
        good = f.report_observation(parent, effect="succeeded", evidence=f.DIGEST, cleanup=True)
        replacement = parent.version + 1 if field == "expected_version" else "wrong"
        assert good.dispatch_readback is not None
        bad = msgspec.structs.replace(good.dispatch_readback, **{field: replacement})
        with pytest.raises(req.Refused, match="observation"):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: report_success(old),
                observation=msgspec.structs.replace(good, dispatch_readback=bad),
            )
        assert db.get("dispatch", parent.approval_id) == parent
        done = db.transition("dispatch", parent.approval_id, parent.version, report_success, observation=good)
        assert done.settled and done.evidence == f.DIGEST
        assert db.db.execute("SELECT count(*) FROM resources").fetchone() == (0,)


def report_success(old: dis.Dispatch):
    return dis.report(
        old,
        attempt_id=old.attempt_id,
        claim_hash=old.claim_hash or "",
        digest=old.digest,
        target=old.target,
        effect="succeeded",
        evidence=f.DIGEST,
        cleanup_proven=True,
    )


@pytest.mark.parametrize("field", ["expected_version", "blockers_clear", "native_terminal", "cleaned_shells"])
def test_cleanup_has_independent_bound_native_facts(tmp_path, field):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key, request = r2.persist_request(db, until="ask-pinned")
        recovery = f.withdrawal(request)
        good = f.cleanup_observation(request, cancellation_recovery=recovery)
        replacement = {
            "expected_version": request.version + 1,
            "blockers_clear": False,
            "native_terminal": False,
            "cleaned_shells": (),
        }[field]
        assert good.cleanup_readback is not None
        bad = msgspec.structs.replace(good.cleanup_readback, **{field: replacement})

        def proposal(old):
            return req.settle_request(old, "cancelled", recovery=recovery, **r1.cleanup(old))

        with pytest.raises(req.Refused):
            db.transition(
                "request",
                key,
                request.version,
                proposal,
                observation=msgspec.structs.replace(good, cleanup_readback=bad),
            )
        assert db.get("request", key) == request
        done = db.transition("request", key, request.version, proposal, observation=good)
        assert done.cleanup_confirmed


@pytest.mark.parametrize("adopt", [False, True])
def test_reboot_during_allocation_has_exact_no_recreate_recovery(tmp_path, adopt):
    now = dis.Clock("old-boot", 1, 1)
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=lambda: now)) as db:
        key = db.add_request(f.initial())
        value = db.transition(
            "request",
            key,
            0,
            req.issue_allocation,
            observation=TransitionObservation(now=dis.Clock("old-boot", 1, 1)),
        )
        assert value.allocation_lookups == 0
        now = dis.Clock("new-boot", 1, 100)
        if adopt:
            recovery = req.AllocationRecovery(
                value.key,
                value.origin,
                "btq-ab123",
                ("btq-ab123",),
                (),
                value.version,
                f.DIGEST,
                "marmot-repair",
                True,
                True,
                True,
                True,
                now,
            )
            done = db.transition(
                "request",
                key,
                value.version,
                lambda old: req.recover_allocation(old, recovery),
                observation=TransitionObservation(allocation_recovery=recovery),
            )
            assert done.approval_id == "btq-ab123" and done.phase == "allocated"
        else:
            recovery = req.AbandonmentRecovery(
                value.key,
                value.origin,
                value.version,
                (),
                f.DIGEST,
                "marmot-repair",
                True,
                True,
                True,
                True,
                now,
            )
            done = db.transition(
                "request",
                key,
                value.version,
                lambda old: req.settle_request(
                    old, "reconciled-abandoned", recovery=recovery, **r1.cleanup(old)
                ),
                observation=f.cleanup_observation(value, abandonment_recovery=recovery),
            )
            assert done.cleanup_confirmed and done.phase == "reconciled-abandoned"
        assert done.create_issued and done.allocation_lookups == 0
        with pytest.raises(req.Refused):
            db.transition("request", key, done.version, req.issue_allocation)
        observed = db.db.execute(
            "SELECT data FROM events WHERE kind='request-observation' AND version=?", (done.version,)
        ).fetchone()
        assert msgspec.json.decode(observed[0], type=TransitionObservation).now == now


@pytest.mark.parametrize("mode", ["continuous", "mismatch", "no-sampler"])
def test_allocation_recovery_cannot_fabricate_discontinuity(tmp_path, mode):
    start = dis.Clock("old", 1, 1)
    actual = start
    with closing(
        FixtureJournal.create(tmp_path / "fixture", clock=None if mode == "no-sampler" else lambda: actual)
    ) as db:
        key = db.add_request(f.initial())
        value = db.transition(
            "request", key, 0, req.issue_allocation, observation=TransitionObservation(now=start)
        )
        actual = dis.Clock("old", 2, 2) if mode == "continuous" else dis.Clock("new", 1, 1)
        supplied = dis.Clock("forged", 1, 1) if mode == "mismatch" else actual
        recovery = req.AllocationRecovery(
            value.key,
            value.origin,
            "btq-ab123",
            ("btq-ab123",),
            (),
            value.version,
            f.DIGEST,
            "marmot-repair",
            True,
            True,
            True,
            True,
            supplied,
        )
        if mode == "mismatch":
            done = db.transition(
                "request",
                key,
                value.version,
                lambda old: req.recover_allocation(old, recovery),
                observation=TransitionObservation(allocation_recovery=recovery),
            )
            row = db.db.execute(
                "SELECT data FROM events WHERE kind='request-observation' AND key=? AND version=?",
                (key, done.version),
            ).fetchone()
            assert msgspec.json.decode(row[0], type=TransitionObservation).now == actual
        else:
            with pytest.raises(req.Refused, match="clock"):
                db.transition(
                    "request",
                    key,
                    value.version,
                    lambda old: req.recover_allocation(old, recovery),
                    observation=TransitionObservation(allocation_recovery=recovery),
                )
            assert db.get("request", key) == value


def test_dispatch_lookup_waits_after_previous_observation(tmp_path):
    from heterodyne.actions.journal import dispatch_key

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        parent = f.persist_reconcile_observation(db, parent, 1)
        # The second result is real and durable, but cannot count until five seconds
        # after the first observation. Caller elapsed=30 and a forged anchor do not help.
        now = dis.Clock(parent.started.boot_id, parent.started.mono_ns + 10**9, parent.started.utc_ns + 10**9)
        child = dis.prepare_reconciliation(parent, "second-read", "9" * 64, now)
        previous = db.get("dispatch", parent.approval_id + ":reconcile:read-0")
        child = msgspec.structs.replace(
            child,
            retired_startups=tuple(
                sorted(
                    {
                        *child.retired_startups,
                        *previous.retired_startups,
                        *previous.retired_nonces,
                        previous.startup_hash,
                    }
                )
            ),
        )
        with pytest.raises(req.Refused, match="not-due"):
            db.add_dispatch(child)
        due = f.reader_clock(db, parent)
        child = msgspec.structs.replace(
            child,
            started=due,
            deadline_ns=due.mono_ns + 60 * 10**9,
            preparation_deadline_ns=due.mono_ns + 60 * 10**9,
        )
        db.add_dispatch(child)
        assert db.get("dispatch", dispatch_key(child)).started == due


def fill_normal_capacity(db):
    from heterodyne.actions.journal import MAX_EVENTS

    # Fixture-only padding: leave the exact per-outstanding-record reserve intact.
    limit = MAX_EVENTS - db._reserved()
    count = db.db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
    with db.transaction():
        for version in range(count, limit):
            db._event("fixture-padding", "fixed", version, b"0")
    return limit


def test_full_normal_capacity_still_allows_native_cleanup_and_no_new_work(tmp_path):
    from heterodyne.actions.journal import MAX_EVENTS

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key, request = r2.persist_request(db, until="ask-pinned")
        fill_normal_capacity(db)
        before = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
        with pytest.raises(req.Refused, match="capacity"):
            db.add_request(f.initial(2))
        assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == before
        recovery = f.withdrawal(request)
        done = db.transition(
            "request",
            key,
            request.version,
            lambda old: req.settle_request(old, "cancelled", recovery=recovery, **r1.cleanup(old)),
            observation=f.cleanup_observation(request, cancellation_recovery=recovery),
        )
        assert done.cleanup_confirmed
        count = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
        assert before < count <= MAX_EVENTS
        assert db.db.execute("SELECT outstanding FROM requests WHERE key=?", (key,)).fetchone() == (0,)
        # Terminal capacity is not pruned or replayed by an idempotent acknowledgement.
        assert db.transition("request", key, done.version, lambda old: old) == done
        assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == count


def test_reserved_capacity_finishes_uncertain_dispatch_then_request(tmp_path):
    from heterodyne.actions.journal import MAX_EVENTS

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        fill_normal_capacity(db)
        for elapsed in (1, 5, 30):
            parent = f.persist_reconcile_observation(db, parent, elapsed)
        recovery = dis.DispatchRecovery(
            parent.approval_id,
            parent.revision,
            parent.digest,
            parent.target,
            parent.attempt_id,
            parent.version,
            f.DIGEST,
            "marmot-repair",
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
            observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
        )
        assert parent.settled and db.db.execute("SELECT count(*) FROM resources").fetchone() == (0,)
        key = request_key(f.initial())
        request = db.get("request", key)
        done = db.transition(
            "request",
            key,
            request.version,
            lambda old: req.settle_request(old, "completed", dispatch=parent, **r1.cleanup(old)),
            observation=f.cleanup_observation(request),
        )
        assert done.cleanup_confirmed and done.completion_effect == "absent"
        assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] <= MAX_EVENTS


def test_one_request_cannot_spend_another_requests_terminal_reserve(tmp_path):
    from heterodyne.actions.journal import TERMINAL_RESERVE

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        _, first = r2.persist_request(db, until="ask-pinned")
        second_key, second = r2.persist_request(db, sequence=2, until="ask-pinned")
        fill_normal_capacity(db)
        before = db.db.execute("SELECT count(*) FROM events").fetchone()[0]
        # A bounded adversarial fixture transaction cannot exhaust another owner's
        # cleanup budget, and over-budget refusal rolls back every attempted event.
        with pytest.raises(req.Refused, match="capacity-budget"):
            with db.transaction():
                db._terminal_budget(first)
                for version in range(TERMINAL_RESERVE):
                    db._event("fixture-recovery", "one-owner", version, b"0")
        assert db.db.execute("SELECT count(*) FROM events").fetchone()[0] == before
        recovery = f.withdrawal(second)
        done = db.transition(
            "request",
            second_key,
            second.version,
            lambda old: req.settle_request(old, "cancelled", recovery=recovery, **r1.cleanup(old)),
            observation=f.cleanup_observation(second, cancellation_recovery=recovery),
        )
        assert done.cleanup_confirmed
        assert db.get("request", request_key(first)) == first


def test_explicit_operation_cannot_select_worker_report_by_state_delta(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
        observation = f.report_observation(parent, effect="succeeded", evidence=f.DIGEST, cleanup=True)
        for operation in (None, "uncertain", "settle"):
            with pytest.raises(req.Refused):
                db.transition(
                    "dispatch",
                    parent.approval_id,
                    parent.version,
                    report_success,
                    observation=msgspec.structs.replace(observation, dispatch_operation=operation),
                )
        assert db.get("dispatch", parent.approval_id) == parent


def test_dispatch_clock_recovery_uses_last_retained_lookup_anchor(tmp_path):
    # Recovery binds the exact retained last observation, not a caller-selected
    # alternate anchor. The independent sampler establishes the discontinuity.
    samples = iter(
        [
            dis.Clock("allocation", 1, 1),
            dis.Clock("allocation", 10**9 + 1, 10**9 + 1),
            *([dis.Clock("boot", 1, 1)] * 4),
        ]
    )
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=lambda: next(samples))) as db:
        parent, _ = r3.ready_parent(db)
        now = dis.Clock("boot", 10**9 + 1, 10**9 + 1)
        db.clock = lambda: now
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        parent = f.persist_reconcile_observation(db, parent, 1)
        anchor = now
        now = dis.Clock("boot", 3 * 10**9 + 1, 5 * 10**9 + 1)
        recovery = dis.DispatchRecovery(
            parent.approval_id,
            parent.revision,
            parent.digest,
            parent.target,
            parent.attempt_id,
            parent.version,
            f.DIGEST,
            "marmot-repair",
            True,
            True,
            True,
            True,
            True,
            clock_discontinuity=True,
            observed_now=now,
            clock_anchor=anchor,
        )
        with pytest.raises(req.Refused, match="anchor"):
            bad = msgspec.structs.replace(recovery, clock_anchor=dis.Clock("forged", 1, 1))
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
            lambda old: dis.settle_dispatch(old, recovery),
            observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
        )
        assert done.settled and done.reconciliation_lookups == 1
