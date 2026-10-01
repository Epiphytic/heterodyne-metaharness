import asyncio
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent
from fakes.settings import OPERATOR_HEX, make_settings

from heterodyne.admind.agent import TMUX_SOCKET, AdminAgent
from heterodyne.admind.audit import Audit
from heterodyne.admind.commands import CommandRunner
from heterodyne.admind.daemon import (
    LONG_TURN,
    NO_REPLY,
    READY_NOTICE,
    RESTARTED_NOTICE,
    UNCONFIRMED,
    Admind,
    supervised,
)
from heterodyne.admind.hook import HookEvent
from heterodyne.admind.store import Store
from heterodyne.marmot.control import ControlClient, ControlError
from heterodyne.marmot.nip19 import hex_to_npub
from heterodyne.services import UnitStatus
from heterodyne.tmux import Tmux, TmuxError

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
FAKE_CLAUDE = Path(__file__).parent / "fakes" / "fake_claude.py"
STRANGER = "e5" * 32


@pytest.fixture(autouse=True)
def _fake_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_LOG", "unset")      # restored after the test; Harness overwrites it


class Services:
    def restart(self, unit: str) -> tuple[bool, str]:
        return True, ""

    def status(self, unit: str) -> UnitStatus:
        return UnitStatus(unit, "active", "running", "")


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "claude"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_CLAUDE} \"$@\"\n")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        self.log = tmp_path / "claude.log"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        self.settings = make_settings(tmp_path, adapter_binary=str(wrapper))
        self.store = Store(self.settings.state_dir / "admind.db")
        self.store.set("group_id_hex", "b2" * 32)
        self.audit = Audit(self.settings.state_dir / "audit.jsonl")
        self.fake = FakeWnAgent(tmp_path / "wn.sock")
        self.tmux = Tmux(f"hz-test-{uuid.uuid4().hex[:8]}")
        self.agent = AdminAgent(self.tmux, self.store, self.settings, self.settings.state_dir / "hook.sock")
        runner = CommandRunner(self.agent, Services(), ("fake.service",), lambda: True)
        self.daemon = Admind(self.settings, ControlClient(tmp_path / "wn.sock", "test-token", timeout=5),
                             self.store, self.audit, self.agent, runner, ACCOUNT, "b2" * 32)
        self.seq = 0

    async def say(self, text: str, sender: str = OPERATOR_HEX) -> str:
        self.seq += 1
        mid = f"{self.seq:064x}"
        await self.fake.push_event(self.fake.message_event(text, sender, mid))
        return mid

    def event(self, name: str, session: str, transcript: str | None = None, reply: str | None = None,
              **kw: Any) -> HookEvent:
        """A hook event as the current launch's hook command would deliver it (with its nonce)."""
        kw.setdefault("last_assistant_message", reply)
        return HookEvent(name, session, transcript, launch=self.agent.launch_nonce, **kw)

    def texts(self) -> list[str]:
        return [r["text"] for r in self.fake.sent]

    async def until(self, pred: Callable[[], bool], timeout: float = 15) -> None:
        async def poll() -> None:
            while not pred():
                await asyncio.sleep(0.05)
        await asyncio.wait_for(poll(), timeout)


def drop_tmux(h: Harness) -> None:
    """Kill the test's private tmux server and remove its socket."""
    h.tmux.kill_server()
    tmpdir = Path(os.environ.get("TMUX_TMPDIR") or tempfile.gettempdir()) / f"tmux-{os.getuid()}"
    (tmpdir / h.tmux.socket_name).unlink(missing_ok=True)


def run_with(tmp_path: Path, scenario: Callable[[Harness], Awaitable[None]],
             before: Callable[[Harness], Any] | None = None) -> Harness:
    h = Harness(tmp_path)
    if before:
        before(h)

    async def body() -> None:
        await h.fake.start()
        task = asyncio.create_task(h.daemon.run())
        try:
            await h.fake.wait_subscribed(10)
            await scenario(h)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await h.fake.stop()
    try:
        asyncio.run(body())
    finally:
        drop_tmux(h)
    return h


@needs_tmux
def test_operator_round_trip_ready_notice_and_threaded_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        mid = await h.say("hello there")
        await h.until(lambda: "echo: hello there" in h.texts())
        assert h.texts()[0] == READY_NOTICE
        reply = next(r for r in h.fake.sent if r["text"] == "echo: hello there")
        assert reply["reply_to_message_id_hex"] == mid
        assert h.store.inbound_with_status("dispatched") == [mid]
    h = run_with(tmp_path, scenario)
    audit = (h.settings.state_dir / "audit.jsonl").read_text()
    assert '"kind": "inbound"' in audit and '"kind": "dispatch"' in audit
    assert TMUX_SOCKET == "heterodyne-admind"


