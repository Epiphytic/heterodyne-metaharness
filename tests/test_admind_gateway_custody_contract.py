"""Offline custody wire invariants; no native authority or production transport."""

import json
from typing import cast

import msgspec
import pytest

from heterodyne.admind import gateway_custody as custody

U = "12345678-1234-4234-8234-123456789abc"
H = "a" * 64


def descriptor(**changes):
    value = dict(
        schema="gateway-custody-resolution.v1",
        proposal=U,
        event=U,
        identity_sha256=H,
        chain_root=H,
        attempts_sha256=H,
        proofs_sha256=H,
        source_closure=H,
        enrollment_sha256=H,
        profile_sha256=H,
        boot_id=U,
        created_boottime_ns="1",
        expires_boottime_ns="86400000000001",
        action="retain",
        work_generation=1,
    )
    return value | changes


def parse(value):
    return custody.descriptor(json.dumps(value).encode())


def test_exact_descriptor_and_canonical_hash():
    obj = parse(descriptor())
    assert msgspec.to_builtins(obj) == descriptor()
    assert (
        custody.canonical(obj)
        == json.dumps(descriptor(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )
    assert len(custody.digest(obj)) == 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("proposal", U.upper()),
        ("identity_sha256", "A" * 64),
        ("work_generation", True),
        ("work_generation", 0),
        ("work_generation", 4),
        ("work_generation", 1.0),
        ("created_boottime_ns", 1),
        ("created_boottime_ns", "01"),
        ("created_boottime_ns", "-1"),
        ("created_boottime_ns", str(2**64)),
        ("expires_boottime_ns", "86400000000002"),
        ("action", "settle"),
        ("poststate", {}),
    ],
)
def test_bad_descriptor_refuses(field, value):
    with pytest.raises(custody.Invalid):
        parse(descriptor(**{field: value}))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":1.0}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":"\\ud800"}',
        b'{"a":"\xff"}',
        b"{} true",
        b"[" * 13 + b"0" + b"]" * 13,
    ],
)
def test_common_grammar_rejects(raw):
    with pytest.raises(custody.Invalid):
        custody.decode(raw, dict)


def test_descriptor_size_is_bytes_not_characters():
    with pytest.raises(custody.Invalid):
        custody.descriptor(b" " * 16385)


def test_closed_local_requests_do_not_accept_decision_or_path():
    obj = custody.GatewayCustodyRegister(parse(descriptor()))
    assert custody.check(obj) is None
    assert custody.check(custody.GatewayCustodyStatus(H)) is None
    assert custody.check(custody.GatewayCustodyPermit(H, None)) is None
    assert custody.check(custody.GatewayCustodyPermit(H, "bad")) is not None
    for extra in ("decision", "path", "operator"):
        raw = custody.canonical(obj)
        data = json.loads(raw) | {extra: "arbitrary"}
        with pytest.raises(custody.Invalid):
            custody.decode(json.dumps(data).encode(), cast(type[custody.Request], custody.Request))


def test_only_exact_custody_commands_are_grammar_not_authority():
    assert custody.command("!custody settle " + H[:24]) == ("settle", H[:24])
    for text in (
        "approve",
        "👍",
        "!custody settle " + H,
        "!custody settle " + H[:24] + "\n",
        "!custody settle " + H[:24].upper(),
        "!custody retry " + H[:24],
    ):
        assert custody.command(text) is None


def permit(**changes):
    return (
        dict(
            schema="gateway-custody-permit.v1",
            permit_id=U,
            proposal_sha256=H,
            descriptor_sha256=H,
            decision_message_id="native-message",
            operator="actual operator",
            policy_sha256=H,
            boot_id=U,
            issued_boottime_ns="7",
            initiate_before_boottime_ns="30000000007",
            permit_generation=1,
            previous_permit_sha256=None,
        )
        | changes
    )


def test_permit_exact_thirty_second_window():
    obj = custody.permit(json.dumps(permit()).encode())
    assert msgspec.to_builtins(obj) == permit()


