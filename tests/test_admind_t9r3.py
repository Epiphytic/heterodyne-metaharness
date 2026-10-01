"""Task 9 review round 3, code fixes: a delayed Stop is tied to its own turn by a hook sequence number,
a relaunch invalidates the departing launch before it yields, a stale Stop never uses the transcript
fallback, and malformed launch or seq values are dropped per event. Fakes, tmp_path and explicit barriers
only (threading events, futures, queues); no sleeps, no real wn-agent, claude, systemctl, network or
~/.claude.
"""

import asyncio
import contextlib
import json
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import msgspec
from test_admind_r1 import Unit, run
from test_admind_t9r2 import fire, hook_args

from heterodyne.admind.hook import HookEvent, HookServer, hook_main

LONG_AGO = "0" * 32


class Network:
    """A socket that accepts a hook's frame, answers `ok` at once (so the hook returns) and keeps the event
    back: a delivery delayed in transit. `release` hands the held events to the daemon."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.held: list[HookEvent] = []
        self.raw: list[dict[str, Any]] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        self.raw.append(json.loads(line))
        self.held.append(msgspec.json.decode(line, type=HookEvent))
        writer.write(b"ok\n")
        await writer.drain()
        writer.close()

    async def release(self, queue: asyncio.Queue[HookEvent]) -> None:
        held, self.held = self.held, []
        for ev in held:
            await queue.put(ev)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


def via(args: list[str], sock: Path) -> list[str]:
    """The same hook command line, aimed at another socket."""
    out = list(args)
    out[out.index("--socket") + 1] = str(sock)
    return out


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


# --- 1 and 3. a delayed Stop from an older turn of the same launch ---

def delayed_stop_scenario(tmp_path: Path, stop_extra: dict[str, object],
                          transcript_text: str | None = None) -> tuple[Unit, str, str, str]:
    u = Unit(tmp_path)
    flushed, _reached = instrument(u)
    net = Network(tmp_path / "d.sock")
    out: dict[str, str] = {}

    async def scenario() -> None:
        await net.start()
        async with serving(u):
            try:
                await u.daemon.start_agent(relaunch=True)
                args = hook_args(u.agent.settings_file)
                await fire(args, "SessionStart", flushed, source="startup")
                a = await u.say("job A")
                await fire(args, "UserPromptSubmit", flushed, prompt="job A")
                assert u.store.get("anchor") == a
                # A's Stop hook runs now (Claude Code finished A) but its delivery is delayed.
                await invoke(via(args, tmp_path / "d.sock"), "Stop", **stop_extra)
                assert len(net.held) == 1
                await u.say("!interrupt")                       # the operator gives up on A
                b = await u.say("job B")
                assert u.tmux.pasted == ["job A", "job B"]
                await fire(args, "UserPromptSubmit", flushed, prompt="job B")
                assert u.store.get("anchor") == b and u.store.get("busy") is not None
                c = await u.say("job C")                        # held behind B
                assert u.daemon.held == [(c, "job C")]
                if transcript_text is not None:                 # B has produced text in the transcript
                    (tmp_path / "S1.jsonl").write_text(
                        json.dumps({"type": "user", "message": {"content": "job B"}}) + "\n"
                        + json.dumps({"type": "assistant", "message": {
                            "content": [{"type": "text", "text": transcript_text}]}}) + "\n")
                flushed.clear()
                await net.release(u.daemon.hooks)               # now A's Stop arrives
                await asyncio.wait_for(flushed.wait(), 10)
                out.update(a=a, b=b, c=c)
            finally:
                await net.close()
    run(scenario())
    return u, out["a"], out["b"], out["c"]


def test_a_delayed_stop_of_an_interrupted_turn_cannot_steal_the_newer_turn(tmp_path: Path) -> None:
    u, _a, b, c = delayed_stop_scenario(tmp_path, {"last_assistant_message": "old reply"})
    assert u.store.get("anchor") == b and u.store.get("in_flight") == b
    assert u.store.get("busy") is not None
    assert u.tmux.pasted == ["job A", "job B"]                  # C is not pasted while B runs
    assert u.daemon.held == [(c, "job C")]
    sent = [(t, r) for _k, t, r in u.outbox()]
    assert ("old reply", None) in sent and ("old reply", b) not in sent


def test_a_stale_stop_without_text_does_not_use_the_transcript_fallback(tmp_path: Path) -> None:
    transcript = tmp_path / "S1.jsonl"
    u, _a, b, c = delayed_stop_scenario(tmp_path, {"transcript_path": str(transcript)},
                                        transcript_text="B's text, not A's")
    assert "B's text, not A's" not in u.texts()
    assert u.store.get("anchor") == b and u.store.get("busy") is not None
    assert u.tmux.pasted == ["job A", "job B"] and u.daemon.held == [(c, "job C")]


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


def test_a_frame_without_a_sequence_number_is_stale(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed, _ = instrument(u)

    async def scenario() -> None:
        async with serving(u):
            await u.daemon.start_agent(relaunch=True)
            args = hook_args(u.agent.settings_file)
            await fire(args, "SessionStart", flushed, source="startup")
            a = await u.say("job")
            await fire(args, "UserPromptSubmit", flushed, prompt="job")
            seq_less = [x for pair in zip(args[::2], args[1::2], strict=False) if pair[0] != "--seq-file"
                        for x in pair]
            assert "--seq-file" not in seq_less and "--launch" in seq_less
            await fire(seq_less, "Stop", flushed, last_assistant_message="no seq")
            assert u.store.get("anchor") == a and u.store.get("busy") is not None
            assert ("no seq", None) in [(t, r) for _k, t, r in u.outbox()]
            await fire(seq_less, "UserPromptSubmit", flushed, prompt="intruder")
            await fire(seq_less, "SessionStart", flushed, source="clear")
            assert u.store.get("anchor") == a and u.store.get("in_flight") == a
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

    def wrapped(*a: Any) -> None:
        if armed[0]:
            armed[0] = False
            gate.wait()
        real(*a)

    def kill_and_revive(name: str) -> None:
        dead.discard(name)
        if gate_on == "kill":
            wrapped(name)
        else:
            kill(name)

    def new_session_gated(name: str, cwd: Path, argv: list[str]) -> None:
        if gate_on == "new_session":
            wrapped(name, cwd, argv)
        else:
            new_session(name, cwd, argv)
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
            reached.clear()
            await invoke(first, "SessionStart", source="resume")    # the OLD launch's hook, now
            await asyncio.wait_for(reached.wait(), 10)              # hook_loop has handled it
            held_job = asyncio.create_task(u.say("held job"))       # queues behind the dispatch lock
            gate.release.set()
            await asyncio.wait_for(check, 10)
            await asyncio.wait_for(held_job, 10)
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
            reached.clear()
            await invoke(second, "SessionStart", source="resume")   # the replacement is already up
            await asyncio.wait_for(reached.wait(), 10)
            gate.release.set()
            await asyncio.wait_for(check, 10)
            assert u.daemon.ready.is_set()
    run(scenario())


# --- 4. malformed launch and seq values ---

def test_a_malformed_launch_or_seq_is_dropped_without_restarting_the_hook_loop(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed, _ = instrument(u)

    async def scenario() -> None:
        async with serving(u) as loop_task:
            await u.daemon.start_agent(relaunch=True)
            args = hook_args(u.agent.settings_file)
            await fire(args, "SessionStart", flushed, source="startup")
            sock = str(u.settings.state_dir / "hook.sock")
            for extra in ({"launch": "é"}, {"launch": "abc"}, {"launch": "A" * 32}, {"seq": -1},
                          {"seq": "7"}, {"seq": 1.5}, {"seq": True}):
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


# --- 5. the hook's sequence number ---

class Capture:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.frames: list[dict[str, Any]] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.path))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.frames.append(json.loads(await reader.readline()))
        writer.write(b"ok\n")
        await writer.drain()
        writer.close()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


def hook_frames(tmp_path: Path, seq_file: Path, count: int = 1, extra: dict[str, object] | None = None,
                before: Callable[[], None] | None = None) -> list[dict[str, Any]]:
    cap = Capture(tmp_path / "c.sock")

    async def scenario() -> None:
        await cap.start()
        try:
            if before is not None:
                before()
            payload = json.dumps({"hook_event_name": "Stop", "session_id": "S1", **(extra or {})}).encode()
            for _ in range(count):
                args = ["--socket", str(cap.path), "--launch", LONG_AGO, "--seq-file", str(seq_file)]
                assert await asyncio.to_thread(hook_main, args, payload) == 0
        finally:
            await cap.close()
    run(scenario())
    return cap.frames


def test_each_hook_invocation_gets_a_strictly_increasing_private_sequence_number(tmp_path: Path) -> None:
    seq_file = tmp_path / "seq"
    frames = hook_frames(tmp_path, seq_file, 3, {"seq": 999})      # a payload's own seq is never trusted
    assert [f["seq"] for f in frames] == [1, 2, 3]
    assert (seq_file.stat().st_mode & 0o777) == 0o600


def test_concurrent_hooks_get_distinct_sequence_numbers(tmp_path: Path) -> None:
    from heterodyne.admind.hook import next_seq
    seq_file = tmp_path / "seq"
    with ThreadPoolExecutor(8) as pool:
        got = list(pool.map(lambda _i: next_seq(str(seq_file)), range(64)))
    assert sorted(g for g in got if g is not None) == list(range(1, 65))


def test_a_sequence_failure_still_sends_the_frame_without_seq(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_text("5")
    link = tmp_path / "link"
    link.symlink_to(target)
    for bad in (link, tmp_path / "missing-dir" / "seq", tmp_path):
        frames = hook_frames(tmp_path, bad, extra={"seq": 12})
        assert len(frames) == 1 and "seq" not in frames[0] and frames[0]["launch"] == LONG_AGO
    assert target.read_text() == "5"                    # the symlink was not followed
    corrupt = tmp_path / "corrupt"
    corrupt.write_text("not a number")
    frames = hook_frames(tmp_path, corrupt)
    assert "seq" not in frames[0] and corrupt.read_text() == "not a number"
    target.chmod(0o400)
    if os.geteuid() != 0:
        assert "seq" not in hook_frames(tmp_path, target)[0]
