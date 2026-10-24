"""Closed primary-child consent; disposable evidence, no native effects or transport."""

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import msgspec
import pytest
from test_admind_recovery import LINK, THREAD, Rig, write

from heterodyne.admind import recovery
from heterodyne.admind.redact import redact

FIXTURE = Path(__file__).parent / "fixtures" / "primary-child-settlement-v1.json"
FIXTURE_SHA = "499397ba9b59549c0453b071419eae5c225c9374c4c3ec16b725809f6105c403"


def value() -> dict[str, Any]:
    return json.loads(FIXTURE.read_bytes())["descriptor"]


def rebind(v: dict[str, Any]) -> None:
    """Rebuild exact derived images after a fixture's chosen business facts change."""
    c, plan = v["primary_child"], v["terminal_plan"]
    intent = c["intent"]
    c["intent_payload_sha256"] = recovery.sha(intent)
    c["coverage"] = dict(
        schema="primary-child-local-coverage.v1",
        **{k: intent[k] for k in ("path", "source", "executable", "actor")},
    )
    c["classification"] = (
        "observed-invariant"
        if c["observed"] == intent["before"] == intent["after"]
        else "observed-after"
        if c["observed"] == intent["after"]
        else "observed-before"
        if c["observed"] == intent["before"]
        else "observed-other"
    )
    before = plan["before_effect"]
    before.update(
        operation=v["operation"],
        ordinal=v["effect_count"],
        source_closure=v["source_closure"],
        intent_sha256=c["intent_payload_sha256"],
        subject=dict(type="local", id=c["intent_payload_sha256"]),
    )
    payload = dict(
        schema="primary-child-observed-outcome.v1",
        proposal_id=v["proposal_id"],
        proposal_generation=v["proposal_generation"],
        **{
            k: c[k]
            for k in (
                "intent_envelope_sha256",
                "intent_payload_sha256",
                "intent",
                "observed",
                "classification",
                "original_action_outcome",
            )
        },
    )
    plan["proof"].update(
        operation=v["operation"],
        ordinal=v["effect_count"],
        source_closure=v["source_closure"],
        intent_sha256=c["intent_payload_sha256"],
        payload=payload,
    )
    proof = plan["proof"]
    evidence = dict(
        adapter_sha256=plan["after_effect"]["evidence"]["adapter_sha256"],
        proof_sha256=recovery.sha(proof),
        result_id=recovery.sha(payload),
        readback_sha256=recovery.sha(payload),
    )
    plan["after_effect"] = dict(before, phase="observed-primary-child", evidence=evidence)
    plan["after_operation"].update(
        actor=intent["actor"],
        source_closure=v["source_closure"],
        intent_sha256=c["intent_payload_sha256"],
        ordinal=v["effect_count"],
    )
    plan["before_operation_sha256"] = v["original_record_sha256"]
    terminal = plan["terminal"]
    terminal.update(
        original=plan["after_operation"],
        original_sha256=recovery.sha(plan["after_operation"]),
        effect_count=v["effect_count"],
    )
    terminal["effects"][-1] = [v["effect_count"], recovery.sha(plan["after_effect"])]
    terminal["adapter_verdicts"][-1] = [v["effect_count"], "observed-primary-child", evidence]
    c["effects_after_root"] = recovery.sha(terminal["effects"])
    terminal["effects_root"] = c["effects_after_root"]
    plan["receipt"].update(
        original_record_sha256=v["original_record_sha256"],
        effects_before_root=v["effects_root"],
        effects_after_root=c["effects_after_root"],
        effect_count=v["effect_count"],
        proof_set_sha256=v["proof_set_sha256"],
    )
    v["program_sha256"] = recovery.sha(plan)
    # Constructing the typed value does not validate it or grant authority.
    obj = msgspec.convert(v, type=recovery.PrimaryChildDescriptor)
    v["allowed_writes"] = recovery.primary_child_writes(obj)


def parse(v):
    obj = recovery.descriptor(recovery.canonical(v))
    assert isinstance(obj, recovery.PrimaryChildDescriptor)
    return obj


