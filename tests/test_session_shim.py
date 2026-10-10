import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from heterodyne.session import shim
from heterodyne.session.server import SessionServer

TOKEN = "fake-session-token"  # noqa: S105 (test value)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


@pytest.fixture
def run(tmp_path: Path) -> Path:
    run = tmp_path / "r1"
    run.mkdir()
    (run / "token").write_text(TOKEN)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (tmp_path / "home").mkdir()                 # the sandbox's $HOME always exists
    (run / "shim.json").write_text(json.dumps({"wait_seconds": 5, "local_classes": ["worktree_edit"],
                                               "worktree": str(worktree)}))
    return run


@pytest.fixture
def live(run: Path) -> Iterator[SessionServer]:
    s = SessionServer(run / "s.sock", TOKEN, run.parent / "events.jsonl")
    s.start()
    yield s
    s.close()


def env_for(run: Path) -> dict[str, str]:
    return {"HZ_SESSION_SOCKET": str(run / "s.sock"), "HOME": str(run.parent / "home")}


def hook(run: Path, payload: dict[str, object], t: FakeTime | None = None) -> tuple[int, str]:
    t = t or FakeTime()
    return shim.run_hook(json.dumps(payload).encode(), env_for(run), clock=t.clock, sleep=t.sleep)


def denied(out: str) -> str:
    body = json.loads(out)["hookSpecificOutput"]
    assert body["hookEventName"] == "PreToolUse" and body["permissionDecision"] == "deny"
    return body["permissionDecisionReason"]


def test_answered_hook_is_allowed_silently(run: Path, live: SessionServer) -> None:
    assert hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Bash"}) == (0, "")
    assert hook(run, {"hook_event_name": "Stop"}) == (0, "")
    assert live.turns().stops == 1


def test_wrong_token_denies_a_tool_call(run: Path, live: SessionServer) -> None:
    (run / "token").write_text("wrong")
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    assert rc == 0 and "refused" in denied(out)


def test_wsd_down_waits_then_fails_closed(run: Path) -> None:
    t = FakeTime()
    bash = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    rc, out = hook(run, bash, t)
    assert rc == 0 and denied(out) == shim.DENY_REASON and t.now >= 5


def test_wsd_down_allows_a_worktree_edit_only(run: Path, tmp_path: Path) -> None:
    inside = str(tmp_path / "wt" / "src" / "a.py")
    outside = str(tmp_path / "elsewhere.py")
    escape = str(tmp_path / "wt" / ".." / "elsewhere.py")
    assert hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                      "tool_input": {"file_path": inside}}) == (0, "")
    for path in (outside, escape):
        write = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": path}}
        rc, out = hook(run, write)
        assert denied(out) == shim.DENY_REASON
    (tmp_path / "wt" / "link").symlink_to(tmp_path)
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                         "tool_input": {"file_path": str(tmp_path / "wt" / "link" / "x")}})
    assert denied(out) == shim.DENY_REASON


def test_worktree_edit_needs_its_class(run: Path, tmp_path: Path) -> None:
    cfg = json.loads((run / "shim.json").read_text())
    (run / "shim.json").write_text(json.dumps({**cfg, "local_classes": []}))
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                         "tool_input": {"file_path": str(tmp_path / "wt" / "a.py")}})
    assert denied(out) == shim.DENY_REASON


def test_wsd_down_spools_other_events(run: Path) -> None:
    assert hook(run, {"hook_event_name": "Stop", "last_assistant_message": "done"}) == (0, "")
    spool = run.parent / "home" / ".hz" / "spool.jsonl"
    assert json.loads(spool.read_text())["payload"]["hook_event_name"] == "Stop"


def test_a_missing_config_fails_closed(run: Path) -> None:
    (run / "shim.json").unlink()
    rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit"})
    assert denied(out) == shim.DENY_REASON


def test_plan5_deny_reply_is_passed_through(run: Path) -> None:
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(run / "s.sock"))
    srv.listen(1)

    def answer() -> None:
        conn, _ = srv.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b'{"ok": true, "decision": "deny", "reason": "needs approval"}\n')

    threading.Thread(target=answer, daemon=True).start()
    try:
        rc, out = hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    finally:
        srv.close()
    assert denied(out) == "needs approval"


def test_request(run: Path, live: SessionServer) -> None:
    t = FakeTime()
    rc, out = shim.run_request(["please", "push"], env_for(run), clock=t.clock, sleep=t.sleep)
    assert (rc, json.loads(out)) == (1, {"ok": False, "error": "unsupported"})


def test_request_with_wsd_down(run: Path) -> None:
    t = FakeTime()
    rc, out = shim.run_request(["x"], env_for(run), clock=t.clock, sleep=t.sleep)
    assert (rc, out) == (shim.EX_TEMPFAIL, shim.DENY_REASON)


def test_the_file_runs_standalone_in_isolated_mode(run: Path, live: SessionServer) -> None:
    """As inside the sandbox: `python3 -I shim.py hook`, with no heterodyne on the path."""
    proc = subprocess.run([sys.executable, "-I", str(Path(shim.__file__)), "hook"], env=env_for(run),
                          input=b'{"hook_event_name": "Stop"}', capture_output=True, timeout=30, check=False)
    assert (proc.returncode, proc.stdout) == (0, b"")
    assert live.turns().stops == 1


# r1 review: the hook input, the reply and $HOME are all untrusted.


class Ticking(FakeTime):
    """A clock that moves on by `tick` every time it is read, as a slow peer's clock would."""

    def __init__(self, tick: float) -> None:
        super().__init__()
        self.tick = tick

    def clock(self) -> float:
        self.now += self.tick
        return self.now


