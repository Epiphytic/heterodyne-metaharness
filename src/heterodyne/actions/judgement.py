"""Pure P2 judgement receipts; fake-provider port only, never consent or dispatch.

Receipts are serializable evidence for a future owning journal, not durable storage here.
The supplied P1/native snapshot and model identity are host attestations. This module
neither authenticates their source nor claims that an opaque ask is a native canonical ask.
"""

import asyncio
import hashlib
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Literal, Protocol, cast

import msgspec

from heterodyne.actions.codec import assemble_envelope, safe_label, validate_request_inputs
from heterodyne.actions.contracts import Intent, Origin, PolicyFacts, Record, Resolved
from heterodyne.actions.policy import DENIAL_DOMAIN, denial_key, evaluate_policy, request_denial_key
from heterodyne.actions.requests import (
    CreationKey,
    Refused,
    Request,
    digest_syntax,
    groom,
    repin_enriched_ask,
    same_session,
    source_syntax,
)
from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.project import disposition, groom_disposition
from heterodyne.gatekeeper.validate import Fault, canonical, data, decode_result, digest, egress, prepare

DEADLINE_NS = 60_000_000_000
FAKE_BOOT_ID = "fake-provider-unattested-boot"


class ModelSpec(Record, frozen=True):
    requested: str
    schema: Literal["plan5-judgement-v1"]
    release: str


class Invocation(Record, frozen=True):
    input_digest: str
    model: ModelSpec
    public_input: bytes
    nonce: str


class Reply(Record, frozen=True):
    input_digest: str
    reported: ModelSpec
    output: bytes
    nonce: str = ""


class Provider(Protocol):
    async def judge(self, invocation: Invocation) -> Reply: ...


class Attempt(Record, frozen=True):
    """Trusted future-owner observation, NOT an unforgeable provider attestation."""

    nonce: str
    boot_id: str
    started_ns: int
    ended_ns: int
    budget_ns: int


class Receipt(Record, frozen=True):
    input_digest: str
    kind: Literal["sufficiency", "permission"]
    elapsed_ns: int
    reply: Reply | None
    failure: Literal["timeout", "unavailable", "stale_digest", "clock"] | None
    attempt: Attempt | None = None


class GroomState(Record, frozen=True):
    origin: Origin
    failures: int = 0
    assessed: tuple[str, ...] = ()
    key: CreationKey | None = None
    approval_id: str | None = None
    revisions: tuple[int, ...] = ()
    receipts: tuple[Receipt, ...] = ()


def _prepared(request: object, privacy: c.PrivacyContext) -> c.SufficiencyRequest | c.PermissionRequest:
    prepared = prepare(request, privacy)
    if not isinstance(prepared, c.Ready) or not isinstance(
        prepared.value, c.SufficiencyRequest | c.PermissionRequest
    ):
        raise Refused("judgement-input-refused")
    return prepared.value


def bind(
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    *,
    policy: PolicyFacts | None = None,
    host_overrides: Mapping[str, str] | None = None,
    restrict: Mapping[str, list[str]] | None = None,
) -> str:
    """Bind exact whole ask, P1 state, source pins, policy, model and judgement schema.

    The native context digest remains opaque; an eventual native adapter must prove it.
    No snapshot from an earlier revision can be used to certify a later one.
    """
    return _bind(
        action,
        _prepared(request, privacy),
        privacy,
        model,
        policy=policy,
        host_overrides=host_overrides,
        restrict=restrict,
    )


