import json
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
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
