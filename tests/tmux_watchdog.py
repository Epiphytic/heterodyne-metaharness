"""The tmux watchdog for offline tests (spec 2026-10-08-test-tmux-leak-design.md §2, btq-q1r4p).

Run as `python tmux_watchdog.py <run dir> <ack fd> [--deadline S]` by the `tmux_guard` plugin, in a
session of its own. It writes `ready` on the ack fd, then blocks until its stdin (whose only writer is
the pytest that started it) reaches EOF: pytest exited, by any signal, or closed it at teardown. Then:

- Barrier: `rename(run/s, run/dead)`. A launch that starts afterwards gets ENOENT; a bind that resolved
  the root before the rename can still land in `dead`, so closure is a successful `rmdir(dead)`.
- Closure loop: sweep every entry of `dead`, then `rmdir`. On ENOTEMPTY sweep again until the deadline.
- Finish: remove `run` only if closure succeeded and every outcome list is empty; otherwise keep it with
  `run/summary.json`.

Every entry of the root was created by our pytest or a child of it (the run dir is a 0700 mkdtemp only
that pytest knows), so whatever is in `dead` is ours to kill.
"""

import contextlib
import errno
import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEADLINE = 30.0      # seconds for the whole closure loop
CMD_TIMEOUT = 5.0    # any one tmux command, connect or ps
DEATH_WAIT = 5.0     # after kill-server, for the server to be proven dead
LOCK_TRIES = 20
LOCK_GAP = 0.1
PASS_GAP = 0.2
OUTCOMES = ("killed", "stale", "survived", "unresolved")


class DeadlineReached(Exception):
    """The global deadline cut an operation short: the entry is kept and reported, never forced."""


