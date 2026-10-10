import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import wait_for

from heterodyne.session.server import SessionServer

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
