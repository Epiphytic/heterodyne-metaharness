"""Explicit synthetic fixture adapter. This is NEVER production/native authority evidence."""

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import msgspec

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m
from heterodyne.acceptance.evaluator import (
    DRILLS,
    GATES,
    ORIGINAL_ADR,
    SEGMENT_COVERAGE,
    Inventory,
    ProofRequest,
)

A = m.Artifact(id="synthetic", sha256=codec.digest(b"synthetic fixture only"))
SOURCE = m.SourceRef(repository="product", commit="a" * 40, tree="b" * 40)
IDENTITY = m.Identity(
    components=(
        m.Component(id="claude", kind="install", content_digest="c" * 64),
        m.Component(id="codex", kind="install", content_digest="d" * 64),
    )
)
NATIVE = m.NativeAuthority(
    native_bead_id="btq-ovngv",
    decision="approve",
    approver_ref="synthetic",
    approved_at="2026-10-14T15:57:38Z",
    approved_at_utc="2026-10-14T15:57:38.000000Z",
    approved_digest="a" * 64,
    context_digest="a" * 64,
    adr_revision=ORIGINAL_ADR,
    via="marmot",
    via_ref="synthetic",
    readback=A,
    policy_readback=A,
    provenance=m.MarmotProvenance(audit=A),
)
TARGET = m.BeadRef(queue_id="new", bead_id="real-shaped-not-real")
CORPUS = m.Corpus(
    origin=m.BeadRef(queue_id="old", bead_id="old-shaped"),
    target=TARGET,
    origin_evidence=A,
    migration_readback=A,
)
T0 = datetime(2026, 10, 16, tzinfo=UTC)
BASE_NS = 9007199254740993


def at(seconds: float = 0) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def coverage(name: m.CoverageID, phase: Literal["preclock", "segment"] = "segment") -> m.Coverage:
    return m.Coverage(
        id=name,
        phase=phase,
        members=(TARGET,),
        binding=m.CoverageBinding(
            identity=IDENTITY, projection_ids=("claude", "codex"), projection=A, reuse=None
        ),
        evidence=(A,),
        result="pass",
    )


def entry() -> m.Entry:
    native_gates = {
        "governing_authority",
        "plan5",
        "plan6",
        "plan7",
        "plan8",
        "acceptance_integration",
        "user_schedules",
        "store_helper_host_change",
        "wsd_act_privilege",
    }
    release = m.Release(
        product_commit="a" * 40,
        product_tree="b" * 40,
        queue_commit="c" * 40,
        governing_adr=ORIGINAL_ADR,
        source_inventory=A,
        loaded_inventory=A,
        identity=IDENTITY,
        managed_installs=tuple(
            m.Install(adapter=cast(m.Adapter, adapter), version="synthetic", component_id=cid, verification=A)
            for adapter, cid in [("codex", "codex"), ("claude-code", "claude")]
        ),
        capabilities=A,
        tests=(A,),
    )
    drills = tuple(
        m.Drill(
            id=name,
            family=family,
            subcase=name,
            scope=scope,
            authority=NATIVE,
            identity=IDENTITY,
            projection_ids=("claude", "codex"),
            equivalence=m.Equivalence(isolation=A, component_projection=A, limitations=A)
            if scope == "isolated"
            else None,
            reuse=None,
            runbook=A,
            receipt=A,
            result="pass",
        )
        for name, (family, scope) in DRILLS.items()
    )
    return m.Entry(
        format=1,
        run_id="synthetic",
        created_at=at(),
        release=release,
        prerequisites=tuple(
            m.Gate(
                name=cast(m.GateName, name),
                mode="satisfied",
                sources=(SOURCE,),
                completion=A,
                authority=NATIVE if name in native_gates else None,
            )
            for name in sorted(GATES)
        ),
        design_authority=NATIVE,
        governing_chain=(
            m.GoverningLink(revision=ORIGINAL_ADR, parent_revision=None, authority=NATIVE, amendment=None),
        ),
        deployment_authority=NATIVE,
        workstream=m.Workstream(
            id="synthetic",
            native_queue_identity_ref="synthetic",
            sessions=A,
            claims=A,
            journal_backup=A,
            beads_backup=A,
        ),
        migration=m.Migration(
            source_queues=tuple(
                m.QueueBinding(repository=cast(m.Repository, repo), queues=(), discovery=A)
                for repo in ("hermes-workstream-harness", "harness-improvements", "harness-dev")
            ),
            cutoff=at(),
            old_open_inventory=A,
            dispositions=A,
            delta_audit=A,
        ),
        corpus=(CORPUS,),
        migrated_reserve=(),
        preclock_coverage=(coverage("migration", "preclock"), coverage("operator_surfaces", "preclock")),
        drills=drills,
        observation=m.Observation(
            nominal_period_seconds=300,
            max_gap_seconds=600,
            max_clock_error_ns="1000000000",
            collector=A,
            audit_capabilities=A,
            source_inventory=A,
            retention=A,
        ),
        evidence=(),
    )