class Sweeper:
    """One watchdog's cleanup of `dead`. The probes (`tmux`, `connect`, `pid_running`, `try_lock`) and the
    clock are methods so tests can script them. Every wait, retry and command timeout is bounded by the
    time left before the global deadline."""

    def __init__(self, dead: Path, deadline: float = DEADLINE,
                 log: Callable[[str], None] = print) -> None:
        self.dead = dead
        self.log = log
        self.end = self.now() + deadline
        self.killed: list[dict[str, Any]] = []
        self.stale: list[dict[str, Any]] = []
        self.pending: dict[str, tuple[str, int | None]] = {}   # name -> (survived | unresolved, pid)
        self.known: dict[str, int] = {}     # name -> server PID once discovered; its death must be proven

    # --- probes ---

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def budget(self) -> float:
        """A timeout for one command: CMD_TIMEOUT, or less if the deadline is nearer."""
        left = self.end - self.now()
        if left <= 0:
            raise DeadlineReached
        return min(CMD_TIMEOUT, left)

    def tmux(self, path: Path, *args: str) -> tuple[int, str]:
        try:
            proc = subprocess.run(["tmux", "-S", str(path), *args], stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=self.budget(), check=False)
        except (OSError, subprocess.TimeoutExpired):
            return (-1, "")
        return (proc.returncode, proc.stdout.decode("utf-8", "replace"))

    def connect(self, path: Path) -> str:
        """`accept`, `refused`, `missing` or `error`."""
        timeout = self.budget()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except FileNotFoundError:
            return "missing"
        except ConnectionRefusedError:
            return "refused"
        except OSError:
            return "error"
        finally:
            s.close()
        return "accept"

    def pid_running(self, pid: int) -> bool:
        """False only if `pid` is proven gone or a zombie; any doubt counts as running."""
        if Path("/proc/self/stat").exists():
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
            except FileNotFoundError:
                return False
            except OSError:
                return True
            fields = stat.rsplit(")", 1)[-1].split()
            return not fields or fields[0] not in ("Z", "X")
        return self._ps_running(pid)

    def _ps_running(self, pid: int) -> bool:
        """`ps` where there is no /proc. Only its ordinary "no such process" (exit 1, no output at all)
        counts as gone; a failed inspection is doubt."""
        try:
            proc = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=self.budget(), check=False)
        except (OSError, subprocess.TimeoutExpired):
            return True
        state = proc.stdout.decode("utf-8", "replace").strip()
        if state:
            return not state.startswith("Z")
        return not (proc.returncode == 1 and not proc.stderr.strip())

    def try_lock(self, lock: Path) -> tuple[bool, int | None]:
        """Take tmux's startup lock (`<socket>.lock`, held from before bind until listen). Returns
        (taken, fd to close); a missing lock file means no startup holds it."""
        try:
            fd = os.open(lock, os.O_RDONLY | os.O_CLOEXEC)
        except FileNotFoundError:
            return (True, None)
        except OSError:
            return (False, None)
        for i in range(LOCK_TRIES):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return (True, fd)
            except BlockingIOError:
                if i == LOCK_TRIES - 1 or self.end - self.now() <= LOCK_GAP:
                    break
                self.sleep(LOCK_GAP)
            except OSError:
                break
        os.close(fd)
        return (False, None)

    # --- the sweep ---

    def run(self) -> bool:
        """Sweep and rmdir until `dead` is gone (True) or the deadline passes (False)."""
        while True:
            self.sweep()
            try:
                self.dead.rmdir()
                return True
            except FileNotFoundError:
                return True
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    self.log(f"rmdir {self.dead}: {exc}")
            left = self.end - self.now()
            if left <= 0:
                return False
            self.sleep(min(PASS_GAP, left))

    def sweep(self) -> None:
        try:
            names = sorted(p.name for p in self.dead.iterdir())
        except OSError as exc:
            self.log(f"listdir {self.dead}: {exc}")
            return
        for name in names:
            if self.now() >= self.end:
                return
            path = self.dead / name
            try:
                if name.endswith(".lock"):
                    if name[:-5] not in names:
                        self.sweep_lock(path)
                else:
                    self.sweep_socket(path)
            except DeadlineReached:
                # Keep what an earlier pass found (survived); otherwise it is unresolved.
                self.pending.setdefault(name, ("unresolved", self.known.get(name)))
                return
            except Exception as exc:  # noqa: BLE001 - one entry's failure must not stop the others
                self.log(f"{path}: {type(exc).__name__}: {exc}")
                self.pending[name] = ("unresolved", self.known.get(name))

    def sweep_socket(self, path: Path) -> None:
        pid = self.server_pid(path)
        if pid is not None:
            self.kill_known(path, pid)
            return
        state = self.connect(path)
        if state == "accept":
            self.log(f"kill-server {path} (pid unknown)")
            self.tmux(path, "kill-server")
            if not self.wait(lambda: self.connect(path) != "accept"):
                pid = self.server_pid(path)
                if pid is not None:
                    self.kill_known(path, pid)
                else:
                    self.pending[path.name] = ("survived", None)
                return
            state = self.connect(path)
        if state == "missing":
            self.pending.pop(path.name, None)
            return
        if state != "refused":
            self.pending[path.name] = ("unresolved", None)
            return
        self.refused_without_pid(path)

    def refused_without_pid(self, path: Path) -> None:
        """tmux binds before it listens, so a refusal proves nothing while a startup holds the lock."""
        lock = path.with_name(path.name + ".lock")
        taken, fd = self.try_lock(lock)
        if not taken:
            self.pending[path.name] = ("unresolved", None)
            return
        try:
            state = self.connect(path)
            if state == "accept":
                pid = self.server_pid(path)
                if pid is not None:
                    self.kill_known(path, pid)
                else:
                    self.pending[path.name] = ("survived", None)
                return
            if state not in ("refused", "missing"):
                self.pending[path.name] = ("unresolved", None)
                return
            self.log(f"stale {path}")
            path.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
            self.stale.append({"path": str(path), "pid": None})
            self.pending.pop(path.name, None)
        finally:
            if fd is not None:
                os.close(fd)

    def sweep_lock(self, lock: Path) -> None:
        taken, fd = self.try_lock(lock)
        if not taken:
            self.pending[lock.name] = ("unresolved", None)
            return
        try:
            self.log(f"stale {lock}")
            lock.unlink(missing_ok=True)
            self.stale.append({"path": str(lock), "pid": None})
            self.pending.pop(lock.name, None)
        finally:
            if fd is not None:
                os.close(fd)

    def server_pid(self, path: Path) -> int | None:
        """The server's PID, or the one an earlier pass found: once known, only proven death removes
        the socket (a later failed query is no proof)."""
        code, out = self.tmux(path, "display-message", "-p", "#{pid}")
        text = out.strip()
        if code == 0 and text.isdigit():
            self.known[path.name] = int(text)
        return self.known.get(path.name)

    def kill_known(self, path: Path, pid: int) -> None:
        self.log(f"kill-server {path} pid {pid}")
        self.tmux(path, "kill-server")
        if self.wait(lambda: self.connect(path) in ("refused", "missing") and not self.pid_running(pid)):
            path.unlink(missing_ok=True)
            self.killed.append({"path": str(path), "pid": pid})
            self.pending.pop(path.name, None)
        else:
            self.pending[path.name] = ("survived", pid)

    def wait(self, pred: Callable[[], bool], seconds: float = DEATH_WAIT) -> bool:
        """False after `seconds`; DeadlineReached if the global deadline comes first."""
        end = self.now() + seconds
        while not pred():
            if self.now() >= self.end:
                raise DeadlineReached
            if self.now() >= end:
                return False
            self.sleep(0.05)
        return True

    # --- the result ---

    def summary(self, closed: bool) -> dict[str, Any]:
        """`survived` and `unresolved` describe the final state: entries still present in `dead`."""
        present = {p.name for p in self.dead.iterdir()} if self.dead.exists() else set()
        out: dict[str, Any] = {"killed": list(self.killed), "stale": list(self.stale),
                               "survived": [], "unresolved": []}
        for name in sorted(present):
            if name.endswith(".lock") and name[:-5] in present:
                continue                            # reported with its socket
            outcome, pid = self.pending.get(name, ("unresolved", self.known.get(name)))
            out[outcome].append({"path": str(self.dead / name), "pid": pid})
        out["closed"] = closed
        return out


def finish(run: Path, summary: dict[str, Any]) -> None:
    if summary["closed"] and not any(summary[k] for k in OUTCOMES):
        shutil.rmtree(run, ignore_errors=True)
        return
    tmp = run / "summary.json.tmp"
    tmp.write_text(json.dumps(summary, indent=2) + "\n")
    tmp.replace(run / "summary.json")


def main(argv: list[str]) -> int:
    run, ack = Path(argv[1]), int(argv[2])
    deadline = float(argv[argv.index("--deadline") + 1]) if "--deadline" in argv else DEADLINE
    os.write(ack, b"ready")
    os.close(ack)
    while sys.stdin.buffer.read(4096):     # nothing is ever written; this returns at EOF
        pass
    root, dead = run / "s", run / "dead"
    with contextlib.suppress(FileNotFoundError):
        root.rename(dead)
    print(f"EOF: swept {dead}", flush=True)
    sweeper = Sweeper(dead, deadline=deadline, log=lambda line: print(line, flush=True))
    closed = sweeper.run() if dead.exists() else True
    summary = sweeper.summary(closed)
    print(json.dumps(summary), flush=True)
    finish(run, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
