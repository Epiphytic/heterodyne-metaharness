import asyncio
import contextlib
import json
import socket
from pathlib import Path

from fakes.settings import make_settings

from heterodyne.admind.agent import AdminAgent, AgentStuck
from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import (
    HookEvent,
    HookServer,
    hook_command,
    hook_main,
    last_assistant_text,
    reply_text,
    settings_json,
)
from heterodyne.admind.store import Store
from heterodyne.agents.claude_code import interactive_argv


class FakeTmux:
    def __init__(self) -> None:
        self.sessions: dict[str, list[str]] = {}
        self.dead: set[str] = set()
        self.pasted: list[str] = []
        self.keys: list[str] = []

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None:
        self.sessions[name] = argv

    def pane_dead(self, name: str) -> bool:
        return name in self.dead

    def paste(self, name: str, text: str) -> None:
        self.pasted.append(text)

    def send_key(self, name: str, key: str) -> None:
        self.keys.append(key)

    def capture(self, name: str, lines: int) -> str:
        return f"screen of {name}, {lines} lines"

    def kill(self, name: str) -> None:
        self.sessions.pop(name, None)
        self.dead.discard(name)


def test_interactive_argv() -> None:
    profile = {"adapter": "claude-code", "model": "m1", "args": ["--verbose"]}
    new = interactive_argv("claude", profile, session_id="S", resume=False,
                           settings_file=Path("/x/s.json"), name="admin")
    assert new == ["claude", "--session-id", "S", "--model", "m1", "--permission-mode", "bypassPermissions",
                   "--settings", "/x/s.json", "--name", "admin", "--verbose"]
    again = interactive_argv("claude", {"adapter": "claude-code"}, session_id="S", resume=True,
                             settings_file=Path("/x/s.json"), name="admin")
    assert again[:3] == ["claude", "--resume", "S"] and "--model" not in again


def make_agent(tmp_path: Path) -> tuple[AdminAgent, FakeTmux, Store]:
    s = make_settings(tmp_path)
    store = Store(s.state_dir / "admind.db")
    tmux = FakeTmux()
    return AdminAgent(tmux, store, s, s.state_dir / "hook.sock"), tmux, store  # type: ignore[arg-type]


def test_launch_resume_adopt_and_new(tmp_path: Path) -> None:
    agent, tmux, store = make_agent(tmp_path)
    assert agent.ensure_running() == "launched"
    sid = agent.session_id
    assert sid is not None and tmux.sessions["admin"][1:3] == ["--session-id", sid]
    settings = json.loads((tmp_path / "state" / "admind" / "claude-settings.json").read_text())
    assert set(settings["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}
    assert agent.ensure_running() == "adopted"
    agent.started(sid)
    tmux.dead.add("admin")                      # crashed after a successful start
    assert agent.ensure_running() == "resumed"
    assert tmux.sessions["admin"][1:3] == ["--resume", sid]
    assert agent.new() == "launched"
    assert agent.session_id != sid


def test_crash_loop_stops_after_three_launches(tmp_path: Path) -> None:
    agent, tmux, _ = make_agent(tmp_path)
    for _ in range(3):
        tmux.kill("admin")
        agent.ensure_running()
    tmux.kill("admin")
    try:
        agent.ensure_running()
    except AgentStuck:
        pass
    else:
        raise AssertionError("expected AgentStuck")
    assert agent.new() == "launched"            # !new resets the counter


def test_send_interrupt_tail(tmp_path: Path) -> None:
    agent, tmux, _ = make_agent(tmp_path)
    assert agent.tail(5) == "(no admin agent session)"
    agent.ensure_running()
    agent.send("hello\nworld")
    agent.interrupt()
    assert tmux.pasted == ["hello\nworld"] and tmux.keys == ["Escape"]
    assert agent.tail(5) == "screen of admin, 5 lines"


def test_reply_text_prefers_the_hook_field(tmp_path: Path) -> None:
    transcript = tmp_path / "S.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "final"},
                                                      {"type": "text", "text": "answer"}]}},
    ]) + "\nnot json\n")
    assert last_assistant_text(transcript) == "final\nanswer"
    assert reply_text(HookEvent("Stop", "S", str(transcript), "from hook")) == "from hook"
    assert reply_text(HookEvent("Stop", "S", str(transcript), None)) == "final\nanswer"
    # a transcript path that isn't this session's file is not read
    assert reply_text(HookEvent("Stop", "OTHER", str(transcript), None)) == ""
    assert last_assistant_text(tmp_path / "missing.jsonl") == ""


