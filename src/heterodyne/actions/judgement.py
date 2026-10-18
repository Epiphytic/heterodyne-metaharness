"""Pure P2 judgement receipts; fake-provider port only, never consent or dispatch.

Receipts are serializable evidence for a future owning journal, not durable storage here.
The supplied P1/native snapshot and model identity are host attestations. This module
neither authenticates their source nor claims that an opaque ask is a native canonical ask.
"""

import asyncio
import hashlib
import time
from collections.abc import Callable
from typing import Literal, Protocol

import msgspec

from heterodyne.actions.codec import assemble_envelope, safe_label, validate_request_inputs
from heterodyne.actions.contracts import Origin, PolicyFacts, Record
from heterodyne.actions.policy import denial_key, evaluate_policy
from heterodyne.actions.requests import Refused, Request, digest_syntax, same_session, source_syntax
from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.project import disposition, groom_disposition
from heterodyne.gatekeeper.validate import Fault, canonical, data, decode_result, digest, egress, prepare

DEADLINE_NS = 60_000_000_000


class ModelSpec(Record, frozen=True):
    requested: str
    schema: Literal["plan5-judgement-v1"]
    release: str


class Invocation(Record, frozen=True):
    input_digest: str
    model: ModelSpec
    public_input: bytes


class Reply(Record, frozen=True):
    input_digest: str
    reported: ModelSpec
    output: bytes


class Provider(Protocol):
    async def judge(self, invocation: Invocation) -> Reply: ...


class Receipt(Record, frozen=True):
    input_digest: str
    kind: Literal["sufficiency", "permission"]
    elapsed_ns: int
    reply: Reply | None
    failure: Literal["timeout", "unavailable", "stale_digest"] | None