def test_shared_queue_fixture_exact_snapshot_and_wire():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA
    fixture = json.loads(FIXTURE.read_bytes())
    obj = parse(fixture["descriptor"])
    assert isinstance(obj, recovery.PrimaryChildDescriptor)
    assert recovery.target_snapshot(obj) == recovery.sha(fixture["snapshot"])
    assert recovery.target_snapshot(obj) == "2d225bb5a9526bd40fc4d54669adcf45d1565b2da66e04a8b73e0603a0bb92d7"
    request = recovery.Register(obj, obj.proof_set_sha256)
    wire = recovery.decode(msgspec.json.encode(request), cast(type[recovery.Request], recovery.Request))
    assert recovery.check(wire) is None
    assert isinstance(wire, recovery.Register)
    assert parse(wire.descriptor) == obj


@pytest.mark.parametrize(
    "kind,before,after,observed,classification",
    [
        ("pause", None, 384, 384, "observed-after"),
        ("pause", None, 384, None, "observed-before"),
        ("pause", 420, 420, 420, "observed-invariant"),
        ("pause", None, 384, 420, "observed-other"),
        ("resume", 420, None, None, "observed-after"),
        ("resume", None, None, None, "observed-invariant"),
    ],
)
def test_observation_is_truthful_not_execution(kind, before, after, observed, classification):
    v = value()

    def snapshot(mode):
        return None if mode is None else dict(mode=mode, sha256=hashlib.sha256(b"").hexdigest())

    v["primary_child"]["intent"].update(kind=kind, before=snapshot(before), after=snapshot(after))
    v["primary_child"]["observed"] = snapshot(observed)
    rebind(v)
    obj = parse(v)
    assert obj.primary_child.classification == classification
    assert obj.primary_child.original_action_outcome == "not-established"
    card = recovery.render(obj, obj.primary_child.intent.actor, LINK, 3200)
    assert "Observation does not prove execution or causation" in card
    assert "never rerun the child" in card


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("schema",), "maintenance-recovery.v1"),
        (("mode",), "settle"),
        (("result",), "complete"),
        (("extra",), True),
        (("effect_count",), True),
        (("effect_count",), 0),
        (("proposal_generation",), 0),
        (("proposal_generation",), 65),
        (("primary_child", "ordinal"), 2),
        (("primary_child", "extra"), True),
        (("primary_child", "original_action_outcome"), "succeeded"),
        (("primary_child", "intent", "contract"), "shell.v1"),
        (("primary_child", "intent", "path"), "/invalid/paused"),
        (("primary_child", "coverage", "actor"), "other"),
        (("primary_child", "intent", "after", "mode"), 511),
        (("primary_child", "intent", "after", "sha256"), "a" * 64),
        (("terminal_plan", "extra"), True),
        (("terminal_plan", "before_effect", "evidence"), {}),
        (("terminal_plan", "after_operation", "action"), "shell"),
        (("terminal_plan", "receipt", "prepared_at"), "$now"),
        (("terminal_plan", "terminal", "extra"), True),
        (("expires_at",), "2026-10-24T00:00:00+00:00"),
    ],
)
def test_closed_shape_rejects_scope_or_truth_changes(path, replacement):
    v = value()
    target = v
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(recovery.Invalid):
        parse(v)
    assert recovery.check(recovery.Register(v, v["proof_set_sha256"])) == "invalid-request"


@pytest.mark.parametrize("role", range(7))
@pytest.mark.parametrize(
    "field,replacement",
    [
        ("path", "arbitrary/file"),
        ("image_sha256", "a" * 64),
        ("before_sha256", "b" * 64),
        ("authority_bindings", ["descriptor"]),
    ],
)
def test_allowed_writes_are_exact_roles_paths_images_and_slots(role, field, replacement):
    v = value()
    if v["allowed_writes"][role][field] == replacement:
        replacement = []
    v["allowed_writes"][role][field] = replacement
    with pytest.raises(recovery.Invalid):
        parse(v)


def test_terminal_earlier_evidence_preserves_nullable_native_identity():
    v = value()
    v["effect_count"] = v["primary_child"]["ordinal"] = 2
    terminal = v["terminal_plan"]["terminal"]
    terminal["effects"].insert(0, [1, "a" * 64])
    terminal["adapter_verdicts"].insert(
        0,
        [
            1,
            "not-started",
            dict(adapter_sha256="b" * 64, proof_sha256="c" * 64, result_id=None, readback_sha256=None),
        ],
    )
    terminal["primary_child_intents"][0]["name"] = v["operation"] + ".0002.json"
    rebind(v)
    assert parse(v).effect_count == 2
    terminal["adapter_verdicts"][0][2]["result_id"] = "btq-abc12"
    rebind(v)
    assert parse(v).effect_count == 2


