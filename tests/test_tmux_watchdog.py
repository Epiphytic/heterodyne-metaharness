"""Offline tests must not leak tmux servers (spec 2026-10-08-test-tmux-leak-design.md, btq-q1r4p)."""

import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from tmux_guard import start_watchdog
from tmux_watchdog import Sweeper

from heterodyne import tmux as tmux_mod
from heterodyne.tmux import Tmux


class Calls:
    """Fake subprocess.run that records argv and always succeeds."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def _drive(t: Tmux, tmp_path: Path) -> None:
    t.new_session("s", tmp_path, ["true"])
    t.has_session("s")
    t.capture("s", 5)
    t.kill_server()


@pytest.mark.parametrize("launcher", [None, ("systemd-run", "--user")])
def test_socket_name_selects_with_dash_l(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                         launcher: tuple[str, ...] | None) -> None:
    rec = Calls()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    _drive(Tmux("hz-test-x", launcher=(lambda: launcher) if launcher else None), tmp_path)
    prefix = list(launcher or ())
    assert rec.calls[0][:len(prefix) + 3] == [*prefix, "tmux", "-L", "hz-test-x"]
    assert all(c[:3] == ["tmux", "-L", "hz-test-x"] for c in rec.calls[1:])


@pytest.mark.parametrize("launcher", [None, ("systemd-run", "--user")])
def test_socket_path_selects_with_dash_s(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                         launcher: tuple[str, ...] | None) -> None:
    rec = Calls()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    path = tmp_path / "sock"
    t = Tmux("hz-test-x", launcher=(lambda: launcher) if launcher else None, socket_path=path)
    _drive(t, tmp_path)
    prefix = list(launcher or ())
    assert rec.calls[0][:len(prefix) + 3] == [*prefix, "tmux", "-S", str(path)]
    assert all(c[:3] == ["tmux", "-S", str(path)] for c in rec.calls[1:])
    assert not any("-L" in c for c in rec.calls)


# --- the sweep, with fake probes (test 9 and the closure loop) ---

class FakeSweeper(Sweeper):
    """kill-server always "succeeds"; connect, PID and lock answers are scripted; time is fake."""

    def __init__(self, dead: Path, *, pid: int | None = 4242, connect: str = "refused",
                 running: bool = False, lock_free: bool = True, deadline: float = 30.0) -> None:
        self.clock = 0.0
        super().__init__(dead, deadline=deadline, log=lambda _line: None)
        self.pid, self.state, self.running, self.lock_free = pid, connect, running, lock_free
        self.commands: list[tuple[str, ...]] = []

    def now(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def tmux(self, path: Path, *args: str) -> tuple[int, str]:
        self.commands.append(args)
        if args[0] == "display-message":
            return (0, f"{self.pid}\n") if self.pid is not None else (1, "")
        return (0, "")

    def connect(self, path: Path) -> str:
        return self.state if path.exists() else "missing"

    def pid_running(self, pid: int) -> bool:
        return self.running

    def try_lock(self, lock: Path) -> tuple[bool, int | None]:
        return self.lock_free, None


def _dead_with(tmp_path: Path, *names: str) -> Path:
    dead = tmp_path / "dead"
    dead.mkdir()
    for n in names:
        (dead / n).touch()
    return dead


@pytest.mark.parametrize(("connect", "running"), [("refused", True), ("accept", False)])
def test_a_kill_that_cannot_be_proven_is_survived(tmp_path: Path, connect: str, running: bool) -> None:
    dead = _dead_with(tmp_path, "aa")
    s = FakeSweeper(dead, connect=connect, running=running)
    closed = s.run()
    summary = s.summary(closed)
    assert not closed and (dead / "aa").exists()
    assert summary["survived"] == [{"path": str(dead / "aa"), "pid": 4242}]
    assert summary["killed"] == summary["stale"] == summary["unresolved"] == []
    assert ("kill-server",) in s.commands


def test_a_proven_kill_is_unlinked_and_closes(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")
    s = FakeSweeper(dead)
    closed = s.run()
    assert closed and not dead.exists()
    assert s.summary(closed) == {"killed": [{"path": str(dead / "aa"), "pid": 4242}], "stale": [],
                                 "survived": [], "unresolved": [], "closed": True}


@pytest.mark.parametrize(("lock_free", "outcome"), [(True, "stale"), (False, "unresolved")])
def test_unknown_pid_and_refused_needs_the_startup_lock(tmp_path: Path, lock_free: bool,
                                                        outcome: str) -> None:
    dead = _dead_with(tmp_path, "aa", "aa.lock")
    s = FakeSweeper(dead, pid=None, lock_free=lock_free, deadline=2.0)
    closed = s.run()
    summary = s.summary(closed)
    assert summary[outcome] == [{"path": str(dead / "aa"), "pid": None}]
    assert closed == lock_free and (dead / "aa").exists() != lock_free
    assert ("kill-server",) not in s.commands


def test_a_held_lock_with_no_socket_is_unresolved_at_the_deadline(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa.lock")
    s = FakeSweeper(dead, lock_free=False, deadline=2.0)
    closed = s.run()
    assert not closed and s.summary(closed)["unresolved"] == [{"path": str(dead / "aa.lock"), "pid": None}]


def test_an_entry_bound_after_the_first_sweep_is_still_swept(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path)

    class LateBind(FakeSweeper):
        def sweep(self) -> None:
            super().sweep()
            if not (dead / "late").exists() and not self.summary(False)["killed"]:
                (dead / "late").touch()            # a bind that resolved the root before the rename

    s = LateBind(dead)
    closed = s.run()
    assert closed and not dead.exists()
    assert s.summary(closed)["killed"] == [{"path": str(dead / "late"), "pid": 4242}]


def test_a_later_pass_resolves_an_earlier_survivor(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")

    class SlowDeath(FakeSweeper):
        def pid_running(self, pid: int) -> bool:
            return self.clock < 6.0                # outlives the first 5 s death wait only

    s = SlowDeath(dead)
    closed = s.run()
    summary = s.summary(closed)
    assert closed and summary["survived"] == [] and len(summary["killed"]) == 1


# --- the watchdog for real: child pytests killed mid-run (tests 1-8, 10, 11, 13) ---

TESTS = Path(__file__).resolve().parent
BUDGET = 90.0 if sys.platform == "darwin" else 60.0     # watchdog deadline (30 s) plus CI margin
needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

CHILD_PRELUDE = '''
import json, os, subprocess, sys, threading, time
from pathlib import Path
import pytest
from tmux_guard import new_test_socket_path
from heterodyne.tmux import Tmux

HAND = Path(os.environ["HZ_CHILD_DIR"]) / "hand.json"
MARK = os.environ["HZ_CHILD_MARK"]
PANE = [sys.executable, "-c", "import time; time.sleep(600)", MARK]


def tmux_out(sock, *args):
    return subprocess.run(["tmux", "-S", str(sock), *args], capture_output=True, text=True).stdout


def pids(sock):
    server = int(tmux_out(sock, "display-message", "-p", "#{pid}").strip())
    return [server, *(int(p) for p in tmux_out(sock, "list-panes", "-a", "-F", "#{pane_pid}").split())]


def hand(sock, ps, **extra):
    tmp = HAND.with_suffix(".tmp")
    tmp.write_text(json.dumps({"run": str(Path(sock).parent.parent), "socket": str(sock), "pids": ps,
                               **extra}))
    tmp.replace(HAND)


def block():
    threading.Event().wait(600)
'''


def until(pred: Callable[[], object], what: str, budget: float = BUDGET) -> None:
    end = time.monotonic() + budget
    while not pred():
        assert time.monotonic() < end, f"timed out waiting for {what}"
        time.sleep(0.05)


def running(pid: int) -> bool:
    return Sweeper(Path("/nonexistent")).pid_running(pid)


def gone(pid: int) -> bool:
    """Not even a zombie: the process was reaped."""
    if Path("/proc/self").exists():
        return not Path(f"/proc/{pid}").exists()
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, check=False)
    return not out.stdout.strip()


def matching(text: str) -> list[str]:
    out = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, check=True)
    lines = out.stdout.decode("utf-8", "replace").splitlines()
    return [ln for ln in lines if text in ln and int(ln.split()[0]) != os.getpid()]


class Child:
    """A child pytest that loads only the tmux_guard plugin, in its own session and directory."""

    def __init__(self, tmp_path: Path, body: str, env: dict[str, str] | None = None) -> None:
        self.dir = tmp_path / "child"
        self.dir.mkdir()
        (self.dir / "pytest.ini").write_text("[pytest]\n")
        (self.dir / "test_child.py").write_text(CHILD_PRELUDE + body)
        self.hand_path = tmp_path / "hand.json"
        self.out = tmp_path / "child.out"
        self.mark = f"hz-mark-{uuid.uuid4().hex}"
        self.pids: list[int] = []
        self.run: Path | None = None
        base = {k: v for k, v in os.environ.items() if not k.startswith("HZ_TMUX_WATCHDOG_")}
        full = {**base, "PYTHONPATH": os.pathsep.join([str(TESTS), os.environ.get("PYTHONPATH", "")]),
                "HZ_CHILD_DIR": str(tmp_path), "HZ_CHILD_MARK": self.mark, **(env or {})}
        with self.out.open("wb") as out:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "pytest", "-q", "-p", "tmux_guard", "-p", "no:cacheprovider",
                 "-c", str(self.dir / "pytest.ini"), "--rootdir", str(self.dir), "test_child.py"],
                cwd=self.dir, env=full, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                start_new_session=True)

    def output(self) -> str:
        return self.out.read_text(errors="replace")

    def hand(self) -> dict[str, Any]:
        until(lambda: self.hand_path.exists() or self.proc.poll() is not None, "the child's handshake")
        assert self.hand_path.exists(), self.output()
        data: dict[str, Any] = json.loads(self.hand_path.read_text())
        self.pids, self.run = data["pids"], Path(data["run"])
        return data

    def finished(self) -> int:
        try:
            return self.proc.wait(BUDGET)
        except subprocess.TimeoutExpired:
            raise AssertionError(self.output()) from None

    def summary(self) -> dict[str, Any]:
        assert self.run is not None
        path = self.run / "summary.json"
        until(path.exists, "the watchdog's summary")
        result: dict[str, Any] = json.loads(path.read_text())
        return result

    def cleanup(self) -> None:
        """Parent-side safety net: whatever a failed assertion left running."""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(10)
        for pid in self.pids:
            if running(pid):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        for line in matching(self.mark):
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(line.split()[0]), signal.SIGKILL)
        if self.run is not None and self.run.exists():
            until(lambda: not any("tmux_watchdog" in ln and str(self.run) in ln
                                  for ln in matching(str(self.run))), "the watchdog to exit")
            shutil.rmtree(self.run, ignore_errors=True)


@pytest.fixture
def child(tmp_path: Path) -> Iterator[Callable[..., Child]]:
    made: list[Child] = []

    def make(body: str, env: dict[str, str] | None = None) -> Child:
        made.append(Child(tmp_path, body, env))
        return made[-1]
    yield make
    for c in made:
        c.cleanup()


def assert_cleaned(c: Child, data: dict[str, Any], outcome: str = "killed") -> dict[str, Any]:
    assert c.run is not None
    summary = c.summary()
    until(lambda: not any(running(p) for p in c.pids), "every captured PID to die")
    dead = c.run / "dead" / Path(data["socket"]).name
    server = c.pids[0] if outcome == "killed" else None
    assert summary == {"killed": [], "stale": [], "survived": [], "unresolved": [], "closed": True,
                       outcome: [{"path": str(dead), "pid": server}]}
    assert not Path(data["socket"]).exists() and not (c.run / "s").exists()
    assert not (c.run / "dead").exists() and not matching(c.mark)
    return summary


RUN_WITH_BODY = '''
import asyncio
from test_admind_daemon import run_with

def test_child(tmp_path):
    async def scenario(h):
        await h.say("hello")
        await h.until(lambda: "echo: hello" in h.texts(), 30)
        hand(h.tmux.socket_path, pids(h.tmux.socket_path))
        await asyncio.sleep(600)
    run_with(tmp_path, scenario)
'''

FIXTURE_BODY = '''
from test_tmux import tmux

def test_child(tmux, tmp_path):
    tmux.new_session("a", tmp_path, PANE)
    tmux.new_session("b", tmp_path, PANE)
    hand(tmux.socket_path, pids(tmux.socket_path))
    block()
'''


@needs_tmux
@pytest.mark.parametrize("body", [RUN_WITH_BODY, FIXTURE_BODY], ids=["run_with", "fixture"])
def test_sigkill_of_pytest_kills_its_servers_and_panes(child: Callable[..., Child], body: str) -> None:
    c = child(body)
    data = c.hand()
    assert len(c.pids) >= 2
    os.kill(c.proc.pid, signal.SIGKILL)
    c.finished()
    assert_cleaned(c, data)


@needs_tmux
def test_killpg_of_pytest_spares_the_watchdog(child: Callable[..., Child]) -> None:
    c = child(FIXTURE_BODY)
    data = c.hand()
    os.killpg(c.proc.pid, signal.SIGKILL)
    c.finished()
    assert_cleaned(c, data)


@needs_tmux
def test_a_launch_released_after_the_rename_starts_nothing(child: Callable[..., Child],
                                                           tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    c = child('''
def test_child(tmp_path):
    sock = new_test_socket_path()
    gate = os.environ["HZ_CHILD_DIR"]
    script = ('echo $$ > "$0/started.tmp"; mv "$0/started.tmp" "$0/started"; '
              'while [ ! -e "$0/gate" ]; do sleep 0.05; done; '
              '"$@" > "$0/launch.tmp" 2>&1; mv "$0/launch.tmp" "$0/launch"')
    hand(sock, [])
    t = Tmux("x", launcher=lambda: ("sh", "-c", script, gate), socket_path=sock)
    t.new_session("s", tmp_path, PANE)
''')
    data = c.hand()
    run = c.run
    assert run is not None
    until((tmp_path / "started").exists, "the launcher to be waiting at the gate")
    c.pids = [int((tmp_path / "started").read_text().split()[0])]       # the gated launcher itself
    os.kill(c.proc.pid, signal.SIGKILL)
    c.finished()
    until(lambda: not (run / "s").exists(), "the rename")
    gate.touch()
    until((tmp_path / "launch").exists, "the late launch to finish")
    assert "No such file or directory" in (tmp_path / "launch").read_text()
    until(lambda: not run.exists(), "the watchdog to remove run")
    assert not matching(c.mark) and not matching(data["socket"])


@pytest.mark.parametrize("held", [True, False], ids=["lock-held", "lock-released"])
def test_a_bound_but_not_listening_socket_needs_the_startup_lock(held: bool) -> None:
    run = Path(tempfile.mkdtemp(prefix="hzt", dir="/tmp")).resolve()
    (run / "s").mkdir(mode=0o700)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lock = os.open(run / "s" / "ab.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        sock.bind(str(run / "s" / "ab"))                   # bound, never listening: connect is refused
        fcntl.flock(lock, fcntl.LOCK_EX)                   # as a tmux startup holds it until listen
        proc = start_watchdog(run, extra=("--deadline", "3"))
        if not held:
            os.close(lock)
            lock = -1
        assert proc.stdin is not None
        proc.stdin.close()
        assert proc.wait(BUDGET) == 0
        summary = json.loads((run / "summary.json").read_text())
        entry = [{"path": str(run / "dead" / "ab"), "pid": None}]
        if held:
            assert summary == {"killed": [], "stale": [], "survived": [], "unresolved": entry,
                               "closed": False}
            assert (run / "dead" / "ab").exists()
        else:
            assert summary == {"killed": [], "stale": entry, "survived": [], "unresolved": [], "closed": True}
            assert not (run / "dead").exists()
    finally:
        sock.close()
        if lock >= 0:
            os.close(lock)
        shutil.rmtree(run, ignore_errors=True)


@needs_tmux
def test_sigkill_after_kill_server_but_before_unlink_is_stale(child: Callable[..., Child]) -> None:
    c = child('''
def test_child(tmp_path):
    sock = new_test_socket_path()
    t = Tmux("x", socket_path=sock)
    t.new_session("s", tmp_path, PANE)
    ps = pids(sock)
    t.kill_server()                                  # drop_tmux, stopped before its unlink
    end = time.monotonic() + 30
    while Path(f"/proc/{ps[0]}").exists() if Path("/proc/self").exists() else subprocess.run(
            ["ps", "-p", str(ps[0])], capture_output=True).returncode == 0:
        assert time.monotonic() < end
        time.sleep(0.05)
    hand(sock, ps)
    block()
''')
    data = c.hand()
    assert Path(data["socket"]).exists()
    os.kill(c.proc.pid, signal.SIGKILL)
    c.finished()
    assert_cleaned(c, data, outcome="stale")
    assert c.run is not None
    assert "kill-server" not in (c.run / "watchdog.log").read_text()


@needs_tmux
def test_a_hook_child_dies_with_its_pane(child: Callable[..., Child]) -> None:
    c = child('''
def test_child(tmux, tmp_path):
    hook = tmp_path / "hook.pid"
    tmux.new_session("s", tmp_path, ["sh", "-c", f"{sys.executable} -c 'import time; time.sleep(600)' "
                                     f"{MARK} & echo $! > {hook}; wait"])
    end = time.monotonic() + 30
    while not hook.exists() or not hook.read_text().strip():
        assert time.monotonic() < end
        time.sleep(0.05)
    hand(tmux.socket_path, [*pids(tmux.socket_path), int(hook.read_text())])
    block()

from test_tmux import tmux
''')
    data = c.hand()
    assert len(c.pids) == 3
    os.kill(c.proc.pid, signal.SIGKILL)
    c.finished()
    assert_cleaned(c, data)


MAIN_SCRIPT = '''
import json, os, subprocess, sys, time
from pathlib import Path
import pytest
import tmux_guard
child = Path(sys.argv[1])
code = pytest.main(["-q", "-p", "tmux_guard", "-p", "no:cacheprovider", "-c", str(child / "pytest.ini"),
                    "--rootdir", str(child), str(child / "test_child.py")])
g = tmux_guard.last
end = time.monotonic() + float(sys.argv[2])
while g.proc.returncode is None and time.monotonic() < end:     # set by wait(), here or in the reaper
    time.sleep(0.05)
pid = g.proc.pid
if Path("/proc/self").exists():
    reaped = not Path(f"/proc/{pid}").exists()
else:     # a zombie still shows in ps until it is reaped
    reaped = not subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True).stdout.strip()
print("RESULT " + json.dumps({"code": int(code), "pid": pid, "returncode": g.proc.returncode,
                              "timed_out": g.timed_out, "run": str(g.run), "run_exists": g.run.exists(),
                              "reaped": reaped}))
'''


@needs_tmux
@pytest.mark.parametrize("slow", [False, True], ids=["prompt", "slow-cleanup"])
def test_pytest_main_in_a_live_interpreter_reaps_its_watchdog(tmp_path: Path, slow: bool) -> None:
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    (child_dir / "pytest.ini").write_text("[pytest]\n")
    mark = f"hz-mark-{uuid.uuid4().hex}"
    leak = "    Tmux('x', socket_path=new_test_socket_path()).new_session('l', tmp_path, PANE)\n"
    leak = leak if slow else ""
    (child_dir / "test_child.py").write_text(CHILD_PRELUDE + "from test_tmux import tmux\n\n"
                                             "def test_child(tmux, tmp_path):\n"
                                             "    tmux.new_session('s', tmp_path, PANE)\n" + leak)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(TESTS), os.environ.get("PYTHONPATH", "")]),
           "HZ_CHILD_DIR": str(tmp_path), "HZ_CHILD_MARK": mark}
    env.pop("HZ_TMUX_WATCHDOG_WAIT", None)
    if slow:
        env["HZ_TMUX_WATCHDOG_WAIT"] = "0"
    proc = subprocess.run([sys.executable, "-c", MAIN_SCRIPT, str(child_dir), str(BUDGET)], env=env,
                          capture_output=True, text=True, timeout=BUDGET * 2, check=False)
    try:
        line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT "))
        result = json.loads(line.removeprefix("RESULT "))
        assert result["code"] == 0 and result["returncode"] == 0 and result["reaped"]
        assert gone(result["pid"]) and not matching(mark)
        assert result["timed_out"] is slow
        if slow:
            assert "still cleaning up" in proc.stderr
            assert json.loads((Path(result["run"]) / "summary.json").read_text())["killed"]
        else:
            assert not result["run_exists"] and "tmux_guard" not in proc.stderr
    finally:
        for ln in matching(mark):
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(ln.split()[0]), signal.SIGKILL)
        for run in [Path(ln) for ln in re.findall(r'"run": "([^"]+)"', proc.stdout)]:
            shutil.rmtree(run, ignore_errors=True)


@needs_tmux
def test_a_test_that_skips_its_teardown_is_reported(child: Callable[..., Child]) -> None:
    c = child('''
def test_child(tmp_path):
    sock = new_test_socket_path()
    Tmux("x", socket_path=sock).new_session("s", tmp_path, PANE)
    hand(sock, pids(sock))
''')
    data = c.hand()
    assert c.finished() == 0
    assert_cleaned(c, data)
    out = c.output()
    assert "teardown gap" in out and str(c.run) + "/dead/" + Path(data["socket"]).name in out


HANG = ("import os, time\n"
        "open(os.environ['HZ_CHILD_DIR'] + '/wd.pid', 'w').write(str(os.getpid()))\n"
        "time.sleep(600)\n")


@pytest.mark.parametrize("failure", ["bad-path", "no-ack"])
def test_a_watchdog_that_does_not_start_is_reaped_and_nothing_launches(
        child: Callable[..., Child], tmp_path: Path, failure: str) -> None:
    hang = tmp_path / "hang.py"
    hang.write_text(HANG)
    script = str(tmp_path / "missing.py") if failure == "bad-path" else str(hang)
    c = child('''
from test_tmux import tmux

def zombies():
    out = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,stat="], capture_output=True, text=True).stdout
    rows = [ln.split() for ln in out.splitlines()]
    return [r for r in rows if int(r[1]) == os.getpid() and r[2].startswith("Z")]

def test_reaped_and_refused():
    import tmux_guard
    with pytest.raises(tmux_guard.WatchdogError):
        new_test_socket_path()
    assert not zombies()
    pid_file = Path(os.environ["HZ_CHILD_DIR"]) / "wd.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text())
        assert not Path(f"/proc/{pid}").exists() if Path("/proc/self").exists() else subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True).stdout.strip() == b""

def test_a_tmux_test_errors(tmux):
    tmux.new_session("s", Path("."), PANE)
''', env={"HZ_TMUX_WATCHDOG_SCRIPT": script, "HZ_TMUX_WATCHDOG_ACK_TIMEOUT": "1"})
    assert c.finished() != 0
    out = c.output()
    assert "1 passed, 1 error" in out and "WatchdogError" in out, out
    assert (tmp_path / "wd.pid").exists() == (failure == "no-ack")
    assert not matching(c.mark)


@needs_tmux
def test_the_socket_root_ignores_tmpdir_and_tmux_tmpdir(child: Callable[..., Child], tmp_path: Path) -> None:
    (tmp_path / "tmpdir").mkdir()
    (tmp_path / "tmuxdir").mkdir()
    c = child('''
def test_child(tmux, tmp_path):
    sock = new_test_socket_path()
    assert str(sock).startswith(str(Path("/tmp").resolve()) + "/hzt") and len(str(sock)) < 40
    assert tmux.socket_path.parent == sock.parent
    tmux.new_session("s", tmp_path, PANE)
    assert tmux.socket_path.is_socket()

from test_tmux import tmux
''', env={"TMPDIR": str(tmp_path / "tmpdir"), "TMUX_TMPDIR": str(tmp_path / "tmuxdir")})
    assert c.finished() == 0, c.output()
    assert list((tmp_path / "tmuxdir").iterdir()) == []


def test_require_tmux_fails_the_session_once(child: Callable[..., Child], tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    c = child('''
def test_one():
    pass

def test_two():
    pass
''', env={"HZ_REQUIRE_TMUX": "1", "PATH": str(tmp_path / "empty")})
    assert c.finished() == pytest.ExitCode.USAGE_ERROR
    out = c.output()
    assert out.count("Exit: HZ_REQUIRE_TMUX=1 but tmux is not installed\n") == 1
    assert not re.search(r"\b(passed|failed|skipped|error)\b", out)
