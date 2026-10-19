"""Actual R1 regressions, synthetic authority and disposable storage only."""

import dataclasses
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_admind_gateway_custody_decisions import POLICY, answer, ready
from test_admind_gateway_custody_permits import issue
from test_admind_gateway_custody_quotas import populate

from heterodyne.admind import gateway_custody as custody

pytest_plugins = ("test_admind_gateway_custody_registration", "test_admind_gateway_custody_permits")


def test_agreeing_new_settle_retains_first_consent(rig):
    _, key, service, _ = ready(rig)
    answer(service, key)
    before = service.get(key, uid=os.getuid())["bundle"]["decision"]
    assert answer(service, key, mid="1" * 64) == "consented"
    status = service.get(key, uid=os.getuid())
    assert status["state"] == "consented"
    assert status["bundle"]["decision"] == before
    assert len(status["bundle"]["commands"]) == 2


def test_concurrent_authenticated_decline_is_not_lost(rig):
    _, key, service, authority = ready(rig)
    barrier = threading.Barrier(2)
    settled = threading.Event()
    local = threading.local()

    def policy(*args):
        local.calls += 1
        barrier.wait(timeout=5)
        if local.action == "decline" and local.calls == 2:
            assert settled.wait(5)
        return POLICY

    authority.side_effect = policy

    def decide(pair):
        action, mid = pair
        local.action, local.calls = action, 0
        try:
            result = answer(service, key, action=action, mid=mid)
            if action == "settle":
                settled.set()
            return result
        except custody.Invalid as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, [("settle", "1" * 64), ("decline", "2" * 64)]))
    status = service.get(key, uid=os.getuid())
    assert status["state"] == "revoked"
    assert "revoked" in outcomes
    assert any(
        v["event"]["text"].startswith("!custody decline ") for v in status["bundle"]["commands"].values()
    )


@pytest.mark.parametrize("conflict", [True, False])
def test_terminal_state_survives_conflict_and_capacity(rig, conflict):
    store, key, service, _ = ready(rig)
    answer(service, key)
    store.db.execute("UPDATE gateway_custody_proposals SET state='terminal'")
    if not conflict:
        bundle = service.get(key, uid=os.getuid())["bundle"]
        bundle["commands"]["fixture-reserved-bytes"] = "x" * 32000
        store.db.execute("UPDATE gateway_custody_proposals SET bundle=?", (custody.canonical(bundle),))
    before = service.get(key, uid=os.getuid())
    if conflict:
        with pytest.raises(custody.Invalid):
            answer(service, key, action="decline")
    else:
        assert answer(service, key, action="decline", mid="1" * 64) == "held"
    assert service.get(key, uid=os.getuid()) == before


def test_failed_notice_is_reported_as_recovery_hold_not_registered(rig):
    store, obj, _, _, service = rig
    key = service.register(obj, uid=os.getuid(), max_chars=3200)
    seq = store.db.execute("SELECT seq FROM outbox").fetchone()[0]
    store.mark_failed(seq)
    with pytest.raises(custody.Invalid, match="notice-failed"):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    status = service.get(key, uid=os.getuid())
    assert status["notice_status"] == "failed"
    assert status["state"] == "awaiting-consent"
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_failed_notices_still_exhaust_undelivered_limit(rig):
    store, obj, _, _, service = rig
    populate(store, 256, recent=False)
    store.db.execute("UPDATE outbox SET status='failed'")
    with pytest.raises(custody.Invalid, match="notice-capacity"):
        service.register(obj, uid=os.getuid(), max_chars=3200)


def proof_for(issued_rig, previous, observed):
    _, key, service, _, enrolled, rig = issued_rig
    prior = custody.permit(previous)
    value = custody.Nonconsumption(
        "gateway-custody-nonconsumption.v1",
        key,
        hashlib.sha256(previous).hexdigest(),
        prior.permit_generation,
        key,
        rig[1].chain_root,
        rig[1].boot_id,
        str(observed),
    )
    raw = custody.canonical(value)
    for suffix in ("nonconsumption", "superseded"):
        path = enrolled.queue_root / f"{key}.{suffix}-{prior.permit_generation}.json"
        path.write_bytes(raw)
        path.chmod(0o600)
    service.source.return_value = dataclasses.replace(rig[2], boottime_ns=observed + 1)
    return raw


def test_renewal_requires_expired_old_window_and_persists_exact_source_tombstone(issued_rig):
    _, key, service, _, enrolled, _ = issued_rig
    old = issue(service, key)
    raw = proof_for(issued_rig, old, int(custody.permit(old).initiate_before_boottime_ns))
    renewed = issue(service, key, hashlib.sha256(raw).hexdigest())
    tombstone = enrolled.root / "generations" / key / "1.superseded.json"
    assert tombstone.read_bytes() == raw
    assert tombstone.stat().st_mode & 0o777 == 0o600
    assert (enrolled.root / "generations" / key / "1.json").read_bytes() == old
    assert custody.permit(renewed).permit_generation == 2


