"""R1 regressions use the actual journal and independent synthetic sampler only."""

from contextlib import closing
from unittest.mock import patch

import msgspec
import pytest
import test_actions_p1 as f
from gatekeeper_fixtures import privacy
from test_actions_p2 import SPEC, inputs, policy_for

from heterodyne.actions import decisions as dec
from heterodyne.actions import dispatch as dis
from heterodyne.actions import judgement as j
from heterodyne.actions import requests as req
from heterodyne.actions.codec import encode_record
from heterodyne.actions.journal import FixtureJournal, request_key
from heterodyne.actions.transitions import TransitionObservation
from heterodyne.gatekeeper import contracts as c


class Sampler:
    def __init__(self):
        self.now = dis.Clock("owned", 1, 1)
        self.advance = True
        self.calls = 0

    def __call__(self):
        value = self.now
        self.calls += 1
        if self.advance:
            self.now = dis.Clock("owned", value.mono_ns + 10**9, value.utc_ns + 10**9)
        return value


def pending_dispatch(db, sampler):
    f.persist_decision(db)
    value = dis.prepare(
        "btq-ab123",
        1,
        f.DIGEST,
        f.DIGEST,
        "attempt",
        "resource",
        "c" * 64,
        sampler.now,
        fake_finalized_approval=True,
    )
    return value


@pytest.mark.parametrize("field", ["deadline_ns", "preparation_deadline_ns"])
def test_forged_dispatch_offsets_never_extend_policy(tmp_path, field):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        value = pending_dispatch(db, sampler)
        forged = msgspec.structs.replace(value, **{field: value.started.mono_ns + 10**15})
        db.add_dispatch(forged)
        stored = db.get("dispatch", value.approval_id)
        assert stored.deadline_ns - stored.started.mono_ns == 60_000_000_000
        assert stored.preparation_deadline_ns - stored.started.mono_ns == 30_000_000_000


@pytest.mark.parametrize(
    "reason",
    [
        "no\U000e0061",
        "no\U000e007f",
        "no\u3164",
        "no\u2800",
        "no\rgo",
        "no\tgo",
        "🏴\U000e007f",
        "🏴\U000e0061",
        "no\u115f",
        "no\u1160",
    ],
)
def test_hidden_denial_metadata_is_refused(reason):
    with pytest.raises(req.Refused, match="decision-reason-unsafe"):
        dec.begin_decision(
            f.filed(),
            msgspec.structs.replace(f.event("deny"), denial_reason=reason),
            expected_purpose="execution",
            current_digest=f.DIGEST,
        )


@pytest.mark.parametrize("reason", ["🏳️‍🌈 no", "❤️‍🔥", "👩🏽‍💻 says no", "No.\r\nGroom."])
def test_visible_emoji_and_crlf_are_preserved(reason):
    value = dec.begin_decision(
        f.filed(),
        msgspec.structs.replace(f.event("deny"), denial_reason=reason),
        expected_purpose="execution",
        current_digest=f.DIGEST,
    )
    assert value.event.denial_reason == reason


def test_malformed_projection_returns_fixed_hold():
    action, selected = inputs("permission")
    malformed = msgspec.structs.replace(action, request_revision="1")
    assert j.current_projection(
        None, malformed, selected, privacy(), SPEC, policy=policy_for(selected)
    ) == c.EscalateProposal("stale_digest")


@pytest.mark.parametrize("entry", ["add", "transition", "dispatch"])
def test_legacy_domain_cannot_record_decision(tmp_path, entry):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        value = f.persist_filed_request(db)
        if entry == "transition":
            db.add_decision(f.begun())
        elif entry == "dispatch":
            f.persist_decision(db)
        legacy = msgspec.structs.replace(value, denial_domain=None)
        db.db.execute("UPDATE requests SET data=? WHERE key=?", (encode_record(legacy), request_key(value)))
        before = tuple(db.db.execute("SELECT * FROM events"))
        with pytest.raises(req.Refused, match="legacy-denial-domain"):
            if entry == "add":
                db.add_decision(f.begun())
            elif entry == "transition":
                db.transition(
                    "decision",
                    "btq-ab123",
                    0,
                    lambda old: dec.issue_decision(old, fresh_authority=True, current_digest=f.DIGEST),
                    observation=f.decision_observation(f.begun()),
                )
            else:
                db.add_dispatch(
                    dis.prepare(
                        "btq-ab123",
                        1,
                        f.DIGEST,
                        f.DIGEST,
                        "attempt",
                        "resource",
                        "c" * 64,
                        sampler.now,
                        fake_finalized_approval=True,
                    )
                )
        assert tuple(db.db.execute("SELECT * FROM events")) == before


