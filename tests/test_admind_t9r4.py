"""Task 9 review round 4, code fixes: hook events are ordered by Claude Code's own synchronous hooks and
admind's accept-order sequencer (no counter), a hook is answered only after its event was processed, a Stop
never ends a turn whose prompt it has not followed (and a late prompt never revives a stopped reservation),
and a transcript read that straddles a turn change is discarded. Fakes, tmp_path and explicit barriers only
(futures, threading events); no sleeps, no real wn-agent, claude, systemctl, network or ~/.claude.
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

from heterodyne.admind import hook as hook_module
from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import HookEvent, HookServer, hook_main

# --- helpers ---


def frame(event: str = "Stop", **extra: object) -> bytes:
    return json.dumps({"hook_event_name": event, "session_id": "S1", **extra}).encode() + b"\n"


class Consumer:
    """Stands in for hook_loop: takes what the server queues, in queue order, and lets the test decide when
    each event counts as processed. It copes with the old (bare HookEvent) and new (Delivery) item types."""

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self.queue = queue
        self.seen: list[HookEvent] = []
        self.pending: list[Any] = []

    async def take(self) -> HookEvent:
        item = await asyncio.wait_for(self.queue.get(), 10)
        ev = getattr(item, "event", item)
        self.seen.append(ev)
        self.pending.append(getattr(item, "done", None))
        return ev

    def finish(self, ok: bool = True) -> None:
        done = self.pending.pop(0)
        if done is not None:
            done.set_result(ok)


async def yield_loop(times: int = 30) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


@contextlib.asynccontextmanager
async def server_for(tmp_path: Path) -> AsyncIterator[tuple[HookServer, Consumer, Path]]:
    queue: asyncio.Queue[Any] = asyncio.Queue()
    server = HookServer(tmp_path / "h.sock", queue, Audit(tmp_path / "audit.jsonl"))
    await server.start()
    try:
        yield server, Consumer(queue), tmp_path / "h.sock"
    finally:
        await server.close()


# --- 1. accept order, serial processing, answers after processing ---


def test_a_hook_whose_connect_came_first_is_processed_first_though_its_frame_finishes_later(
        tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server_for(tmp_path) as (_server, consumer, sock):
            ra, wa = await asyncio.open_unix_connection(str(sock))      # A connects first ...
            rb, wb = await asyncio.open_unix_connection(str(sock))      # ... then B
            wb.write(frame(last_assistant_message="B"))                 # B's frame is complete first
            await wb.drain()
            await yield_loop()
            assert consumer.queue.empty()                               # B waits for A
            wa.write(frame(last_assistant_message="A"))                 # A's frame finishes later
            await wa.drain()
            first = await consumer.take()
            assert first.last_assistant_message == "A"
            await yield_loop()
            assert consumer.queue.empty()                               # B is held until A is processed
            consumer.finish()
            second = await consumer.take()
            assert second.last_assistant_message == "B"
            consumer.finish()
            assert await asyncio.wait_for(ra.readline(), 10) == b"ok\n"
            assert await asyncio.wait_for(rb.readline(), 10) == b"ok\n"
            for w in (wa, wb):
                w.close()
    run(scenario())


def test_an_event_is_answered_only_after_it_was_processed_and_a_failure_is_answered_err(
        tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server_for(tmp_path) as (_server, consumer, sock):
            reader, writer = await asyncio.open_unix_connection(str(sock))
            writer.write(frame())
            await writer.drain()
            await consumer.take()
            answer = asyncio.create_task(reader.readline())
            await yield_loop()
            assert not answer.done()                                    # not acknowledged while queued
            consumer.finish(ok=False)
            assert await asyncio.wait_for(answer, 10) == b"err\n"
            writer.close()
    run(scenario())


def test_the_daemon_answers_ok_after_route_hook_and_err_when_it_raised(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    gate: asyncio.Future[None] | None = None
    calls = [0]

    async def route(ev: HookEvent) -> None:
        calls[0] += 1
        assert gate is not None
        await gate
        if calls[0] == 2:
            raise RuntimeError("boom")
    monkeypatch.setattr(u.daemon, "route_hook", route)

    async def scenario() -> None:
        nonlocal gate
        gate = asyncio.get_running_loop().create_future()
        server = HookServer(u.settings.state_dir / "hook.sock", u.daemon.hooks, u.audit)
        await server.start()
        loop_task = asyncio.create_task(u.daemon.hook_loop())
        try:
            sock = str(u.settings.state_dir / "hook.sock")
            r1, w1 = await asyncio.open_unix_connection(sock)
            w1.write(frame())
            await w1.drain()
            answer = asyncio.create_task(r1.readline())
            await yield_loop()
            assert not answer.done()                                    # route_hook is still running
            gate.set_result(None)
            assert await asyncio.wait_for(answer, 10) == b"ok\n"
            r2, w2 = await asyncio.open_unix_connection(sock)
            w2.write(frame())
            await w2.drain()
            assert await asyncio.wait_for(r2.readline(), 10) == b"err\n"
            assert not loop_task.done() and "boom" not in u.audit_text()
            for w in (w1, w2):
                w.close()
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            await server.close()
    run(scenario())


def test_a_dropped_connection_releases_the_next_one_and_a_slow_frame_times_out(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_module, "FRAME_SECONDS", 0.2)

    async def scenario() -> None:
        async with server_for(tmp_path) as (_server, consumer, sock):
            _ra, wa = await asyncio.open_unix_connection(str(sock))     # connects, sends nothing, EOF
            _rb, wb = await asyncio.open_unix_connection(str(sock))     # partial frame, then silence
            rc, wc = await asyncio.open_unix_connection(str(sock))
            wb.write(b'{"hook_event_name":"Stop"')
            await wb.drain()
            wc.write(frame(last_assistant_message="C"))
            await wc.drain()
            await yield_loop()
            assert consumer.queue.empty()                               # C waits behind A and B
            wa.close()                                                  # A: EOF
            ev = await consumer.take()                                  # B times out; then C
            assert ev.last_assistant_message == "C"
            consumer.finish()
            assert await asyncio.wait_for(rc.readline(), 10) == b"ok\n"
            wb.close()
            wc.close()
        assert (tmp_path / "audit.jsonl").read_text().count('"dropped"') == 2
    run(scenario())


# --- 2. the hook client ---


def test_the_hook_connects_before_it_parses_or_reads_anything(tmp_path: Path) -> None:
    async def scenario() -> None:
        accepted = asyncio.Event()
        held: list[asyncio.StreamWriter] = []

        async def handle(_r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            held.append(w)
            accepted.set()
        server = await asyncio.start_unix_server(handle, path=str(tmp_path / "c.sock"))
        try:
            # The payload is not JSON, so nothing can be sent; the connection must exist regardless.
            argv = ["--socket", str(tmp_path / "c.sock")]
            assert await asyncio.to_thread(hook_main, argv, b"not json") == 0
            await asyncio.wait_for(accepted.wait(), 10)
            for w in held:
                w.close()
        finally:
            server.close()
            await server.wait_closed()
    run(scenario())


def test_the_hook_gives_up_after_its_bound_and_still_exits_zero(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_module, "ACK_SECONDS", 0.3)

    async def scenario() -> None:
        held: list[asyncio.StreamWriter] = []

        async def silent(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            held.append(w)
            await r.readline()          # reads the frame, never answers
        server = await asyncio.start_unix_server(silent, path=str(tmp_path / "c.sock"))
        try:
            assert await asyncio.to_thread(hook_main, ["--socket", str(tmp_path / "c.sock")], frame()) == 0
            for w in held:
                w.close()
        finally:
            server.close()
            await server.wait_closed()
    run(scenario())


def test_the_hook_ignores_an_old_seq_file_argument_and_creates_no_counter(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server_for(tmp_path) as (_server, consumer, sock):
            counter = tmp_path / "hook-seq"
            hook = asyncio.create_task(asyncio.to_thread(
                hook_main, ["--socket", str(sock), "--launch", "ab" * 16, "--seq-file", str(counter)],
                frame(seq=999)))
            ev = await consumer.take()
            assert ev.launch == "ab" * 16
            consumer.finish()
            assert await hook == 0
            assert not counter.exists()
    run(scenario())


def test_an_unknown_seq_field_in_a_frame_does_not_break_decoding(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with server_for(tmp_path) as (_server, consumer, sock):
            reader, writer = await asyncio.open_unix_connection(str(sock))
            writer.write(frame(seq=12, last_assistant_message="old hook"))
            await writer.drain()
            ev = await consumer.take()
            assert ev.last_assistant_message == "old hook" and not hasattr(ev, "seq")
            consumer.finish()
            assert await asyncio.wait_for(reader.readline(), 10) == b"ok\n"
            writer.close()
    run(scenario())


# --- 3. Stop semantics without a counter (D-e) ---


def outbox_replies(u: Unit) -> list[tuple[str, str | None]]:
    return [(t, r) for k, t, r in u.outbox() if k.startswith("reply:")]


def test_a_stop_before_any_prompt_hook_does_not_release_the_reservation(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        a = await u.say("job A")                                   # dispatched; no UserPromptSubmit yet
        busy = u.store.get("busy")
        await u.daemon.on_hook(HookEvent("Stop", "S1", last_assistant_message="who knows"))
        assert u.store.get("in_flight") == a and u.store.get("busy") == busy
        assert outbox_replies(u) == [("who knows", None)]          # posted top-level
        assert u.store.get("in_flight_text") == "job A"
        b = await u.say("job B")                                   # still held behind the reservation
        assert u.daemon.held == [(b, "job B")] and u.tmux.pasted == ["job A"]
    run(scenario())


def test_a_textless_stop_before_any_prompt_hook_posts_nothing_and_skips_the_transcript(
        tmp_path: Path) -> None:
    u = Unit(tmp_path)
    transcript = tmp_path / "S1.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {
        "content": [{"type": "text", "text": "someone else's text"}]}}) + "\n")

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("Stop", "S1", transcript_path=str(transcript)))
        assert outbox_replies(u) == [] and "someone else's text" not in u.texts()
        assert "stale-stop-unrecoverable" in u.audit_text()
        assert u.store.get("in_flight") == a and u.store.get("busy") is not None
    run(scenario())


def test_a_late_prompt_never_re_anchors_a_reservation_whose_stop_overtook_it(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        a = await u.say("job A")
        busy = u.store.get("busy")
        await u.daemon.on_hook(HookEvent("Stop", "S1", last_assistant_message="A's reply"))
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))     # arrives late
        assert u.store.get("anchor") is None and u.store.get("busy") == busy
        assert u.store.get("in_flight") == a
        assert "ignored-late-prompt" in u.audit_text() and "prompt-submitted" not in u.audit_text()
        assert outbox_replies(u) == [("A's reply", None)]
        await u.say("!interrupt")                                  # the operator's release still works
        assert u.store.get("in_flight") is None and u.store.get("busy") is None
        assert u.store.get("stopped_flight") is None
        # A fresh reservation is not affected by the old one's marker.
        c = await u.say("job C")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job C"))
        assert u.store.get("anchor") == c
    run(scenario())


def test_a_normal_turn_still_threads_and_releases(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        assert u.store.get("anchor") == a
        await u.daemon.on_hook(HookEvent("Stop", "S1", last_assistant_message="done"))
        assert outbox_replies(u) == [("done", a)]
        assert u.store.get("in_flight") is None and u.store.get("busy") is None
        assert u.store.get("anchor") is None
    run(scenario())


# --- 4. a transcript read that straddles a turn change (D-f) ---


class GatedRead:
    def __init__(self, text: str) -> None:
        self.text = text
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, ev: HookEvent, fallback: bool = True) -> str:
        self.started.set()
        assert self.release.wait(10), "test never released the read"
        return self.text


def test_a_fallback_read_that_straddles_interrupt_and_a_new_dispatch_is_discarded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    read = GatedRead("B's text, not A's")
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", read)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        assert u.store.get("anchor") == a
        stop = asyncio.create_task(u.daemon.on_hook(HookEvent("Stop", "S1")))   # no text: fallback
        assert await asyncio.to_thread(read.started.wait, 10)
        await u.say("!interrupt")                                   # releases A (the lock is free)
        b = await u.say("job B")                                    # B is dispatched meanwhile
        assert u.store.get("in_flight") == b
        read.release.set()
        await stop
        assert outbox_replies(u) == []                              # B's text is not posted
        assert "stale-stop-unrecoverable" in u.audit_text()
        assert u.store.get("in_flight") == b and u.store.get("busy") is not None
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job B"))
        assert u.store.get("anchor") == b                           # B's own turn is untouched
    run(scenario())


def test_a_fallback_read_with_the_same_turn_afterwards_is_used(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    read = GatedRead("from the transcript")
    read.release.set()
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", read)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))
        await u.daemon.on_hook(HookEvent("Stop", "S1"))
        assert outbox_replies(u) == [("from the transcript", a)]
    run(scenario())
