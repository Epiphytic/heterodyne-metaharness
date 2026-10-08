"""pytest plugin: test tmux servers live in a private directory that a watchdog outside pytest cleans up
(spec 2026-10-08-test-tmux-leak-design.md, btq-q1r4p).

At configure it creates `run` (a 0700 mkdtemp under /tmp, ignoring TMPDIR and TMUX_TMPDIR) with the
socket root `run/s`, and starts `tmux_watchdog.py` in a session of its own with a stdin pipe whose only
writer is this process. Tests get socket paths from `new_test_socket_path()`. When pytest exits by any
means the pipe reaches EOF and the watchdog kills whatever is left; at unconfigure the plugin closes the
pipe itself and waits, bounded. Loaded from tests/conftest.py, or with `-p tmux_guard`.

`HZ_REQUIRE_TMUX=1` (set in CI) makes a missing tmux fail the session instead of skipping its tests.
Test-only knobs: `HZ_TMUX_WATCHDOG_SCRIPT`, `HZ_TMUX_WATCHDOG_ACK_TIMEOUT`, `HZ_TMUX_WATCHDOG_WAIT`.
"""

import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

WATCHDOG = Path(__file__).resolve().with_name("tmux_watchdog.py")
ACK_TIMEOUT = 5.0
TEARDOWN_WAIT = 45.0


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


def new_test_socket_path() -> Path:
    """A fresh socket path in this session's watched root, for `Tmux(..., socket_path=...)`."""
    if _guard is None:
        raise WatchdogError("the tmux_guard plugin is not active")
    if _guard.error is not None or _guard.proc is None:
        raise WatchdogError(f"no tmux watchdog, so no test tmux server may start: {_guard.error}")
    return _guard.run / "s" / uuid.uuid4().hex[:8]


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
    (run / "s").mkdir(mode=0o700)
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
