"""Immutable version-one presentation inputs and descriptive results; no authority handles."""

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, TypeVar

Source = Literal["configuration", "beads", "wsd-journal", "account-usage"]
Kind = Literal[
    "task",
    "workstreams",
    "workstream-status",
    "control-status",
    "bead-status",
    "approvals",
    "queue",
    "approval",
    "question",
    "fyi",
    "notice",
]
Query = Literal["Workstreams", "Status", "BeadStatus", "Approvals", "Queue", "QueueFor"]
Trust = Literal["trusted", "untrusted", "unknown"]
TaskState = Literal[
    "received",
    "running",
    "review",
    "parked",
    "deferred",
    "needs-operator",
    "closed",
    "failed",
    "held",
    "unknown",
]
StreamState = Literal["running", "idle", "all-blocked", "deferred", "paused", "held", "stuck", "unknown"]
CollectionName = Literal[
    "configuration",
    "state",
    "holds",
    "running",
    "ready",
    "parked",
    "held",
    "deferred",
    "approvals",
    "needs-human",
    "candidates",
    "pause",
    "recent-events",
    "usage",
    "decisions",
    "transcripts",
    "launches",
    "closed-yesterday",
    "open-approval-labels",
    "idle-workstreams",
    "accounts-hit-limit",
]


@dataclass(frozen=True)
class SectionKey:
    source: Source
    collection: CollectionName
    workstream: str | None = None


@dataclass(frozen=True)
class Count:
    shown: int
    total: int


@dataclass(frozen=True)
class SectionStatus:
    key: SectionKey
    observed_at: datetime | None
    availability: Literal["reachable", "cached-unreachable", "unavailable"]
    completeness: Literal["complete", "incomplete", "unavailable"]
    refresh: Literal["pending", "idle"]
    count: Count | None
    order: Literal["none", "unknown", "approvals-created-key", "owner-queue-order"]
    required: bool


T = TypeVar("T")


@dataclass(frozen=True)
class Rows(Generic[T]):  # noqa: UP046 - runtime closed-schema substitution uses this TypeVar
    key: SectionKey
    items: tuple[T, ...]


@dataclass(frozen=True)
class EventOrigin:
    pass


@dataclass(frozen=True)
class QueryOrigin:
    query: Query
    workstream: str | None = None
    bead: str | None = None


Origin = EventOrigin | QueryOrigin


@dataclass(frozen=True)
class Quota:
    resume_at: datetime | None


@dataclass(frozen=True)
class AccountChanged:
    pass


@dataclass(frozen=True)
class Held:
    reason: Literal["operator-stop", "escalation"]
    explanation: str


@dataclass(frozen=True)
class Blocked:
    explanation: str


@dataclass(frozen=True)
class Unknown:
    pass


Reason = Quota | AccountChanged | Held | Blocked | Unknown | None


@dataclass(frozen=True)
class Task:
    bead: str
    title: str
    agent: str
    role: str
    branch: str
    state: TaskState
    reason: Reason
    duration_us: int | None


@dataclass(frozen=True)
class RoleHeadroom:
    role: str
    account: str | None
    remaining_percent: int | None
    trust: Trust
    observed_at: datetime | None


@dataclass(frozen=True)
class WorkstreamRow:
    name: str
    state: StreamState
    current_bead: str | None
    ready: int | None
    parked: int | None
    held: int | None
    deferred: int | None
    open_approval: int | None
    role_headroom: tuple[RoleHeadroom, ...]
    reason: Reason


@dataclass(frozen=True)
class ApprovalSummary:
    bead: str
    title: str
    status: Literal["open", "blocked", "closed", "unknown"]
    created_at: datetime
    opaque_order_key: str


@dataclass(frozen=True)
class NeedsHuman:
    bead: str
    title: str
    reason: Reason


@dataclass(frozen=True)
class AccountUsage:
    account: str
    role: str
    launch_label: str | None
    window: str
    used_percent: int | None
    remaining_percent: int | None
    reset_at: datetime | None
    observed_at: datetime | None
    trust: Trust


@dataclass(frozen=True)
class Event:
    sequence: int
    timestamp: datetime
    kind: Literal["started", "parked", "resumed", "review", "closed", "failed", "unknown"]
    summary: str


