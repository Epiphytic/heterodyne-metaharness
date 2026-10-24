"""Native-observation consent uses disposable store/evidence and existing permits."""

import copy
import json
import os
from datetime import timedelta
from typing import Any, cast

import msgspec
import pytest
from test_admind_recovery import LINK, OPERATOR, Rig, descriptor, write

from heterodyne.admind import recovery

FAMILIES = (
    "create-approval",
    "create-member",
    "publish",
    "pin-claim",
    "decision-metadata",
    "close-approval",
    "pin-metadata",
    "audit-comment",
    "pin-release",
)


def native_value() -> dict[str, Any]:
    value: dict[str, Any] = descriptor()
    value.update(
        schema="settle-approval-native.v1",
        mode="settle-approval-native",
        native_effect=dict(
            ordinal=1,
            task_id="btq-abc12",
            approval_id="btq-def34",
            family="create-member",
            intent_envelope_sha256="1" * 64,
            plan_sha256="2" * 64,
            observation_sha256="3" * 64,
            verdict="observed-applied",
        ),
    )
    return value


@pytest.fixture
def native_rig(tmp_path):
    rig = Rig(tmp_path)
    native = native_value()
    rig.value.update(schema=native["schema"], mode=native["mode"], native_effect=native["native_effect"])
    rig.obj = recovery.descriptor(recovery.canonical(rig.value))
    rig.key = recovery.sha(rig.obj)
    rig.enrolled = msgspec.structs.replace(rig.enrolled, descriptor_links={rig.key: LINK})
    write(rig.root / "enrollment.json", rig.enrolled)
    yield rig
    rig.store.close()


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("verdict", ["observed-applied", "observed-unapplied"])
def test_closed_native_shape_and_wire_dispatch(family, verdict):
    value = native_value()
    value["native_effect"].update(family=family, verdict=verdict)
    obj = recovery.descriptor(recovery.canonical(value))
    assert isinstance(obj, recovery.NativeDescriptor)
    request = recovery.Register(obj, obj.proof_set_sha256)
    wire = recovery.decode(msgspec.json.encode(request), cast(type[recovery.Request], recovery.Request))
    assert isinstance(wire, recovery.Register)
    assert recovery.check(wire) is None
    assert recovery.descriptor(recovery.canonical(wire.descriptor)) == obj


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "maintenance-recovery.v1"},
        {"schema": "unknown.v1"},
        {"mode": "settle"},
        {"result": "abandoned-diagnostic"},
        {"extra": "unapproved"},
        {"effect_count": True},
        {"effect_count": 0},
        {"effect_count": 2},
        {"effects_root": None},
        {"backup_before_sha256": "a" * 64},
        {"failure_before_sha256": "a" * 64},
        {
            "local_records": [
                {
                    "adapter_sha256": "a" * 64,
                    "before_sha256": "b" * 64,
                    "selector": {"type": "task", "id": "btq-abc12"},
                    "action": "clear-proven-notice",
                }
            ]
        },
    ],
)
def test_native_schema_cannot_weaken_generic_or_add_scope(change):
    value = native_value()
    value.update(change)
    with pytest.raises(recovery.Invalid):
        recovery.descriptor(recovery.canonical(value))
    assert recovery.check(recovery.Register(value, value["proof_set_sha256"])) == "invalid-request"


@pytest.mark.parametrize(
    "change",
    [
        {"ordinal": True},
        {"ordinal": 0},
        {"family": "generic-status"},
        {"verdict": "success"},
        {"task_id": "btq/abc12"},
        {"approval_id": "BTQ-def34"},
        {"task_id": "a" * 200},
        {"task_id": "btq_abc12"},
        {"task_id": "a"},
        {"observation_sha256": "bad"},
        {"plan_sha256": "A" * 64},
        {"intent_envelope_sha256": None},
        {"returncode": 0},
    ],
)
def test_native_effect_fields_are_exact_and_bounded(change):
    value = native_value()
    value["native_effect"].update(change)
    with pytest.raises(recovery.Invalid):
        recovery.descriptor(recovery.canonical(value))


def test_legacy_descriptor_and_target_snapshot_remain_exact():
    value = descriptor()
    obj = recovery.descriptor(recovery.canonical(value))
    assert type(obj) is recovery.Descriptor
    expected = {
        name: value[name]
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
    }
    assert recovery.target_snapshot(obj) == recovery.sha(
        dict(schema="maintenance-recovery-target-snapshot.v1", **expected)
    )
    value["native_effect"] = native_value()["native_effect"]
    with pytest.raises(recovery.Invalid):
        recovery.descriptor(recovery.canonical(value))


