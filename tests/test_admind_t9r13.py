"""Task 9 review round 13: shutdown never pastes past an unapplied hook (the daemon's `shutting_down` flag,
and a cancelled delivery holds dispatch before it is resolved); a lost connection's ordered release
re-asserts its hold; abort back-off after exactly ACCEPT_ABORTS; accept bookkeeping never counts a
connection without a releasable record. Fakes, tmp_path and local sockets only; bounded waits.
"""

import asyncio
import contextlib
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
from admind_waits import lock_waiters, stays, wait_until
from test_admind_r1 import Unit, run
from test_admind_t9r3 import Gate
from test_admind_t9r5 import answer, arm, frame, send
from test_admind_t9r12 import Rig, first_pending, stop_frame

from heterodyne.admind.hook import ACCEPT_ABORTS


def test_sigterm_with_a_prompt_behind_interrupt_never_pastes_and_holds(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)
    sock = str(u.settings.state_dir / "hook.sock")

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        gate = Gate(loop)
        real = u.tmux.send_key

        def send_key(name: str, key: str) -> None:
            gate.wait()
            real(name, key)
        u.tmux.send_key = send_key                              # type: ignore[method-assign]
        daemon = asyncio.create_task(u.daemon.run())
        try:
            await wait_until(lambda: Path(sock).exists())
            r, w = await send(sock, frame("Stop", last_assistant_message="x"))     # leaves the agent idle
            assert await answer(r) == b"ok\n"
            w.close()
            await wait_until(lambda: u.daemon.pending_hooks() == 0)
            u.daemon.held.append((u.mid(), "held operator message"))     # held, not yet dispatched
            assert not u.daemon.dispatch_blocked
            interrupt = asyncio.create_task(u.say("!interrupt"))
            await asyncio.wait_for(gate.entered.wait(), 10)             # the lock is held
            r2, w2 = await send(sock, frame("UserPromptSubmit", prompt="terminal prompt"))
            server = u.daemon.hook_server
            assert server is not None
            await wait_until(lambda: any(c.queued and c.index == 2 for c in server._conns)    # pyright: ignore[reportPrivateUsage]
                             and u.daemon.hooks.empty())        # hook_loop took it ...
            await wait_until(lambda: lock_waiters(u.daemon.dispatch_lock) >= 1)     # ... and waits behind it
            daemon.cancel()                                             # SIGTERM equivalent: the command too
            interrupt.cancel()
            gate.release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(daemon, 10)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await interrupt
            w2.close()
            del r2
            assert u.tmux.pasted == []
            assert u.daemon.shutting_down and u.daemon.dispatch_blocked
            await stays(lambda: u.tmux.pasted == [])
        finally:
            gate.release.set()
            daemon.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await daemon
    run(scenario())


def test_no_flush_is_scheduled_or_run_while_shutting_down(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        u.daemon.held.append((u.mid(), "held"))
        u.daemon.shutting_down = True
        u.daemon.hooks_idle()
        assert not u.daemon._idle_tasks          # pyright: ignore[reportPrivateUsage]
        await u.daemon.flush()
        assert u.tmux.pasted == []
        u.daemon.shutting_down = False
        await u.daemon.flush()
        assert u.tmux.pasted == ["held"]
    run(scenario())


def test_lost_connection_hold_is_reasserted_at_ordered_release(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(tmp_path)
    blocked = [False]
    rig.server.on_drop = lambda: blocked.__setitem__(0, True)

    async def scenario() -> None:
        real = asyncio.open_unix_connection
        calls = [0]

        async def opener(*a: Any, **k: Any) -> Any:
            calls[0] += 1
            if calls[0] == 1:
                return await real(*a, **k)
            raise OSError("injected")
        monkeypatch.setattr(asyncio, "open_unix_connection", opener)
        await rig.server.start()
        try:
            a, delivery = await first_pending(rig)
            b = rig.client()
            await wait_until(lambda: blocked[0])
            blocked[0] = False              # predecessor's Stop lifts the hold (set_idle)
            delivery.done.set_result(True)
            await wait_until(lambda: rig.server.pending_hooks == 0)
            assert blocked[0]               # B's release re-asserted it before the count dropped
            a.close()
            b.close()
        finally:
            await rig.server.close()
    run(scenario())


def test_back_off_after_exactly_eight_aborts(tmp_path: Path) -> None:
    assert ACCEPT_ABORTS == 8
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
            assert calls[0] == 8 and rig.server._backoff is not None    # pyright: ignore[reportPrivateUsage]
        finally:
            await rig.server.close()
    run(scenario())


class FailingSet(set[Any]):
    def add(self, element: Any) -> None:
        raise MemoryError("injected")


@pytest.mark.parametrize("where", ["future", "conn", "set"])
def test_accept_bookkeeping_failure_never_strands_the_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            where: str) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        await rig.server.start()
        try:
            if where == "future":
                real = loop.create_future

                def boom() -> Any:
                    if sys._getframe(1).f_code.co_name == "_on_readable":     # only the accept's own call
                        raise MemoryError("injected")
                    return real()
                monkeypatch.setattr(loop, "create_future", boom)
            elif where == "conn":
                def bad(*a: Any, **k: Any) -> Any:
                    raise MemoryError("injected")
                monkeypatch.setattr("heterodyne.admind.hook._Conn", bad)
            else:
                rig.server._conns = FailingSet()    # pyright: ignore[reportPrivateUsage]
            c = rig.client(stop_frame())
            await wait_until(lambda: rig.holds >= 1)
            await wait_until(lambda: rig.server.accepted == 1)
            assert rig.server.pending_hooks == 0
            assert not rig.server._conns            # pyright: ignore[reportPrivateUsage]
            c.close()
        finally:
            monkeypatch.undo()
            await rig.server.close()
    run(scenario())