@dataclass(frozen=True)
class DecisionSummary:
    timestamp: datetime | None
    kind: Literal["approval", "question", "unknown"]
    status: Literal["open", "approved", "denied", "unknown"]
    actor_label: str | None
    via: Literal["marmot", "native-record", "unknown"]
    summary: str


@dataclass(frozen=True)
class Launch:
    sequence: int
    role: str
    account_label: str | None
    trust: Trust
    observed_at: datetime | None


@dataclass(frozen=True)
class Candidate:
    bead: str
    title: str
    blocker_labels: tuple[str, ...]
    eligibility: Literal["known", "unknown"]
    segment: Literal["resumable", "native-ready"]
    ordinal: int


@dataclass(frozen=True)
class Workstreams:
    workstreams: Rows[WorkstreamRow]


@dataclass(frozen=True)
class WorkstreamStatus:
    name: str
    state: StreamState
    running: Rows[Task]
    ready: Rows[Task]
    parked: Rows[Task]
    held: Rows[Task]
    deferred: Rows[Task]
    approvals: Rows[ApprovalSummary]
    usage: Rows[AccountUsage]


@dataclass(frozen=True)
class ControlStatus:
    workstreams: Rows[WorkstreamRow]
    approvals: Rows[ApprovalSummary]
    needs_human: Rows[NeedsHuman]
    usage: Rows[AccountUsage]


@dataclass(frozen=True)
class BeadStatus:
    bead: str
    title: str
    state: TaskState
    branch: str | None
    session_label: str | None
    recent_events: Rows[Event]
    decisions: Rows[DecisionSummary]
    transcript_labels: Rows[str]
    launches: Rows[Launch]


@dataclass(frozen=True)
class Approvals:
    approvals: Rows[ApprovalSummary]


@dataclass(frozen=True)
class Queue:
    candidates: Rows[Candidate]
    paused: bool | None


@dataclass(frozen=True)
class Repo:
    forge: Literal["github", "gitlab", "other"]
    origin: str
    owner: str
    name: str


@dataclass(frozen=True)
class File:
    repo: Repo
    commit: str
    path: str
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class Commit:
    repo: Repo
    commit: str


@dataclass(frozen=True)
class PullRequest:
    repo: Repo
    number: int
    head: str


@dataclass(frozen=True)
class Range:
    repo: Repo
    base: str
    head: str


@dataclass(frozen=True)
class Inline:
    title: str
    full_text: str


@dataclass(frozen=True)
class OperatorDoc:
    title: str
    content_digest12: str


@dataclass(frozen=True)
class BeadContext:
    short_bead: str
    digest12: str
    accessible_detail: File | Inline


EvidenceRef = File | Commit | PullRequest | Range | Inline | OperatorDoc | BeadContext


@dataclass(frozen=True)
class LiteralText:
    text: str


@dataclass(frozen=True)
class PublicHash:
    algorithm: Literal["sha1", "sha256"]
    text: str


@dataclass(frozen=True)
class EffectText:
    spans: tuple[LiteralText | PublicHash, ...]


@dataclass(frozen=True)
class Approval:
    bead: str
    purpose: Literal["design", "execution"] | None
    what: str
    exact_effect: EffectText
    excludes: str
    why: str
    context_summary: str
    evidence: tuple[EvidenceRef, ...]
    digest12: str
    lifecycle: Literal["current", "stale", "superseded"]
    content_revision: str
    digest_revision: str
    source_eligible: bool
    answer_policy: Literal["wsd-standard", "unsupported"]


@dataclass(frozen=True)
class Choice:
    label: str
    description: str


@dataclass(frozen=True)
class PlainQuestion:
    title: str
    context: str
    question: str
    answer_instruction: str


@dataclass(frozen=True)
class ChoiceQuestion:
    title: str
    context: str
    question: str
    choices: tuple[Choice, ...]
    recommended_label: str | None


@dataclass(frozen=True)
class Question:
    lifecycle: Literal["current", "stale", "superseded"]
    content_revision: str
    digest_revision: str
    digest12: str
    source_eligible: bool
    body: PlainQuestion | ChoiceQuestion


@dataclass(frozen=True)
class Fyi:
    title: str
    body: str


@dataclass(frozen=True)
class AllBlocked:
    workstream: str
    blocked_beads_count: int
    approval_count: int
    reason: str


