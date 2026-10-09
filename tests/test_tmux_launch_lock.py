"""The launch lock that serializes socket removal against guarded tmux starts (spec
2026-10-08-test-tmux-leak-design.md, Amendment 1, regressions 1-6 and 8-12; btq-q1r4p).

The scripted tests use FakeSweeper with real flocks (`lock_free=None`); "servers" and launchers are helper
processes that hold the lock. The real-tmux tests start servers in a root of their own, watched by a
watchdog of their own, so they never contend with the session's servers.
"""

import contextlib
import errno
import fcntl
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import tmux_guard
from test_tmux_watchdog import (
    BUDGET,
    FakeSweeper,
    _dead_with,
    assert_cleaned,
    children,
    matching,
    needs_tmux,
    running,
    until,
)
from tmux_watchdog import Sweeper

from heterodyne import tmux as tmux_mod

LOCK = "launch.lock"
REPLACED = "launch lock replaced"
APPEARED = "appeared after final unlink"
TIMED_OUT = "deadline passed"
PANE = [sys.executable, "-c", "import time; time.sleep(600)"]

# A helper process that takes the locks named in argv, says `ok`, and holds them until stdin closes:
# `sh:<path>` (LOCK_SH), `ex-unlinked:<path>` (create, LOCK_EX, then unlink: tmux's own startup lock after
# server_start), `at:<fd>:<path>` (open at that exact fd number).
HOLDER = """
import fcntl, os, resource, sys
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
if soft < 2048 and (hard == resource.RLIM_INFINITY or hard >= 2048):
    resource.setrlimit(resource.RLIMIT_NOFILE, (2048, hard))
for spec in sys.argv[1:]:
    kind, _, path = spec.partition(":")
    if kind == "sh":
        fcntl.flock(os.open(path, os.O_RDONLY), fcntl.LOCK_SH)
    elif kind == "ex-unlinked":
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.unlink(path)
    elif kind == "at":
        num, _, path = path.partition(":")
        fd = os.open(path, os.O_RDONLY)
        os.dup2(fd, int(num))
        os.close(fd)
print("ok", flush=True)
sys.stdin.read()
"""


@contextlib.contextmanager
def holder(*specs: str) -> Iterator["subprocess.Popen[str]"]:
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, *specs], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline() == "ok\n"
        yield proc
    finally:
        proc.kill()
        proc.wait()


def identity(path: Path) -> tuple[int, int]:
    st = path.lstat()
    return (st.st_dev, st.st_ino)


def replace_lock(dead: Path) -> tuple[int, int]:
    """A new launch.lock inode at the same path (the old one stays open in the sweeper, so is not reused)."""
    (dead / LOCK).unlink()
    (dead / LOCK).touch(mode=0o600)
    return identity(dead / LOCK)


