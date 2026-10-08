"""pytest plugin: test tmux servers live in a private directory that a watchdog outside pytest cleans up
(spec 2026-10-08-test-tmux-leak-design.md and its Amendment 1, btq-q1r4p).

At configure it creates `run` (a 0700 mkdtemp under /tmp, ignoring TMPDIR and TMUX_TMPDIR) with the
socket root `run/s` and its launch lock `run/s/launch.lock`, and starts `tmux_watchdog.py` in a session of
its own with a stdin pipe whose only writer is this process. Tests get a tmux from `new_test_tmux()`: a
GuardedTmux on a fresh socket in the root, which runs every tmux command under LOCK_SH on the launch lock
and passes that descriptor to tmux, so the client and the server it forks hold SH for their lifetimes.
When pytest exits by any means the pipe reaches EOF and the watchdog kills whatever is left, removing
entries only under LOCK_EX; at unconfigure the plugin closes the pipe itself and waits, bounded. Loaded
from tests/conftest.py, or with `-p tmux_guard`.

It also holds the source scan (`scan`, `scan_tree`) that keeps every test-side tmux start on this path.

`HZ_REQUIRE_TMUX=1` (set in CI) makes a missing tmux fail the session instead of skipping its tests.
Test-only knobs: `HZ_TMUX_WATCHDOG_SCRIPT`, `HZ_TMUX_WATCHDOG_ACK_TIMEOUT`, `HZ_TMUX_WATCHDOG_WAIT`.
"""

import ast
import fcntl
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

from heterodyne.tmux import Tmux

WATCHDOG = Path(__file__).resolve().with_name("tmux_watchdog.py")
ACK_TIMEOUT = 5.0
TEARDOWN_WAIT = 45.0
LOCK_NAME = "launch.lock"
LOCK_FLAGS = os.O_RDONLY | os.O_CLOEXEC     # never O_CREAT: after the rename the open must fail


class WatchdogError(RuntimeError):
    pass


@dataclass
class Guard:
    run: Path
    proc: "subprocess.Popen[bytes] | None" = None
    error: str | None = None
    timed_out: bool = False


_guard: Guard | None = None
last: Guard | None = None      # the most recent session's guard, kept after unconfigure for inspection


def make_root(run: Path) -> Path:
    """`run/s` and its launch lock, created before any watchdog or tmux; no descriptor is kept."""
    root = run / "s"
    root.mkdir(mode=0o700)
    os.close(os.open(root / LOCK_NAME, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600))
    return root


def _new_socket_path() -> Path:
    """A fresh socket path in this session's watched root."""
    if _guard is None:
        raise WatchdogError("the tmux_guard plugin is not active")
    if _guard.error is not None or _guard.proc is None:
        raise WatchdogError(f"no tmux watchdog, so no test tmux server may start: {_guard.error}")
    return _guard.run / "s" / uuid.uuid4().hex[:8]


