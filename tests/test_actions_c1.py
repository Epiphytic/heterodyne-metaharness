"""C1 regression contracts: synthetic inputs, disposable journals, no adapters."""

from contextlib import closing
from typing import Any, cast

import msgspec
import pytest
import test_actions_p1 as f
from gatekeeper_fixtures import privacy, seal
from test_actions_p2 import SPEC, inputs, policy_for

from heterodyne.actions import decisions as dec
from heterodyne.actions import dispatch as dis
from heterodyne.actions import judgement as j
from heterodyne.actions import requests as req
from heterodyne.actions.journal import FixtureJournal
from heterodyne.actions.transitions import TransitionObservation
from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.validate import prepare


def test_issuance_without_journal_sampler_is_refused(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        key = db.add_request(f.initial())
        with pytest.raises(req.Refused, match="sampler"):
            db.transition(
                "request",
                key,
                0,
                req.issue_allocation,
                observation=TransitionObservation(now=dis.Clock("forged", 1, 1)),
            )
        assert db.get("request", key).phase == "allocation-intent"


def test_issuance_without_observation_still_samples(tmp_path):
    now = dis.Clock("owned", 9, 9)
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=lambda: now)) as db:
        key = db.add_request(f.initial())
        value = db.transition("request", key, 0, req.issue_allocation)
        row = db.db.execute(
            "SELECT data FROM events WHERE kind='request-clock' AND key=? AND version=?", (key, value.version)
        ).fetchone()
        assert row is not None
        assert msgspec.json.decode(row[0], type=dis.Clock) == now


def test_obsolete_elapsed_counter_is_not_an_observation_field():
    with pytest.raises(TypeError):
        cast(Any, TransitionObservation)(allocation_elapsed_seconds=30)


def test_unconsumed_success_is_counted_but_never_attributed():
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
    value = msgspec.structs.replace(value, state="uncertain")
    changed = dis.reconcile_lookup(
        value, elapsed_seconds=1, effect="succeeded", evidence=f.DIGEST, cleanup_proven=True
    )
    assert changed.reconciliation_lookups == 1
    assert changed.state == "uncertain" and not changed.settled and not changed.claim_consumed


@pytest.mark.parametrize("phase", ["completed", "cancelled", "reconciled-abandoned", "blocker-pending"])
def test_permission_terminal_and_blocker_never_project_fresh_denial(phase):
    action, selected = inputs("permission")
    assert isinstance(selected, c.PermissionRequest)
    action = msgspec.structs.replace(action, phase=phase)
    policy = policy_for(selected)
    assert policy is not None
    policy = msgspec.structs.replace(policy, widens_permissions=True)
    assert isinstance(
        j.current_projection(None, action, selected, privacy(), SPEC, policy=policy), c.EscalateProposal
    )


@pytest.mark.parametrize("fault", ["subject", "policy", "key", "envelope"])
def test_hard_deny_never_certifies_unbound_permission(fault):
    action, selected = inputs("permission")
    assert isinstance(selected, c.PermissionRequest)
    policy = policy_for(selected)
    assert policy is not None
    policy = msgspec.structs.replace(policy, widens_permissions=True)
    if fault == "subject":
        selected = msgspec.structs.replace(
            selected, subject=msgspec.structs.replace(selected.subject, task_id="btq-zz999")
        )
    elif fault == "policy":
        policy = msgspec.structs.replace(policy, revision="e" * 64)
    elif fault == "key":
        action = msgspec.structs.replace(action, key=msgspec.structs.replace(action.key, bead="btq-zz999"))
    else:
        assert action.envelope is not None
        action = msgspec.structs.replace(
            action, envelope=msgspec.structs.replace(action.envelope, request_revision=2)
        )
    assert isinstance(
        j.current_projection(None, action, selected, privacy(), SPEC, policy=policy), c.EscalateProposal
    )


def test_permission_deadline_precedes_semantic_floor():
    action, selected = inputs("permission")
    assert isinstance(selected, c.PermissionRequest)
    raw = msgspec.to_builtins(selected)
    raw["elapsed_ms"] = selected.effective_budget_ms
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    selected = prepared.value
    assert j.current_projection(
        None, action, selected, privacy(), SPEC, policy=policy_for(selected)
    ) == c.EscalateProposal("deadline")


@pytest.mark.parametrize(
    "reason", ["No.\r\nPlease groom.", "👩\u200d💻 says no", "🏴\U000e0067\U000e0062\U000e007f"]
)
def test_decision_metadata_preserves_authenticated_unicode_and_lines(reason):
    event = msgspec.structs.replace(f.event("deny"), denial_reason=reason)
    result = dec.begin_decision(f.filed(), event, expected_purpose="execution", current_digest=f.DIGEST)
    assert result.event.denial_reason == reason


@pytest.mark.parametrize("reason", ["\u200d", "\r\n ", "\u202eevil"])
def test_invisible_or_bidi_denial_is_refused(reason):
    with pytest.raises(req.Refused):
        dec.begin_decision(
            f.filed(),
            msgspec.structs.replace(f.event("deny"), denial_reason=reason),
            expected_purpose="execution",
            current_digest=f.DIGEST,
        )


def test_approval_cannot_carry_denial_reason():
    with pytest.raises(req.Refused):
        dec.begin_decision(
            f.filed(),
            msgspec.structs.replace(f.event(), denial_reason="No"),
            expected_purpose="execution",
            current_digest=f.DIGEST,
        )