def test_legacy_null_observation_does_not_escape_validation_error(tmp_path):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        key = db.add_request(f.initial())
        value = db.transition("request", key, 0, req.issue_allocation)
        row = db.db.execute(
            "SELECT data FROM events WHERE kind='request-observation' AND key=?", (key,)
        ).fetchone()
        original = msgspec.json.decode(row[0])
        original["allocation_elapsed_seconds"] = None
        db.db.execute(
            "UPDATE events SET data=? WHERE kind='request-observation' AND key=?",
            (msgspec.json.encode(original), key),
        )
        # The retained old null is non-authoritative; actual journal clock still applies.
        value = db.transition(
            "request",
            key,
            value.version,
            lambda old: req.observe_allocation(old, (), lookup_ok=False, elapsed_seconds=1),
        )
        assert value.allocation_lookups == 1


def test_allocation_backoff_uses_production_sampler_not_caller_flag(tmp_path):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        key = db.add_request(f.initial())
        value = db.transition("request", key, 0, req.issue_allocation)
        sampler.advance = False
        sampler.now = dis.Clock("owned", 1, 1)
        forged = dis.Clock("owned", 31 * 10**9 + 1, 31 * 10**9 + 1)
        with pytest.raises(req.Refused):
            db.transition(
                "request",
                key,
                value.version,
                lambda old: req.observe_allocation(old, (), lookup_ok=False, elapsed_seconds=31),
                observation=TransitionObservation(now=forged),
            )
        sampler.now = dis.Clock("owned", 10**9 + 1, 10**9 + 1)
        value = db.transition(
            "request",
            key,
            value.version,
            lambda old: req.observe_allocation(old, (), lookup_ok=False, elapsed_seconds=1),
        )
        assert value.allocation_lookups == 1


def test_permission_identity_and_policy_checked_once():
    action, selected = inputs("permission")
    with (
        patch.object(j, "_identity", wraps=j._identity) as identity,
        patch.object(j, "evaluate_policy", wraps=j.evaluate_policy) as policy,
    ):
        result = j.current_projection(None, action, selected, privacy(), SPEC, policy=policy_for(selected))
    assert isinstance(result, c.EscalateProposal)
    assert identity.call_count == 1
    assert policy.call_count == 1


def test_dispatch_backoff_ignores_forged_caller_now(tmp_path):
    from heterodyne.actions.journal import dispatch_key

    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        value = pending_dispatch(db, sampler)
        sampler.advance = False
        db.add_dispatch(value)
        parent = db.get("dispatch", value.approval_id)
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old: dis.ready(old, "e" * 64, sampler.now, fresh_gates=True, startup_readback=True),
            observation=TransitionObservation(dispatch_operation="ready"),
        )
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            dis.uncertain,
            observation=TransitionObservation(dispatch_operation="uncertain"),
        )
        anchor = sampler.now
        forged = dis.Clock(anchor.boot_id, anchor.mono_ns + 31 * 10**9, anchor.utc_ns + 31 * 10**9)
        child = dis.prepare_reconciliation(parent, "read", "f" * 64, forged)
        child = msgspec.structs.replace(
            child, deadline_ns=forged.mono_ns + 10**15, preparation_deadline_ns=forged.mono_ns + 10**15
        )
        with pytest.raises(req.Refused, match="reconcile-attempt-not-due"):
            db.add_dispatch(child)
        sampler.now = dis.Clock(anchor.boot_id, anchor.mono_ns + 10**9, anchor.utc_ns + 10**9)
        db.add_dispatch(child)
        stored = db.get("dispatch", dispatch_key(child))
        assert stored.deadline_ns == sampler.now.mono_ns + 60_000_000_000
        assert stored.preparation_deadline_ns == sampler.now.mono_ns + 30_000_000_000
        db.transition(
            "dispatch",
            dispatch_key(child),
            0,
            lambda old: dis.finish_reconciliation(
                old, evidence=f.DIGEST, cleanup_proven=True, effect="unknown"
            ),
            observation=f.reconciliation_observation(child, effect="unknown"),
        )
        parent = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            lambda old: dis.reconcile_lookup(
                old, elapsed_seconds=1, effect="unknown", cleanup_proven=False, evidence=f.DIGEST
            ),
            observation=TransitionObservation(
                dispatch_operation="lookup", reconciliation_key=dispatch_key(child)
            ),
        )
        second = dis.prepare_reconciliation(parent, "read-second", "d" * 64, forged)
        history = set((*stored.retired_startups, *stored.retired_nonces, stored.startup_hash))
        second = msgspec.structs.replace(second, retired_startups=tuple(sorted(history)))
        sampler.now = dis.Clock(anchor.boot_id, anchor.mono_ns + 6 * 10**9, anchor.utc_ns + 6 * 10**9)
        db.add_dispatch(second)
        db.transition(
            "dispatch",
            dispatch_key(second),
            0,
            lambda old: dis.finish_reconciliation(
                old, evidence=f.DIGEST, cleanup_proven=True, effect="unknown"
            ),
            observation=f.reconciliation_observation(second, effect="unknown"),
        )
        observation = TransitionObservation(
            dispatch_operation="lookup", now=forged, reconciliation_key=dispatch_key(second)
        )

        def reducer(old):
            return dis.reconcile_lookup(
                old, elapsed_seconds=31, effect="unknown", cleanup_proven=False, evidence=f.DIGEST
            )

        sampler.now = dis.Clock(anchor.boot_id, anchor.mono_ns + 10**9, anchor.utc_ns + 10**9)
        with pytest.raises(req.Refused):
            db.transition("dispatch", parent.approval_id, parent.version, reducer, observation=observation)
        assert db.get("dispatch", parent.approval_id) == parent
        sampler.now = dis.Clock(anchor.boot_id, anchor.mono_ns + 6 * 10**9, anchor.utc_ns + 6 * 10**9)
        value = db.transition(
            "dispatch",
            parent.approval_id,
            parent.version,
            reducer,
            observation=msgspec.structs.replace(observation, now=None),
        )
        assert value.reconciliation_lookups == 2


