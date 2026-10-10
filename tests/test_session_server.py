import contextlib
import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import wait_for

from heterodyne.session.server import MAX_DEPTH, SessionServer, TurnState

TOKEN = "fake-session-token"  # noqa: S105 (test value)
THREAD = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[SessionServer]:
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl")
    s.start()
    yield s
    s.close()


def ask(path: Path, raw: bytes) -> str:
    with socket.socket(socket.AF_UNIX) as c:
        c.settimeout(5)
        c.connect(str(path))
        c.sendall(raw)
        return c.makefile("rb").readline().decode().strip()


def req(token: str, kind: str, payload: object) -> bytes:
    return (json.dumps({"token": token, "type": kind, "payload": payload}) + "\n").encode()


def test_replies_are_exact(server: SessionServer, tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"
    assert ask(sock, req(TOKEN, "hook_event", {})) == '{"ok": true}'
    assert ask(sock, req(TOKEN, "approve", {})) == '{"ok": false, "error": "forbidden"}'
    assert ask(sock, req("wrong", "hook_event", {})) == '{"ok": false, "error": "forbidden"}'
    assert ask(sock, req(TOKEN, "ws_request", {"text": "x"})) == '{"ok": false, "error": "unsupported"}'
    assert ask(sock, b"not json\n") == '{"ok": false, "error": "malformed"}'
    assert ask(sock, req(TOKEN, "hook_event", [1])) == '{"ok": false, "error": "malformed"}'


def test_oversized_line_is_refused(server: SessionServer, tmp_path: Path) -> None:
    assert ask(tmp_path / "s.sock", b"x" * (65 * 1024)) == '{"ok": false, "error": "malformed"}'


def test_turn_state_and_thread_id(server: SessionServer, tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"
    assert not server.turns().idle
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "SessionStart", "session_id": "not-a-uuid"}))
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "SessionStart", "session_id": THREAD}))
    other = THREAD.replace("0", "1")
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "SessionStart", "session_id": other}))
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "UserPromptSubmit"}))
    assert not server.turns().idle
    ask(sock, req(TOKEN, "hook_event", {"hook_event_name": "Stop"}))
    t = server.turns()
    assert (t.prompts, t.stops, t.idle, t.thread_id) == (1, 1, True, THREAD)
    ask(sock, req("wrong", "hook_event", {"hook_event_name": "UserPromptSubmit"}))
    assert server.turns().idle                      # a refused request changes nothing


def test_accepted_requests_are_spooled_with_a_cap(tmp_path: Path) -> None:
    s = SessionServer(tmp_path / "s.sock", TOKEN, tmp_path / "events.jsonl", max_spool=200)
    s.start()
    try:
        for n in range(10):
            ask(tmp_path / "s.sock", req(TOKEN, "hook_event", {"n": n}))
        ask(tmp_path / "s.sock", req("wrong", "hook_event", {"n": 99}))
    finally:
        s.close()
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert 1 <= len(lines) < 10 and all(json.loads(x)["type"] == "hook_event" for x in lines)
    assert all(json.loads(x)["payload"]["n"] != 99 for x in lines)
    assert (tmp_path / "events.jsonl").stat().st_size <= 200


def test_close_removes_the_socket_and_start_replaces_a_stale_one(tmp_path: Path) -> None:
    path = tmp_path / "s.sock"
    path.write_text("stale")
    s = SessionServer(path, TOKEN, tmp_path / "e.jsonl")
    s.start()
    assert path.is_socket()
    s.close()
    assert not path.exists()


