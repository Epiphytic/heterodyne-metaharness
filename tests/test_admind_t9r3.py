"""Task 9 review round 3, code fixes still in force after round 4 replaced the sequence counter: a
relaunch invalidates the departing launch before it yields, a stale (old launch) Stop never uses the
transcript fallback, and malformed launch values are dropped per event. Fakes, tmp_path and explicit
barriers only (threading events, futures, queues); no sleeps, no real wn-agent, claude, systemctl,
network or ~/.claude.
"""

import asyncio
import contextlib
import json
import threading
from pathlib import Path
from typing import Any

from test_admind_r1 import Unit, run
from test_admind_t9r2 import fire, hook_args

from heterodyne.admind.hook import HookEvent, HookServer, hook_main

LONG_AGO = "0" * 32


async def invoke(args: list[str], event: str, **extra: object) -> None:
    payload = json.dumps({"hook_event_name": event, "session_id": "S1", **extra}).encode()
    assert await asyncio.to_thread(hook_main, args, payload) == 0




def instrument(u: Unit) -> tuple[asyncio.Event, asyncio.Event]:
    """(flushed, reached): `reached` is set when hook_loop starts its flush (the event is handled);
    `flushed` when that flush completes."""
    flushed, reached = asyncio.Event(), asyncio.Event()
    real_flush = u.daemon.flush

    async def flush() -> None:
        reached.set()
        await real_flush()
        flushed.set()
    u.daemon.flush = flush                              # type: ignore[method-assign]
    return flushed, reached


@contextlib.asynccontextmanager
async def serving(u: Unit):                             # type: ignore[no-untyped-def]
    server = HookServer(u.settings.state_dir / "hook.sock", u.daemon.hooks, u.audit)
    await server.start()
    loop_task = asyncio.create_task(u.daemon.hook_loop())
    try:
        yield loop_task
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await loop_task
        await server.close()


# --- 3. a Stop from an old launch ---

def test_a_stop_from_an_old_launch_without_text_does_not_use_the_transcript_fallback(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed, _ = instrument(u)
    transcript = tmp_path / "S1.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {
        "content": [{"type": "text", "text": "the replacement's text"}]}}) + "\n")

    async def scenario() -> None:
        async with serving(u):
            await u.daemon.start_agent(relaunch=True)
            args = hook_args(u.agent.settings_file)
            await fire(args, "SessionStart", flushed, source="startup")
            a = await u.say("job")
            await fire(args, "UserPromptSubmit", flushed, prompt="job")
            old = [*args[:args.index("--launch") + 1], LONG_AGO, *args[args.index("--launch") + 2:]]
            await fire(old, "Stop", flushed, transcript_path=str(transcript))
            assert u.store.get("anchor") == a and u.store.get("busy") is not None
            assert "the replacement's text" not in u.texts()
            assert "stale" in u.audit_text()
    run(scenario())


# --- 2. relaunch readiness ---