class SyntheticProofs:
    """Only a deterministic evaluator fixture; deliberately no native/API calls."""

    def __init__(self, reject: str | None = None):
        self.reject = reject
        self.seen: list[str] = []

    def verify(self, request: ProofRequest, inventory: Inventory) -> None:
        from heterodyne.acceptance.evaluator import Hold

        self.seen.append(request.role)
        if request.role == self.reject:
            raise Hold("synthetic_source_refused")


def event(
    seq: int, previous: str | None, seconds: float, kind: m.EventKind, checkpoint: m.Artifact | None = None
) -> m.Event:
    return m.Event(
        format=1,
        run_id="synthetic",
        seq=seq,
        at=at(seconds),
        monotonic_ns=str(BASE_NS + round(seconds * 1_000_000_000)),
        boot_id="boot",
        segment_id="segment",
        previous_sha256=previous,
        event=kind,
        actor=m.Actor(kind="automatic", identity_ref="synthetic"),
        refs=(),
        detail=m.Detail(code="synthetic", subject_refs=(), expected="same", observed="same"),
        checkpoint=checkpoint,
        stop=None,
    )


def timeline(seconds: int = 259200, period: int = 600) -> tuple[bytes, list[bytes], dict[str, bytes]]:
    raw = codec.canonical(entry())
    digest = codec.digest(raw)
    inventory = {A.id: b"synthetic fixture only"}
    events: list[bytes] = []
    points = list(range(0, seconds, period)) + [seconds]
    for i, point in enumerate(points):
        previous_point = points[i - 1] if i else point
        cp = m.Checkpoint(
            id=f"cp{i}",
            entry_sha256=digest,
            boot_id="boot",
            clock_source="boottime",
            at=at(point),
            monotonic_ns=str(BASE_NS + point * 1_000_000_000),
            identity=IDENTITY,
            inventory_observation=A,
            audit_intervals=(
                m.AuditInterval(
                    source_id="audit",
                    from_cursor=f"c{max(i - 1, 0)}",
                    to_cursor=f"c{i}",
                    from_monotonic_ns=str(BASE_NS + previous_point * 1_000_000_000),
                    to_monotonic_ns=str(BASE_NS + point * 1_000_000_000),
                    evidence=A,
                ),
            ),
            source_cursors=A,
            continuity=A,
            result="pass",
        )
        cpraw = codec.canonical(cp)
        artifact = m.Artifact(id=cp.id, sha256=codec.digest(cpraw))
        inventory[artifact.id] = cpraw
        item = event(
            i,
            codec.digest(events[-1]) if events else None,
            point,
            "segment_start" if i == 0 else "heartbeat",
            artifact,
        )
        events.append(codec.canonical(item))
    return raw, events, inventory


def rows() -> tuple[m.Coverage, ...]:
    return tuple(coverage(cast(m.CoverageID, name)) for name in sorted(SEGMENT_COVERAGE))


def replace[T: msgspec.Struct](value: T, **changes: object) -> T:
    return msgspec.structs.replace(value, **changes)
