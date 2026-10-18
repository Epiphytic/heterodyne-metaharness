"""Third-review regressions: pure detached inputs and synthetic offline boot labels."""

import asyncio

import msgspec
import pytest
from gatekeeper_fixtures import privacy
from test_actions_p2 import GROOM, SPEC, Provider, groomed_inputs, inputs, policy_for

from heterodyne.actions.contracts import PolicyFacts
from heterodyne.actions.judgement import (
    GroomState,
    assess,
    concern,
    current_projection,
    groom_request,
    record_groom,
    repin_enrichment,
)
from heterodyne.actions.policy import denial_key
from heterodyne.gatekeeper import contracts as c

BOOT = "offline-boot-abc"


def judged(action, selected, outcome):
    return asyncio.run(assess(action, selected, privacy(), SPEC, Provider(selected, outcome), boot_id=BOOT))


@pytest.mark.parametrize("denied", ("history", "phase"))
@pytest.mark.parametrize("policy", (None, PolicyFacts("e" * 64, False)))
def test_repeated_canonical_denial_dominates_receipt_and_host_policy(denied, policy):
    action, selected = inputs("permission")
    assert action.envelope is not None
    changed = (
        msgspec.structs.replace(action, prior_denials=(denial_key(action.envelope, action.config),))
        if denied == "history"
        else msgspec.structs.replace(action, phase="denied")
    )
    assert current_projection(None, changed, selected, privacy(), SPEC, policy=policy) == c.DenyProposal()


def test_real_offline_boot_survives_groom_record_transition_and_concern():
    action, selected = inputs()
    state = GroomState(action.origin)
    for revision in range(3):
        receipt = judged(action, selected, GROOM)
        state = record_groom(state, receipt, action, selected, privacy(), SPEC, boot_id=BOOT)
        assert len(state.assessed) == revision + 1
        if revision < 2:
            advanced = groom_request(
                state,
                receipt,
                action,
                selected,
                privacy(),
                SPEC,
                boot_id=BOOT,
                intent=action.intent,
                resolved=action.resolved,
                source=action.requested_source,
                origin=action.origin,
            )
            assert advanced.groom_revisions == revision + 1
            action, selected = groomed_inputs(action, selected)
    assert (
        concern(state, action, selected, privacy(), SPEC, "missing-context", boot_id=BOOT).recommended
        == "deny"
    )


def test_real_offline_boot_survives_enrichment_repin_and_wrong_boot_refuses():
    action, selected = inputs()
    outcome = dict(
        status="enrich",
        reason="Relevant quote",
        gaps=[],
        additions=[dict(source_id="1", start=0, end=8, quote="Explicit")],
    )
    receipt = judged(action, selected, outcome)
    regenerated = repin_enrichment(
        receipt,
        action,
        selected,
        privacy(),
        SPEC,
        whole_ask=b"new opaque host ask",
        context_digest="e" * 64,
        boot_id=BOOT,
    )
    assert regenerated.request_revision == 2
    assert isinstance(
        current_projection(receipt, action, selected, privacy(), SPEC, boot_id="other"), c.HoldUnavailable
    )


@pytest.mark.parametrize("mismatch", ("host", "action"))
def test_permission_rejects_stale_current_policy_fingerprint(mismatch):
    action, selected = inputs("permission")
    policy = policy_for(selected)
    if mismatch == "host":
        policy = PolicyFacts("e" * 64, False)
    else:
        action = msgspec.structs.replace(action, policy_revision="e" * 64)
    projection = current_projection(None, action, selected, privacy(), SPEC, policy=policy)
    assert projection == c.EscalateProposal("stale_digest")


@pytest.mark.parametrize("field", ("task", "session", "workstream", "revision", "malformed"))
def test_permission_binding_refuses_other_subject_or_malformed_typed_snapshot(field):
    action, selected = inputs("permission")
    assert isinstance(selected, c.PermissionRequest)
    changed = selected
    if field == "task":
        changed = msgspec.structs.replace(
            selected, subject=msgspec.structs.replace(selected.subject, task_id="other")
        )
    elif field == "session":
        changed = msgspec.structs.replace(
            selected, subject=msgspec.structs.replace(selected.subject, session_ref="other")
        )
    elif field == "workstream":
        changed = msgspec.structs.replace(selected, workstream_id="other")
    elif field == "revision":
        changed = msgspec.structs.replace(selected, revision=2)
    else:
        changed = {"kind": "permission"}
    projection = current_projection(None, action, changed, privacy(), SPEC, policy=policy_for(selected))
    assert projection == c.EscalateProposal("stale_digest")


@pytest.mark.parametrize("gap", ("what", "alternatives"))
def test_missing_request_content_uses_missing_context_not_unsupported_effect(gap):
    action, selected = inputs()
    for _ in range(2):
        action, selected = groomed_inputs(action, selected)
    chosen = {**GROOM, "gaps": [dict(field=gap, detail="Missing request content")]}
    receipt = judged(action, selected, chosen)
    state = record_groom(GroomState(action.origin), receipt, action, selected, privacy(), SPEC, boot_id=BOOT)
    assert (
        concern(state, action, selected, privacy(), SPEC, "missing-context", boot_id=BOOT).reason
        == "missing-context"
    )


def test_wrong_boot_cannot_record_or_spend_groom_advice():
    from heterodyne.actions.requests import Refused

    action, selected = inputs()
    receipt = judged(action, selected, GROOM)
    empty = GroomState(action.origin)
    assert record_groom(empty, receipt, action, selected, privacy(), SPEC, boot_id="other") == empty
    state = record_groom(empty, receipt, action, selected, privacy(), SPEC, boot_id=BOOT)
    with pytest.raises(Refused):
        groom_request(
            state,
            receipt,
            action,
            selected,
            privacy(),
            SPEC,
            boot_id="other",
            intent=action.intent,
            resolved=action.resolved,
            source=action.requested_source,
            origin=action.origin,
        )


@pytest.mark.parametrize("code", ("oversize", "invariant"))
def test_content_gap_fallback_does_not_claim_unsupported_effect(code):
    from heterodyne.actions.judgement import _concern_reason

    assert _concern_reason((c.Gap(code, "/candidate/description", None),)) == "missing-context"
