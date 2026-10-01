"""Task 9 review round 11: admind owns the hook socket's accept. The listening socket is non-blocking and
registered with `loop.add_reader`; the reader callback accepts, counts and schedules each connection with
no yield in between, so an accepted connection is never invisible to the dispatch gate (asyncio's own
accept path leaves a gap between accept() and connection_made). Real sockets and the real event loop;
fakes and tmp_path only.
"""

import asyncio
import errno
import socket
from pathlib import Path

from admind_waits import stays, wait_until
from test_admind_r1 import Unit, run
from test_admind_t9r5 import arm, frame, serving

from heterodyne.admind.hook import HookServer


async def until_accepted(server: HookServer) -> None:
    """Run the loop only until the connection has left the listening backlog (one `sleep(0)` per turn)."""
    for _ in range(1000):
        if not server.backlog_waiting():
            return
        await asyncio.sleep(0)
    raise AssertionError("the connection was never accepted")


def connect(path: str) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(path)        # completes in the kernel; the loop has not run
    client.sendall(frame("Stop", last_assistant_message="x"))
    return client


def test_the_connection_is_counted_before_any_other_task_runs_after_accept(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        arm(u)
        async with serving(u) as (server, path):
            client = connect(path)
            try:
                assert server.pending_hooks == 0
                await until_accepted(server)
                # no await since the accept: this is the first code to run after the reader callback
                assert server.pending_hooks == 1 and server.accepted == 1
            finally:
                client.close()
            await wait_until(lambda: server.pending_hooks == 0)
    run(scenario())


def test_a_dispatcher_scheduled_right_after_the_accept_defers(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        arm(u)
        async with serving(u) as (server, path):
            client = connect(path)
            try:
                b = "b" * 64
                u.daemon.held.append((b, "job B"))
                await until_accepted(server)
                dispatcher = asyncio.create_task(u.daemon.flush())      # ready in the same loop iteration
                await dispatcher
                assert u.tmux.pasted == [] and u.daemon.held == [(b, "job B")]
                await wait_until(lambda: u.tmux.pasted == ["job B"])    # the idle edge flushed it later
            finally:
                client.close()
    run(scenario())


def test_an_accept_error_backs_off_without_spinning_and_keeps_the_gate_closed(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        arm(u)
        async with serving(u) as (server, path):
            real = server._accept_conn          # pyright: ignore[reportPrivateUsage]
            calls = [0]

            def failing() -> socket.socket:
                calls[0] += 1
                if calls[0] <= 3:
                    raise OSError(errno.EMFILE, "too many open files")
                return real()
            server._accept_conn = failing       # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
            client = connect(path)
            try:
                await wait_until(lambda: calls[0] >= 1)
                n = calls[0]
                u.daemon.held.append(("b" * 64, "job B"))
                await u.daemon.flush()
                assert u.tmux.pasted == [] and server.backlog_waiting()     # a waiting connection gates it
                await asyncio.sleep(0.02)
                assert calls[0] <= n + 1                                    # not spinning
                await wait_until(lambda: u.tmux.pasted == ["job B"], timeout=10)    # recovered
                assert calls[0] >= 4 and "accept-error" in u.audit_text()
            finally:
                client.close()
    run(scenario())


def test_close_releases_accepted_connections_and_removes_the_socket(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        arm(u)
        path = u.settings.state_dir / "hook.sock"
        async with serving(u) as (server, _):
            idle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            idle.connect(str(path))                 # sends nothing: its frame is awaited
            try:
                await wait_until(lambda: server.pending_hooks == 1)
                await stays(lambda: server.pending_hooks == 1)
                await server.close()
                assert server.pending_hooks == 0 and not path.exists()
                assert not server.backlog_waiting()
            finally:
                idle.close()
    run(scenario())


def test_close_releases_a_connection_accepted_but_not_yet_started(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        arm(u)
        async with serving(u) as (server, path):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(path)
            try:
                await until_accepted(server)
                assert server.pending_hooks == 1
                await server.close()                # the handler task never ran
                assert server.pending_hooks == 0
            finally:
                client.close()
    run(scenario())