@dataclass(frozen=True)
class AllAccountsExhausted:
    workstream: str
    role: str
    reset: datetime | None


@dataclass(frozen=True)
class DeliveryUnconfirmed:
    card_safe_label: str


@dataclass(frozen=True)
class Reminder:
    card_safe_label: str
    reference: File | Commit


@dataclass(frozen=True)
class Digest:
    closed_yesterday: Rows[str]
    open_approval_labels: Rows[str]
    needs_human: Rows[str]
    idle_workstreams: Rows[str]
    accounts_that_hit_a_limit: Rows[str]


@dataclass(frozen=True)
class TrustSuspended:
    pass


WithheldReason = Literal[
    "missing-required",
    "unsafe-content",
    "stale-card",
    "incoherent-revision",
    "ineligible",
    "unsupported-answer-policy",
    "unsupported-reference",
    "missing-mention",
    "content-too-large",
    "content-too-wide",
    "required-source",
]
TerminalReason = Literal["busy", "usage", "not-found", "unsupported", "unavailable", "narrow-query"]


@dataclass(frozen=True)
class CannotApprove:
    reason: WithheldReason


@dataclass(frozen=True)
class Terminal:
    reason: TerminalReason


Notice = (
    AllBlocked
    | AllAccountsExhausted
    | AccountChanged
    | DeliveryUnconfirmed
    | Reminder
    | Digest
    | TrustSuspended
    | CannotApprove
    | Terminal
)
Payload = (
    Task
    | Workstreams
    | WorkstreamStatus
    | ControlStatus
    | BeadStatus
    | Approvals
    | Queue
    | Approval
    | Question
    | Fyi
    | Notice
)


@dataclass(frozen=True)
class Available:
    kind: Kind
    origin: Origin
    rendered_at: datetime
    sections: tuple[SectionStatus, ...]
    payload: Payload
    version: int = 1


@dataclass(frozen=True)
class Missing:
    kind: Kind
    origin: Origin
    version: int = 1


PresentationView = Available | Missing


@dataclass(frozen=True)
class DisplayLabel:
    key: str
    label: str


@dataclass(frozen=True)
class RenderPolicy:
    max_chars: int = 2000
    operator_npub: str | None = None
    login_dir_forms: tuple[str, ...] = ()
    session_labels: tuple[DisplayLabel, ...] = ()
    transcript_labels: tuple[DisplayLabel, ...] = ()
    forges: tuple[Repo, ...] = ()


@dataclass(frozen=True)
class Text:
    body: str


@dataclass(frozen=True)
class ReactionAdded:
    glyph: str


@dataclass(frozen=True)
class ReactionRemoved:
    glyph: str


@dataclass(frozen=True)
class ClassificationInput:
    surface: Literal["workstream", "control"]
    ingress: Literal["unverified", "rejected", "replayed", "verified"]
    suspended: bool
    event: Text | ReactionAdded | ReactionRemoved
    placement: Literal["root", "thread"]
    target: Literal["none", "stale", "unknown", "current-task", "current-approval", "current-question"]
    version: int = 1


@dataclass(frozen=True)
class Invalid:
    code: str


@dataclass(frozen=True)
class Classification:
    kind: Literal[
        "Ignored",
        "DeferredDecision",
        "ReadQuery",
        "InvalidCommand",
        "UnsupportedIn6A",
        "DeferredIntake",
        "DeferredSteering",
        "UnresolvedTarget",
    ]
    reason: str
    selector: str | None = None


@dataclass(frozen=True)
class Rendered:
    parts: tuple[str, ...]
    kind: Kind
    complete: bool
    collection_counts: tuple[tuple[SectionKey, Count | None], ...]


@dataclass(frozen=True)
class NeedsDetails:
    reason: Literal["excessive-parts", "content-too-wide"]
    text: str = "Too much detail for this view. No partial content shown."


@dataclass(frozen=True)
class Withheld:
    reason: WithheldReason
    text: str


@dataclass(frozen=True)
class Unavailable:
    reason: Literal["missing-snapshot", "required-source"]
    text: str = "Data unavailable; try again later."


@dataclass(frozen=True)
class Pair:
    workstream_single_part: str
    control_single_part: str


RenderResult = Invalid | Rendered | NeedsDetails | Withheld | Unavailable
CardPairResult = Invalid | Withheld | Pair
ClassificationResult = Invalid | Classification