def bash() -> dict[str, object]:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}


def serving(run: Path, answer: Callable[[socket.socket], None]) -> socket.socket:
    """A peer on the session socket that runs `answer` on every connection."""
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(run / "s.sock"))
    srv.listen(8)

    def loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn, contextlib.suppress(OSError):
                conn.recv(65536)
                answer(conn)

    threading.Thread(target=loop, daemon=True).start()
    return srv


def test_a_trickling_reply_meets_the_deadline(run: Path) -> None:
    def trickle(conn: socket.socket) -> None:
        while True:
            conn.sendall(b" " * 512)            # never a newline, never silent long enough to time out

    srv = serving(run, trickle)
    try:
        t = Ticking(1.0)
        rc, out = hook(run, bash(), t)
    finally:
        srv.close()
    assert denied(out) == shim.DENY_REASON and t.now <= 5 + 3


def test_a_silent_peer_meets_the_deadline(run: Path) -> None:
    cfg = json.loads((run / "shim.json").read_text())
    (run / "shim.json").write_text(json.dumps({**cfg, "wait_seconds": 3.1}))
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(run / "s.sock"))
    srv.listen(8)                               # connects, but nothing ever answers
    try:
        t = Ticking(1.0)                        # what is left for the read is 0.1 s
        assert denied(hook(run, bash(), t)[1]) == shim.DENY_REASON
    finally:
        srv.close()


def test_an_overlong_reply_is_no_answer(run: Path) -> None:
    attempts: list[int] = []

    def flood(conn: socket.socket) -> None:
        attempts.append(1)
        conn.sendall(b"x" * (shim.MAX_REPLY + 9000))

    srv = serving(run, flood)
    try:
        assert denied(hook(run, bash())[1]) == shim.DENY_REASON
    finally:
        srv.close()
    assert len(attempts) > 1                    # retried, within the one deadline


def test_a_fifo_spool_never_blocks(run: Path) -> None:
    folder = run.parent / "home" / ".hz"
    folder.mkdir()
    os.mkfifo(folder / "spool.jsonl")
    result: list[tuple[int, str]] = []
    worker = threading.Thread(target=lambda: result.append(hook(run, {"hook_event_name": "Stop"})),
                              daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive() and result == [(0, "")]


def test_a_linked_spool_is_not_followed(run: Path, tmp_path: Path) -> None:
    folder = run.parent / "home" / ".hz"
    folder.mkdir()
    target = tmp_path / "outside.jsonl"
    (folder / "spool.jsonl").symlink_to(target)
    assert hook(run, {"hook_event_name": "Stop"}) == (0, "")
    assert not target.exists()


def test_a_missing_home_is_made_for_the_spool(run: Path, tmp_path: Path) -> None:
    home = tmp_path / "no" / "home"
    t = FakeTime()
    env = {**env_for(run), "HOME": str(home)}
    assert shim.run_hook(b'{"hook_event_name": "Stop"}', env, clock=t.clock, sleep=t.sleep) == (0, "")
    assert json.loads((home / ".hz" / "spool.jsonl").read_text())["payload"]["hook_event_name"] == "Stop"


@pytest.mark.parametrize("raw", [b"not json", b"[1]", b"[" * 5000 + b"]" * 5000,
                                 b'{"hook_event_name": "PreToolUse", "x": ' + b"[" * 200 + b"]" * 200 + b"}"])
def test_unreadable_input_is_denied(run: Path, raw: bytes) -> None:
    t = FakeTime()
    rc, out = shim.run_hook(raw, env_for(run), clock=t.clock, sleep=t.sleep)
    assert rc == 0 and denied(out) == shim.DENY_REASON


@pytest.mark.parametrize("edit", [{"tool_name": [], "tool_input": {"file_path": "a.py"}},
                                  {"tool_name": "Edit", "tool_input": {"file_path": "/x/\0/a.py"}},
                                  {"tool_name": "Edit", "tool_input": []},
                                  {"tool_name": "Edit", "tool_input": {"file_path": 7}}])
def test_odd_fields_deny_a_tool_call(run: Path, edit: dict[str, object]) -> None:
    rc, out = hook(run, {"hook_event_name": "PreToolUse", **edit})
    assert rc == 0 and denied(out) == shim.DENY_REASON


def test_odd_config_values_deny_a_tool_call(run: Path, tmp_path: Path) -> None:
    cfg = json.loads((run / "shim.json").read_text())
    edit = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "wt" / "a.py")}}
    for odd in ({"local_classes": 7}, {"worktree": "wt"}, {"wait_seconds": "x"}):
        (run / "shim.json").write_text(json.dumps({**cfg, **odd}))
        expect = (0, "") if "wait_seconds" in odd else None      # a bad wait falls back to the default
        result = hook(run, edit)
        if expect is None:
            assert denied(result[1]) == shim.DENY_REASON
        else:
            assert result == expect


def test_a_relative_path_counts_only_against_an_absolute_cwd(run: Path, tmp_path: Path) -> None:
    def edit(**extra: object) -> tuple[int, str]:
        return hook(run, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                          "tool_input": {"file_path": "a.py"}, **extra})

    assert edit(cwd=str(tmp_path / "wt")) == (0, "")
    for cwd in ({"cwd": str(tmp_path)}, {"cwd": "wt"}, {}, {"cwd": 3}):
        assert denied(edit(**cwd)[1]) == shim.DENY_REASON
