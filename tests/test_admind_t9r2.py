"""Task 9 review round 2, code fixes: group verification is bound to its subscription, and hook events
are bound to the launch that produced them. Fakes, tmp_path and explicit barriers only; no sleeps, no real
wn-agent, claude, systemctl, network or ~/.claude.
"""

import asyncio
import contextlib
import json
import re
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_admind_r1 import StubClient, Unit, run

from heterodyne.admind.hook import HookServer, hook_main
from heterodyne.config.secret_scan import show

# --- 1. group verification is bound to its subscription ---


class GatedClient(StubClient):
    """group_info calls past the first `free` ones wait on a future the test resolves (with a member
    count); subscriptions end when the test resolves their `drop` future."""

    def __init__(self) -> None:
        super().__init__()
        self.gates: list[asyncio.Future[int]] = []
        self.entered: asyncio.Queue[int] = asyncio.Queue()     # the index of each gated group_info call
        self.drops: list[asyncio.Future[None]] = []
        self.acks: asyncio.Queue[int] = asyncio.Queue()
        self.calls = 0

    async def group_info(self, account: str, group: str) -> Any:
        index = self.calls
        self.calls += 1
        if index < len(self.gates):
            self.entered.put_nowait(index)
            return SimpleNamespace(group_id_hex=group, member_count=await self.gates[index])
        return SimpleNamespace(group_id_hex=group, member_count=self.member_count)

    async def subscribe(self, account: str, group: str,
                        on_ack: Callable[[], Awaitable[None]] | None = None) -> AsyncIterator[Any]:
        drop: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.drops.append(drop)
        if on_ack is not None:
            await on_ack()
        self.acks.put_nowait(len(self.drops) - 1)
        await drop
        return
        yield      # pragma: no cover - makes this an async generator


def gated_unit(tmp_path: Path) -> tuple[Unit, GatedClient]:
    u = Unit(tmp_path)
    client = GatedClient()
    u.client = client       # type: ignore[assignment]
    u.build()
    u.daemon.group_ok = False
    u.daemon.observing = False

    async def no_wait(_delay: float) -> None:
        await asyncio.sleep(0)
    u.daemon._sleep = no_wait       # type: ignore[method-assign]
    return u, client


async def reach_stale_check(u: Unit, client: GatedClient, count_b: int) -> tuple[asyncio.Task[bool], Any]:
    """A is acknowledged and observing; a periodic check on A is in flight; A disconnects; B is
    acknowledged-pending with its own check in flight. Returns A's check and the futures to resolve."""
    loop = asyncio.get_running_loop()
    fut_a: asyncio.Future[int] = loop.create_future()
    fut_b: asyncio.Future[int] = loop.create_future()
    client.gates[:] = [loop.create_future(), fut_a, fut_b]
    client.gates[0].set_result(2)                   # A's own acknowledgement check passes at once
    task = asyncio.create_task(u.daemon.inbound_loop())
    u.cleanup = task        # type: ignore[attr-defined]
    assert await client.entered.get() == 0
    assert await client.acks.get() == 0 and u.daemon.observing
    check_a = asyncio.create_task(u.daemon.check_group())
    assert await client.entered.get() == 1          # A's slow periodic check is waiting
    client.drops[0].set_result(None)                # A disconnects
    assert await client.entered.get() == 2          # B was acknowledged and began its own check
    assert not u.daemon.observing
    fut_a.set_result(2)                             # A's delayed answer: two members
    await check_a
    return check_a, fut_b


async def stop(u: Unit) -> None:
    task: asyncio.Task[None] = u.cleanup    # type: ignore[attr-defined]
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_a_stale_group_check_cannot_enable_posting_on_an_unverified_replacement(tmp_path: Path) -> None:
    u, client = gated_unit(tmp_path)

    async def scenario() -> None:
        try:
            _check_a, fut_b = await reach_stale_check(u, client, 2)
            assert not u.daemon.observing and not u.daemon.may_post()
            u.daemon.post("k", "private reply", None)
            await u.daemon.outbox_pass()
            assert client.sent == []                # nothing before B's own check passes
            fut_b.set_result(2)
            await client.acks.get()
            assert u.daemon.observing and u.daemon.may_post()
            await u.daemon.outbox_pass()
            assert [s.text for s in client.sent] == ["private reply"]
        finally:
            await stop(u)
    run(scenario())