def _open_parent(socket_path: Path) -> int:
    try:
        return os.open(socket_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError:
        raise WatchdogError(f"the tmux root {socket_path.parent} is gone: the watchdog has run") from None


def _open_lock_at(parent_fd: int) -> int:
    try:
        return os.open(LOCK_NAME, LOCK_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise WatchdogError("the launch lock is gone: the watchdog has run") from None
    finally:
        os.close(parent_fd)


def begin_lock_open(socket_path: Path) -> Callable[[], int]:
    """Resolve the root now; the returned `finish()` opens the launch lock in it (the fd is the caller's).
    Both steps raise WatchdogError once the root was renamed."""
    parent = _open_parent(socket_path)
    return lambda: _open_lock_at(parent)


class GateLauncher:
    """The one allowed launcher prefix: `sh` waits for `<directory>/gate`, then runs tmux with `"$@"`, which
    keeps every open descriptor (the launch lock's included). It writes its PID to `started` first and
    tmux's output to `launch` after."""

    SCRIPT = ('echo $$ > "$0/started.tmp"; mv "$0/started.tmp" "$0/started"; '
              'while [ ! -e "$0/gate" ]; do sleep 0.05; done; '
              '"$@" > "$0/launch.tmp" 2>&1; status=$?; mv "$0/launch.tmp" "$0/launch"; exit $status')

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def __call__(self) -> tuple[str, ...]:
        return ("sh", "-c", self.SCRIPT, str(self.directory))


class GuardedTmux(Tmux):
    """A Tmux whose every command runs under LOCK_SH on the root's launch lock, with that descriptor
    passed to tmux. The fd is closed after the command, never unlocked: LOCK_UN would release SH for the
    client and server sharing it."""

    socket_path: Path

    def __init__(self, socket_path: Path, launcher: GateLauncher | None = None) -> None:
        if launcher is not None and not isinstance(launcher, GateLauncher):
            raise WatchdogError("a guarded tmux takes no launcher but GateLauncher: another could drop "
                                "the launch lock's descriptor")
        super().__init__("hz-test-guarded", launcher=launcher, socket_path=socket_path)

    def _exec(self, argv: list[str], *, input: bytes | None,
              timeout: float) -> subprocess.CompletedProcess[bytes]:
        fd = begin_lock_open(self.socket_path)()
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            return subprocess.run(argv, input=input, capture_output=True, timeout=timeout, check=False,
                                  pass_fds=(fd,))
        finally:
            os.close(fd)


def new_test_tmux(launcher: GateLauncher | None = None) -> GuardedTmux:
    """A guarded tmux on a fresh socket in this session's watched root. Nothing runs here."""
    path = _new_socket_path()
    os.close(begin_lock_open(path)())               # the root and its lock are still there
    return GuardedTmux(path, launcher)


def guarded_tmux(sock: Path | str, *args: str) -> str:
    """One tmux command on `sock` under SH; its stdout, whatever the exit status."""
    return GuardedTmux(Path(sock))._run(*args, check=False).stdout.decode("utf-8", "replace")


def start_watchdog(run: Path, script: Path = WATCHDOG, ack_timeout: float = ACK_TIMEOUT,
                   extra: tuple[str, ...] = ()) -> "subprocess.Popen[bytes]":
    """Start the watchdog for `run` and wait for its `ready`. On failure it is stopped and reaped."""
    ack_r, ack_w = os.pipe()
    try:
        with (run / "watchdog.log").open("ab") as log:
            proc = subprocess.Popen([sys.executable, str(script), str(run), str(ack_w), *extra],
                                    stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, close_fds=True, pass_fds=(ack_w,))
    except OSError as exc:
        os.close(ack_r)
        raise WatchdogError(f"could not start the tmux watchdog: {exc}") from None
    finally:
        os.close(ack_w)
    try:
        ack = _read_ack(ack_r, ack_timeout)
    finally:
        os.close(ack_r)
    if ack != b"ready":
        stop(proc)
        raise WatchdogError(f"the tmux watchdog did not start (exit {proc.returncode}); "
                            f"see {run}/watchdog.log")
    return proc


def _read_ack(fd: int, timeout: float) -> bytes:
    end = time.monotonic() + timeout
    data = b""
    while len(data) < len(b"ready"):
        left = end - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            break
        chunk = os.read(fd, 16)
        if not chunk:
            break
        data += chunk
    return data


def stop(proc: "subprocess.Popen[bytes]") -> None:
    """Terminate, then kill, and reap."""
    if proc.stdin is not None:
        proc.stdin.close()
    proc.terminate()
    try:
        proc.wait(2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def pytest_configure(config: pytest.Config) -> None:
    global _guard
    run = Path(tempfile.mkdtemp(prefix="hzt", dir="/tmp")).resolve()
    make_root(run)
    _guard = Guard(run)
    try:
        _guard.proc = start_watchdog(
            run, Path(os.environ.get("HZ_TMUX_WATCHDOG_SCRIPT") or WATCHDOG),
            float(os.environ.get("HZ_TMUX_WATCHDOG_ACK_TIMEOUT") or ACK_TIMEOUT))
    except WatchdogError as exc:
        _guard.error = str(exc)
        shutil.rmtree(run, ignore_errors=True)       # nothing could have launched in it


def pytest_sessionstart(session: pytest.Session) -> None:
    if os.environ.get("HZ_REQUIRE_TMUX") == "1" and shutil.which("tmux") is None:
        pytest.exit("HZ_REQUIRE_TMUX=1 but tmux is not installed", returncode=pytest.ExitCode.USAGE_ERROR)


def pytest_unconfigure(config: pytest.Config) -> None:
    global _guard, last
    guard, _guard = _guard, None
    last = guard
    if guard is None or guard.proc is None:
        return
    proc = guard.proc
    assert proc.stdin is not None
    proc.stdin.close()
    wait = float(os.environ.get("HZ_TMUX_WATCHDOG_WAIT") or TEARDOWN_WAIT)
    try:
        proc.wait(wait)
    except subprocess.TimeoutExpired:
        guard.timed_out = True
        threading.Thread(target=proc.wait, name="tmux-watchdog-reaper", daemon=True).start()
        _warn(f"the tmux watchdog is still cleaning up after {wait:g}s; it will be reaped when it "
              f"exits. See {guard.run}")
        return
    summary = guard.run / "summary.json"
    if summary.exists():
        _warn(f"tests left tmux servers behind (a teardown gap); the watchdog's report is {summary}:\n"
              + summary.read_text())


def _warn(text: str) -> None:
    sys.stderr.write(f"\ntmux_guard: {text}\n")
    sys.stderr.flush()


# --- the source scan (Amendment 1, A2): a tripwire for unguarded tmux starts, not a proof ---

TRUSTED = frozenset({"tests/tmux_watchdog.py", "tests/tmux_guard.py", "tests/data/tmux_scan_specimens.txt"})
# Fully mocked tests (subprocess.run is a recorder, so no tmux runs): exact (module, function) pairs.
EXEMPT = frozenset({("tests/test_tmux_watchdog.py", "test_socket_path_selects_with_dash_s")})
PRIVATE = frozenset({"_new_socket_path", "_open_parent", "_open_lock_at"})
NESTING = 3


class Finding(NamedTuple):
    path: str
    line: int
    rule: str


def scan(relpath: str, source: str) -> list[Finding]:
    """Findings for one file, by its path relative to the repository root."""
    if relpath in TRUSTED:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [Finding(relpath, 0, "unparsable")]
    out: list[Finding] = []
    skipped: set[ast.AST] = set()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    for module, name in sorted(EXEMPT):
        if module != relpath:
            continue
        func = funcs.get(name)
        if func is None:
            out.append(Finding(relpath, 0, "exempt-missing"))
        else:
            if not any(_patches_run(n) for n in ast.walk(func)):
                out.append(Finding(relpath, func.lineno, "exempt-unpatched"))
            skipped.update(ast.walk(func))
    out.extend(Finding(relpath, line, rule) for line, rule in _rules(tree, skipped, 0))
    return out


def _rules(tree: ast.AST, skipped: set[ast.AST], depth: int) -> list[tuple[int, str]]:
    names = _tmux_names(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if node in skipped:
            continue
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call):
            if _is_tmux(node.func, names):
                if any(k.arg == "socket_path" for k in node.keywords):
                    out.append((line, "alias-socket-path"))
                if any(k.arg is None for k in node.keywords):
                    out.append((line, "kwargs"))
            if _raw_s(node.args):
                out.append((line, "raw-tmux-S"))
        elif isinstance(node, ast.List | ast.Tuple) and _raw_s(node.elts):
            out.append((line, "raw-tmux-S"))
        elif (isinstance(node, ast.Name) and node.id in PRIVATE
              or isinstance(node, ast.Attribute) and node.attr in PRIVATE):
            out.append((line, "private-ref"))
        elif isinstance(node, ast.alias) and (node.name in PRIVATE or node.asname in PRIVATE):
            out.append((line, "private-ref"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and depth < NESTING:
            try:
                inner = ast.parse(node.value)
            except (SyntaxError, ValueError):
                continue
            out.extend((line, rule) for _, rule in _rules(inner, set(), depth + 1))
    return out


def _tmux_names(tree: ast.AST) -> set[str]:
    """Every simple name `heterodyne.tmux.Tmux` may go by: itself, `as` aliases, and assignment chains."""
    names = {"Tmux"}
    for node in ast.walk(tree):
        if isinstance(node, ast.alias) and node.name == "Tmux" and node.asname:
            names.add(node.asname)
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    while True:
        more = {t.id for a in assigns if _is_tmux(a.value, names) for t in a.targets
                if isinstance(t, ast.Name)} - names
        if not more:
            return names
        names |= more


def _is_tmux(node: ast.expr, names: set[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in names
            or isinstance(node, ast.Attribute) and node.attr == "Tmux")


def _raw_s(items: Sequence[ast.expr]) -> bool:
    return (bool(items) and isinstance(items[0], ast.Constant) and items[0].value == "tmux"
            and any(isinstance(i, ast.Constant) and i.value == "-S" for i in items))


def _patches_run(node: ast.AST) -> bool:
    """A `setattr(<...>subprocess, "run", ...)` or `setattr("<...>subprocess.run", ...)` call."""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "setattr"
            or isinstance(func, ast.Name) and func.id == "setattr"):
        return False
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.endswith("subprocess.run")
    target = (first.attr if isinstance(first, ast.Attribute)
              else first.id if isinstance(first, ast.Name) else None)
    second = node.args[1] if len(node.args) > 1 else None
    return target == "subprocess" and isinstance(second, ast.Constant) and second.value == "run"


def scan_tree(repo_root: Path) -> list[Finding]:
    """Scan every tests/**/*.py (tests/live/ belongs to another workstream and is out of scope)."""
    out: list[Finding] = []
    for path in sorted((repo_root / "tests").rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        if rel.startswith("tests/live/"):
            continue
        out.extend(scan(rel, path.read_text()))
    return out
