"""Disposable source/transport fixtures; registration is never native consent."""

import dataclasses
import os
from unittest.mock import Mock

import pytest
from test_admind_gateway_custody_contract import U, descriptor, parse

from heterodyne.admind import gateway_custody as custody
from heterodyne.admind.store import Store

LINK = "https://github.com/example/project/blob/" + "b" * 40 + "/descriptor.json"


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path / "admind.db")
    obj = parse(descriptor())
    observation = custody.Observation(custody.canonical(obj), "original operator", LINK, U, 2)
    source = Mock(return_value=observation)
    service = custody.Service(store, source=source)
    yield store, obj, observation, source, service
    store.close()


def test_missing_source_holds_before_custody_storage(tmp_path):
    store = Store(tmp_path / "admind.db")
    service = custody.Service(store)
    with pytest.raises(custody.Invalid, match="source-unavailable"):
        service.register(parse(descriptor()), uid=os.getuid(), max_chars=3200)
    assert not store.db.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'gateway_custody%'"
    ).fetchall()
    store.close()


def test_registration_is_atomic_independent_notice_and_idempotent(rig):
    store, obj, observed, source, service = rig
    key = service.register(obj, uid=os.getuid(), max_chars=3200)
    assert key == custody.digest(obj)
    assert service.register(obj, uid=os.getuid(), max_chars=3200) == key
    assert store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    rows = store.db.execute("SELECT key,text,ownership_kind FROM outbox").fetchall()
    assert len(rows) == 1 and rows[0][2] == "system"
    assert rows[0][1].startswith("APPROVAL: Gateway custody")
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"


@pytest.mark.parametrize(
    "field,value",
    [
        ("boot_id", "22345678-1234-4234-8234-123456789abc"),
        ("boottime_ns", 0),
        ("boottime_ns", 86400000000001),
        ("descriptor", b"{}"),
        ("evidence_url", "https://example.com/descriptor"),
    ],
)
def test_source_mismatch_or_expiry_holds_without_registration(rig, field, value):
    store, obj, observed, source, service = rig
    source.return_value = dataclasses.replace(observed, **{field: value})
    with pytest.raises(custody.Invalid):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    assert not store.db.execute(
        "SELECT name FROM sqlite_master WHERE name='gateway_custody_proposals'"
    ).fetchall()
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_wrong_uid_cannot_read_or_register(rig):
    store, obj, observed, source, service = rig
    with pytest.raises(custody.Invalid, match="peer"):
        service.register(obj, uid=os.getuid() + 1, max_chars=3200)
    source.assert_not_called()
    with pytest.raises(custody.Invalid, match="peer"):
        service.get(custody.digest(obj), uid=None)


def test_enqueue_failure_rolls_back_whole_proposal(rig, monkeypatch):
    store, obj, observed, source, service = rig
    monkeypatch.setattr(store, "enqueue", Mock(side_effect=OSError("fixture disk error")))
    with pytest.raises(OSError):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    assert store.db.execute("SELECT count(*) FROM gateway_custody_proposals").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM notices").fetchone()[0] == 0


def test_shared_notice_rate_cap_does_not_consume_custody_quota(rig):
    store, obj, observed, source, service = rig
    for n in range(32):
        store.insert_notice(str(n), "fixture", 1, 0, 1, 0, 1, "9999-01-01T00:00:00+00:00")
    key = service.register(obj, uid=os.getuid(), max_chars=3200)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"


def test_registration_retains_original_material_on_changed_source(rig):
    store, obj, observed, source, service = rig
    key = service.register(obj, uid=os.getuid(), max_chars=3200)
    source.return_value = dataclasses.replace(observed, original_actor="another operator")
    with pytest.raises(custody.Invalid, match="immutable-proposal"):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    assert service.get(key, uid=os.getuid())["bundle"]["original_actor"] == "original operator"
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["prefix", "proposal", "event", "overall"])
def test_proposal_collisions_and_lifetime_caps_do_not_allocate_notice(rig, kind):
    store, obj, observed, source, service = rig
    key = service.register(obj, uid=os.getuid(), max_chars=3200)
    store.db.execute("DELETE FROM gateway_custody_proposals")
    count = 16 if kind == "event" else 4096 if kind == "overall" else 1
    for index in range(count):
        other = f"{index + 1:064x}"
        store.db.execute(
            "INSERT INTO gateway_custody_proposals VALUES(?,?,?,?,?,?,?)",
            (
                other,
                key[:24] if kind == "prefix" else other[:24] + str(index),
                obj.proposal if kind == "proposal" else str(index),
                obj.event if kind == "event" else "unrelated-event",
                b"{}",
                f"fixture-notice-{index}",
                "terminal",
            ),
        )
    with pytest.raises(custody.Invalid, match="proposal-(collision|capacity)"):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM gateway_custody_proposals").fetchone()[0] == count


def test_notice_enqueue_collision_rolls_back_registration(rig, monkeypatch):
    store, obj, observed, source, service = rig
    monkeypatch.setattr(store, "enqueue", Mock(return_value=False))
    with pytest.raises(custody.Invalid, match="notice-collision"):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    assert store.db.execute("SELECT count(*) FROM gateway_custody_proposals").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM notices").fetchone()[0] == 0
