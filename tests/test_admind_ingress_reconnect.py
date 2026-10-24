"""Private real reader/worker/Unix socket fixtures; never starts daemon services."""

import asyncio
import contextlib
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import msgspec
import pytest
from test_admind_daemon import Harness
from test_admind_ingress_observation import message

from heterodyne.admind import ingress
from heterodyne.admind.ingress_reconnect import Capacity, RetainedQueue
from heterodyne.marmot.control import InboundMessage


async def until(predicate) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_real_socket_cutover_is_owned_deduplicated_and_does_not_change_asks(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        server = ingress.server(tmp_path / "ingress.sock", h.audit, d.ingress_snapshot, d.ingress_reconnect)
        await server.start()
        try:
            await until(lambda: d.reading)
            original_reader, original_transport = d.reconnect.reader, d.reconnect.transport
            generation = d.sub_gen
            req = ingress.Request(
                "admind.ingress.request.v1", "reconnect", d.ingress.instance, generation, str(uuid.uuid4())
            )
            # Transport socket returns accepted; lifecycle is retained by supervisor.
            reply = await ingress.request(tmp_path / "ingress.sock", req)
            assert reply.operation.result == "accepted"
            await until(lambda: not d.reconnect.intent)
            assert original_reader.done() and original_transport.closed
            assert d.reconnect.reader is not original_reader
            assert d.reading and d.group_ok and d.observing and d.acked
            assert not supervisor.done()
            repeat = await ingress.request(tmp_path / "ingress.sock", req)
            assert repeat.operation.result == "observing"
            assert d.sub_gen > generation
            assert d.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
        finally:
            await server.close()
            await stop(supervisor)
            await h.fake.stop()

    asyncio.run(scenario())


def test_busy_preserves_generation_and_worker_waiter_retains_dequeued_event(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.reconnect.running = True
        generation = d.sub_gen
        async with d._ingress_lock("work", "membership"):
            busy = await d.reconnect.admit(d.ingress.instance, generation, str(uuid.uuid4()))
            assert busy.result == "busy" and d.sub_gen == generation
            assert not d.reconnect.intent and not d.reconnect.records
        d.reconnect.paused = True
        d.reconnect.ready.clear()
        d.on_message = AsyncMock()
        event = message(h)
        d.work.put_nowait(event)
        used = d.ingress_capacity.used
        worker = asyncio.create_task(d.worker_loop())
        try:
            await until(lambda: d.work.qsize() == 0)
            assert not d.work_lock.locked() and not d.on_message.called
            assert d.ingress_capacity.used == used
            assert d.work.full() is False
            d.acked = d.reading = d.group_ok = True
            d.reconnect.observed(d.sub_gen)
            await until(lambda: d.on_message.called)
            await d.work.join()
            d.on_message.assert_awaited_once_with(event)
            assert d.ingress_capacity.used == 0
        finally:
            await stop(worker)

    asyncio.run(scenario())


def test_retained_count_and_shared_byte_limits_include_inflight(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    event = message(h)
    size = len(msgspec.json.encode(event))
    capacity = Capacity(size * 2)
    normal = RetainedQueue[InboundMessage](capacity)
    custody = RetainedQueue[InboundMessage](capacity)
    normal.put_nowait(event)
    assert normal.get_nowait() is event
    custody.put_nowait(event)
    with pytest.raises(asyncio.QueueFull):
        normal.put_nowait(event)
    normal.task_done()
    normal.put_nowait(event)
    assert capacity.used == size * 2
    count = RetainedQueue[InboundMessage](Capacity(size * 300))
    for _ in range(256):
        count.put_nowait(event)
    count.get_nowait()
    assert count.full()
    with pytest.raises(asyncio.QueueFull):
        count.put_nowait(event)
    count.task_done()
    count.put_nowait(event)


def test_uncertain_teardown_retains_exact_handles_and_fresh_request_retries(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.reconnect.running = True
        stubborn = asyncio.Event()
        entered = asyncio.Event()

        async def reader() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                entered.set()
                await stubborn.wait()

        original = asyncio.create_task(reader())
        await asyncio.sleep(0)
        d.reconnect.reader = original
        d.reconnect.teardown_seconds = 0.01
        first = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
        await until(lambda: not d.reconnect.intent)
        assert first.result == "accepted"
        assert entered.is_set() and not original.done()
        assert d.reconnect.teardown_unknown
        assert d.reconnect.reader is original and not d.reading
        assert not d.work_lock.locked() and not d.transition_lock.locked() and not d.custody_lock.locked()
        # A later request may close the exact original; it does not replace unknown custody.
        stubborn.set()
        await original
        await h.fake.start()
        try:
            second = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
            assert second.result == "accepted"
            await until(lambda: not d.reconnect.intent)
            assert d.reading and not d.reconnect.teardown_unknown
            assert d.reconnect.reader is not original
        finally:
            await d.reconnect._stop_reader(time.monotonic() + 1)
            await h.fake.stop()

    asyncio.run(scenario())


def test_stale_ack_cannot_reopen_and_group_wait_uses_shared_deadline(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        generation = d.sub_gen
        d.reconnect.invalidate()
        await d.confirm_observing(generation)
        assert not d.acked and not d.reading
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        try:
            await until(lambda: d.reading)
            await d.count_lock.acquire()
            d.reconnect.recovery_seconds = 0.03
            result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
            assert result.result == "accepted"
            await until(lambda: not d.reconnect.intent)
            assert d.reconnect.records[result.operation.request_id].reason == "failed-observation"
            assert not d.reading and not d.observing
            assert not d.work_lock.locked()
            d.count_lock.release()
        finally:
            if d.count_lock.locked():
                d.count_lock.release()
            await stop(supervisor)
            await h.fake.stop()

    asyncio.run(scenario())


def test_request_shape_is_closed() -> None:
    assert ingress.check(ingress.Request("admind.ingress.request.v1", "status", "a" * 32))
    for request_id in ("not-a-uuid", uuid.uuid4().hex, str(uuid.uuid4()).upper()):
        assert ingress.check(
            ingress.Request("admind.ingress.request.v1", "reconnect", "a" * 32, 0, request_id)
        )
    assert (
        ingress.check(
            ingress.Request("admind.ingress.request.v1", "reconnect", "a" * 32, 0, str(uuid.uuid4()))
        )
        is None
    )


def test_automatic_teardown_cannot_invalidate_concurrent_accepted_cutover(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        original = asyncio.create_task(asyncio.sleep(0))
        await original
        d.reconnect.reader = original
        entered = asyncio.Event()
        release = asyncio.Event()
        class GatedTransport:
            calls = 0

            async def close(self, deadline: float) -> bool:
                self.calls += 1
                if self.calls == 1:
                    entered.set()
                    await release.wait()
                return True

        d.reconnect.transport = GatedTransport()
        supervisor = asyncio.create_task(d.inbound_loop())
        try:
            await entered.wait()
            result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
            assert result.result == "accepted"
            # Explicit cutover owns a new reader while automatic teardown is still waiting.
            await until(lambda: d.reading)
            generation = d.sub_gen
            release.set()
            await until(lambda: not d.reconnect.intent)
            await asyncio.sleep(.01)
            assert d.sub_gen == generation and d.reading
            assert d.reconnect.reader is not original
            assert not supervisor.done()
        finally:
            release.set()
            await stop(supervisor)
            await h.fake.stop()

    asyncio.run(scenario())


def test_accepted_audit_failure_keeps_gates_closed_and_releases_owned_locks(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.reconnect.running = True
        def fail(*args, **kwargs):
            raise OSError("private fixture")
        h.audit.write = fail
        result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
        await until(lambda: not d.reconnect.intent)
        assert d.reconnect.records[result.operation.request_id].reason == "failed-observation"
        assert not d.reconnect.teardown_unknown
        assert d.reconnect.paused and not d.reading
        assert all(not lock.locked() for lock in (d.work_lock, d.transition_lock, d.custody_lock))
        await d.reconnect.operation

    asyncio.run(scenario())
