"""Task 9 review round 10: (1) a live pane adopted at startup is held (a terminal prompt accepted by the
previous process and never applied cannot be told from a running turn) until a current Stop, a successful
!interrupt or !new; (2) the hook acceptance gate starts in the acceptance callback itself and the
dispatcher refuses to paste while a connection waits in the listening backlog; (3) a foreign-session or
unknown event is marked a no-op before its audit. Fakes, tmp_path and bounded waits only; no real
wn-agent, claude, systemctl, network or ~/.claude.
"""

import asyncio
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from admind_waits import stays, wait_until
from test_admind_r1 import Unit, run
from test_admind_t9r5 import NONCE, arm, frame, serving

from heterodyne.admind.daemon import ADOPT_HOLD_NOTICE
from heterodyne.admind.hook import HookEvent


def restart(u: Unit) -> None:
    """The daemon is rebuilt over the same SQLite file; the live pane is still there in the fake tmux."""
    u.build()
    u.daemon.recover()


# --- 1. startup adoption hold ---

def test_an_adopted_pane_is_held_until_interrupt_even_if_the_accepted_prompt_was_never_applied(
        tmp_path: Path) -> None:
    u = Unit(tmp_path)
    assert u.store.get("busy") is None      # the terminal prompt's busy change died with the process

    async def scenario() -> None:
        restart(u)
        await u.daemon.start_agent(startup=True)
        assert u.daemon.is_ready() and u.daemon.dispatch_blocked and u.store.get("busy") is not None
        assert u.texts().count(ADOPT_HOLD_NOTICE) == 1
        b = await u.say("job B")
        assert u.tmux.pasted == [] and u.daemon.held == [(b, "job B")]
        await u.daemon.flush()
        assert u.tmux.pasted == []
        await u.say("!interrupt")
        assert u.tmux.keys == ["Escape"] and u.tmux.pasted == ["job B"]     # released, then dispatched
    run(scenario())


def test_an_adopted_hold_survives_a_second_restart_and_a_current_stop_releases_it(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        restart(u)
        await u.daemon.start_agent(startup=True)
        restart(u)                                  # killed again before anything released it
        assert u.store.get("busy") is not None
        await u.daemon.start_agent(startup=True)
        await u.say("job B")
        assert u.tmux.pasted == []
        arm(u)
        await u.daemon.on_hook(HookEvent("Stop", "S1", launch=NONCE, last_assistant_message="done"), 1,
                               validate=True)
        await u.daemon.flush()
        assert u.tmux.pasted == ["job B"]
    run(scenario())


def test_a_fresh_launch_is_not_held_by_the_adoption_rule(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.tmux.sessions.clear()                         # no pane: startup launches one

    async def scenario() -> None:
        restart(u)
        await u.daemon.start_agent(startup=True)
        assert not u.daemon.dispatch_blocked and u.store.get("busy") is None
        assert ADOPT_HOLD_NOTICE not in u.texts()
        ev = HookEvent("SessionStart", u.agent.session_id or "", source="startup",
                       launch=u.store.get("launch_nonce"))
        await u.daemon.on_hook(ev)
        await u.say("job B")
        assert u.tmux.pasted == ["job B"]
    run(scenario())


def test_new_releases_an_adopted_hold(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        restart(u)
        await u.daemon.start_agent(startup=True)
        await u.say("!new")
        assert not u.daemon.dispatch_blocked and u.store.get("busy") is None
    run(scenario())


# --- 2. the acceptance gate ---

class StubTransport(asyncio.Transport):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return default

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def write(self, data: object) -> None:
        pass


def test_the_acceptance_callback_counts_the_connection_before_a_scheduled_dispatcher_runs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    captured: list[Callable[..., Any]] = []
    real = asyncio.start_unix_server

    async def capture(cb: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        captured.append(cb)
        return await real(cb, *args, **kwargs)
    monkeypatch.setattr(asyncio, "start_unix_server", capture)

    async def scenario() -> None:
        arm(u)
        async with serving(u) as (server, _):
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(loop=loop)
            protocol = asyncio.StreamReaderProtocol(reader, captured[0], loop=loop)
            u.daemon.held.append(("m" * 64, "job B"))
            dispatcher = asyncio.create_task(u.daemon.flush())      # already scheduled, not yet run
            protocol.connection_made(StubTransport())               # the real acceptance callback
            try:
                assert server.pending_hooks == 1 and server.accepted == 1   # counted synchronously
                await dispatcher
                assert u.tmux.pasted == [] and u.daemon.held       # the connection gates it
            finally:
                protocol.eof_received()
                protocol.connection_lost(None)
                await wait_until(lambda: server.pending_hooks == 0)
    run(scenario())


def test_the_dispatcher_defers_while_a_connection_waits_in_the_listening_backlog(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        arm(u)
        async with serving(u) as (server, path):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(path)        # completes in the kernel; the event loop has not accepted it
                client.sendall(frame("Stop", last_assistant_message="x"))
                assert server.pending_hooks == 0
                b = "b" * 64
                u.daemon.held.append((b, "job B"))
                await u.daemon.flush()      # no await point was reached since the connect
                assert u.tmux.pasted == [] and u.daemon.held == [(b, "job B")]
                await wait_until(lambda: u.tmux.pasted == ["job B"])    # the accept path flushed later
            finally:
                client.close()
    run(scenario())


# --- 3. no-op ordering ---

def test_a_failing_audit_with_a_foreign_session_or_unknown_event_holds_nothing(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)
    real = u.audit.write

    def write(kind: str, **fields: object) -> None:
        if fields.get("action", "").startswith("ignored-"):    # type: ignore[union-attr]
            raise OSError("audit filesystem unavailable")
        real(kind, **fields)
    u.audit.write = write       # type: ignore[method-assign]

    async def scenario() -> None:
        async with serving(u) as (server, path):
            for body in (frame("UserPromptSubmit", prompt="x").replace(b'"S1"', b'"OTHER"'),
                         frame("NoSuchEvent")):
                reader, writer = await asyncio.open_unix_connection(path)
                writer.write(body)
                await writer.drain()
                await asyncio.wait_for(reader.readline(), 10)
                writer.close()
            await wait_until(lambda: server.pending_hooks == 0)
            await stays(lambda: not u.daemon.dispatch_blocked and u.store.get("busy") is None)
            await u.say("job B")
            assert u.tmux.pasted == ["job B"]
    run(scenario())
