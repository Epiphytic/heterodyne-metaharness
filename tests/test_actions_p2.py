"""Pure P2: synthetic provider and detached P1 snapshots, never production admission."""

import asyncio

import msgspec
import pytest
from gatekeeper_fixtures import judgment, privacy, request, seal, wire
from test_actions_p1 import DIGEST, staged

from heterodyne.actions.contracts import PolicyFacts
from heterodyne.actions.judgement import (
    GroomState,
    ModelSpec,
    Reply,
    assess,
    bind,
    current_projection,
    record_groom,
)
from heterodyne.actions.requests import Refused, pin_ask
from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.validate import prepare

SPEC = ModelSpec("fake-opus", "plan5-judgement-v1", "test-release")


def inputs(kind="sufficiency"):
    action = pin_ask(staged(), b'{"whole":"native opaque fixture"}', DIGEST, revision=1)
    raw = request(kind)
    raw["workstream_id"] = action.origin.workstream
    raw["revision"] = action.request_revision
    if kind == "sufficiency":
        raw["subject_id"] = raw["approval_id"] = action.approval_id
        raw["native_context_digest"] = action.context_digest
        raw["origin"]["session_ref"] = action.origin.session_key
    else:
        raw["subject_id"] = raw["subject"]["task_id"] = action.origin.bead
        raw["subject"]["session_ref"] = action.origin.session_key
        from heterodyne.gatekeeper.validate import digest

        raw["invocation"]["sha256"] = digest(
            dict(
                subject=raw["subject"],
                cwd_id=raw["invocation"]["cwd_id"],
                input=raw["invocation"]["input"],
                policy_revision=raw["policy_revision"],
            )
        )
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready), prepared
    return action, prepared.value


class Provider:
    def __init__(self, request, outcome=None, model=SPEC):
        self.request, self.outcome, self.model = request, outcome, model
        self.calls = []

    async def judge(self, invocation):
        self.calls.append(invocation)
        return Reply(invocation.input_digest, self.model, wire(judgment(self.request, self.outcome)))


def policy_for(request):
    return (
        PolicyFacts(request.policy_revision.sha256, False)
        if isinstance(request, c.PermissionRequest)
        else None
    )


def run(provider, action, request, clock=lambda: 0):
    return asyncio.run(
        assess(action, request, privacy(), SPEC, provider, clock=clock, policy=policy_for(request))
    )


def test_sufficient_receipt_rechecks_whole_current_binding():
    action, request = inputs()
    result = run(Provider(request), action, request)
    assert current_projection(result, action, request, privacy(), SPEC) == c.SufficientProposal()
    for altered in (
        msgspec.structs.replace(action, requested_source="c" * 40),
        msgspec.structs.replace(action, ask_bytes=b"other"),
        msgspec.structs.replace(action, request_revision=2),
        msgspec.structs.replace(action, context_digest="c" * 64),
    ):
        assert isinstance(current_projection(result, altered, request, privacy(), SPEC), c.HoldUnavailable)
    assert isinstance(
        current_projection(result, action, request, privacy(), ModelSpec("other", SPEC.schema, SPEC.release)),
        c.HoldUnavailable,
    )


def test_late_wrong_model_and_unavailable_never_sufficient():
    action, request = inputs()
    samples = iter((0, 60_000_000_000))
    for result in (
        run(Provider(request), action, request, lambda: next(samples)),
        run(Provider(request, model=ModelSpec("other", SPEC.schema, SPEC.release)), action, request),
    ):
        assert isinstance(current_projection(result, action, request, privacy(), SPEC), c.HoldUnavailable)


def test_wrong_native_digest_refuses_before_provider():
    action, request = inputs()
    altered = msgspec.structs.replace(action, context_digest="e" * 64)
    with pytest.raises(Refused):
        bind(altered, request, privacy(), SPEC)


def test_permission_is_advisory_and_failure_escalates():
    action, request = inputs("permission")
    result = run(Provider(request), action, request)
    assert (
        current_projection(result, action, request, privacy(), SPEC, policy=policy_for(request))
        == c.AllowProposal()
    )
    wrong = run(Provider(request, model=ModelSpec("other", SPEC.schema, SPEC.release)), action, request)
    assert isinstance(
        current_projection(wrong, action, request, privacy(), SPEC, policy=policy_for(request)),
        c.EscalateProposal,
    )


