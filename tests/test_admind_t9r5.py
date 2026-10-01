"""Task 9 review round 5, code fixes: a hook's session and launch are decided under the turn-state lock,
the dispatch gate counts accepted hooks that are still waiting in the sequencer, a prompt accepted before
its turn was released is ignored (arrival-index floor), and hook processing and transcript extraction have
deadlines. The review's interleavings are reproduced with the real hook server, hook_loop, Stop handler
and dispatcher. Fakes, tmp_path and explicit barriers only (futures, threading events); no sleeps, no
real wn-agent, claude, systemctl, network or ~/.claude.
"""

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from test_admind_r1 import Unit, run
from test_admind_t9r3 import Gate

from heterodyne.admind.hook import HookEvent, HookServer

NONCE = "ab" * 16


def arm(u: Unit) -> None:
    """Give the Unit's current launch a nonce, ready for it, so routed events validate as current."""
    u.store.set("launch_nonce", NONCE)
    u.daemon.ready_nonce = NONCE
    u.daemon.ready.set()


def frame(event: str, **extra: object) -> bytes:
    body = {"hook_event_name": event, "session_id": "S1", "launch": NONCE, **extra}
    return json.dumps(body).encode() + b"\n"


@contextlib.asynccontextmanager
async def serving(u: Unit) -> AsyncIterator[tuple[HookServer, str]]:
    """The real server (wired to the daemon where the daemon supports it) and the real hook_loop."""
    path = u.settings.state_dir / "hook.sock"
    make = getattr(u.daemon, "make_server", None)
    server = make(path) if make is not None else HookServer(path, u.daemon.hooks, u.audit)
    await server.start()
    loop_task = asyncio.create_task(u.daemon.hook_loop())
    try:
        yield server, str(path)
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await loop_task
        await server.close()


async def send(sock: str, data: bytes) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(sock)
    writer.write(data)
    await writer.drain()
    return reader, writer


async def answer(reader: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(reader.readline(), 10)


async def spin(times: int = 40) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


def replies(u: Unit) -> list[tuple[str, str | None]]:
    return [(t, r) for k, t, r in u.outbox() if k.startswith("reply:")]


class GatedRead:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, ev: HookEvent, fallback: bool = True) -> str:
        self.calls += 1
        self.started.set()
        assert self.release.wait(10), "test never released the read"
        return self.text


# --- 1. validation under the lock (R5 #1) ---

