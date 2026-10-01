"""Review round 1 for the admind daemon: races, crash windows and hostile input, tested without tmux.

The daemon runs over a recording fake tmux and a stub control client; races are forced with explicit
barriers (events, patched awaitables), never sleeps. Nothing here starts a real wn-agent, claude,
systemctl or tmux server, or touches the network or ~/.claude.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import subprocess
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent
from fakes.settings import OPERATOR_HEX, make_settings

from heterodyne.admind.agent import SESSION, AdminAgent
from heterodyne.admind.audit import Audit
from heterodyne.admind.commands import CommandRunner
from heterodyne.admind.daemon import RESTARTED_NOTICE, UNCERTAIN, Admind
from heterodyne.admind.hook import HookEvent
from heterodyne.admind.store import Store, now
from heterodyne.marmot.control import ControlError, InboundMessage, decode_event
from heterodyne.marmot.nip19 import hex_to_npub
from heterodyne.services import UnitStatus
from heterodyne.tmux import Tmux, TmuxError, TmuxPasteUncertain

GROUP = "b2" * 32
STRANGER = "e5" * 32
_EVENTS = FakeWnAgent(Path("/nonexistent"))     # only used to build event frames, never started


class Services:
    def __init__(self) -> None:
        self.restart_started = threading.Event()
        self.restart_release = threading.Event()
        self.block_restart = False

    def restart(self, unit: str) -> tuple[bool, str]:
        if self.block_restart:
            self.restart_started.set()
            assert self.restart_release.wait(10), "test never released the restart"
        return True, ""

    def status(self, unit: str) -> UnitStatus:
        return UnitStatus(unit, "active", "running", "")


class FakeTmux:
    """Records what the daemon does to the admin agent's pane. `paste_error` is raised by paste()."""

    def __init__(self) -> None:
        self.sessions = {SESSION}
        self.pasted: list[str] = []
        self.keys: list[str] = []
        self.paste_error: BaseException | None = None

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None:
        self.sessions.add(name)

    def pane_dead(self, name: str) -> bool:
        return False

    def paste(self, name: str, text: str) -> None:
        if self.paste_error is not None:
            raise self.paste_error
        self.pasted.append(text)

    def send_key(self, name: str, key: str) -> None:
        self.keys.append(key)

    def capture(self, name: str, lines: int) -> str:
        return ""

    def kill(self, name: str) -> None:
        self.sessions.discard(name)


class StubClient:
    """The ControlClient surface Admind uses. Events come from `events`; `sent` records send_final."""

    def __init__(self) -> None:
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.member_count = 2
        self.group_info_error: ControlError | None = None
        self.sent: list[SimpleNamespace] = []
        self.subscribe_calls = 0
        self.subscribe_failures = 0           # the next N subscribe() calls raise a retryable error
        self.on_subscribe: Callable[[], None] | None = None

    async def group_info(self, account: str, group: str) -> Any:
        if self.group_info_error is not None:
            raise self.group_info_error
        return SimpleNamespace(group_id_hex=group, member_count=self.member_count)

    async def send_final(self, account: str, group: str, text: str, reply_to: str | None,
                         key: str | None = None) -> Any:
        self.sent.append(SimpleNamespace(text=text, reply_to=reply_to, key=key))
        return SimpleNamespace(message_ids_hex=[hashlib.sha256(f"{len(self.sent)}".encode()).hexdigest()])

    async def subscribe(self, account: str, group: str) -> AsyncIterator[Any]:
        self.subscribe_calls += 1
        if self.on_subscribe is not None:
            self.on_subscribe()
        if self.subscribe_failures > 0:
            self.subscribe_failures -= 1
            raise ControlError("wn-agent closed the connection", "socket_closed", True)
        while True:
            yield await self.events.get()


def inbound(text: str, mid: str, sender: str = OPERATOR_HEX) -> InboundMessage:
    frame = {"marmot_agent_control": "marmot.agent-control.v2", "id": "r",
             **_EVENTS.message_event(text, sender, mid)}
    event = decode_event(json.dumps(frame).encode(), "r")
    assert isinstance(event, InboundMessage)
    return event