def _identity(action: Request, selected: c.SufficiencyRequest | c.PermissionRequest) -> None:
    """Shared detached identity checks, usable before native shell allocation.

    Typed objects are caller data, not authenticated host attestations. Never let a
    policy floor or retained denial certify a different subject or cached envelope.
    """
    if type(action) is not Request:
        raise Refused("judgement-binding-refused")
    # Bytes are not JSON builtins; validate the entire remaining typed snapshot.
    checked = msgspec.convert(
        data(msgspec.structs.replace(action, ask_bytes=None)), type=Request, strict=True
    )
    validate_request_inputs(checked.intent, checked.origin, checked.resolved, checked.config)
    if (
        checked.denial_domain != DENIAL_DOMAIN
        or checked.phase in {"completed", "cancelled", "reconciled-abandoned", "blocker-pending"}
        or checked.key.workstream != checked.origin.workstream
        or checked.key.bead != checked.origin.bead
        or checked.key.session_key != checked.origin.session_key
        or selected.workstream_id != checked.origin.workstream
        or selected.revision != checked.request_revision
        or checked.denial_identity
        != request_denial_key(checked.intent, checked.origin, checked.resolved, checked.config)
        or len(checked.prior_denials) > 64
        or any(not digest_syntax(item) for item in checked.prior_denials)
    ):
        raise Refused("judgement-binding-refused")
    if isinstance(selected, c.PermissionRequest):
        if (selected.subject.task_id, selected.subject.session_ref) != (
            checked.origin.bead,
            checked.origin.session_key,
        ):
            raise Refused("judgement-binding-refused")
    elif selected.origin.session_ref != checked.origin.session_key:
        raise Refused("judgement-binding-refused")
    if checked.approval_id is None:
        if checked.envelope is not None:
            raise Refused("judgement-binding-refused")
    elif checked.envelope != assemble_envelope(
        checked.intent,
        checked.origin,
        checked.resolved,
        checked.approval_id,
        checked.request_revision,
        checked.config,
    ):
        raise Refused("judgement-binding-refused")