def test_transcript_fallback_is_bounded_to_the_current_turn(tmp_path: Path) -> None:
    transcript = tmp_path / "S.jsonl"
    records = [
        {"type": "user", "message": {"role": "user", "content": "first question"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "old answer"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "second"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    assert last_assistant_text(transcript) == ""   # the second turn had no text; never "old answer"


def test_hook_round_trip_over_the_socket(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> HookEvent:
        queue: asyncio.Queue[HookEvent] = asyncio.Queue()
        server = HookServer(sock, queue, Audit(tmp_path / "audit.jsonl"))
        await server.start()
        stdin = json.dumps({"hook_event_name": "Stop", "session_id": "S", "last_assistant_message": "ok",
                            "stop_hook_active": False, "cwd": "/x"}).encode()
        rc = await asyncio.to_thread(hook_main, ["--socket", str(sock)], stdin)
        assert rc == 0
        ev = await asyncio.wait_for(queue.get(), 5)
        await server.close()
        return ev
    ev = asyncio.run(body())
    assert ev == HookEvent("Stop", "S", None, "ok")


def test_hook_socket_is_private(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> int:
        server = HookServer(sock, asyncio.Queue(), Audit(tmp_path / "audit.jsonl"))
        await server.start()
        mode = sock.stat().st_mode & 0o777
        await server.close()
        return mode
    assert asyncio.run(body()) == 0o600
    assert not sock.exists()


def test_hook_never_fails_the_agent(tmp_path: Path) -> None:
    assert hook_main(["--socket", str(tmp_path / "absent.sock")], b'{"hook_event_name":"Stop"}') == 0
    assert hook_main(["--socket", str(tmp_path / "absent.sock")], b"not json") == 0
    assert hook_main([], b"{}") == 0


def test_hook_server_drops_garbage(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> int:
        queue: asyncio.Queue[HookEvent] = asyncio.Queue()
        server = HookServer(sock, queue, Audit(tmp_path / "audit.jsonl"))
        await server.start()

        def send() -> None:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(str(sock))
                s.sendall(b'{"no": "fields"}\n')
        await asyncio.to_thread(send)
        await asyncio.sleep(0.2)
        await server.close()
        return queue.qsize()
    assert asyncio.run(body()) == 0
    assert "hook" in (tmp_path / "audit.jsonl").read_text()


def test_hook_server_drops_oversized_frames(tmp_path: Path) -> None:
    sock = tmp_path / "hook.sock"

    async def body() -> int:
        queue: asyncio.Queue[HookEvent] = asyncio.Queue()
        server = HookServer(sock, queue, Audit(tmp_path / "audit.jsonl"))
        await server.start()

        def send() -> None:
            # admind closes the connection mid-frame, so the client may see a broken pipe.
            with contextlib.suppress(OSError), socket.socket(socket.AF_UNIX) as s:
                s.connect(str(sock))
                s.sendall(b'{"hook_event_name":"Stop","session_id":"S","last_assistant_message":"'
                          + b"x" * (2 * 1024 * 1024) + b'"}\n')
        await asyncio.to_thread(send)
        await asyncio.sleep(0.3)
        await server.close()
        return queue.qsize()
    assert asyncio.run(body()) == 0


def test_settings_json_wires_both_hooks(tmp_path: Path) -> None:
    command = hook_command(tmp_path / "hook sock")
    assert "'" in command                      # the socket path is shell-quoted
    data = json.loads(settings_json(command))
    for event in ("SessionStart", "UserPromptSubmit", "Stop"):
        assert data["hooks"][event] == [{"hooks": [{"type": "command", "command": command}]}]