def bind_stale(path: Path) -> None:
    """A socket that is bound but never listens: connect is refused."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(str(path))
    finally:
        s.close()


@pytest.fixture
def quiet_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """`run` with a root and launch lock of its own, current for new_test_tmux. Nothing watches it, so
    no tmux may start in it; the test renames and sweeps it itself."""
    run = Path(tempfile.mkdtemp(prefix="hzt", dir="/tmp")).resolve()
    tmux_guard.make_root(run)
    current = tmux_guard._guard
    assert current is not None
    monkeypatch.setattr(tmux_guard, "_guard", tmux_guard.Guard(run, proc=current.proc))
    try:
        yield run
    finally:
        shutil.rmtree(run, ignore_errors=True)


@pytest.fixture
def watched_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """`run` with its own root and its own watchdog, current for new_test_tmux: whatever a failed test
    leaves in it is killed at teardown."""
    run = Path(tempfile.mkdtemp(prefix="hzt", dir="/tmp")).resolve()
    tmux_guard.make_root(run)
    proc = tmux_guard.start_watchdog(run)
    monkeypatch.setattr(tmux_guard, "_guard", tmux_guard.Guard(run, proc=proc))
    try:
        yield run
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        try:
            proc.wait(BUDGET)
        finally:
            shutil.rmtree(run, ignore_errors=True)


# --- 1. a startup holding an unlinked tmux lock ---

def test_a_startup_holding_an_unlinked_tmux_lock_keeps_its_socket() -> None:
    run = Path(tempfile.mkdtemp(prefix="hzt", dir="/tmp")).resolve()
    try:
        root = tmux_guard.make_root(run)
        bind_stale(root / "ab")                    # bound, not yet listening
        with holder(f"ex-unlinked:{root / 'ab.lock'}", f"sh:{root / LOCK}"):
            proc = tmux_guard.start_watchdog(run, extra=("--deadline", "2"))
            assert proc.stdin is not None
            proc.stdin.close()
            assert proc.wait(BUDGET) == 0
            dead = run / "dead"
            assert (dead / "ab").exists() and not (dead / "ab.lock").exists()
            summary = json.loads((run / "summary.json").read_text())
        assert summary == {"killed": [], "stale": [], "survived": [], "closed": False, "reason": TIMED_OUT,
                           "unresolved": [{"path": str(dead / "ab"), "pid": None, "reason": "held"}]}
    finally:
        shutil.rmtree(run, ignore_errors=True)


# --- 2. the launch lock replaced during cleanup ---

def test_the_launch_lock_keeps_its_inode_until_the_final_step(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")
    seen: list[tuple[int, int]] = []
    final: list[tuple[int, int]] = []

    class SlowDeath(FakeSweeper):
        def pid_running(self, pid: int) -> bool:
            return self.clock < 6.0                # outlives the first death wait: several passes

        def sweep(self) -> None:
            seen.append(identity(dead / LOCK))
            super().sweep()

        def before_rmdir(self) -> None:
            assert not (dead / LOCK).exists()
            final.append(seen[0])

    original = identity(dead / LOCK)
    s = SlowDeath(dead, lock_free=None)
    closed = s.run()
    assert len(seen) >= 2 and set(seen) == {original} and final == [original]
    assert closed and not dead.exists()


@pytest.mark.parametrize("how", ["replaced", "deleted"])
def test_a_launch_lock_replaced_mid_closure_stops_every_removal(tmp_path: Path, how: str) -> None:
    dead = _dead_with(tmp_path, "aa", "bb")

    class Swap(FakeSweeper):
        def unlink(self, path: Path) -> None:
            super().unlink(path)
            if path.name == "aa":
                if how == "replaced":
                    replace_lock(dead)
                else:
                    (dead / LOCK).unlink()

    s = Swap(dead, pid=None, lock_free=None)
    closed = s.run()
    summary = s.summary(closed)
    assert not closed and (dead / "bb").exists() and (dead / LOCK).exists() == (how == "replaced")
    assert summary["stale"] == [{"path": str(dead / "aa"), "pid": None}]
    unresolved = [{"path": str(dead / "bb"), "pid": None, "reason": REPLACED}]
    if how == "replaced":
        unresolved.append({"path": str(dead / LOCK), "pid": None, "reason": REPLACED})
    assert summary["unresolved"] == unresolved


def test_a_replacement_before_the_final_step_is_never_unlinked(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path)
    swapped: list[tuple[int, int]] = []

    class Swap(FakeSweeper):
        def sweep(self) -> None:
            super().sweep()
            if not swapped:
                swapped.append(replace_lock(dead))   # dead holds only the lock: the final step is next

    s = Swap(dead, lock_free=None)
    closed = s.run()
    assert not closed and dead.exists() and identity(dead / LOCK) == swapped[0]
    assert s.summary(closed)["unresolved"] == [{"path": str(dead / LOCK), "pid": None, "reason": REPLACED}]


def test_an_earlier_replacement_stops_the_final_step_even_once_restored(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")
    aside = tmp_path / "original.lock"

    class SwapAndRestore(FakeSweeper):
        def sweep(self) -> None:
            if aside.exists():
                super().sweep()
                return
            (dead / LOCK).rename(aside)             # the original inode, kept
            (dead / LOCK).touch(mode=0o600)
            super().sweep()                         # aa's removal sees the replacement
            assert (dead / "aa").exists()
            (dead / "aa").unlink()                  # gone some other way
            (dead / LOCK).unlink()
            aside.rename(dead / LOCK)               # the original is back: only the lock is left

    s = SwapAndRestore(dead, pid=None, lock_free=None)
    closed = s.run()
    assert not closed and (dead / LOCK).exists()
    assert s.summary(closed)["unresolved"] == [{"path": str(dead / LOCK), "pid": None, "reason": REPLACED}]


def test_a_missing_launch_lock_removes_nothing(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")
    (dead / LOCK).unlink()
    s = FakeSweeper(dead, lock_free=None)
    closed = s.run()
    summary = s.summary(closed)
    assert not closed and (dead / "aa").exists() and ("kill-server",) in s.commands
    assert summary["killed"] == [{"path": str(dead / "aa"), "pid": 4242}]
    assert summary["unresolved"] == [{"path": str(dead / "aa"), "pid": None, "reason": "launch lock missing"}]


# The summary's top-level reason: present exactly when closed is false (code review r5, finding 2).

@pytest.mark.parametrize("lost", ["before-the-run", "mid-closure"])
def test_a_launch_lock_lost_from_an_empty_dir_names_the_reason(tmp_path: Path, lost: str) -> None:
    dead = _dead_with(tmp_path)
    if lost == "before-the-run":
        (dead / LOCK).unlink()

    class Loses(FakeSweeper):
        def take_ex(self) -> bool:
            taken = super().take_ex()
            (dead / LOCK).unlink(missing_ok=True)   # under EX, just before the final identity check
            return taken

    s = Loses(dead, lock_free=None)
    closed = s.run()
    assert not closed and dead.exists() and list(dead.iterdir()) == []
    reason = "launch lock missing" if lost == "before-the-run" else REPLACED
    assert s.summary(closed) == {"killed": [], "stale": [], "survived": [], "unresolved": [],
                                 "closed": False, "reason": reason}


def test_a_launch_lock_held_to_the_deadline_names_the_reason(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path)
    s = FakeSweeper(dead, lock_free=False, deadline=1.0)
    closed = s.run()
    held = {"path": str(dead / LOCK), "pid": None, "reason": "launch lock held"}
    assert s.summary(closed) == {"killed": [], "stale": [], "survived": [], "unresolved": [held],
                                 "closed": False, "reason": "launch lock held"}


def test_entries_left_at_the_deadline_name_the_reason(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")
    s = FakeSweeper(dead, pid=None, connect="live", deadline=1.0)
    closed = s.run()
    assert not closed and s.summary(closed)["reason"] == TIMED_OUT


def test_a_failed_final_rmdir_with_nothing_left_names_the_error(monkeypatch: pytest.MonkeyPatch,
                                                                tmp_path: Path) -> None:
    dead = _dead_with(tmp_path)
    real = Path.rmdir

    def refused(path: Path) -> None:
        if path == dead:
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        real(path)
    monkeypatch.setattr(Path, "rmdir", refused)
    s = FakeSweeper(dead, lock_free=None)
    closed = s.run()
    assert not closed and dead.exists() and list(dead.iterdir()) == [] and s.clock < 1.0
    assert s.summary(closed) == {"killed": [], "stale": [], "survived": [], "unresolved": [],
                                 "closed": False, "reason": "final rmdir failed: EACCES"}


def test_a_closed_summary_has_no_reason(tmp_path: Path) -> None:
    s = FakeSweeper(_dead_with(tmp_path, "aa"), pid=None, lock_free=None)
    closed = s.run()
    assert closed and "reason" not in s.summary(closed)


# --- 3. a late launcher ---

def test_a_guarded_tmux_after_the_rename_raises_and_runs_nothing(monkeypatch: pytest.MonkeyPatch,
                                                                 quiet_root: Path) -> None:
    def no_run(*_a: Any, **_kw: Any) -> Any:
        pytest.fail("tmux ran")
    monkeypatch.setattr(tmux_mod.subprocess, "run", no_run)
    monkeypatch.setattr(tmux_guard.subprocess, "run", no_run)
    earlier = tmux_guard.new_test_tmux()
    (quiet_root / "s").rename(quiet_root / "dead")
    with pytest.raises(tmux_guard.WatchdogError):
        tmux_guard.new_test_tmux()
    with pytest.raises(tmux_guard.WatchdogError):
        earlier.has_session("s")


@needs_tmux
def test_a_launcher_released_after_the_final_unlink_starts_nothing(tmp_path: Path) -> None:
    with children(tmp_path) as make:
        c = make('''
import errno, fcntl, os, tmux_guard
from heterodyne.tmux import TmuxError

def test_child(tmp_path, monkeypatch):
    t = new_test_tmux()
    run = t.socket_path.parent.parent
    hand(t.socket_path, [])
    real = fcntl.flock

    def gated(fd, op):
        """Between the open and the flock: pytest's end closes, and closure runs to the end."""
        if op == fcntl.LOCK_SH:
            tmux_guard._guard.proc.stdin.close()
            end = time.monotonic() + 60
            while run.exists():
                assert time.monotonic() < end
                time.sleep(0.05)
        return real(fd, op)
    monkeypatch.setattr(tmux_guard.fcntl, "flock", gated)
    results = []
    real_exec = t._exec

    def recorded(*a, **kw):
        results.append(real_exec(*a, **kw))
        return results[-1]
    monkeypatch.setattr(t, "_exec", recorded)
    try:
        t.new_session("s", tmp_path, PANE)
    except TmuxError:                                # however tmux exits, the start must not succeed
        pass
    else:
        raise AssertionError("a start with no socket directory reported success")
    [proc] = results
    if proc.returncode == 0:                         # tmux 3.4: exit 0, the cause on stderr
        causes = {b"No such file or directory", os.strerror(errno.ENOENT).encode()}
        assert any(cause in proc.stderr for cause in causes), proc
