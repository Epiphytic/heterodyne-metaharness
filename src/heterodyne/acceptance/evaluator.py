"""Deterministic Plan9 evaluator, requiring explicit source-specific proof adapters.

The default adapter always holds. A supplied adapter is trusted integration code,
not a JSON boolean or an authentication mechanism implemented by these models.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast, get_args

import msgspec

from . import codec
from . import models as m

DURATION_NS = 259200000000000
GAP_NS = 600000000000
ERROR_NS = 1000000000
ORIGINAL_ADR = "a27acecee8274e6ad8dc65def436614d42b5cb04"
GATES: frozenset[str] = frozenset(get_args(m.GateName))
PRECLOCK: frozenset[str] = frozenset(("migration", "operator_surfaces"))
SEGMENT_COVERAGE: frozenset[str] = frozenset(get_args(m.CoverageID)) - PRECLOCK
REPOSITORIES: frozenset[str] = frozenset(get_args(m.Repository))
DRILLS: dict[m.DrillSubcase, tuple[m.DrillFamily, Literal["isolated", "release"]]] = {
    "adapter_restart": ("runtime_control", "release"),
    "wsd_restart": ("runtime_control", "release"),
    "scheduler_recovery": ("runtime_control", "release"),
    "host_reboot": ("runtime_control", "isolated"),
    "dolt_outage": ("data_and_effects", "isolated"),
    "journal_corruption_loss": ("data_and_effects", "isolated"),
    "uncertain_effect_restore": ("data_and_effects", "isolated"),
    "relay_hermes_outage": ("communications", "release"),
    "fake_forge_outage": ("communications", "isolated"),
    "quota_failover_defer": ("accounts", "isolated"),
    "account_changed_regate": ("accounts", "isolated"),
    "host_free_picker": ("marmot_only", "release"),
    "answer_stop_races": ("marmot_only", "isolated"),
    "startup_break_glass": ("marmot_only", "isolated"),
    "sandbox_login": ("security_and_install", "isolated"),
    "managed_install_negative_controls": ("security_and_install", "isolated"),
    "privilege_rollback": ("security_and_install", "isolated"),
}
ProofRole = Literal[
    "native_authority",
    "marmot_authority",
    "governing_amendment",
    "gate_completion",
    "release_inventory",
    "migration",
    "claim",
    "coverage",
    "drill",
    "checkpoint",
    "audit_registry",
    "event",
    "reserve_activation",
    "terminal_fence",
    "archive",
    "final_migration",
    "current_release",
]


class Hold(ValueError):
    """A fixed evidence/proof failure; never authorization to repair live state."""


@dataclass(frozen=True)
class ProofRequest:
    role: ProofRole
    entry_sha256: str
    value: bytes


class Proofs(Protocol):
    def verify(self, request: ProofRequest, inventory: "Inventory") -> None:
        """Raise Hold unless the exact request is established by admitted source proofs.

        native_authority MUST call pinned Queue.approval_valid/ask_digest under
        current policy, preserving complete native refs; no replacement algorithm.
        All other roles require their final reviewed source-specific runbooks.
        """
        ...


class Unavailable:
    def verify(self, request: ProofRequest, inventory: "Inventory") -> None:
        raise Hold("source_adapter_unavailable:" + request.role)


class Inventory:
    """A bounded immutable byte snapshot, not a collector-selected authority registry."""

    def __init__(self, artifacts: Mapping[str, bytes]):
        if len(artifacts) > 100_000 or sum(len(v) for v in artifacts.values()) > 128 * 1024 * 1024:
            raise Hold("inventory_bound")
        self._values = {key: bytes(value) for key, value in artifacts.items()}

    def read(self, artifact: m.Artifact) -> bytes:
        codec.validate(artifact)
        raw = self._values.get(artifact.id)
        if raw is None or codec.digest(raw) != artifact.sha256:
            raise Hold("artifact_missing_or_changed")
        return raw

    def references(self, value: object) -> None:
        if isinstance(value, m.Artifact):
            self.read(value)
        elif isinstance(value, msgspec.Struct):
            for field in msgspec.structs.fields(type(value)):
                self.references(getattr(value, field.name))
        elif isinstance(value, (tuple, list)):
            for child in cast(tuple[object, ...], value):
                self.references(child)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Hold(code)


def _unique(values: Sequence[object], code: str) -> None:
    require(len(values) == len(set(values)), code)


def _identity(identity: m.Identity) -> None:
    ids = [component.id for component in identity.components]
    require(bool(ids) and ids == sorted(set(ids)), "identity_registry")


@dataclass(frozen=True)
class Evaluation:
    result: Literal["not_qualified", "qualified_pending_archive", "release_complete"]
    reason: str
    duration_ns: str | None
    entry_sha256: str
    segment_id: str | None


class Evaluator:
    def __init__(self, inventory: Inventory, proofs: Proofs | None = None):
        self.inventory = inventory
        self.proofs: Proofs = proofs if proofs is not None else Unavailable()

    def _prove(self, role: ProofRole, entry: str, value: object) -> None:
        self.proofs.verify(ProofRequest(role, entry, codec.canonical(value)), self.inventory)

    def authority(self, value: m.Authority, entry: str) -> None:
        require(codec.native_utc(value.approved_at) == value.approved_at_utc, "authority_time")
        if isinstance(value, m.NativeAuthority):
            require(value.approved_digest == value.context_digest, "native_context")
            if isinstance(value.provenance, m.MarmotProvenance):
                require(value.via == "marmot" and value.via_ref is not None, "marmot_provenance")
            else:
                require(value.via == "cli", "historical_provenance")
            self._prove("native_authority", entry, value)
        else:
            self._prove("marmot_authority", entry, value)

    def _release(self, release: m.Release, entry: str) -> None:
        _identity(release.identity)
        require(
            {i.adapter for i in release.managed_installs} == {"codex", "claude-code"}
            and len(release.managed_installs) == 2,
            "managed_installs",
        )
        components = {c.id: c for c in release.identity.components}
        for install in release.managed_installs:
            require(
                install.component_id in components and components[install.component_id].kind == "install",
                "install_component",
            )
        require(bool(release.tests), "release_tests_missing")
        self._prove("release_inventory", entry, release)

    def _gates(self, entry: m.Entry, digest: str) -> None:
        require(
            len(entry.prerequisites) == len(GATES) and {g.name for g in entry.prerequisites} == set(GATES),
            "gate_set",
        )
        native = {
            "governing_authority",
            "plan5",
            "plan6",
            "plan7",
            "plan8",
            "acceptance_integration",
            "user_schedules",
        }
        deploy = {"store_helper_host_change", "wsd_act_privilege"}
        for gate in entry.prerequisites:
            rows = [(s.repository, s.commit, s.tree) for s in gate.sources]
            require(bool(rows) and rows == sorted(set(rows)), "gate_sources")
            require(gate.mode == "satisfied" or gate.name == "user_schedules", "gate_deviation")
            if gate.name in native:
                require(isinstance(gate.authority, m.NativeAuthority), "gate_native_authority")
            elif gate.name in deploy:
                require(gate.authority is not None, "gate_deployment_authority")
            else:
                require(gate.authority is None and gate.mode == "satisfied", "technical_gate_authority")
            if gate.authority is not None:
                self.authority(gate.authority, digest)
            if gate.name == "governing_authority":
                require(gate.authority == entry.design_authority, "governing_gate")
            if gate.mode == "approved_deviation":
                require(
                    any(
                        link.authority == gate.authority and link.amendment is not None
                        for link in entry.governing_chain
                    ),
                    "deviation_chain",
                )
            self._prove("gate_completion", digest, gate)

    def _governing(self, entry: m.Entry, digest: str) -> None:
        chain = entry.governing_chain
        require(bool(chain), "governing_chain_empty")
        require(
            chain[0].revision == ORIGINAL_ADR
            and chain[0].parent_revision is None
            and chain[0].amendment is None
            and chain[0].authority.native_bead_id == "btq-ovngv",
            "governing_original",
        )
        _unique([link.revision for link in chain], "governing_duplicate")
        for index, link in enumerate(chain):
            require(link.authority.adr_revision == link.revision, "governing_revision")
            self.authority(link.authority, digest)
            if index:
                require(
                    link.parent_revision == chain[index - 1].revision and link.amendment is not None,
                    "governing_successor",
                )
                self._prove("governing_amendment", digest, link)
        require(
            chain[-1].authority == entry.design_authority
            and chain[-1].revision == entry.release.governing_adr,
            "governing_final",
        )

    def _coverage(
        self,
        rows: Sequence[m.Coverage],
        phase: str,
        entry: m.Entry,
        digest: str,
        members: set[m.BeadRef],
        context: object = None,
    ) -> None:
        expected = PRECLOCK if phase == "preclock" else SEGMENT_COVERAGE
        require(len(rows) == len(expected) and {r.id for r in rows} == set(expected), "coverage_set")
        for row in rows:
            codec.validate(row)
            self.inventory.references(row)
            require(row.phase == phase and row.result == "pass", "coverage_not_passing")
            require(bool(row.members) and set(row.members) <= members, "coverage_members")
            _unique(row.members, "coverage_duplicate_members")
            require(row.binding.identity == entry.release.identity, "coverage_identity")
            self._projection(row.binding.projection_ids, entry.release.identity)
            require(bool(row.evidence), "coverage_evidence")
            self._prove("coverage", digest, (row, phase, context))

    @staticmethod
    def _projection(ids: tuple[str, ...], identity: m.Identity) -> None:
        require(
            bool(ids) and list(ids) == sorted(set(ids)) and set(ids) <= {c.id for c in identity.components},
            "component_projection",
        )

    def entry(self, raw: bytes) -> m.Entry:
        entry = codec.decode(raw, m.Entry)
        digest = codec.digest(raw)
        self.inventory.references(entry)
        self._release(entry.release, digest)
        self._governing(entry, digest)
        self._gates(entry, digest)
        self.authority(entry.deployment_authority, digest)
        require(
            len(entry.migration.source_queues) == 3
            and {q.repository for q in entry.migration.source_queues} == set(REPOSITORIES),
            "migration_queue_set",
        )
        for binding in entry.migration.source_queues:
            _unique([q.id for q in binding.queues], "duplicate_queue")
        require(codec.utc_ns(entry.migration.cutoff) <= codec.utc_ns(entry.created_at), "migration_cutoff")
        require(bool(entry.corpus), "empty_corpus")
        all_rows = entry.corpus + entry.migrated_reserve
        _unique([r.target for r in all_rows], "corpus_target_duplicate")
        _unique([r.origin for r in all_rows], "corpus_origin_duplicate")
        self._prove("migration", digest, entry.migration)
        self._prove("claim", digest, entry.workstream)
        self._coverage(entry.preclock_coverage, "preclock", entry, digest, {r.target for r in entry.corpus})
        require(
            len(entry.drills) == len(DRILLS) and {d.subcase for d in entry.drills} == set(DRILLS),
            "drill_subcases",
        )
        _unique([d.id for d in entry.drills], "drill_duplicate_id")
        for drill in entry.drills:
            require((drill.family, drill.scope) == DRILLS[drill.subcase], "drill_scope")
            require((drill.equivalence is not None) == (drill.scope == "isolated"), "drill_equivalence")
            if drill.scope == "release":
                require(drill.identity == entry.release.identity, "drill_release_identity")
            _identity(drill.identity)
            self._projection(drill.projection_ids, entry.release.identity)
            self._projection(drill.projection_ids, drill.identity)
            release_components = {c.id: c for c in entry.release.identity.components}
            drill_components = {c.id: c for c in drill.identity.components}
            require(
                all(release_components[cid] == drill_components[cid] for cid in drill.projection_ids),
                "drill_projection_mismatch",
            )
            self.authority(drill.authority, digest)
            self._prove("drill", digest, drill)
        require(entry.observation.source_inventory == entry.release.source_inventory, "observation_registry")
        self._prove("audit_registry", digest, entry.observation)
        return entry

    def checkpoint(
        self, checkpoint: m.Checkpoint, entry: m.Entry, digest: str, previous: m.Checkpoint | None
    ) -> None:
        codec.validate(checkpoint)
        self.inventory.references(checkpoint)
        require(checkpoint.entry_sha256 == digest, "checkpoint_entry")
        require(checkpoint.result == "pass", "checkpoint_" + checkpoint.result)
        require(checkpoint.identity == entry.release.identity, "release_change")
        _unique([i.source_id for i in checkpoint.audit_intervals], "audit_duplicate_source")
        require(bool(checkpoint.audit_intervals), "audit_empty")
        for interval in checkpoint.audit_intervals:
            require(int(interval.to_monotonic_ns) == int(checkpoint.monotonic_ns), "audit_endpoint")
            require(int(interval.from_monotonic_ns) <= int(interval.to_monotonic_ns), "audit_order")
        if previous is not None:
            require(checkpoint.boot_id == previous.boot_id, "clock_uncertain")
            elapsed = int(checkpoint.monotonic_ns) - int(previous.monotonic_ns)
            wall = codec.utc_ns(checkpoint.at) - codec.utc_ns(previous.at)
            require(0 < elapsed <= GAP_NS, "observation_gap")
            require(wall >= 0 and abs(wall - elapsed) <= ERROR_NS, "clock_uncertain")
            old = {i.source_id: i for i in previous.audit_intervals}
            require(set(old) == {i.source_id for i in checkpoint.audit_intervals}, "audit_registry_changed")
            for interval in checkpoint.audit_intervals:
                require(
                    interval.from_cursor == old[interval.source_id].to_cursor
                    and interval.from_monotonic_ns == previous.monotonic_ns,
                    "audit_discontinuity",
                )
        self._prove("checkpoint", digest, checkpoint)

    def interval(
        self,
        entry_raw: bytes,
        event_bytes: Sequence[bytes],
        segment_id: str,
        coverage: Sequence[m.Coverage],
        additions: Sequence[m.CorpusAddition] = (),
        unresolved: Sequence[str] = (),
    ) -> Evaluation:
        """Evaluate one segment's ordered prefix. Never add elapsed time from another segment."""
        digest = codec.digest(entry_raw)
        try:
            entry = self.entry(entry_raw)
            require(not unresolved, "unresolved_acceptance_faults")
            events = self._events(entry, digest, event_bytes)
            selected = [e for e in events if e.segment_id == segment_id]
            require(bool(selected) and selected[0].event == "segment_start", "segment_start_missing")
            require(sum(e.event == "segment_start" for e in selected) == 1, "duplicate_segment_start")
            members = {row.target for row in entry.corpus}
            activated: set[m.BeadRef] = set()
            reserve = {row.target: row for row in entry.migrated_reserve}
            hashes = {codec.digest(raw): event for raw, event in zip(event_bytes, events, strict=True)}
            for addition in additions:
                codec.validate(addition)
                self.inventory.references(addition)
                require(addition.entry_sha256 == digest and bool(addition.rows), "addition_entry")
                event = hashes.get(addition.event_sha256)
                require(
                    event is not None
                    and event.event == "corpus_transition"
                    and event.segment_id == segment_id
                    and addition.migration_delta in event.refs,
                    "addition_event",
                )
                for row in addition.rows:
                    require(
                        reserve.get(row.target) == row and row.target not in activated,
                        "addition_not_prepared_reserve",
                    )
                    activated.add(row.target)
                self._prove("reserve_activation", digest, addition)
            members |= activated
            start: m.Checkpoint | None = None
            last: m.Checkpoint | None = None
            for event in events[events.index(selected[0]) :]:
                self._intervention(event)
                require(event.segment_id in (segment_id, None), "overlapping_segment")
                require(event.segment_id is not None or event.checkpoint is None, "unbound_checkpoint")
                require(event.event not in {"qualification", "archive"}, "segment_already_terminal")
                require(event.stop is None, "segment_already_stopped")
                if event.checkpoint is None:
                    if last is None or event.boot_id != last.boot_id:
                        raise Hold("clock_uncertain")
                    require(
                        0 <= int(event.monotonic_ns) - int(last.monotonic_ns) <= GAP_NS, "observation_gap"
                    )
                    continue
                checkpoint = codec.decode(self.inventory.read(event.checkpoint), m.Checkpoint)
                self.checkpoint(checkpoint, entry, digest, last)
                require(
                    event.boot_id == checkpoint.boot_id
                    and int(event.monotonic_ns) >= int(checkpoint.monotonic_ns)
                    and codec.utc_ns(event.at) >= codec.utc_ns(checkpoint.at),
                    "checkpoint_event_time",
                )
                if start is None:
                    start = checkpoint
                    require(codec.utc_ns(start.at) >= codec.utc_ns(entry.created_at), "start_before_entry")
                last = checkpoint
            if start is None or last is None:
                raise Hold("checkpoint_missing")
            duration = int(last.monotonic_ns) - int(start.monotonic_ns)
            require(duration >= DURATION_NS, "duration_short")
            require(
                abs(codec.utc_ns(last.at) - codec.utc_ns(start.at) - duration) <= ERROR_NS, "clock_uncertain"
            )
            self._coverage(
                coverage,
                "segment",
                entry,
                digest,
                members,
                (segment_id, start, last, tuple(codec.digest(raw) for raw in event_bytes)),
            )
            return Evaluation("qualified_pending_archive", "none", str(duration), digest, segment_id)
        except (Hold, codec.InvalidEvidence) as exc:
            return Evaluation("not_qualified", str(exc), None, digest, segment_id)

    def _events(self, entry: m.Entry, digest: str, raws: Sequence[bytes]) -> list[m.Event]:
        events: list[m.Event] = []
        previous: str | None = None
        for index, raw in enumerate(raws):
            event = codec.decode(raw, m.Event)
            self.inventory.references(event)
            require(
                event.run_id == entry.run_id and event.seq == index and event.previous_sha256 == previous,
                "event_chain",
            )
            require((event.stop is not None) == (event.event == "segment_stop"), "event_stop_shape")
            require(
                (event.checkpoint is not None) == (event.event in {"heartbeat", "segment_start"}),
                "event_checkpoint_shape",
            )
            if events:
                require(codec.utc_ns(event.at) >= codec.utc_ns(events[-1].at), "event_chronology")
            self._prove("event", digest, event)
            events.append(event)
            previous = codec.digest(raw)
        return events

    @staticmethod
    def _intervention(event: m.Event) -> None:
        fixed = {
            "source_change": "release_change",
            "manual_intervention": "manual_intervention",
            "observation_gap": "observation_gap",
            "drill": "intentional_drill",
            "hold": "human_repair_required",
        }
        require(event.event not in fixed, fixed.get(event.event, "invariant_failure"))
        require(event.actor.kind != "operator" or event.event == "approval", "manual_intervention")

    def _archive_interval(
        self, entry_raw: bytes, raws: Sequence[bytes], segment: m.Segment, before: m.Assessment
    ) -> None:
        require(bool(raws), "archive_interval_missing")
        entry = codec.decode(entry_raw, m.Entry)
        events = self._events(entry, codec.digest(entry_raw), raws)
        matches = [i for i in range(len(events)) if codec.digest(raws[i]) == segment.end_event_sha256]
        require(len(matches) == 1, "archive_stop_missing")
        index = matches[0]
        stop_event = events[index]
        stop = stop_event.stop
        require(
            stop_event.segment_id == segment.segment_id
            and stop is not None
            and stop_event.event == "segment_stop"
            and stop.reason == "none",
            "archive_stop",
        )
        if stop is None:
            raise Hold("archive_stop")
        require(
            index > 0 and codec.digest(raws[index - 1]) == stop.checkpoint_event_sha256,
            "archive_stop_checkpoint",
        )
        endpoint = events[index - 1]
        require(endpoint.checkpoint == stop.checkpoint, "archive_stop_checkpoint")
        last = codec.decode(self.inventory.read(stop.checkpoint), m.Checkpoint)
        starts = [
            event
            for event, raw in zip(events[:index], raws[:index], strict=True)
            if codec.digest(raw) == segment.start_event_sha256
        ]
        require(
            len(starts) == 1
            and starts[0].event == "segment_start"
            and starts[0].segment_id == segment.segment_id
            and starts[0].checkpoint is not None,
            "archive_start",
        )
        if starts[0].checkpoint is None:
            raise Hold("archive_start")
        first = codec.decode(self.inventory.read(starts[0].checkpoint), m.Checkpoint)
        require(segment.start == first.at and segment.end == last.at, "archive_endpoint")
        require(
            all(
                event.event == "qualification" and event.segment_id == segment.segment_id
                for event in events[index + 1 :]
            )
            and len(events[index + 1 :]) <= 1,
            "archive_terminal_tail",
        )
        result = self.interval(
            entry_raw,
            raws[:index],
            segment.segment_id,
            before.coverage,
            before.supplemental_corpus,
            before.unresolved,
        )
        require(result.result == "qualified_pending_archive", result.reason)
        require(result.duration_ns == segment.duration_ns, "archive_duration")

    def archive(
        self,
        entry_raw: bytes,
        assessment_raw: bytes,
        archive_raw: bytes,
        final_raw: bytes,
        segment_raw: bytes,
        *,
        event_bytes: Sequence[bytes] = (),
    ) -> Evaluation:
        """Validate the noncircular postqualification graph, never execute archival."""
        digest = codec.digest(entry_raw)
        try:
            entry = self.entry(entry_raw)
            before = codec.decode(assessment_raw, m.Assessment)
            archive = codec.decode(archive_raw, m.Archive)
            final = codec.decode(final_raw, m.Assessment)
            segment = codec.decode(segment_raw, m.Segment)
            for value in (before, archive, final, segment):
                self.inventory.references(value)
                require(value.run_id == entry.run_id, "archive_run")
            require(
                segment.status == "sealed"
                and segment.reason == "none"
                and segment.entry_sha256 == digest
                and segment.end is not None
                and segment.end_event_sha256 is not None
                and segment.duration_ns is not None
                and int(segment.duration_ns) >= DURATION_NS,
                "archive_segment",
            )
            require(
                before.result == "qualified_pending_archive"
                and before.archive_receipt is None
                and before.current_release is None
                and not before.unresolved
                and before.segment_sha256 == codec.digest(segment_raw),
                "prearchive_assessment",
            )
            require(
                archive.assessment_sha256 == codec.digest(assessment_raw) and archive.result == "complete",
                "archive_assessment",
            )
            require(
                len(archive.repositories) == 3
                and {r.repository for r in archive.repositories} == set(REPOSITORIES)
                and all(r.result == "complete" for r in archive.repositories),
                "archive_repositories",
            )
            require(
                codec.utc_ns(before.at) <= codec.utc_ns(archive.at) <= codec.utc_ns(final.at),
                "archive_chronology",
            )
            require(
                final.result == "release_complete"
                and final.archive_receipt is not None
                and self.inventory.read(final.archive_receipt) == archive_raw
                and final.current_release is not None
                and not final.unresolved,
                "final_assessment",
            )
            require(
                final.segment_sha256 == before.segment_sha256
                and final.coverage == before.coverage
                and final.final_inventory == before.final_inventory
                and final.evaluation == before.evaluation
                and final.supplemental_corpus == before.supplemental_corpus,
                "final_interval_changed",
            )
            if final.current_release is None:
                raise Hold("final_assessment")
            require(final.current_release.identity == entry.release.identity, "release_change")
            self._release(final.current_release, digest)
            self._archive_interval(entry_raw, event_bytes, segment, before)
            self.authority(archive.authority, digest)
            self._prove("terminal_fence", digest, (segment, before))
            self._prove("final_migration", digest, archive.final_migration)
            self._prove("archive", digest, archive)
            self._prove("current_release", digest, final)
            return Evaluation("release_complete", "none", segment.duration_ns, digest, segment.segment_id)
        except (Hold, codec.InvalidEvidence) as exc:
            return Evaluation("not_qualified", str(exc), None, digest, None)
