"""Stage 0 values only; possession of a record proves no origin or approval authority."""

from typing import Literal

import msgspec

ObjectFormat = Literal["sha1", "sha256"]
ActionKind = Literal["push_branch", "open_pr", "merge_pr", "deploy", "notify"]
Tier = Literal["auto_approve", "escalate", "hard_deny"]


class Record(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    pass


class Clock(Record, frozen=True):
    boot_id: str
    mono_ns: int
    utc_ns: int


class BodyRef(Record, frozen=True):
    commit: str
    path: str


class PushBranch(Record, tag="push_branch", frozen=True):
    repo: str
    from_ref: str
    to_ref: str
    expected_sha: str


class OpenPR(Record, tag="open_pr", frozen=True):
    repo: str
    head: str
    base: str
    title: str
    body_ref: BodyRef


class MergePR(Record, tag="merge_pr", frozen=True):
    repo: str
    pr_number: int
    expected_head_sha: str
    method: Literal["merge", "squash", "rebase"]


class Deploy(Record, tag="deploy", frozen=True):
    target: str


class Notify(Record, tag="notify", frozen=True):
    channel_ref: str
    text: str


Action = PushBranch | OpenPR | MergePR | Deploy | Notify


class Intent(Record, frozen=True):
    schema_version: Literal[1]
    action: Action
    context: str = ""


class Origin(Record, frozen=True):
    workstream: str
    bead: str
    session_key: str
    generation: int
    repo_alias: str


class Repository(Record, frozen=True):
    alias: str
    repository_id: str
    remote_id: str
    object_format: ObjectFormat
    mapping_revision: str


class PushFacts(Record, tag="push_branch", frozen=True):
    repository: Repository
    source_bead: str
    owned_branch: str
    destination_ref: str
    source_sha: str


class PRFacts(Record, tag="open_pr", frozen=True):
    repository: Repository
    head_sha: str


class MergeFacts(Record, tag="merge_pr", frozen=True):
    repository: Repository
    pr_number: int
    head_sha: str
    base_ref: str


class Deployment(Record, frozen=True):
    alias: str
    target_id: str
    definition_revision: str
    artifact_id: str
    artifact_sha256: str


class DeployFacts(Record, tag="deploy", frozen=True):
    target: Deployment


class Channel(Record, frozen=True):
    alias: str
    channel_id: str
    definition_revision: str


class NotifyFacts(Record, tag="notify", frozen=True):
    channel: Channel


Resolved = PushFacts | PRFacts | MergeFacts | DeployFacts | NotifyFacts


class ActionConfig(Record, frozen=True):
    repositories: tuple[Repository, ...] = ()
    deployments: tuple[Deployment, ...] = ()
    channels: tuple[Channel, ...] = ()


class Envelope(Record, frozen=True):
    schema_version: Literal[1]
    action: Action
    resolved: Resolved
    origin: Origin
    idempotency_key: str
    request_revision: int


class Interpretation(Record, frozen=True):
    """Caller-provided semantic projection, not a classifier or authentication port."""

    workstream: str
    repository_id: str
    requirements: tuple[str, ...]
    kind: Literal["question", "action"]
    outward: bool
    destructive: bool
    unique_resolution: bool = False


class PolicyFacts(Record, frozen=True):
    """Facts from future trusted admission; never accept these from an agent request."""

    revision: str
    widens_permissions: bool = True


class PolicyResult(Record, frozen=True):
    tier: Tier
    reason: Literal["permissions-widening", "policy", "outward-floor"]


class Materiality(Record, frozen=True):
    result: Literal["minor", "confirm"]
    dispatch_allowed: Literal[False] = False


class Adapter(Record, frozen=True):
    kind: ActionKind
    available: Literal[False] = False


REGISTRY = (
    Adapter("push_branch"),
    Adapter("open_pr"),
    Adapter("merge_pr"),
    Adapter("deploy"),
    Adapter("notify"),
)