def membership_change() -> Any:
    frame = {"marmot_agent_control": "marmot.agent-control.v2", "id": "r", "type": "group_state_changed",
             "account_id_hex": ACCOUNT, "group_id_hex": GROUP, "event_id_hex": "f6" * 32,
             "change": "member_added"}
    return decode_event(json.dumps(frame).encode(), "r")


class Unit:
    """An Admind over fakes, with a running, idle admin agent and an operator already seen."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.settings = make_settings(tmp_path)
        self.store = Store(self.settings.state_dir / "admind.db")
        self.audit = Audit(self.settings.state_dir / "audit.jsonl")
        self.tmux = FakeTmux()
        self.services = Services()
        self.client = StubClient()
        self.build()
        self.store.set("agent_session", "S1")
        self.agent.started("S1")
        self.store.set("operator_seen_at", now())
        self.daemon.ready.set()
        self.daemon.group_ok = True
        self.seq = 0

    def build(self) -> None:
        """(Re)create the daemon over the same store, as after a restart."""
        self.agent = AdminAgent(self.tmux, self.store, self.settings, self.settings.state_dir / "hook.sock")
        runner = CommandRunner(self.agent, self.services, ("fake.service",), lambda: True)
        self.daemon = Admind(self.settings, self.client, self.store, self.audit, self.agent, runner,  # type: ignore[arg-type]
                             ACCOUNT, GROUP)

    def mid(self) -> str:
        self.seq += 1
        return f"{self.seq:064x}"

    async def say(self, text: str) -> str:
        mid = self.mid()
        await self.daemon.on_message(inbound(text, mid))
        return mid

    def audit_text(self) -> str:
        return (self.settings.state_dir / "audit.jsonl").read_text()

    def outbox(self) -> list[tuple[str, str, str | None]]:
        return [(r.key, r.text, r.reply_to) for r in self.store.pending()]

    def texts(self) -> list[str]:
        return [t for _, t, _ in self.outbox()]


def run(coro: Awaitable[Any]) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(coro, 20)
    return asyncio.run(bounded())


# --- 1. alert outbox keys never carry the alert's name ---

def secret_token() -> str:
    return "sk" + "-" + "Q" * 24


def write_alert(u: Unit, raw_name: bytes, text: str = "disk is full") -> None:
    u.settings.alerts_dir.mkdir(parents=True, exist_ok=True)
    stem = raw_name.decode("utf-8", "surrogateescape")
    ident = stem.removesuffix(".json")
    (u.settings.alerts_dir / stem).write_text(json.dumps({"id": ident, "created_at": "t", "text": text}))


def test_alert_keys_and_audit_never_hold_a_name_that_is_an_identifier_or_a_secret(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    npub = hex_to_npub(STRANGER)
    token = secret_token()
    names = [f"{npub}.json", f"{token}.json", f"{'ab' * 32}.json"]
    for n in names:
        write_alert(u, n.encode())

    async def scenario() -> None:
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
        await u.daemon.relay_alerts()           # a second sweep relays nothing new
        await u.daemon.outbox_pass()
    run(scenario())
    assert len(u.client.sent) == 3
    for sent in u.client.sent:
        key = sent.key
        assert key.startswith("alert:") and len(key) == len("alert:") + 32
        assert all(c in "0123456789abcdef" for c in key[len("alert:"):])
    expected = {"alert:" + hashlib.sha256(os.fsencode(n.removesuffix(".json"))).hexdigest()[:32]
                for n in names}
    assert {s.key for s in u.client.sent} == expected
    everything = u.audit_text() + "\n".join(s.text + s.key for s in u.client.sent)
    for banned in (npub, STRANGER, token, "ab" * 32):
        assert banned not in everything, banned[:8]
    assert any(k in u.audit_text() for k in expected)           # the audit correlates on the opaque key


# --- 2. an alert file whose name is not UTF-8 ---

def test_a_non_utf8_alert_name_is_reported_safely_and_never_blocks_later_alerts(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    write_alert(u, b"bad\xff\xfe.json")
    write_alert(u, b"later.json", "still relayed")

    async def scenario() -> None:
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
    run(scenario())
    texts = [s.text for s in u.client.sent]
    assert any("still relayed" in t for t in texts)
    assert any("\\xff" in t for t in texts)                      # the bad one is reported, escaped
    for sent in u.client.sent:
        sent.text.encode("utf-8")                                # no surrogate escapes anywhere
        sent.key.encode("utf-8")
    u.audit_text().encode("utf-8")

    async def again() -> None:                                    # relayed once only
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
    run(again())
    assert len(u.client.sent) == 2


def test_an_alert_that_fails_to_relay_is_audited_by_type_and_never_blocks_the_rest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    write_alert(u, b"first.json")
    write_alert(u, b"second.json", "second goes out")
    real = u.store.relay_alert
    calls = [0]

    def flaky(name: bytes, key: str, text: str) -> bool:
        calls[0] += 1
        if calls[0] == 1:
            raise ValueError(f"boom {hex_to_npub(STRANGER)}")
        return real(name, key, text)
    monkeypatch.setattr(u.store, "relay_alert", flaky)

    async def scenario() -> None:
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
    run(scenario())
    assert any("second goes out" in s.text for s in u.client.sent)
    audit = u.audit_text()
    assert "ValueError" in audit and hex_to_npub(STRANGER) not in audit and "boom" not in audit


# --- 3. a latch (or an unverified group) stops held prompts reaching the agent ---

@pytest.mark.parametrize("how", ["latched", "unverified"])
def test_held_prompts_are_not_pasted_once_latched_or_unverified(tmp_path: Path, how: str) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        a = await u.say("prompt A")
        b = await u.say("prompt B")                       # held behind A
        assert u.tmux.pasted == ["prompt A"] and u.daemon.held == [(b, "prompt B")]
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="prompt A"))
        if how == "latched":
            u.daemon.latch("group membership changed (member_added)")
        else:
            u.daemon.group_ok = False
        assert u.store.get("anchor") == a
        await u.daemon.on_hook(HookEvent("Stop", "S1", None, "A is done"))
        await u.daemon.flush()
        assert u.tmux.pasted == ["prompt A"]               # B stays held: nothing reaches the agent
        assert u.daemon.held == [(b, "prompt B")]
        assert u.store.get("in_flight") is None and u.store.get("busy") is None   # only A's Stop acted
    run(scenario())
    assert '"action": "dispatch-blocked"' in u.audit_text()


# --- 4. membership observation stays responsive ---

async def stop_task(task: "asyncio.Task[Any]") -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_membership_event_latches_while_a_slow_restart_runs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    u.services.block_restart = True
    latched = asyncio.Event()
    subscribed = asyncio.Event()
    restart_replied = asyncio.Event()
    real_latch = u.daemon.latch
    real_enqueue = u.store.enqueue

    def latch(why: str) -> None:
        real_latch(why)
        latched.set()

    def enqueue(key: str, text: str, reply_to: str | None) -> bool:
        if text.startswith("Restarted"):
            restart_replied.set()
        return real_enqueue(key, text, reply_to)
    monkeypatch.setattr(u.daemon, "latch", latch)
    monkeypatch.setattr(u.store, "enqueue", enqueue)
    u.client.on_subscribe = subscribed.set

    async def scenario() -> None:
        daemon = asyncio.create_task(u.daemon.run())
        try:
            await subscribed.wait()
            await u.client.events.put(inbound("!restart fake.service", u.mid()))
            assert await asyncio.to_thread(u.services.restart_started.wait, 10)   # the restart is running
            await u.client.events.put(membership_change())
            # The latch must happen while the restart is still blocked: the reader never waits for it.
            await asyncio.wait_for(latched.wait(), 3)
            assert not u.services.restart_release.is_set()
            u.services.restart_release.set()
            await asyncio.wait_for(restart_replied.wait(), 10)      # its reply is queued ...
            await u.daemon.outbox_pass()
            assert u.client.sent == []                              # ... but nothing is sent after the latch
        finally:
            u.services.restart_release.set()
            await stop_task(daemon)
    run(scenario())


def test_subscription_failure_pauses_the_outbox_until_the_group_is_reverified(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.client.subscribe_failures = 1
    asleep = asyncio.Event()
    gate = asyncio.Event()
    resubscribed = asyncio.Event()

    async def fake_sleep(delay: float) -> None:
        asleep.set()
        await gate.wait()
    u.daemon._sleep = fake_sleep
    u.client.on_subscribe = lambda: resubscribed.set() if u.client.subscribe_calls >= 2 else None

    async def scenario() -> None:
        reader = asyncio.create_task(u.daemon.inbound_loop())
        try:
            await asleep.wait()                                   # the subscription failed; backing off
            assert not u.daemon.group_ok
            u.store.enqueue("k:1", "held back", None)
            await u.daemon.outbox_pass()
            assert u.client.sent == []
            u.client.group_info_error = ControlError("down", "unavailable", True)
            gate.set()
            await resubscribed.wait()                             # reconnected, but not re-verified
            await u.daemon.outbox_pass()
            assert u.client.sent == [] and not u.daemon.group_ok
            u.client.group_info_error = None
            assert await u.daemon.check_group()
            await u.daemon.outbox_pass()
            assert [s.text for s in u.client.sent] == ["held back"]
        finally:
            await stop_task(reader)
    run(scenario())


def test_subscription_failures_back_off_with_doubling_delays(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.client.subscribe_failures = 10**6
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:        # a fake clock: no waiting, and a bounded run
        delays.append(delay)
        if len(delays) == 8:
            raise asyncio.CancelledError
    u.daemon._sleep = fake_sleep
    with pytest.raises(asyncio.CancelledError):
        run(u.daemon.inbound_loop())
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]       # doubling up to the cap, never a busy loop
    assert u.client.subscribe_calls == 8 and "reconnect" in u.audit_text()


# --- 5. a Stop applies only to the turn it was captured for ---

class SlowRead:
    """Stands in for reading a Stop's reply text: blocks in its worker thread until released."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, ev: HookEvent) -> str:
        self.started.set()
        assert self.release.wait(10), "test never released the read"
        return self.text