def test_card_shows_exact_observation_scope_without_execution_claim(native_rig):
    assert native_rig.register().result == "registered"
    text = native_rig.store.pending()[0].text
    for value in ("btq-abc12", "btq-def34", "create-member", "observed-applied"):
        assert value in text
    for field, expected in [("Intent envelope", "1" * 64), ("Plan", "2" * 64), ("Observation", "3" * 64)]:
        line = next(row for row in text.splitlines() if row.startswith(field + " SHA256: "))
        assert line.split(": ", 1)[1].replace(" ", "") == expected
    assert recovery.redact(text) == text
    assert "unchanged precondition after cessation; absence alone is insufficient" in text
    assert "No native replay; no command return code or causation is asserted." in text
    assert "Remaining native unknowns: never cleared by this consent." not in text
    assert len(text) < 2000


def test_native_scope_binds_target_snapshot_hash():
    value = native_value()
    first = recovery.target_snapshot(recovery.descriptor(recovery.canonical(value)))
    for field, replacement in [
        ("verdict", "observed-unapplied"),
        ("observation_sha256", "4" * 64),
        ("task_id", "btq-xxx12"),
        ("family", "pin-claim"),
    ]:
        changed = copy.deepcopy(value)
        changed["native_effect"][field] = replacement
        assert recovery.target_snapshot(recovery.descriptor(recovery.canonical(changed))) != first


def test_existing_authenticated_thread_and_30_second_permit_apply(native_rig):
    native_rig.deliver()
    with pytest.raises(recovery.Invalid):
        native_rig.message(target="99" * 32)
    with pytest.raises(recovery.Invalid):
        native_rig.permit()
    assert native_rig.message().result == "recorded"
    assert native_rig.permit().result == "issued"
    permit = native_rig.service.get(native_rig.key)["permit"]
    assert permit["initiate_before_boottime_ns"] - permit["issued_boottime_ns"] == 30_000_000_000
    assert permit["operator"] == OPERATOR
    assert permit["generation"] == 1
    assert native_rig.permit().result == "issued"
    assert native_rig.service.get(native_rig.key)["permit"] == permit


def test_revocation_and_expiry_do_not_gain_native_authority(native_rig):
    native_rig.deliver()
    native_rig.message()
    native_rig.message(action="revoke", mid="88" * 32)
    with pytest.raises(recovery.Invalid):
        native_rig.permit()
    native_rig.at += timedelta(days=2)
    with pytest.raises(recovery.Invalid):
        native_rig.register()


def test_bad_native_transport_rejected_before_store_mutation(native_rig):
    value = json.loads(recovery.canonical(native_rig.obj))
    value["native_effect"]["family"] = "arbitrary-native"
    req = recovery.decode(
        msgspec.json.encode(recovery.Register(value, value["proof_set_sha256"])),
        cast(type[recovery.Request], recovery.Request),
    )
    assert isinstance(req, recovery.Register)
    assert recovery.check(req) == "invalid-request"
    with pytest.raises(recovery.Invalid):
        native_rig.service.register(req, uid=os.getuid(), pid=1, chunk_chars=2000, post=native_rig.post)
    assert native_rig.store.pending() == []


def test_native_card_exact_byte_budget_preserves_every_material_field(native_rig):
    text = recovery.render(native_rig.obj, "local-controller", LINK, 2000)
    size = len(text.encode("utf-8"))
    assert text.isascii()
    assert recovery.render(native_rig.obj, "local-controller", LINK, size) == text
    with pytest.raises(recovery.Invalid, match="notice-material-unrepresentable"):
        recovery.render(native_rig.obj, "local-controller", LINK, size - 1)
    lines = text.splitlines()
    assert lines[1].startswith("Approve: settle the latest unknown native effect")
    assert "btq-abc12" in lines[2] and "btq-def34" in lines[2]
    assert "observed-applied" in lines[3]
    assert all(text.count(recovery.display_hash(digit * 64)) == 1 for digit in "123")
    assert "??" not in text and "(sanitized)" not in text


def test_native_card_budget_counts_utf8_bytes(native_rig):
    text = recovery.render(native_rig.obj, "contrôleur", LINK, 2000)
    size = len(text.encode("utf-8"))
    assert size > len(text)
    assert recovery.render(native_rig.obj, "contrôleur", LINK, size) == text
    with pytest.raises(recovery.Invalid, match="notice-material-unrepresentable"):
        recovery.render(native_rig.obj, "contrôleur", LINK, len(text))
