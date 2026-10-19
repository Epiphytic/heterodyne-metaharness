"""Inactive Plan7A records: host facts and model proposals are separate namespaces."""

from typing import Literal, Protocol

import msgspec

HostCode = Literal[
    "timeout", "unavailable", "unreadable_policy", "stale_digest", "egress_unsafe", "egress_oversize"
]
CheckCode = Literal["schema", "unsafe", "oversize", "binding", "invariant"]


class Record(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    pass


class PrivacyContext(Record, frozen=True):
    format: Literal[1]
    login_dirs: tuple[str, ...]
    rule_revision: str
    sha256: str


class PolicyRevision(Record, frozen=True):
    id: str
    sha256: str


class Origin(Record, frozen=True):
    event_ref: str
    creator_ref: str
    session_ref: str | None


class FileRef(Record, tag="file", tag_field="kind", frozen=True):
    id: str
    repo: str
    path: str


class CommitRef(Record, tag="commit", tag_field="kind", frozen=True):
    id: str
    repo: str


class RangeRef(Record, tag="range", tag_field="kind", frozen=True):
    id: str
    repo: str


class BeadRef(Record, tag="bead", tag_field="kind", frozen=True):
    id: str


type NativeRef = FileRef | CommitRef | RangeRef | BeadRef


class NativeAsk(Record, frozen=True):
    effect: object
    excludes: object
    why: object
    risks: object
    refs: tuple[NativeRef, ...]
    targets: tuple[str, ...] | msgspec.UnsetType = msgspec.UNSET


class Candidate(Record, frozen=True):
    title: str
    description: str
    ask: NativeAsk


class ResolvedRef(Record, frozen=True):
    ref: NativeRef
    resolved_id: str | None


class ContextItem(Record, frozen=True):
    source_id: str
    kind: Literal["task", "doc", "diff", "excerpt"]
    content_id: str
    text: str
    ref: NativeRef
    resolved_id: str


class RepoView(Record, frozen=True):
    label: str


class CwdView(Record, frozen=True):
    repo: RepoView
    path: str


class TargetView(Record, frozen=True):
    workstream: str
    repo: RepoView


class RefRepository(Record, frozen=True):
    ref_index: int
    repo: RepoView


class PublicBindings(Record, frozen=True):
    target: TargetView | None
    ref_repositories: tuple[RefRepository, ...]
    cwd: CwdView | None


class Target(Record, frozen=True):
    workstream_id: str
    repo_id: str


class OpenTask(Record, frozen=True):
    id: str
    title: str
    description_digest: str


class Gap(Record, frozen=True):
    code: Literal[
        "schema", "profile", "unsafe", "oversize", "binding", "invariant", "native_lint", "semantic"
    ]
    field: str
    detail: str | None


class Passed(Record, tag="passed", tag_field="status", frozen=True):
    gaps: tuple[Gap, ...]


class Failed(Record, tag="failed", tag_field="status", frozen=True):
    gaps: tuple[Gap, ...]


class UnavailableLint(Record, tag="unavailable", tag_field="status", frozen=True):
    reason_code: HostCode


type Lint = Passed | Failed | UnavailableLint


class Addition(Record, frozen=True):
    source_id: str
    start: int
    end: int
    quote: str


class Unused(Record, tag="unused", tag_field="status", frozen=True):
    pass


class Used(Record, tag="used", tag_field="status", frozen=True):
    base_description: str
    context_source_ids: tuple[str, ...]
    additions: tuple[Addition, ...]


class Subject(Record, frozen=True):
    task_id: str
    session_ref: str
    tool_name: str


class Invocation(Record, frozen=True):
    cwd_id: str
    input: object
    sha256: str


class Request(Record, frozen=True):
    format: Literal[1]
    request_id: str
    workstream_id: str
    subject_id: str
    revision: int
    policy_revision: PolicyRevision
    privacy_sha256: str
    context: tuple[ContextItem, ...]
    public_bindings: PublicBindings
    snapshot_sha256: str


class IntakeRequest(Request, tag="intake", tag_field="kind", frozen=True):
    original: str
    origin: Origin
    target: Target
    context_complete: bool
    open_tasks: tuple[OpenTask, ...]


class SufficiencyRequest(Request, tag="sufficiency", tag_field="kind", frozen=True):
    approval_id: str
    native_context_digest: str | None
    lint: Lint
    candidate: Candidate
    resolved_refs: tuple[ResolvedRef, ...]
    origin: Origin
    submission_id: str
    submission_revision: int
    base_candidate_sha256: str
    enrichment: Unused | Used


class PermissionRequest(Request, tag="permission", tag_field="kind", frozen=True):
    subject: Subject
    intent: str
    invocation: Invocation
    effective_budget_ms: int
    elapsed_ms: int


type AnyRequest = IntakeRequest | SufficiencyRequest | PermissionRequest


class ChangeFlags(Record, frozen=True):
    target_changed: bool
    requirements_changed: bool
    question_to_action: bool
    destructive: bool
    outward_facing: bool
    context_incomplete: bool


class Dependency(Record, frozen=True):
    task_id: str
    type: Literal["blocks"]


class Proposed(Record, tag="proposed", tag_field="status", frozen=True):
    materiality: Literal["minor", "material"]
    title: str
    description: str
    acceptance: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    change_flags: ChangeFlags
    reason: str


class ModelUnavailable(Record, tag="unavailable", tag_field="status", frozen=True):
    pass


class Sufficient(Record, tag="sufficient", tag_field="status", frozen=True):
    reason: str
    additions: tuple[Addition, ...]
    gaps: tuple[Gap, ...]


class Enrich(Record, tag="enrich", tag_field="status", frozen=True):
    additions: tuple[Addition, ...]
    reason: str
    gaps: tuple[Gap, ...]


class SemanticGap(Record, frozen=True):
    field: Literal["what", "effect", "excludes", "why", "context", "risks", "alternatives", "refs"]
    detail: str


class Groom(Record, tag="groom", tag_field="status", frozen=True):
    gaps: tuple[SemanticGap, ...]
    reason: str
    additions: tuple[Addition, ...]


class Allow(Record, tag="allow", tag_field="status", frozen=True):
    reason: str


class Deny(Record, tag="deny", tag_field="status", frozen=True):
    reason: str


class Escalate(Record, tag="escalate", tag_field="status", frozen=True):
    reason: str


class Judgment(Record, frozen=True):
    format: Literal[1]
    request_id: str
    revision: int
    snapshot_sha256: str


class IntakeJudgment(Judgment, tag="intake", tag_field="kind", frozen=True):
    outcome: Proposed | ModelUnavailable


class SufficiencyJudgment(Judgment, tag="sufficiency", tag_field="kind", frozen=True):
    outcome: Sufficient | Enrich | Groom | ModelUnavailable


class PermissionJudgment(Judgment, tag="permission", tag_field="kind", frozen=True):
    outcome: Allow | Deny | Escalate | ModelUnavailable


type AnyJudgment = IntakeJudgment | SufficiencyJudgment | PermissionJudgment


class Valid[T](Record, tag="valid", tag_field="status", frozen=True):
    value: T


class Invalid(Record, tag="invalid", tag_field="status", frozen=True):
    stage: Literal["request", "result"]
    code: CheckCode
    field: str


type Check[T] = Valid[T] | Invalid


class Ready[T](Record, tag="ready", tag_field="status", frozen=True):
    value: T


class OriginGap(Record, tag="origin_gap", tag_field="status", frozen=True):
    gaps: tuple[Gap, ...]


class HostFailed(Record, tag="host_failed", tag_field="status", frozen=True):
    reason_code: HostCode


class InvalidHost(Record, tag="invalid_host", tag_field="status", frozen=True):
    field: str


type Prepared[T] = Ready[T] | OriginGap | HostFailed | InvalidHost


class Completed[T](Record, tag="completed", tag_field="status", frozen=True):
    result: Check[T]


type JudgmentAttempt[T] = Completed[T] | HostFailed


class HoldInvalidInput(Record, tag="HoldInvalidInput", tag_field="status", frozen=True):
    gaps: tuple[Gap, ...]


class NeedsGrooming(Record, tag="NeedsGrooming", tag_field="status", frozen=True):
    gaps: tuple[Gap, ...]


ReasonCode = (
    HostCode
    | Literal[
        "invalid_host",
        "input_profile",
        "missing_attempt",
        "bad_output",
        "model_unavailable",
        "model_escalate",
        "deadline",
    ]
)


class HoldUnavailable(Record, tag="HoldUnavailable", tag_field="status", frozen=True):
    reason_code: ReasonCode


class EscalateProposal(Record, tag="EscalateProposal", tag_field="status", frozen=True):
    reason_code: ReasonCode


class EnrichmentProposal(Record, tag="EnrichmentProposal", tag_field="status", frozen=True):
    candidate: Candidate


# Empty proposals have no native authority or executable payload.
class CreateMinorProposal(Record, tag="CreateMinorProposal", tag_field="status", frozen=True):
    pass


class ConfirmMaterialProposal(Record, tag="ConfirmMaterialProposal", tag_field="status", frozen=True):
    pass


class RawUnreviewedProposal(Record, tag="RawUnreviewedProposal", tag_field="status", frozen=True):
    pass


class RawConfirmProposal(Record, tag="RawConfirmProposal", tag_field="status", frozen=True):
    pass


class SufficientProposal(Record, tag="SufficientProposal", tag_field="status", frozen=True):
    pass


class ReturnToOrigin(Record, tag="ReturnToOrigin", tag_field="status", frozen=True):
    pass


class CannotApproveNotice(Record, tag="CannotApproveNotice", tag_field="status", frozen=True):
    pass


class AllowProposal(Record, tag="AllowProposal", tag_field="status", frozen=True):
    pass


class DenyProposal(Record, tag="DenyProposal", tag_field="status", frozen=True):
    pass


type Projection = (
    HoldInvalidInput
    | NeedsGrooming
    | HoldUnavailable
    | EscalateProposal
    | EnrichmentProposal
    | CreateMinorProposal
    | ConfirmMaterialProposal
    | RawUnreviewedProposal
    | RawConfirmProposal
    | SufficientProposal
    | ReturnToOrigin
    | CannotApproveNotice
    | AllowProposal
    | DenyProposal
)


class ModelPort(Protocol):
    """Only fake ports are supplied; no provider authentication/invocation is installed."""

    async def classify(self, request: dict[str, object]) -> bytes: ...
    async def assess(self, request: dict[str, object]) -> bytes: ...
    async def permission(self, request: dict[str, object]) -> bytes: ...