def test_many_concurrent_clients(server: SessionServer, tmp_path: Path) -> None:
    replies: list[str] = []
    def one() -> None:
        replies.append(ask(tmp_path / "s.sock", req(TOKEN, "hook_event", {})))

    threads = [threading.Thread(target=one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    wait_for(lambda: len(replies) == 20 and server.served >= 20)
    assert set(replies) == {'{"ok": true}'}


# r1 review: the session is untrusted with wsd's threads and descriptors too.


def refused(path: Path, raw: bytes) -> bool:
    """True if the server closed the connection without a reply."""
    try:
        return ask(path, raw) == ""
    except (ConnectionResetError, BrokenPipeError):
        return True


def held(path: Path, raw: bytes) -> socket.socket:
    """A connection that has sent `raw` (no newline yet) and stays open."""
    c = socket.socket(socket.AF_UNIX)
    c.settimeout(5)
    c.connect(str(path))
    c.sendall(raw)
    return c


def handler_threads(server: SessionServer) -> list[threading.Thread]:
    name = f"session-{server.path.parent.name}-conn"
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


def test_connections_past_the_cap_are_closed_unread(tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"
    s = SessionServer(sock, TOKEN, tmp_path / "e.jsonl", max_handlers=2)
    s.start()
    try:
        partial = req(TOKEN, "hook_event", {})[:-1]
        first, second = held(sock, partial), held(sock, partial)
        try:
            wait_for(lambda: s.active == 2)
            assert refused(sock, req(TOKEN, "hook_event", {}))
            first.sendall(b"\n")
            assert first.makefile("rb").readline().decode().strip() == '{"ok": true}'
            wait_for(lambda: s.active == 1)
            assert ask(sock, req(TOKEN, "hook_event", {})) == '{"ok": true}'     # its slot came back
        finally:
            first.close()
            second.close()
    finally:
        s.close()


def test_a_request_has_one_deadline_however_it_trickles(tmp_path: Path) -> None:
    now, reads = [0.0], [0]

    def clock() -> float:
        reads[0] += 1
        return now[0]

    sock = tmp_path / "s.sock"
    s = SessionServer(sock, TOKEN, tmp_path / "e.jsonl", client_seconds=5.0, clock=clock)
    s.start()
    try:
        line = req(TOKEN, "hook_event", {})
        for late, served in ((4.9, True), (5.1, False)):
            now[0], reads[0] = 0.0, 0
            c = held(sock, line[:10])
            try:
                # The deadline is set, the first chunk read, and the next read waiting (a per-read
                # timeout would start again here). The rest arrives just before or after the deadline.
                wait_for(lambda: reads[0] >= 3)
                now[0] = late
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    c.sendall(line[10:])
                try:
                    reply = c.makefile("rb").readline().decode().strip()
                except ConnectionResetError:
                    reply = ""
                assert reply == ('{"ok": true}' if served else "")
            finally:
                c.close()
        wait_for(lambda: s.active == 0)
    finally:
        s.close()


def test_a_thread_that_fails_to_start_refuses_only_its_connection(
        server: SessionServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = threading.Thread.start
    failed: list[str] = []

    def flaky(self: threading.Thread) -> None:
        if self.name.endswith("-conn") and not failed:
            failed.append(self.name)
            raise RuntimeError("can't start new thread")
        real(self)

    monkeypatch.setattr(threading.Thread, "start", flaky)
    sock = tmp_path / "s.sock"
    assert refused(sock, req(TOKEN, "hook_event", {}))
    assert ask(sock, req(TOKEN, "hook_event", {})) == '{"ok": true}'
    assert failed and server.active == 0


def test_close_ends_held_connections_and_nothing_is_recorded_after(tmp_path: Path) -> None:
    sock, events = tmp_path / "s.sock", tmp_path / "e.jsonl"
    s = SessionServer(sock, TOKEN, events)
    s.start()
    c = held(sock, req(TOKEN, "hook_event", {"hook_event_name": "UserPromptSubmit"})[:-1])
    try:
        wait_for(lambda: s.active == 1)
        s.close()
        assert s.active == 0 and handler_threads(s) == []
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            c.sendall(b"\n")
            assert c.recv(100) == b""
    finally:
        c.close()
    assert s.turns() == TurnState() and not events.exists()


def test_deep_nesting_is_malformed(server: SessionServer, tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"
    deep = b"[" * 30000 + b"]" * 30000 + b"\n"
    assert ask(sock, deep) == '{"ok": false, "error": "malformed"}'
    nested: object = {}
    for _ in range(MAX_DEPTH):
        nested = {"a": nested}
    assert ask(sock, req(TOKEN, "hook_event", nested)) == '{"ok": false, "error": "malformed"}'
    assert ask(sock, req(TOKEN, "hook_event", {"a": [{"b": "[[[[{{{{"}]})) == '{"ok": true}'


def test_a_token_that_cannot_be_encoded_is_forbidden(server: SessionServer, tmp_path: Path) -> None:
    raw = b'{"token": "\\ud800", "type": "hook_event", "payload": {}}\n'
    assert ask(tmp_path / "s.sock", raw) == '{"ok": false, "error": "forbidden"}'
    assert server.turns() == TurnState()


def test_close_during_a_failed_thread_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r2 review: close() runs while a connection's thread is failing to start. It returns normally,
    joins nothing unstarted, and still removes the socket."""
    sock = tmp_path / "s.sock"
    s = SessionServer(sock, TOKEN, tmp_path / "e.jsonl")
    s.start()
    real = threading.Thread.start
    errors: list[BaseException] = []

    def closing() -> None:
        try:
            s.close()
        except BaseException as exc:    # recorded for the assertion below
            errors.append(exc)

    closer = threading.Thread(target=closing)

    def flaky(self: threading.Thread) -> None:
        if self.name.endswith("-conn"):
            real(closer)
            closer.join(1)              # bounded: an unsynchronised close finishes (and fails) here
            raise RuntimeError("can't start new thread")
        real(self)

    monkeypatch.setattr(threading.Thread, "start", flaky)
    assert refused(sock, req(TOKEN, "hook_event", {}))
    closer.join(10)
    assert not closer.is_alive() and errors == []
    assert not sock.exists() and s.active == 0
