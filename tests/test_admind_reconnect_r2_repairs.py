"""R2 causal production-entry regressions using private sockets/Store only."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, Mock

import msgspec
import pytest
from test_admind_daemon import Harness
from test_admind_ingress_reconnect import stop, until

from heterodyne.admind import ctl, ingress


def test_latched_failed_group_check_releases_all_locks_promptly(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.latched = Mock(return_value=True)
        d.check_group = AsyncMock(return_value=False)
        d.reconnect.running = True
        d.reconnect.recovery_seconds = 125.0
        await h.fake.start()
        result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
        task = d.reconnect.operation
        assert result.operation is not None and task is not None
        try:
            done, _ = await asyncio.wait({task}, timeout=1.0)
            assert task in done, "latched verification failure must not hold locks for 125 seconds"
            assert d.reconnect.records[result.operation.request_id].reason == "failed-observation"
            assert not any(lock.locked() for lock in (d.work_lock, d.transition_lock, d.custody_lock))
            assert not d.reconnect.intent and d.reconnect.paused and not d.observing
            d.check_group.assert_awaited_once()
        finally:
            await stop(task)
            await d.reconnect._stop_reader(asyncio.get_running_loop().time() + 1)
            await h.fake.stop()
    asyncio.run(scenario())


def test_supervisor_fault_cancels_pending_cutover_without_claiming_shutdown(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        owner = asyncio.create_task(d.inbound_loop())
        try:
            await until(lambda: d.reading)
            transport = d.reconnect.transport
            assert transport is not None
            close = transport.close
            entered = asyncio.Event()
            count = 0

            async def blocked_first_close(deadline: float | None = None) -> bool:
                nonlocal count
                count += 1
                if count == 1:
                    entered.set()
                    await asyncio.Event().wait()
                return await close(deadline)

            async def fail_wait() -> Literal[True]:
                await entered.wait()
                raise RuntimeError("private supervisor boundary fault")

            transport.close = blocked_first_close
            d.reconnect.changed.wait = fail_wait
            result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
            assert result.operation is not None
            with pytest.raises(RuntimeError, match="private supervisor boundary fault"):
                await asyncio.wait_for(owner, 1)
            assert not d.shutting_down and not d.reconnect.running
            record = d.reconnect.records[result.operation.request_id]
            assert record.state == "failed" and record.reason == "failed-observation"
            assert not any(lock.locked() for lock in (d.work_lock, d.transition_lock, d.custody_lock))
            rows = [json.loads(line) for line in h.audit.path.read_text().splitlines()]
            assert any(row.get("action") == "failed-observation" for row in rows)
            assert not any(row.get("action") == "shutdown" for row in rows)
        finally:
            if not owner.done():
                await stop(owner)
            await h.fake.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [
    b'{"schema":"admind.ingress.request.v1","op":"reconnect","generation":"5"}',
    b'{"schema":"admind.ingress.request.v1","op":"reconnect","extra":"private-value"}',
    b'{"schema":"admind.ingress.request.v1","op":"reconnect","extra":"' + b"x" * 4097 + b'"}',
])
def test_decode_level_reconnect_refusal_has_fixed_payload_free_audit(tmp_path: Path, payload: bytes) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        path = tmp_path / "ingress.sock"
        server = ingress.server(path, h.audit, d.ingress_snapshot, d.ingress_reconnect)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(payload + b"\n")
            await writer.drain()
            reply = msgspec.json.decode(await reader.readline(), type=ingress.Reply)
            assert reply == ingress.Reply("refused", "malformed")
            await ctl.hang_up(writer)
            assert h.audit.path.exists(), "decode refusal must leave a fixed protocol audit"
            rows = [json.loads(line) for line in h.audit.path.read_text().splitlines()]
            assert len(rows) == 1
            assert rows[0]["action"] == "reconnect-protocol-failed"
            assert rows[0]["reason"] == "malformed"
            assert "private-value" not in h.audit.path.read_text()
            size = h.audit.path.stat().st_size
            await ingress.request(path)
            assert h.audit.path.stat().st_size == size
            assert not d.reconnect.records and d.sub_gen == 0
        finally:
            await server.close()
    asyncio.run(scenario())


def test_unknown_teardown_does_not_enqueue_unreachable_or_stale_alarm(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        owner = asyncio.create_task(d.inbound_loop())
        transport = None
        close = None
        try:
            await until(lambda: d.reading)
            transport, reader = d.reconnect.transport, d.reconnect.reader
            assert transport is not None and reader is not None
            close = transport.close
            transport.close = AsyncMock(return_value=False)
            reader.cancel()
            await until(lambda: d.reconnect.teardown_unknown)
            await d.outbox_pass()
            assert not h.fake.sent
            count = h.store.db.execute(
                "SELECT count(*) FROM outbox WHERE key LIKE 'ingress-teardown:%'"
            ).fetchone()[0]
            assert count == 0, "an observation-gated outage alarm cannot be delivered or left for later"
            status = d.ingress_snapshot().snapshot
            assert status is not None and status.teardown_unknown and not status.observing
            assert d.reconnect.transport is transport and d.reconnect.reader is reader
        finally:
            if transport is not None and close is not None:
                transport.close = close
            await stop(owner)
            await h.fake.stop()
    asyncio.run(scenario())
