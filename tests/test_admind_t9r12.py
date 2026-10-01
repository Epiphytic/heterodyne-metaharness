"""Task 9 review round 12: every accepted connection ends applied (its event routed, ack attempted) or
lost; a lost one applies the hold first, and every slot resolves only after its predecessor's. The accept
loop is bounded per reader callback. Failures in task creation and listener registration leave no
bookkeeping or descriptor behind. Real local sockets, fakes and tmp_path only; no sleeps.
"""

import asyncio
import os
import socket
from pathlib import Path
from typing import Any

import pytest
from admind_waits import stays, wait_until
from test_admind_r1 import Unit, run
from test_admind_t9r5 import frame

from heterodyne.admind.hook import ACCEPT_ABORTS, ACCEPT_BATCH, Delivery, HookEvent, HookServer


class Rig:
    """A bare HookServer whose queue is read by the test, counting `on_drop` holds."""

    def __init__(self, tmp_path: Path) -> None:
        self.u = Unit(tmp_path)
        self.queue: asyncio.Queue[HookEvent | Delivery] = asyncio.Queue()
        self.holds = 0
        self.path = tmp_path / "r12.sock"
        self.server = HookServer(self.path, self.queue, self.u.audit, on_drop=self.hold)

    def hold(self) -> None:
        self.holds += 1

    def client(self, data: bytes = b"") -> socket.socket:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(str(self.path))
        if data:
            c.sendall(data)
        return c


def stop_frame() -> bytes:
    return frame("Stop", last_assistant_message="x")


async def first_pending(rig: Rig) -> tuple[socket.socket, Delivery]:
    """Connection A: its event sits in the queue, unanswered, so its slot stays unresolved."""
    a = rig.client(stop_frame())
    item = await asyncio.wait_for(rig.queue.get(), 10)
    assert isinstance(item, Delivery)
    return a, item


def failing_open(first_ok: bool = True, exc: type[Exception] = OSError) -> Any:
    real = asyncio.open_unix_connection
    calls = [0]

    async def opener(*a: Any, **k: Any) -> Any:
        calls[0] += 1
        if first_ok and calls[0] == 1:
            return await real(*a, **k)
        raise exc("injected")
    return opener


@pytest.mark.parametrize("exc", [OSError, RuntimeError, MemoryError])
def test_setup_failure_holds_and_resolves_after_the_predecessor(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: type[Exception]) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> None:
        monkeypatch.setattr(asyncio, "open_unix_connection", failing_open(True, exc))
        await rig.server.start()
        try:
            a, delivery = await first_pending(rig)
            b = rig.client()
            await wait_until(lambda: rig.holds == 1)            # the hold is immediate and synchronous
            assert rig.server.pending_hooks == 2
            await stays(lambda: rig.server.pending_hooks == 2)  # B's slot waits for A, whatever happened to B
            delivery.done.set_result(True)
            await wait_until(lambda: rig.server.pending_hooks == 0)
            assert rig.holds == 1
            a.close()
            b.close()
        finally:
            await rig.server.close()
    run(scenario())


