"""Independent private socket/worker qualification of the reconnect source snapshot.

No daemon.run(), tmux, service, native queue or live-runtime request is executed.
"""

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_admind_daemon import Harness
from test_admind_ingress_observation import message
from test_admind_ingress_reconnect import stop, until

from heterodyne.admind import ingress


def test_pending_identity_conflict_busy_and_fifo_dedup(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        try:
            await until(lambda: d.reading)
            h.fake.info_gate = asyncio.Event()
            instance, generation, request_id = d.ingress.instance, d.sub_gen, str(uuid.uuid4())
            accepted = await d.reconnect.admit(instance, generation, request_id)
            assert accepted.result == "accepted"
            await until(lambda: d.count_lock.locked())
            assert (await d.reconnect.admit(instance, generation, request_id)).result == "pending"
            assert (await d.reconnect.admit(instance, d.sub_gen, request_id)).reason == "request-conflict"
            assert (await d.reconnect.admit(instance, d.sub_gen, str(uuid.uuid4()))).result == "busy"
            assert list(d.reconnect.records) == [request_id]
            h.fake.info_gate.set()
            await until(lambda: not d.reconnect.intent)
            assert (await d.reconnect.admit(instance, generation, request_id)).result == "observing"
            first_remaining = None
            for n in range(64):
                next_id = str(uuid.uuid4())
                if n == 0:
                    first_remaining = next_id
                assert (await d.reconnect.admit(instance, d.sub_gen, next_id)).result == "accepted"
                await until(lambda: not d.reconnect.intent)
                assert d.reconnect.records[next_id].state == "observing"
                assert len(d.reconnect.records) <= 64
            assert request_id not in d.reconnect.records
            assert next(iter(d.reconnect.records)) == first_remaining
            # Forgotten IDs cannot authenticate a stale generation.
            assert (await d.reconnect.admit(instance, generation, request_id)).reason == "stale"
        finally:
            await stop(supervisor)
            await h.fake.stop()
    asyncio.run(scenario())


def test_socket_close_does_not_cancel_accepted_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        server = ingress.server(tmp_path / "ingress.sock", h.audit, d.ingress_snapshot, d.ingress_reconnect)
        await server.start()
        try:
            await until(lambda: d.reading)
            h.fake.info_gate = asyncio.Event()
            req = ingress.Request("admind.ingress.request.v1", "reconnect", d.ingress.instance,
                                  d.sub_gen, str(uuid.uuid4()))
            reply = await ingress.request(tmp_path / "ingress.sock", req)
            assert reply.operation is not None
            assert reply.operation.result == "accepted"
            lifecycle = d.reconnect.operation
            assert lifecycle is not None
            await until(lambda: d.count_lock.locked())
            await server.close()
            assert lifecycle is d.reconnect.operation and not lifecycle.done()
            assert d.reconnect.intent and not d.reading
            h.fake.info_gate.set()
            await until(lambda: lifecycle.done())
            assert req.request_id is not None
            assert d.reconnect.records[req.request_id].state == "observing"
            assert not supervisor.done()
        finally:
            await server.close()
            await stop(supervisor)
            await h.fake.stop()
    asyncio.run(scenario())


def test_real_supervisor_shutdown_during_cutover_releases_all_locks(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        try:
            await until(lambda: d.reading)
            h.fake.info_gate = asyncio.Event()
            result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
            await until(lambda: d.count_lock.locked())
            await stop(supervisor)
            assert d.reconnect.operation is not None
            assert result.operation is not None
            assert d.reconnect.operation.done()
            assert d.reconnect.records[result.operation.request_id].reason == "shutdown"
            assert not d.reconnect.intent
            assert all(not lock.locked() for lock in (d.work_lock, d.transition_lock, d.custody_lock))
            assert d.reconnect.reader is not None and d.reconnect.transport is not None
            assert d.reconnect.reader.done() and d.reconnect.transport.closed
        finally:
            await stop(supervisor)
            await h.fake.stop()
    asyncio.run(scenario())


def test_cancel_accepted_operation_before_first_instruction_releases_admission(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.reconnect.running = True
        result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
        assert result.result == "accepted"
        # create_task() has not run: cancellation here cannot execute coroutine finally.
        operation = d.reconnect.operation
        assert operation is not None
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert not d.reconnect.intent
        assert all(not lock.locked() for lock in (d.work_lock, d.transition_lock, d.custody_lock))
        assert result.operation is not None
        assert d.reconnect.records[result.operation.request_id].state == "failed"
    asyncio.run(scenario())


def test_normal_and_custody_dequeued_events_retain_order_until_observed(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        normal = [message(h, mid=f"{n:064x}") for n in (10, 11)]
        custody = [message(h, mid=f"{n:064x}") for n in (20, 21)]
        for event in normal:
            d.work.put_nowait(event)
        for event in custody:
            d.custody_work.put_nowait(event)
        original_bytes = d.ingress_capacity.used
        d.on_message = AsyncMock()
        d.custody_message = AsyncMock()
        d.reconnect.paused = True
        d.reconnect.ready.clear()
        normal_worker = asyncio.create_task(d.worker_loop())
        custody_worker = asyncio.create_task(d.custody_loop())
        try:
            await until(lambda: d.work.qsize() == d.custody_work.qsize() == 1)
            assert not d.on_message.called and not d.custody_message.called
            assert d.ingress_capacity.used == original_bytes
            assert not d.work_lock.locked() and not d.custody_lock.locked()
            d.acked = d.reading = d.group_ok = True
            d.reconnect.observed(d.sub_gen)
            await asyncio.wait_for(asyncio.gather(d.work.join(), d.custody_work.join()), 1)
            assert [call.args[0] for call in d.on_message.await_args_list] == normal
            assert [call.args[0] for call in d.custody_message.await_args_list] == custody
            assert d.ingress_capacity.used == 0
        finally:
            await stop(normal_worker)
            await stop(custody_worker)
    asyncio.run(scenario())