def test_origin_groom_budget_only_counts_distinct_valid_groom_results():
    action, request = inputs()
    chosen = dict(
        status="groom",
        gaps=[dict(field="context", detail="Explain the target")],
        reason="Missing context",
        additions=[],
    )
    receipt = run(Provider(request, chosen), action, request)
    state = GroomState(action.origin)
    first = record_groom(state, receipt, action, request, privacy(), SPEC)
    assert first.failures == 0
    assert len(first.assessed) == 1
    assert record_groom(first, receipt, action, request, privacy(), SPEC) == first
    other = msgspec.structs.replace(
        action, origin=msgspec.structs.replace(action.origin, session_key="other")
    )
    with pytest.raises(Refused):
        record_groom(first, receipt, other, request, privacy(), SPEC)


@pytest.mark.parametrize("kind", ("sufficiency", "permission"))
@pytest.mark.parametrize("start,end", ((100, 99), (0, 60_000_000_000), (0, 60_000_000_001), (0, False)))
def test_clock_rollback_deadline_and_malformed_clock_never_grant(kind, start, end):
    action, selected = inputs(kind)
    samples = iter((start, end))
    result = run(Provider(selected), action, selected, lambda: next(samples))
    projection = current_projection(result, action, selected, privacy(), SPEC, policy=policy_for(selected))
    assert isinstance(projection, (c.HoldUnavailable, c.EscalateProposal))


def test_actual_async_deadline_cancels_cooperative_fake(monkeypatch):
    import heterodyne.actions.judgement as module

    class Slow:
        cancelled = False

        async def judge(self, invocation):
            try:
                await asyncio.sleep(1)
            finally:
                self.cancelled = True

    action, selected = inputs()
    port = Slow()
    monkeypatch.setattr(module, "DEADLINE_NS", 1_000_000)
    result = run(port, action, selected)
    assert result.failure == "timeout"
    assert port.cancelled


@pytest.mark.parametrize("kind", ("sufficiency", "permission"))
@pytest.mark.parametrize("output", (b"{}", b'{"kind":"permission","kind":"sufficiency"}', b"x" * 32769))
def test_malformed_and_oversized_model_output_cannot_grant(kind, output):
    action, selected = inputs(kind)

    class Bad:
        async def judge(self, invocation):
            return Reply(invocation.input_digest, SPEC, output)

    result = run(Bad(), action, selected)
    assert isinstance(
        current_projection(result, action, selected, privacy(), SPEC, policy=policy_for(selected)),
        (c.HoldUnavailable, c.EscalateProposal),
    )


def test_hard_deny_dominates_model_and_missing_policy_refuses():
    action, selected = inputs("permission")
    port = Provider(selected)
    policy = PolicyFacts(selected.policy_revision.sha256, True)
    result = asyncio.run(assess(action, selected, privacy(), SPEC, port, policy=policy))
    assert port.calls == []
    assert current_projection(result, action, selected, privacy(), SPEC, policy=policy) == c.DenyProposal()
    with pytest.raises(Refused):
        asyncio.run(assess(action, selected, privacy(), SPEC, port))


def test_lint_failure_cannot_be_overridden_or_sent_to_model():
    action, selected = inputs()
    raw = msgspec.to_builtins(selected)
    raw["lint"] = dict(status="failed", gaps=[dict(code="native_lint", field="/candidate/ask", detail=None)])
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    port = Provider(prepared.value)
    result = run(port, action, prepared.value)
    assert port.calls == []
    assert isinstance(current_projection(result, action, prepared.value, privacy(), SPEC), c.NeedsGrooming)


def test_current_policy_source_and_snapshot_changes_invalidate_at_all_lifecycle_uses():
    from heterodyne.actions.requests import observe_filed, observe_sufficient

    action, selected = inputs()
    result = run(Provider(selected), action, selected)
    sufficient = observe_sufficient(
        action,
        digest=DIGEST,
        policy_revision=selected.policy_revision.sha256,
        source=action.requested_source,
        sufficient=True,
    )
    filed = observe_filed(sufficient, digest=DIGEST, revision=1, native_readback=True)
    for stage in (action, sufficient, filed):
        assert current_projection(result, stage, selected, privacy(), SPEC) == c.SufficientProposal()
        altered = msgspec.structs.replace(stage, requested_source="c" * 40)
        assert isinstance(current_projection(result, altered, selected, privacy(), SPEC), c.HoldUnavailable)
        raw = msgspec.to_builtins(selected)
        raw["policy_revision"]["sha256"] = "c" * 64
        changed = prepare(seal(raw), privacy())
        assert isinstance(changed, c.Ready)
        assert isinstance(
            current_projection(result, stage, changed.value, privacy(), SPEC), c.HoldUnavailable
        )