class GroomState(Record, frozen=True):
    origin: Origin
    failures: int = 0
    assessed: tuple[str, ...] = ()


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
) -> str:
    """Bind exact whole ask, P1 state, source pins, policy, model and judgement schema.

    The native context digest remains opaque; an eventual native adapter must prove it.
    No snapshot from an earlier revision can be used to certify a later one.
    """
    selected = _prepared(request, privacy)
    try:
        if (
            type(action) is not Request
            or type(action.ask_bytes) is not bytes
            or not 1 <= len(action.ask_bytes) <= 65536
        ):
            raise Refused("judgement-binding-refused")
        action = msgspec.structs.replace(
            msgspec.convert(data(msgspec.structs.replace(action, ask_bytes=None)), type=Request, strict=True),
            ask_bytes=action.ask_bytes,
        )
        model = msgspec.convert(data(model), type=ModelSpec, strict=True)
        for label in (model.requested, model.schema, model.release):
            if not safe_label(label, 128):
                raise Refused("judgement-model-refused")
        if (
            type(action) is not Request
            or action.phase not in {"ask-pinned", "sufficient", "filed"}
            or type(action.ask_bytes) is not bytes
            or not 1 <= len(action.ask_bytes) <= 65536
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
            evaluate_policy(action.envelope, action.config, policy, {}, {})
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
        )
        return digest(
            dict(
                domain="plan5-p2-v1",
                state=state,
                whole_ask=hashlib.sha256(action.ask_bytes).hexdigest(),
                judgement=selected,
                model=model,
                privacy=privacy,
                policy=policy,
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
    policy: PolicyFacts | None = None,
) -> Receipt:
    """One bounded attempt. Cancellation propagates; provider failures never expose prose.

    A cooperative async fake port is required. No thread, service or actual model adapter
    is installed. A future service adapter must enforce its own resource cancellation.
    """
    selected = _prepared(request, privacy)
    identity = bind(action, selected, privacy, model, policy=policy)
    kind: Literal["sufficiency", "permission"] = (
        "sufficiency" if isinstance(selected, c.SufficiencyRequest) else "permission"
    )
    if isinstance(selected, c.SufficiencyRequest) and not isinstance(selected.lint, c.Passed):
        return Receipt(identity, kind, 0, None, "unavailable")
    if isinstance(selected, c.PermissionRequest) and policy is not None and policy.widens_permissions:
        return Receipt(identity, kind, 0, None, "unavailable")
    public = egress(selected, privacy)
    if not isinstance(public, c.Valid):
        return Receipt(identity, kind, 0, None, "unavailable")
    try:
        start = _sample(clock)
    except (Refused, TypeError, ValueError, OverflowError, OSError):
        return Receipt(identity, kind, 0, None, "unavailable")
    budget = DEADLINE_NS
    if isinstance(selected, c.PermissionRequest):
        budget = min(budget, max(0, selected.effective_budget_ms - selected.elapsed_ms) * 1_000_000)
    if budget == 0:
        return Receipt(identity, kind, 0, None, "timeout")
    deadline = asyncio.timeout(budget / 1_000_000_000)
    try:
        async with deadline:
            reply = await provider.judge(Invocation(identity, model, canonical(public.value)))
    except TimeoutError:
        return Receipt(identity, kind, budget, None, "timeout")
    except Exception:
        return Receipt(identity, kind, 0, None, "unavailable")
    try:
        elapsed = _sample(clock) - start
    except (Refused, TypeError, ValueError, OverflowError, OSError):
        return Receipt(identity, kind, 0, None, "unavailable")
    if deadline.expired() or elapsed < 0 or elapsed >= budget:
        return Receipt(identity, kind, max(0, elapsed), None, "timeout")
    if (
        type(reply) is not Reply
        or reply.input_digest != identity
        or reply.reported != model
        or type(reply.output) is not bytes
        or len(reply.output) > 32768
    ):
        return Receipt(identity, kind, elapsed, None, "stale_digest")
    return Receipt(identity, kind, elapsed, reply, None)


def current_projection(
    receipt: Receipt,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
    *,
    policy: PolicyFacts | None = None,
) -> c.Projection:
    """Revalidate on EVERY use. Permission proposals cannot defeat deterministic P0 policy.

    Even AllowProposal has no effect port or authorization meaning. P0 hard-deny and
    outward-floor policy remain mandatory before any future adapter considers a proposal.
    """
    kind = "permission" if isinstance(request, c.PermissionRequest) else "sufficiency"
    hold = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
    try:
        selected = _prepared(request, privacy)
        kind = "permission" if isinstance(selected, c.PermissionRequest) else "sufficiency"
        hold = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
        if (
            type(receipt) is not Receipt
            or receipt.input_digest != bind(action, selected, privacy, model, policy=policy)
            or receipt.kind != kind
            or receipt.failure not in (None, "timeout", "unavailable", "stale_digest")
            or type(receipt.elapsed_ns) is not int
            or not 0 <= receipt.elapsed_ns < DEADLINE_NS
        ):
            return hold("stale_digest")
        if isinstance(selected, c.SufficiencyRequest) and not isinstance(selected.lint, c.Passed):
            return disposition(kind, c.Ready(selected), None, privacy)
        if isinstance(selected, c.PermissionRequest):
            if policy is not None and policy.widens_permissions:
                return c.DenyProposal()
            remaining = max(0, selected.effective_budget_ms - selected.elapsed_ms) * 1_000_000
            if receipt.elapsed_ns >= remaining:
                return hold("deadline")
        if receipt.failure is not None:
            return hold(receipt.failure)
        reply = receipt.reply
        if (
            type(reply) is not Reply
            or reply.input_digest != receipt.input_digest
            or reply.reported != model
            or type(reply.output) is not bytes
        ):
            return hold("stale_digest")
        checked = decode_result(reply.output, selected, privacy)
        return disposition(kind, c.Ready(selected), c.Completed(checked), privacy)
    except (Refused, Fault, TypeError, ValueError, AttributeError, RecursionError):
        return hold("stale_digest")


def record_groom(
    state: GroomState,
    receipt: Receipt,
    action: Request,
    request: object,
    privacy: c.PrivacyContext,
    model: ModelSpec,
) -> GroomState:
    """Record initial advice, then charge only failed actual groom revisions (maximum two).

    This returns inert state, not a session-resume capability. P1.groom performs the
    actual pure revision transition and clears prior whole-ask/sufficiency bindings.
    """
    if (
        type(state) is not GroomState
        or type(state.failures) is not int
        or not 0 <= state.failures <= 2
        or len(state.assessed) > 3
        or (len(state.assessed) != state.failures + 1 and (state.failures != 0 or state.assessed))
        or len(set(state.assessed)) != len(state.assessed)
        or not all(digest_syntax(value) for value in state.assessed)
        or not same_session(state.origin, action.origin)
        or action.origin.generation < state.origin.generation
    ):
        raise Refused("groom-origin-refused")
    proposal = current_projection(receipt, action, request, privacy, model)
    if not isinstance(proposal, c.NeedsGrooming):
        return state
    if receipt.input_digest in state.assessed:
        return state
    if state.failures == 2 or action.groom_revisions != len(state.assessed):
        raise Refused("groom-budget-refused")
    return GroomState(action.origin, action.groom_revisions, (*state.assessed, receipt.input_digest))


def groom_route(state: GroomState) -> c.ReturnToOrigin | c.CannotApproveNotice:
    """Fixed bounded advice only; silence/unknown is never an authenticated denial."""
    if type(state.failures) is not int or not 0 <= state.failures <= 2:
        raise Refused("groom-budget-refused")
    return groom_disposition(state.failures)


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
) -> Concern:
    """After two actual groom assessments, expose no model prose or private source pins."""
    if (
        type(state) is not GroomState
        or reason not in ("missing-context", "ambiguous-target", "unsupported-effect", "lint-failed")
        or state.failures != 2
        or len(state.assessed) != 3
        or len(set(state.assessed)) != 3
        or not all(digest_syntax(value) for value in state.assessed)
        or not same_session(state.origin, action.origin)
        or action.origin.generation < state.origin.generation
    ):
        raise Refused("concern-refused")
    return Concern(reason, bind(action, request, privacy, model)[:12])