class Gate:
    """Blocks a fake tmux call (in the supervision thread) until released; `entered` is an asyncio event."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.entered = asyncio.Event()
        self.release = threading.Event()

    def wait(self) -> None:
        self.loop.call_soon_threadsafe(self.entered.set)
        assert self.release.wait(10)


def relaunch_rig(u: Unit, gate_on: str, loop: asyncio.AbstractEventLoop,
                 armed: list[bool]) -> tuple[Gate, set[str]]:
    gate = Gate(loop)
    dead: set[str] = set()
    u.tmux.pane_dead = lambda name: name in dead        # type: ignore[method-assign]
    real = getattr(u.tmux, gate_on)
    kill, new_session = u.tmux.kill, u.tmux.new_session

    def wrapped(*a: Any, **kw: Any) -> None:
        if armed[0]:
            armed[0] = False
            gate.wait()
        real(*a, **kw)

    def kill_and_revive(name: str) -> None:
        dead.discard(name)
        if gate_on == "kill":
            wrapped(name)
        else:
            kill(name)

    def new_session_gated(name: str, cwd: Path, argv: list[str], **env: Any) -> None:
        if gate_on == "new_session":
            wrapped(name, cwd, argv, **env)
        else:
            new_session(name, cwd, argv, **env)
    u.tmux.kill = kill_and_revive                       # type: ignore[method-assign]
    u.tmux.new_session = new_session_gated              # type: ignore[method-assign]
    return gate, dead


def test_an_old_launchs_session_start_during_relaunch_does_not_ready_the_replacement(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed, reached = instrument(u)
    now = [100.0]
    u.daemon.clock = lambda: now[0]

    async def scenario() -> None:
        armed = [False]
        gate, dead = relaunch_rig(u, "kill", asyncio.get_running_loop(), armed)
        async with serving(u):
            await u.daemon.start_agent(relaunch=True)
            first = hook_args(u.agent.settings_file)
            await fire(first, "SessionStart", flushed, source="startup")
            assert u.daemon.ready.is_set()
            dead.add("admin")                           # the pane died; supervision relaunches
            armed[0] = True
            check = asyncio.create_task(u.daemon.check_agent())
            await asyncio.wait_for(gate.entered.wait(), 10)     # ensure_running is killing the old pane
            # The OLD launch's hook, now: it waits for the turn-state lock the relaunch holds.
            old_hook = asyncio.create_task(invoke(first, "SessionStart", source="resume"))
            held_job = asyncio.create_task(u.say("held job"))       # queues behind the dispatch lock too
            gate.release.set()
            await asyncio.wait_for(check, 10)
            await asyncio.wait_for(old_hook, 10)
            await asyncio.wait_for(held_job, 10)
            assert "ignored-stale-launch" in u.audit_text()
            assert not u.daemon.ready.is_set()
            assert "held job" not in u.tmux.pasted
            # the readiness timeout still applies to the replacement
            now[0] += u.daemon.ready_timeout + 1
            await u.daemon.check_agent()
            assert "ready-timeout" in u.audit_text()
            second = hook_args(u.agent.settings_file)
            await fire(second, "SessionStart", flushed, source="resume")
            assert u.daemon.ready.is_set() and u.tmux.pasted[-1] == "held job"
    run(scenario())


def test_the_replacements_early_session_start_during_launch_is_accepted(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed, reached = instrument(u)

    async def scenario() -> None:
        armed = [False]
        gate, dead = relaunch_rig(u, "new_session", asyncio.get_running_loop(), armed)
        async with serving(u):
            await u.daemon.start_agent(relaunch=True)
            first = hook_args(u.agent.settings_file)
            await fire(first, "SessionStart", flushed, source="startup")
            dead.add("admin")
            armed[0] = True
            check = asyncio.create_task(u.daemon.check_agent())
            await asyncio.wait_for(gate.entered.wait(), 10)     # the replacement is being started
            second = hook_args(u.agent.settings_file)
            assert second != first
            # The replacement is already up and fires its SessionStart; it waits for the relaunch's lock.
            hook = asyncio.create_task(invoke(second, "SessionStart", source="resume"))
            gate.release.set()
            await asyncio.wait_for(check, 10)
            await asyncio.wait_for(hook, 10)
            assert u.daemon.ready.is_set()
    run(scenario())


# --- 4. malformed launch values ---

def test_a_malformed_launch_is_dropped_without_restarting_the_hook_loop(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed, _ = instrument(u)

    async def scenario() -> None:
        async with serving(u) as loop_task:
            await u.daemon.start_agent(relaunch=True)
            args = hook_args(u.agent.settings_file)
            await fire(args, "SessionStart", flushed, source="startup")
            sock = str(u.settings.state_dir / "hook.sock")
            for extra in ({"launch": "é"}, {"launch": "abc"}, {"launch": "A" * 32}, {"launch": 7}):
                frame = {"hook_event_name": "Stop", "session_id": "S1",
                         "last_assistant_message": "malformed", **extra}
                reader, writer = await asyncio.open_unix_connection(sock)
                writer.write(json.dumps(frame).encode() + b"\n")
                await writer.drain()
                assert await reader.read() == b""           # no ok: the frame was dropped
                writer.close()
            assert u.daemon.hooks.empty() and "malformed" not in u.texts()
            # an event that reaches the loop with a non-ASCII launch is contained too
            flushed.clear()
            await u.daemon.hooks.put(HookEvent("Stop", "S1", last_assistant_message="odd", launch="é"))
            await asyncio.wait_for(flushed.wait(), 10)
            assert not loop_task.done()
            await fire(args, "Stop", flushed, last_assistant_message="still alive")
            assert "still alive" in u.texts()
    run(scenario())
