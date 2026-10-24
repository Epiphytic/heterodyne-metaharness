"""Actual daemon observations using private Stores; no upstream/agent process is started."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import msgspec
import pytest
from fakes.settings import OPERATOR_HEX
from test_admind_daemon import Harness

from heterodyne.admind import ingress
from heterodyne.marmot.control import InboundMessage


def message(
    h: Harness,
    *,
    sender: str = OPERATOR_HEX,
    mid: str = "12" * 32,
    text: str = "private fixture text",
    is_self: bool = False,
) -> InboundMessage:
    return msgspec.convert(h.fake.message_event(text, sender, mid, is_self=is_self), type=InboundMessage)


def counters(h: Harness) -> dict[str, int]:
    snapshot = h.daemon.ingress_snapshot().snapshot
    assert snapshot is not None
    return {counter.name: counter.value for counter in snapshot.counters}


def test_snapshot_needs_neither_store_nor_transport(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    class Forbidden:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError("snapshot must not access external resources")

    h.daemon.store = Forbidden()  # type: ignore[assignment]
    h.daemon.client = Forbidden()  # type: ignore[assignment]
    h.daemon.work.put_nowait(message(h))
    snapshot = h.daemon.ingress_snapshot().snapshot
    assert snapshot is not None
    assert snapshot.normal_depth == 1
    assert snapshot.custody_depth == 0
    assert snapshot.retained_bytes is None
    assert "durable_disposition_completion" in snapshot.unavailable
    assert "last_error_category" in snapshot.unavailable
    assert snapshot.upstream_delivery_verified is False
    assert all(counter.value == 0 for counter in snapshot.counters)


def test_actual_worker_waits_for_original_membership_lock(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        daemon.on_message = AsyncMock()
        async with daemon._ingress_lock("work", "membership"):
            daemon.work.put_nowait(message(h))
            task = asyncio.create_task(daemon._ingress_loop("normal", daemon.worker_loop))
            daemon.ingress.task("normal", task)
            try:
                async with asyncio.timeout(2):
                    while not daemon.work.empty():
                        await asyncio.sleep(0)
                snapshot = daemon.ingress_snapshot().snapshot
                assert snapshot is not None
                normal = next(item for item in snapshot.tasks if item.name == "normal")
                assert normal.phase == "waiting_work_lock"
                assert normal.state == "running"
                assert next(item for item in snapshot.locks if item.name == "work").owner == "membership"
                daemon.on_message.assert_not_awaited()
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        snapshot = daemon.ingress_snapshot().snapshot
        assert snapshot is not None
        assert next(item for item in snapshot.locks if item.name == "work").owner == "none"
        assert next(item for item in snapshot.tasks if item.name == "normal").phase == "stopped"

    asyncio.run(scenario())


def test_uninstrumented_holder_is_unknown_and_holder_cancellation_releases(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = Harness(tmp_path).daemon
        await daemon.work_lock.acquire()
        snapshot = daemon.ingress_snapshot().snapshot
        assert snapshot is not None
        assert next(item for item in snapshot.locks if item.name == "work").owner == "unknown"
        daemon.work_lock.release()
        entered = asyncio.Event()

        async def holder() -> None:
            async with daemon._ingress_lock("work", "recovery"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(holder())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not daemon.work_lock.locked()
        snapshot = daemon.ingress_snapshot().snapshot
        assert snapshot is not None
        assert next(item for item in snapshot.locks if item.name == "work").owner == "none"

    asyncio.run(scenario())


def test_guard_claim_and_replay_counters_follow_actual_store(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        daemon.authorise({OPERATOR_HEX})
        daemon.refresh_authority = Mock(return_value=True)
        daemon.latched = Mock(return_value=False)
        daemon.check_group = AsyncMock(return_value=False)
        await daemon.on_message(message(h, is_self=True))
        await daemon.on_message(message(h, sender="ab" * 32))
        await daemon.on_message(message(h, mid="malformed"))
        event = message(h)
        await daemon.on_message(event)
        await daemon.on_message(event)
        count = counters(h)
        assert count["dropped_self"] == 1
        assert count["dropped_guard"] == 1
        assert count["authenticated_normal"] == 3
        assert count["dropped_malformed"] == 1
        assert count["claimed"] == 1
        assert count["dropped_replay"] == 1
        assert count["completed_normal"] == count["completed_custody"] == 0
        assert h.store.db.execute("SELECT count(*) FROM inbound").fetchone()[0] == 1

    asyncio.run(scenario())


def test_custody_capacity_does_not_count_as_normal_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        daemon.authorise({OPERATOR_HEX})
        daemon.refresh_authority = Mock(return_value=True)
        command = message(h, text="!custody settle " + "a" * 24)
        for _ in range(daemon.custody_work.maxsize):
            daemon.on_event(command)
        daemon.on_event(command)
        count = counters(h)
        assert count["authenticated_custody"] == daemon.custody_work.maxsize + 1
        assert count["enqueued_custody"] == daemon.custody_work.maxsize
        assert count["dropped_capacity"] == 1
        assert count["enqueued_normal"] == count["claimed"] == 0
        assert daemon.work.empty()

    asyncio.run(scenario())


def test_reader_ack_failure_backoff_is_not_reading(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        daemon.check_group = AsyncMock(return_value=False)
        daemon.latched = Mock(return_value=False)
        sleeping = asyncio.Event()

        async def pause(delay: float) -> None:
            assert delay == 1.0
            sleeping.set()
            await asyncio.Event().wait()

        async def subscribe(*args: object, **kwargs: object) -> AsyncIterator[object]:
            await daemon.confirm_observing()
            yield object()

        daemon.client.subscribe = subscribe  # type: ignore[method-assign]
        daemon._sleep = pause
        task = asyncio.create_task(daemon._ingress_loop("reader", daemon.inbound_loop))
        daemon.ingress.task("reader", task)
        try:
            await asyncio.wait_for(sleeping.wait(), 2)
            snapshot = daemon.ingress_snapshot().snapshot
            assert snapshot is not None
            assert snapshot.acked is snapshot.reading is snapshot.observing is False
            assert next(item for item in snapshot.tasks if item.name == "reader").phase == "backoff"
            assert counters(h)["reconnects"] == 1
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_worker_handler_failure_remains_visible_without_false_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        daemon.on_message = AsyncMock(side_effect=ValueError("private fixture payload"))
        daemon.work.put_nowait(message(h))
        task = asyncio.create_task(daemon._ingress_loop("normal", daemon.worker_loop))
        try:
            async with asyncio.timeout(2):
                while counters(h)["handler_failed"] == 0:
                    await asyncio.sleep(0)
            assert counters(h)["completed_normal"] == 0
            assert not daemon.work_lock.locked()
            snapshot = daemon.ingress_snapshot().snapshot
            assert snapshot is not None
            assert "private fixture payload" not in msgspec.json.encode(snapshot).decode()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_run_serves_status_and_closes_socket_on_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        daemon.check_group = AsyncMock(return_value=False)
        daemon.start_agent = AsyncMock()

        async def idle() -> None:
            await asyncio.Event().wait()

        # Keep real run/socket/task lifecycle while prohibiting upstream and agent work.
        for name in (
            "inbound_loop",
            "hook_loop",
            "outbox_loop",
            "alerts_loop",
            "group_loop",
            "agent_loop",
            "summary_loop",
            "batch_loop",
            "autobump_loop",
            "maintenance_retry_loop",
        ):
            setattr(daemon, name, idle)
        task = asyncio.create_task(daemon.run())
        socket = h.settings.state_dir / ingress.SOCKET
        try:
            async with asyncio.timeout(3):
                while not socket.exists():
                    if task.done():
                        await task
                    await asyncio.sleep(0.001)
            reply = await ingress.request(socket)
            assert reply.result == "ok"
            assert reply.snapshot is not None
            assert any(item.name == "normal" and item.state == "running" for item in reply.snapshot.tasks)
            assert reply.snapshot.upstream_delivery_verified is False
            daemon.start_agent.assert_awaited_once_with(startup=True)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not socket.exists()

    asyncio.run(scenario())


def test_recovery_task_reports_running_while_original_work_lock_is_held(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = Harness(tmp_path).daemon
        entered = asyncio.Event()

        async def reconcile(*args: object, **kwargs: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        daemon.reconcile_quietly = reconcile  # type: ignore[method-assign]
        task = asyncio.create_task(
            daemon._ingress_loop("recovery", lambda: daemon.reconcile_asks([("a", "b", "c")]))
        )  # type: ignore[arg-type]
        daemon.ingress.task("recovery", task)
        try:
            await asyncio.wait_for(entered.wait(), 2)
            value = daemon.ingress_snapshot().snapshot
            assert value is not None
            recovery = next(t for t in value.tasks if t.name == "recovery")
            assert (recovery.state, recovery.phase) == ("running", "handling")
            assert next(lock for lock in value.locks if lock.name == "work").owner == "recovery"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not daemon.work_lock.locked()

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [OSError("private failure"), ValueError("private failure")])
def test_custody_failures_count_once_at_caught_or_escaping_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    from heterodyne.admind import gateway_custody_control

    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        monkeypatch.setattr(gateway_custody_control, "worker", AsyncMock(side_effect=error))
        daemon.custody_work.put_nowait(message(h))
        task = asyncio.create_task(daemon._ingress_loop("custody", daemon.custody_loop))
        try:
            async with asyncio.timeout(2):
                while counters(h)["handler_failed"] == 0:
                    if task.done():
                        await task
                    await asyncio.sleep(0)
            assert counters(h)["handler_failed"] == 1
            assert counters(h)["completed_custody"] == 0
            assert daemon.custody_work.empty()
            assert not daemon.custody_lock.locked()
            assert "private failure" not in msgspec.json.encode(daemon.ingress_snapshot()).decode()
        finally:
            if not task.done():
                task.cancel()
            with pytest.raises((asyncio.CancelledError, ValueError)):
                await task

    asyncio.run(scenario())


def test_reader_per_event_failure_counts_once_and_continues(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        seen = asyncio.Event()

        async def subscribe(*args: object, **kwargs: object) -> AsyncIterator[object]:
            yield object()
            seen.set()
            await asyncio.Event().wait()

        daemon.client.subscribe = subscribe  # type: ignore[method-assign]
        daemon.on_event = Mock(side_effect=ValueError("private event"))
        task = asyncio.create_task(daemon._ingress_loop("reader", daemon.inbound_loop))
        try:
            await asyncio.wait_for(seen.wait(), 2)
            assert counters(h)["handler_failed"] == 1
            assert counters(h)["reconnects"] == 0
            assert not task.done()
            assert "private event" not in msgspec.json.encode(daemon.ingress_snapshot()).decode()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_custody_worker_waits_on_distinct_control_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heterodyne.admind import ctl, gateway_custody, gateway_custody_control

    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        entered = asyncio.Event()

        async def control_work(*args: object) -> object:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(gateway_custody_control, "worker", control_work)
        control = asyncio.create_task(
            daemon.on_gateway_custody(
                gateway_custody.GatewayCustodyStatus("a" * 64), ctl.Peer(None, os.getuid())
            )
        )
        worker = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            daemon.custody_work.put_nowait(message(h))
            worker = asyncio.create_task(daemon._ingress_loop("custody", daemon.custody_loop))
            daemon.ingress.task("custody", worker)
            async with asyncio.timeout(2):
                while not daemon.custody_work.empty():
                    await asyncio.sleep(0)
            value = daemon.ingress_snapshot().snapshot
            assert value is not None
            assert next(t for t in value.tasks if t.name == "custody").phase == "waiting_custody_lock"
            assert next(lock for lock in value.locks if lock.name == "custody").owner == "custody_control"
            assert next(lock for lock in value.locks if lock.name == "work").owner == "none"
        finally:
            for task in (control, worker):
                if task is not None:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
        assert not daemon.custody_lock.locked()

    asyncio.run(scenario())


def test_autobump_records_distinct_holder_at_actual_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        daemon = Harness(tmp_path).daemon
        daemon.autobump_seconds = 0
        observed = []

        def bump() -> None:
            value = daemon.ingress_snapshot().snapshot
            assert value is not None
            observed.append(next(lock for lock in value.locks if lock.name == "work").owner)
            raise ValueError("stop fixture loop")

        monkeypatch.setattr(daemon, "autobump", bump)
        with pytest.raises(ValueError, match="stop fixture loop"):
            await daemon.autobump_loop()
        assert observed == ["autobump"]
        assert not daemon.work_lock.locked()

    asyncio.run(scenario())


@pytest.mark.parametrize("symlink", [False, True])
def test_diagnostic_bind_refusal_does_not_stop_daemon_or_unlink_foreign_leaf(
    tmp_path: Path, symlink: bool
) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        daemon = h.daemon
        daemon.check_group = AsyncMock(return_value=False)
        daemon.start_agent = AsyncMock()

        async def idle() -> None:
            await asyncio.Event().wait()

        for name in (
            "inbound_loop",
            "hook_loop",
            "outbox_loop",
            "alerts_loop",
            "group_loop",
            "agent_loop",
            "summary_loop",
            "batch_loop",
            "autobump_loop",
            "maintenance_retry_loop",
        ):
            setattr(daemon, name, idle)
        h.settings.state_dir.mkdir(parents=True, exist_ok=True)
        target = h.settings.state_dir / "foreign"
        target.write_bytes(b"private fixture")
        path = h.settings.state_dir / ingress.SOCKET
        if symlink:
            path.symlink_to(target)
        else:
            path.write_bytes(b"foreign leaf")
        before = path.lstat()
        task = asyncio.create_task(daemon.run())
        try:
            async with asyncio.timeout(3):
                while not daemon.start_agent.await_count:
                    if task.done():
                        await task
                    await asyncio.sleep(0.001)
            assert not task.done()
            assert (h.settings.state_dir / "ctl.sock").exists()
            assert daemon.ingress_available is False
            assert path.lstat().st_ino == before.st_ino
            assert target.read_bytes() == b"private fixture"
            audit = daemon.audit.path.read_text()
            rows = [json.loads(line) for line in audit.splitlines()]
            assert any(
                row.get("kind") == "ingress"
                and row.get("reason") == "bind"
                and row.get("action") == "unavailable"
                for row in rows
            )
            assert "foreign leaf" not in audit and "private fixture" not in audit
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert path.lstat().st_ino == before.st_ino

    asyncio.run(scenario())