@needs_tmux
def test_two_quick_messages_each_get_their_own_threaded_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("warm up")
        await h.until(lambda: "echo: warm up" in h.texts())
        first = await h.say("one")
        second = await h.say("two")
        await h.until(lambda: "echo: two" in h.texts())
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["echo: one"] == first and by_text["echo: two"] == second
    run_with(tmp_path, scenario)


@needs_tmux
def test_recovery_notice_waits_for_a_good_group_check(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.set("operator_seen_at", "2026-09-30T00:00:00+00:00")
        h.store.claim_inbound("99" * 32)
        h.fake.fail_group_info = True

    async def scenario(h: Harness) -> None:
        await asyncio.sleep(1.5)
        assert h.fake.sent == []                    # group unverified: nothing leaves
        h.fake.fail_group_info = False
        await h.until(lambda: RESTARTED_NOTICE in h.texts())
    run_with(tmp_path, scenario, before)


@needs_tmux
def test_recovery_notice_waits_for_the_operator(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.claim_inbound("99" * 32)            # claimed, but the operator was never seen

    async def scenario(h: Harness) -> None:
        await asyncio.sleep(1.5)
        assert h.fake.sent == []
        await h.say("hi")
        await h.until(lambda: RESTARTED_NOTICE in h.texts())
    run_with(tmp_path, scenario, before)


@needs_tmux
def test_latch_mid_batch_stops_the_rest(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hi")
        await h.until(lambda: "echo: hi" in h.texts())
        sent = len(h.fake.sent)
        h.fake.on_send = lambda _req: h.store.set("latched", "test")
        for i in range(3):
            h.store.enqueue(f"batch:{i}", f"b{i}", None)
        h.daemon.wake.set()
        await h.until(lambda: len(h.fake.sent) > sent)
        await asyncio.sleep(0.5)
        assert len(h.fake.sent) == sent + 1
    run_with(tmp_path, scenario)


@needs_tmux
def test_non_operator_dropped_silently_and_commands_answered(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("let me in", sender=STRANGER)
        await h.say("!ps")
        await h.until(lambda: any("fake.service: active (running)" in t for t in h.texts()))
        await h.say("!restrat wsd")
        await h.until(lambda: any(t.startswith("Unknown command !restrat") for t in h.texts()))
        await h.say("bad \x1b[201~ paste")
        await h.until(lambda: any("control characters" in t for t in h.texts()))
        assert not any("let me in" in t for t in h.texts())
    h = run_with(tmp_path, scenario)
    assert "sender is not the operator" in (h.settings.state_dir / "audit.jsonl").read_text()


@needs_tmux
def test_member_count_latches_and_stops_all_posting(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("first")
        await h.until(lambda: "echo: first" in h.texts())
        sent = len(h.fake.sent)
        h.fake.member_count = 3
        await h.say("second")
        await h.until(lambda: h.store.get("latched") is not None)
        (h.settings.alerts_dir).mkdir(parents=True, exist_ok=True)
        (h.settings.alerts_dir / "a1.json").write_text(json.dumps(
            {"id": "a1", "created_at": "t", "text": "x"}))
        await asyncio.sleep(1.0)
        assert len(h.fake.sent) == sent
        h.fake.member_count = 2
        await h.say("third")
        await asyncio.sleep(1.0)
        assert len(h.fake.sent) == sent            # still latched until `admind rearm`
    h = run_with(tmp_path, scenario)
    assert "group has 3 members" in (h.store.get("latched") or "")


@needs_tmux
def test_membership_event_latches(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.fake.push_event({"type": "group_state_changed", "account_id_hex": ACCOUNT,
                                 "group_id_hex": "b2" * 32, "event_id_hex": "f6" * 32,
                                 "change": "member_added"})
        await h.until(lambda: h.store.get("latched") is not None)
    run_with(tmp_path, scenario)


@needs_tmux
def test_alerts_wait_for_the_operator_then_relay_once(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.settings.alerts_dir.mkdir(parents=True)
        (h.settings.alerts_dir / "a1.json").write_text(json.dumps(
            {"id": "a1", "created_at": "2026-09-30T00:00:00Z", "text": "card undelivered"}))

    async def scenario(h: Harness) -> None:
        await asyncio.sleep(0.8)
        assert h.fake.sent == []                    # operator not seen yet (plan decision D5)
        await h.say("hi")
        await h.until(lambda: any("card undelivered" in t for t in h.texts()))
        await asyncio.sleep(0.5)
        assert sum("card undelivered" in t for t in h.texts()) == 1
    run_with(tmp_path, scenario, before)


@needs_tmux
def test_restart_recovery_answers_undelivered_messages(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.set("operator_seen_at", "2026-09-30T00:00:00+00:00")
        h.store.claim_inbound("99" * 32)

    async def scenario(h: Harness) -> None:
        await h.until(lambda: RESTARTED_NOTICE in h.texts())
        row = next(r for r in h.fake.sent if r["text"] == RESTARTED_NOTICE)
        assert row["reply_to_message_id_hex"] == "99" * 32
    h = run_with(tmp_path, scenario, before)
    assert h.store.inbound_with_status("dropped") == ["99" * 32]


@needs_tmux
def test_outbox_retries_transient_failures_in_order(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        h.fake.fail_sends = 2
        await h.say("one")
        await h.until(lambda: "echo: one" in h.texts(), timeout=30)
        assert h.texts().index(READY_NOTICE) < h.texts().index("echo: one")
    run_with(tmp_path, scenario)


@needs_tmux
def test_empty_reply_gets_a_placeholder(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("earlier")                       # the transcript now holds an earlier text reply
        await h.until(lambda: "echo: earlier" in h.texts())
        mid = await h.say("__silent__")
        await h.until(lambda: NO_REPLY in h.texts())
        row = next(r for r in h.fake.sent if r["text"] == NO_REPLY)
        assert row["reply_to_message_id_hex"] == mid
        assert h.texts().count("echo: earlier") == 1   # the old reply was not resent
    run_with(tmp_path, scenario)


def audit_text(h: Harness) -> str:
    return (h.settings.state_dir / "audit.jsonl").read_text()


def dispatched(h: Harness) -> list[str]:
    records = [json.loads(line) for line in (h.settings.state_dir / "audit.jsonl").read_text().splitlines()]
    return [r["message_id"] for r in records if r["kind"] == "dispatch"]


@needs_tmux
def test_lost_prompt_hook_holds_the_queue_until_interrupt(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        lost = await h.say("__noprompt__")
        after = await h.say("after")
        await h.until(lambda: UNCONFIRMED in h.texts(), timeout=30)
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["echo: __noprompt__"] is None             # unconfirmed: posted top-level
        assert by_text[UNCONFIRMED] == lost
        assert after not in dispatched(h)                        # a timeout never dispatches
        await h.say("!interrupt")
        await h.until(lambda: "echo: after" in h.texts(), timeout=30)
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["echo: after"] == after
        assert by_text["No reply to this message: interrupted by !interrupt."] == lost
    run_with(tmp_path, scenario)


@needs_tmux
def test_long_turn_with_a_lost_prompt_hook_is_never_pasted_over(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        busy = await h.say("__noprompt__ __hang__")
        nxt = await h.say("next")
        await h.until(lambda: UNCONFIRMED in h.texts() and LONG_TURN in h.texts(), timeout=30)
        await asyncio.sleep(0.5)
        assert nxt not in dispatched(h) and "echo: next" not in h.texts()
        await h.say("!interrupt")
        await h.until(lambda: "echo: next" in h.texts(), timeout=30)
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["echo: next"] == nxt
        assert by_text["No reply to this message: interrupted by !interrupt."] == busy
    run_with(tmp_path, scenario)


@needs_tmux
def test_failed_interrupt_keeps_the_queue_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def before(h: Harness) -> None:
        def fail() -> None:
            raise TmuxError("send-keys failed")
        monkeypatch.setattr(h.agent, "interrupt", fail)

    async def scenario(h: Harness) -> None:
        hung = await h.say("__hang__")
        await h.until(lambda: h.store.get("anchor") == hung)
        nxt = await h.say("next")
        await h.say("!interrupt")
        await h.until(lambda: any(t.startswith("!interrupt failed: a tmux command failed")
                                  for t in h.texts()))
        await asyncio.sleep(0.5)
        assert nxt not in dispatched(h)
        assert h.store.get("anchor") == hung and h.store.get("busy") is not None
    run_with(tmp_path, scenario, before)


@needs_tmux
def test_stop_during_interrupt_never_dispatches_into_the_escape(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def before(h: Harness) -> None:
        real = h.agent.interrupt

        def slow() -> None:
            started.set()
            assert release.wait(30), "test never released the Escape"   # the Stop lands while it is pending
            real()
        monkeypatch.setattr(h.agent, "interrupt", slow)

    async def scenario(h: Harness) -> None:
        hung = await h.say("__hang__")
        await h.until(lambda: h.store.get("anchor") == hung)
        nxt = await h.say("next")
        await h.say("!interrupt")
        await h.until(started.is_set)
        await h.daemon.hooks.put(h.event("Stop", h.agent.session_id or "", None, "finished anyway"))
        # Processed while the Escape is pending: the reply is queued (the outbox loop itself is parked
        # behind the dispatch lock the !interrupt holds, so it cannot have been sent yet).
        await h.until(lambda: any(r.text == "finished anyway" for r in h.store.pending()))
        release.set()
        await h.until(lambda: "echo: next" in h.texts())
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["finished anyway"] == hung
        assert by_text["echo: next"] == nxt
        assert not any(t.startswith("No reply to this message") for t in h.texts())
    run_with(tmp_path, scenario, before)


def slow(monkeypatch: pytest.MonkeyPatch, h: Harness, method: str, started: threading.Event,
         release: threading.Event) -> None:
    """Hold AdminAgent.<method> until `release` is set, so events can land while the command is in
    progress: the test sets `release` only after injecting them."""
    real = getattr(h.agent, method)

    def delayed() -> Any:
        started.set()
        assert release.wait(30), "test never released the command"
        return real()
    monkeypatch.setattr(h.agent, method, delayed)


@needs_tmux
def test_prompt_during_interrupt_keeps_the_new_turn_busy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    async def scenario(h: Harness) -> None:
        hung = await h.say("__hang__")
        await h.until(lambda: h.store.get("anchor") == hung)
        nxt = await h.say("next")
        before = h.store.get("busy")
        await h.say("!interrupt")
        await h.until(started.is_set)
        sid = h.agent.session_id or ""
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, prompt="typed at the terminal"))
        await h.until(lambda: h.store.get("busy") != before)       # the terminal turn began: new period
        release.set()
        await h.until(lambda: "Sent Esc to the admin agent." in h.texts())
        await asyncio.sleep(0.5)
        assert nxt not in dispatched(h)                          # the terminal turn is still running
        await h.daemon.hooks.put(h.event("Stop", sid, last_assistant_message="terminal answer"))
        await h.until(lambda: "echo: next" in h.texts())
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["terminal answer"] is None and by_text["echo: next"] == nxt
    h = run_with(tmp_path, scenario, lambda h: slow(monkeypatch, h, "interrupt", started, release))
    audit = audit_text(h)
    assert "typed at the terminal" not in audit and "terminal answer" not in audit


@needs_tmux
def test_new_ignores_the_replaced_sessions_late_session_start(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    async def scenario(h: Harness) -> None:
        hung = await h.say("__hang__")
        await h.until(lambda: h.store.get("anchor") == hung)
        old = h.agent.session_id or ""
        nxt = await h.say("next")                                # held: the agent is busy
        await h.say("!new")
        await h.until(started.is_set)
        await h.daemon.hooks.put(HookEvent("SessionStart", old))   # late, from the session being replaced
        await h.until(lambda: "ignored-other-session" in audit_text(h))   # handled while !new is pending
        release.set()
        await h.until(lambda: "echo: next" in h.texts(), timeout=30)
        assert h.agent.session_id != old
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["echo: next"] == nxt and dispatched(h).count(nxt) == 1
    run_with(tmp_path, scenario, lambda h: slow(monkeypatch, h, "new", started, release))


@needs_tmux
def test_ignored_hook_last_in_the_queue_still_releases_dispatch(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        stuck = await h.say("__nostop__")
        await h.until(lambda: h.store.get("anchor") == stuck)
        nxt = await h.say("next")
        await asyncio.sleep(0.5)
        # Queued together: this session's Stop, then a hook from another (e.g. retired) session.
        h.daemon.hooks.put_nowait(h.event("Stop", h.agent.session_id or "", None, "done"))
        h.daemon.hooks.put_nowait(HookEvent("UserPromptSubmit", "a-retired-session", prompt="x"))
        await h.until(lambda: "echo: next" in h.texts())
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["done"] == stuck and by_text["echo: next"] == nxt
    run_with(tmp_path, scenario)


@needs_tmux
def test_lost_stop_then_terminal_turn_never_takes_the_thread(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        stuck = await h.say("__nostop__")
        await h.until(lambda: h.store.get("anchor") == stuck)
        nxt = await h.say("next")
        await asyncio.sleep(1.0)
        assert nxt not in dispatched(h)                          # busy until the agent is seen idle
        sid = h.agent.session_id or ""
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, prompt="typed at the terminal"))
        await h.daemon.hooks.put(h.event("Stop", sid, last_assistant_message="terminal answer"))
        await h.until(lambda: "echo: next" in h.texts())
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["terminal answer"] is None
        notice = next(r for r in h.fake.sent
                      if r["text"].startswith("No reply to this message: admind did not"))
        assert notice["reply_to_message_id_hex"] == stuck
        assert by_text["echo: next"] == nxt
    run_with(tmp_path, scenario)


@needs_tmux
def test_terminal_prompt_never_takes_a_pending_anchor(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("warm up")
        await h.until(lambda: "echo: warm up" in h.texts())
        sid = h.agent.session_id or ""
        h.store.set("in_flight", "aa" * 32)
        h.store.set("in_flight_text", "from admind")
        await h.daemon.hooks.put(h.event("UserPromptSubmit", sid, prompt="typed at the terminal"))
        await h.daemon.hooks.put(h.event("Stop", sid, last_assistant_message="terminal answer"))
        await h.until(lambda: "terminal answer" in h.texts())
        row = next(r for r in h.fake.sent if r["text"] == "terminal answer")
        assert row["reply_to_message_id_hex"] is None
        assert h.store.get("anchor") is None and h.store.get("in_flight") == "aa" * 32
    run_with(tmp_path, scenario)


@needs_tmux
def test_late_stop_after_interrupt_never_takes_the_next_anchor(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        hung = await h.say("__hang__")
        await h.until(lambda: h.store.get("anchor") == hung)
        await h.say("!interrupt")
        await h.until(lambda: any(t.startswith("No reply to this message: interrupted") for t in h.texts()))
        # Hold the dispatch lock so "next" cannot be pasted (let alone confirmed) until the interrupted
        # turn's late Stop has been processed and its reply sent: the order is explicit, not timing.
        async with h.daemon.dispatch_lock:
            nxt = await h.say("next")
            await h.daemon.hooks.put(h.event("Stop", h.agent.session_id or "", None, "late"))
            await h.until(lambda: "late" in h.texts())
            assert "echo: next" not in h.texts()
        await h.until(lambda: "echo: next" in h.texts())
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["late"] is None and by_text["echo: next"] == nxt
    run_with(tmp_path, scenario)


@needs_tmux
def test_concurrent_flushes_dispatch_each_message_once(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("warm up")
        await h.until(lambda: "echo: warm up" in h.texts())
        h.daemon.held += [("aa" * 32, "x1"), ("bb" * 32, "x2")]
        await asyncio.gather(h.daemon.flush(), h.daemon.flush(), h.daemon.flush())
        await h.until(lambda: "echo: x2" in h.texts())
        records = [json.loads(line) for line in
                   (h.settings.state_dir / "audit.jsonl").read_text().splitlines()]
        dispatched = [r["message_id"] for r in records if r["kind"] == "dispatch"]
        assert dispatched.count("aa" * 32) == 1 and dispatched.count("bb" * 32) == 1
        by_text = {r["text"]: r["reply_to_message_id_hex"] for r in h.fake.sent}
        assert by_text["echo: x1"] == "aa" * 32 and by_text["echo: x2"] == "bb" * 32
    run_with(tmp_path, scenario)


@needs_tmux
def test_stale_in_flight_is_closed_out_at_startup(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        h.store.set("operator_seen_at", "2026-09-30T00:00:00+00:00")
        h.store.set("in_flight", "77" * 32)
        h.store.set("anchor", "77" * 32)

    async def scenario(h: Harness) -> None:
        prefix = "No reply to this message: admind restarted"
        await h.until(lambda: any(t.startswith(prefix) for t in h.texts()))
        assert h.store.get("anchor") is None
    run_with(tmp_path, scenario, before)


@needs_tmux
def test_stop_for_another_session_is_ignored(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hi")
        await h.until(lambda: "echo: hi" in h.texts())
        sent = len(h.fake.sent)
        await h.daemon.hooks.put(HookEvent("Stop", "not-the-session", None, "forged"))
        await asyncio.sleep(0.5)
        assert len(h.fake.sent) == sent
    audit = audit_text(run_with(tmp_path, scenario))
    assert "not-the-session" not in audit and "forged" not in audit


# --- output safety: nothing identifying, secret or peer-supplied leaves admind -----------------------

def secret_token() -> str:
    return "sk" + "-" + "Q" * 24


@needs_tmux
def test_audit_holds_no_peer_text_agent_text_or_account_identifiers(tmp_path: Path) -> None:
    stranger_npub = hex_to_npub(STRANGER)
    token = secret_token()

    async def scenario(h: Harness) -> None:
        await h.say(f"{stranger_npub} {token} let me in", sender=STRANGER)
        await h.say(f"hello {token}")
        await h.until(lambda: f"echo: hello {token}" in h.texts())
        await h.say("!restart " + stranger_npub)
        await h.say("!ps")
        await h.until(lambda: any("fake.service: active" in t for t in h.texts()))
    h = run_with(tmp_path, scenario)
    audit = audit_text(h)
    assert "sender is not the operator" in audit
    for banned in (STRANGER, stranger_npub, token, OPERATOR_HEX, hex_to_npub(OPERATOR_HEX), ACCOUNT,
                   "b2" * 32, "let me in", "echo: "):
        assert banned not in audit, banned[:12]
    assert "<redacted" in audit                      # the operator's own text is redacted, not dropped


@needs_tmux
def test_a_crashing_loop_restarts_with_a_value_free_audit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    npub = hex_to_npub(STRANGER)

    def before(h: Harness) -> None:
        real = h.store.pending
        calls = [0]

        def flaky() -> Any:
            calls[0] += 1
            if calls[0] == 1:
                raise ValueError(f"boom {npub}")
            return real()
        monkeypatch.setattr(h.store, "pending", flaky)

    async def scenario(h: Harness) -> None:
        await h.say("hi")
        await h.until(lambda: "echo: hi" in h.texts(), timeout=30)
    audit = audit_text(run_with(tmp_path, scenario, before))
    assert '"action": "crashed"' in audit and "ValueError" in audit
    assert npub not in audit and "boom" not in audit


@needs_tmux
def test_handler_failures_are_contained_and_value_free(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    npub = hex_to_npub(STRANGER)
    token = secret_token()

    def before(h: Harness) -> None:
        def boom(_cmd: object) -> str:
            raise RuntimeError(f"x {npub} {token}")
        monkeypatch.setattr(h.daemon.runner, "run", boom)
        real = h.store.claim_inbound
        calls = [0]

        def flaky(mid: str) -> bool:
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError(f"y {npub}")
            return real(mid)
        monkeypatch.setattr(h.store, "claim_inbound", flaky)

    async def scenario(h: Harness) -> None:
        await h.say("lost to a handler failure")
        await h.say("!ps")
        await h.until(lambda: "!ps failed: internal error" in h.texts())
        await h.say("still alive")
        await h.until(lambda: "echo: still alive" in h.texts(), timeout=30)
        assert not any(npub in t or token in t for t in h.texts())
    audit = audit_text(run_with(tmp_path, scenario, before))
    assert "RuntimeError" in audit and npub not in audit and token not in audit


@needs_tmux
def test_tmux_failure_detail_never_reaches_the_chat_or_audit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    npub = hex_to_npub(STRANGER)
    token = secret_token()

    def before(h: Harness) -> None:
        def fail() -> None:
            raise TmuxError(f"tmux send-keys failed: {npub} {token}")
        monkeypatch.setattr(h.agent, "interrupt", fail)

    async def scenario(h: Harness) -> None:
        await h.say("!interrupt")
        await h.until(lambda: any(t.startswith("!interrupt failed:") for t in h.texts()))
        assert not any(npub in t or token in t for t in h.texts())
    audit = audit_text(run_with(tmp_path, scenario, before))
    assert npub not in audit and token not in audit


@needs_tmux
def test_command_errors_and_control_characters_never_echo_values(tmp_path: Path) -> None:
    npub = hex_to_npub(STRANGER)
    token = secret_token()

    async def scenario(h: Harness) -> None:
        await h.say("!" + token)
        await h.say("!" + npub)
        await h.say("!ps \x1b[201~")
        await h.until(lambda: any("control characters" in t for t in h.texts()))
        await h.say("!restrat")
        await h.until(lambda: any(t.startswith("Unknown command !restrat") for t in h.texts()))
        for banned in (token, npub, "\x1b"):
            assert not any(banned in t for t in h.texts())
        assert sum(t.startswith("<redacted") for t in h.texts()) == 2
    audit = audit_text(run_with(tmp_path, scenario))
    assert token not in audit and npub not in audit


@needs_tmux
def test_peer_error_detail_is_never_audited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    npub = hex_to_npub(STRANGER)
    token = secret_token()

    def before(h: Harness) -> None:
        async def failing_info(account: str, group: str) -> Any:
            raise ControlError("wn-agent returned error unavailable", "unavailable", True,
                               detail=f"{token} {npub}")
        monkeypatch.setattr(h.daemon.client, "group_info", failing_info)

    async def scenario(h: Harness) -> None:
        await h.say("hi")
        await asyncio.sleep(1.2)                      # startup, the group loop and the message each checked
        assert h.fake.sent == []
    audit = audit_text(run_with(tmp_path, scenario, before))
    assert "group-check-failed" in audit and '"code": "unavailable"' in audit
    assert token not in audit and npub not in audit


@needs_tmux
def test_a_rejected_send_is_audited_without_peer_detail(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = secret_token()

    def before(h: Harness) -> None:
        async def rejected(*_a: Any) -> Any:
            raise ControlError("wn-agent returned error not_found", "not_found", False, detail=token)
        monkeypatch.setattr(h.daemon.client, "send_final", rejected)

    async def scenario(h: Harness) -> None:
        await h.say("hi")
        await h.until(lambda: '"action": "failed"' in audit_text(h))
    audit = audit_text(run_with(tmp_path, scenario, before))
    assert '"code": "not_found"' in audit and token not in audit


def test_supervised_restarts_with_bounded_growing_backoff(tmp_path: Path) -> None:
    npub = hex_to_npub(STRANGER)
    audit = Audit(tmp_path / "audit.jsonl")
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 8:
            raise asyncio.CancelledError

    async def crash() -> None:
        raise ValueError(f"nope {npub}")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(supervised("t", crash, audit, sleep=fake_sleep))
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    text = (tmp_path / "audit.jsonl").read_text()
    assert "ValueError" in text and npub not in text and "nope" not in text

    delays.clear()
    ticks = iter(range(0, 10_000, 100))               # every run "lived" 100s: healthy, backoff resets
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(supervised("t", crash, audit, sleep=fake_sleep, clock=lambda: float(next(ticks))))
    assert delays == [1] * 8


def test_a_returning_loop_is_restarted_not_spun(tmp_path: Path) -> None:
    audit = Audit(tmp_path / "audit.jsonl")
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError

    async def returns() -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(supervised("t", returns, audit, sleep=fake_sleep))
    assert delays == [1, 2, 4]


# --- alerts and transcripts: hostile files ---------------------------------------------------------------

def run_with_watchdog(path: Path, fn: Callable[[], Any]) -> Any:
    """Run `fn`; if it blocks on a FIFO at `path`, a helper thread unblocks it, and the test fails."""
    unblocked = threading.Event()

    def unblock() -> None:
        if not unblocked.wait(1.5):
            try:
                os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
    watchdog = threading.Thread(target=unblock, daemon=True)
    watchdog.start()
    started = time.monotonic()
    result = fn()
    unblocked.set()
    assert time.monotonic() - started < 1.0, "blocked on a FIFO"
    return result


def test_alert_scan_never_follows_symlinks_or_blocks_on_fifos(tmp_path: Path) -> None:
    from heterodyne.admind import alerts
    real = tmp_path / "outside.txt"
    real.write_text(json.dumps({"id": "b", "created_at": "t", "text": "from outside"}))
    d = tmp_path / "alerts"
    d.mkdir()
    (d / "b.json").symlink_to(real)
    os.mkfifo(d / "a.json")
    (d / "big.json").write_text(json.dumps({"id": "big", "created_at": "t", "text": "x" * 200_000}))
    (d / "ok.json").write_text(json.dumps({"id": "ok", "created_at": "t", "text": "fine"}))
    found = run_with_watchdog(d / "a.json", lambda: alerts.scan(d))
    by_name = dict(found)
    assert by_name["a"] is None and by_name["b"] is None and by_name["big"] is None
    ok = by_name["ok"]
    assert ok is not None and ok.text == "fine"


def test_alert_render_withholds_secrets_and_identifiers_and_escapes_controls() -> None:
    from heterodyne.admind import alerts
    npub = hex_to_npub(STRANGER)
    token = secret_token()
    for bad in (f"see {token}", f"member {npub}", f"id {'ab' * 32}"):
        out = alerts.render("a1", alerts.Alert(id="a1", created_at="t", text=bad), 4000)
        assert token not in out and npub not in out and "ab" * 32 not in out
        assert "withheld" in out
    out = alerts.render("a1", alerts.Alert(id="a1", created_at=npub, text="x"), 4000)
    assert npub not in out and "withheld" in out
    out = alerts.render("a1", alerts.Alert(id="a1", created_at="t", text="a\x1b[2Jb\nc\td"), 4000)
    assert "\x1b" not in out and "\\x1b" in out and "\n" in out and "\t" in out
    assert npub not in alerts.render(npub[:60], None, 4000)
    assert npub not in alerts.render("n" * 64, None, 4000) and len(alerts.render("n" * 200, None, 4000)) < 200


def test_transcript_fallback_never_follows_symlinks_or_blocks_on_fifos(tmp_path: Path) -> None:
    from heterodyne.admind.hook import last_assistant_text, reply_text
    real = tmp_path / "real.txt"
    real.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "secret reply"}]}}) + "\n")
    link = tmp_path / "S.jsonl"
    link.symlink_to(real)
    assert last_assistant_text(link) == ""
    assert reply_text(HookEvent("Stop", "S", str(link), None)) == ""
    fifo = tmp_path / "F.jsonl"
    os.mkfifo(fifo)
    assert run_with_watchdog(fifo, lambda: reply_text(HookEvent("Stop", "F", str(fifo), None))) == ""


def test_transcript_fallback_reads_only_a_bounded_tail(tmp_path: Path) -> None:
    from heterodyne.admind.hook import last_assistant_text
    path = tmp_path / "S.jsonl"
    old = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "old " * 500}]}})
    new = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "newest"}]}})
    path.write_text("\n".join([old] * 50 + [new]) + "\n")
    assert last_assistant_text(path, limit=1000) == "newest"


# --- `admind run` wiring ------------------------------------------------------------------------------

class StubWn:
    """The WnAgent surface cli._serve uses, over the in-process fake (no child process)."""

    def __init__(self, sock: Path, start_error: Exception | None = None) -> None:
        self.socket_path = sock
        self.start_error = start_error
        self.stopped = False

    def prepare(self) -> None:
        pass

    def token(self) -> str:
        return "test-token"

    async def start(self, client: ControlClient) -> None:
        if self.start_error is not None:
            raise self.start_error

    async def account(self, client: ControlClient) -> str:
        return ACCOUNT

    async def supervise(self, client: ControlClient) -> None:
        await asyncio.Event().wait()

    async def stop(self) -> None:
        self.stopped = True

    def alive(self) -> bool:
        return True


def test_run_refuses_an_uninitialised_install(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from heterodyne.admind import cli
    s = make_settings(tmp_path)
    code = cli.run(s, Store(s.state_dir / "admind.db"), Audit(s.state_dir / "audit.jsonl"))
    err = capsys.readouterr().err
    assert code == cli.EX_CONFIG and "admind init" in err and "Traceback" not in err


@needs_tmux
def test_serve_runs_until_sigterm_then_stops_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import signal

    from heterodyne.admind import cli
    h = Harness(tmp_path)
    stub = StubWn(tmp_path / "wn.sock")
    monkeypatch.setattr(cli, "WnAgent", lambda *_a, **_k: stub)
    monkeypatch.setattr(cli, "TMUX_SOCKET", h.tmux.socket_name)

    async def body() -> int:
        await h.fake.start()
        task = asyncio.create_task(cli._serve(h.settings, h.store, h.audit, "b2" * 32, Services()))
        try:
            await h.fake.wait_subscribed(10)
            os.kill(os.getpid(), signal.SIGTERM)
            return await asyncio.wait_for(task, 10)
        finally:
            await h.fake.stop()
    try:
        code = asyncio.run(body())
    finally:
        drop_tmux(h)
    assert code == 0 and stub.stopped
    audit = audit_text(h)
    assert '"action": "stop"' in audit and "b2" * 32 not in audit and ACCOUNT not in audit


@pytest.mark.parametrize("make", [
    lambda npub: __import__("heterodyne.admind.wnagent", fromlist=["x"]).WnAgentError(f"bad {npub}"),
    lambda npub: ControlError(f"bad {npub}", "socket_io", True),
])
def test_serve_startup_failure_is_a_value_free_line(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        make: Callable[[str], Exception]) -> None:
    from heterodyne.admind import cli
    npub = hex_to_npub(STRANGER)
    s = make_settings(tmp_path)
    store = Store(s.state_dir / "admind.db")
    stub = StubWn(tmp_path / "wn.sock", start_error=make(npub))
    monkeypatch.setattr(cli, "WnAgent", lambda *_a, **_k: stub)
    code = asyncio.run(cli._serve(s, store, Audit(s.state_dir / "audit.jsonl"), "b2" * 32, Services()))
    out = capsys.readouterr()
    assert code == 1 and stub.stopped
    assert npub not in out.out + out.err and "Traceback" not in out.err