def test_an_old_session_start_that_waited_for_the_lock_cannot_ready_the_replacement(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        await u.daemon.start_agent(relaunch=True)                   # launch A (same session, resumed)
        old_nonce = u.agent.launch_nonce
        assert old_nonce is not None and not u.daemon.ready.is_set()
        loop = asyncio.get_running_loop()
        gate, state = Gate(loop), {"dead": True}

        def pane_dead(name: str) -> bool:
            if state["dead"]:
                state["dead"] = False
                gate.wait()
                return True                                          # supervision finds the pane dead
            return False
        u.tmux.pane_dead = pane_dead                                # type: ignore[method-assign]
        check = asyncio.create_task(u.daemon.check_agent())          # holds the lock, awaiting alive()
        await asyncio.wait_for(gate.entered.wait(), 10)
        old = HookEvent("SessionStart", "S1", source="startup", launch=old_nonce)
        hook = asyncio.create_task(u.daemon.route_hook(old))        # passes any early check, waits
        await spin()
        assert not hook.done()
        gate.release.set()
        await asyncio.wait_for(check, 10)                           # the relaunch: same session, new nonce
        await asyncio.wait_for(hook, 10)
        assert u.agent.launch_nonce != old_nonce and u.agent.session_id == "S1"
        assert not u.daemon.ready.is_set()                          # the replacement never reported in
        assert u.store.get("launches_without_start") == "2"         # two launches, none reported in
        assert "ignored-stale-launch" in u.audit_text()
    run(scenario())


def test_mark_ready_records_the_nonce_the_event_carried(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.store.set("launch_nonce", NONCE)
    u.daemon.ready.clear()
    u.daemon.mark_ready("cd" * 16)                                  # another launch's nonce
    assert not u.daemon.ready.is_set()
    u.daemon.mark_ready(NONCE)
    assert u.daemon.ready.is_set() and u.daemon.ready_nonce == NONCE


def test_a_stale_stop_that_waited_for_the_lock_posts_only_its_own_text(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        stop = HookEvent("Stop", "S1", launch=NONCE, last_assistant_message="old launch's reply")
        async with u.daemon.dispatch_lock:                          # supervision is mid-relaunch
            task = asyncio.create_task(u.daemon.route_hook(stop))
            await spin()
            u.store.set("launch_nonce", "cd" * 16)                  # the replacement's nonce
        await asyncio.wait_for(task, 10)
        assert u.store.get("anchor") == a and u.store.get("busy") is not None   # state untouched
        assert replies(u) == [("old launch's reply", None)]
    run(scenario())


# --- 2. the dispatch gate counts accepted hooks (R5 #2) ---

def test_a_held_message_is_not_pasted_before_an_accepted_prompt_behind_a_slow_stop(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    read = GatedRead("A's reply")
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", read)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        assert u.store.get("anchor") == a
        c = await u.say("job C")                                    # held behind A's busy turn
        assert u.daemon.held == [(c, "job C")]
        async with serving(u) as (_server, sock):
            ra, wa = await send(sock, frame("Stop"))                # A's Stop: slow transcript fallback
            assert await asyncio.to_thread(read.started.wait, 10)
            rb, wb = await send(sock, frame("UserPromptSubmit", prompt="typed at the terminal"))
            await spin()                                            # B's frame is complete, behind A
            read.release.set()                                      # A clears busy ...
            assert await answer(ra) == b"ok\n"
            await spin()
            assert u.tmux.pasted == ["job A"]                       # ... but C waits for B
            assert await answer(rb) == b"ok\n"
            await spin()
            assert u.tmux.pasted == ["job A"]                       # B set busy: C stays held
            assert u.store.get("busy") is not None and u.daemon.held == [(c, "job C")]
            for w in (wa, wb):
                w.close()
    run(scenario())


def test_the_held_message_is_pasted_when_the_last_accepted_hook_is_done(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        assert u.store.get("anchor") == a
        c = await u.say("job C")
        async with serving(u) as (server, sock):
            r, w = await send(sock, frame("Stop", last_assistant_message="done"))
            assert await answer(r) == b"ok\n"
            await asyncio.wait_for(wait_until(lambda: u.tmux.pasted == ["job A", "job C"]), 10)
            assert server.pending_hooks == 0 and u.store.get("in_flight") == c
            w.close()
    run(scenario())


async def wait_until(pred: Any) -> None:
    while not pred():
        await asyncio.sleep(0)


def test_the_server_counts_a_connection_from_accept_until_its_slot_completes(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    idle: list[int] = []

    async def scenario() -> None:
        server = HookServer(tmp_path / "h.sock", u.daemon.hooks, u.audit, lambda: idle.append(1))
        await server.start()
        try:
            r1, w1 = await asyncio.open_unix_connection(str(tmp_path / "h.sock"))
            r2, w2 = await asyncio.open_unix_connection(str(tmp_path / "h.sock"))
            await spin()
            assert server.accepted == 2 and server.pending_hooks == 2   # counted before any frame arrived
            w1.write(frame("Stop", arrival=0))                       # a frame cannot choose its own index
            await w1.drain()
            first = await asyncio.wait_for(u.daemon.hooks.get(), 10)
            assert first.arrival == 1                                # type: ignore[union-attr]
            first.done.set_result(True)                              # type: ignore[union-attr]
            assert await answer(r1) == b"ok\n"
            await spin()
            assert server.pending_hooks == 1 and idle == []          # the second is still on its way
            w2.close()                                               # EOF: dropped
            await spin()
            assert server.pending_hooks == 0 and idle == [1]
            w1.close()
        finally:
            await server.close()
    run(scenario())


# --- 3. the arrival-index floor (R5 #3) ---

async def interrupt_with_prompt_behind(u: Unit, sock: str, gate: Gate, before: bytes | None) -> None:
    """A is dispatched (and possibly stopped); `!interrupt` holds the lock; A's prompt is accepted and
    waits; the Escape succeeds and releases A."""
    real = u.tmux.send_key

    def send_key(name: str, key: str) -> None:
        gate.wait()
        real(name, key)
    u.tmux.send_key = send_key                                      # type: ignore[method-assign]
    if before is not None:
        r, w = await send(sock, before)
        assert await answer(r) == b"ok\n"
        w.close()
    interrupt = asyncio.create_task(u.say("!interrupt"))
    await asyncio.wait_for(gate.entered.wait(), 10)                 # the Escape is in flight, lock held
    r, w = await send(sock, frame("UserPromptSubmit", prompt="job A"))
    await spin()
    gate.release.set()
    await asyncio.wait_for(interrupt, 10)
    assert await answer(r) == b"ok\n"
    w.close()


def test_a_prompt_that_waited_behind_interrupt_does_not_restore_busy(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)

    async def scenario() -> None:
        await u.say("job A")
        async with serving(u) as (_server, sock):
            await interrupt_with_prompt_behind(u, sock, Gate(asyncio.get_running_loop()), None)
            assert u.store.get("in_flight") is None and u.store.get("busy") is None
            assert u.store.get("anchor") is None
            assert "stale-prompt" in u.audit_text()
            c = await u.say("job C")                                # dispatches normally
            assert u.tmux.pasted == ["job A", "job C"] and u.store.get("in_flight") == c
            await spin()
            r, w = await send(sock, frame("UserPromptSubmit", prompt="job C"))      # C's own prompt counts
            assert await answer(r) == b"ok\n"
            assert u.store.get("anchor") == c
            w.close()
    run(scenario())


def test_a_prompt_for_a_stopped_then_released_reservation_is_ignored(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    arm(u)

    async def scenario() -> None:
        await u.say("job A")
        async with serving(u) as (_server, sock):
            # A's Stop is processed first (unanchored: the reservation is stopped, nothing released) ...
            stop = frame("Stop", last_assistant_message="A's reply")
            await interrupt_with_prompt_behind(u, sock, Gate(asyncio.get_running_loop()), stop)
            # ... then !interrupt released the reservation (and its stopped marker) before A's prompt ran.
            assert u.store.get("in_flight") is None and u.store.get("busy") is None
            assert u.store.get("stopped_flight") is None
            assert "stale-prompt" in u.audit_text() and "prompt-submitted" not in u.audit_text()
            c = await u.say("job C")
            assert u.tmux.pasted == ["job A", "job C"] and u.store.get("in_flight") == c
    run(scenario())


def test_a_stop_accepted_before_the_release_posts_only_its_own_text_and_changes_no_state(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    read = GatedRead("must not be used")
    read.release.set()
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", read)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        assert u.store.get("anchor") == a
        gate = Gate(asyncio.get_running_loop())
        real = u.tmux.send_key

        def send_key(name: str, key: str) -> None:
            gate.wait()
            real(name, key)
        u.tmux.send_key = send_key                                  # type: ignore[method-assign]
        async with serving(u) as (_server, sock):
            interrupt = asyncio.create_task(u.say("!interrupt"))
            await asyncio.wait_for(gate.entered.wait(), 10)
            r1, w1 = await send(sock, frame("Stop"))                # textless: no transcript fallback
            await spin()
            gate.release.set()
            await asyncio.wait_for(interrupt, 10)
            assert await answer(r1) == b"ok\n"
            c = await u.say("job C")                                # a new turn begins ...
            r2, w2 = await send(sock, frame("Stop", last_assistant_message="late"))
            assert await answer(r2) == b"ok\n"                      # ... its own Stop has a newer index
            assert read.calls == 0 and "stale-stop-unrecoverable" in u.audit_text()
            assert u.store.get("in_flight") == c
            for w in (w1, w2):
                w.close()
    run(scenario())


# --- 4. deadlines (R5 #4) ---

def test_a_stalled_transcript_read_times_out_and_releases_the_slot(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    read = GatedRead("late text")
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", read)
    u.daemon.extract_timeout = 0.05

    async def scenario() -> None:
        async with serving(u) as (_server, sock):
            r1, w1 = await send(sock, frame("Stop"))                # no text: fallback, which stalls
            assert await asyncio.to_thread(read.started.wait, 10)
            assert await answer(r1) == b"ok\n"                      # the slot was released at the deadline
            assert replies(u) == [] and "reply-extraction-timeout" in u.audit_text()
            r2, w2 = await send(sock, frame("Stop"))                # the first thread is still running
            assert await answer(r2) == b"ok\n"
            assert read.calls == 1                                  # at most one extraction thread
            assert u.audit_text().count("reply-extraction-timeout") == 2
            read.release.set()                                      # the abandoned thread finishes
            await wait_until(lambda: u.daemon._extraction is not None and u.daemon._extraction.done())  # pyright: ignore[reportPrivateUsage]
            r3, w3 = await send(sock, frame("Stop"))                # and the next fallback may run again
            assert await answer(r3) == b"ok\n"
            assert read.calls == 2 and replies(u) == [("late text", None)]
            for w in (w1, w2, w3):
                w.close()
    run(scenario())


def test_an_event_that_exceeds_the_overall_deadline_is_cancelled_and_its_slot_released(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    u.daemon.hook_deadline = 0.05
    real = u.daemon.route_hook
    calls = [0]

    async def route(ev: HookEvent, arrival: int | None = None) -> None:
        calls[0] += 1
        if calls[0] == 1:
            await asyncio.Event().wait()                            # never finishes
        await real(ev, arrival)
    monkeypatch.setattr(u.daemon, "route_hook", route)

    async def scenario() -> None:
        async with serving(u) as (_server, sock):
            r1, w1 = await send(sock, frame("Stop", last_assistant_message="stuck"))
            r2, w2 = await send(sock, frame("Stop", last_assistant_message="next"))
            assert await answer(r1) == b"err\n"
            assert await answer(r2) == b"ok\n"                      # the sequencer moved on
            assert "hook-deadline" in u.audit_text() and replies(u) == [("next", None)]
            assert not u.daemon.dispatch_lock.locked()
            for w in (w1, w2):
                w.close()
    run(scenario())
