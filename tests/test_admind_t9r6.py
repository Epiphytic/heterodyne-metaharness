"""Task 9 review round 6: a hook event that is lost after it was accepted (deadline, failure, dropped frame)
holds dispatch, because it might have been a turn start. The review's interleaving is reproduced with the
real hook server, hook_loop, routing and dispatcher. Fakes, tmp_path and explicit barriers only; no real
wn-agent, claude, systemctl, network or ~/.claude.
"""

import asyncio
from pathlib import Path

import pytest
from test_admind_r1 import Unit, run
from test_admind_t9r3 import Gate
from test_admind_t9r5 import GatedRead, answer, arm, frame, send, serving, spin, wait_until

from heterodyne.admind.hook import HookEvent, HookServer
from heterodyne.tmux import TmuxError

HOOK_LOST_NOTICE = ("admind lost an agent hook event; new messages are held. "
                    "When the agent is idle, send !interrupt (or !new) to resume.")


def notices(u: Unit) -> list[str]:
    return [k for k, t, _ in u.outbox() if t == HOOK_LOST_NOTICE]


def lost(u: Unit, server: HookServer) -> bool:
    return u.store.get("busy") is not None and server.pending_hooks == 0


async def elapse(seconds: float) -> None:
    """Let real time pass without sleeping: yield to the loop until the clock reaches the mark."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        await asyncio.sleep(0)


def test_a_prompt_expired_behind_a_dispatch_is_not_followed_by_a_retry_into_its_turn(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)
    u.daemon.hook_deadline = 0.05          # shortened (production: 30 s)
    u.daemon.hook_lock_wait = 0.05         # shortened (production: 120 s)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        gate = Gate(loop)

        def failing_paste(name: str, text: str) -> None:
            gate.wait()
            raise TmuxError("tmux paste failed")
        u.tmux.paste = failing_paste       # type: ignore[method-assign]
        async with serving(u) as (_server, sock):
            a = asyncio.create_task(u.say("job A"))                 # holds dispatch_lock, send pending
            await asyncio.wait_for(gate.entered.wait(), 10)
            rb, wb = await send(sock, frame("UserPromptSubmit", prompt="typed at the terminal"))
            assert await answer(rb) == b"err\n"                     # B's processing expired
            gate.release.set()                                      # A's send now fails definitively
            await asyncio.wait_for(a, 10)
            wb.close()
            await spin()
        assert u.tmux.pasted == []                                  # A was not retried into B's turn
        assert u.store.get("busy") is not None                      # the hold stands
        assert len(u.daemon.held) == 1
        assert len(notices(u)) == 1
        assert "hook-lost-hold" in u.audit_text()
    run(scenario())


def test_waiting_for_the_lock_does_not_count_against_the_processing_deadline(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)
    u.daemon.hook_deadline = 0.2
    u.daemon.hook_lock_wait = 30.0

    async def scenario() -> None:
        async with serving(u) as (_server, sock):
            async with u.daemon.dispatch_lock:
                r, w = await send(sock, frame("UserPromptSubmit", prompt="typed"))
                await elapse(0.3)                                   # longer than the processing deadline
            assert await answer(r) == b"ok\n"
            w.close()
        assert u.store.get("busy") is not None and not notices(u)
    run(scenario())


def test_a_processing_failure_holds_dispatch_and_posts_one_notice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)

    def boom(ev: HookEvent, arrival: int | None = None) -> None:
        raise RuntimeError("secret detail that must not leak")
    monkeypatch.setattr(u.daemon, "on_prompt", boom)

    async def scenario() -> None:
        async with serving(u) as (_server, sock):
            for _ in range(2):                                      # two lost events, one episode
                r, w = await send(sock, frame("UserPromptSubmit", prompt="typed"))
                assert await answer(r) == b"err\n"
                w.close()
        assert u.store.get("busy") is not None
        assert len(notices(u)) == 1
        assert "secret detail" not in u.audit_text()
        await u.say("job")
        assert u.tmux.pasted == [] and len(u.daemon.held) == 1
    run(scenario())


def test_a_stop_that_expires_in_extraction_holds_dispatch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    u.daemon.hook_deadline = 0.05
    read = GatedRead("late")
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", read)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        assert u.store.get("anchor") == a
        async with serving(u) as (_server, sock):
            r, w = await send(sock, frame("Stop"))
            assert await asyncio.to_thread(read.started.wait, 10)
            assert await answer(r) == b"err\n"
            read.release.set()
            w.close()
        assert u.store.get("busy") is not None and len(notices(u)) == 1
        await u.say("job B")
        assert u.tmux.pasted == ["job A"]
    run(scenario())


def test_a_stale_event_that_fails_holds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("never reached for a stale launch")
    monkeypatch.setattr(u.daemon, "on_prompt", boom)

    async def scenario() -> None:
        async with serving(u) as (_server, sock):
            old = frame("UserPromptSubmit", prompt="x").replace(b'"ab' * 1, b'"cd', 1)
            r, w = await send(sock, old)                            # another launch's nonce
            assert await answer(r) == b"ok\n"
            w.close()
        assert u.store.get("busy") is None and not notices(u)
    run(scenario())


def test_a_dropped_frame_holds_dispatch_and_interrupt_releases_it(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)

    async def scenario() -> None:
        async with serving(u) as (server, sock):
            r, w = await send(sock, b"this is not json\n")
            await asyncio.wait_for(wait_until(lambda: lost(u, server)), 10)
            assert u.store.get("busy") is not None and len(notices(u)) == 1
            assert "hook-lost-hold" in u.audit_text()
            await u.say("job A")
            assert u.tmux.pasted == [] and len(u.daemon.held) == 1  # held
            await u.say("!interrupt")
            await u.daemon.flush()
            assert u.store.get("hook_lost") is None
            assert u.tmux.pasted == ["job A"]                       # released: the held message went
            w.close()
            del r
    run(scenario())


def test_a_current_stop_releases_the_hold(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)

    async def scenario() -> None:
        async with serving(u) as (server, sock):
            _r, w = await send(sock, b"{broken\n")
            await asyncio.wait_for(wait_until(lambda: lost(u, server)), 10)
            w.close()
            assert u.store.get("busy") is not None
            r2, w2 = await send(sock, frame("Stop", last_assistant_message="done"))
            assert await answer(r2) == b"ok\n"
            w2.close()
            assert u.store.get("busy") is None and u.store.get("hook_lost") is None
    run(scenario())