def test_provider_projection_omits_private_binding_material():
    action, selected = inputs()
    port = Provider(selected)
    result = run(port, action, selected)
    assert result.reply is not None
    outgoing = port.calls[0].public_input
    for private in (b"/private/source", b"session-1", action.ask_bytes, action.requested_source.encode()):
        assert private not in outgoing
    assert port.calls[0].model == SPEC


def test_unavailable_and_cancellation_are_not_groom_failures():
    action, selected = inputs()

    class Down:
        async def judge(self, invocation):
            raise RuntimeError("must never be returned")

    state = GroomState(action.origin)
    result = run(Down(), action, selected)
    assert record_groom(state, result, action, selected, privacy(), SPEC) == state
    assert "must never" not in repr(result)

    class Cancelled:
        async def judge(self, invocation):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run(Cancelled(), action, selected)


def test_enrichment_proposal_requires_regenerated_whole_ask_and_fresh_judgement():
    from heterodyne.gatekeeper.validate import digest

    action, selected = inputs()
    chosen = dict(
        status="enrich",
        reason="Relevant quote",
        gaps=[],
        additions=[dict(source_id="1", start=0, end=8, quote="Explicit")],
    )
    receipt = run(Provider(selected, chosen), action, selected)
    proposal = current_projection(receipt, action, selected, privacy(), SPEC)
    assert isinstance(proposal, c.EnrichmentProposal)
    assert isinstance(selected, c.SufficiencyRequest)
    assert proposal.candidate.description != selected.candidate.description
    # Host-native regeneration is represented honestly by NEW opaque bytes/digest;
    # this fixture does not implement the native context-digest algorithm.
    raw = msgspec.to_builtins(selected)
    raw["candidate"] = msgspec.to_builtins(proposal.candidate)
    raw["base_candidate_sha256"] = digest(proposal.candidate)
    raw["native_context_digest"] = "e" * 64
    enriched = prepare(seal(raw), privacy())
    assert isinstance(enriched, c.Ready)
    regenerated = msgspec.structs.replace(
        action, ask_bytes=b'{"whole":"new native fixture"}', context_digest="e" * 64
    )
    assert isinstance(
        current_projection(receipt, regenerated, enriched.value, privacy(), SPEC), c.HoldUnavailable
    )
    fresh = run(Provider(enriched.value), regenerated, enriched.value)
    assert current_projection(fresh, regenerated, enriched.value, privacy(), SPEC) == c.SufficientProposal()


def groomed_inputs(action, selected):
    from heterodyne.actions.requests import groom, observe_stage

    updated = groom(
        action,
        intent=action.intent,
        resolved=action.resolved,
        source=action.requested_source,
        origin=msgspec.structs.replace(action.origin, generation=action.origin.generation + 1),
    )
    assert updated.ask_bytes is None and updated.context_digest is None and updated.policy_revision is None
    approval_id = updated.approval_id
    assert approval_id is not None
    for operation in ("park", "land"):
        updated = observe_stage(
            updated,
            operation,
            key=updated.key,
            approval_id=approval_id,
            origin=updated.origin,
            source=updated.requested_source,
            readback_ok=True,
        )
    new_digest = ("e" if updated.groom_revisions == 1 else "f") * 64
    updated = pin_ask(
        updated,
        f'{{"revision":{updated.request_revision}}}'.encode(),
        new_digest,
        revision=updated.request_revision,
    )
    raw = msgspec.to_builtins(selected)
    raw["revision"] = updated.request_revision
    raw["native_context_digest"] = new_digest
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    return updated, prepared.value


def test_two_actual_origin_groom_revisions_then_fixed_bounded_concern():
    from heterodyne.actions.judgement import concern, groom_route

    action, selected = inputs()
    chosen = dict(
        status="groom",
        gaps=[dict(field="context", detail="Context missing")],
        reason="Missing context",
        additions=[],
    )
    state = GroomState(action.origin)
    for revision in range(3):
        receipt = run(Provider(selected, chosen), action, selected)
        state = record_groom(state, receipt, action, selected, privacy(), SPEC)
        assert state.failures == revision
        assert record_groom(state, receipt, action, selected, privacy(), SPEC) == state
        if revision < 2:
            assert groom_route(state) == c.ReturnToOrigin()
            action, selected = groomed_inputs(action, selected)
    assert groom_route(state) == c.CannotApproveNotice()
    public = concern(state, action, selected, privacy(), SPEC, "missing-context")
    assert public.choices == ("groom", "deny") and public.recommended == "deny"
    encoded = wire(public)
    assert len(public.digest_prefix) == 12 and len(encoded) < 256
    assert action.context_digest is not None
    for private in (action.requested_source.encode(), action.context_digest.encode(), b"session-1"):
        assert private not in encoded
    with pytest.raises(Refused):
        groomed_inputs(action, selected)