@pytest.mark.parametrize(
    "field,expected",
    [
        ("what", "missing-context"),
        ("context", "missing-context"),
        ("alternatives", "missing-context"),
        ("why", "missing-context"),
        ("excludes", "missing-context"),
        ("risks", "missing-context"),
        ("refs", "ambiguous-target"),
        ("effect", "unsupported-effect"),
    ],
)
def test_all_semantic_gap_labels_follow_actual_gatekeeper_projection(field, expected):
    from heterodyne.gatekeeper.project import _sufficiency

    _, selected = inputs()
    assert isinstance(selected, c.SufficiencyRequest)
    projection = _sufficiency(selected, c.Groom((c.SemanticGap(field, "Missing"),), "Needs context", ()))
    assert isinstance(projection, c.NeedsGrooming)
    assert j._concern_reason(projection.gaps) == expected


def test_preallocation_denial_uses_canonical_identity_without_fake_approval():
    action, selected = inputs("permission")
    assert isinstance(selected, c.PermissionRequest)
    from heterodyne.actions.policy import request_denial_key

    identity = request_denial_key(action.intent, action.origin, action.resolved, action.config)
    action = msgspec.structs.replace(
        action, approval_id=None, envelope=None, phase="allocation-intent", prior_denials=(identity,)
    )
    assert j.current_projection(None, action, selected, privacy(), SPEC) == c.DenyProposal()
    assert action.approval_id is None and action.envelope is None


def test_legacy_denial_domain_holds_without_rewriting_audit(tmp_path):
    from heterodyne.actions.codec import encode_record
    from heterodyne.actions.journal import request_key

    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        value = f.initial()
        key = db.add_request(value)
        legacy = msgspec.structs.replace(value, denial_domain=None)
        payload = encode_record(legacy)
        db.db.execute("UPDATE requests SET data=? WHERE key=?", (payload, key))
        before = tuple(db.db.execute("SELECT * FROM events"))
        with pytest.raises(req.Refused, match="legacy-denial-domain"):
            db.get("request", request_key(legacy))
        assert tuple(db.db.execute("SELECT * FROM events")) == before
        assert db.db.execute("SELECT data FROM requests WHERE key=?", (key,)).fetchone()[0] == payload


def test_unassociated_legacy_denial_cannot_be_forgotten_on_new_admission(tmp_path):
    with closing(FixtureJournal.create(tmp_path / "fixture")) as db:
        value = f.initial()
        db.db.execute("INSERT INTO denials VALUES(?,?,?)", ("a" * 64, value.origin.session_key, "btq-old12"))
        with pytest.raises(req.Refused, match="unique-durable-request"):
            db.add_request(value)
        assert db.db.execute("SELECT count(*) FROM requests").fetchone() == (0,)
        assert db.db.execute("SELECT count(*) FROM denials").fetchone() == (1,)


def test_unconsumed_success_keeps_durable_child_and_parent_audit(tmp_path):
    import test_actions_p1_r3 as r3
    from actions_journal_fixture import FixtureJournal as SampledJournal

    from heterodyne.actions.journal import dispatch_key

    with closing(SampledJournal.create(tmp_path / "fixture")) as db:
        parent, clock = r3.ready_parent(db, consumed=False)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        now = dis.Clock(clock.boot_id, clock.mono_ns + 10**9, clock.utc_ns + 10**9)
        child = dis.prepare_reconciliation(parent, "read-success", "f" * 64, now)
        db.add_dispatch(child)
        child = db.transition(
            "dispatch",
            dispatch_key(child),
            0,
            lambda old: dis.finish_reconciliation(
                old, evidence=f.DIGEST, cleanup_proven=True, effect="succeeded", effect_cleanup_proven=True
            ),
            observation=f.reconciliation_observation(child, effect="succeeded", effect_cleanup=True),
        )
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old: dis.reconcile_lookup(
                old, elapsed_seconds=1, effect="succeeded", evidence=f.DIGEST, cleanup_proven=True
            ),
            observation=TransitionObservation(
                dispatch_operation="lookup", now=now, reconciliation_key=dispatch_key(child)
            ),
        )
        assert parent.reconciliation_lookups == 1 and not parent.claim_consumed and not parent.settled
        assert parent.reconciliation_attempts == (child.attempt_id,)
        assert db.get("dispatch", dispatch_key(child)) == child
        assert (
            db.db.execute(
                "SELECT count(*) FROM events WHERE kind='dispatch' AND key=?", (parent.approval_id,)
            ).fetchone()[0]
            >= 2
        )


def test_denial_history_requires_exact_retained_decision_binding(tmp_path):
    from actions_journal_fixture import FixtureJournal as SampledJournal

    from heterodyne.actions.codec import encode_record

    with closing(SampledJournal.create(tmp_path / "fixture")) as db:
        value = f.persist_filed_request(db)
        decision = f.persist_decision(db, "deny")
        db.record_denial(value, decision)
        identity = f.required(value.denial_identity)
        assert db.denied(identity, value.origin.session_key)
        forged = msgspec.structs.replace(decision, digest="a" * 64)
        db.db.execute(
            "UPDATE decisions SET data=? WHERE key=?", (encode_record(forged), decision.approval_id)
        )
        before = tuple(db.db.execute("SELECT * FROM events"))
        with pytest.raises(req.Refused, match="denial-history-not-committed"):
            db.denied(identity, value.origin.session_key)
        assert tuple(db.db.execute("SELECT * FROM events")) == before
        assert db.db.execute("SELECT count(*) FROM denials").fetchone() == (1,)
