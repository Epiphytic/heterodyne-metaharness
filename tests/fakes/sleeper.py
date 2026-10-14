"""A test-owned long-lived descendant that ends itself (spec 2026-10-13-u63ak-escaped-fake-cleanup-design.md).

A test that needs a descendant to outlive something (an escaped child, a grandchild holding a pipe) starts
this sleeper instead of `sleep`. Each use of tests/escapes.py's fixture has a directory holding
- `lifeline`: the fixture holds LOCK_EX on it for the test; the sleeper polls LOCK_SH|LOCK_NB and exits once
  it gets it (or after CAP seconds, a backstop only);
- `census`: every sleeper holds LOCK_SH on it for its whole life, through an open file description its
  spawner locked before the spawn, so it is held from the instant the process exists. The fixture proves
  none is left by taking LOCK_EX;
- `pids/`: each sleeper creates `pids/<pid>` once running, for reports only.

Spawners (`spawn`, `fork_held`, and the shell's `--take`) lock the census shared, then check the locked fd
is still the file at `<dir>/census`: teardown removes the directory only under LOCK_EX, so a lock on a file
that is gone or replaced would be outlived, and the spawner refuses instead.

Standard library only, with no imports from tests: the fakes import it, and it runs as
`python -I sleeper.py <dir> --census-fd N [--new-session] [--ready-fd M] [--pid-file P]` or
`python -I sleeper.py --take N <dir>`. It never signals anything, starts nothing, and never reads its stdin
or writes its stdout or stderr.
"""

import fcntl
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

CENSUS = "census"
LIFELINE = "lifeline"
PIDS = "pids"
ENV = "HZ_ESCAPE_DIR"
POLL = 0.05
CAP = 60.0              # a runnable sleeper's backstop; no proof relies on it
REFUSED = 97            # the shell's `--take` (and a script after it) refused: nothing was spawned
SLEEPER = str(Path(__file__).resolve())
# `spawn(_gate=True)`: the child stops itself before it runs any sleeper code, then execs the sleeper.
GATE = "import os, signal, sys; os.kill(os.getpid(), signal.SIGSTOP); os.execv(sys.argv[1], sys.argv[1:])"

_held: tuple[str, int] | None = None     # in a `fork_held` child: (use dir, census fd) until it execs


class Refused(RuntimeError):
    """The use directory is unset or gone, or teardown has begun: nothing was spawned."""


def _same(fd: int, path: Path) -> bool:
    try:
        have, want = os.fstat(fd), path.stat()
    except OSError:
        return False
    return (have.st_dev, have.st_ino) == (want.st_dev, want.st_ino)


def _lock_shared(fd: int, use_dir: str) -> bool:
    """LOCK_SH|LOCK_NB on `fd`, then check it is still the live census: False if either fails."""
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return False
    return _same(fd, Path(use_dir, CENSUS))