def test_the_replacement_check_latches_with_nothing_posted_when_it_finds_three(tmp_path: Path) -> None:
    u, client = gated_unit(tmp_path)

    async def scenario() -> None:
        try:
            _check_a, fut_b = await reach_stale_check(u, client, 3)
            u.daemon.post("k", "private reply", None)
            await u.daemon.outbox_pass()
            assert client.sent == []
            fut_b.set_result(3)
            await client.acks.get()
            assert u.daemon.latched() and not u.daemon.observing
            await u.daemon.outbox_pass()
            assert client.sent == []
        finally:
            await stop(u)
    run(scenario())


def test_a_stale_check_that_finds_three_members_still_latches(tmp_path: Path) -> None:
    u, client = gated_unit(tmp_path)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        fut_a: asyncio.Future[int] = loop.create_future()
        fut_b: asyncio.Future[int] = loop.create_future()
        client.gates[:] = [loop.create_future(), fut_a, fut_b]
        client.gates[0].set_result(2)
        task = asyncio.create_task(u.daemon.inbound_loop())
        u.cleanup = task        # type: ignore[attr-defined]
        try:
            await client.entered.get()
            await client.acks.get()
            check_a = asyncio.create_task(u.daemon.check_group())
            await client.entered.get()
            client.drops[0].set_result(None)
            await client.entered.get()
            fut_a.set_result(3)
            await check_a
            assert u.daemon.latched()               # an old answer can still fail closed
            fut_b.set_result(2)
            await client.acks.get()
            assert not u.daemon.may_post()
        finally:
            await stop(u)
    run(scenario())


# --- 2. hook events are bound to the launch that produced them ---