''', env={"LC_ALL": "C"})
        data = c.hand()
        assert c.finished() == 0, c.output()
        assert c.run is not None and not c.run.exists()
        assert not matching(c.mark) and not matching(data["socket"])


# --- 4. removal blocked while SH is held ---

@pytest.mark.parametrize("release", [True, False], ids=["released", "held-to-deadline"])
def test_a_dead_servers_socket_waits_for_the_shared_lock(tmp_path: Path, release: bool) -> None:
    dead = _dead_with(tmp_path, "aa")
    sh = os.open(dead / LOCK, os.O_RDONLY)
    fcntl.flock(sh, fcntl.LOCK_SH)
    gone_at: list[int] = []

    class Blocked(FakeSweeper):
        passes = 0

        def tmux(self, path: Path, *args: str) -> tuple[int, str]:
            out = super().tmux(path, *args)
            if args[0] == "kill-server":
                self.pid = None                     # a dead server answers nothing
            return out

        def sweep(self) -> None:
            super().sweep()
            self.passes += 1
            if not (dead / "aa").exists() and not gone_at:
                gone_at.append(self.passes)
            if release and self.passes == 2:
                os.close(sh)

    s = Blocked(dead, lock_free=None, deadline=3.0)
    try:
        closed = s.run()
    finally:
        with contextlib.suppress(OSError):
            os.close(sh)
    summary = s.summary(closed)
    assert summary["killed"] == [{"path": str(dead / "aa"), "pid": 4242}]
    if release:
        assert gone_at == [3] and closed and not dead.exists()
    else:
        assert not closed and (dead / "aa").exists()
        assert summary["unresolved"] == [{"path": str(dead / "aa"), "pid": None, "reason": "held"}]


# --- 5. SH outlives the launcher ---

def test_a_child_of_a_guarded_command_keeps_the_shared_lock(quiet_root: Path) -> None:
    t = tmux_guard.new_test_tmux()
    proc = t._exec(["sh", "-c", "sleep 600 >/dev/null 2>&1 & echo $!"], input=None, timeout=10)
    pid = int(proc.stdout)
    probe = os.open(quiet_root / "s" / LOCK, os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.kill(pid, signal.SIGKILL)
        until(lambda: _ex_free(probe), "the shared lock to go with the child")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        os.close(probe)


def _ex_free(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    fcntl.flock(fd, fcntl.LOCK_UN)
    return True


@needs_tmux
def test_a_server_keeps_the_shared_lock_after_pytest_is_killed(tmp_path: Path) -> None:
    with children(tmp_path) as make:
        c = make('''
