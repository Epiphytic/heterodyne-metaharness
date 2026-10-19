"""Approved independent custody notice quotas; disposable storage only."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from heterodyne.admind import gateway_custody as custody

pytest_plugins = ("test_admind_gateway_custody_registration",)


def populate(store, count, *, recent=True):
    store.db.execute(custody.REGISTRATION_SCHEMA)
    stamp = datetime.now(UTC) - timedelta(hours=0 if recent else 2)
    for n in range(count):
        key = f"{n + 1:064x}"
        notice_key = "gateway-custody:" + key
        store.db.execute(
            "INSERT INTO gateway_custody_proposals VALUES(?,?,?,?,?,?,?)",
            (
                key,
                key[:24] + str(n),
                str(n),
                str(n),
                custody.canonical({"notice_created_at": stamp.isoformat()}),
                notice_key,
                "terminal",
            ),
        )
        assert store.enqueue(notice_key + ":0", "fixture", None, ownership_kind="system")


@pytest.mark.parametrize("count,recent", [(32, True), (256, False)])
def test_custody_exhaustion_is_independent_of_generic_notice_capacity(rig, count, recent):
    store, obj, observed, source, service = rig
    populate(store, count, recent=recent)
    with pytest.raises(custody.Invalid, match="notice-capacity"):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    assert store.notices_since("2000-01-01") == 0
    assert store.undelivered_notices() == 0
    store.insert_notice("aaaaaa", "fixture", None, 0, 1, 0, 1, datetime.now(UTC).isoformat())
    assert store.enqueue("notice:aaaaaa:0", "ordinary", None, ownership_kind="system")
    assert store.notices_since("2000-01-01") == 1
    assert store.undelivered_notices() == 1
    assert store.db.execute("SELECT count(*) FROM gateway_custody_proposals").fetchone()[0] == count


def test_pending_generic_notice_exhaustion_does_not_block_custody(rig):
    store, obj, observed, source, service = rig
    for n in range(256):
        alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
        key = "aaaa" + alphabet[n // len(alphabet)] + alphabet[n % len(alphabet)]
        assert store.enqueue(f"notice:{key}:0", "ordinary", None, ownership_kind="system")
    assert store.undelivered_notices() == 256
    key = service.register(obj, uid=os.getuid(), max_chars=3200)
    assert service.get(key, uid=os.getuid())["state"] == "awaiting-consent"
    assert store.undelivered_notices() == 256
    assert store.notices_since("2000-01-01") == 0
