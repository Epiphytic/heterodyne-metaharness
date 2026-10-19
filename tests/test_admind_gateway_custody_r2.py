"""R2 regressions: disposable storage and synthetic source authority only."""

import dataclasses
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import msgspec
import pytest
from test_admind_gateway_custody_decisions import answer, ready
from test_admind_gateway_custody_permits import certificate, issue

from heterodyne.admind import gateway_custody as custody

pytest_plugins = ("test_admind_gateway_custody_registration", "test_admind_gateway_custody_permits")


def test_command_storm_cannot_consume_three_permit_reserve(issued_rig):
    _, key, service, _, _, _ = issued_rig
    answer(service, key)
    held = 0
    for n in range(100):
        try:
            answer(service, key, mid=f"{n:064x}")
        except custody.Invalid as exc:
            assert str(exc) == "bundle-capacity"
            held += 1
    assert held
    first = issue(service, key)
    second = issue(service, key, certificate(issued_rig, first))
    third = issue(service, key, certificate(issued_rig, second))
    assert custody.permit(third).permit_generation == 3
    assert len(custody.canonical(service.get(key, uid=os.getuid())["bundle"])) <= custody.MAX_BUNDLE


@pytest.mark.parametrize("removed,wrong_thread", [(True, False), (False, True)])
def test_conflicting_replay_requires_current_authority_and_thread(rig, removed, wrong_thread):
    _, key, service, authority = ready(rig)
    answer(service, key)
    before = service.get(key, uid=os.getuid())
    if removed:
        authority.return_value = None
    with pytest.raises(custody.Invalid):
        if wrong_thread:
            answer(service, key, action="revoke", target=None)
        else:
            answer(service, key, action="revoke")
    assert service.get(key, uid=os.getuid()) == before


@pytest.mark.parametrize(
    "status,mid", [("sent", None), ("quarantined", None), ("suppressed", None), ("delayed", None)]
)
def test_duplicate_registration_does_not_report_unmapped_notice_success(rig, status, mid):
    store, obj, _, _, service = rig
    service.register(obj, uid=os.getuid(), max_chars=3200)
    store.db.execute("UPDATE outbox SET status=?,message_id=?", (status, mid))
    before = store.db.execute("SELECT * FROM outbox").fetchall()
    with pytest.raises(custody.Invalid, match="notice-"):
        service.register(obj, uid=os.getuid(), max_chars=3200)
    assert store.db.execute("SELECT * FROM outbox").fetchall() == before


def test_exact_renewal_retry_repairs_retained_candidate(issued_rig, monkeypatch):
    _, key, service, _, _, _ = issued_rig
    first = issue(service, key)
    proof_hash = certificate(issued_rig, first)
    real = custody.recovery.publish
    monkeypatch.setattr(custody.recovery, "publish", lambda *a: (_ for _ in ()).throw(OSError("ENOSPC")))
    with pytest.raises(OSError):
        issue(service, key, proof_hash)
    retained = service.get(key, uid=os.getuid())["bundle"]["permits"][-1]
    monkeypatch.setattr(custody.recovery, "publish", real)
    assert issue(service, key, proof_hash) == custody.canonical(retained)
    assert len(service.get(key, uid=os.getuid())["bundle"]["permits"]) == 2


def test_published_renewal_readback_needs_no_new_window_or_source_certificate(issued_rig):
    _, key, service, _, enrolled, rig = issued_rig
    first = issue(service, key)
    proof_hash = certificate(issued_rig, first)
    second = issue(service, key, proof_hash)
    for path in enrolled.queue_root.iterdir():
        path.unlink()
    (enrolled.queue_root / f"{key}.consumed.json").write_text("unknown")
    service.source.return_value = dataclasses.replace(rig[2], boottime_ns=int(rig[1].expires_boottime_ns) - 1)
    assert issue(service, key) == second
    assert issue(service, key, proof_hash) == second


def test_external_authority_wait_does_not_block_authenticated_revoke(issued_rig):
    _, key, service, authority, _, _ = issued_rig
    answer(service, key)
    entered, proceed = threading.Event(), threading.Event()
    worker_id = []

    def policy(*args):
        if threading.get_ident() in worker_id:
            entered.set()
            assert proceed.wait(5)
        return "f" * 64

    authority.side_effect = policy

    def issuing():
        worker_id.append(threading.get_ident())
        with pytest.raises(custody.Invalid, match="proposal-changed"):
            issue(service, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(issuing)
        assert entered.wait(2)
        revoked = pool.submit(answer, service, key, action="revoke", mid="1" * 64)
        try:
            assert revoked.result(timeout=1) == "revoked"
        finally:
            proceed.set()
        pending.result(timeout=5)


def test_direct_fallback_full_queue_audits_without_ack(rig):
    import asyncio

    from test_admind_gateway_custody_control import daemon, event

    from heterodyne.admind.daemon import Admind

    async def run():
        _, key, service, _ = ready(rig)
        d = daemon(service)
        d.latched.return_value = False
        d.custody_work = asyncio.Queue(maxsize=1)
        d.custody_work.put_nowait(event(key))
        await Admind.on_message(d, event(key))
        d.audit.write.assert_any_call("gateway-custody", action="refused", reason="command-capacity")
        assert d.custody_work.qsize() == 1

    asyncio.run(run())


def test_rate_limit_uses_source_clock_not_wall_clock(rig):
    import uuid

    store, obj, observed, source, service = rig
    for _n in range(32):
        current = msgspec.structs.replace(obj, proposal=str(uuid.uuid4()), event=str(uuid.uuid4()))
        source.return_value = dataclasses.replace(observed, descriptor=custody.canonical(current))
        service.register(current, uid=os.getuid(), max_chars=3200)
    store.db.execute(
        "UPDATE gateway_custody_proposals SET bundle=json_set(bundle,'$.notice_created_at','1900-01-01')"
    )
    current = msgspec.structs.replace(obj, proposal=str(uuid.uuid4()), event=str(uuid.uuid4()))
    source.return_value = dataclasses.replace(observed, descriptor=custody.canonical(current))
    with pytest.raises(custody.Invalid, match="notice-capacity"):
        service.register(current, uid=os.getuid(), max_chars=3200)
    source.return_value = dataclasses.replace(source.return_value, boottime_ns=3_600_000_000_002)
    assert service.register(current, uid=os.getuid(), max_chars=3200) == custody.digest(current)


def test_exact_readback_does_not_republish_completed_bytes(issued_rig, monkeypatch):
    _, key, service, _, _, _ = issued_rig
    first = issue(service, key)
    second = issue(service, key, certificate(issued_rig, first))
    monkeypatch.setattr(custody.recovery, "publish", lambda *a: pytest.fail("unexpected republish"))
    monkeypatch.setattr(custody.recovery, "replace_current", lambda *a: pytest.fail("unexpected rewrite"))
    assert issue(service, key) == second