def hook_args(settings_file: Path) -> list[str]:
    command = json.loads(settings_file.read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    argv = shlex.split(command)
    return argv[argv.index("hook") + 1:]


async def fire(args: list[str], event: str, flushed: asyncio.Event, **extra: object) -> None:
    flushed.clear()
    payload = json.dumps({"hook_event_name": event, "session_id": "S1", **extra}).encode()
    assert await asyncio.to_thread(hook_main, args, payload) == 0
    await asyncio.wait_for(flushed.wait(), 10)      # hook_loop flushes once it has handled the event


def test_a_delayed_stop_from_a_crashed_launch_cannot_hijack_the_resumed_turn(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    dead: set[str] = set()
    u.tmux.pane_dead = lambda name: name in dead        # type: ignore[method-assign]
    original = u.tmux.new_session

    def relaunch(name: str, cwd: Path, argv: list[str], **env: Any) -> None:
        dead.discard(name)
        original(name, cwd, argv, **env)
    u.tmux.new_session = relaunch                       # type: ignore[method-assign]
    flushed = asyncio.Event()
    real_flush = u.daemon.flush

    async def flush() -> None:
        await real_flush()
        flushed.set()
    u.daemon.flush = flush                              # type: ignore[method-assign]

    async def scenario() -> None:
        server = HookServer(u.settings.state_dir / "hook.sock", u.daemon.hooks, u.audit)
        await server.start()
        loop_task = asyncio.create_task(u.daemon.hook_loop())
        try:
            await u.daemon.start_agent(relaunch=True)               # launch 1
            first = hook_args(u.agent.settings_file)
            await fire(first, "SessionStart", flushed, source="startup")
            a = await u.say("first job")
            await fire(first, "UserPromptSubmit", flushed, prompt="first job")
            assert u.store.get("anchor") == a
            dead.add("admin")                                       # the launch crashes mid-turn
            await u.daemon.check_agent()                            # supervision resumes S1: launch 2
            second = hook_args(u.agent.settings_file)
            assert second != first
            await fire(second, "SessionStart", flushed, source="resume")
            b = await u.say("second job")
            await fire(second, "UserPromptSubmit", flushed, prompt="second job")
            assert u.store.get("anchor") == b and u.store.get("busy") is not None
            held = await u.say("third job")                         # held behind the running turn
            await fire(first, "Stop", flushed, last_assistant_message="old launch reply")
            assert u.store.get("anchor") == b and u.store.get("in_flight") == b
            assert u.store.get("busy") is not None
            assert u.tmux.pasted[-1] == "second job" and u.daemon.held == [(held, "third job")]
            sent = [(t, r) for _k, t, r in u.outbox()]
            assert ("old launch reply", None) in sent
            await fire(second, "Stop", flushed, last_assistant_message="new launch reply")
            assert ("new launch reply", b) in [(t, r) for _k, t, r in u.outbox()]
            assert u.tmux.pasted[-1] == "third job"
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            await server.close()
    run(scenario())


def test_each_launch_has_a_fresh_nonce_that_is_not_redacted(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    run(u.daemon.start_agent(relaunch=True))
    first = hook_args(u.agent.settings_file)
    run(u.daemon.start_agent(relaunch=True))
    second = hook_args(u.agent.settings_file)
    n1, n2 = first[first.index("--launch") + 1], second[second.index("--launch") + 1]
    assert re.fullmatch(r"[0-9a-f]{32}", n1) and re.fullmatch(r"[0-9a-f]{32}", n2) and n1 != n2
    assert show(n1, False) == n1


def test_an_event_with_no_launch_nonce_is_treated_as_stale(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed = asyncio.Event()
    real_flush = u.daemon.flush

    async def flush() -> None:
        await real_flush()
        flushed.set()
    u.daemon.flush = flush                              # type: ignore[method-assign]

    async def scenario() -> None:
        server = HookServer(u.settings.state_dir / "hook.sock", u.daemon.hooks, u.audit)
        await server.start()
        loop_task = asyncio.create_task(u.daemon.hook_loop())
        try:
            await u.daemon.start_agent(relaunch=True)
            current = hook_args(u.agent.settings_file)
            await fire(current, "SessionStart", flushed, source="startup")
            a = await u.say("job")
            await fire(current, "UserPromptSubmit", flushed, prompt="job")
            assert u.store.get("anchor") == a
            await fire(["--socket", str(u.settings.state_dir / "hook.sock")], "Stop", flushed,
                       last_assistant_message="no nonce")
            assert u.store.get("anchor") == a and u.store.get("busy") is not None
            assert [r for _k, t, r in u.outbox() if t == "no nonce"] == [None]
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            await server.close()
    run(scenario())


def test_stale_events_other_than_stop_change_nothing(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    flushed = asyncio.Event()
    real_flush = u.daemon.flush

    async def flush() -> None:
        await real_flush()
        flushed.set()
    u.daemon.flush = flush                              # type: ignore[method-assign]

    async def scenario() -> None:
        server = HookServer(u.settings.state_dir / "hook.sock", u.daemon.hooks, u.audit)
        await server.start()
        loop_task = asyncio.create_task(u.daemon.hook_loop())
        try:
            await u.daemon.start_agent(relaunch=True)
            current = hook_args(u.agent.settings_file)
            await fire(current, "SessionStart", flushed, source="startup")
            a = await u.say("job")
            await fire(current, "UserPromptSubmit", flushed, prompt="job")
            old = ["--socket", str(u.settings.state_dir / "hook.sock"), "--launch", "0" * 32]
            await fire(old, "UserPromptSubmit", flushed, prompt="intruder")
            await fire(old, "SessionStart", flushed, source="clear")
            assert u.store.get("anchor") == a and u.store.get("in_flight") == a
            assert u.store.get("busy") is not None
            assert "intruder" not in u.audit_text()
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            await server.close()
    run(scenario())


# --- 4. the child restart failure is audited by type and through `show` ---

class _Exited:
    returncode = 1

    async def wait(self) -> int:
        return 1


def test_a_child_restart_failure_is_audited_by_type_and_through_show(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep

    async def instant(_delay: float, *_a: object) -> None:
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", instant)
    from heterodyne.admind.audit import Audit
    from heterodyne.admind.wnagent import WnAgent, WnAgentError

    token = "sk" + "-" + "Q" * 24
    wn = WnAgent("wn-agent", tmp_path / "h", ("wss://a",), Audit(tmp_path / "audit.jsonl"))
    wn.proc = _Exited()     # type: ignore[assignment]

    async def failing(client: object, wait: float = 0) -> None:
        raise WnAgentError(f"cannot start\x1b[31m {token}")
    wn.start = failing      # type: ignore[method-assign]

    async def body() -> None:
        task = asyncio.create_task(wn.supervise(object()))      # type: ignore[arg-type]
        text = tmp_path / "audit.jsonl"
        while not text.exists() or "restart-failed" not in text.read_text():
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    run(body())
    log = (tmp_path / "audit.jsonl").read_text()
    assert "restart-failed" in log and "WnAgentError" in log
    assert token not in log and "\\u001b" not in log and "\x1b" not in log
