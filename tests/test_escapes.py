"""Escaped test descendants end themselves, and each test proves it: tests/fakes/sleeper.py and the `escapes`
fixture (tests/escapes.py; spec 2026-10-13-u63ak-escaped-fake-cleanup-design.md, tests 1-10).

Every wait has a hang guard of the cleanup budget (10 s), not a timing claim. A process a test stops is
resumed, and every process a test starts by its own handle is waited for before the test returns. The spawn
paths that run inside a fake or a script are observed in a harness: a fresh interpreter (`AUDIT`) with an
audit hook, never this one, since an audit hook can't be removed.
"""

import fcntl
import json
import os
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from escapes import Escapes, reported, sleeper
from harness_env import FAKE_CLI
from sandbox_env import short_dir
from test_admind_approvals import DESCENDANT
from test_admind_r13_summary import SETSID, escaped_body, grandchild_body, group_sleeper_body
from test_sandbox_probes import grandchild_code

TESTS = Path(__file__).resolve().parent
FAKES = TESTS / "fakes"
BUDGET = 10.0
DEVNULL: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                           "stderr": subprocess.DEVNULL}
RATE_READ = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "account/rateLimits/read"}) + "\n"

# The harness: `python -I -c AUDIT LOG CODE ARG...` appends `[pid, event, args]` to LOG, one line per
# spawn or signal event (the hook survives fork, so a fork child's events are logged too), and per call of
# os.kill, os.killpg or signal.pidfd_send_signal (which has no audit event), then runs CODE with
# sys.argv = ["-c", ARG...] and tests/fakes and tests on sys.path.
AUDIT = f"""\
import json, os, signal, sys
LOG = sys.argv[1]
WATCH = {{"subprocess.Popen", "os.fork", "os.posix_spawn", "os.exec", "os.kill", "os.killpg"}}
def note(event, args):
    line = json.dumps([os.getpid(), event, [str(a) for a in args[:2]]]) + "\\n"
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)
def hook(event, args):
    if event in WATCH:
        note(event, args)
sys.addaudithook(hook)
for mod, name in ((os, "kill"), (os, "killpg"), (signal, "pidfd_send_signal")):
    def wrapped(*a, _real=getattr(mod, name), _name=name):
        note("called " + _name, a)
        return _real(*a)
    setattr(mod, name, wrapped)
sys.path[:0] = [{str(FAKES)!r}, {str(TESTS)!r}]
code = sys.argv[2]
sys.argv = ["-c", *sys.argv[3:]]
exec(compile(code, "<harness>", "exec"), {{"__name__": "__main__"}})
"""
SPAWNS = ("subprocess.Popen", "os.fork", "os.posix_spawn", "os.exec")


def until(ok: Callable[[], object], what: str, bound: float = BUDGET) -> None:
    deadline = time.monotonic() + bound
    while not ok():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what}: not within {bound} s")
        time.sleep(0.02)


def census_held(path: Path) -> bool:
    """Whether some fd holds the census: LOCK_EX|LOCK_NB fails. Only while nothing spawns into the use."""
    fd = os.open(path / sleeper.CENSUS, os.O_RDONLY | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def ready(r: int, bound: float = BUDGET) -> bytes:
    """The ready byte from pipe `r` (b"" once every writer is gone), within the hang guard."""
    with selectors.DefaultSelector() as selector:
        selector.register(r, selectors.EVENT_READ)
        readable = selector.select(bound)
    assert readable, f"no ready byte or EOF within {bound} s"
    return os.read(r, 1)


def state(pid: int) -> str:
    """ps's state letters (ps, not /proc: CI runs macOS too); empty once there's no such process."""
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                          timeout=10).stdout.strip()


def alive(pid: int) -> bool:
    s = state(pid)
    return s != "" and not s.startswith("Z")


