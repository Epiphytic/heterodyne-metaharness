"""Second HIGH C1 regressions; private SQLite and independently owned clock only."""

from contextlib import closing
from unittest.mock import patch

import msgspec
import pytest
import test_actions_p1 as f
from gatekeeper_fixtures import privacy
from test_actions_c1_r1 import Sampler, pending_dispatch
from test_actions_p2 import SPEC, inputs, policy_for

from heterodyne.actions import decisions as dec
from heterodyne.actions import dispatch as dis
from heterodyne.actions import judgement as j
from heterodyne.actions import requests as req
from heterodyne.actions.journal import FixtureJournal


@pytest.mark.parametrize(
    "fields",
    [
        {"settled": True, "cleanup_proven": True, "reconciliation_effect": "succeeded"},
        {"reconciliation_lookups": 3},
        {"launch_attempts": 2},
        {"claim_consumed": True, "claim_hash": "f" * 64},
        {"recovery_operator_event": "invented"},
        {"evidence": "f" * 64},
    ],
)
def test_initial_dispatch_cannot_import_lifecycle_evidence(tmp_path, fields):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        value = pending_dispatch(db, sampler)
        before = tuple(db.db.execute("SELECT * FROM events"))
        with pytest.raises(req.Refused, match="dispatch-not-initial"):
            db.add_dispatch(msgspec.structs.replace(value, **fields))
        assert tuple(db.db.execute("SELECT * FROM events")) == before
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 0


@pytest.mark.parametrize(
    "reason",
    [
        "no\ufe0f",
        "no\U000e0100",
        "no\ue000",
        "no\u0378",
        "🏴" + "\U000e0061" * 64 + "\U000e007f",
        "no\u200dgo",
        "no\u200cgo",
    ],
)
def test_hidden_metadata_cannot_encode_arbitrary_payload(reason):
    with pytest.raises(req.Refused, match="decision-reason-unsafe"):
        dec.begin_decision(
            f.filed(),
            msgspec.structs.replace(f.event("deny"), denial_reason=reason),
            expected_purpose="execution",
            current_digest=f.DIGEST,
        )


@pytest.mark.parametrize("reason", ["نمی\u200cخواهم", "क्\u200dष नहीं", "👩🏽‍💻 no", "🏳️‍🌈 no"])
def test_visible_joined_language_and_emoji_preserved(reason):
    result = dec.begin_decision(
        f.filed(),
        msgspec.structs.replace(f.event("deny"), denial_reason=reason),
        expected_purpose="execution",
        current_digest=f.DIGEST,
    )
    assert result.event.denial_reason == reason


def test_typed_model_refusal_is_not_masked_by_valueerror():
    action, selected = inputs("sufficiency")
    with pytest.raises(req.Refused, match="^judgement-model-refused$"):
        j.bind(action, selected, privacy(), msgspec.structs.replace(SPEC, requested="\n"))


def test_permission_floor_does_not_compute_unused_binding():
    action, selected = inputs("permission")
    with patch.object(j, "digest", wraps=j.digest) as hashed:
        j.current_projection(None, action, selected, privacy(), SPEC, policy=policy_for(selected))
    assert not any(
        isinstance(call.args[0], dict) and call.args[0].get("domain") == "plan5-p2-v1"
        for call in hashed.call_args_list
    )


def test_clock_only_decision_has_one_clock_event_without_duplicate_observation(tmp_path):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        f.persist_filed_request(db)
        value = f.begun()
        db.add_decision(value)
        db.transition(
            "decision",
            value.approval_id,
            0,
            lambda old: dec.issue_decision(old, fresh_authority=True, current_digest=f.DIGEST),
            observation=f.decision_observation(value),
        )
        # The explicit native readback remains meaningful and retained.
        assert (
            db.db.execute("SELECT count(*) FROM events WHERE kind='decision-observation'").fetchone()[0] == 1
        )
        # Plain request steps have clock evidence without redundant clock-only observations.
        assert (
            db.db.execute("SELECT count(*) FROM events WHERE kind='request-observation'").fetchone()[0] == 2
        )


def test_reconciliation_insert_cannot_forge_finished_readback(tmp_path):
    import test_actions_p1_r3 as r3
    from actions_journal_fixture import FixtureJournal as ScriptedJournal

    with closing(ScriptedJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
        from heterodyne.actions.transitions import TransitionObservation

        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        child = dis.prepare_reconciliation(parent, "reader", "f" * 64, f.reader_clock(db, parent))
        forged = msgspec.structs.replace(
            child, settled=True, cleanup_proven=True, reconciliation_effect="succeeded", evidence=f.DIGEST
        )
        with pytest.raises(req.Refused, match="dispatch-not-initial"):
            db.add_dispatch(forged)
        assert db._reconciliation_children(parent.approval_id) == ()


def test_retained_worker_success_conflicts_with_authenticated_absence(tmp_path):
    import test_actions_p1_r3 as r3
    from actions_journal_fixture import FixtureJournal as ScriptedJournal

    from heterodyne.actions.transitions import TransitionObservation

    with closing(ScriptedJournal.create(tmp_path / "fixture")) as db:
        parent, _ = r3.ready_parent(db)
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
                effect="succeeded",
                evidence=f.DIGEST,
                cleanup_proven=False,
            ),
            observation=f.report_observation(parent, effect="succeeded", evidence=f.DIGEST, cleanup=False),
        )
        for seconds in (1, 5, 30):
            parent = f.persist_reconcile_observation(db, parent, seconds)
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
        )
        before = tuple(db.db.execute("SELECT * FROM events"))
        with pytest.raises(req.Refused, match="success-conflicts-with-absence"):
            db.transition(
                "dispatch",
                parent.approval_id,
                parent.version,
                lambda old: dis.settle_dispatch(old, recovery),
                observation=TransitionObservation(dispatch_operation="settle", dispatch_recovery=recovery),
            )
        assert tuple(db.db.execute("SELECT * FROM events")) == before
        assert db.db.execute("SELECT count(*) FROM resources").fetchone()[0] == 3
