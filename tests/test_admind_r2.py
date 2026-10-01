"""Review round 2 for the admind daemon: authorisation re-checks, uncertain paste, dispatch generations,
late Stops and READY atomicity. Same rules as round 1: fakes, tmp_path and explicit barriers only; no
sleeps, no real wn-agent, claude, systemctl, network or ~/.claude.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_admind_r1 import Unit, inbound, run

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
