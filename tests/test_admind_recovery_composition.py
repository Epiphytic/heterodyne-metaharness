"""Owned offline composition: lineage, retirement and recovery remain separate authorities."""

import asyncio
import os
from pathlib import Path

import msgspec
from test_admind_approvals import APPROVE
from test_admind_operations import peer, request
from test_admind_r1 import Unit
from test_admind_recovery import Rig
from test_admind_retirement import command, eligible

from heterodyne.admind import asks, ctl, fulfillment, operations, recovery
from heterodyne.admind.retirement import retire
from heterodyne.admind.store import Store


def test_withdrawn_held_attempt_can_retire_without_reopening_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            adopted = await u.daemon.on_operation(request(), peer())
            assert adopted.lineage[0].ask_id == APPROVE.ask_id
            assert (await u.daemon.on_operation(request("withdraw"), peer())).withdrawn
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            status = await u.daemon.on_operation(request("status"), peer())
            assert status.withdrawn and status.lineage[0].status == "cancelled"
            assert status.lineage[0].attempt is None
            assert not u.store.begin_attempt(APPROVE)
            assert port.calls == 1  # Initial genuine not-issued HOLD, no new native decision.
            assert u.store.retirement.receipt(proof.token, notice) is not None
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_consent_cannot_change_retirement_or_operation_receipts_on_reopen(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, _, proof, notice = await eligible(tmp_path / "unit")
        try:
            await u.daemon.on_operation(request(), peer())
            await u.daemon.on_operation(request("withdraw"), peer())
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            before = tuple(u.store.db.execute("SELECT * FROM asks"))
            receipts = tuple(u.store.db.execute("SELECT * FROM maintenance_retirements"))
            answers = tuple(u.store.db.execute("SELECT * FROM ask_answers"))
            events = tuple(u.store.db.execute("SELECT * FROM approval_operation_events"))
            rig = Rig(tmp_path / "rig", store=u.store)
            rig.deliver()
            assert rig.message().message == "consented"
            assert tuple(u.store.db.execute("SELECT * FROM asks")) == before
            assert tuple(u.store.db.execute("SELECT * FROM maintenance_retirements")) == receipts
            assert tuple(u.store.db.execute("SELECT * FROM ask_answers")) == answers
            assert tuple(u.store.db.execute("SELECT * FROM approval_operation_events")) == events
            db_path = u.settings.state_dir / "admind.db"
        finally:
            u.store.close()
        reopened = Store(db_path)
        try:
            service = recovery.Service(reopened, rig.root)
            assert service.get(rig.key)["state"] == "consented"
            assert tuple(reopened.db.execute("SELECT * FROM asks")) == before
            assert tuple(reopened.db.execute("SELECT * FROM maintenance_retirements")) == receipts
            assert tuple(reopened.db.execute("SELECT * FROM ask_answers")) == answers
            assert tuple(reopened.db.execute("SELECT * FROM approval_operation_events")) == events
        finally:
            reopened.close()
    asyncio.run(scenario())


def test_three_socket_protocols_decode_independently_and_remove_only_owned_paths(tmp_path: Path) -> None:
    async def scenario() -> None:
        u = Unit(tmp_path)
        servers = []
        try:
            servers.append(ctl.CtlServer(u.settings.state_dir / ctl.CTL_SOCKET, u.daemon.on_ctl, u.audit))
            servers.append(ctl.JsonSocketServer(
                u.settings.state_dir / asks.ASK_SOCKET, u.daemon.on_ask_wire, u.audit,
                kind="ask", max_request=asks.MAX_REQUEST, max_reply=asks.MAX_REPLY,
                request_type=asks.AskRequest | fulfillment.Request | operations.Request,
                check=u.daemon.check_ask_wire, refused=asks.refused, failed=asks.failed,
                describe=lambda req: {"op": type(req).__name__}))
            servers.append(ctl.JsonSocketServer(
                u.settings.state_dir / recovery.SOCKET, u.daemon.on_recovery, u.audit,
                kind="maintenance-recovery", max_request=recovery.MAX_BYTES, max_reply=recovery.MAX_BYTES,
                request_type=recovery.Request, check=recovery.check,
                refused=lambda why: recovery.Reply("refused", why),
                failed=lambda: recovery.Reply("unavailable", "fixed"),
                describe=lambda req: {"op": type(req).__name__}))
            for server in servers:
                await server.start()

            async def send(index: int, wire: bytes) -> dict:
                reader, writer = await asyncio.open_unix_connection(servers[index].path)
                try:
                    writer.write(wire + b"\n")
                    await writer.drain()
                    return msgspec.json.decode(await reader.readline())
                finally:
                    writer.close()
                    await writer.wait_closed()

            control_reply = await send(0, b'{"op":"no-such-command"}')
            assert control_reply["result"] == "refused"
            assert (await send(1, msgspec.json.encode(asks.AskList())))["result"] == "ok"
            op = msgspec.json.encode(request())
            assert (await send(1, op[:-1] + b',"mode":"post"}'))["result"] == "refused"
            status = msgspec.json.encode(recovery.Status("a" * 64))
            status_reply = await send(2, status)
            assert status_reply["result"] == "refused"
            assert status_reply["message"] == "missing"
            assert (await send(2, status[:-1] + b',"proposal_sha256":"' + b'b' * 64 + b'"}'))[
                "result"] == "refused"
            assert (await send(2, op))["result"] == "refused"
            assert (await send(1, status))["result"] == "refused"
        finally:
            for server in reversed(servers):
                await server.close()
                assert not os.path.lexists(server.path)
            u.store.close()
    asyncio.run(scenario())


def test_withdrawn_held_attempt_keeps_late_native_decision_instead_of_retirement(tmp_path: Path) -> None:
    from test_admind_approvals import readout

    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            await u.daemon.on_operation(request(), peer())
            assert (await u.daemon.on_operation(request("withdraw"), peer())).withdrawn
            port.current = readout(status="closed", decided=True, decision=APPROVE.action,
                                   approved_by=APPROVE.operator, approved_digest=APPROVE.digest,
                                   via_ref=APPROVE.ref, gate_valid=True, gate_preview=None)
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            status = await u.daemon.on_operation(request("status"), peer())
            assert status.withdrawn and status.lineage[0].status == "approved"
            assert status.lineage[0].decision_refs == [APPROVE.ref]
            assert u.store.retirement.receipt(proof.token, notice) is None
            assert not u.store.begin_attempt(APPROVE)
            assert port.calls == 1
        finally:
            u.store.close()
    asyncio.run(scenario())