def _bind(
    action: Request,
    selected: c.SufficiencyRequest | c.PermissionRequest,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    *,
    policy: PolicyFacts | None = None,
    host_overrides: Mapping[str, str] | None = None,
    restrict: Mapping[str, list[str]] | None = None,
) -> str:
    try:
        _identity(action, selected)
        if (
            type(action) is not Request
            or type(action.ask_bytes) is not bytes
            or not 1 <= len(action.ask_bytes) <= 65536
        ):
            raise Refused("judgement-binding-refused")
        ask_bytes = action.ask_bytes
        action = msgspec.structs.replace(
            msgspec.convert(data(msgspec.structs.replace(action, ask_bytes=None)), type=Request, strict=True),
            ask_bytes=ask_bytes,
        )
        model = msgspec.convert(data(model), type=ModelSpec, strict=True)
        for label in (model.requested, model.schema, model.release):
            if not safe_label(label, 128):
                raise Refused("judgement-model-refused")
        if (
            action.phase not in {"ask-pinned", "sufficient", "filed"}
            or action.context_digest is None
            or not digest_syntax(action.context_digest)
            or not source_syntax(action.requested_source)
            or action.key.workstream != action.origin.workstream
            or action.key.bead != action.origin.bead
            or action.key.session_key != action.origin.session_key
            or selected.workstream_id != action.origin.workstream
            or selected.revision != action.request_revision
        ):
            raise Refused("judgement-binding-refused")
        validate_request_inputs(action.intent, action.origin, action.resolved, action.config)
        if (
            len(action.prior_denials) > 64
            or any(not digest_syntax(item) for item in action.prior_denials)
            or action.envelope is None
            or denial_key(action.envelope, action.config) in action.prior_denials
        ):
            raise Refused("judgement-denied-intent")
        if (
            action.approval_id is None
            or action.envelope
            != assemble_envelope(
                action.intent,
                action.origin,
                action.resolved,
                action.approval_id,
                action.request_revision,
                action.config,
            )
            or (
                action.policy_revision is not None
                and action.policy_revision != selected.policy_revision.sha256
            )
            or (
                action.sufficiency_source is not None and action.sufficiency_source != action.requested_source
            )
        ):
            raise Refused("judgement-binding-refused")
        if isinstance(selected, c.SufficiencyRequest):
            if (
                selected.approval_id != action.approval_id
                or selected.native_context_digest != action.context_digest
                or selected.origin.session_ref != action.origin.session_key
            ):
                raise Refused("judgement-binding-refused")
            if isinstance(selected.enrichment, c.Used):
                if (
                    action.enrichment_base_ask is None
                    or action.enrichment_base_context is None
                    or action.enrichment_candidate != digest(selected.candidate)
                    or hashlib.sha256(ask_bytes).hexdigest() == action.enrichment_base_ask
                    or action.context_digest == action.enrichment_base_context
                ):
                    raise Refused("enrichment-repin-required")
            elif action.enrichment_candidate is not None:
                raise Refused("enrichment-repin-required")
        elif (
            selected.subject.task_id != action.origin.bead
            or selected.subject.session_ref != action.origin.session_key
        ):
            raise Refused("judgement-binding-refused")
        if isinstance(selected, c.PermissionRequest):
            if (
                type(policy) is not PolicyFacts
                or policy.revision != selected.policy_revision.sha256
                or type(policy.widens_permissions) is not bool
            ):
                raise Refused("judgement-policy-refused")
            evaluate_policy(
                action.envelope,
                action.config,
                policy,
                {} if host_overrides is None else host_overrides,
                {} if restrict is None else restrict,
            )
        elif policy is not None:
            raise Refused("judgement-policy-refused")
        # Lifecycle phase/CAS versions may advance without changing the judged input.
        # Denials/terminal phases still fail above. No semantic input is dropped.
        state = dict(
            key=action.key,
            origin=action.origin,
            intent=action.intent,
            resolved=action.resolved,
            config=action.config,
            requested_source=action.requested_source,
            approval_id=action.approval_id,
            envelope=action.envelope,
            revision=action.request_revision,
            groom_revisions=action.groom_revisions,
            context_digest=action.context_digest,
            denial_identity=action.denial_identity,
            prior_denials=action.prior_denials,
            shell_ids=action.shell_ids,
            recovery_operator_event=action.recovery_operator_event,
            recovery_evidence=action.recovery_evidence,
            recovery_closed_orphans=action.recovery_closed_orphans,
            enrichment_base_ask=action.enrichment_base_ask,
            enrichment_base_context=action.enrichment_base_context,
            enrichment_candidate=action.enrichment_candidate,
        )
        return digest(
            dict(
                domain="plan5-p2-v1",
                state=state,
                whole_ask=hashlib.sha256(ask_bytes).hexdigest(),
                judgement=selected,
                model=model,
                privacy=privacy,
                policy=policy,
                host_overrides={} if host_overrides is None else host_overrides,
                restrict={} if restrict is None else restrict,
            )
        )
    except (Fault, msgspec.ValidationError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise Refused("judgement-binding-refused") from exc


def _sample(clock: Callable[[], int]) -> int:
    value = clock()
    if type(value) is not int or value < 0:
        raise Refused("judgement-clock-refused")
    return value


def _boottime() -> int:
    """Linux elapsed deadline includes suspend; no calendar-clock authority is inferred."""
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


async def assess(
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    provider: Provider,
    *,
    clock: Callable[[], int] = _boottime,
    boot_id: str = FAKE_BOOT_ID,
    policy: PolicyFacts | None = None,
    host_overrides: Mapping[str, str] | None = None,
    restrict: Mapping[str, list[str]] | None = None,
) -> Receipt:
    """One cooperative fake attempt; nonce/clocks are host observations, not model proof.

    A production owner must persist nonce+input+boot+start BEFORE invoking its adapter,
    corroborate the echoed nonce and end sample, and recheck boot/deadline on use. This
    pure port has no durable owner and its serialized receipts are forgeable by callers.
    """
    selected = _prepared(request, privacy)
    identity = _bind(
        action, selected, privacy, model, policy=policy, host_overrides=host_overrides, restrict=restrict
    )
    kind: Literal["sufficiency", "permission"] = (
        "sufficiency" if isinstance(selected, c.SufficiencyRequest) else "permission"
    )
    # Every P0 outward action has at least the escalation floor. There is no approved
    # typed semantic bridge from arbitrary permission tool input to this envelope.
    if isinstance(selected, c.PermissionRequest):
        return Receipt(identity, kind, 0, None, "unavailable")
    if not isinstance(selected.lint, c.Passed):
        return Receipt(identity, kind, 0, None, "unavailable")
    public = egress(selected, privacy)
    if not isinstance(public, c.Valid) or not safe_label(boot_id, 128):
        return Receipt(identity, kind, 0, None, "unavailable")
    try:
        start = _sample(clock)
    except (Refused, TypeError, ValueError, OverflowError, OSError):
        return Receipt(identity, kind, 0, None, "clock")
    nonce = uuid.uuid4().hex
    budget = DEADLINE_NS
    failure: Literal["timeout", "unavailable", "stale_digest", "clock"] | None = None
    reply = None
    deadline = asyncio.timeout(budget / 1_000_000_000)
    try:
        async with deadline:
            reply = await provider.judge(Invocation(identity, model, canonical(public.value), nonce))
    except TimeoutError:
        failure = "timeout"
    except Exception:
        failure = "unavailable"
    try:
        end = _sample(clock)
    except (Refused, TypeError, ValueError, OverflowError, OSError):
        return Receipt(identity, kind, 0, None, "clock")
    elapsed = end - start
    attempt = Attempt(nonce, boot_id, start, end, budget)
    if elapsed < 0:
        return Receipt(identity, kind, elapsed, None, "clock", attempt)
    if deadline.expired() or elapsed >= budget:
        failure = "timeout"
    if failure is not None:
        return Receipt(identity, kind, elapsed, None, failure, attempt)
    if (
        type(reply) is not Reply
        or reply.input_digest != identity
        or reply.reported != model
        or reply.nonce != nonce
        or type(reply.output) is not bytes
        or len(reply.output) > 32768
    ):
        return Receipt(identity, kind, elapsed, None, "stale_digest", attempt)
    return Receipt(identity, kind, elapsed, reply, None, attempt)


def _permission_input(request: object) -> bool:
    return isinstance(request, c.PermissionRequest) or (
        type(request) is dict and cast(dict[object, object], request).get("kind") == "permission"
    )


def current_projection(
    receipt: object,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    *,
    policy: PolicyFacts | None = None,
    boot_id: str = FAKE_BOOT_ID,
    host_overrides: Mapping[str, str] | None = None,
    restrict: Mapping[str, list[str]] | None = None,
) -> c.Projection:
    """Bind the current subject/policy before deriving any durable denial proposal."""
    permission = _permission_input(request)
    hold = c.EscalateProposal if permission else c.HoldUnavailable
    try:
        if permission:
            selected = _prepared(request, privacy)
            if not isinstance(selected, c.PermissionRequest):
                return c.EscalateProposal("invalid_host")
            _identity(action, selected)
            canonical_denial = request_denial_key(
                action.intent, action.origin, action.resolved, action.config
            )
            if action.phase == "denied" or canonical_denial in action.prior_denials:
                return c.DenyProposal()
            if (
                type(policy) is not PolicyFacts
                or policy.revision != selected.policy_revision.sha256
                or (action.policy_revision is not None and action.policy_revision != policy.revision)
            ):
                return c.EscalateProposal("stale_digest")
            precheck = disposition("permission", c.Ready(selected), None, privacy)
            if precheck == c.EscalateProposal("deadline"):
                return precheck
            if action.approval_id is None:
                return c.EscalateProposal("input_profile")
            _bind(
                action,
                selected,
                privacy,
                model,
                policy=policy,
                host_overrides=host_overrides,
                restrict=restrict,
            )
            if action.envelope is None:
                raise Refused("judgement-binding-refused")
            floor = evaluate_policy(
                action.envelope,
                action.config,
                policy,
                {} if host_overrides is None else host_overrides,
                {} if restrict is None else restrict,
            )
            if floor.tier == "hard_deny":
                return c.DenyProposal()
            return c.EscalateProposal("input_profile")
        selected = _prepared(request, privacy)
        binding = _bind(
            action,
            selected,
            privacy,
            model,
            policy=policy,
            host_overrides=host_overrides,
            restrict=restrict,
        )
        return _sufficiency_projection(receipt, selected, privacy, model, binding, boot_id)
    except (Refused, Fault, TypeError, ValueError, AttributeError, RecursionError):
        return hold("stale_digest")


def _sufficiency_projection(
    receipt: object,
    selected: c.SufficiencyRequest | c.PermissionRequest,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    binding: str,
    boot_id: str = FAKE_BOOT_ID,
) -> c.Projection:
    """Reuse a binding computed by the caller; this grants no authority or freshness."""
    hold = c.HoldUnavailable
    try:
        if (
            type(receipt) is not Receipt
            or receipt.input_digest != binding
            or receipt.kind != "sufficiency"
            or receipt.failure not in (None, "timeout", "unavailable", "stale_digest", "clock")
            or not isinstance(selected, c.SufficiencyRequest)
        ):
            return hold("stale_digest")
        if not isinstance(selected.lint, c.Passed):
            return disposition("sufficiency", c.Ready(selected), None, privacy)
        if receipt.failure is not None:
            return hold("invalid_host" if receipt.failure == "clock" else receipt.failure)
        attempt = receipt.attempt
        if (
            type(attempt) is not Attempt
            or type(attempt.nonce) is not str
            or len(attempt.nonce) != 32
            or any(ch not in "0123456789abcdef" for ch in attempt.nonce)
            or not safe_label(attempt.boot_id, 128)
            or attempt.boot_id != boot_id
            or any(
                type(v) is not int
                for v in (attempt.started_ns, attempt.ended_ns, attempt.budget_ns, receipt.elapsed_ns)
            )
            or not 0 <= attempt.started_ns <= attempt.ended_ns
            or not 0 < attempt.budget_ns <= DEADLINE_NS
            or receipt.elapsed_ns != attempt.ended_ns - attempt.started_ns
            or not 0 <= receipt.elapsed_ns < attempt.budget_ns
        ):
            return hold("invalid_host")
        reply = receipt.reply
        if (
            type(reply) is not Reply
            or reply.input_digest != receipt.input_digest
            or reply.reported != model
            or reply.nonce != attempt.nonce
            or type(reply.output) is not bytes
        ):
            return hold("stale_digest")
        checked = decode_result(reply.output, selected, privacy)
        return disposition("sufficiency", c.Ready(selected), c.Completed(checked), privacy)
    except (Refused, Fault, TypeError, ValueError, AttributeError, RecursionError):
        return hold("stale_digest")


def _valid_groom_state(state: GroomState, action: Request | None = None) -> bool:
    if action is not None and (
        type(action) is not Request
        or type(action.origin) is not Origin
        or type(action.origin.generation) is not int
        or type(action.groom_revisions) is not int
        or not 0 <= action.groom_revisions <= 2
    ):
        return False
    if (
        type(state) is not GroomState
        or type(state.origin) is not Origin
        or type(state.origin.generation) is not int
        or type(state.failures) is not int
        or not 0 <= state.failures <= 2
        or type(state.assessed) is not tuple
        or type(state.revisions) is not tuple
        or type(state.receipts) is not tuple
        or len(state.assessed) > 3
        or len(state.receipts) != len(state.assessed)
        or any(
            type(r) is not Receipt or r.input_digest != d
            for r, d in zip(state.receipts, state.assessed, strict=True)
        )
        or len(state.assessed) != len(state.revisions)
        or not all(digest_syntax(value) for value in state.assessed)
        or len(set(state.assessed)) != len(state.assessed)
        or any(type(v) is not int or not 0 <= v <= 2 for v in state.revisions)
        or tuple(sorted(set(state.revisions))) != state.revisions
    ):
        return False
    if not state.assessed:
        return (
            state.failures == 0
            and state.key is None
            and state.approval_id is None
            and (
                action is None
                or (
                    same_session(state.origin, action.origin)
                    and action.origin.generation >= state.origin.generation
                )
            )
        )
    if (
        type(state.key) is not CreationKey
        or state.approval_id is None
        or state.revisions[-1] != state.failures
    ):
        return False
    return action is None or (
        state.key == action.key
        and state.approval_id == action.approval_id
        and same_session(state.origin, action.origin)
        and action.origin.generation >= state.origin.generation
        and action.groom_revisions >= state.failures
    )


def record_groom(
    state: GroomState,
    receipt: Receipt,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    *,
    boot_id: str = FAKE_BOOT_ID,
) -> GroomState:
    """Record advice, reconciling already consumed P1 revisions without another groom.

    An outage records no assessment; it cannot refund a P1 revision already consumed
    elsewhere. A later negative assessment at revision two has a fixed concern exit.
    New callers use groom_request to require current negative advice before advancing.
    """
    if type(action) is not Request or not _valid_groom_state(state, action):
        raise Refused("groom-origin-refused")
    binding, proposal = _groom_projection(receipt, action, request, privacy, model, boot_id)
    if not isinstance(proposal, c.NeedsGrooming):
        return state
    if receipt.input_digest in state.assessed:
        return state
    if state.revisions and action.groom_revisions <= state.revisions[-1]:
        raise Refused("groom-budget-refused")
    return GroomState(
        action.origin,
        action.groom_revisions,
        (*state.assessed, binding),
        action.key,
        action.approval_id,
        (*state.revisions, action.groom_revisions),
        (*state.receipts, receipt),
    )


def groom_request(
    state: GroomState,
    receipt: Receipt,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    *,
    boot_id: str = FAKE_BOOT_ID,
    intent: Intent,
    resolved: Resolved,
    source: str,
    origin: Origin,
) -> Request:
    """No session effect: only current recorded negative advice can spend a P1 groom."""
    if type(action) is not Request or not _valid_groom_state(state, action):
        raise Refused("groom-advice-required")
    binding, proposal = _groom_projection(receipt, action, request, privacy, model, boot_id)
    if (
        not state.assessed
        or state.failures != action.groom_revisions
        or state.assessed[-1] != binding
        or state.receipts[-1] != receipt
        or not isinstance(proposal, c.NeedsGrooming)
    ):
        raise Refused("groom-advice-required")
    # Reuse the P1 typed transition; its validation also refuses supplied wrong types.
    return groom(action, intent, resolved, source, origin=origin)


def groom_route(state: GroomState) -> c.ReturnToOrigin | c.CannotApproveNotice:
    """Fixed advice from a coherent assessed state, never from a bare counter."""
    if not _valid_groom_state(state) or not state.assessed:
        raise Refused("groom-budget-refused")
    return groom_disposition(state.failures)


def repin_enrichment(
    receipt: Receipt,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    *,
    whole_ask: bytes,
    context_digest: str,
    boot_id: str = FAKE_BOOT_ID,
) -> Request:
    """Rebuild opaque host-observed ask from a current validated enrichment proposal.

    A future native adapter must prove candidate-to-canonical-ask equivalence. This
    transition enforces distinct bytes/digest, clears sufficiency and charges no groom.
    """
    proposal = current_projection(receipt, action, request, privacy, model, boot_id=boot_id)
    if not isinstance(proposal, c.EnrichmentProposal):
        raise Refused("enrichment-proposal-required")
    return repin_enriched_ask(action, whole_ask, context_digest, digest(proposal.candidate))


ConcernCode = Literal["missing-context", "ambiguous-target", "unsupported-effect", "lint-failed"]


class Concern(Record, frozen=True):
    """Inert fixed-label public projection, not a question, denial or consent token."""

    reason: ConcernCode
    digest_prefix: str
    choices: tuple[Literal["groom"], Literal["deny"]] = ("groom", "deny")
    recommended: Literal["deny"] = "deny"


def concern(
    state: GroomState,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    reason: ConcernCode,
    *,
    boot_id: str = FAKE_BOOT_ID,
) -> Concern:
    """After two P1 revisions and current negative advice, expose only fixed labels."""
    if type(action) is not Request or not _valid_groom_state(state, action) or not state.receipts:
        raise Refused("concern-refused")
    binding, proposal = _groom_projection(state.receipts[-1], action, request, privacy, model, boot_id)
    if (
        state.failures != 2
        or action.groom_revisions != 2
        or state.assessed[-1] != binding
        or not isinstance(proposal, c.NeedsGrooming)
    ):
        raise Refused("concern-refused")
    derived = _concern_reason(proposal.gaps)
    if reason != derived:
        raise Refused("concern-refused")
    return Concern(derived, binding[:12])


def _groom_projection(
    receipt: Receipt,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    boot_id: str,
) -> tuple[str, c.Projection]:
    """Validate receipt identity even when native lint supplies the negative advice."""
    if type(receipt) is not Receipt:
        raise Refused("groom-receipt-refused")
    selected = _prepared(request, privacy)
    binding = _bind(action, selected, privacy, model)
    if receipt.input_digest != binding or receipt.kind != "sufficiency":
        raise Refused("groom-receipt-refused")
    return binding, _sufficiency_projection(receipt, selected, privacy, model, binding, boot_id)


def _concern_reason(gaps: tuple[c.Gap, ...]) -> ConcernCode:
    """Fixed deterministic labels; never copy model prose to the public concern."""
    if any(gap.code == "native_lint" for gap in gaps):
        return "lint-failed"
    if any(
        gap.field
        in {
            "/context",
            "/candidate/title",
            "/candidate/description",
            "/candidate/ask/why",
            "/candidate/ask/excludes",
            "/candidate/ask/risks",
        }
        for gap in gaps
    ):
        return "missing-context"
    if any(gap.field in {"/candidate/ask/refs"} for gap in gaps):
        return "ambiguous-target"
    return "unsupported-effect"
