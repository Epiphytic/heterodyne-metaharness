"""Closed immutable Plan9 d4 evidence grammar (e009a2c6).

These records describe evidence; construction never authenticates its contents.
"""

from typing import Annotated, Literal

import msgspec

ID = Annotated[str, msgspec.Meta(pattern=r"^[A-Za-z0-9_:.+\-]{1,128}$")]
SHA = Annotated[str, msgspec.Meta(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, msgspec.Meta(pattern=r"^[0-9a-f]{64}$")]
NS = Annotated[str, msgspec.Meta(pattern=r"^(0|[1-9][0-9]{0,19})$")]
UTC = Annotated[
    str, msgspec.Meta(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")
]
NativeTime = Annotated[str, msgspec.Meta(min_length=1, max_length=64)]
Count = Annotated[int, msgspec.Meta(ge=0, le=2147483647)]
Repository = Literal["hermes-workstream-harness", "harness-improvements", "harness-dev"]
Adapter = Literal["codex", "claude-code"]
Reason = Literal[
    "none",
    "manual_intervention",
    "release_change",
    "observation_gap",
    "clock_uncertain",
    "invariant_failure",
    "human_repair_required",
    "intentional_drill",
    "break_glass",
]
EventKind = Literal[
    "entry",
    "approval",
    "corpus_transition",
    "automated_recovery",
    "drill",
    "source_change",
    "manual_intervention",
    "heartbeat",
    "observation_gap",
    "segment_start",
    "segment_stop",
    "qualification",
    "archive",
    "hold",
]
GateName = Literal[
    "governing_authority",
    "plan5",
    "plan6",
    "plan7",
    "plan8",
    "acceptance_integration",
    "user_schedules",
    "store_helper_host_change",
    "wsd_act_privilege",
    "spikes_s1_s4",
    "sandbox_s5",
    "accounts_s7",
    "admin_s8",
    "managed_installs",
    "deterministic_failure_suite",
    "source_specific_runbooks",
    "marmot_only",
    "forge_phase2_boundary",
]
CoverageID = Literal[
    "migration",
    "operator_surfaces",
    "pickup",
    "park_resume",
    "marmot_approval",
    "codex_managed",
    "claude_managed",
    "review_close",
    "ordinary_operation",
]
DrillFamily = Literal[
    "runtime_control", "data_and_effects", "communications", "accounts", "marmot_only", "security_and_install"
]
DrillSubcase = Literal[
    "adapter_restart",
    "wsd_restart",
    "scheduler_recovery",
    "host_reboot",
    "dolt_outage",
    "journal_corruption_loss",
    "uncertain_effect_restore",
    "relay_hermes_outage",
    "fake_forge_outage",
    "quota_failover_defer",
    "account_changed_regate",
    "host_free_picker",
    "answer_stop_races",
    "startup_break_glass",
    "sandbox_login",
    "managed_install_negative_controls",
    "privilege_rollback",
]


class Record(msgspec.Struct, frozen=True, forbid_unknown_fields=True, kw_only=True):
    """All fields required, including explicitly nullable fields."""


class Artifact(Record, frozen=True):
    id: ID
    sha256: Digest


class MarmotProvenance(Record, tag="marmot", tag_field="kind", frozen=True):
    audit: Artifact


class HistoricalProvenance(Record, tag="historical_native", tag_field="kind", frozen=True):
    record: Artifact
    authority_evidence: Artifact


class NativeAuthority(Record, tag="native", tag_field="kind", frozen=True):
    native_bead_id: ID
    decision: Literal["approve"]
    approver_ref: ID
    approved_at: NativeTime
    approved_at_utc: UTC
    approved_digest: Digest
    context_digest: Digest
    adr_revision: SHA
    via: Literal["marmot", "cli"]
    via_ref: ID | None
    readback: Artifact
    policy_readback: Artifact
    provenance: MarmotProvenance | HistoricalProvenance


class MarmotAuthority(Record, tag="marmot_scope", tag_field="kind", frozen=True):
    decision: Literal["approve"]
    approver_ref: ID
    approved_at: NativeTime
    approved_at_utc: UTC
    scope_sha256: Digest
    via_ref: ID
    readback: Artifact
    policy_readback: Artifact
    audit: Artifact


Authority = NativeAuthority | MarmotAuthority


class Component(Record, frozen=True):
    id: ID
    kind: Literal["code", "executable", "runtime", "install", "unit", "configuration", "policy", "capability"]
    content_digest: Digest


class Identity(Record, frozen=True):
    components: tuple[Component, ...]


class Install(Record, frozen=True):
    adapter: Adapter
    version: ID
    component_id: ID
    verification: Artifact


class Release(Record, frozen=True):
    product_commit: SHA
    product_tree: SHA
    queue_commit: SHA
    governing_adr: SHA
    source_inventory: Artifact
    loaded_inventory: Artifact
    identity: Identity
    managed_installs: tuple[Install, ...]
    capabilities: Artifact
    tests: tuple[Artifact, ...]


class SourceRef(Record, frozen=True):
    repository: ID
    commit: SHA
    tree: SHA


class Gate(Record, frozen=True):
    name: GateName
    mode: Literal["satisfied", "approved_deviation"]
    sources: tuple[SourceRef, ...]
    completion: Artifact
    authority: Authority | None


class GoverningLink(Record, frozen=True):
    revision: SHA
    parent_revision: SHA | None
    authority: NativeAuthority
    amendment: Artifact | None


class Workstream(Record, frozen=True):
    id: ID
    native_queue_identity_ref: ID
    sessions: Artifact
    claims: Artifact
    journal_backup: Artifact
    beads_backup: Artifact


class QueueIdentity(Record, frozen=True):
    id: ID
    identity: Artifact


class QueueBinding(Record, frozen=True):
    repository: Repository
    queues: tuple[QueueIdentity, ...]
    discovery: Artifact


class Migration(Record, frozen=True):
    source_queues: tuple[QueueBinding, ...]
    cutoff: UTC
    old_open_inventory: Artifact
    dispositions: Artifact
    delta_audit: Artifact


class BeadRef(Record, frozen=True):
    queue_id: ID
    bead_id: ID


class Disposition(Record, frozen=True):
    origin: BeadRef
    state: Literal["migrated", "culled", "deferred"]
    target: BeadRef | None
    target_workstream: ID | None
    rationale: Artifact
    readback: Artifact


class FinalMigration(Record, frozen=True):
    entry_migration: Artifact
    final_inventory: Artifact
    final_dispositions: Artifact
    second_delta_audit: Artifact


class Corpus(Record, frozen=True):
    origin: BeadRef
    target: BeadRef
    origin_evidence: Artifact
    migration_readback: Artifact


class CorpusAddition(Record, frozen=True):
    entry_sha256: Digest
    event_sha256: Digest
    migration_delta: Artifact
    rows: tuple[Corpus, ...]


class Equivalence(Record, frozen=True):
    isolation: Artifact
    component_projection: Artifact
    limitations: Artifact


class DrillReuse(Record, frozen=True):
    original_drill: Artifact
    original_entry: Artifact
    projection_comparison: Artifact
    revalidation: Artifact


class Drill(Record, frozen=True):
    id: ID
    family: DrillFamily
    subcase: DrillSubcase
    scope: Literal["isolated", "release"]
    authority: Authority
    identity: Identity
    projection_ids: tuple[ID, ...]
    equivalence: Equivalence | None
    reuse: DrillReuse | None
    runbook: Artifact
    receipt: Artifact
    result: Literal["pass"]


class CoverageReuse(Record, frozen=True):
    original_coverage: Artifact
    original_entry: Artifact
    comparison: Artifact
    revalidation: Artifact


class CoverageBinding(Record, frozen=True):
    identity: Identity
    projection_ids: tuple[ID, ...]
    projection: Artifact
    reuse: CoverageReuse | None


class Coverage(Record, frozen=True):
    id: CoverageID
    phase: Literal["preclock", "segment"]
    members: tuple[BeadRef, ...]
    binding: CoverageBinding
    evidence: tuple[Artifact, ...]
    result: Literal["missing", "pass", "fail"]


class Observation(Record, frozen=True):
    nominal_period_seconds: Literal[300]
    max_gap_seconds: Literal[600]
    max_clock_error_ns: Literal["1000000000"]
    collector: Artifact
    audit_capabilities: Artifact
    source_inventory: Artifact
    retention: Artifact


class Receipt(Record, frozen=True):
    format: Literal[1]
    run_id: ID


class Entry(Receipt, tag="entry", tag_field="type", frozen=True):
    created_at: UTC
    release: Release
    prerequisites: tuple[Gate, ...]
    design_authority: NativeAuthority
    governing_chain: tuple[GoverningLink, ...]
    deployment_authority: Authority
    workstream: Workstream
    migration: Migration
    corpus: tuple[Corpus, ...]
    migrated_reserve: tuple[Corpus, ...]
    preclock_coverage: tuple[Coverage, ...]
    drills: tuple[Drill, ...]
    observation: Observation
    evidence: tuple[Artifact, ...]


class Actor(Record, frozen=True):
    kind: Literal["automatic", "operator", "observer"]
    identity_ref: ID


class Detail(Record, frozen=True):
    code: ID
    subject_refs: tuple[ID, ...]
    expected: ID
    observed: ID


class Stop(Record, frozen=True):
    reason: Reason
    checkpoint: Artifact
    checkpoint_event_sha256: Digest
    transaction_id: ID


class Event(Receipt, tag="event", tag_field="type", frozen=True):
    seq: Count
    at: UTC
    monotonic_ns: NS
    boot_id: ID
    segment_id: ID | None
    previous_sha256: Digest | None
    event: EventKind
    actor: Actor
    refs: tuple[Artifact, ...]
    detail: Detail
    checkpoint: Artifact | None
    stop: Stop | None


class Segment(Receipt, tag="segment", tag_field="type", frozen=True):
    segment_id: ID
    entry_sha256: Digest
    start_event_sha256: Digest
    end_event_sha256: Digest | None
    start: UTC
    end: UTC | None
    duration_ns: NS | None
    status: Literal["running", "interrupted", "sealed"]
    reason: Reason
    evidence: tuple[Artifact, ...]


class Assessment(Receipt, tag="assessment", tag_field="type", frozen=True):
    at: UTC
    segment_sha256: Digest
    coverage: tuple[Coverage, ...]
    final_inventory: Artifact
    supplemental_corpus: tuple[CorpusAddition, ...]
    evaluation: Artifact
    archive_receipt: Artifact | None
    current_release: Release | None
    unresolved: tuple[ID, ...]
    result: Literal["not_qualified", "qualified_pending_archive", "release_complete"]
    evidence: tuple[Artifact, ...]


class ArchiveRow(Record, frozen=True):
    repository: Repository
    retained_history: Artifact
    read_only_verification: Artifact
    dependency_audit: Artifact
    operation: Artifact
    result: Literal["held", "complete"]


class Archive(Receipt, tag="archive", tag_field="type", frozen=True):
    at: UTC
    assessment_sha256: Digest
    authority: Authority
    final_migration: FinalMigration
    repositories: tuple[ArchiveRow, ...]
    result: Literal["held", "complete"]
    evidence: tuple[Artifact, ...]


class AuditInterval(Record, frozen=True):
    source_id: ID
    from_cursor: ID
    to_cursor: ID
    from_monotonic_ns: NS
    to_monotonic_ns: NS
    evidence: Artifact


class Checkpoint(Record, frozen=True):
    id: ID
    entry_sha256: Digest
    boot_id: ID
    clock_source: Literal["boottime"]
    at: UTC
    monotonic_ns: NS
    identity: Identity
    inventory_observation: Artifact
    audit_intervals: tuple[AuditInterval, ...]
    source_cursors: Artifact
    continuity: Artifact
    result: Literal["pass", "hold", "fail"]


class SealIntent(Record, frozen=True):
    transaction_id: ID
    segment_id: ID
    entry_sha256: Digest
    start_event_sha256: Digest
    checkpoint: Artifact
    checkpoint_event_sha256: Digest
    evaluation: Artifact
    reason: Reason
    result: Literal["sealed", "interrupted"]


EvidenceReceipt = Entry | Event | Segment | Assessment | Archive
