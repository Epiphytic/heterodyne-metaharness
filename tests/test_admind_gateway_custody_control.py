"""Offline control framing and independently scheduled custody routing."""

import asyncio
import os
from unittest.mock import AsyncMock, Mock

import pytest
from test_admind_gateway_custody_decisions import ACCOUNT, GROUP, MID, POLICY, SENDER, TARGET, ready

from heterodyne.admind import gateway_custody as custody
from heterodyne.admind import gateway_custody_control as control

pytest_plugins = ("test_admind_gateway_custody_registration",)


def test_control_register_and_status_preserve_closed_source_contract(rig):
    store, obj, _, _, service = rig
    reply = control.perform(service, custody.GatewayCustodyRegister(obj), uid=os.getuid(), max_chars=3200)
    assert reply.result == "registered"
    key = custody.digest(obj)
    assert reply.data == {"proposal_sha256": key}
    status = control.perform(service, custody.GatewayCustodyStatus(key), uid=os.getuid(), max_chars=3200)
    assert status.result == "ok"
    assert status.data is not None
    assert status.data["state"] == "awaiting-consent"
    with pytest.raises(custody.Invalid, match="peer"):
        control.perform(service, custody.GatewayCustodyStatus(key), uid=None, max_chars=3200)


def test_absent_production_source_cannot_be_enabled_by_control(rig):
    store, obj, _, _, _ = rig
    service = custody.Service(store)
    with pytest.raises(custody.Invalid, match="source-unavailable"):
        control.perform(service, custody.GatewayCustodyRegister(obj), uid=os.getuid(), max_chars=3200)
    assert not store.db.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'gateway_custody%'"
    ).fetchall()


def test_independent_worker_runs_authority_on_event_loop(rig):
    async def scenario():
        store, key, service, _ = ready(rig)
        authority = AsyncMock(return_value=POLICY)
        isolated = control.independent(service, authority)
        result = await control.worker(
            lambda: isolated.message(
                account=ACCOUNT,
                group=GROUP,
                mid=MID,
                sender=SENDER,
                target=TARGET,
                text=f"!custody settle {key[:24]}",
            )
        )
        assert result == "consented"
        assert authority.await_count == 2
        authority.assert_awaited_with(ACCOUNT, GROUP, SENDER)

    asyncio.run(scenario())


def test_cancelled_control_waits_for_worker_before_releasing_ownership():
    import threading

    async def scenario():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()

        def perform():
            entered.set()
            release.wait(timeout=5)
            finished.set()

        task = asyncio.create_task(control.worker(perform))
        await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(scenario())


def event(key, text=None):
    from heterodyne.marmot.control import InboundMessage, Message, ReplyTo, Sender

    return InboundMessage(
        ACCOUNT,
        GROUP,
        Message(MID, Sender(SENDER, False), text or f"!custody settle {key[:24]}", 1),
        ReplyTo(TARGET),
    )


def daemon(service):
    from heterodyne.admind import ingress, ingress_reconnect
    from heterodyne.admind.daemon import Admind

    d = Mock(spec=Admind)
    d.ingress = ingress.Observation()
    d.reconnect = ingress_reconnect.Reconnect(d)
    d.ingress_capacity = ingress_reconnect.Capacity()
    d._enqueue_ingress = Admind._enqueue_ingress.__get__(d, Admind)
    d.transition_lock = asyncio.Lock()
    d._ingress_lock = Admind._ingress_lock.__get__(d, Admind)
    d.gateway_custody = service
    d.account, d.group = ACCOUNT, GROUP
    d.operators = {SENDER: "synthetic independent operator"}
    d.custody_lock = asyncio.Lock()
    d.custody_work = asyncio.Queue(maxsize=256)
    d.work = asyncio.Queue()
    d.work_lock = asyncio.Lock()
    d.s = Mock(chunk_chars=3200)
    d.wake = asyncio.Event()
    d.refresh_authority = Mock(return_value=True)
    d.check_group = AsyncMock(return_value=True)
    d.recovery_authority = Mock(return_value=POLICY)
    d.custody_authority = AsyncMock(return_value=POLICY)
    d.audit = Mock()
    return d


def test_actual_daemon_control_and_native_command_do_not_wait_for_model_lock(rig):
    from heterodyne.admind import ctl
    from heterodyne.admind.daemon import Admind

    async def scenario():
        store, key, service, _ = ready(rig)
        d = daemon(service)
        await d.work_lock.acquire()
        reply = await asyncio.wait_for(
            Admind.on_gateway_custody(d, custody.GatewayCustodyStatus(key), ctl.Peer(None, os.getuid())), 0.5
        )
        assert reply.result == "ok"
        Admind.on_event(d, event(key))
        assert d.work.empty() and d.custody_work.qsize() == 1
        await asyncio.wait_for(Admind.custody_message(d, d.custody_work.get_nowait()), 0.5)
        assert service.get(key, uid=os.getuid())["state"] == "consented"
        assert store.db.execute("SELECT count(*) FROM prompts").fetchone()[0] == 0
        assert store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
        d.work_lock.release()

    asyncio.run(scenario())


def test_actual_daemon_authority_binds_account_group_and_current_operator(rig):
    from heterodyne.admind.daemon import Admind

    async def scenario():
        d = daemon(rig[-1])
        d.account, d.group = ACCOUNT, GROUP
        assert await Admind.custody_authority(d, ACCOUNT, GROUP, SENDER) == POLICY
        assert await Admind.custody_authority(d, "0" * 64, GROUP, SENDER) is None
        assert await Admind.custody_authority(d, ACCOUNT, "0" * 64, SENDER) is None
        d.refresh_authority.return_value = False
        assert await Admind.custody_authority(d, ACCOUNT, GROUP, SENDER) is None
        d.refresh_authority.return_value = True
        d.check_group.return_value = False
        assert await Admind.custody_authority(d, ACCOUNT, GROUP, SENDER) is None
        d.check_group.return_value = True
        d.recovery_authority.return_value = None
        assert await Admind.custody_authority(d, ACCOUNT, GROUP, SENDER) is None

    asyncio.run(scenario())


def test_actual_socket_rejects_duplicate_keys_before_handler(tmp_path):
    import msgspec

    from heterodyne.admind import ctl
    from heterodyne.admind.audit import Audit

    async def scenario():
        handler = AsyncMock(return_value=control.Reply("ok", "synthetic"))
        server = ctl.JsonSocketServer(
            tmp_path / "socket" / "custody.sock",
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
            writer.write(
                b'{"type":"status","proposal_sha256":"'
                + b"a" * 64
                + b'","proposal_sha256":"'
                + b"b" * 64
                + b'"}\n'
            )
            await writer.drain()
            reply = msgspec.json.decode(await reader.readline(), type=control.Reply)
            assert reply.result == "refused"
            handler.assert_not_awaited()
            writer.close()
            await writer.wait_closed()
            reply = await ctl.request(
                server.path,
                custody.GatewayCustodyStatus("a" * 64),
                1,
                reply_type=control.Reply,
                require_uid=os.getuid(),
                max_reply=custody.MAX_REQUEST,
            )
            assert reply.result == "ok"
            assert handler.await_count == 1
        finally:
            await server.close()

    asyncio.run(scenario())