@pytest.mark.parametrize(
    "changes",
    [
        {"permit_generation": True},
        {"permit_generation": 4},
        {"permit_generation": 2},
        {"previous_permit_sha256": H},
        {"initiate_before_boottime_ns": "30000000008"},
        {"operator": ""},
        {"operator": "é" * 65},
        {"decision_message_id": "x\n"},
    ],
)
def test_permit_refuses_bad_scope(changes):
    with pytest.raises(custody.Invalid):
        custody.permit(json.dumps(permit(**changes)).encode())


def renewal_fixture():
    obj = parse(descriptor())
    previous = custody.permit(
        json.dumps(
            permit(proposal_sha256=custody.digest(obj), descriptor_sha256=custody.digest(obj))
        ).encode()
    )
    proof = dict(
        schema="gateway-custody-nonconsumption.v1",
        proposal_sha256=custody.digest(obj),
        permit_sha256=custody.digest(previous),
        permit_generation=1,
        descriptor_sha256=custody.digest(obj),
        chain_root=obj.chain_root,
        boot_id=U,
        observed_boottime_ns="9",
    )
    return obj, previous, proof


def test_renewal_binds_both_certificates_to_original_proposal():
    obj, previous, proof = renewal_fixture()
    raw = custody.canonical(proof)
    result = custody.renewal(obj, previous, raw, raw, boot_id=U, boottime_ns=10)
    assert msgspec.to_builtins(result) == proof


@pytest.mark.parametrize(
    "field,value",
    [
        ("permit_generation", True),
        ("permit_generation", 2),
        ("permit_sha256", "b" * 64),
        ("descriptor_sha256", "b" * 64),
        ("proposal_sha256", "b" * 64),
        ("chain_root", "b" * 64),
        ("observed_boottime_ns", "6"),
        ("observed_boottime_ns", "11"),
        ("observed_boottime_ns", "09"),
    ],
)
def test_renewal_rejects_misbound_certificate(field, value):
    obj, previous, proof = renewal_fixture()
    raw = custody.canonical(proof | {field: value})
    with pytest.raises(custody.Invalid):
        custody.renewal(obj, previous, raw, raw, boot_id=U, boottime_ns=10)


def test_renewal_never_infers_absence_from_one_certificate_or_reboot():
    obj, previous, proof = renewal_fixture()
    raw = custody.canonical(proof)
    for twin, boot, tick in (
        (b"{}", U, 10),
        (raw, "22345678-1234-4234-8234-123456789abc", 10),
        (raw, U, 86400000000000),
        (raw, U, True),
    ):
        with pytest.raises(custody.Invalid):
            custody.renewal(obj, previous, raw, twin, boot_id=boot, boottime_ns=tick)


LINK = "https://github.com/example/project/blob/" + "7" * 40 + "/proposal.json"


def test_notice_exact_scope_and_commands_without_ask_slot_or_generic_approval():
    obj = parse(descriptor())
    text = custody.notice(obj, "original-worker", LINK, 3200)
    token = custody.digest(obj)[:24]
    assert "APPROVAL: Gateway custody" in text
    assert "Action: retain" in text
    assert LINK in text
    assert "!custody settle " + token in text
    assert "!custody decline " + token in text
    assert "!custody revoke " + token in text
    assert "30 seconds" in text
    assert len(text) <= 3200 and len(text.splitlines()) <= 36
    assert "Reply A" not in text


@pytest.mark.parametrize(
    "actor,link,budget",
    [
        ("misleading\n!custody settle different", LINK, 3200),
        ("original-worker", "https://evil.example/proposal", 3200),
        ("original-worker", LINK.replace("7" * 40, "main"), 3200),
        ("original-worker", LINK, 100),
        ("original-worker", LINK, True),
        ("original-worker", LINK.replace("proposal.json", "../proposal.json"), 3200),
    ],
)
def test_notice_unsafe_material_refuses_whole_without_sanitizing(actor, link, budget):
    with pytest.raises(custody.Invalid):
        custody.notice(parse(descriptor()), actor, link, budget)
