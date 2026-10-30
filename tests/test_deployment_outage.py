"""Finite outage data and synthetic native evidence, never operational authority."""

import json

import msgspec
import pytest
from test_deployment_step1 import TARGET, URL, H, native_fixture, release

from heterodyne.actions import deployment_authority as authority
from heterodyne.actions import deployment_outage_authority as outage_authority
from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.deployment import digest
from heterodyne.actions.deployment_outage import (
    OUTAGE_STEPS,
    DeploymentPacket,
    IndependentRoute,
    OutageCapsule,
    RecoveryCommand,
    Transition,
    decode_packet,
    decode_recovery,
    packet_digest,
)


def packet():
    r = release()
    transitions = tuple(
        Transition(step, str(i + 1) * 64, str(i + 2) * 64)
        for i, step in enumerate(OUTAGE_STEPS)
    )
    return DeploymentPacket(
        "heterodyne-protected-deployment.v1", r,
        OutageCapsule(
            "heterodyne-outage-capsule.v1", digest(r), r.execution_id, r.nonce,
            2, 90, 200, transitions, IndependentRoute("terminal1", H, H, H),
        ), H, H, H,
    )


def command(p, **changes):
    return msgspec.structs.replace(RecoveryCommand(
        "heterodyne-independent-recovery.v1", packet_digest(p), p.release.execution_id,
        H, "cancel1", "cancel", H, 3, 150,
    ), **changes)


def test_exact_finite_outage_and_recovery():
    p = packet()
    assert decode_packet(encode_record(p), (TARGET,)) == p
    c = command(p)
    assert decode_recovery(encode_record(c), p, (TARGET,)) == c
    # A command is a finite value, with no effect method or target release path.
    assert not hasattr(c, "execute")
    assert not hasattr(c, "release_target")


@pytest.mark.parametrize("change", ["unknown", "extra", "order", "chain", "nonce", "route", "deadline"])
def test_outage_closed_and_bound(change):
    raw = json.loads(encode_record(packet()))
    if change == "unknown":
        raw["argv"] = ["arbitrary"]
    elif change == "extra":
        raw["outage"]["transitions"].append(raw["outage"]["transitions"][-1])
    elif change == "order":
        raw["outage"]["transitions"].reverse()
    elif change == "chain":
        raw["outage"]["transitions"][1]["before_sha256"] = H
    elif change == "nonce":
        raw["outage"]["nonce"] = "other"
    elif change == "route":
        raw["outage"]["route"]["route_id"] = "other"
    else:
        raw["outage"]["recovery_deadline_ns"] += 1
    with pytest.raises(InvalidAction):
        decode_packet(json.dumps(raw).encode(), (TARGET,))


@pytest.mark.parametrize("changes", [
    {"packet_sha256": "b" * 64}, {"route_epoch_sha256": "b" * 64},
    {"execution_id": "other"}, {"command_nonce": "bad\u2028nonce"},
    {"not_before_ns": 1}, {"deadline_ns": 201}, {"operation": "held-restore"},
])
def test_recovery_cannot_expand_approved_scope(changes):
    p = packet()
    with pytest.raises(InvalidAction):
        decode_recovery(encode_record(command(p, **changes)), p, (TARGET,))


def test_actual_admind_combined_card_budget_and_title_binding(tmp_path):
    # Each deviation satisfies its own bound, but the aggregate cannot be posted
    # untruncated by the actual admind question renderer.
    oversized = release(deviations=tuple(str(i) + "x" * 999 for i in range(4)))
    with pytest.raises(InvalidAction, match="release-card-budget"):
        authority.approval_body(oversized, URL, H)
    r, native, response = native_fixture(tmp_path)
    native["ask"]["title"] = "Different release"
    with pytest.raises(authority._core().AuthorityHold, match="exact release"):
        authority._core()._validate(r, native, response, {"bel": "d" * 64}, authority._bindings)


def test_core_tamper_reason_survives(monkeypatch):
    monkeypatch.setattr(authority, "_loaded_core", None)

    def refused():
        raise InvalidAction("authority-source-pin")

    monkeypatch.setattr(authority, "_load_core", refused)
    with pytest.raises(authority.AuthorityHold, match="authority-source-pin"):
        authority._core()


def test_whole_packet_synthetic_native_binding(tmp_path):
    p = packet()
    _, native, response = native_fixture(tmp_path)
    native["ask"]["body"] = outage_authority.approval_body(p, URL, H)
    core = authority._core()
    core._validate(p, native, response, {"bel": "d" * 64}, outage_authority._bindings)
    changed = msgspec.structs.replace(p, outage=msgspec.structs.replace(p.outage, deadline_ns=89))
    with pytest.raises(core.AuthorityHold, match="exact outage"):
        core._validate(changed, native, response, {"bel": "d" * 64}, outage_authority._bindings)
    # An old inner Release approval cannot approve the added outage wrapper.
    native["ask"]["body"] = authority.approval_body(p.release, URL, H)
    with pytest.raises(core.AuthorityHold, match="exact outage"):
        core._validate(p, native, response, {"bel": "d" * 64}, outage_authority._bindings)


@pytest.mark.parametrize("change", ["sender", "owner", "cli", "membership", "newer-no"])
def test_outage_keeps_actual_native_authentication(tmp_path, change):
    p = packet()
    _, native, response = native_fixture(tmp_path)
    native["ask"]["body"] = outage_authority.approval_body(p, URL, H)
    if change == "sender":
        native["inbound"][0]["sender_key"] = "e" * 64
    elif change == "owner":
        native["cards"][0]["ownership_kind"] = "session"
    elif change == "cli":
        response["ask"]["answers"] = []
    elif change == "membership":
        next(row for row in native["kv"] if row["key"] == "group_operators")["value"] = json.dumps(["e" * 64])
    else:
        native["answers"][0]["choice"] = "no"
    with pytest.raises(authority._core().AuthorityHold):
        authority._core()._validate(p, native, response, {"bel": "d" * 64}, outage_authority._bindings)
