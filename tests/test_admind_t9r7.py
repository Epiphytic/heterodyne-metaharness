"""Task 9 review round 7: the hold on a lost hook event is established first and cannot be undone by a
failing audit write, store write or notice. The real server handler, hook_loop, Store transactions and
dispatcher are used; failures are injected once, deterministically. Fakes and tmp_path only; no real
wn-agent, claude, systemctl, network or ~/.claude.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_admind_r1 import Unit, run
from test_admind_t9r5 import GatedRead, answer, arm, frame, send, serving, wait_until

from heterodyne.admind.hook import HookEvent, HookServer
from heterodyne.admind.store import Store

HOOK_LOST_NOTICE = ("admind lost an agent hook event; new messages are held. "
                    "When the agent is idle, send !interrupt (or !new) to resume.")


def notices(u: Unit) -> list[str]:
    return [k for k, t, _ in u.outbox() if t == HOOK_LOST_NOTICE]


def settled(server: HookServer, accepted: int = 1) -> bool:
    """The connection was accepted and its slot completed (the count alone is zero before the accept)."""
    return server.accepted >= accepted and server.pending_hooks == 0


def fail_audit_once(u: Unit, match: Callable[[dict[str, Any]], bool]) -> list[int]:
    """The first audit write whose fields satisfy `match` raises, as a failing audit filesystem would."""
    real = u.audit.write
    hits: list[int] = []

    def write(kind: str, **fields: object) -> None:
        if not hits and match({"kind": kind, **fields}):
            hits.append(1)
            raise OSError("audit filesystem unavailable")
        real(kind, **fields)
    u.audit.write = write       # type: ignore[method-assign]
    return hits


async def held_c(u: Unit, held: int = 1) -> None:
    await u.say("job C")
    assert u.tmux.pasted == [] and len(u.daemon.held) == held


def test_an_audit_failure_on_a_dropped_frame_still_holds_dispatch(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)
    hits = fail_audit_once(u, lambda f: f.get("result") == "dropped")

    async def scenario() -> None:
        async with serving(u) as (server, sock):
            _r, w = await send(sock, b"{broken\n")
            await asyncio.wait_for(wait_until(lambda: settled(server)), 10)
            w.close()
            assert hits
            await held_c(u)
        assert len(notices(u)) == 1
    run(scenario())


def test_an_audit_failure_on_a_failed_event_still_holds_dispatch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    hits = fail_audit_once(u, lambda f: f.get("action") == "hook-failed")

    def boom(ev: HookEvent, arrival: int | None = None) -> None:
        raise RuntimeError("processing failed")
    monkeypatch.setattr(u.daemon, "on_prompt", boom)

    async def scenario() -> None:
        async with serving(u) as (_server, sock):
            r, w = await send(sock, frame("UserPromptSubmit", prompt="typed"))
            assert await answer(r) == b"err\n"
            w.close()
            assert hits
            await held_c(u)
        assert len(notices(u)) == 1
    run(scenario())


def test_an_audit_failure_on_a_deadline_still_holds_dispatch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    u.daemon.hook_deadline = 0.05
    hits = fail_audit_once(u, lambda f: f.get("action") == "hook-deadline")
    read = GatedRead("late")
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", read)

    async def scenario() -> None:
        await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        async with serving(u) as (_server, sock):
            r, w = await send(sock, frame("Stop"))
            assert await asyncio.to_thread(read.started.wait, 10)
            assert await answer(r) == b"err\n"
            read.release.set()
            w.close()
            assert hits
            await u.say("job C")
        assert u.tmux.pasted == ["job A"]       # C was not pasted into the turn the lost Stop may have ended
        assert len(notices(u)) == 1
    run(scenario())


def test_a_failed_notice_does_not_roll_back_the_hold_and_is_retried_next_episode(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    real = Store.enqueue
    failed: list[str] = []

    def enqueue(self: Store, key: str, text: str, reply_to: str | None) -> bool:
        if text == HOOK_LOST_NOTICE and not failed:
            failed.append(key)
            raise OSError("outbox write failed")
        return real(self, key, text, reply_to)
    monkeypatch.setattr(Store, "enqueue", enqueue)

    async def scenario() -> None:
        async with serving(u) as (server, sock):
            _r, w = await send(sock, b"{broken\n")
            await asyncio.wait_for(wait_until(lambda: settled(server)), 10)
            w.close()
            assert failed and not notices(u)
            assert u.store.get("busy") is not None              # persisted despite the failed notice
            await held_c(u)
            _r2, w2 = await send(sock, b"{broken again\n")      # the next lost event: same episode, retried
            await asyncio.wait_for(wait_until(lambda: settled(server, 2) and bool(notices(u))), 10)
            w2.close()
            assert len(notices(u)) == 1
            await held_c(u, 2)
    run(scenario())


def test_a_failed_store_write_keeps_the_in_memory_hold_and_interrupt_releases_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    calls: list[int] = []
    real = u.daemon.set_busy

    def set_busy() -> None:
        if not calls:
            calls.append(1)
            raise OSError("database is locked")
        real()
    monkeypatch.setattr(u.daemon, "set_busy", set_busy)

    async def scenario() -> None:
        async with serving(u) as (server, sock):
            _r, w = await send(sock, b"{broken\n")
            await asyncio.wait_for(wait_until(lambda: settled(server)), 10)
            w.close()
            assert calls and u.store.get("busy") is None        # nothing persisted ...
            await held_c(u)                                     # ... yet nothing is pasted
            await u.say("!interrupt")
            await u.daemon.flush()
        assert u.tmux.pasted[-1:] == ["job C"] and u.daemon.held == []
    run(scenario())


# --- the sweep: other safeguards that shared a transaction with, or followed, an audit or a notice ---

def test_an_audit_failure_cannot_roll_back_the_busy_period_or_anchor_of_a_prompt(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)
    hits = fail_audit_once(u, lambda f: f.get("action") == "prompt-submitted")

    async def scenario() -> None:
        mid = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        assert hits
        assert u.store.get("busy") is not None and u.store.get("anchor") == mid
    run(scenario())


def test_a_failed_abandon_notice_cannot_roll_back_the_busy_period_of_a_new_prompt(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    real = Store.enqueue

    def enqueue(self: Store, key: str, text: str, reply_to: str | None) -> bool:
        if key.startswith("abandoned:"):
            raise OSError("outbox write failed")
        return real(self, key, text, reply_to)

    async def scenario() -> None:
        await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))     # A anchored
        u.store.delete("busy")
        monkeypatch.setattr(Store, "enqueue", enqueue)
        with pytest.raises(OSError):
            await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="typed at the terminal"))
        assert u.store.get("busy") is not None          # the new turn is running, whatever the notice did
    run(scenario())


def test_an_audit_failure_cannot_keep_an_uncertain_paste_reserved(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    hits = fail_audit_once(u, lambda f: f.get("action") == "send-uncertain")

    def paste(name: str, text: str) -> None:
        raise RuntimeError("paste outcome unknown")
    u.tmux.paste = paste       # type: ignore[method-assign]

    async def scenario() -> None:
        await u.say("job A")
        assert hits
        assert u.store.get("in_flight") is None and u.store.get("busy") is not None
    run(scenario())
