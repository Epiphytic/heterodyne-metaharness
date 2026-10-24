"""Source-enrolled maintenance NOTICE consent. Never executes a queue/native operation.

Local callers share the trusted daemon UID. Registration is not authority; only an
actual authenticated, current-operator message on the delivered notice can consent.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

import msgspec

from heterodyne.admind.redact import redact

if TYPE_CHECKING:
    from heterodyne.admind.store import Store

SOCKET = "maintenance-recovery.sock"
MAX_BYTES = 262_144
HEX = re.compile(r"[0-9a-f]{64}\Z")
COMMAND = re.compile(r"!maintenance (settle|revoke) ([0-9a-f]{24})\Z")


class Invalid(ValueError):
    """Fixed refusal code; never includes input material."""


class Selector(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    type: Literal["task", "ask", "notice", "local"]
    id: str


class Projection(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    adapter_sha256: str
    selector: Selector
    before_sha256: str
    action: Literal[
        "clear-proven-notice",
        "clear-proven-cancel",
        "abort-proven-handoff",
        "abandon-notice",
        "finish-health",
    ]


class Descriptor(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["maintenance-recovery.v1"]
    proposal_id: str
    operation: str
    mode: Literal["settle", "abandon-notice", "finish-health"]
    enrollment_sha256: str
    original_record_sha256: str
    source_closure: str
    effects_root: str | None
    effect_count: int
    local_records: list[Projection]
    backup_before_sha256: str | None
    failure_before_sha256: str | None
    proof_set_sha256: str
    result: Literal["complete", "abandoned-diagnostic", "health-restored"]
    expires_at: str


class NativeEffect(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    ordinal: int
    task_id: str
    approval_id: str
    family: Literal[
        "create-approval",
        "create-member",
        "publish",
        "pin-claim",
        "decision-metadata",
        "close-approval",
        "pin-metadata",
        "audit-comment",
        "pin-release",
    ]
    intent_envelope_sha256: str
    plan_sha256: str
    observation_sha256: str
    verdict: Literal["observed-applied", "observed-unapplied"]


class NativeDescriptor(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["settle-approval-native.v1"]
    proposal_id: str
    operation: str
    mode: Literal["settle-approval-native"]
    enrollment_sha256: str
    original_record_sha256: str
    source_closure: str
    effects_root: str
    effect_count: int
    local_records: list[Projection]
    backup_before_sha256: None
    failure_before_sha256: None
    proof_set_sha256: str
    result: Literal["complete"]
    expires_at: str
    native_effect: NativeEffect


class MarkerSnapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    sha256: str
    mode: int


class PrimaryChildIntent(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    contract: Literal["primary-pickup-child.v1"]
    source: str
    executable: str
    actor: str
    kind: Literal["pause", "resume"]
    path: str
    before: MarkerSnapshot | None
    after: MarkerSnapshot | None


class PrimaryChildCoverage(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["primary-child-local-coverage.v1"]
    path: str
    source: str
    executable: str
    actor: str


class PrimaryChildObservation(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    ordinal: int
    intent_envelope_sha256: str
    intent_payload_sha256: str
    intent: PrimaryChildIntent
    observed: MarkerSnapshot | None
    classification: Literal["observed-after", "observed-before", "observed-invariant", "observed-other"]
    original_action_outcome: Literal["not-established"]
    effects_after_root: str
    coverage: PrimaryChildCoverage


class PrimaryChildWrite(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    role: Literal[
        "issuing-program", "permit-consumption", "proof", "effect", "receipt", "operation", "compaction"
    ]
    path: str
    before_sha256: str | None
    image_sha256: str
    authority_bindings: list[
        Literal["descriptor", "permit", "proposal_sha256", "permit_sha256", "authority_receipt_sha256"]
    ]


class PrimaryChildTerminalPlan(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["maintenance-primary-child-terminal-plan.v1"]
    proof: dict[str, object]
    before_effect: dict[str, object]
    after_effect: dict[str, object]
    before_operation_sha256: str
    after_operation: dict[str, object]
    receipt: dict[str, object]
    terminal: dict[str, object]


class PrimaryChildDescriptor(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["maintenance-primary-child-settlement.v1"]
    proposal_id: str
    proposal_generation: int
    operation: str
    mode: Literal["settle-primary-child"]
    enrollment_sha256: str
    original_record_sha256: str
    source_closure: str
    effects_root: str
    effect_count: int
    local_records: list[Projection]
    backup_before_sha256: None
    failure_before_sha256: None
    proof_set_sha256: str
    result: Literal["settled-observed"]
    expires_at: str
    primary_child: PrimaryChildObservation
    terminal_plan: PrimaryChildTerminalPlan
    allowed_writes: list[PrimaryChildWrite]
    # Hash of the stable terminal plan, NOT of the future issuing program.
    program_sha256: str


RecoveryDescriptor = Descriptor | NativeDescriptor | PrimaryChildDescriptor


class BackupOwner(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    pid: int
    start: int
    boot: str


class BackupRecord(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["maintenance-backup.v1"]
    operation: str
    owner: BackupOwner
    source_sha256: str
    state: Literal["active", "issued", "unknown", "complete"]
    ordinal: int
    intent_sha256: str | None
    recovery_only: bool


class Certificate(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["maintenance-recovery-nonconsumption.v1"]
    proposal_sha256: str
    permit_sha256: str
    generation: int
    operation: str
    original_record_sha256: str
    effects_root: str | None
    enrollment_sha256: str
    target_snapshot_sha256: str
    boot_id: str
    observed_boottime_ns: int


def target_snapshot(obj: RecoveryDescriptor) -> str:
    """Expected snapshot; only the queue can attest its fresh observation under EX."""
    value: dict[str, object] = {
        "schema": "maintenance-recovery-target-snapshot.v1",
        **{
            name: getattr(obj, name)
            for name in (
                "mode",
                "operation",
                "original_record_sha256",
                "effects_root",
                "effect_count",
                "proof_set_sha256",
                "local_records",
                "backup_before_sha256",
                "failure_before_sha256",
                "enrollment_sha256",
                "source_closure",
            )
        },
    }
    if isinstance(obj, NativeDescriptor):
        value.update(
            schema="maintenance-recovery-native-target-snapshot.v1",
            descriptor_schema=obj.schema,
            native_effect=obj.native_effect,
        )
    elif isinstance(obj, PrimaryChildDescriptor):
        value.update(
            schema="maintenance-primary-child-target-snapshot.v1",
            descriptor_schema=obj.schema,
            **{
                name: getattr(obj, name)
                for name in (
                    "proposal_id",
                    "proposal_generation",
                    "primary_child",
                    "terminal_plan",
                    "allowed_writes",
                    "program_sha256",
                )
            },
        )
    return sha(value)


class Register(msgspec.Struct, frozen=True, tag="maintenance.recovery.register", forbid_unknown_fields=True):
    # Transport only: untagged struct unions are not supported by msgspec.
    # check() and register() both require exact schema-specific conversion;
    # this field is never accepted as validated descriptor data by itself.
    descriptor: object
    proof_set_sha256: str


class Status(msgspec.Struct, frozen=True, tag="maintenance.recovery.status", forbid_unknown_fields=True):
    proposal_sha256: str


class Permit(msgspec.Struct, frozen=True, tag="maintenance.recovery.permit", forbid_unknown_fields=True):
    proposal_sha256: str
    certificate_sha256: str | None = None


Request = Register | Status | Permit


class Reply(msgspec.Struct, frozen=True):
    result: str
    message: str
    view: dict[str, object] | None = None


def canonical(value: object) -> bytes:
    return json.dumps(
        msgspec.to_builtins(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def sha(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise Invalid("duplicate-key")
        result[key] = value
    return result


def decode[T](data: bytes, kind: type[T]) -> T:
    if len(data) > MAX_BYTES:
        raise Invalid("oversized")
    try:
        raw: object = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(Invalid("nonfinite")),
        )
        stack: list[tuple[object, int]] = [(raw, 0)]
        while stack:
            value, depth = stack.pop()
            if depth > 12:
                raise Invalid("depth")
            if isinstance(value, dict):
                stack.extend((item, depth + 1) for item in cast(dict[str, object], value).values())
            elif isinstance(value, list):
                stack.extend((item, depth + 1) for item in cast(list[object], value))
        return msgspec.convert(raw, type=kind, strict=True)
    except (ValueError, TypeError, RecursionError, msgspec.ValidationError):
        raise Invalid("malformed") from None


def stamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise Invalid("time") from None


def descriptor(data: bytes) -> RecoveryDescriptor:
    raw = decode(data, dict[str, object])
    schema = raw.get("schema")
    if schema == "maintenance-recovery.v1":
        obj: RecoveryDescriptor = decode(data, Descriptor)
    elif schema == "settle-approval-native.v1":
        obj = decode(data, NativeDescriptor)
    elif schema == "maintenance-primary-child-settlement.v1":
        obj = decode(data, PrimaryChildDescriptor)
    else:
        raise Invalid("descriptor-schema")
    for value in (obj.proposal_id, obj.operation):
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except ValueError:
            raise Invalid("uuid") from None
    hashes = [obj.enrollment_sha256, obj.original_record_sha256, obj.source_closure, obj.proof_set_sha256]
    hashes.extend(
        v for v in (obj.effects_root, obj.backup_before_sha256, obj.failure_before_sha256) if v is not None
    )
    for item in obj.local_records:
        hashes.extend((item.adapter_sha256, item.before_sha256))
        if not re.fullmatch(r"[\x21-\x7e]{1,128}", item.selector.id):
            raise Invalid("selector")
    if not all(HEX.fullmatch(v) for v in hashes):
        raise Invalid("hash")
    if not 0 <= obj.effect_count <= 4096 or len(obj.local_records) > 8:
        raise Invalid("bounds")
    if len({(r.selector.type, r.selector.id) for r in obj.local_records}) != len(obj.local_records):
        raise Invalid("duplicate-selector")
    expected = {
        "settle": "complete",
        "settle-approval-native": "complete",
        "settle-primary-child": "settled-observed",
        "abandon-notice": "abandoned-diagnostic",
        "finish-health": "health-restored",
    }
    if obj.result != expected[obj.mode]:
        raise Invalid("result")
    health = obj.mode == "finish-health"
    if isinstance(obj, NativeDescriptor):
        native_descriptor(obj)
    elif isinstance(obj, PrimaryChildDescriptor):
        primary_child_descriptor(obj)
    elif health:
        if (
            obj.effects_root is not None
            or obj.effect_count != 0
            or obj.backup_before_sha256 is None
            or obj.failure_before_sha256 is None
            or obj.proof_set_sha256 != sha([])
            or any(r.action != "finish-health" for r in obj.local_records)
        ):
            raise Invalid("mode-scope")
    elif (
        obj.backup_before_sha256 is not None
        or obj.failure_before_sha256 is not None
        or (obj.effect_count > 0 and obj.effects_root is None)
        or any(r.action == "finish-health" for r in obj.local_records)
        or (obj.mode == "settle" and any(r.action == "abandon-notice" for r in obj.local_records))
    ):
        raise Invalid("mode-scope")
    stamp(obj.expires_at)
    return obj


def native_descriptor(obj: NativeDescriptor) -> None:
    """Only the latest unknown native effect; no local projection or replay."""
    effect = obj.native_effect
    if (
        obj.effect_count < 1
        or effect.ordinal != obj.effect_count
        or obj.local_records
        or not HEX.fullmatch(obj.effects_root)
    ):
        raise Invalid("native-scope")
    for value in (effect.task_id, effect.approval_id):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}", value):
            raise Invalid("native-id")
    if not all(
        HEX.fullmatch(value)
        for value in (
            effect.intent_envelope_sha256,
            effect.plan_sha256,
            effect.observation_sha256,
        )
    ):
        raise Invalid("native-hash")


def primary_closed(value: object, keys: tuple[str, ...]) -> dict[str, object]:
    """Business image objects are closed data, never a substitution program."""
    if not isinstance(value, dict) or set(cast(dict[str, object], value)) != set(keys):
        raise Invalid("primary-image-fields")
    return cast(dict[str, object], value)


def primary_child_writes(obj: PrimaryChildDescriptor) -> list[dict[str, object]]:
    """Exact images except the enumerated same-proposal authority bindings."""
    plan = msgspec.to_builtins(obj.terminal_plan)
    proposal, operation = obj.proposal_id, obj.operation
    roles: tuple[tuple[str, str, str | None, object, list[str]], ...] = (
        (
            "issuing-program",
            f"recovery-v1/{proposal}.primary-issuing.json",
            None,
            {
                "schema": "maintenance-primary-child-issuing-template.v1",
                "descriptor": "$descriptor",
                "permit": "$authenticated-permit",
                "plan": plan,
            },
            ["descriptor", "permit"],
        ),
        (
            "permit-consumption",
            f"recovery-v1/{proposal}.primary-consumed.json",
            None,
            {
                "schema": "maintenance-primary-child-consumption.v1",
                "proposal_sha256": "$proposal-sha256",
                "permit_sha256": "$authenticated-permit-sha256",
            },
            ["proposal_sha256", "permit_sha256"],
        ),
        ("proof", f"effect-proofs-v1/{sha(plan['proof'])}.json", None, plan["proof"], []),
        (
            "effect",
            f"effects-v1/{operation}.{obj.effect_count:04d}.json",
            sha(plan["before_effect"]),
            plan["after_effect"],
            [],
        ),
        (
            "receipt",
            f"recovery-v1/{proposal}.primary-receipt.json",
            None,
            plan["receipt"],
            ["proposal_sha256", "authority_receipt_sha256"],
        ),
        (
            "operation",
            f"operations/{operation}.json",
            obj.original_record_sha256,
            plan["after_operation"],
            [],
        ),
        ("compaction", f"terminal-v1/{operation}.json", None, plan["terminal"], []),
    )
    return [
        dict(role=role, path=path, before_sha256=before, image_sha256=sha(image), authority_bindings=bindings)
        for role, path, before, image, bindings in roles
    ]


def primary_child_descriptor(obj: PrimaryChildDescriptor) -> None:
    """Validate the closed observation and stable plan; the queue proves live facts."""
    child, intent = obj.primary_child, obj.primary_child.intent
    if (
        not 1 <= obj.effect_count <= 4096
        or child.ordinal != obj.effect_count
        or obj.local_records
        or not 1 <= obj.proposal_generation <= 64
        or len(canonical(obj)) > 65_536
    ):
        raise Invalid("primary-scope")
    for snapshot in (intent.before, intent.after, child.observed):
        if snapshot is not None and (
            snapshot.sha256 != hashlib.sha256(b"").hexdigest() or snapshot.mode not in (0o600, 0o644)
        ):
            raise Invalid("primary-marker")
    for value, name in ((intent.source, "beads.py"), (intent.executable, "btq"), (intent.path, "paused")):
        if (
            len(value) > 4096
            or not Path(value).is_absolute()
            or str(Path(value)) != value
            or ".." in Path(value).parts
            or Path(value).name != name
            or not re.fullmatch(r"[\x21-\x7e]+", value)
        ):
            raise Invalid("primary-path")
    try:
        identity = intent.actor.split(":")[-1]
        valid_actor = str(uuid.UUID(identity)) == identity
    except ValueError:
        valid_actor = False
    if (
        not valid_actor
        or not re.fullmatch(r"[\x21-\x7e]{1,256}", intent.actor)
        or "+" in intent.actor
        or Path(intent.path).parent.name != hashlib.sha256(intent.actor.encode()).hexdigest()
        or (intent.kind == "resume") != (intent.after is None)
    ):
        raise Invalid("primary-intent")
    expected = (
        "observed-invariant"
        if child.observed == intent.before == intent.after
        else "observed-after"
        if child.observed == intent.after
        else "observed-before"
        if child.observed == intent.before
        else "observed-other"
    )
    coverage = dict(
        schema="primary-child-local-coverage.v1",
        **{key: getattr(intent, key) for key in ("path", "source", "executable", "actor")},
    )
    if (
        child.classification != expected
        or child.intent_payload_sha256 != sha(intent)
        or msgspec.to_builtins(child.coverage) != coverage
        or not all(
            HEX.fullmatch(value)
            for value in (
                child.intent_envelope_sha256,
                child.effects_after_root,
                obj.program_sha256,
            )
        )
    ):
        raise Invalid("primary-observation")
    primary_child_plan(obj)
    if msgspec.to_builtins(obj.allowed_writes) != primary_child_writes(obj):
        raise Invalid("primary-write-bindings")
    if obj.expires_at != stamp(obj.expires_at).strftime("%Y-%m-%dT%H:%M:%SZ"):
        raise Invalid("primary-expiry")


def primary_child_plan(obj: PrimaryChildDescriptor) -> None:
    """Freeze every business image; only two receipt authority digest slots vary."""
    plan, child = obj.terminal_plan, obj.primary_child
    intent = child.intent
    before = primary_closed(
        plan.before_effect,
        (
            "schema",
            "operation",
            "ordinal",
            "kind",
            "subject",
            "intent_sha256",
            "source_closure",
            "phase",
            "evidence",
        ),
    )
    primary_closed(plan.after_effect, tuple(before))
    subject = {"type": "local", "id": child.intent_payload_sha256}
    if (
        before["schema"] != "maintenance-effect.v1"
        or before["operation"] != obj.operation
        or type(before["ordinal"]) is not int
        or before["ordinal"] != obj.effect_count
        or before["kind"] != "local"
        or before["subject"] != subject
        or before["intent_sha256"] != child.intent_payload_sha256
        or before["source_closure"] != obj.source_closure
        or before["phase"] not in ("issuing", "unknown")
        or before["evidence"] is not None
    ):
        raise Invalid("primary-before-effect")
    payload = dict(
        schema="primary-child-observed-outcome.v1",
        proposal_id=obj.proposal_id,
        proposal_generation=obj.proposal_generation,
        **{
            key: getattr(child, key)
            for key in (
                "intent_envelope_sha256",
                "intent_payload_sha256",
                "intent",
                "observed",
                "classification",
                "original_action_outcome",
            )
        },
    )
    proof = dict(
        schema="maintenance-effect-proof.v1",
        operation=obj.operation,
        ordinal=obj.effect_count,
        source_closure=obj.source_closure,
        intent_sha256=child.intent_payload_sha256,
        phase="observed-primary-child",
        payload=msgspec.to_builtins(payload),
    )
    evidence = primary_closed(
        plan.after_effect["evidence"], ("adapter_sha256", "proof_sha256", "result_id", "readback_sha256")
    )
    if not isinstance(evidence["adapter_sha256"], str) or not HEX.fullmatch(evidence["adapter_sha256"]):
        raise Invalid("primary-adapter")
    expected_evidence = dict(
        adapter_sha256=evidence["adapter_sha256"],
        proof_sha256=sha(proof),
        result_id=sha(payload),
        readback_sha256=sha(payload),
    )
    if (
        plan.proof != proof
        or evidence != expected_evidence
        or plan.after_effect != dict(before, phase="observed-primary-child", evidence=expected_evidence)
        or plan.before_operation_sha256 != obj.original_record_sha256
        or sha(plan) != obj.program_sha256
    ):
        raise Invalid("primary-proof-images")
    operation = primary_closed(
        plan.after_operation,
        (
            "schema",
            "operation",
            "action",
            "actor",
            "source_closure",
            "state",
            "ordinal",
            "intent_sha256",
            "owner",
        ),
    )
    primary_owner(operation["owner"])
    if (
        operation["schema"] != "maintenance-operation.v1"
        or operation["operation"] != obj.operation
        or operation["action"] not in ("hermes.task", "harness.cli.task_dispatch")
        or operation["actor"] != intent.actor
        or operation["source_closure"] != obj.source_closure
        or operation["state"] != "complete"
        or type(operation["ordinal"]) is not int
        or operation["ordinal"] != obj.effect_count
        or operation["intent_sha256"] != child.intent_payload_sha256
    ):
        raise Invalid("primary-operation-image")
    receipt = plan.receipt
    primary_closed(
        receipt,
        (
            "schema",
            "proposal_sha256",
            "authority_receipt_sha256",
            "operation",
            "original_record_sha256",
            "effects_before_root",
            "effects_after_root",
            "effect_count",
            "proof_set_sha256",
            "result",
            "original_action_outcome",
            "prepared_by",
            "prepared_at",
        ),
    )
    primary_owner(receipt["prepared_by"])
    prepared = receipt["prepared_at"]
    if not isinstance(prepared, str) or prepared != stamp(prepared).strftime("%Y-%m-%dT%H:%M:%SZ"):
        raise Invalid("primary-prepared-time")
    expected_receipt = dict(
        schema="maintenance-primary-child-receipt.v1",
        proposal_sha256="$proposal-sha256",
        authority_receipt_sha256="$authenticated-permit-sha256",
        operation=obj.operation,
        original_record_sha256=obj.original_record_sha256,
        effects_before_root=obj.effects_root,
        effects_after_root=child.effects_after_root,
        effect_count=obj.effect_count,
        proof_set_sha256=obj.proof_set_sha256,
        result="settled-observed",
        original_action_outcome="not-established",
        prepared_by=receipt["prepared_by"],
        prepared_at=prepared,
    )
    if receipt != expected_receipt or type(receipt["effect_count"]) is not int:
        raise Invalid("primary-receipt-image")
    primary_child_terminal(obj)


def primary_owner(value: object) -> None:
    owner = primary_closed(value, ("pid", "start", "boot"))
    try:
        valid = isinstance(owner["boot"], str) and str(uuid.UUID(owner["boot"])) == owner["boot"]
    except ValueError:
        valid = False
    if (
        not valid
        or type(owner["pid"]) is not int
        or owner["pid"] <= 0
        or type(owner["start"]) is not int
        or owner["start"] < 0
    ):
        raise Invalid("primary-owner")


def primary_child_terminal(obj: PrimaryChildDescriptor) -> None:
    terminal = obj.terminal_plan.terminal
    keys = (
        "schema",
        "operation",
        "original",
        "original_sha256",
        "effect_count",
        "effects_root",
        "effects",
        "adapter_verdicts",
        "primary_child_intents",
    )
    if "approval_native_intents" in terminal:
        keys += ("approval_native_intents",)
    primary_closed(terminal, keys)
    if (
        terminal["schema"] != "maintenance-terminal.v1"
        or terminal["operation"] != obj.operation
        or terminal["original"] != obj.terminal_plan.after_operation
        or terminal["original_sha256"] != sha(obj.terminal_plan.after_operation)
        or type(terminal["effect_count"]) is not int
        or terminal["effect_count"] != obj.effect_count
        or terminal["effects_root"] != obj.primary_child.effects_after_root
    ):
        raise Invalid("primary-terminal")
    if not isinstance(terminal["effects"], list) or not isinstance(terminal["adapter_verdicts"], list):
        raise Invalid("primary-terminal-effects")
    effects = cast(list[object], terminal["effects"])
    verdicts = cast(list[object], terminal["adapter_verdicts"])
    if (
        len(effects) != obj.effect_count
        or len(verdicts) != obj.effect_count
        or sha(effects) != terminal["effects_root"]
    ):
        raise Invalid("primary-terminal-effects")
    phases = {
        "not-started",
        "refused-pre-effect",
        "admitted-local",
        "finished-native",
        "observed-native",
        "reconciled",
        "abandoned-diagnostic",
        "finished-pickup-hook",
        "finished-controller-files",
        "finished-primary-pickup",
        "finished-primary-child",
        "observed-primary-child",
    }
    for ordinal, (raw_effect, raw_verdict) in enumerate(zip(effects, verdicts, strict=True), 1):
        if not isinstance(raw_effect, list) or not isinstance(raw_verdict, list):
            raise Invalid("primary-terminal-effects")
        effect, verdict = cast(list[object], raw_effect), cast(list[object], raw_verdict)
        if (
            len(effect) != 2
            or type(effect[0]) is not int
            or effect[0] != ordinal
            or not isinstance(effect[1], str)
            or not HEX.fullmatch(effect[1])
            or len(verdict) != 3
            or type(verdict[0]) is not int
            or verdict[0] != ordinal
            or not isinstance(verdict[1], str)
            or verdict[1] not in phases
        ):
            raise Invalid("primary-terminal-effects")
        evidence = primary_closed(
            verdict[2], ("adapter_sha256", "proof_sha256", "result_id", "readback_sha256")
        )
        for key in ("adapter_sha256", "proof_sha256", "readback_sha256"):
            value = evidence[key]
            if key == "readback_sha256" and value is None:
                continue
            if not isinstance(value, str) or not HEX.fullmatch(value):
                raise Invalid("primary-terminal-evidence")
        result_id = evidence["result_id"]
        if result_id is not None and (
            not isinstance(result_id, str) or not re.fullmatch(r"[\x21-\x7e]{1,256}", result_id)
        ):
            raise Invalid("primary-terminal-evidence")
    if effects[-1] != [obj.effect_count, sha(obj.terminal_plan.after_effect)] or verdicts[-1] != [
        obj.effect_count,
        "observed-primary-child",
        obj.terminal_plan.after_effect["evidence"],
    ]:
        raise Invalid("primary-terminal-last")
    for name, extended in (("primary_child_intents", True), ("approval_native_intents", False)):
        raw_rows = terminal.get(name, [])
        if not isinstance(raw_rows, list):
            raise Invalid("primary-retained-intents")
        rows = cast(list[object], raw_rows)
        if len(rows) > 4097:
            raise Invalid("primary-retained-intents")
        names: set[str] = set()
        for row in rows:
            entry = primary_closed(
                row, ("name", "sha256", "pre_effect_orphan") if extended else ("name", "sha256")
            )
            filename, digest = entry["name"], entry["sha256"]
            if (
                not isinstance(filename, str)
                or not re.fullmatch(re.escape(obj.operation) + r"\.[0-9]{4}\.json", filename)
                or filename in names
                or not isinstance(digest, str)
                or not HEX.fullmatch(digest)
                or extended
                and type(entry["pre_effect_orphan"]) is not bool
            ):
                raise Invalid("primary-retained-intents")
            names.add(filename)
        if extended and not any(
            row
            == dict(
                name=f"{obj.operation}.{obj.effect_count:04d}.json",
                sha256=obj.primary_child.intent_envelope_sha256,
                pre_effect_orphan=False,
            )
            for row in rows
        ):
            raise Invalid("primary-retained-coverage")


def check(req: Request) -> str | None:
    try:
        decode(msgspec.json.encode(req), cast(type[Request], Request))
        if isinstance(req, Register):
            obj = descriptor(canonical(req.descriptor))
            if req.proof_set_sha256 != obj.proof_set_sha256:
                raise Invalid("proof-set")
        elif not HEX.fullmatch(req.proposal_sha256):
            raise Invalid("hash")
        elif isinstance(req, Permit) and req.certificate_sha256 is not None:
            if not HEX.fullmatch(req.certificate_sha256):
                raise Invalid("hash")
    except (Invalid, TypeError, ValueError):
        return "invalid-request"
    return None


def private_read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise Invalid("unsafe-file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = stream.read(MAX_BYTES + 1)
        if len(value) > MAX_BYTES:
            raise Invalid("oversized")
        return value
    finally:
        os.close(fd)


def publish(path: Path, data: bytes) -> None:
    """Atomic no-replace immutable publication; a crash never exposes a partial file."""
    private_parents(path.parent)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise Invalid("unsafe-directory")
    temporary = path.parent / (".recovery-" + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            repair_alias(path, data)
            if private_read(path) != data:
                raise Invalid("publication-conflict") from None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            temporary.unlink()
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def private_parents(path: Path) -> None:
    """Create every missing ancestor privately without changing an existing ancestor."""
    missing: list[Path] = []
    current = path
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
    # The publication parent must itself be private, even when it already existed.
    for directory in [path, *missing]:
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise Invalid("unsafe-directory")


def repair_alias(path: Path, data: bytes) -> None:
    """Repair only the exact link-before-unlink crash of our immutable publisher.

    An arbitrary extra link is never accepted. A bounded private-directory scan
    must find exactly one own temporary alias to this exact inode and byte value.
    """
    info = path.lstat()
    if info.st_nlink == 1:
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 2
    ):
        raise Invalid("unsafe-file")
    alias = None
    with os.scandir(path.parent) as entries:
        for count, entry in enumerate(entries):
            if count >= 256:
                raise Invalid("publication-directory-bound")
            if not re.fullmatch(r"\.recovery-[0-9a-f]{32}", entry.name):
                continue
            other = entry.stat(follow_symlinks=False)
            if (other.st_dev, other.st_ino) == (info.st_dev, info.st_ino):
                if alias is not None:
                    raise Invalid("unsafe-file")
                alias = Path(entry.path)
    if alias is None:
        raise Invalid("unsafe-file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        now = os.fstat(fd)
        if (now.st_dev, now.st_ino, now.st_nlink) != (info.st_dev, info.st_ino, 2):
            raise Invalid("unsafe-file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            if stream.read(MAX_BYTES + 1) != data:
                raise Invalid("publication-conflict")
        alias.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)


class Enrollment(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Installed data-only locator; publishing it is a separate deployment action."""

    schema: Literal["admind-maintenance-recovery-enrollment.v1"]
    enrollment_sha256: str
    source_closure: str
    coordinator_root: str
    descriptor_links: dict[str, str] = {}


SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_recovery_proposals (
 hash TEXT PRIMARY KEY, operation TEXT NOT NULL, descriptor BLOB NOT NULL,
 notice_key TEXT NOT NULL UNIQUE, notice_text TEXT NOT NULL, created_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting-consent','consented','revoked','terminal')),
 decision TEXT, operator TEXT, policy TEXT, generation INTEGER NOT NULL DEFAULT 0,
 permit BLOB, terminal BLOB);
CREATE TABLE IF NOT EXISTS maintenance_recovery_current (
 operation TEXT PRIMARY KEY, proposal TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS maintenance_recovery_conflicts (
 proposal TEXT PRIMARY KEY, account TEXT NOT NULL, group_id TEXT NOT NULL,
 message_id TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS maintenance_recovery_messages (
 account TEXT NOT NULL, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, proposal TEXT NOT NULL, provenance BLOB NOT NULL,
 outcome TEXT NOT NULL, PRIMARY KEY(account,group_id,message_id));
CREATE TABLE IF NOT EXISTS maintenance_recovery_revocations (
 account TEXT NOT NULL, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, proposal TEXT NOT NULL UNIQUE, provenance BLOB NOT NULL,
 outcome TEXT NOT NULL, PRIMARY KEY(account,group_id,message_id));
CREATE TABLE IF NOT EXISTS maintenance_recovery_refusals (
 account TEXT NOT NULL, group_id TEXT NOT NULL, message_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, provenance BLOB NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(account,group_id,message_id));
"""


class MessageFields(TypedDict):
    account: str
    group: str
    mid: str
    sender: str
    target: str | None
    text: str
    authority: Callable[[], str | None]


class Service:
    """All mutating calls join Store transactions and daemon work_lock serialization.

    The pure core's `authority` callback is a fresh host observation, never request JSON.
    It is called twice without awaits around each transaction's decision boundary.
    """

    def __init__(
        self,
        store: Store,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        boottime: Callable[[], int] | None = None,
        boot: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.root = root
        self.clock = clock or (lambda: datetime.now(UTC))
        self.boottime = boottime or boot_ns
        self.boot = boot or boot_id
        with store.transaction():
            self.upgrade_proposals()
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    store.db.execute(statement)

    def upgrade_proposals(self) -> None:
        """Remove the legacy per-operation uniqueness without discarding immutable history.

        This known schema has no foreign keys, user indexes or triggers. Refuse unexpected
        dependents rather than silently dropping them during the transactional rebuild.
        """
        db = self.store.db
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='maintenance_recovery_proposals'"
        ).fetchone()
        if row is None or "operation TEXT NOT NULL UNIQUE" not in row[0]:
            return
        if (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE tbl_name='maintenance_recovery_proposals' "
                "AND type IN ('trigger','index') AND sql IS NOT NULL"
            ).fetchone()
            is not None
        ):
            raise Invalid("proposal-schema")
        expected = (
            SCHEMA.split(";")[0]
            .strip()
            .replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
            .replace("operation TEXT NOT NULL,", "operation TEXT NOT NULL UNIQUE,")
        )
        if " ".join(row[0].split()) != " ".join(expected.split()):
            raise Invalid("proposal-schema")
        db.execute(
            SCHEMA.split(";")[0].replace("maintenance_recovery_proposals", "maintenance_recovery_upgrade")
        )
        db.execute("INSERT INTO maintenance_recovery_upgrade SELECT * FROM maintenance_recovery_proposals")
        db.execute(
            "CREATE TABLE IF NOT EXISTS maintenance_recovery_current "
            "(operation TEXT PRIMARY KEY, proposal TEXT NOT NULL UNIQUE)"
        )
        db.execute(
            "INSERT INTO maintenance_recovery_current SELECT operation,hash "
            "FROM maintenance_recovery_proposals"
        )
        db.execute("DROP TABLE maintenance_recovery_proposals")
        db.execute("ALTER TABLE maintenance_recovery_upgrade RENAME TO maintenance_recovery_proposals")

    def current(self, operation: str, key: str) -> bool:
        row = self.store.db.execute(
            "SELECT proposal FROM maintenance_recovery_current WHERE operation=?", (operation,)
        ).fetchone()
        return row is not None and row[0] == key

    def current_boot(self) -> tuple[str, int]:
        boot, tick = self.boot(), self.boottime()
        if type(boot) is not str:
            raise Invalid("boot")
        try:
            if str(uuid.UUID(boot)) != boot:
                raise ValueError
        except ValueError:
            raise Invalid("boot") from None
        if type(tick) is not int or tick < 0:
            raise Invalid("clock")
        return boot, tick

    def supersede(self, replacement: RecoveryDescriptor, enrolled: Enrollment) -> None:
        """Preserve one active scope; inactive proposals may change protocol."""
        row = self.store.db.execute(
            "SELECT p.state,p.permit,p.terminal,p.hash,p.descriptor FROM maintenance_recovery_current c "
            "JOIN maintenance_recovery_proposals p ON p.hash=c.proposal WHERE c.operation=?",
            (replacement.operation,),
        ).fetchone()
        if row is None:
            return
        if row[0] == "terminal" or row[2] is not None:
            raise Invalid("operation-terminal")
        if row[1] is not None:
            old = decode(bytes(row[1]), dict[str, object])
            boot, tick = self.current_boot()
            deadline = old.get("initiate_before_boottime_ns")
            if type(deadline) is not int:
                raise Invalid("clock")
            if old.get("boot_id") != boot:
                raise Invalid("boot-changed")
            if tick < deadline:
                raise Invalid("permit-active")
            # Expiration/absence alone never proves nonconsumption. Require the
            # queue's exact, durable positive observation and generation tombstone.
            key = str(row[3])
            cert = private_read(
                Path(enrolled.coordinator_root)
                / "recovery-v1"
                / f"{key}.nonconsumption-{old['generation']}.json"
            )
            self.certificate(
                descriptor(bytes(row[4])), Permit(key, hashlib.sha256(cert).hexdigest()), old, enrolled
            )
            return
        # No issued authority exists. Inspect only stored lifecycle fields so a
        # newer descriptor validator cannot permanently strand an old proposal.
        previous = decode(bytes(row[4]), dict[str, object])
        if previous.get("schema") != replacement.schema:
            expiry = previous.get("expires_at")
            if not isinstance(expiry, str):
                raise Invalid("time")
            if row[0] != "revoked" and stamp(expiry) > self.clock():
                raise Invalid("recovery-scope-conflict")

    def enrollment(self) -> Enrollment:
        obj = decode(private_read(self.root / "enrollment.json"), Enrollment)
        if (
            not HEX.fullmatch(obj.enrollment_sha256)
            or not HEX.fullmatch(obj.source_closure)
            or not Path(obj.coordinator_root).is_absolute()
            or len(obj.descriptor_links) > 128
            or any(not HEX.fullmatch(key) or len(link) > 2048 for key, link in obj.descriptor_links.items())
        ):
            raise Invalid("enrollment")
        graph = decode(private_read(Path(obj.coordinator_root) / "enrollment.json"), dict[str, object])
        sources = graph.get("sources")
        roots = graph.get("roots")
        if (
            sha(graph) != obj.enrollment_sha256
            or graph.get("root") != obj.coordinator_root
            or graph.get("schema") != "queue-maintenance-fence.v1"
            or type(graph.get("uid")) is not int
            or graph.get("uid") != os.getuid()
            or not isinstance(sources, dict)
            or not sources
            or len(cast(dict[str, object], sources)) > 256
            or not isinstance(roots, dict)
            or cast(dict[str, object], roots).get("admind_maintenance_recovery") != str(self.root)
            or sha(cast(dict[str, object], sources)) != obj.source_closure
        ):
            raise Invalid("enrollment-drift")
        for filename, expected in cast(dict[str, object], sources).items():
            if (
                not Path(filename).is_absolute()
                or not isinstance(expected, str)
                or not HEX.fullmatch(expected)
                or source_hash(Path(filename)) != expected
            ):
                raise Invalid("source-drift")
        coordinator = graph.get("coordinator_path")
        if (
            not isinstance(coordinator, str)
            or not Path(coordinator).is_absolute()
            or source_hash(Path(coordinator)) != graph.get("coordinator_sha256")
        ):
            raise Invalid("coordinator-drift")
        coverage = decode(private_read(Path(obj.coordinator_root) / "coverage.json"), dict[str, object])
        if sha(coverage) != graph.get("coverage_sha256"):
            raise Invalid("coverage-drift")
        return obj

    def get(self, key: str) -> dict[str, object]:
        if not HEX.fullmatch(key):
            raise Invalid("hash")
        with self.store.transaction(read_only=True):
            row = self.store.db.execute(
                "SELECT state,generation,permit,terminal FROM maintenance_recovery_proposals WHERE hash=?",
                (key,),
            ).fetchone()
            if row is None:
                raise Invalid("missing")
            return {
                "proposal_sha256": key,
                "state": row[0],
                "generation": row[1],
                "permit": None if row[2] is None else json.loads(row[2]),
                "terminal": None if row[3] is None else json.loads(row[3]),
            }

    def live(self, obj: RecoveryDescriptor, created: str | None = None) -> datetime:
        at = self.clock()
        if at.tzinfo is None or not math.isfinite(at.timestamp()):
            raise Invalid("clock")
        expiry = stamp(obj.expires_at)
        if expiry <= at or expiry > at + timedelta(hours=24):
            raise Invalid("expired")
        if created is not None and at < stamp(created):
            raise Invalid("clock-rollback")
        return at

    def original_provenance(self, obj: RecoveryDescriptor, enrolled: Enrollment) -> str:
        """Read local evidence only; the queue repeats fresh target validation under EX."""
        root = Path(enrolled.coordinator_root)
        if obj.mode != "finish-health":
            origin = decode(private_read(root / "operations" / f"{obj.operation}.json"), dict[str, object])
            if sha(origin) != obj.original_record_sha256:
                raise Invalid("original-mismatch")
            actor = origin.get("actor")
            if not isinstance(actor, str) or not re.fullmatch(r"[\x21-\x7e]{1,256}", actor):
                raise Invalid("actor")
            return actor
        backup = decode(private_read(root / "backup.json"), BackupRecord)
        if sha(backup) != obj.original_record_sha256 or sha(backup) != obj.backup_before_sha256:
            raise Invalid("backup-mismatch")
        owner = backup.owner
        try:
            valid_boot = str(uuid.UUID(owner.boot)) == owner.boot
        except ValueError:
            valid_boot = False
        if (
            backup.operation != obj.operation
            or owner.pid <= 0
            or owner.start < 0
            or not valid_boot
            or not 0 <= backup.ordinal <= 10000
            or (backup.ordinal == 0) != (backup.intent_sha256 is None)
            or (backup.intent_sha256 is not None and not HEX.fullmatch(backup.intent_sha256))
            or not HEX.fullmatch(backup.source_sha256)
        ):
            raise Invalid("backup-identity")
        graph = decode(private_read(root / "enrollment.json"), dict[str, object])
        coverage = decode(private_read(root / "coverage.json"), dict[str, object])
        if sha(graph) != enrolled.enrollment_sha256 or sha(coverage) != graph.get("coverage_sha256"):
            raise Invalid("enrollment-drift")
        policy, sources = coverage.get("backup_policy"), graph.get("sources")
        if (
            not isinstance(policy, dict)
            or not isinstance(sources, dict)
            or not isinstance(graph.get("profile"), dict)
        ):
            raise Invalid("backup-source")
        source = cast(dict[str, object], policy).get("source")
        if (
            not isinstance(source, str)
            or cast(dict[str, object], sources).get(source) != backup.source_sha256
        ):
            raise Invalid("backup-source")
        failure = decode(private_read(root / "backup-failure.json"), dict[str, object])
        legacy = {"schema", "operation"}
        current = legacy | {"source_sha256", "profile_sha256"}
        if (
            sha(failure) != obj.failure_before_sha256
            or set(failure) not in (legacy, current)
            or failure.get("schema") != "maintenance-backup-failure.v1"
            or failure.get("operation") != obj.operation
            or (
                set(failure) == current
                and (
                    failure.get("source_sha256") != backup.source_sha256
                    or failure.get("profile_sha256") != sha(graph.get("profile"))
                )
            )
        ):
            raise Invalid("backup-failure")
        return f"pid={owner.pid} start={owner.start} boot={owner.boot}"

    def register(
        self,
        req: Register,
        *,
        uid: int | None,
        pid: int | None,
        chunk_chars: int,
        post: Callable[[str, str], None],
    ) -> Reply:
        if uid != os.getuid():
            raise Invalid("peer")
        if check(req):
            raise Invalid("request")
        obj = descriptor(canonical(req.descriptor))
        enrolled = self.enrollment()
        if (
            obj.enrollment_sha256 != enrolled.enrollment_sha256
            or obj.source_closure != enrolled.source_closure
        ):
            raise Invalid("enrollment-mismatch")
        at = self.live(obj)
        key = sha(obj)
        provenance = self.original_provenance(obj, enrolled)
        text = render(obj, provenance, enrolled.descriptor_links.get(key), min(chunk_chars, 3200))
        from heterodyne.admind import notices

        notice_key = ""
        with self.store.transaction():
            previous = self.store.db.execute(
                "SELECT descriptor FROM maintenance_recovery_proposals WHERE hash=?",
                (key,),
            ).fetchone()
            if previous is not None:
                if bytes(previous[0]) != canonical(obj):
                    raise Invalid("immutable-proposal")
                return Reply("existing", "proposal already registered", self.get(key))
            self.supersede(obj, enrolled)
            if (
                self.store.db.execute("SELECT count(*) FROM maintenance_recovery_proposals").fetchone()[0]
                >= 128
            ):
                raise Invalid("capacity")
            if (
                self.store.notices_since((at - timedelta(hours=1)).isoformat()) >= notices.NOTICE_PER_HOUR
                or self.store.undelivered_notices() >= notices.NOTICE_UNDELIVERED
            ):
                raise Invalid("notice-capacity")
            notice_id = notices.new_id(self.store.notice_exists)
            notice_key = "notice:" + notice_id
            self.store.db.execute(
                "INSERT INTO maintenance_recovery_proposals "
                "(hash,operation,descriptor,notice_key,notice_text,created_at,state) VALUES(?,?,?,?,?,?,?)",
                (key, obj.operation, canonical(obj), notice_key, text, at.isoformat(), "awaiting-consent"),
            )
            self.store.db.execute(
                "INSERT INTO maintenance_recovery_current VALUES(?,?) "
                "ON CONFLICT(operation) DO UPDATE SET proposal=excluded.proposal",
                (obj.operation, key),
            )
            self.store.insert_notice(notice_id, "maintenance", pid, 0, len(text), 0, 1, at.isoformat())
            post(notice_key + ":0", text)
        return Reply("registered", "awaiting delivered-notice consent", self.get(key))

    def message(
        self,
        *,
        account: str,
        group: str,
        mid: str,
        sender: str,
        target: str | None,
        text: str,
        authority: Callable[[], str | None],
        post: Callable[[str], None] | None = None,
    ) -> Reply:
        if len(text.encode("utf-8")) > 1024:
            raise Invalid("command-bound")
        command = COMMAND.fullmatch(text)
        if command is None or not all(HEX.fullmatch(v) for v in (account, mid, sender)):
            raise Invalid("command-auth")
        if not re.fullmatch(r"(?:[0-9a-f]{2}){1,128}", group):
            raise Invalid("group")
        provenance = dict(
            account=account,
            group=group,
            message_id=mid,
            sender=sender,
            reply_to=target,
            text=text,
        )
        payload = sha(provenance)
        with self.store.transaction():
            duplicate = self.store.db.execute(
                "SELECT payload_sha256,outcome,proposal FROM maintenance_recovery_messages "
                "WHERE account=? AND group_id=? AND message_id=? UNION ALL "
                "SELECT payload_sha256,outcome,proposal FROM maintenance_recovery_revocations "
                "WHERE account=? AND group_id=? AND message_id=?",
                (account, group, mid, account, group, mid),
            ).fetchone()
            if duplicate is not None:
                if duplicate[0] != payload:
                    raise Invalid("replay-mismatch")
                if post is not None:
                    post("Maintenance recovery: " + duplicate[1] + ".")
                return Reply("duplicate", duplicate[1], self.get(duplicate[2]))
            refused = self.store.db.execute(
                "SELECT payload_sha256,reason FROM maintenance_recovery_refusals "
                "WHERE account=? AND group_id=? AND message_id=?",
                (account, group, mid),
            ).fetchone()
            if refused is not None:
                if refused[0] != payload:
                    raise Invalid("replay-mismatch")
                if post is not None:
                    post("Maintenance recovery refused: " + refused[1] + ".")
                return Reply("duplicate", refused[1])
            policy = authority()
            if policy is None:
                raise Invalid("authority")
            provenance["policy_sha256"] = policy
            rows = self.store.db.execute(
                "SELECT hash,descriptor,notice_key,state,created_at,notice_text FROM "
                "maintenance_recovery_proposals WHERE substr(hash,1,24)=?",
                (command[2],),
            ).fetchall()
            if len(rows) != 1:
                raise Invalid("token")
            key, raw, notice_key, state, created, notice_text = rows[0]
            deliveries = self.store.db.execute(
                "SELECT message_id,text,status FROM outbox WHERE key IN (?,?) AND status='sent'",
                (notice_key + ":0", notice_key + ":r0"),
            ).fetchall()
            delivery = deliveries[0] if len(deliveries) == 1 else None
            if delivery is None or target is None or delivery[0] != target or delivery[1] != notice_text:
                raise Invalid("thread-unmapped")
            try:
                obj = descriptor(bytes(raw))
                self.live(obj, created)
                live = self.current(obj.operation, key)
            except Invalid:
                live = False
            revoking = command[1] == "revoke" and state in ("awaiting-consent", "consented")
            if (
                not revoking
                and self.store.db.execute(
                    "SELECT count(*) FROM maintenance_recovery_messages WHERE proposal=?", (key,)
                ).fetchone()[0]
                >= 128
            ):
                raise Invalid("message-capacity")
            if authority() != policy:
                raise Invalid("authority-drift")
            outcome = "history-only"
            if revoking:
                self.store.db.execute(
                    "UPDATE maintenance_recovery_proposals SET state='revoked' WHERE hash=?", (key,)
                )
                outcome = "revoked"
            elif live and command[1] == "settle" and state == "awaiting-consent":
                self.store.db.execute(
                    "UPDATE maintenance_recovery_proposals SET state='consented',"
                    "decision=?,operator=?,policy=? WHERE hash=?",
                    (mid, sender, policy, key),
                )
                outcome = "consented"
            elif live and command[1] == "settle" and state == "consented":
                outcome = "duplicate-decision"
            table = "maintenance_recovery_revocations" if revoking else "maintenance_recovery_messages"
            self.store.db.execute(
                f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?)",
                (account, group, mid, payload, key, canonical(provenance), outcome),
            )
            if post is not None:
                post("Maintenance recovery: " + outcome + ".")
        return Reply("recorded", outcome, self.get(key))

    def refusal(
        self,
        *,
        account: str,
        group: str,
        mid: str,
        sender: str,
        target: str | None,
        text: str,
        reason: str,
        authority: Callable[[], str | None],
        post: Callable[[str], None],
    ) -> None:
        """Bounded authenticated refusal receipt; never a recovery decision."""
        provenance = dict(
            account=account, group=group, message_id=mid, sender=sender, reply_to=target, text=text
        )
        payload = sha(provenance)
        with self.store.transaction():
            old = self.store.db.execute(
                "SELECT payload_sha256,reason FROM maintenance_recovery_refusals "
                "WHERE account=? AND group_id=? AND message_id=?",
                (account, group, mid),
            ).fetchone()
            if old is not None:
                if old[0] != payload:
                    raise Invalid("replay-mismatch")
                post("Maintenance recovery refused: " + old[1] + ".")
                return
            if reason == "replay-mismatch":
                self.store.db.execute(
                    "INSERT OR IGNORE INTO maintenance_recovery_conflicts "
                    "SELECT proposal,account,group_id,message_id,? FROM maintenance_recovery_messages "
                    "WHERE account=? AND group_id=? AND message_id=? AND payload_sha256!=? UNION ALL "
                    "SELECT proposal,account,group_id,message_id,? FROM maintenance_recovery_revocations "
                    "WHERE account=? AND group_id=? AND message_id=? AND payload_sha256!=?",
                    (payload, account, group, mid, payload, payload, account, group, mid, payload),
                )
            # A contradiction to a retained authenticated ID is denial-only
            # evidence even when fresh policy is unreadable; never new consent.
            policy = authority()
            if policy is None:
                return
            if (
                self.store.db.execute("SELECT count(*) FROM maintenance_recovery_refusals").fetchone()[0]
                >= 128
            ):
                # Conflict protection is retained even when the diagnostic ledger is full.
                if reason == "replay-mismatch":
                    return
                raise Invalid("refusal-capacity")
            provenance["policy_sha256"] = policy
            self.store.db.execute(
                "INSERT INTO maintenance_recovery_refusals VALUES(?,?,?,?,?,?)",
                (account, group, mid, payload, canonical(provenance), reason),
            )
            post("Maintenance recovery refused: " + reason + ".")

    def permit(self, req: Permit, *, uid: int | None, authority: Callable[[str], str | None]) -> Reply:
        if uid != os.getuid():
            raise Invalid("peer")
        enrolled = self.enrollment()
        query = (
            "SELECT descriptor,state,decision,operator,policy,generation,permit,"
            "created_at FROM maintenance_recovery_proposals WHERE hash=?"
        )
        with self.store.lock:
            observed = self.store.db.execute(query, (req.proposal_sha256,)).fetchone()
        if observed is None:
            raise Invalid("not-consented")
        obj = descriptor(bytes(observed[0]))
        if not self.current(obj.operation, req.proposal_sha256):
            raise Invalid("superseded")
        if observed[1] != "consented":
            raise Invalid("not-consented")
        # Authority may cross back to the daemon loop. Never hold the Store lock
        # across that callback: current policy reads the same Store. Recheck the
        # complete proposal tuple under the writer transaction before issuance.
        current_policy = authority(observed[3])
        if current_policy is None:
            raise Invalid("authority")
        if current_policy != observed[4]:
            raise Invalid("policy-drift")
        previous = None if observed[6] is None else bytes(observed[6])
        repairing = previous is not None and req.certificate_sha256 is None
        if previous is None and req.certificate_sha256 is not None:
            raise Invalid("unexpected-certificate")
        if previous is not None and not repairing:
            if observed[5] >= 3:
                raise Invalid("generation-cap")
            old = decode(previous, dict[str, object])
            if old["boot_id"] != self.current_boot()[0]:
                raise Invalid("boot-changed")
            self.certificate(descriptor(bytes(observed[0])), req, old, enrolled)
        if authority(observed[3]) != current_policy:
            raise Invalid("authority-drift")
        with self.store.transaction():
            row = self.store.db.execute(query, (req.proposal_sha256,)).fetchone()
            if row != observed:
                raise Invalid("proposal-changed")
            obj = descriptor(bytes(row[0]))
            if not self.current(obj.operation, req.proposal_sha256):
                raise Invalid("superseded")
            if row[1] != "consented":
                raise Invalid("not-consented")
            if (
                self.store.db.execute(
                    "SELECT 1 FROM maintenance_recovery_conflicts WHERE proposal=?", (req.proposal_sha256,)
                ).fetchone()
                is not None
            ):
                raise Invalid("conflict")
            self.live(obj, row[7])
            generation = row[5]
            if previous is not None and repairing:
                encoded = previous
            else:
                boot, issued = self.current_boot()
                value = dict(
                    schema="maintenance-recovery-permit.v1",
                    permit_id=str(uuid.uuid4()),
                    proposal_sha256=req.proposal_sha256,
                    decision_message_id=row[2],
                    operator=row[3],
                    policy_sha256=current_policy,
                    boot_id=boot,
                    issued_boottime_ns=issued,
                    initiate_before_boottime_ns=issued + 30_000_000_000,
                    state="issued",
                    generation=generation + 1,
                    previous_generation_sha256=None
                    if previous is None
                    else hashlib.sha256(previous).hexdigest(),
                )
                encoded = canonical(value)
                self.store.db.execute(
                    "UPDATE maintenance_recovery_proposals SET generation=?,permit=? WHERE hash=?",
                    (generation + 1, encoded, req.proposal_sha256),
                )
        # DB commit precedes publication. A crash leaves exactly this candidate for idempotent repair.
        self.publish_permit(req.proposal_sha256, encoded)
        return Reply(
            "issued",
            "existing generation; never blindly reissue" if repairing else "single-use enrolled publication",
            self.get(req.proposal_sha256),
        )

    def certificate(
        self, obj: RecoveryDescriptor, req: Permit, old: dict[str, object], enrolled: Enrollment
    ) -> None:
        generation = old["generation"]
        folder = Path(enrolled.coordinator_root) / "recovery-v1"
        raw = private_read(folder / f"{req.proposal_sha256}.nonconsumption-{generation}.json")
        if (
            hashlib.sha256(raw).hexdigest() != req.certificate_sha256
            or private_read(folder / f"{req.proposal_sha256}.superseded-{generation}.json") != raw
        ):
            raise Invalid("certificate-publication")
        value = msgspec.to_builtins(decode(raw, Certificate))
        expected = dict(
            schema="maintenance-recovery-nonconsumption.v1",
            proposal_sha256=req.proposal_sha256,
            permit_sha256=hashlib.sha256(canonical(old)).hexdigest(),
            generation=generation,
            operation=obj.operation,
            original_record_sha256=obj.original_record_sha256,
            effects_root=obj.effects_root,
            enrollment_sha256=obj.enrollment_sha256,
            target_snapshot_sha256=target_snapshot(obj),
            boot_id=old["boot_id"],
            observed_boottime_ns=value.get("observed_boottime_ns"),
        )
        observed = value.get("observed_boottime_ns")
        old_issued = old.get("issued_boottime_ns")
        if (
            value != expected
            or not isinstance(observed, int)
            or isinstance(observed, bool)
            or not isinstance(old_issued, int)
            or observed < old_issued
            or observed > self.boottime()
        ):
            raise Invalid("certificate")
        stems = [(req.proposal_sha256, "")]
        if isinstance(obj, PrimaryChildDescriptor):
            stems.append((obj.proposal_id, "primary-"))
        for stem, prefix in stems:
            for suffix in ("consumed", "issuing", "receipt"):
                if os.path.lexists(folder / f"{stem}.{prefix}{suffix}.json"):
                    raise Invalid("consumption-unknown")

    def publish_permit(self, key: str, data: bytes) -> None:
        value = decode(data, dict[str, object])
        publish(self.root / "generations" / key / f"{value['generation']}.json", data)
        replace_current(self.root / "permits" / f"{key}.json", data)


def boot_id() -> str:
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if str(uuid.UUID(value)) != value:
        raise Invalid("boot")
    return value


def boot_ns() -> int:
    import time

    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def primary_marker_text(value: MarkerSnapshot | None) -> str:
    return "absent" if value is None else f"present, empty (sha256 of empty bytes), mode {value.mode:04o}"


def primary_child_card(obj: PrimaryChildDescriptor, actor: str, token: str) -> list[str]:
    """Display material observations and write roles without claiming command success."""
    child, intent = obj.primary_child, obj.primary_child.intent
    if actor != intent.actor:
        raise Invalid("primary-actor-mismatch")
    writes = "; ".join(
        f"{entry.role}: "
        + ("effect-proofs-v1/[linked proof SHA256].json" if entry.role == "proof" else entry.path)
        for entry in obj.allowed_writes
    )
    marker_root = str(Path(intent.path).parent.parent)
    marker_formula = f"{marker_root}/SHA256(original actor UTF-8, lowercase hex)/paused"
    return [
        "Primary pickup child observation settlement consent",
        "Approve: record this observation and settle the interrupted operation; never rerun the child.",
        f"Operation: {obj.operation}; proposal generation: {obj.proposal_generation}; "
        f"ordinal: {child.ordinal}",
        f"Original actor: {actor}; original request: {intent.kind}",
        f"Marker path derivation (not literal): {marker_formula}",
        f"Retained before: {primary_marker_text(intent.before)}",
        f"Retained after: {primary_marker_text(intent.after)}",
        f"Observed: {primary_marker_text(child.observed)}; classification: {child.classification}",
        "Original action outcome: not-established. Observation does not prove execution or causation.",
        f"Source: {intent.source}; child: {intent.executable}",
        "Queue must verify ended owner/child, exact route/local coverage and all earlier terminal proofs.",
        "Admind checks enrollment, original-record binding and consent; not these observation proof claims.",
        f"Allowed coordinator writes: {writes}",
        "Proof/effect/operation/terminal images are fixed; only named same-proposal permit bindings vary.",
        "No marker, DB, Hermes or pickup setting changes; no native replay or automatic resume.",
        "Exact hashes/images and binding slots are in the linked descriptor; "
        "the permit binds that descriptor.",
        f"Expires: {obj.expires_at}",
        f"Reply: !maintenance settle {token}",
        f"Revoke: !maintenance revoke {token}",
        "An already issued permit may start recovery for 30 seconds after revoke.",
    ]


def render(obj: RecoveryDescriptor, actor: str, link: str | None, budget: int) -> str:
    """Keep exact scope in the linked proposal; never exempt text from redaction."""
    native = isinstance(obj, (NativeDescriptor, PrimaryChildDescriptor))
    if isinstance(obj, PrimaryChildDescriptor) and link is None:
        raise Invalid("primary-evidence-link-required")
    if native and link is None:
        raise Invalid("native-evidence-link-required")
    token = sha(obj)[:24]
    if isinstance(obj, NativeDescriptor):
        effect = obj.native_effect
        lines = [
            "Native approval observation recovery consent",
            "Approve: settle the latest unknown native effect using the recorded observation.",
            f"Native task: {effect.task_id}; approval: {effect.approval_id}",
            f"Coordinator reports: {effect.verdict}; write family: {effect.family}; "
            f"ordinal: {effect.ordinal}",
            f"Operation: {obj.operation}",
            f"Original actor: {actor}",
            "Consent covers only this effect; the queue must verify earlier effects are terminal.",
            "Queue must verify writer/descendant cessation and a consistent exact native projection.",
            "Unapplied requires unchanged precondition after cessation; absence alone is insufficient.",
            "Admind checks enrollment, original-record binding and consent; not these native proof claims.",
            "No native replay; no command return code or causation is asserted.",
            "Exact hashes are in the linked descriptor; the authenticated permit binds that descriptor.",
            f"Expires: {obj.expires_at}",
            f"Reply: !maintenance settle {token}",
            f"Revoke: !maintenance revoke {token}",
            "An already issued permit may start recovery for 30 seconds after revoke.",
        ]
    elif isinstance(obj, PrimaryChildDescriptor):
        lines = primary_child_card(obj, actor, token)
    else:
        records = (
            ", ".join(f"{p.action} {p.selector.type}:{p.selector.id}" for p in obj.local_records) or "none"
        )
        consequences = (
            "Diagnostic may already be delivered; never resend it. Other native effects stay held."
            if obj.mode == "abandon-notice"
            else "No native replay or task/approval changes."
        )
        lines = [
            "Maintenance recovery consent",
            f"Operation: {obj.operation}",
            f"Backup owner: {actor}" if obj.mode == "finish-health" else f"Original actor: {actor}",
            f"Result: {obj.result}",
            f"Local records: {records}",
            f"Proof-covered effects: {obj.effect_count}",
            "Remaining native unknowns: never cleared by this consent.",
            consequences,
            f"Expires: {obj.expires_at}",
            f"Reply: !maintenance settle {token}",
            f"Revoke: !maintenance revoke {token}",
            "An already issued permit may start recovery for 30 seconds after revoke.",
        ]
    if link is not None:
        if not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/[0-9a-f]{40}/[A-Za-z0-9_./-]+", link
        ):
            raise Invalid("public-link")
        if native:
            lines.append("Exact proposal/source/proofs: " + link)
        else:
            lines.insert(5, "Exact proposal/source/proofs: " + link)
    else:
        lines.insert(5, "Source closure: " + obj.source_closure)
        lines.insert(6, "Exact proposal: " + sha(obj))
    text = "\n".join(lines)
    size = len(text.encode("utf-8")) if native else len(text)
    if size > budget or len(lines) > 36 or redact(text) != text:
        raise Invalid("notice-material-unrepresentable")
    return text


def replace_current(path: Path, data: bytes) -> None:
    """Atomic current pointer; immutable generation was fsynced before this call."""
    private_parents(path.parent)
    directory = path.parent.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.getuid()
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise Invalid("unsafe-directory")
    if path.exists() or path.is_symlink():
        private_read(path)
    temporary = path.parent / (".permit-" + uuid.uuid4().hex)
    publish(temporary, data)
    try:
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def source_hash(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16_777_216:
            raise Invalid("source-bound")
        value = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            remaining = 16_777_217
            while remaining:
                block = stream.read(min(65_536, remaining))
                if not block:
                    break
                remaining -= len(block)
                value.update(block)
            if remaining == 0:
                raise Invalid("source-bound")
        return value.hexdigest()
    finally:
        os.close(fd)
