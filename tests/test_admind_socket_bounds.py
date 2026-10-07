"""Bounded sockets (relay Task 2 review r1): a client that stops reading can't hold a handler or `close()`
on ctl.sock or ask.sock, and `admind ask wait --timeout N` keeps to N whatever the daemon does.

Every socket is under tmp_path; nothing reads /proc, so these run on macOS too.
"""

import asyncio
import socket
import threading
import time
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fakes.settings import make_settings
from test_admind_asks import ask_server, records

from heterodyne.admind import asks, cli, ctl
from heterodyne.admind.audit import Audit

BIG = 900_000           # well past any socket buffer and asyncio's 64 KiB high-water mark; under MAX_REPLY


async def until(pred: Any, timeout: float = 10.0) -> None:
    """Wait for a condition, bounded and checked (the condition is the synchronisation, not the poll)."""
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


async def big_reply(_req: asks.AskRequest, _peer: ctl.Peer) -> asks.AskReply:
    return asks.AskReply("ok", "h" * BIG)


def blocked(server: ctl.JsonSocketServer[Any, Any]) -> bool:
    """Some accepted connection's reply is stuck in the transport: drain is waiting on the peer."""
    return any(w.transport.get_write_buffer_size() > 0 for w in server.handlers.values())


def unread_request(path: Path, request: bool = True) -> socket.socket:
    """A plain client socket that sends its request and reads nothing until told to. (An asyncio stream
    would keep reading into its own buffer, so the server's drain would never wait.)"""
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(5)
    client.connect(str(path))
    if request:
        client.sendall(msgspec.json.encode(asks.AskList()) + b"\n")
    client.setblocking(False)
    return client


async def read_to_end(client: socket.socket) -> int:
    """Bytes until the server hangs up; each read bounded, so a connection never dropped fails the test."""
    loop = asyncio.get_running_loop()
    total = 0
    try:
        while chunk := await asyncio.wait_for(loop.sock_recv(client, 65_536), 5):
            total += len(chunk)
    except ConnectionResetError:
        pass
    finally:
        client.close()
    return total


def test_close_drops_a_client_that_never_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.2)     # WRITE_SECONDS stays 30: close() must not wait for it

    async def body() -> None:
        server = await ask_server(tmp_path, big_reply)
        client = unread_request(server.path)
        await until(lambda: blocked(server))
        start = time.monotonic()
        await asyncio.wait_for(server.close(), 5)
        assert time.monotonic() - start < 2.0 and server.handlers == {}
        assert await read_to_end(client) < BIG          # dropped, not delivered

    asyncio.run(body())


def test_unread_reply_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "WRITE_SECONDS", 0.2)
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.2)

    async def body() -> None:
        server = await ask_server(tmp_path, big_reply)
        try:
            client = unread_request(server.path)
            await until(lambda: blocked(server))
            await until(lambda: server.handlers == {})    # the handler gave up on its own
            assert await read_to_end(client) < BIG
        finally:
            await server.close()

    asyncio.run(body())
    assert any(r.get("action") == "failed" and r["error"] == "TimeoutError"
               for r in records(tmp_path / "s" / "audit.jsonl"))


def test_shutdown_with_stalled_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ctl.sock with a client that sends nothing (READ_SECONDS is 5) and ask.sock with a handler that never
    returns: close() cancels both within its bound and both clients are hung up on."""
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.2)
    entered = threading.Event()

    async def never(_req: asks.AskRequest, _peer: ctl.Peer) -> asks.AskReply:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def unused(_req: ctl.CtlRequest) -> ctl.CtlReply:
        raise AssertionError("no request is sent")

    async def body() -> None:
        audit = Audit(tmp_path / "c" / "audit.jsonl")
        control = ctl.CtlServer(tmp_path / "c" / ctl.CTL_SOCKET, unused, audit)
        await control.start()
        server = await ask_server(tmp_path, never)
        silent = unread_request(control.path, request=False)
        await until(lambda: len(control.handlers) == 1)
        client = unread_request(server.path)
        await until(entered.is_set)
        start = time.monotonic()
        await asyncio.wait_for(asyncio.gather(control.close(), server.close()), 3)
        assert time.monotonic() - start < 2.0
        assert control.handlers == {} and server.handlers == {}
        assert await read_to_end(silent) == 0 and await read_to_end(client) == 0
        assert not control.path.exists() and not server.path.exists()

    asyncio.run(body())


def test_close_lets_a_handler_finish_within_the_grace(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 5.0)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(_req: asks.AskRequest, _peer: ctl.Peer) -> asks.AskReply:
        entered.set()
        await release.wait()
        return asks.AskReply("ok", "finished")

    async def body() -> None:
        server = await ask_server(tmp_path, slow)
        reply = asyncio.create_task(ctl.request(server.path, asks.AskList(), 10, reply_type=asks.AskReply))
        await asyncio.wait_for(entered.wait(), 5)
        closing = asyncio.create_task(server.close())
        await until(lambda: server.server is not None and not server.server.is_serving())
        assert not closing.done()
        release.set()
        assert await asyncio.wait_for(reply, 5) == asks.AskReply("ok", "finished")
        await asyncio.wait_for(closing, 5)

    asyncio.run(body())


def test_hang_up_cancelled_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handler cancelled while its graceful close waits on a peer that doesn't read drops the connection
    at once; it does not leave the close pending."""
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 30.0)

    async def body() -> None:
        ours, theirs = socket.socketpair(socket.AF_UNIX)
        theirs.setblocking(False)
        _reader, writer = await asyncio.open_unix_connection(sock=ours)
        writer.write(b"x" * BIG)
        assert writer.transport.get_write_buffer_size() > 0
        task = asyncio.create_task(ctl.hang_up(writer))
        await until(writer.is_closing)                 # the task ran to its first await: wait_closed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert await read_to_end(theirs) < BIG

    asyncio.run(body())