def test_card_path_is_lossless_derivation_without_redactor_exemption():
    obj = parse(value())
    actor, path = obj.primary_child.intent.actor, Path(obj.primary_child.intent.path)
    assert path.parent.name == hashlib.sha256(actor.encode("utf-8")).hexdigest()
    text = recovery.render(obj, actor, LINK, 3200)
    assert f"{path.parent.parent}/SHA256(original actor UTF-8, lowercase hex)/paused" in text
    assert f"Original actor: {actor}" in text
    assert "effect-proofs-v1/[linked proof SHA256].json" in text
    for entry in obj.allowed_writes:
        assert entry.role in text
        if entry.role != "proof":
            assert entry.path in text
    assert redact(text) == text
    assert len(text.encode()) <= 3200
    with pytest.raises(recovery.Invalid, match="notice-material-unrepresentable"):
        recovery.render(obj, actor, LINK, len(text.encode()) - 1)
    with pytest.raises(recovery.Invalid, match="primary-actor-mismatch"):
        recovery.render(obj, "other", LINK, 3200)
    with pytest.raises(recovery.Invalid, match="primary-evidence-link-required"):
        recovery.render(obj, actor, None, 3200)


@pytest.mark.parametrize("material_part", ["actor", "root"])
def test_secret_looking_material_scope_refuses_instead_of_hiding(material_part):
    v = value()
    intent = v["primary_child"]["intent"]
    if material_part == "actor":
        intent["actor"] = "a" * 64 + ":" + intent["actor"].split(":")[-1]
        intent["path"] = str(
            Path(intent["path"]).parent.parent
            / hashlib.sha256(intent["actor"].encode()).hexdigest()
            / "paused"
        )
    else:
        intent["path"] = "/" + "a" * 64 + "/" + Path(intent["path"]).parent.name + "/paused"
    rebind(v)
    obj = parse(v)
    with pytest.raises(recovery.Invalid, match="notice-material-unrepresentable"):
        recovery.render(obj, intent["actor"], LINK, 3200)


