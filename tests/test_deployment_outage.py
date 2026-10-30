"""Finite outage data and synthetic native evidence, never operational authority."""

import json

import msgspec
import pytest
from test_deployment_step1 import (  # noqa: F401
    TARGET,
    URL,
    H,
    native_fixture,
    release,
    synthetic_rendering_profile,
)

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

OUTER_URL = URL.replace("a" * 40, "b" * 40)


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
        ), H, H, H, H, H, H,
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
    native["ask"]["title"] = outage_authority.APPROVAL_TITLE
    response["ask"]["summary"]["title"] = outage_authority.APPROVAL_TITLE
    native["ask"]["body"] = outage_authority.approval_body(p, OUTER_URL, H)
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
    native["ask"]["title"] = outage_authority.APPROVAL_TITLE
    response["ask"]["summary"]["title"] = outage_authority.APPROVAL_TITLE
    native["ask"]["body"] = outage_authority.approval_body(p, OUTER_URL, H)
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


def rendered_outage(p):
    from heterodyne.admind import asks
    from heterodyne.admind.redact import redact
    from heterodyne.admind.store import AskRow

    body = outage_authority.approval_body(p, OUTER_URL, H)
    request = asks.AskPost(
        "question", title=outage_authority.APPROVAL_TITLE, body=body,
        poster="protected-deploy", form="yes-no", recommended="yes",
    )
    assert asks.check(request) is None
    form, choices = asks.stored_form(request)
    at = asks.stamp(asks.now())
    row = AskRow(
        p.release.approval_id, "question", "protected-deploy", request.title, body,
        None, None, None, None, False, 1, "open", None, None, at, at,
        question_form=form, choices=choices,
    )
    assert redact(asks.context(row)) == asks.context(row)
    parts, truncated = asks.checked_card_chunks(row, 8000)
    assert not truncated and len(parts) == 1
    assert asks.card_chunks(row, 8000) == (parts, False)
    return parts[0]


def test_actual_rendered_outage_card_retains_exact_outer_digest_and_gap():
    from heterodyne.harness.digests import parse_display

    p = packet()
    shown = rendered_outage(p)
    line = next(line for line in shown.splitlines() if line.startswith("Protected deployment SHA256: "))
    assert parse_display(line.removeprefix("Protected deployment SHA256: ")).hex == packet_digest(p)
    assert outage_authority.APPROVAL_TITLE in shown
    assert "queued but undelivered Marmot reply is not a received revocation" in shown
    assert "A yes explicitly accepts that gap" in shown
    assert "<redacted" not in shown
    changed = msgspec.structs.replace(p, outage=msgspec.structs.replace(
        p.outage, route=msgspec.structs.replace(p.outage.route, credential_sha256="b" * 64),
    ))
    assert rendered_outage(changed) != shown


def test_actual_login_redaction_cannot_expand_or_change_approval_context(monkeypatch):
    monkeypatch.setattr(authority, "_rendering", lambda: (("/tiny",), 2000))
    r = release(deviations=("Read /tiny before deployment",))
    with pytest.raises(InvalidAction, match="card-redaction"):
        authority.approval_body(r, URL, H)


def test_real_endpoint_chunk_bound_and_protected_target_mapping(monkeypatch):
    from heterodyne.actions.deployment_host_store import _installer_argv

    p = packet()
    monkeypatch.setattr(authority, "_rendering", lambda: ((), 1000))
    with pytest.raises(InvalidAction, match="single-part|transport-bound"):
        outage_authority.approval_body(p, OUTER_URL, H)
    with pytest.raises(InvalidAction):
        _installer_argv(p, ())


def test_outer_url_is_independent_and_retained_in_exact_native_body(tmp_path):
    p = packet()
    outer = URL.replace("a" * 40, "b" * 40)
    body = outage_authority.approval_body(p, outer, H)
    assert "Outer Packet: " + outer in body
    assert "Inner Release: " + URL in body
    _, native, response = native_fixture(tmp_path)
    native["ask"].update(title=outage_authority.APPROVAL_TITLE, body=body)
    response["ask"]["summary"]["title"] = outage_authority.APPROVAL_TITLE
    authority._core()._validate(p, native, response, {"bel": "d" * 64}, outage_authority._bindings)
    with pytest.raises(InvalidAction, match="publication-url"):
        outage_authority.approval_body(p, outer.replace("docs/release.json", "docs/other.json"), H)


def test_rendering_profile_resolved_without_global_login_state(monkeypatch, request):
    from types import SimpleNamespace

    from heterodyne.admind import redact as redactor

    monkeypatch.setattr(authority, "_core", lambda: SimpleNamespace(_read=lambda path: b"pinned config"))
    seen = []

    def config(ws, env):
        seen.append((ws, env))
        return object()

    monkeypatch.setattr(authority, "load_config", config)
    monkeypatch.setattr(authority.admind_settings, "resolve", lambda cfg, env: SimpleNamespace(
        login_dirs=("~/.claude-b",), chunk_chars=2000,
    ))
    monkeypatch.setattr(redactor, "_login_dirs", None)
    monkeypatch.setattr(authority, "_rendering", request.getfixturevalue("synthetic_rendering_profile"))
    with pytest.raises(InvalidAction, match="card-redaction"):
        authority.approval_body(release(deviations=("Read ~/.claude-b",)), URL, H)
    assert seen == [(None, {
        "HOME": str(authority.HOST_POLICY.parent.parent.parent),
        "HETERODYNE_CONFIG_DIR": str(authority.HOST_POLICY.parent),
    })]


@pytest.mark.parametrize("field", ["root_helper_sha256", "root_profile_sha256", "sudo_sha256"])
def test_root_helper_contract_pins_are_required_and_closed(field):
    p = packet()
    with pytest.raises(InvalidAction, match="outage-digest"):
        decode_packet(encode_record(msgspec.structs.replace(p, **{field: "bad"})), (TARGET,))
    raw = json.loads(encode_record(p))
    del raw[field]
    with pytest.raises(InvalidAction):
        decode_packet(json.dumps(raw).encode(), (TARGET,))


def test_outer_publication_commit_must_differ_from_inner_release():
    with pytest.raises(InvalidAction, match="outer-packet-publication-url"):
        outage_authority.approval_body(packet(), URL, H)


def test_whole_outage_preparation_and_binding_never_require_unposted_inner_card(tmp_path, monkeypatch):
    _, native, response = native_fixture(tmp_path)
    original = authority._check_card
    checked = []
    def outer_only(title, body, ask_id):
        checked.append(title)
        if title == authority.APPROVAL_TITLE:
            raise InvalidAction("unposted-inner-renderer-refusal")
        return original(title, body, ask_id)
    monkeypatch.setattr(authority, "_check_card", outer_only)
    p = packet()
    native["ask"]["title"] = outage_authority.APPROVAL_TITLE
    response["ask"]["summary"]["title"] = outage_authority.APPROVAL_TITLE
    native["ask"]["body"] = outage_authority.approval_body(p, OUTER_URL, H)
    authority._core()._validate(p, native, response, {"bel": "d" * 64}, outage_authority._bindings)
    assert checked == [outage_authority.APPROVAL_TITLE] * 2
    native["ask"]["card_parts"] = 2
    with pytest.raises(authority._core().AuthorityHold, match="exact release"):
        authority._core()._validate(p, native, response, {"bel": "d" * 64}, outage_authority._bindings)