def held_census(use_dir: str | None, between: Callable[[], Any] | None = None) -> int:
    """A census fd of `use_dir`, locked shared and checked live; Refused otherwise. `between` (test-only)
    runs after the open and before the lock."""
    if not use_dir:
        raise Refused("no escape directory")
    try:
        fd = os.open(Path(use_dir, CENSUS), os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        raise Refused("the escape directory is gone") from None
    try:
        if between is not None:
            between()
        if not _lock_shared(fd, use_dir):
            raise Refused("the escape directory's teardown has begun")
    except BaseException:
        os.close(fd)
        raise
    return fd


def sleeper_argv(use_dir: str, fd: int, *, new_session: bool = False, ready_fd: int | None = None,
                 pid_file: str | None = None) -> list[str]:
    argv = [sys.executable, "-I", SLEEPER, use_dir, "--census-fd", str(fd)]
    if new_session:
        argv.append("--new-session")
    if ready_fd is not None:
        argv += ["--ready-fd", str(ready_fd)]
    if pid_file is not None:
        argv += ["--pid-file", pid_file]
    return argv


def spawn(use_dir: str | None, *, new_session: bool = False, ready_fd: int | None = None,
          pid_file: str | None = None, pass_fds: Sequence[int] = (), _gate: bool = False,
          _between_open_and_lock: Callable[[], Any] | None = None, **popen: Any) -> "subprocess.Popen[Any]":
    """Start one sleeper that holds the census from birth. `new_session` starts it in a session of its own
    before it execs (start_new_session), so a group kill can't reach it however soon it lands."""
    fd = held_census(use_dir, _between_open_and_lock)
    try:
        assert use_dir is not None
        argv = sleeper_argv(use_dir, fd, ready_fd=ready_fd, pid_file=pid_file)
        if _gate:
            argv = [sys.executable, "-I", "-c", GATE, *argv]
        fds = (fd, *pass_fds) if ready_fd is None else (fd, ready_fd, *pass_fds)
        return subprocess.Popen(argv, pass_fds=fds, start_new_session=new_session, **popen)
    finally:
        os.close(fd)


def fork_held(use_dir: str | None, *, _between_open_and_lock: Callable[[], Any] | None = None) -> int:
    """os.fork with the census already held: the child's PID in the parent; 0 in the child, which must do
    nothing but redirect its output and call `exec_sleeper`."""
    global _held
    fd = held_census(use_dir, _between_open_and_lock)
    try:
        pid = os.fork()
    except BaseException:
        os.close(fd)
        raise
    if pid == 0:
        assert use_dir is not None
        _held = (use_dir, fd)
        return 0
    os.close(fd)
    return pid


def exec_sleeper(*, new_session: bool = False, ready_fd: int | None = None, pid_file: str | None = None,
                 prefix: Sequence[str] = ()) -> None:
    """In a `fork_held` child: exec the sleeper on the held census (after `prefix`, test-only). Never
    returns: a failed exec exits 127, which releases the census."""
    try:
        assert _held is not None, "exec_sleeper outside a fork_held child"
        use_dir, fd = _held
        os.set_inheritable(fd, True)
        if ready_fd is not None:
            os.set_inheritable(ready_fd, True)
        argv = [*prefix, *sleeper_argv(use_dir, fd, new_session=new_session, ready_fd=ready_fd,
                                       pid_file=pid_file)]
        os.execv(argv[0], argv)  # noqa: S606 - the sleeper, by absolute path
    finally:
        os._exit(127)


def _report(use_dir: str, ready_fd: int | None, pid_file: str | None) -> None:
    """After admission, so every failure here is harmless: the sleeper still ends on the lifeline."""
    pid = str(os.getpid())
    try:
        os.close(os.open(Path(use_dir, PIDS, pid), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError:
        pass
    if pid_file is not None:
        tmp = Path(f"{pid_file}.{pid}.tmp")
        try:
            tmp.write_text(pid)
            tmp.replace(pid_file)
        except OSError:
            pass
    if ready_fd is not None:
        try:
            os.write(ready_fd, b"1")
            os.close(ready_fd)
        except OSError:
            pass


def wait_for_release(use_dir: str) -> None:
    """Return once the use's lifeline is released (or after CAP). The caller holds the census."""
    try:
        fd = os.open(Path(use_dir, LIFELINE), os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return
    end = time.monotonic() + CAP
    while time.monotonic() < end:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return
        except OSError:
            time.sleep(POLL)


def _options(words: list[str]) -> dict[str, str | bool] | None:
    out: dict[str, str | bool] = {}
    while words:
        word = words.pop(0)
        if word == "--new-session":
            out[word] = True
        elif word in ("--census-fd", "--ready-fd", "--pid-file") and words:
            out[word] = words.pop(0)
        else:
            return None
    return out


def main(argv: list[str]) -> int:
    if argv[:1] == ["--take"]:
        if len(argv) != 3 or not argv[1].isdigit():
            return 2
        return 0 if _lock_shared(int(argv[1]), argv[2]) else REFUSED
    if not argv:
        return 2
    use_dir, opts = argv[0], _options(argv[1:])
    if opts is None or not str(opts.get("--census-fd", "")).isdigit():
        return 2
    fd = int(str(opts["--census-fd"]))
    if not _same(fd, Path(use_dir, CENSUS)):
        return 1
    os.set_inheritable(fd, False)
    if opts.get("--new-session"):
        os.setsid()
    ready = opts.get("--ready-fd")
    pid_file = opts.get("--pid-file")
    _report(use_dir, int(str(ready)) if ready is not None else None,
            str(pid_file) if pid_file is not None else None)
    wait_for_release(use_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