def test_early_nonconsumption_cannot_renew(issued_rig):
    _, key, service, _, _, _ = issued_rig
    old = issue(service, key)
    raw = proof_for(issued_rig, old, int(custody.permit(old).issued_boottime_ns) + 1)
    with pytest.raises(custody.Invalid, match="nonconsumption-clock"):
        issue(service, key, hashlib.sha256(raw).hexdigest())
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 1


def test_publication_never_holds_shared_store_lock(issued_rig, monkeypatch):
    store, key, service, _, _, _ = issued_rig
    real = custody.recovery.publish
    observed = []

    def publish(path, raw):
        acquired = threading.Event()

        def reader():
            with store.lock:
                acquired.set()

        thread = threading.Thread(target=reader)
        thread.start()
        observed.append(acquired.wait(0.5))
        thread.join(timeout=0.5)
        return real(path, raw)

    monkeypatch.setattr(custody.recovery, "publish", publish)
    issue(service, key)
    assert observed and all(observed)


def test_agreeing_capacity_failure_does_not_revoke_existing_consent(rig):
    store, key, service, _ = ready(rig)
    answer(service, key)
    bundle = service.get(key, uid=os.getuid())["bundle"]
    bundle["commands"]["fixture-padding"] = "x" * 32000
    raw = custody.canonical(bundle)
    store.db.execute("UPDATE gateway_custody_proposals SET bundle=?", (raw,))
    with pytest.raises(custody.Invalid, match="bundle-capacity"):
        answer(service, key, mid="1" * 64)
    status = service.get(key, uid=os.getuid())
    assert status["state"] == "consented"
    assert custody.canonical(status["bundle"]) == raw


def test_renewal_repair_requires_original_source_tombstone(issued_rig, monkeypatch):
    _, key, service, _, enrolled, _ = issued_rig
    old = issue(service, key)
    raw = proof_for(issued_rig, old, int(custody.permit(old).initiate_before_boottime_ns))
    real = custody.recovery.publish
    monkeypatch.setattr(custody.recovery, "publish", lambda *args: (_ for _ in ()).throw(OSError("ENOSPC")))
    with pytest.raises(OSError):
        issue(service, key, hashlib.sha256(raw).hexdigest())
    retained = service.get(key, uid=os.getuid())["bundle"]["permits"][-1]
    monkeypatch.setattr(custody.recovery, "publish", real)
    source_path = enrolled.queue_root / f"{key}.superseded-1.json"
    source_path.unlink()
    with pytest.raises(OSError):
        issue(service, key)
    assert not (enrolled.root / "generations" / key / "2.json").exists()
    source_path.write_bytes(raw)
    source_path.chmod(0o600)
    assert issue(service, key) == custody.canonical(retained)
    assert (enrolled.root / "generations" / key / "1.superseded.json").read_bytes() == raw


def test_direct_message_fallback_routes_to_independent_worker(rig):
    import asyncio

    from test_admind_gateway_custody_control import daemon, event

    from heterodyne.admind.daemon import Admind

    async def run():
        _, key, service, _ = ready(rig)
        d = daemon(service)
        d.latched.return_value = False
        await Admind.on_message(d, event(key))
        assert d.custody_work.qsize() == 1
        d.custody_message.assert_not_called()

    asyncio.run(run())


def test_independent_policy_view_shares_publication_mutex(rig):
    import asyncio
    from unittest.mock import AsyncMock

    from heterodyne.admind import gateway_custody_control as control

    async def run():
        service = rig[-1]
        bound = control.independent(service, AsyncMock(return_value=POLICY))
        assert bound.publication_lock is service.publication_lock

    asyncio.run(run())


def test_socket_exact_maximum_excludes_frame_newline(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock

    import msgspec

    from heterodyne.admind import ctl
    from heterodyne.admind import gateway_custody_control as control
    from heterodyne.admind.audit import Audit

    async def run():
        handler = AsyncMock(return_value=control.Reply("ok", "synthetic"))
        server = ctl.JsonSocketServer(
            tmp_path / "sock" / "custody.sock",
            handler,
            Audit(tmp_path / "audit.jsonl"),
            kind="gateway-custody",
            max_request=custody.MAX_REQUEST,
            max_reply=custody.MAX_REQUEST,
            request_type=custody.Request,
            check=custody.check,
            refused=lambda why: control.Reply("refused", why),
            failed=lambda: control.Reply("unavailable", "failed"),
            describe=lambda req: {},
        )
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(server.path))
            raw = custody.canonical(custody.GatewayCustodyStatus("a" * 64))
            writer.write(raw + b" " * (custody.MAX_REQUEST - len(raw)) + b"\n")
            await writer.drain()
            reply = msgspec.json.decode(await reader.readline(), type=control.Reply)
            assert reply.result == "ok"
            assert handler.await_count == 1
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()

    asyncio.run(run())