def test_slow_reader_gets_the_whole_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that keeps reading, slowly, gets every byte: the write budget lasts until asyncio's output
    buffer is empty (handed to the kernel), not just until the default low-water mark, so the hang-up's
    CLOSE_SECONDS has no tail left to cut."""
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.001)      # any tail left for the hang-up to flush is cut
    monkeypatch.setattr(ctl, "WRITE_SECONDS", 20.0)
    size = 300_000

    go = asyncio.Event()

    async def reply(_req: asks.AskRequest, _peer: ctl.Peer) -> asks.AskReply:
        await go.wait()
        return asks.AskReply("ok", "h" * size)

    async def body() -> None:
        server = await ask_server(tmp_path, reply)
        try:
            client = unread_request(server.path)
            await until(lambda: len(server.handlers) == 1)
            # a small kernel send buffer, so the reply's tail waits in the transport for the reader
            [writer] = server.handlers.values()
            writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            go.set()
            await until(lambda: blocked(server))
            loop = asyncio.get_running_loop()
            got = bytearray()
            start = time.monotonic()
            try:
                while chunk := await asyncio.wait_for(loop.sock_recv(client, 4096), 5):
                    got += chunk
                    await asyncio.sleep(0.002)          # the slowness under test, not synchronisation
            finally:
                client.close()
            assert time.monotonic() - start > ctl.CLOSE_SECONDS
            assert msgspec.json.decode(bytes(got), type=asks.AskReply).message == "h" * size
        finally:
            await server.close()

    asyncio.run(body())


def test_a_connection_is_registered_as_it_is_accepted(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.1)

    async def body() -> None:
        server = await ask_server(tmp_path, big_reply)
        ours, theirs = socket.socketpair(socket.AF_UNIX)
        try:
            reader, writer = await asyncio.open_unix_connection(sock=ours)
            server._accept(reader, writer)             # pyright: ignore[reportPrivateUsage]
            assert list(server.handlers.values()) == [writer]          # before any await
        finally:
            await server.close()
            theirs.close()
        assert server.handlers == {}

    asyncio.run(body())


def test_a_connection_accepted_while_closing_is_aborted(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """The interleave the review found: a connection whose callback runs once close() has begun (here,
    during the grace for another connection). It is aborted at once, never registered, and close() leaves
    no handler or transport behind."""
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.2)
    entered = asyncio.Event()

    async def never(_req: asks.AskRequest, _peer: ctl.Peer) -> asks.AskReply:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def body() -> None:
        server = await ask_server(tmp_path, never)
        first = unread_request(server.path)
        await asyncio.wait_for(entered.wait(), 5)
        closing = asyncio.create_task(server.close())
        await until(lambda: server.closing)
        ours, theirs = socket.socketpair(socket.AF_UNIX)
        theirs.setblocking(False)
        reader, writer = await asyncio.open_unix_connection(sock=ours)
        server._accept(reader, writer)                 # pyright: ignore[reportPrivateUsage]
        assert writer.transport.is_closing() and len(server.handlers) == 1     # only the first
        await asyncio.wait_for(closing, 5)
        assert server.handlers == {}
        assert await read_to_end(theirs) == 0 and await read_to_end(first) == 0

    asyncio.run(body())


def test_cancelled_close_still_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 30.0)        # the grace would outlast the test
    entered = asyncio.Event()

    async def never(_req: asks.AskRequest, _peer: ctl.Peer) -> asks.AskReply:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def either(req: asks.AskRequest, peer: ctl.Peer) -> asks.AskReply:
        return await (big_reply if isinstance(req, asks.AskList) else never)(req, peer)

    async def body() -> None:
        server = await ask_server(tmp_path, either)
        unread = unread_request(server.path)                  # a list: a big reply it never reads
        await until(lambda: blocked(server))
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(5)
        client.connect(str(server.path))
        client.sendall(msgspec.json.encode(asks.AskGet("k7m2")) + b"\n")
        client.setblocking(False)
        await asyncio.wait_for(entered.wait(), 5)
        handlers = list(server.handlers)
        assert len(handlers) == 2
        closing = asyncio.create_task(server.close())
        await until(lambda: server.closing)               # close() is in its grace wait
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 5)
        assert all(h.cancelling() for h in handlers) and not server.path.exists()
        await until(lambda: server.handlers == {})
        assert await read_to_end(client) == 0             # the connections were dropped, not left open
        assert await read_to_end(unread) < BIG            # aborted now, not after another CLOSE_SECONDS

    asyncio.run(body())


@pytest.mark.parametrize("stall", ["reply", "connect"])
def test_wait_keeps_to_its_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str], stall: str) -> None:
    """A daemon that accepts and never replies, or a connect that never completes: `--timeout 1` exits 3
    in about a second, not after the 30 s per-request cap."""
    s = make_settings(tmp_path)
    monkeypatch.setattr(cli.hconfig, "load", lambda: None)
    monkeypatch.setattr(cli, "resolve", lambda cfg, env: s)
    s.state_dir.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(s.state_dir / asks.ASK_SOCKET))
    listener.listen()                                   # never accepted: connect and write succeed, no reply
    if stall == "connect":
        async def hang(*_args: Any, **_kw: Any) -> Any:
            await asyncio.Event().wait()
        monkeypatch.setattr(ctl.asyncio, "open_unix_connection", hang)
    result: list[int] = []
    start = time.monotonic()
    argv = ["ask", "wait", "k7m2", "--timeout", "1"]
    thread = threading.Thread(target=lambda: result.append(cli.main(argv)))
    thread.start()
    thread.join(20)
    elapsed = time.monotonic() - start
    listener.close()
    assert not thread.is_alive() and result == [cli.EX_TIMEOUT]
    assert elapsed < 2.0, elapsed
    assert cli.NOT_RUNNING in capsys.readouterr().err


def test_wait_polls_until_the_deadline_and_no_further(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """Each poll gets what is left of the budget (at most the per-request cap), the deadline is checked before
    each poll, and the sleep between polls never passes it."""
    s = make_settings(tmp_path)
    clock = [0.0]
    budgets: list[float] = []
    sleeps: list[float] = []

    def request(_s: Any, _req: asks.AskRequest, timeout: float) -> asks.AskReply:
        budgets.append(timeout)
        clock[0] += 0.5                                  # each poll takes half a second
        return asks.AskReply("ok", "open", ask=asks.AskView(asks.AskSummary(
            "k7m2", "question", "open", "t", None, None, True, 0, None, None), []))

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(cli, "_ask_request", request)
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", sleep)
    monkeypatch.setattr(cli, "WAIT_POLL", 2.0)
    assert cli._ask_wait(s, asks.AskGet("k7m2"), 5.0, False) == cli.EX_TIMEOUT     # pyright: ignore[reportPrivateUsage]
    assert budgets == [5.0, 2.5] and sleeps == [2.0, 2.0]                       # 0.5+2, 0.5+2: 5.0 reached
    clock[0], budgets[:], sleeps[:] = 0.0, [], []
    assert cli._ask_wait(s, asks.AskGet("k7m2"), 100.0, False) == cli.EX_TIMEOUT    # pyright: ignore[reportPrivateUsage]
    assert budgets[0] == cli.ASK_READ_SECONDS and max(budgets) <= cli.ASK_READ_SECONDS
    assert clock[0] == pytest.approx(100.0) and all(x >= 0 for x in sleeps)
    clock[0], budgets[:], sleeps[:] = 0.0, [], []
    assert cli._ask_wait(s, asks.AskGet("k7m2"), 1.2, False) == cli.EX_TIMEOUT      # pyright: ignore[reportPrivateUsage]
    assert budgets == [1.2] and sleeps == [pytest.approx(0.7)] and clock[0] == pytest.approx(1.2)
