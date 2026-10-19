"""Synthetic independent native event/policy ports; never live command evidence."""

import os
from unittest.mock import Mock

import pytest

from heterodyne.admind import gateway_custody as custody

pytest_plugins = ("test_admind_gateway_custody_registration",)

ACCOUNT, GROUP, SENDER, MID, TARGET, POLICY = (letter * 64 for letter in "abcdef")


def ready(rig):
    store, obj, observation, source, _ = rig
    authority = Mock(return_value=POLICY)
    service = custody.Service(store, source=source, authority=authority)
    key = service.register(obj, uid=os.getuid(), max_chars=3200)
    row = store.db.execute("SELECT seq FROM outbox").fetchone()
    store.mark_sent(row[0], TARGET)
    return store, key, service, authority


def answer(service, key, *, action="settle", mid=MID, **changes):
    event = dict(
        account=ACCOUNT,
        group=GROUP,
        mid=mid,
        sender=SENDER,
        target=TARGET,
        text=f"!custody {action} {key[:24]}",
    )
    return service.message(**(event | changes))


def test_first_native_command_retains_decision_and_idempotent_receipt(rig):
    store, key, service, authority = ready(rig)
    assert answer(service, key) == "consented"
    authority.return_value = None
    assert answer(service, key) == "consented"
    row = service.get(key, uid=os.getuid())
    assert row["state"] == "consented"
    assert row["bundle"]["decision"]["message_id"] == MID
    assert row["bundle"]["decision"]["sender"] == SENDER
    assert row["bundle"]["original_actor"] == "original operator"
    assert row["bundle"]["decision"]["policy_sha256"] == POLICY
    assert len(row["bundle"]["commands"]) == 1


@pytest.mark.parametrize("action", ["decline", "revoke"])
def test_contrary_command_holds_unissued_authority_without_replacing_decision(rig, action):
    store, key, service, _ = ready(rig)
    answer(service, key)
    assert answer(service, key, action=action, mid="1" * 64) == "revoked"
    row = service.get(key, uid=os.getuid())
    assert row["state"] == "revoked"
    assert row["bundle"]["decision"]["message_id"] == MID
    assert len(row["bundle"]["commands"]) == 2
    assert answer(service, key, mid="2" * 64) == "held"
    assert service.get(key, uid=os.getuid())["state"] == "revoked"


@pytest.mark.parametrize(
    "changes",
    [
        {"target": None},
        {"target": "0" * 64},
        {"text": "approve"},
        {"text": "!custody settle " + "0" * 24},
        {"mid": "bad"},
        {"sender": "bad"},
        {"account": "bad"},
        {"group": "bad"},
    ],
)
def test_wrong_native_context_or_command_never_decides(rig, changes):
    store, key, service, _ = ready(rig)
    with pytest.raises(custody.Invalid):
        answer(service, key, **changes)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"


def test_current_policy_is_required_and_rechecked_outside_store_transaction(rig):
    store, key, service, authority = ready(rig)
    authority.return_value = None
    with pytest.raises(custody.Invalid, match="authority"):
        answer(service, key)
    authority.side_effect = [POLICY, "1" * 64]
    with pytest.raises(custody.Invalid, match="authority-drift"):
        answer(service, key)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"


def test_policy_callback_runs_without_store_writer_transaction(rig):
    store, key, service, authority = ready(rig)

    def observe(account, group, sender):
        assert not store.db.in_transaction
        assert (account, group, sender) == (ACCOUNT, GROUP, SENDER)
        return POLICY

    authority.side_effect = observe
    assert answer(service, key) == "consented"


def test_undelivered_notice_cannot_authorize_command(rig):
    store, key, service, _ = ready(rig)
    store.db.execute("UPDATE outbox SET status='pending'")
    with pytest.raises(custody.Invalid, match="thread-unmapped"):
        answer(service, key)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"


def test_same_native_id_with_conflicting_payload_revokes_without_overwriting(rig):
    store, key, service, _ = ready(rig)
    answer(service, key)
    with pytest.raises(custody.Invalid, match="replay-mismatch"):
        answer(service, key, action="decline")
    row = service.get(key, uid=os.getuid())
    assert row["state"] == "revoked"
    assert row["bundle"]["decision"]["message_id"] == MID


def test_missing_native_authority_port_cannot_decide(rig):
    store, key, service, _ = ready(rig)
    service.authority = None
    with pytest.raises(custody.Invalid, match="authority-unavailable"):
        answer(service, key)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"