def stop_during_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str) -> tuple[Unit, str]:
    u = Unit(tmp_path)
    held: list[str] = []
    slow = SlowRead("A is done")
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", slow)

    async def scenario() -> None:
        await u.say("prompt A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="prompt A"))
        b = await u.say("prompt B")                               # held behind A
        stop = asyncio.create_task(u.daemon.on_hook(HookEvent("Stop", "S1")))
        assert await asyncio.to_thread(slow.started.wait, 10)     # A's reply is being read
        await u.daemon.handle(u.mid(), command)                   # runs, and may dispatch B, meanwhile
        slow.release.set()
        await stop
        held.append(b)
    run(scenario())
    return u, held[0]


def test_interrupt_dispatching_b_during_a_reply_read_leaves_b_busy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u, b = stop_during_command(tmp_path, monkeypatch, "!interrupt")
    assert u.tmux.pasted == ["prompt A", "prompt B"]
    assert u.store.get("in_flight") == b
    assert u.store.get("busy") is not None                        # A's Stop did not clear B's busy period
    assert "A is done" not in u.texts()                           # nor post a reply for a turn that was cut
    assert '"action": "stale-stop"' in u.audit_text()


def test_new_during_a_reply_read_emits_no_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u, _ = stop_during_command(tmp_path, monkeypatch, "!new")
    assert "A is done" not in u.texts()
    assert not any(k.startswith("reply:") for k, _, _ in u.outbox())
    assert '"action": "stale-stop"' in u.audit_text()


# --- 6. uncertain delivery is never retried ---

def scripted_tmux(monkeypatch: pytest.MonkeyPatch, fail: dict[str, BaseException]) -> tuple[Tmux, list[str]]:
    """A Tmux whose subprocess calls are replaced: the named subcommands raise, the rest succeed."""
    t = Tmux("hz-test-never-started")
    calls: list[str] = []

    def fake_run(*args: str, data: bytes | None = None, check: bool = True) -> Any:
        calls.append(args[0])
        if args[0] in fail:
            raise fail[args[0]]
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(t, "_run", fake_run)
    monkeypatch.setattr("heterodyne.tmux.time.sleep", lambda _s: None)
    return t, calls


@pytest.mark.parametrize("step", ["load-buffer", "paste-buffer"])
def test_a_paste_that_fails_before_enter_is_a_definite_non_submission(
        monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    t, calls = scripted_tmux(monkeypatch, {step: TmuxError("failed")})
    with pytest.raises(TmuxError) as info:
        t.paste("s", "hi")
    assert not isinstance(info.value, TmuxPasteUncertain)
    assert "send-keys" not in calls                                # Enter was never reached


@pytest.mark.parametrize("fail", [
    {"send-keys": TmuxError("failed")},
    {"send-keys": subprocess.TimeoutExpired("tmux", 15)},
    {"delete-buffer": subprocess.TimeoutExpired("tmux", 15)},
])
def test_a_paste_that_fails_at_or_after_enter_is_uncertain(
        monkeypatch: pytest.MonkeyPatch, fail: dict[str, BaseException]) -> None:
    t, _ = scripted_tmux(monkeypatch, fail)
    with pytest.raises(TmuxPasteUncertain):
        t.paste("s", "hi")


def test_a_definite_send_failure_requeues_the_prompt_once_more(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.tmux.paste_error = TmuxError("failed")

    async def scenario() -> None:
        a = await u.say("prompt A")
        assert u.daemon.held == [(a, "prompt A")]                  # kept, to be sent again
        assert u.store.get("in_flight") is None and u.store.get("busy") is None
        assert u.store.inbound_with_status("received") == [a]
        u.tmux.paste_error = None
        await u.daemon.flush()
        assert u.tmux.pasted == ["prompt A"]
    run(scenario())


@pytest.mark.parametrize("error", [TmuxPasteUncertain("unsure"), OSError("disk"), RuntimeError("bug")])
def test_an_uncertain_send_is_never_retried_and_tells_the_operator(tmp_path: Path, error: Exception) -> None:
    u = Unit(tmp_path)
    u.tmux.paste_error = error

    async def scenario() -> None:
        a = await u.say("prompt A")
        assert u.daemon.held == []                                 # not requeued
        u.tmux.paste_error = None
        await u.daemon.flush()
        await u.daemon.flush()
        assert u.tmux.pasted == []                                 # and not pasted again
        assert u.store.get("in_flight") is None and u.store.get("anchor") is None
        assert u.store.get("in_flight_text") is None
        assert u.store.get("busy") is not None                     # no evidence the agent is idle
        notice = [(t, r) for _, t, r in u.outbox() if "uncertain" in t]
        assert notice == [(f"No reply to this message: {UNCERTAIN}.", a)]
    run(scenario())
    audit = u.audit_text()
    assert "disk" not in audit and "bug" not in audit and "unsure" not in audit


# --- 7. crash windows: each transition and its outbox batch are one transaction ---
class Crash(BaseException):
    """Stands in for the process dying: nothing in admind catches it."""


def fail_nth_enqueue(u: Unit, monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    real = u.store.enqueue
    calls = [0]

    def enqueue(key: str, text: str, reply_to: str | None) -> bool:
        calls[0] += 1
        if calls[0] == n:
            raise Crash
        return real(key, text, reply_to)
    monkeypatch.setattr(u.store, "enqueue", enqueue)


def test_recovery_notice_and_state_change_are_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    mid = "99" * 32
    u.store.claim_inbound(mid)
    fail_nth_enqueue(u, monkeypatch, 1)
    with pytest.raises(Crash):
        u.daemon.recover()                                  # dies between the transition and the notice
    assert u.store.inbound_with_status("received") == [mid] and u.outbox() == []
    monkeypatch.undo()
    u.build()                                               # the restarted process
    u.daemon.recover()
    u.daemon.recover()                                      # recovery is idempotent
    assert [(t, r) for _, t, r in u.outbox()] == [(RESTARTED_NOTICE, mid)]
    assert u.store.inbound_with_status("dropped") == [mid]


def test_a_stop_is_all_or_nothing_and_recovery_then_answers_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", lambda ev: "x" * 9000)   # three chunks

    async def scenario() -> str:
        a = await u.say("prompt A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="prompt A"))
        fail_nth_enqueue(u, monkeypatch, 2)                 # dies after the first chunk is queued
        with pytest.raises(Crash):
            await u.daemon.on_hook(HookEvent("Stop", "S1"))
        return a
    a = run(scenario())
    assert u.outbox() == []                                  # no partial chunk set is ever visible
    assert u.store.get("anchor") == a and u.store.get("in_flight") == a and u.store.get("busy") is not None
    monkeypatch.undo()
    u.build()
    u.daemon.recover()
    notice = ("No reply to this message: admind restarted during this turn; "
              "a late reply may appear unthreaded.")
    assert [(t, r) for _, t, r in u.outbox()] == [(notice, a)]


def test_a_stop_commits_its_chunks_and_state_together(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", lambda ev: "x" * 9000)

    async def scenario() -> str:
        a = await u.say("prompt A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="prompt A"))
        await u.daemon.on_hook(HookEvent("Stop", "S1"))
        return a
    a = run(scenario())
    assert [r for _, _, r in u.outbox()] == [a, a, a]
    assert u.store.get("anchor") is None and u.store.get("in_flight") is None and u.store.get("busy") is None


def test_a_command_is_executing_before_it_runs_and_recovery_answers_it_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)

    def dies(unit: str) -> tuple[bool, str]:
        assert u.store.inbound_with_status("executing") == [mid]     # persisted before execution
        raise Crash
    mid = u.mid()
    monkeypatch.setattr(u.services, "restart", dies)
    with pytest.raises(Crash):
        run(u.daemon.on_message(inbound("!restart fake.service", mid)))
    u.build()
    u.daemon.recover()
    u.daemon.recover()
    assert [(t, r) for _, t, r in u.outbox()] == [(RESTARTED_NOTICE, mid)]


def test_a_command_waiting_for_the_dispatch_lock_is_recovered_not_lost(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    mid = u.mid()

    async def scenario() -> None:
        async with u.daemon.dispatch_lock:                   # !new waits here for the lock
            task = asyncio.create_task(u.daemon.on_message(inbound("!new", mid)))
            while u.store.inbound_with_status("executing") != [mid]:
                await asyncio.sleep(0)                       # yield until the command has been persisted
            task.cancel()                                    # shutdown
            with contextlib.suppress(asyncio.CancelledError):
                await task
    run(scenario())
    u.build()
    u.daemon.recover()
    assert [(t, r) for _, t, r in u.outbox()] == [(RESTARTED_NOTICE, mid)]


def test_a_finished_command_and_its_reply_are_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    mid = u.mid()
    fail_nth_enqueue(u, monkeypatch, 1)
    with pytest.raises(Crash):
        run(u.daemon.on_message(inbound("!ps", mid)))        # the command ran; its reply can't be queued
    assert u.store.inbound_with_status("done") == [] and u.outbox() == []
    monkeypatch.undo()
    u.build()
    u.daemon.recover()
    assert [(t, r) for _, t, r in u.outbox()] == [(RESTARTED_NOTICE, mid)]
    run(u.daemon.on_message(inbound("!ps", u.mid())))
    assert len(u.store.inbound_with_status("done")) == 1     # a normal command ends up done
