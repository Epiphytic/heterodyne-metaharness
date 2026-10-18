"""Sixth-review regressions over the private fixture journal only."""

from contextlib import closing

import pytest
import test_actions_p1 as f
import test_actions_p1_r1 as r1

from heterodyne.actions import dispatch as dis
from heterodyne.actions import requests as req
from heterodyne.actions.journal import FixtureJournal
from heterodyne.actions.transitions import TransitionObservation


@pytest.mark.parametrize("mode", ["allocate-dispatch", "abandon-dispatch", "abandon-allocation"])
@pytest.mark.parametrize("sampler", [False, True])
def test_decoy_recovery_cannot_bypass_the_selected_clock_check(tmp_path, mode, sampler):
    actual = dis.Clock("continuous", 1, 1)
    clock = (lambda: actual) if sampler else None
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=clock)) as db:
        key = db.add_request(f.initial())
        value = db.transition(
            "request",
            key,
            0,
            req.issue_allocation,
            observation=TransitionObservation(now=dis.Clock("continuous", 1, 1)),
        )
        allocate = req.AllocationRecovery(
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
            dis.Clock("forged", 1, 1),
        )
        decoy = dis.DispatchRecovery("unused", 0, f.DIGEST, "unused", "unused", 0, f.DIGEST, "unused")
        if mode == "allocate-dispatch":

            def proposal(old):
                return req.recover_allocation(old, allocate)

            observation = TransitionObservation(allocation_recovery=allocate, dispatch_recovery=decoy)
        else:
            abandon = req.AbandonmentRecovery(
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
                dis.Clock("forged", 1, 1),
            )

            def proposal(old):
                return req.settle_request(old, "reconciled-abandoned", recovery=abandon, **r1.cleanup(old))

            observation = f.cleanup_observation(
                value,
                abandonment_recovery=abandon,
                **(
                    {"dispatch_recovery": decoy}
                    if mode == "abandon-dispatch"
                    else {
                        "allocation_recovery": req.AllocationRecovery(
                            value.key,
                            value.origin,
                            "btq-ab123",
                            ("btq-ab123",),
                            (),
                            value.version,
                            f.DIGEST,
                            "unused",
                        ),
                    }
                ),
            )
        with pytest.raises(req.Refused, match="recovery"):
            db.transition("request", key, value.version, proposal, observation=observation)
        assert db.get("request", key) == value
        assert not value.allocation_lookups


def test_live_reconciliation_child_blocks_parent_resource_release(tmp_path):
    import test_actions_p1_r3 as r3
    import test_actions_p1_r5 as r5

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        parent, clock = r3.ready_parent(db)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        child = dis.prepare_reconciliation(parent, "active-reader", "f" * 64, f.reader_clock(db, parent))
        db.add_dispatch(child)
        with pytest.raises(req.Refused, match="reconciliation.*held"):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                r5.report_success,
                observation=f.report_observation(parent, effect="succeeded", evidence=f.DIGEST, cleanup=True),
            )
        assert db.get("dispatch", parent.approval_id) == parent
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 3