def test_unchanged_revision_cannot_spend_second_groom_attempt_with_new_model_identity():
    action, selected = inputs()
    chosen = dict(
        status="groom",
        gaps=[dict(field="context", detail="Context missing")],
        reason="Missing context",
        additions=[],
    )
    receipt = run(Provider(selected, chosen), action, selected)
    state = record_groom(GroomState(action.origin), receipt, action, selected, privacy(), SPEC)
    other = ModelSpec("other", SPEC.schema, SPEC.release)
    result = asyncio.run(assess(action, selected, privacy(), other, Provider(selected, chosen, other)))
    with pytest.raises(Refused):
        record_groom(state, result, action, selected, privacy(), other)


def test_receipt_roundtrip_is_evidence_not_authority_and_malformed_reason_is_withheld():
    from heterodyne.actions.judgement import Receipt

    action, selected = inputs()
    receipt = run(Provider(selected), action, selected)
    restored = msgspec.json.decode(msgspec.json.encode(receipt), type=Receipt)
    assert current_projection(restored, action, selected, privacy(), SPEC) == c.SufficientProposal()
    invalid = msgspec.structs.replace(restored, failure="secret accidental exception")
    projected = current_projection(invalid, action, selected, privacy(), SPEC)
    assert isinstance(projected, c.HoldUnavailable)
    assert "secret accidental" not in repr(projected)
    # No serialized result enables the five outward adapters.
    from heterodyne.actions.contracts import REGISTRY

    assert all(not entry.available for entry in REGISTRY)


@pytest.mark.parametrize("field", ("requested", "schema", "release"))
def test_each_reported_model_metadata_field_is_bound(field):
    action, selected = inputs()
    other = msgspec.structs.replace(SPEC, **{field: "different"})
    result = run(Provider(selected, model=other), action, selected)
    assert isinstance(current_projection(result, action, selected, privacy(), SPEC), c.HoldUnavailable)


def test_timeout_is_not_undone_when_fake_swallows_cancellation(monkeypatch):
    import heterodyne.actions.judgement as module

    action, selected = inputs()

    class Suppressed:
        async def judge(self, invocation):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return Reply(invocation.input_digest, SPEC, wire(judgment(selected)))

    monkeypatch.setattr(module, "DEADLINE_NS", 1_000_000)
    receipt = run(Suppressed(), action, selected)
    assert receipt.failure == "timeout" and receipt.reply is None


def test_bound_source_snapshot_and_intent_mutation_reject_old_receipt():
    action, selected = inputs()
    result = run(Provider(selected), action, selected)
    raw = msgspec.to_builtins(selected)
    raw["context"][0]["source_id"] = "different-source"
    changed = prepare(seal(raw), privacy())
    assert isinstance(changed, c.Ready)
    assert isinstance(current_projection(result, action, changed.value, privacy(), SPEC), c.HoldUnavailable)
    for altered in (
        msgspec.structs.replace(action, intent=msgspec.structs.replace(action.intent, context="Changed")),
        msgspec.structs.replace(action, policy_revision="c" * 64),
        msgspec.structs.replace(action, sufficiency_source="c" * 40),
        msgspec.structs.replace(action, phase="denied"),
    ):
        assert isinstance(current_projection(result, altered, selected, privacy(), SPEC), c.HoldUnavailable)


def test_receipt_invalid_input_digest_never_reaches_sufficiency():
    action, selected = inputs()

    class Misbound:
        async def judge(self, invocation):
            return Reply("f" * 64, SPEC, wire(judgment(selected)))

    receipt = run(Misbound(), action, selected)
    assert receipt.reply is None
    assert isinstance(current_projection(receipt, action, selected, privacy(), SPEC), c.HoldUnavailable)


@pytest.mark.parametrize(
    "field,value",
    (("groom_revisions", True), ("request_revision", "1"), ("key", {}), ("prior_denials", ("not-a-digest",))),
)
def test_malformed_typed_action_is_refused_before_provider(field, value):
    action, selected = inputs()
    malformed = msgspec.structs.replace(action, **{field: value})
    port = Provider(selected)
    with pytest.raises(Refused):
        run(port, malformed, selected)
    assert port.calls == []


def test_existing_same_intent_denial_cannot_be_judged_into_permission():
    from heterodyne.actions.policy import denial_key

    action, selected = inputs("permission")
    assert action.envelope is not None
    action = msgspec.structs.replace(action, prior_denials=(denial_key(action.envelope, action.config),))
    port = Provider(selected)
    with pytest.raises(Refused):
        run(port, action, selected)
    assert port.calls == []
