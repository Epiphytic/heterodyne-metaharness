"""Private socket/transport fixtures only; no native or production endpoints."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent

from heterodyne.marmot.control import ControlClient, ControlError, Subscription, remaining


def test_shared_deadline_bounds_group_info_and_existing_call_still_works(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeWnAgent(tmp_path / "w.sock")
        await fake.start()
        client = ControlClient(fake.socket_path, "test-token", timeout=5)
        try:
            assert (await client.group_info(ACCOUNT, fake.group_id)).member_count == 2
            fake.info_gate = asyncio.Event()
            started = time.monotonic()
            with pytest.raises((ControlError, TimeoutError)):
                await client.group_info(ACCOUNT, fake.group_id, deadline=started + 0.04)
            assert time.monotonic() - started < 0.5
        finally:
            await fake.stop()

    asyncio.run(scenario())


def test_owned_subscription_closes_exact_writer_and_reader_without_canceling_parent(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeWnAgent(tmp_path / "w.sock")
        await fake.start()
        owner = Subscription()
        client = ControlClient(fake.socket_path, "test-token")

        async def consume() -> None:
            async for _ in client.subscribe(
                ACCOUNT, fake.group_id, owned=owner, deadline=time.monotonic() + 1
            ):
                pass

        task = asyncio.create_task(consume())
        try:
            await fake.wait_subscribed()
            assert owner.writer is not None
            original = owner.writer
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await owner.close(time.monotonic() + 0.1)
            assert owner.closed and owner.writer is original
            current = asyncio.current_task()
            assert current is not None
            assert not current.cancelling()
        finally:
            if not task.done():
                task.cancel()
            await fake.stop()

    asyncio.run(scenario())


def test_teardown_timeout_retains_same_handle_for_later_retry() -> None:
    async def scenario() -> None:
        owner = Subscription()
        gate = asyncio.Event()
        writer = Mock()
        writer.wait_closed = AsyncMock(side_effect=gate.wait)
        owner.attach(writer)
        assert not await owner.close(time.monotonic() + 0.01)
        pending = owner.closing
        assert pending is not None and not pending.done()
        assert owner.writer is writer and not owner.closed
        gate.set()
        assert await owner.close(time.monotonic() + 1)
        assert owner.closing is pending and owner.writer is writer
        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()

    asyncio.run(scenario())


def test_ack_callback_consumes_shared_deadline(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeWnAgent(tmp_path / "w.sock")
        await fake.start()
        owner = Subscription()

        async def stuck_ack() -> None:
            await asyncio.Event().wait()

        async def consume() -> None:
            async for _ in ControlClient(fake.socket_path, "test-token").subscribe(
                ACCOUNT, fake.group_id, stuck_ack, owned=owner, deadline=time.monotonic() + 0.03
            ):
                pass

        try:
            with pytest.raises(TimeoutError):
                await consume()
            assert owner.closed
        finally:
            await fake.stop()

    asyncio.run(scenario())


def test_remaining_never_extends_deadline() -> None:
    assert remaining(None, 17) == 17
    assert remaining(time.monotonic() - 1, 5) == 0
    value = remaining(time.monotonic() + 0.01, 30)
    assert value is not None and 0 <= value <= 0.01
