"""R2 regressions: detached pure snapshots and synthetic providers only."""

import msgspec
import pytest
from gatekeeper_fixtures import privacy, seal
from test_actions_p2 import GROOM, SPEC, Provider, groomed_inputs, inputs, policy_for, run

from heterodyne.actions.contracts import PolicyFacts
from heterodyne.actions.judgement import GroomState, concern, current_projection, groom_request, record_groom
from heterodyne.actions.requests import Refused, observe_sufficient
from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.validate import prepare


@pytest.mark.parametrize("missing_envelope", (False, True))
@pytest.mark.parametrize(
    "policy_options",
    ({"host_overrides": {"push_branch": "hard_deny"}}, {"restrict": {"hard_deny": ["push_branch"]}}),
)
def test_hard_deny_precedes_bad_permission_input(missing_envelope, policy_options):
    action, selected = inputs("permission")
    if missing_envelope:
        action = msgspec.structs.replace(action, envelope=None)
    assert (
        current_projection(
            None,
            action,
            {"kind": "permission"},
            privacy(),
            SPEC,
            policy=policy_for(selected),
            **policy_options,
        )
        == c.DenyProposal()
    )


@pytest.mark.parametrize("widens", (False, True))
def test_sufficiency_never_uses_permission_projection(widens):
    action, selected = inputs()
    receipt = run(Provider(selected), action, selected)
    assert isinstance(
        current_projection(
            receipt,
            action,
            selected,
            privacy(),
            SPEC,
            policy=PolicyFacts(selected.policy_revision.sha256, widens),
        ),
        c.HoldUnavailable,
    )


def lint_inputs():
    action, selected = inputs()
    raw = msgspec.to_builtins(selected)
    raw["lint"] = dict(status="failed", gaps=[dict(code="native_lint", field="/candidate/ask", detail=None)])
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    return action, prepared.value


@pytest.mark.parametrize("bad_receipt", (None, "foreign"))
def test_lint_receipt_cannot_poison_groom_state(bad_receipt):
    action, selected = lint_inputs()
    receipt = run(Provider(selected), action, selected)
    if bad_receipt == "foreign":
        bad_receipt = msgspec.structs.replace(receipt, input_digest="a" * 64)
    with pytest.raises(Refused):
        record_groom(GroomState(action.origin), bad_receipt, action, selected, privacy(), SPEC)
    state = record_groom(GroomState(action.origin), receipt, action, selected, privacy(), SPEC)
    assert state.assessed == (receipt.input_digest,)


@pytest.mark.parametrize("first_revision", (1, 2))
@pytest.mark.parametrize("initial", ("outage", "sufficient"))
def test_first_negative_after_consumed_p1_revision_has_fixed_exit(first_revision, initial):
    action, selected = inputs()
    state = GroomState(action.origin)

    class Down:
        async def judge(self, invocation):
            raise OSError

    receipt = run(Down() if initial == "outage" else Provider(selected), action, selected)
    assert record_groom(state, receipt, action, selected, privacy(), SPEC) == state
    if initial == "sufficient":
        assert action.context_digest is not None
        action = observe_sufficient(
            action,
            digest=action.context_digest,
            policy_revision=selected.policy_revision.sha256,
            source=action.requested_source,
            sufficient=True,
        )
        assert action.phase == "sufficient"
    for _ in range(first_revision):
        action, selected = groomed_inputs(action, selected)
    receipt = run(Provider(selected, GROOM), action, selected)
    state = record_groom(state, receipt, action, selected, privacy(), SPEC)
    assert state.revisions == (first_revision,)
    if first_revision == 1:
        action, selected = groomed_inputs(action, selected)
        receipt = run(Provider(selected, GROOM), action, selected)
        state = record_groom(state, receipt, action, selected, privacy(), SPEC)
    assert concern(state, action, selected, privacy(), SPEC, "missing-context").recommended == "deny"


def test_concern_rechecks_recorded_lint_and_rejects_invented_label():
    action, selected = lint_inputs()
    state = GroomState(action.origin)
    for _ in range(2):
        action, selected = groomed_inputs(action, selected)
    receipt = run(Provider(selected), action, selected)
    state = record_groom(state, receipt, action, selected, privacy(), SPEC)
    with pytest.raises(Refused):
        concern(state, action, selected, privacy(), SPEC, "ambiguous-target")
    assert concern(state, action, selected, privacy(), SPEC, "lint-failed").reason == "lint-failed"
    forged = msgspec.structs.replace(state, receipts=())
    with pytest.raises(Refused):
        concern(forged, action, selected, privacy(), SPEC, "lint-failed")


@pytest.mark.parametrize("bad_action", (None, {}, "bad", True))
def test_malformed_groom_action_is_typed_refusal(bad_action):
    action, selected = lint_inputs()
    receipt = run(Provider(selected), action, selected)
    state = GroomState(action.origin)
    with pytest.raises(Refused):
        record_groom(state, receipt, bad_action, selected, privacy(), SPEC)
    with pytest.raises(Refused):
        groom_request(
            state,
            receipt,
            bad_action,
            selected,
            privacy(),
            SPEC,
            intent=action.intent,
            resolved=action.resolved,
            source=action.requested_source,
            origin=action.origin,
        )
    with pytest.raises(Refused):
        concern(state, bad_action, selected, privacy(), SPEC, "lint-failed")


@pytest.mark.parametrize("revision", (None, "2", True, -1, 3))
def test_malformed_groom_revision_is_typed_refusal(revision):
    action, selected = lint_inputs()
    receipt = run(Provider(selected), action, selected)
    altered = msgspec.structs.replace(action, groom_revisions=revision)
    with pytest.raises(Refused):
        record_groom(GroomState(action.origin), receipt, altered, selected, privacy(), SPEC)