@pytest.fixture
def primary_rig(tmp_path):
    rig = Rig(tmp_path)
    v = value()
    v.update(
        enrollment_sha256=rig.value["enrollment_sha256"],
        source_closure=rig.value["source_closure"],
        expires_at=(rig.at + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    rebind(v)
    original = dict(v["terminal_plan"]["after_operation"], state="unknown")
    v["original_record_sha256"] = recovery.sha(original)
    rebind(v)
    write(rig.queue / "operations" / f"{v['operation']}.json", original)
    rig.value, rig.obj = v, parse(v)
    rig.key = recovery.sha(rig.obj)
    rig.enrolled = msgspec.structs.replace(rig.enrolled, descriptor_links={rig.key: LINK})
    write(rig.root / "enrollment.json", rig.enrolled)
    yield rig
    rig.store.close()


def register(rig, **kw):
    return rig.service.register(
        recovery.Register(rig.obj, rig.obj.proof_set_sha256),
        uid=os.getuid(),
        pid=1,
        chunk_chars=3200,
        post=kw.get("post", rig.post),
    )


def deliver(rig):
    register(rig)
    for row in rig.store.pending():
        rig.store.mark_sent(row.seq, THREAD)


def test_real_registration_consent_permit_and_snapshot_certificate(primary_rig):
    rig = primary_rig
    assert register(rig).result == "registered"
    assert register(rig).result == "existing"
    assert len(rig.store.pending()) == 1
    deliver(rig)
    assert rig.message().message == "consented"
    first = rig.permit()
    assert first.result == "issued"
    first_bytes = (rig.root / "permits" / f"{rig.key}.json").read_bytes()
    assert rig.permit().result == "issued"
    assert (rig.root / "permits" / f"{rig.key}.json").read_bytes() == first_bytes
    rig.tick += 31_000_000_000
    assert rig.permit().result == "issued"
    assert rig.permit(rig.certificate()).result == "issued"


def test_register_crash_does_not_persist_consent_or_notice(primary_rig):
    rig = primary_rig

    def crash(key, text):
        rig.post(key, text)
        raise RuntimeError("fixture precommit death")

    with pytest.raises(RuntimeError, match="fixture precommit"):
        register(rig, post=crash)
    assert rig.store.pending() == []
    assert rig.store.db.execute("SELECT count(*) FROM maintenance_recovery_proposals").fetchone()[0] == 0


@pytest.mark.parametrize("primary", [True, False])
@pytest.mark.parametrize("suffix", ["issuing", "consumed", "receipt"])
def test_primary_renewal_never_ignores_any_consumption_program(primary_rig, primary, suffix):
    rig = primary_rig
    deliver(rig)
    rig.message()
    rig.permit()
    rig.tick += 40_000_000_000
    cert = rig.certificate()
    stem = rig.obj.proposal_id if primary else rig.key
    prefix = "primary-" if primary else ""
    write(rig.queue / "recovery-v1" / f"{stem}.{prefix}{suffix}.json", {})
    with pytest.raises(recovery.Invalid, match="consumption-unknown"):
        rig.permit(cert)
    assert rig.service.get(rig.key)["permit"]["generation"] == 1


def test_primary_certificate_requires_exact_typed_target_snapshot(primary_rig):
    rig = primary_rig
    deliver(rig)
    rig.message()
    rig.permit()
    rig.tick += 40_000_000_000
    rig.certificate()
    folder = rig.queue / "recovery-v1"
    path = folder / f"{rig.key}.nonconsumption-1.json"
    certificate = json.loads(path.read_bytes())
    certificate["target_snapshot_sha256"] = "a" * 64
    for suffix in ("nonconsumption", "superseded"):
        write(folder / f"{rig.key}.{suffix}-1.json", certificate)
    with pytest.raises(recovery.Invalid, match="certificate"):
        rig.permit(recovery.sha(certificate))


def test_primary_scope_cannot_replace_active_generic_consent(primary_rig):
    rig = primary_rig
    primary = rig.obj
    legacy = {
        k: v
        for k, v in rig.value.items()
        if k
        not in ("proposal_generation", "primary_child", "terminal_plan", "allowed_writes", "program_sha256")
    }
    legacy.update(schema="maintenance-recovery.v1", mode="settle", result="complete")
    rig.obj = recovery.descriptor(recovery.canonical(legacy))
    rig.key = recovery.sha(rig.obj)
    primary_key = recovery.sha(primary)
    rig.enrolled = msgspec.structs.replace(rig.enrolled, descriptor_links={rig.key: LINK, primary_key: LINK})
    write(rig.root / "enrollment.json", rig.enrolled)
    deliver(rig)
    rig.message()
    before = rig.service.get(rig.key)
    with pytest.raises(recovery.Invalid, match="recovery-scope-conflict"):
        rig.service.register(
            recovery.Register(primary, primary.proof_set_sha256),
            uid=os.getuid(),
            pid=1,
            chunk_chars=3200,
            post=rig.post,
        )
    assert rig.service.get(rig.key) == before
    rig.message("revoke", mid="77" * 32)
    rig.obj, rig.key = primary, primary_key
    assert register(rig).result == "registered"


def test_primary_scope_cannot_be_replaced_while_issued_permit_active(primary_rig):
    rig = primary_rig
    deliver(rig)
    rig.message()
    rig.permit()
    old_key = rig.key
    before = rig.service.get(old_key)
    v = value()
    v.update(
        enrollment_sha256=rig.obj.enrollment_sha256,
        source_closure=rig.obj.source_closure,
        original_record_sha256=rig.obj.original_record_sha256,
        expires_at=rig.obj.expires_at,
        proposal_generation=2,
    )
    rebind(v)
    rig.obj, rig.key = parse(v), recovery.sha(v)
    rig.enrolled = msgspec.structs.replace(rig.enrolled, descriptor_links={old_key: LINK, rig.key: LINK})
    write(rig.root / "enrollment.json", rig.enrolled)
    with pytest.raises(recovery.Invalid, match="permit-active"):
        register(rig)
    assert rig.service.get(old_key) == before