def test_child(tmp_path):
    t = new_test_tmux()
    t.new_session("s", tmp_path, PANE)
    hand(t.socket_path, pids(t.socket_path))
    block()
''')
        data = c.hand()
        server = c.pids[0]
        lock = os.open(Path(data["socket"]).parent / LOCK, os.O_RDONLY)
        try:
            assert not _ex_free(lock)              # only the server holds it: pytest's own fd is closed
            os.kill(c.proc.pid, signal.SIGKILL)
            c.finished()

            def free_only_once_dead() -> bool:
                if not _ex_free(lock):
                    return False
                assert not running(server)
                return True
            until(free_only_once_dead, "the watchdog to kill the server")
        finally:
            os.close(lock)
        assert_cleaned(c, data)


# --- 6. cleanup creates no artifacts ---

def test_cleanup_creates_no_lock_files(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa")
    listings: list[list[str]] = []

    class Vanishes(FakeSweeper):
        def take_ex(self) -> bool:
            listings.append(sorted(p.name for p in dead.iterdir()))
            taken = super().take_ex()
            (dead / "aa").unlink(missing_ok=True)   # the socket goes meanwhile
            return taken

    s = Vanishes(dead, pid=None, lock_free=None, deadline=0.1)   # no time for another pass
    closed = s.run()
    assert closed and not dead.exists()
    assert s.summary(closed)["stale"] == s.summary(closed)["unresolved"] == []
    assert listings and all(n in ("aa", LOCK) for names in listings for n in names)


# --- 8. a delayed final-component open ---

@pytest.mark.parametrize("late", ["launcher", "direct-create"])
def test_a_lock_open_resolved_before_the_rename_finds_nothing(quiet_root: Path, late: str) -> None:
    sock = quiet_root / "s" / "abcd1234"
    finish = tmux_guard.begin_lock_open(sock)
    (quiet_root / "s").rename(quiet_root / "dead")
    dead = quiet_root / "dead"
    results: list[str] = []

    class Late(FakeSweeper):
        def before_rmdir(self) -> None:
            if late == "direct-create":            # what a delayed O_CREAT would do
                (dead / LOCK).touch(mode=0o600)
                return
            try:
                os.close(finish())
                results.append("opened")
            except tmux_guard.WatchdogError:
                results.append("refused")

    s = Late(dead, lock_free=None)
    closed = s.run()
    if late == "launcher":
        assert results == ["refused"] and closed and not dead.exists()
    else:
        assert not closed and (dead / LOCK).exists()
        [entry] = s.summary(closed)["unresolved"]
        assert entry["reason"] == APPEARED


# --- 9. prefixes ---

CLOSING_PREFIX = ("import subprocess, sys; "
                  "sys.exit(subprocess.run(sys.argv[1:], close_fds=True).returncode)")


def test_a_prefix_other_than_the_gate_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_run(*_a: Any, **_kw: Any) -> Any:
        pytest.fail("something ran")
    monkeypatch.setattr(tmux_mod.subprocess, "run", no_run)
    monkeypatch.setattr(tmux_guard.subprocess, "run", no_run)
    with pytest.raises(tmux_guard.WatchdogError):
        tmux_guard.new_test_tmux(lambda: (sys.executable, "-c", CLOSING_PREFIX))  # pyright: ignore[reportArgumentType]


def lsof_matches(text: str, pid: int) -> list[tuple[int, int, int]]:
    """(fd, dev, ino) of every numeric-fd record of `pid` in `lsof -F fDi` output. A record is the
    fields after an `f` line, up to the next `f` or `p`; one without D or i, or with a named fd (cwd,
    txt...), is no candidate. A malformed value, or no record for `pid`, raises ValueError."""
    out: list[tuple[int, int, int]] = []
    seen = False
    current: int | None = None
    fd: int | None = None
    dev: int | None = None
    ino: int | None = None

    def close() -> None:
        if current == pid and fd is not None and dev is not None and ino is not None:
            out.append((fd, dev, ino))
    for line in text.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag in "pf":
            close()
            fd = dev = ino = None
        if tag == "p":
            if not value.isdigit():
                raise ValueError(f"lsof: bad pid {value!r}")
            current = int(value)
            seen = seen or current == pid
        elif tag == "f":
            if value.isdigit():
                fd = int(value)
            elif not value.isalpha():
                raise ValueError(f"lsof: bad fd {value!r}")
        elif tag == "D":
            if not value.startswith("0x") or not value[2:] or any(ch not in "0123456789abcdef"
                                                                   for ch in value[2:]):
                raise ValueError(f"lsof: bad device {value!r}")
            dev = int(value, 16)
        elif tag == "i":
            if not value.isdigit():
                raise ValueError(f"lsof: bad inode {value!r}")
            ino = int(value)
    close()
    if not seen:
        raise ValueError(f"lsof: no record for pid {pid}")
    return out


def has_proc_fds() -> bool:
    """Linux lists fds under /proc; macOS needs lsof."""
    return Path("/proc/self/fd").is_dir()


def held_fds(pid: int, lock: Path) -> list[int]:
    """The fds of `pid` open on `lock`'s inode, matched on (st_dev, st_ino). Any inspection error fails."""
    want = identity(lock)
    if has_proc_fds():
        return sorted(int(p.name) for p in Path(f"/proc/{pid}/fd").iterdir() if identity_of(p) == want)
    proc = subprocess.run(["lsof", "-n", "-P", "-a", "-p", str(pid), "-F", "fDi"], capture_output=True,
                          text=True, check=False, timeout=30)
    assert proc.returncode == 0, f"lsof failed ({proc.returncode}): {proc.stderr}"
    return sorted(fd for fd, dev, ino in lsof_matches(proc.stdout, pid) if (dev, ino) == want)


def identity_of(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None                                 # an fd closed while being listed
    return (st.st_dev, st.st_ino)


LSOF = "p42\nfcwd\nD0x10\ni2\nf3\nD0x1000004\ni77\nf4\ni78\nf5\nD0x1000004\n"


def test_the_lsof_parser_takes_device_and_inode_from_one_record() -> None:
    assert lsof_matches(LSOF, 42) == [(3, 0x1000004, 77)]       # fd 4 and 5 lack a field: skipped


@pytest.mark.parametrize("text", [
    "p42\nf3\nD0x1000004\nf4\ni77\n",                         # D and i split across two f records
    "p42\nf3\nD0x1000004\np43\ni77\n",                        # ... or across two processes
    "p43\nf3\nD0x1000004\ni77\np42\nfcwd\n",                  # another process's complete record
])
def test_the_lsof_parser_never_joins_records(text: str) -> None:
    assert lsof_matches(text, 42) == []


@pytest.mark.parametrize("text", ["p42\nf3\nDzz\ni7\n", "p42\nf3\nD0x10\ni7x\n", "p42\nf3x\n", "p42\nf-1\n",
                                  "pabc\n", "p43\nf3\nD0x1\ni2\n"])
def test_the_lsof_parser_fails_on_malformed_values_or_no_process(text: str) -> None:
    with pytest.raises(ValueError):
        lsof_matches(text, 42)


def test_the_inspection_matches_device_and_inode_not_inode_alone(monkeypatch: pytest.MonkeyPatch,
                                                                  tmp_path: Path) -> None:
    lock = tmp_path / LOCK
    lock.touch()
    dev, ino = identity(lock)
    real = Path.stat

    def other_device(path: Path, *a: Any, **kw: Any) -> os.stat_result:
        st = real(path, *a, **kw)
        if str(path).startswith("/proc/") and (st.st_dev, st.st_ino) == (dev, ino):
            return os.stat_result((st.st_mode, ino, dev + 1, *tuple(st)[3:]))   # same inode, other device
        return st
    with holder(f"at:77:{lock}") as proc:
        assert held_fds(proc.pid, lock) == [77]
        if has_proc_fds():
            monkeypatch.setattr(Path, "stat", other_device)
            assert held_fds(proc.pid, lock) == []


@pytest.mark.parametrize("device", ["same", "other"])
def test_the_lsof_branch_matches_device_and_inode_not_inode_alone(monkeypatch: pytest.MonkeyPatch,
                                                                  tmp_path: Path, device: str) -> None:
    """The macOS branch, forced on any platform: lsof's record for the lock's inode on another device
    is no match; the same record on the lock's own device is."""
    lock = tmp_path / LOCK
    lock.touch()
    dev, ino = identity(lock)
    listed = dev if device == "same" else dev + 1
    calls: list[list[str]] = []

    def lsof(argv: list[str], **_kw: Any) -> "subprocess.CompletedProcess[str]":
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"p42\nfcwd\nf77\nD{hex(listed)}\ni{ino}\n", "")
    monkeypatch.setitem(globals(), "has_proc_fds", lambda: False)
    monkeypatch.setattr(subprocess, "run", lsof)
    assert held_fds(42, lock) == ([77] if device == "same" else [])
    assert [argv[0] for argv in calls] == ["lsof"]


def test_the_inspection_finds_a_lock_held_at_a_known_fd(quiet_root: Path) -> None:
    lock = quiet_root / "s" / LOCK
    with holder(f"at:77:{lock}") as proc:
        assert held_fds(proc.pid, lock) == [77]
        assert proc.poll() is None


@needs_tmux
def test_a_gated_start_hands_the_lock_to_the_server(watched_root: Path, tmp_path: Path) -> None:
    (tmp_path / "gate").touch()
    t = tmux_guard.new_test_tmux(tmux_guard.GateLauncher(tmp_path))
    try:
        t.new_session("s", tmp_path, PANE)
        server = int(tmux_guard.guarded_tmux(t.socket_path, "display-message", "-p", "#{pid}"))
        assert held_fds(server, watched_root / "s" / LOCK)
        assert running(server)                     # alive throughout: an empty answer is no proof
    finally:
        t.kill_server()


# --- 10. tmux's children release the fd ---

PROBE = r"""
import errno, json, os, sys
out, known, dev, ino = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
def main():
    spare = os.open(os.devnull, os.O_RDONLY)
    os.close(spare)                                 # the lowest free fd: the listing's own dir fd
    listing = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    matches = []
    for name in os.listdir(listing):
        fd = int(name)
        try:
            st = os.fstat(fd)
        except OSError as exc:
            if exc.errno == errno.EBADF and fd == spare:
                continue
            raise
        if (st.st_dev, st.st_ino) == (dev, ino):
            matches.append(fd)
    try:
        st = os.fstat(known)
        direct = (st.st_dev, st.st_ino) == (dev, ino)
    except OSError as exc:
        if exc.errno != errno.EBADF:
            raise
        direct = False
    return {"matches": matches, "known": direct}
try:
    result, code = main(), 0
except Exception as exc:
    result, code = {"error": repr(exc)}, 1
with open(out + ".tmp", "w") as f:
    json.dump(result, f)
os.replace(out + ".tmp", out)
sys.exit(code)
"""


def probe_result(path: Path) -> dict[str, Any]:
    until(path.exists, f"the probe's {path.name}")
    result: dict[str, Any] = json.loads(path.read_text())
    assert "error" not in result, result
    return result


@needs_tmux
def test_panes_jobs_pipes_and_hooks_get_no_lock_fd(watched_root: Path, tmp_path: Path) -> None:
    lock = watched_root / "s" / LOCK
    dev, ino = identity(lock)
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    t = tmux_guard.new_test_tmux()
    try:
        t.new_session("s", tmp_path, PANE)
        server = int(tmux_guard.guarded_tmux(t.socket_path, "display-message", "-p", "#{pid}"))
        [known] = held_fds(server, lock)

        def cmd(name: str) -> list[str]:
            return [sys.executable, str(probe), str(tmp_path / name), str(known), str(dev), str(ino)]
        line = " ".join

        assert " " not in str(tmp_path) and " " not in sys.executable
        t.new_session("pane", tmp_path, cmd("pane"))
        tmux_guard.guarded_tmux(t.socket_path, "run-shell", "-b", line(cmd("job")))
        tmux_guard.guarded_tmux(t.socket_path, "pipe-pane", "-t", "=s:", line(cmd("pipe")))
        tmux_guard.guarded_tmux(t.socket_path, "set-hook", "-g", "after-new-window",
                                f"run-shell \"{line(cmd('hook'))}\"")
        tmux_guard.guarded_tmux(t.socket_path, "new-window", "-d", "-t", "=s")
        for name in ("pane", "job", "pipe", "hook"):
            assert probe_result(tmp_path / name) == {"matches": [], "known": False}, name
        assert held_fds(server, lock) == [known] and running(server)
    finally:
        t.kill_server()
    for fd in (1500, known):
        out = tmp_path / f"control-{fd}"
        wrap = ("import os, resource, sys; soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE); "
                "resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 2048), hard)); "
                f"fd = os.open({str(lock)!r}, os.O_RDONLY); fd == {fd} or (os.dup2(fd, {fd}), os.close(fd)); "
                "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])")
        subprocess.run([sys.executable, "-c", wrap, str(probe), str(out), str(fd), str(dev), str(ino)],
                       check=True, timeout=30)
        assert probe_result(out) == {"matches": [fd], "known": True}


# --- 11. a survivor next to healthy servers ---

def test_a_survivor_never_stops_the_other_kills(tmp_path: Path) -> None:
    dead = _dead_with(tmp_path, "aa", "bb")
    with contextlib.ExitStack() as stack:
        helpers = {n: stack.enter_context(holder(f"sh:{dead / LOCK}")) for n in ("aa", "bb", "cc")}

        class Mixed(FakeSweeper):
            """aa survives kill-server; bb dies on it; cc is bound only after the first pass."""
            passes = 0

            def tmux(self, path: Path, *args: str) -> tuple[int, str]:
                self.commands.append((path.name, *args))
                h = helpers[path.name]
                if args[0] == "display-message":
                    return (0, f"{h.pid}\n") if h.poll() is None else (1, "")
                if args[0] == "kill-server" and path.name != "aa":
                    h.kill()
                    h.wait()
                return (0, "")

            def connect(self, path: Path) -> str:
                if not path.exists():
                    return "missing"
                return "accept" if helpers[path.name].poll() is None else "refused"

            pid_running = Sweeper.pid_running       # the real probe: helpers really die

            def sweep(self) -> None:
                super().sweep()
                self.passes += 1
                if self.passes == 1:
                    (dead / "cc").touch()

        s = Mixed(dead, lock_free=None)
        closed = s.run()
        summary = s.summary(closed)
        assert ("cc", "kill-server") in s.commands
        assert helpers["bb"].poll() is not None and helpers["cc"].poll() is not None
        assert not closed
        assert summary["survived"] == [{"path": str(dead / "aa"), "pid": helpers["aa"].pid}]
        assert summary["killed"] == [{"path": str(dead / n), "pid": helpers[n].pid} for n in ("bb", "cc")]
        assert summary["unresolved"] == [{"path": str(dead / n), "pid": None, "reason": "held"}
                                         for n in ("bb", "cc")]


# --- 12. something appears after the final unlink ---

@pytest.mark.parametrize("appears", ["launch-lock", "socket"])
def test_an_entry_that_appears_after_the_final_unlink_ends_closure(tmp_path: Path, appears: str) -> None:
    dead = _dead_with(tmp_path)
    original = os.open(dead / LOCK, os.O_RDONLY)    # its own open file description, not a dup
    late_unlinks: list[Path] = []
    contended: list[int] = []
    name = LOCK if appears == "launch-lock" else "zz"

    class Late(FakeSweeper):
        hooked = False
        sweeps = 0

        def sweep(self) -> None:
            self.sweeps += 1
            super().sweep()

        def unlink(self, path: Path) -> None:
            if self.hooked:
                late_unlinks.append(path)
            super().unlink(path)

        def before_rmdir(self) -> None:
            self.hooked = True
            try:
                fcntl.flock(original, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                contended.append(exc.errno or 0)
            if appears == "launch-lock":
                (dead / LOCK).touch(mode=0o600)
            else:
                bind_stale(dead / "zz")

    s = Late(dead, lock_free=None)
    try:
        closed = s.run()
    finally:
        os.close(original)
    st = (dead / name).lstat()
    assert contended == [errno.EWOULDBLOCK]
    assert not closed and dead.exists() and s.sweeps == 1 and late_unlinks == []
    summary = s.summary(closed)
    assert summary["reason"] == APPEARED and summary["unresolved"] == [{
        "path": str(dead / name), "pid": None, "reason": APPEARED,
        "type": "socket" if stat.S_ISSOCK(st.st_mode) else "file", "generation": [st.st_dev, st.st_ino]}]