def test_routing_failure_after_a_read_event_holds_and_keeps_order(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> None:
        await rig.server.start()
        try:
            a, delivery = await first_pending(rig)
            async def broken(item: Any) -> None:
                raise RuntimeError("queue broke")
            rig.queue.put = broken      # type: ignore[method-assign]
            b = rig.client(stop_frame())
            await wait_until(lambda: rig.server.pending_hooks == 2)
            await stays(lambda: rig.holds == 0 and rig.server.pending_hooks == 2)   # B waits behind A
            delivery.done.set_result(True)      # B now reaches the routing step, which fails
            await wait_until(lambda: rig.server.pending_hooks == 0)
            assert rig.holds == 1
            a.close()
            b.close()
        finally:
            await rig.server.close()
    run(scenario())


def test_create_task_failure_cleans_up_holds_and_keeps_order(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> None:
        await rig.server.start()
        try:
            a, delivery = await first_pending(rig)
            loop = asyncio.get_running_loop()
            real = loop.create_task

            def failing(coro: Any, **kw: Any) -> Any:
                loop.create_task = real     # type: ignore[method-assign]
                raise RuntimeError("injected")
            loop.create_task = failing      # type: ignore[method-assign]
            b = rig.client()
            try:
                await wait_until(lambda: rig.holds == 1)
                assert rig.server.pending_hooks == 2
                await stays(lambda: rig.server.pending_hooks == 2)
                delivery.done.set_result(True)
                await wait_until(lambda: rig.server.pending_hooks == 0)
                b.settimeout(10)
                assert b.recv(1) == b""                 # its socket was closed
                assert rig.server._tail is not None and rig.server._tail.done()     # pyright: ignore[reportPrivateUsage]
            finally:
                loop.create_task = real     # type: ignore[method-assign]
                a.close()
                b.close()
        finally:
            await rig.server.close()
    run(scenario())


def fresh_pairs(keep: list[socket.socket]) -> Any:
    def accept() -> socket.socket:
        server_end, client_end = socket.socketpair()
        keep.append(client_end)
        return server_end
    return accept


def test_a_refilled_backlog_is_bounded_per_callback(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> None:
        await rig.server.start()
        keep: list[socket.socket] = []
        try:
            rig.server._accept_conn = fresh_pairs(keep)     # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
            rig.server._on_readable()                       # pyright: ignore[reportPrivateUsage]
            assert rig.server.accepted == ACCEPT_BATCH and len(keep) == ACCEPT_BATCH
            assert rig.server._reading                      # pyright: ignore[reportPrivateUsage]
            ran = asyncio.Event()
            asyncio.get_running_loop().call_soon(ran.set)
            await asyncio.wait_for(ran.wait(), 10)          # the loop got control back
            for c in keep:
                c.close()
            await wait_until(lambda: rig.server.pending_hooks == 0)
        finally:
            for c in keep:
                c.close()
            await rig.server.close()
    run(scenario())


def test_a_real_backlog_takes_several_loop_turns_and_lets_other_tasks_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("heterodyne.admind.hook.ACCEPT_BATCH", 8)     # the listen backlog is 100
    rig = Rig(tmp_path)

    async def scenario() -> None:
        await rig.server.start()
        clients: list[socket.socket] = []
        try:
            stop = asyncio.Event()
            ticks = [0]

            async def other() -> None:
                while not stop.is_set():
                    ticks[0] += 1
                    await asyncio.sleep(0)

            task = asyncio.create_task(other())
            for _ in range(40):
                clients.append(rig.client())
            await wait_until(lambda: rig.server.accepted == len(clients))
            assert ticks[0] >= 5        # 40 connections at 8 per callback need several turns
            stop.set()
            await task
        finally:
            for c in clients:
                c.close()
            await rig.server.close()
    run(scenario())


def test_persistent_aborts_back_off(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> None:
        await rig.server.start()
        try:
            calls = [0]

            def aborting() -> socket.socket:
                calls[0] += 1
                raise ConnectionAbortedError

            rig.server._accept_conn = aborting      # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
            rig.server._on_readable()               # pyright: ignore[reportPrivateUsage]
            assert calls[0] == ACCEPT_ABORTS + 1
            assert not rig.server._reading and rig.server._backoff is not None      # pyright: ignore[reportPrivateUsage]
            await wait_until(lambda: rig.server._reading)       # pyright: ignore[reportPrivateUsage]
            assert calls[0] == ACCEPT_ABORTS + 1
        finally:
            await rig.server.close()
    run(scenario())


def test_add_reader_failure_closes_the_listener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        seen: list[int] = []

        def failing(fd: int, *a: Any) -> None:
            seen.append(fd)
            raise OSError("watch limit")
        monkeypatch.setattr(loop, "add_reader", failing)
        with pytest.raises(OSError):
            await rig.server.start()
        assert rig.server._listen is None       # pyright: ignore[reportPrivateUsage]
        with pytest.raises(OSError):
            os.fstat(seen[0])                   # the descriptor is closed
        assert not rig.path.exists()
    run(scenario())


def test_run_closes_the_server_when_start_fails(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    events: list[str] = []

    class Stub:
        async def start(self) -> None:
            events.append("start")
            raise OSError("boom")

        async def close(self) -> None:
            events.append("close")

    async def scenario() -> None:
        u.daemon.make_server = lambda path: Stub()      # type: ignore[method-assign, assignment]

        async def no_check() -> None:
            return None
        u.daemon.check_group = no_check                 # type: ignore[method-assign]
        with pytest.raises(OSError):
            await u.daemon.run()
        assert events == ["start", "close"]
    run(scenario())


def test_a_failing_resume_retries_and_a_failing_idle_callback_does_not_stop_cleanup(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(tmp_path)
    idle_calls = [0]

    def bad_idle() -> None:
        idle_calls[0] += 1
        raise RuntimeError("idle broke")
    rig.server.on_idle = bad_idle

    async def scenario() -> None:
        await rig.server.start()
        loop = asyncio.get_running_loop()
        try:
            rig.server._stop_reading()      # pyright: ignore[reportPrivateUsage]
            real = loop.add_reader
            fails = [2]

            def flaky(fd: int, cb: Any, *a: Any) -> None:
                if fails[0] > 0:
                    fails[0] -= 1
                    raise OSError("watch limit")
                real(fd, cb, *a)
            monkeypatch.setattr(loop, "add_reader", flaky)
            rig.server._resume_reading()    # pyright: ignore[reportPrivateUsage]
            await wait_until(lambda: rig.server._reading)       # pyright: ignore[reportPrivateUsage]
            assert fails[0] == 0
            c = rig.client()                # still served
            await wait_until(lambda: rig.server.accepted == 1)
            c.close()
            await wait_until(lambda: rig.server.pending_hooks == 0)
            assert idle_calls[0] == 1
        finally:
            await rig.server.close()
    run(scenario())
