"""Review round 2 for the admind daemon: authorisation re-checks, uncertain paste, dispatch generations,
late Stops and READY atomicity. Same rules as round 1: fakes, tmp_path and explicit barriers only; no
sleeps, no real wn-agent, claude, systemctl, network or ~/.claude.
"""

import asyncio
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_admind_r1 import FakeTmux, Unit, inbound, run

from heterodyne.admind.daemon import UNCERTAIN
from heterodyne.admind.hook import HookEvent
from heterodyne.tmux import Tmux, TmuxError

# --- 2. authorisation is re-checked after every await and under the lock, before a side effect ---

def test_a_membership_event_during_verification_stops_a_restart_from_running(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    restarts: list[str] = []
    monkeypatch.setattr(u.services, "restart", lambda unit: (restarts.append(unit), (True, ""))[1])
    in_check = asyncio.Event()
    release = asyncio.Event()

    async def slow_info(account: str, group: str) -> Any:
        in_check.set()
        await release.wait()                    # the membership event is handled while we wait here
        return SimpleNamespace(group_id_hex=group, member_count=2)
    monkeypatch.setattr(u.client, "group_info", slow_info)

    async def scenario() -> None:
        task = asyncio.create_task(u.daemon.on_message(inbound("!restart fake.service", u.mid())))
        await in_check.wait()
        u.daemon.latch("group membership changed (member_added)")
        release.set()
        await task
    run(scenario())
    assert restarts == []
    assert '"reason": "no longer authorised"' in u.audit_text()


@pytest.mark.parametrize("command", ["!interrupt", "!new"])
def test_a_latch_while_waiting_for_the_dispatch_lock_stops_interrupt_and_new(
        tmp_path: Path, command: str) -> None:
    u = Unit(tmp_path)
    sessions_before = set(u.tmux.sessions)

    async def scenario() -> None:
        await u.daemon.dispatch_lock.acquire()
        task = asyncio.create_task(u.daemon.handle(u.mid(), command))
        await asyncio.sleep(0)                  # the command is now parked on the lock
        u.daemon.latch("group membership changed (member_added)")
        u.daemon.dispatch_lock.release()
        await task
    run(scenario())
    assert u.tmux.keys == [] and u.tmux.sessions == sessions_before
    assert u.store.get("agent_session") == "S1" and "S1" not in u.daemon.retired


# --- 3. a paste that times out after delivering text is never retried ---

class PaneTmux(FakeTmux):
    """The real Tmux.paste over a scripted tmux: `paste-buffer` puts the text in the pane, then fails."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__()
        self.pane = ""
        self.buffers: dict[str, str] = {}
        self.fail_paste_buffer: BaseException | None = None
        self.real = Tmux("hz-test-never-started")
        monkeypatch.setattr(self.real, "_run", self._run)
        monkeypatch.setattr("heterodyne.tmux.time.sleep", lambda _s: None)

    def _run(self, *args: str, data: bytes | None = None, check: bool = True) -> Any:
        if args[0] == "load-buffer":
            assert data is not None
            self.buffers[args[2]] = data.decode()
        elif args[0] == "paste-buffer":
            self.pane += self.buffers[args[args.index("-b") + 1]]       # delivered ...
            if self.fail_paste_buffer is not None:
                raise self.fail_paste_buffer                            # ... and then it times out
        elif args[0] == "send-keys":
            self.pane += "<Enter>"
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def paste(self, name: str, text: str) -> None:
        self.real.paste(name, text)


def test_a_paste_buffer_timeout_after_delivery_is_not_retried_and_not_duplicated(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    pane = PaneTmux(monkeypatch)
    u.tmux.paste = pane.paste                       # type: ignore[method-assign]
    pane.fail_paste_buffer = subprocess.TimeoutExpired("tmux", 15)

    async def scenario() -> None:
        a = await u.say("A")
        pane.fail_paste_buffer = None
        await u.daemon.flush()
        await u.daemon.flush()
        assert pane.pane == "A"                     # once; never "AA"
        assert u.daemon.held == []
        assert (f"No reply to this message: {UNCERTAIN}.", a) in [(t, r) for _, t, r in u.outbox()]
    run(scenario())


# --- 4. cleanup after a definite send failure applies only to the dispatch that failed ---

def test_a_send_failure_does_not_clear_the_busy_state_of_a_newer_turn(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def blocking_paste(name: str, text: str) -> None:
        started.set()
        assert release.wait(10), "test never released the paste"
        raise TmuxError("load-buffer failed")
    u.tmux.paste = blocking_paste                   # type: ignore[method-assign]

    async def scenario() -> None:
        sending = asyncio.create_task(u.say("prompt A"))
        assert await asyncio.to_thread(started.wait, 10)        # A's paste is in its worker thread
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="typed at the terminal"))
        busy = u.store.get("busy")
        assert busy is not None
        release.set()
        a = await sending
        assert u.store.get("busy") == busy                      # the terminal turn is still running
        assert u.daemon.held == [(a, "prompt A")] and u.store.get("in_flight") is None
        assert u.store.inbound_with_status("received") == [a]   # A was not delivered: kept, not lost
        await u.daemon.flush()
        assert u.tmux.pasted == []                              # but nothing is dispatched into a busy agent
    run(scenario())