@pytest.fixture
def held_exclusively(escapes: Escapes) -> Iterator[None]:
    """The census held LOCK_EX by the test, as by a teardown under way."""
    fd = os.open(escapes.path / sleeper.CENSUS, os.O_RDONLY | os.O_CLOEXEC)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.close(fd)


def harness(tmp: Path, code: str, *argv: str, env: dict[str, str] | None = None, stdin: str = "",
            log: Path | None = None) -> tuple[int, str, list[list[Any]]]:
    """Run CODE under AUDIT to its end: (exit status, its stdout and stderr, the logged events). Its output
    goes to a file, never a pipe: a sleeper it starts may hold that open."""
    log = log or tmp / "audit.log"
    out = tmp / "harness.out"
    full = {k: v for k, v in os.environ.items() if k != sleeper.ENV} | (env or {})
    with out.open("wb") as f:
        proc = subprocess.run([sys.executable, "-I", "-c", AUDIT, str(log), code, *argv],
                              input=stdin.encode(), stdout=f, stderr=subprocess.STDOUT, env=full,
                              timeout=BUDGET * 3)
    return proc.returncode, out.read_text(errors="replace"), events(log)


def events(log: Path) -> list[list[Any]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def names_sleeper(event: list[Any]) -> bool:
    return any(sleeper.SLEEPER in a for a in event[2])


def run_sh(script: Path, trace: Path, **popen: Any) -> "subprocess.Popen[Any]":
    """`sh -x script`, its trace on `trace`."""
    with trace.open("wb") as err:
        return subprocess.Popen(["sh", "-x", str(script)], stderr=err, **popen)


def finish(p: "subprocess.Popen[Any]") -> None:
    """Kill the test's own child if it is still running (a no-op once it has exited), then reap it."""
    p.kill()
    for f in (p.stdin, p.stdout):
        if f is not None:
            f.close()
    p.wait(BUDGET)


def sh_script(tmp: Path, body: str, name: str = "s.sh") -> Path:
    p = tmp / name
    p.write_text(body + "\n")
    return p


# --- 1-3: release, and admission before the child runs ---------------------------------------------------
def test_release_ends_a_sleeper(escapes: Escapes) -> None:
    r, w = os.pipe()
    p = sleeper.spawn(str(escapes.path), new_session=True, ready_fd=w, **DEVNULL)
    os.close(w)
    try:
        assert ready(r) == b"1"
        assert escapes.pids() == [p.pid] and census_held(escapes.path)
        escapes.end()
        assert p.wait(BUDGET) == 0
        assert not escapes.path.exists()
    finally:
        os.close(r)
        p.wait(BUDGET)


def stopped_then_released(escapes: Escapes, pid: int, r: int) -> None:
    """`pid` has stopped itself before running any sleeper code: the census is held all the same, and `end`
    fails naming no PID. Resumed, it reports, and `end` succeeds."""
    assert escapes.pids() == [] and census_held(escapes.path)
    with pytest.raises(AssertionError, match="census still held after 0.5 s; no PID reported"):
        escapes.end(bound=0.5)
    os.kill(pid, signal.SIGCONT)            # the test's own unreaped child: its PID can't have been reused
    assert ready(r) == b"1"
    assert escapes.pids() == [pid]
    escapes.end()
    assert not escapes.path.exists()


def test_admission_happens_before_a_spawned_child_runs(escapes: Escapes) -> None:
    r, w = os.pipe()
    p = sleeper.spawn(str(escapes.path), ready_fd=w, _gate=True, **DEVNULL)
    os.close(w)
    try:
        _, status = os.waitpid(p.pid, os.WUNTRACED)
        assert os.WIFSTOPPED(status)
        stopped_then_released(escapes, p.pid, r)
        assert p.wait(BUDGET) == 0
    finally:
        os.close(r)
        with_resume(p.pid)
        p.wait(BUDGET)


# Under xdist the worker has threads; the fork child runs nothing but `exec_sleeper`'s exec.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_admission_happens_before_a_forked_child_runs(escapes: Escapes) -> None:
    r, w = os.pipe()
    pid = sleeper.fork_held(str(escapes.path))
    if pid == 0:                            # nothing but the exec: the gate stops it in a fresh interpreter
        sleeper.exec_sleeper(ready_fd=w, prefix=[sys.executable, "-I", "-c", sleeper.GATE])
    os.close(w)
    reaped = False
    try:
        _, status = os.waitpid(pid, os.WUNTRACED)
        assert os.WIFSTOPPED(status)
        stopped_then_released(escapes, pid, r)
        _, status = os.waitpid(pid, 0)
        reaped = True
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        os.close(r)
        if not reaped:
            with_resume(pid)
            os.waitpid(pid, 0)


def with_resume(pid: int) -> None:
    """Resume the test's own child if a failed assertion left it stopped (a no-op once it has exited)."""
    try:
        os.kill(pid, signal.SIGCONT)
    except ProcessLookupError:
        pass


def test_admission_happens_before_a_shell_started_child_runs(escapes: Escapes, tmp_path: Path) -> None:
    gate = f"{shlex.quote(sys.executable)} -I -c {shlex.quote(sleeper.GATE)}"
    # sh stays (`wait`), so the stopped sleeper's process group is never orphaned (no SIGHUP and SIGCONT)
    script = sh_script(tmp_path, f"{escapes.shell_prelude()}{gate} {escapes.sleeper_line()} &\n"
                                 "echo $!\nexec 9<&-\nwait")
    sh = subprocess.Popen(["sh", str(script)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    try:
        assert sh.stdout is not None
        pid = int(sh.stdout.readline())     # sh's own child, unreaped while sh waits: never reused
        until(lambda: state(pid).startswith("T"), "the sleeper stopping itself")
        assert escapes.pids() == [] and census_held(escapes.path)
        with pytest.raises(AssertionError, match="no PID reported"):
            escapes.end(bound=0.5)
        os.kill(pid, signal.SIGCONT)
        until(lambda: escapes.pids() == [pid], "the sleeper reporting")
        escapes.end()
        assert not escapes.path.exists()
        assert sh.wait(BUDGET) == 0
    finally:
        finish(sh)


def take_refused(trace: Path) -> None:
    """The `sh -x` trace stops at the prelude: no sleeper line was run."""
    lines = trace.read_text().splitlines()
    assert not any("--census-fd" in line for line in lines), lines


def test_the_shell_take_refuses_a_census_held_exclusively(escapes: Escapes, tmp_path: Path,
                                                          held_exclusively: None) -> None:
    script = sh_script(tmp_path, group_sleeper_body(escapes.path, tmp_path / "pid") + "\necho after")
    sh = run_sh(script, tmp_path / "trace", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    assert sh.wait(BUDGET) == sleeper.REFUSED
    lines = (tmp_path / "trace").read_text().splitlines()
    assert "--take" in lines[-2] and lines[-1].endswith("exit 97") and not any("after" in x for x in lines)
    take_refused(tmp_path / "trace")
    assert escapes.pids() == [] and not (tmp_path / "pid").exists()


# --- 4-6: publication failing after admission, and refusal before any spawn ------------------------------
def case(name: str, use: str | None, tmp: Path, pid_file: Path) -> tuple[str, list[str], dict[str, str], str]:
    """The harness CODE, argv, env and stdin for one spawn site, with `use` as its use directory."""
    escape = {sleeper.ENV: use} if use is not None else {}
    if name in ("agent-escaped", "agent-orphan"):
        env = {"HZ_FAKE_RATE_FAULT": name.removeprefix("agent-"), "HZ_FAKE_CHILD_PID_FILE": str(pid_file),
               **escape}
        code = f"import runpy, sys; sys.argv = {[str(FAKE_CLI), '--as=codex', 'app-server']!r}; " \
               f"runpy.run_path({str(FAKE_CLI)!r}, run_name='__main__')"
        return code, [], env, RATE_READ
    if name in ("hold_stdout", "descendant"):
        env = {"FAKE_BTQ_DB": str(pid_file.parent / "btq.json"), "FAKE_BTQ_LOG": str(tmp / "btq.log"),
               **escape}
        return f"import fake_approve_bead as f; f.{name}()", [], env, ""
    if name == "DESCENDANT":
        return DESCENDANT, [str(pid_file), use or ""], {}, ""
    assert name == "probe"
    return grandchild_code(Path(use) if use else None, pid_file), [], {}, ""


PID_FILE = {"agent-escaped": "child.pid", "agent-orphan": "child.pid", "hold_stdout": "held.pid",
            "descendant": "descendant.pid", "DESCENDANT": "descendant.pid", "probe": "grandchild"}
FORKS = ("descendant", "DESCENDANT")


@pytest.mark.parametrize("name", list(PID_FILE))
def test_a_missing_pid_file_still_ends_the_sleeper_parent_publication(escapes: Escapes, tmp_path: Path,
                                                                      name: str) -> None:
    """Test 4, and test 6's valid-directory row: the spawn succeeds, then the parent's publication fails
    (its directory doesn't exist) and the parent exits. The admitted sleeper lives on, and ends."""
    pid_file = tmp_path / "missing" / PID_FILE[name]
    code, argv, env, stdin = case(name, str(escapes.path), tmp_path, pid_file)
    rc, out, logged = harness(tmp_path, code, *argv, env=env, stdin=stdin)
    assert rc != 0 and ("FileNotFoundError" in out), out
    spawns = [e for e in logged if e[1] in SPAWNS]
    if name in FORKS:
        [(parent, fork, _), (child, exec_, _)] = spawns
        assert (fork, exec_) == ("os.fork", "os.exec") and parent != child and names_sleeper(spawns[1])
    else:
        [popen] = spawns
        assert popen[1] == "subprocess.Popen" and names_sleeper(popen)
    until(escapes.pids, "the sleeper reporting")
    [pid] = escapes.pids()
    assert alive(pid) and census_held(escapes.path) and not pid_file.exists()
    escapes.end()
    assert not escapes.path.exists()


def test_a_missing_pid_file_still_ends_the_sleeper_self_publication(escapes: Escapes, tmp_path: Path) -> None:
    """Test 5: ESCAPED's sleeper publishes its own PID, into a directory that doesn't exist. It ignores the
    failure and keeps polling; the script's loop waits on (hang guard: the test's own handle stops it)."""
    pid_file = tmp_path / "missing" / "escaped.pid"
    sh = subprocess.Popen(["sh", str(sh_script(tmp_path, escaped_body(escapes.path, pid_file)))],
                          **DEVNULL)
    try:
        until(escapes.pids, "the sleeper reporting")
        [pid] = escapes.pids()
        assert alive(pid) and census_held(escapes.path) and not pid_file.exists()
        assert sh.poll() is None            # still waiting for the PID file
        escapes.end()
        assert not escapes.path.exists()
    finally:
        sh.kill()
        sh.wait(BUDGET)


@pytest.mark.parametrize("how", ["unset", "missing", "held"])
@pytest.mark.parametrize("name", list(PID_FILE))
def test_refusal_before_the_sleeper_starts_spawns_nothing(escapes: Escapes, tmp_path: Path, name: str,
                                                          how: str) -> None:
    """Test 6: the use directory unset, its census gone, or the census held exclusively (a teardown under
    way): the spawner raises before any spawn event, and the harness records none."""
    use = {"unset": None, "missing": str(escapes.path / "gone"), "held": str(escapes.path)}[how]
    pid_file = tmp_path / PID_FILE[name]
    code, argv, env, stdin = case(name, use, tmp_path, pid_file)
    fd = os.open(escapes.path / sleeper.CENSUS, os.O_RDONLY | os.O_CLOEXEC)
    try:
        if how == "held":
            fcntl.flock(fd, fcntl.LOCK_EX)
        rc, out, logged = harness(tmp_path, code, *argv, env=env, stdin=stdin)
    finally:
        os.close(fd)
    assert rc != 0 and "Refused" in out, out
    assert [e for e in logged if e[1] in SPAWNS] == []
    assert escapes.pids() == [] and not pid_file.exists()


SHELL = {"ESCAPED": escaped_body, "group": group_sleeper_body, "grandchild": grandchild_body}


@pytest.mark.parametrize("how", ["valid", "missing", "held"])
@pytest.mark.parametrize("name", list(SHELL))
def test_the_shell_prelude_spawns_only_once_admitted(escapes: Escapes, tmp_path: Path, name: str,
                                                     how: str) -> None:
    """Test 6, the shell rows: under `sh -x`, the trace shows `--take` and then the sleeper's `&` line; or,
    refused, it stops at the prelude and the script exits 97."""
    use = escapes.path / "gone" if how == "missing" else escapes.path
    trace = tmp_path / "trace"
    script = sh_script(tmp_path, SHELL[name](use, tmp_path / "pid"))
    fd = os.open(escapes.path / sleeper.CENSUS, os.O_RDONLY | os.O_CLOEXEC)
    try:
        if how == "held":
            fcntl.flock(fd, fcntl.LOCK_EX)
        with (tmp_path / "out").open("wb") as out:
            sh = run_sh(script, trace, stdin=subprocess.DEVNULL, stdout=out)
        try:
            if how != "valid":
                assert sh.wait(BUDGET) == sleeper.REFUSED
                take_refused(trace)
                assert escapes.pids() == []
                return
            until(escapes.pids, "the sleeper reporting")
            assert census_held(escapes.path)
            escapes.end()
            sh.wait(BUDGET)
        finally:
            sh.kill()
            sh.wait(BUDGET)
    finally:
        os.close(fd)
    text = trace.read_text()              # a background child's trace lines may interleave with sh's
    assert "--take" in text and text.index("--take") < text.find("--census-fd"), text


def test_escaped_setsid_part_execs_only_the_sleeper(escapes: Escapes, tmp_path: Path) -> None:
    """Test 6: ESCAPED's `-c` part, in-process under the hook, on a census fd 9 as the prelude leaves it:
    exactly one `os.exec`, of the sleeper."""
    code = ("import os, sys, sleeper\nfd = sleeper.held_census(sys.argv[1])\nos.dup2(fd, 9)\nos.close(fd)\n"
            "sys.argv = ['-c', *sleeper.sleeper_argv(sys.argv[1], 9, pid_file=sys.argv[2])]\n"
            f"exec({SETSID!r})\n")
    log, pid_file = tmp_path / "audit.log", tmp_path / "escaped.pid"
    with (tmp_path / "out").open("wb") as out:
        p = subprocess.Popen([sys.executable, "-I", "-c", AUDIT, str(log), code, str(escapes.path),
                              str(pid_file)], stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    try:
        until(pid_file.exists, "the sleeper publishing")
        assert int(pid_file.read_text()) == p.pid and escapes.pids() == [p.pid]
        assert os.getsid(p.pid) == p.pid
        escapes.end()
        assert p.wait(BUDGET) == 0
    finally:
        p.kill()
        p.wait(BUDGET)
    spawns = [e for e in events(log) if e[1] in SPAWNS]
    assert len(spawns) == 1 and spawns[0][1] == "os.exec" and names_sleeper(spawns[0])


# --- 7-8: descriptor inheritance, and a killed holder ----------------------------------------------------
FORK_HOLDER = f"""\
import os, sys
sys.path.insert(0, {str(FAKES)!r})
import sleeper
fd = sleeper.held_census(sys.argv[1])
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(w)
    os.read(r, 1)
    os._exit(0)
os.close(fd)
os.close(r)
print("closed", flush=True)
sys.stdin.readline()
os.close(w)
os.waitpid(pid, 0)
"""


def test_a_fork_child_keeps_the_census_until_it_exits(escapes: Escapes) -> None:
    holder = subprocess.Popen([sys.executable, "-I", "-c", FORK_HOLDER, str(escapes.path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert holder.stdout is not None and holder.stdin is not None
        assert holder.stdout.readline() == b"closed\n"      # the parent's copy is closed: the child holds it
        assert census_held(escapes.path)
        holder.stdin.write(b"\n")
        holder.stdin.close()
        assert holder.wait(BUDGET) == 0                      # the child has exited and been reaped
        assert not census_held(escapes.path)
    finally:
        finish(holder)


FD_GATE = """\
import fcntl, json, os, resource, sys
if sys.platform == "linux":
    fds = sorted(int(n) for n in os.listdir("/proc/self/fd") if os.path.exists(f"/proc/self/fd/{n}"))
else:
    fds = []
    for n in range(min(resource.getrlimit(resource.RLIMIT_NOFILE)[0], 1 << 16)):
        try:
            fcntl.fcntl(n, fcntl.F_GETFD)
            fds.append(n)
        except OSError:
            pass
census = int(sys.argv[sys.argv.index("--census-fd") + 1])
with open(OUT, "w") as f:
    json.dump({"fds": fds, "census": census}, f)
os.execv(sys.argv[1], sys.argv[1:])
"""


def test_a_spawned_child_gets_only_the_census_fd(escapes: Escapes, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "fds.json"
    monkeypatch.setattr(sleeper, "GATE", f"OUT = {str(out)!r}\n{FD_GATE}")
    stray = os.open(os.devnull, os.O_RDONLY)                 # inheritable, so only pass_fds keeps it out
    os.set_inheritable(stray, True)
    try:
        p = sleeper.spawn(str(escapes.path), _gate=True, **DEVNULL)
    finally:
        os.close(stray)
    try:
        until(escapes.pids, "the sleeper reporting")
        seen = json.loads(out.read_text())
        assert seen["fds"] == sorted({0, 1, 2, seen["census"]}), seen
        escapes.end()
        assert p.wait(BUDGET) == 0
    finally:
        p.kill()
        p.wait(BUDGET)


def test_the_lifeline_is_not_inherited(escapes: Escapes) -> None:
    """A child of the test with every inheritable fd (close_fds=False) is alive while `end` releases the
    lifeline, and a sleeper still exits: the lifeline's fd is O_CLOEXEC."""
    child = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE, close_fds=False)
    try:
        r, w = os.pipe()
        p = sleeper.spawn(str(escapes.path), ready_fd=w, **DEVNULL)
        os.close(w)
        assert ready(r) == b"1"
        os.close(r)
        escapes.end()
        assert p.wait(BUDGET) == 0 and child.poll() is None
    finally:
        child.communicate(timeout=BUDGET)


HOLDER = f"""\
import subprocess, sys
sys.path[:0] = [{str(FAKES)!r}, {str(TESTS)!r}]
import escapes, sleeper
use = escapes.Escapes()
sleeper.spawn(str(use.path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(use.path, flush=True)
sys.stdin.read()
"""


def test_a_killed_holder_releases_its_sleepers() -> None:
    """Test 8: a harness that holds a use's lifeline is SIGKILLed by its handle; its runnable sleeper exits
    at its next poll, which the census shows."""
    holder = subprocess.Popen([sys.executable, "-I", "-c", HOLDER], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE)
    path: Path | None = None
    try:
        assert holder.stdout is not None
        path = Path(holder.stdout.readline().decode().strip())
        until(lambda: reported(path), "the sleeper reporting")
        assert census_held(path)
        holder.kill()
        holder.wait(BUDGET)
        until(lambda: not census_held(path), "the sleeper exiting")
    finally:
        finish(holder)
        if path is not None:
            shutil.rmtree(path.parent, ignore_errors=True)       # the holder's run directory


# --- 9a, 9: stale census fds, and late sleepers ----------------------------------------------------------
STALE = """\
import fcntl, os, shutil, subprocess, sys, time
import escapes, sleeper
mode, how = sys.argv[1], sys.argv[2]
use = escapes.Escapes()
def replaced():
    use.end()
    use.path.mkdir()
    (use.path / sleeper.CENSUS).touch()
between = {"removed": use.end, "replaced": replaced, "noop": lambda: None}[how]
quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
try:
    if mode == "spawn":
        p = sleeper.spawn(str(use.path), _between_open_and_lock=between, **quiet)
    else:
        pid = sleeper.fork_held(str(use.path), _between_open_and_lock=between)
        if pid == 0:
            sleeper.exec_sleeper()
    deadline = time.monotonic() + 10
    while not use.pids() and time.monotonic() < deadline:
        time.sleep(0.02)
    fd = os.open(use.path / sleeper.CENSUS, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = False
    except OSError:
        held = True
    os.close(fd)
    print("spawned", len(use.pids()), held, flush=True)
    use.end()
    p.wait() if mode == "spawn" else os.waitpid(pid, 0)
except sleeper.Refused as e:
    print("refused:", e, flush=True)
finally:
    shutil.rmtree(escapes.run_dir(), ignore_errors=True)
"""


@pytest.mark.parametrize("how", ["removed", "replaced", "noop"])
@pytest.mark.parametrize("mode", ["spawn", "fork_held"])
def test_a_stale_census_fd_is_refused(tmp_path: Path, mode: str, how: str) -> None:
    """Test 9a: a teardown between the spawner's open and its lock (removed), then a new census at the path
    (replaced): the lock is on a dead inode, the check refuses, and nothing is spawned. With nothing in
    between, the same path spawns an admitted sleeper (positive control)."""
    rc, out, logged = harness(tmp_path, STALE, mode, how)
    spawns = [e for e in logged if e[1] in SPAWNS]
    assert rc == 0, out
    if how == "noop":
        assert out.strip() == "spawned 1 True", out
        assert [e[1] for e in spawns] == (["subprocess.Popen"] if mode == "spawn" else ["os.fork", "os.exec"])
    else:
        assert out.strip() == "refused: the escape directory's teardown has begun", out
        assert spawns == []


def test_the_shell_take_refuses_a_stale_census_fd(escapes: Escapes, tmp_path: Path) -> None:
    census = shlex.quote(str(escapes.path / sleeper.CENSUS))
    take = f"{shlex.quote(sys.executable)} -I {shlex.quote(sleeper.SLEEPER)} --take 9 " \
           f"{shlex.quote(str(escapes.path))} || exit 97"
    script = sh_script(tmp_path, f"command exec 9<{census} || exit 97\necho ready\nread x\n{take}\n"
                                 f"{escapes.sleeper_line()} &")
    sh = run_sh(script, tmp_path / "trace", stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert sh.stdout is not None and sh.stdin is not None
        assert sh.stdout.readline() == b"ready\n"           # fd 9 is open, not yet locked
        escapes.end()
        sh.stdin.write(b"go\n")
        sh.stdin.close()
        assert sh.wait(BUDGET) == sleeper.REFUSED
    finally:
        finish(sh)
    take_refused(tmp_path / "trace")
    assert any("--take" in x for x in (tmp_path / "trace").read_text().splitlines())


def test_a_late_sleeper_never_waits(escapes: Escapes, tmp_path: Path) -> None:
    """Test 9: after `end`, a spawner refuses before spawning, and a sleeper exec'd by hand on the old
    census fd exits 1. Neither writes the ready byte."""
    stale = os.open(escapes.path / sleeper.CENSUS, os.O_RDONLY)     # open, not locked: `end` isn't held
    path = escapes.path
    escapes.end()
    r, w = os.pipe()
    try:
        with pytest.raises(sleeper.Refused, match="gone"):
            sleeper.spawn(str(path), ready_fd=w, **DEVNULL)
        with pytest.raises(sleeper.Refused, match="gone"):
            sleeper.fork_held(str(path))
        path.mkdir(mode=0o700)                                          # a use dir again, a new census
        (path / sleeper.CENSUS).touch()
        try:
            p = subprocess.Popen(sleeper.sleeper_argv(str(path), stale, ready_fd=w), pass_fds=(stale, w),
                                 **DEVNULL)
            assert p.wait(BUDGET) == 1
        finally:
            shutil.rmtree(path)
        os.close(w)
        w = -1
        assert ready(r) == b""
    finally:
        os.close(stale)
        os.close(r)
        if w != -1:
            os.close(w)


# --- 10: the fixture's own life --------------------------------------------------------------------------
CHILD_INI = "[pytest]\n"
CHILD_TESTS = """\
import os, subprocess, time
from pathlib import Path
import escapes, sleeper
OUT = Path(os.environ["HZ_CHILD_OUT"])
QUIET = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

def start(use):
    sleeper.spawn(str(use.path), new_session=True, **QUIET)
    OUT.write_text(str(use.path))
    deadline = time.monotonic() + 10
    while not use.pids() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert use.pids()

def test_fails(escapes):
    start(escapes)
    assert False, "the test fails"

def test_never_ends():
    start(escapes.Escapes())
"""


def child_pytest(base: Path, test: str) -> tuple[int, str, Path]:
    """A child pytest that loads only the escapes plugin, in its own directory: (exit status, output, the
    use directory its test started a sleeper in)."""
    (base / "pytest.ini").write_text(CHILD_INI)
    (base / "test_child.py").write_text(CHILD_TESTS)
    out = base / "use"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(TESTS), os.environ.get("PYTHONPATH", "")]),
           "HZ_CHILD_OUT": str(out)}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", "-p", "escapes", "-p",
                           "no:cacheprovider", "-p", "no:randomly", "-c", str(base / "pytest.ini"),
                           "--rootdir", str(base), "--basetemp", str(base / "bt"), f"test_child.py::{test}"],
                          cwd=base, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=BUDGET * 6)
    return proc.returncode, proc.stdout + proc.stderr, Path(out.read_text())


def test_teardown_passes_after_a_failing_test() -> None:
    with short_dir() as base:
        rc, out, use = child_pytest(base, "test_fails")
    assert rc == 1 and "1 failed" in out and "error" not in out.splitlines()[-1], out
    assert not use.exists()                 # removed only under the census held exclusively


def test_a_use_never_ended_fails_the_session() -> None:
    with short_dir() as base:
        rc, out, use = child_pytest(base, "test_never_ends")
    assert rc == pytest.ExitCode.TESTS_FAILED and "1 passed" in out, out
    assert f"escapes: a use was never ended by its test: {use} (now ended)" in out
    assert not use.exists()


NO_SIGNAL = """\
import os, shutil, subprocess, time
import escapes, sleeper
os.kill(os.getpid(), 0)                     # positive control: the hook and the wrapper both see it
use = escapes.Escapes()
p = sleeper.spawn(str(use.path), new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 10
while not use.pids() and time.monotonic() < deadline:
    time.sleep(0.02)
use.end()
print("ended", p.wait(), flush=True)
shutil.rmtree(escapes.run_dir())
"""


def test_end_sends_no_signal(tmp_path: Path) -> None:
    rc, out, logged = harness(tmp_path, NO_SIGNAL)
    assert (rc, out.strip()) == (0, "ended 0"), out
    signals = [e[1] for e in logged if "kill" in e[1] or "signal" in e[1]]
    assert signals == ["called kill", "os.kill"], logged   # the control's, and nothing from `end`
    assert [e[1] for e in logged if e[1] in SPAWNS] == ["subprocess.Popen"]