@pytest.mark.parametrize("seconds", [31, 61])
def test_extended_caller_deadlines_do_not_allow_late_ready(tmp_path, seconds):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        value = pending_dispatch(db, sampler)
        sampler.advance = False
        value = msgspec.structs.replace(
            value,
            deadline_ns=value.started.mono_ns + 10**15,
            preparation_deadline_ns=value.started.mono_ns + 10**15,
        )
        db.add_dispatch(value)
        stored = db.get("dispatch", value.approval_id)
        sampler.now = dis.Clock(
            stored.started.boot_id,
            stored.started.mono_ns + seconds * 10**9,
            stored.started.utc_ns + seconds * 10**9,
        )
        with pytest.raises(req.Refused):
            db.transition(
                "dispatch",
                value.approval_id,
                0,
                lambda old: dis.ready(old, "e" * 64, sampler.now, fresh_gates=True, startup_readback=True),
                observation=TransitionObservation(dispatch_operation="ready"),
            )
        assert db.get("dispatch", value.approval_id) == stored


@pytest.mark.parametrize("legacy", [1, "bad"])
def test_legacy_nonnull_elapsed_is_fixed_hold_without_rewrite(tmp_path, legacy):
    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        key = db.add_request(f.initial())
        value = db.transition("request", key, 0, req.issue_allocation)
        row = db.db.execute(
            "SELECT data FROM events WHERE kind='request-observation' AND key=?", (key,)
        ).fetchone()
        raw = msgspec.json.decode(row[0])
        raw["allocation_elapsed_seconds"] = legacy
        payload = msgspec.json.encode(raw)
        db.db.execute("UPDATE events SET data=? WHERE kind='request-observation' AND key=?", (payload, key))
        with pytest.raises(req.Refused, match="legacy-observation-needs-provenance"):
            db.transition(
                "request",
                key,
                value.version,
                lambda old: req.observe_allocation(old, (), lookup_ok=False, elapsed_seconds=1),
            )
        assert (
            db.db.execute(
                "SELECT data FROM events WHERE kind='request-observation' AND key=?", (key,)
            ).fetchone()[0]
            == payload
        )


def test_denial_batch_scans_requests_once(tmp_path):
    from heterodyne.actions.codec import assemble_envelope
    from heterodyne.actions.policy import request_denial_key

    sampler = Sampler()
    with closing(FixtureJournal.create(tmp_path / "fixture", clock=sampler)) as db:
        decision = f.persist_decision(db, "deny")
        value = db.get("request", request_key(f.initial()))
        db.record_denial(value, decision)
        # A second exact retained fixture snapshot; not a live authority adapter.
        origin = msgspec.structs.replace(value.origin, bead="btq-xy123")
        key = msgspec.structs.replace(value.key, bead=origin.bead)
        resolved = msgspec.structs.replace(value.resolved, source_bead=origin.bead)
        identity = request_denial_key(value.intent, origin, resolved, value.config)
        other = msgspec.structs.replace(
            value,
            origin=origin,
            key=key,
            approval_id="btq-cd456",
            denial_identity=identity,
            resolved=resolved,
            envelope=assemble_envelope(value.intent, origin, resolved, "btq-cd456", 1, value.config),
        )
        event = msgspec.structs.replace(decision.event, approval_id=other.approval_id)
        other_decision = msgspec.structs.replace(decision, approval_id=other.approval_id, event=event)
        db.db.execute(
            "INSERT INTO requests VALUES(?,?,?,?,?,?)",
            (request_key(other), origin.workstream, origin.bead, other.version, 1, encode_record(other)),
        )
        db.db.execute(
            "INSERT INTO decisions VALUES(?,?,?)",
            (other.approval_id, other_decision.version, encode_record(other_decision)),
        )
        db.db.execute("INSERT INTO denials VALUES(?,?,?)", (identity, origin.session_key, other.approval_id))
        statements = []
        db.db.set_trace_callback(statements.append)
        assert db.denied(identity, origin.session_key)
        db.db.set_trace_callback(None)
        assert sum(sql == "SELECT data FROM requests" for sql in statements) == 1
