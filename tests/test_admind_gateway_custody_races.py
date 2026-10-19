"""Deterministic failure and concurrent-CAS tests on disposable SQLite only."""

import dataclasses
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_admind_gateway_custody_decisions import POLICY, answer, ready
from test_admind_gateway_custody_permits import issue

from heterodyne.admind import gateway_custody as custody

pytest_plugins = ("test_admind_gateway_custody_registration", "test_admind_gateway_custody_permits")


def test_deny_first_is_irreversible_and_repeat_registration_keeps_history(rig):
    store, key, service, _ = ready(rig)
    assert answer(service, key, action="decline") == "revoked"
    assert answer(service, key, mid="1" * 64) == "held"
    before = service.get(key, uid=os.getuid())
    assert service.register(rig[1], uid=os.getuid(), max_chars=3200) == key
    assert service.get(key, uid=os.getuid()) == before
    assert "decision" not in before["bundle"]
    assert len(before["bundle"]["commands"]) == 2


def test_source_drift_cannot_create_decision(rig):
    store, key, service, _ = ready(rig)
    rig[3].return_value = dataclasses.replace(rig[2], descriptor=b"{}")
    with pytest.raises(custody.Invalid, match="source-mismatch"):
        answer(service, key)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"


def test_decision_cas_detects_concurrent_contrary_command(rig):
    store, key, service, authority = ready(rig)
    barrier = threading.Barrier(2)

    def policy(account, group, sender):
        barrier.wait(timeout=5)
        return POLICY

    authority.side_effect = policy

    def decide(mid):
        try:
            return answer(service, key, mid=mid)
        except custody.Invalid as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, ["1" * 64, "2" * 64]))
    assert sorted(outcomes) == ["consented", "proposal-changed"]
    bundle = service.get(key, uid=os.getuid())["bundle"]
    assert len(bundle["commands"]) == 1
    assert bundle["decision"]["message_id"] in ["1" * 64, "2" * 64]


def test_store_update_error_rolls_back_decision_receipt_and_state(rig):
    store, key, service, _ = ready(rig)
    before = service.get(key, uid=os.getuid())
    store.db.execute(
        "CREATE TRIGGER fixture_abort BEFORE UPDATE ON gateway_custody_proposals "
        "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        answer(service, key)
    assert service.get(key, uid=os.getuid()) == before


@pytest.mark.parametrize("policy", ["bad", 7, True, []])
def test_malformed_policy_holds_with_closed_error(rig, policy):
    store, key, service, authority = ready(rig)
    authority.return_value = policy
    with pytest.raises(custody.Invalid):
        answer(service, key)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"


def test_bundle_cap_revokes_without_creating_fake_command_receipt(rig):
    store, key, service, _ = ready(rig)
    answer(service, key)
    bundle = service.get(key, uid=os.getuid())["bundle"]
    bundle["commands"]["fixture-reserved-bytes"] = "x" * 32000
    raw = custody.canonical(bundle)
    store.db.execute("UPDATE gateway_custody_proposals SET bundle=?", (raw,))
    with pytest.raises(custody.Invalid, match="bundle-capacity"):
        answer(service, key, action="decline", mid="2" * 64)
    retained = service.get(key, uid=os.getuid())
    assert retained["state"] == "revoked"
    assert custody.canonical(retained["bundle"]) == raw
    assert retained["bundle"]["decision"]["message_id"] != "2" * 64


def test_issuance_concurrent_revoke_never_publishes(issued_rig):
    store, key, service, authority, enrollment, _ = issued_rig
    calls = 0

    def policy(account, group, sender):
        nonlocal calls
        calls += 1
        if calls == 2:
            store.db.execute("UPDATE gateway_custody_proposals SET state='revoked'")
        return POLICY

    authority.side_effect = policy
    with pytest.raises(custody.Invalid, match="proposal-changed"):
        issue(service, key)
    assert not (enrollment.root / "permits").exists()
    assert "permits" not in service.get(key, uid=os.getuid())["bundle"]


def test_second_source_observation_drift_prevents_issue(issued_rig):
    _, key, service, _, enrollment, rig = issued_rig
    service.source.side_effect = [rig[2], dataclasses.replace(rig[2], descriptor=b"{}")]
    with pytest.raises(custody.Invalid, match="source-mismatch"):
        issue(service, key)
    assert not (enrollment.root / "permits").exists()


def test_changed_publication_enrollment_prevents_issue(issued_rig):
    _, key, service, _, enrollment, _ = issued_rig
    service.publication.side_effect = [enrollment, dataclasses.replace(enrollment, source_closure="1" * 64)]
    with pytest.raises(custody.Invalid, match="publication-drift"):
        issue(service, key)
    assert not (enrollment.root / "permits").exists()


def test_store_failure_prevents_any_permit_publication(issued_rig):
    store, key, service, _, enrollment, _ = issued_rig
    store.db.execute(
        "CREATE TRIGGER fixture_abort BEFORE UPDATE ON gateway_custody_proposals "
        "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        issue(service, key)
    assert not (enrollment.root / "permits").exists()
    assert "permits" not in service.get(key, uid=os.getuid())["bundle"]


def test_permit_bundle_capacity_cannot_use_control_reserve(issued_rig):
    store, key, service, _, enrollment, _ = issued_rig
    bundle = service.get(key, uid=os.getuid())["bundle"]
    bundle["commands"]["fixture-padding"] = "x" * 32000
    store.db.execute("UPDATE gateway_custody_proposals SET bundle=?", (custody.canonical(bundle),))
    with pytest.raises(custody.Invalid, match="bundle-capacity"):
        issue(service, key)
    assert not (enrollment.root / "permits").exists()
    assert "permits" not in service.get(key, uid=os.getuid())["bundle"]
