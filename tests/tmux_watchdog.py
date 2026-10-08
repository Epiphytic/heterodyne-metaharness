"""The tmux watchdog for offline tests (spec 2026-10-08-test-tmux-leak-design.md §2 and Amendment 1,
btq-q1r4p).

Run as `python tmux_watchdog.py <run dir> <ack fd> [--deadline S]` by the `tmux_guard` plugin, in a
session of its own. It writes `ready` on the ack fd, then blocks until its stdin (whose only writer is
the pytest that started it) reaches EOF: pytest exited, by any signal, or closed it at teardown. Then:

- Barrier: `rename(run/s, run/dead)`. A launch that starts afterwards gets ENOENT; a bind that resolved
  the root before the rename can still land in `dead`, so closure is a successful `rmdir(dead)`.
- Closure loop: sweep every entry of `dead` until only `launch.lock` is left, then the final step: under
  LOCK_EX on the original lock inode, unlink it and `rmdir(dead)`. If anything appeared meanwhile the rmdir
  fails, and that ends closure: nothing more is removed.
- Removal: every unlink of a socket or a stray `<p>.lock` happens under LOCK_EX (never blocking) on
  `dead/launch.lock`, after checking the lock is still the inode opened at the start (A3). Every guarded
  tmux client and server holds LOCK_SH on it, so under EX none of them is between bind and listen. tmux's
  own `<p>.lock` is never created, opened or flocked here.
- Finish: remove `run` only if closure succeeded and every outcome list is empty; otherwise keep it with
  `run/summary.json`.

Every entry of the root was created by our pytest or a child of it (the run dir is a 0700 mkdtemp only
that pytest knows), so whatever is in `dead` is ours to kill.
"""

import contextlib
import fcntl
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEADLINE = 30.0      # seconds for the whole closure loop
CMD_TIMEOUT = 5.0    # any one tmux command, connect or ps
DEATH_WAIT = 5.0     # after kill-server, for the server to be proven dead
PASS_GAP = 0.2
WAIT_GAP = 0.05
OUTCOMES = ("killed", "stale", "survived", "unresolved")
LOCK_NAME = "launch.lock"
MISSING = "launch lock missing"
REPLACED = "launch lock replaced"
HELD = "held"
LOCK_HELD = "launch lock held"
APPEARED = "appeared after final unlink"
TIMED_OUT = "deadline passed"


class DeadlineReached(Exception):
    """The global deadline cut an operation short: the entry is kept and reported, never forced."""


class WaitExpired(Exception):
    """A probe inside `wait` ran out of the wait's own time."""


Generation = tuple[int, int]     # (st_dev, st_ino) of one socket file: a replacement is another one


class Sweeper:
    """One watchdog's cleanup of `dead`. The probes (`tmux`, `connect`, `pid_running`, `take_ex`) and the
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
        # name -> (socket generation, server PID) from discovery until the death is proven. A PID whose
        # socket was replaced or went away first moves to `orphans`: still reported until proven dead.
        self.known: dict[str, tuple[Generation, int]] = {}
        self.orphans: list[dict[str, Any]] = []
        self.limit: float | None = None     # the end of the current `wait`, which its probes respect
        self.lock_fd: int | None = None     # dead/launch.lock, opened once; EX is only ever taken on it
        self.lock_id: Generation | None = None
        self.fault: str | None = None       # MISSING or REPLACED: nothing more is removed
        self.note: str | None = None        # why launch.lock itself is still in `dead`
        self.held: set[str] = set()         # names whose last removal found SH held
        self.appeared: dict[str, dict[str, Any]] = {}   # entries found by a failed final rmdir

    # --- probes ---

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def budget(self) -> float:
        """A timeout for one command: CMD_TIMEOUT, or less if the deadline (or the current wait's end)
        is nearer."""
        now = self.now()
        left = self.end - now
        if left <= 0:
            raise DeadlineReached
        if self.limit is not None:
            if self.limit - now <= 0:
                raise WaitExpired
            left = min(left, self.limit - now)
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

    def generation(self, path: Path) -> Generation | None:
        try:
            st = path.lstat()
        except FileNotFoundError:
            return None
        return (st.st_dev, st.st_ino)

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
        """`ps` where there is no /proc. Gone only on its ordinary "no such process" (exit 1, both streams
        empty) or a successful inspection showing a zombie; anything else is doubt."""
        try:
            proc = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=self.budget(), check=False)
        except (OSError, subprocess.TimeoutExpired):
            return True
        if proc.returncode == 1 and proc.stdout == b"" and proc.stderr == b"":
            return False
        if proc.returncode == 0 and proc.stderr == b"":
            return not proc.stdout.decode("utf-8", "replace").strip().startswith("Z")
        return True

    def open_lock(self) -> bool:
        """Open `dead/launch.lock` once (close-on-exec, so tmux and ps never inherit it) and record its
        identity. Without it nothing may be removed."""
        try:
            fd = os.open(self.dead / LOCK_NAME, os.O_RDONLY | os.O_CLOEXEC)
        except OSError as exc:
            self.log(f"open {self.dead / LOCK_NAME}: {exc}")
            self.fault = MISSING
            return False
        st = os.fstat(fd)
        self.lock_fd, self.lock_id = fd, (st.st_dev, st.st_ino)
        return True

    def take_ex(self) -> bool:
        """LOCK_EX, never blocking: False while any guarded tmux process holds SH."""
        assert self.lock_fd is not None
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def drop_ex(self) -> None:
        assert self.lock_fd is not None
        fcntl.flock(self.lock_fd, fcntl.LOCK_UN)

    def lock_intact(self) -> bool:
        """`dead/launch.lock` is still the inode we hold (checked under EX)."""
        try:
            st = os.lstat(self.dead / LOCK_NAME)
        except OSError:
            return False
        return (st.st_dev, st.st_ino) == self.lock_id

    def unlink(self, path: Path) -> None:
        """Every unlink the sweeper makes."""
        path.unlink(missing_ok=True)

    def before_rmdir(self) -> None:
        """After the final unlink, before `rmdir(dead)` (a seam for tests, like `unlinking`)."""

    # --- the sweep ---

    def run(self) -> bool:
        """Sweep until only the launch lock is left and the final step closes `dead` (True), or the
        deadline passes or closure is stopped (False)."""
        if not self.open_lock():
            self.sweep()                            # kills only: every removal is refused
            return False
        try:
            while True:
                self.sweep()
                try:
                    names = {p.name for p in self.dead.iterdir()}
                except FileNotFoundError:
                    self.settle()
                    return True
                except OSError as exc:
                    self.log(f"listdir {self.dead}: {exc}")
                    names = None
                if names is not None and names <= {LOCK_NAME}:
                    closed = self.close_dir()
                    if closed is not None:
                        return closed
                elif self.fault is not None:
                    return False
                left = self.end - self.now()
                if left <= 0:
                    return False
                self.sleep(min(PASS_GAP, left))
        finally:
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd = None

    def close_dir(self) -> bool | None:
        """The final step: True once `dead` is gone, False if closure must stop, None to retry (held)."""
        if self.fault is not None:
            return False
        if not self.take_ex():
            if self.note != LOCK_HELD:
                self.log(f"{LOCK_HELD}: {self.dead / LOCK_NAME}")
            self.note = LOCK_HELD
            return None
        try:
            if not self.lock_intact():
                self.lock_replaced()
                return False
            self.note = None
            self.unlink(self.dead / LOCK_NAME)
            self.before_rmdir()
            try:
                self.dead.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.log(f"rmdir {self.dead}: {exc}")
                self.appeared = self.inventory()
                return False
        finally:
            self.drop_ex()                          # only after the rmdir attempt
        self.settle()
        return True

    def lock_replaced(self) -> None:
        self.log(f"{REPLACED}: {self.dead / LOCK_NAME}")
        self.fault = REPLACED

    def inventory(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with contextlib.suppress(OSError):
            for p in sorted(self.dead.iterdir()):
                try:
                    st = p.lstat()
                except OSError:
                    continue
                out[p.name] = {"path": str(p), "pid": None, "reason": APPEARED, "type": kind(st.st_mode),
                               "generation": [st.st_dev, st.st_ino]}
        return out

    def settle(self) -> None:
        """Retire PIDs whose socket is gone, and drop orphans now proven dead."""
        with contextlib.suppress(OSError):
            present = {p.name for p in self.dead.iterdir()} if self.dead.exists() else set()
            for name in set(self.known) - present:
                self.retire(name)
        for orphan in list(self.orphans):
            try:
                if not self.pid_running(orphan["pid"]):
                    self.log(f"orphan pid {orphan['pid']} of {orphan['path']} is gone")
                    self.orphans.remove(orphan)
            except DeadlineReached:
                return

    def retire(self, name: str) -> None:
        """`name`'s socket was replaced or went away before its server was proven dead. What was found
        about that generation (its pending outcome) goes with it."""
        entry = self.known.pop(name, None)
        if entry is not None:
            self.pending.pop(name, None)
            gen, pid = entry
            self.orphans.append({"path": str(self.dead / name), "pid": pid, "generation": list(gen)})

    def known_pid(self, name: str) -> int | None:
        entry = self.known.get(name)
        return entry[1] if entry else None

    def sweep(self) -> None:
        self.settle()
        try:
            names = sorted(p.name for p in self.dead.iterdir())
        except OSError as exc:
            self.log(f"listdir {self.dead}: {exc}")
            return
        for name in names:
            if self.now() >= self.end:
                return
            if name == LOCK_NAME:
                continue
            path = self.dead / name
            try:
                if name.endswith(".lock"):
                    if name[:-5] not in names:
                        self.sweep_lock(path)
                else:
                    self.sweep_socket(path)
            except DeadlineReached:
                # Keep what an earlier pass found (survived); otherwise it is unresolved.
                self.pending.setdefault(name, ("unresolved", self.known_pid(name)))
                return
            except Exception as exc:  # noqa: BLE001 - one entry's failure must not stop the others
                self.log(f"{path}: {type(exc).__name__}: {exc}")
                self.pending[name] = ("unresolved", self.known_pid(name))

    def sweep_socket(self, path: Path) -> None:
        gen = self.generation(path)
        if gen is None:
            self.gone(path)
            return
        pid = self.server_pid(path, gen)
        if pid is not None:
            self.kill_known(path, gen, pid)
            return
        state = self.connect(path)
        if state == "accept":
            self.log(f"kill-server {path} (pid unknown)")
            self.tmux(path, "kill-server")
            if not self.wait(lambda: self.connect(path) != "accept"):
                pid = self.server_pid(path, gen)
                if pid is not None:
                    self.kill_known(path, gen, pid)
                else:
                    self.pending[path.name] = ("survived", None)
                return
            state = self.connect(path)
        if state == "missing":
            self.gone(path)
            return
        if state != "refused":
            self.pending[path.name] = ("unresolved", None)
            return
        self.refused_without_pid(path, gen)

    def gone(self, path: Path) -> None:
        self.retire(path.name)
        self.pending.pop(path.name, None)

    def refused_without_pid(self, path: Path, gen: Generation) -> None:
        """tmux binds before it listens, so a refusal proves nothing while a guarded process holds SH."""
        result = self.remove(path, gen)
        if result == "removed":
            self.log(f"stale {path}")
            self.stale.append({"path": str(path), "pid": None})
        self.record(path.name, result)

    def record(self, name: str, result: str) -> None:
        """What one removal attempt leaves for the summary: anything but removed or gone is looked at
        again next pass, and is unresolved at the deadline."""
        if result == HELD:
            self.held.add(name)
        else:
            self.held.discard(name)
        if result in ("removed", "gone"):
            self.pending.pop(name, None)
        else:
            self.pending[name] = ("unresolved", None)

    def remove(self, path: Path, gen: Generation, pid: int | None = None) -> str:
        """The one path that unlinks an entry: under EX on the launch lock (so no guarded tmux process
        is alive), only while that lock is still the original inode, and only if the entry is still
        generation `gen`, refuses connections (sockets) and `pid` (if given) is dead. A socket's
        `<p>.lock` goes with it. Returns `removed`, `held` (SH is held), `fault` (the launch lock is
        missing or was replaced: nothing more is removed), `gone`, `changed` (replaced) or `live`."""
        if self.fault is not None:
            return "fault"
        if not self.take_ex():
            return HELD
        try:
            if not self.lock_intact():
                self.lock_replaced()
                return "fault"
            current = self.generation(path)
            if current is None:
                return "gone"
            if current != gen:
                return "changed"
            is_lock = path.name.endswith(".lock")
            if not is_lock and self.connect(path) not in ("refused", "missing"):
                return "live"
            if pid is not None and self.pid_running(pid):
                return "live"
            self.unlinking(path)
            self.unlink(path)
            if not is_lock:
                self.unlink(path.with_name(path.name + ".lock"))
            return "removed"
        finally:
            self.drop_ex()

    def unlinking(self, path: Path) -> None:
        """Between validation and unlink (a seam for tests: the gap a replacement would need)."""

    def sweep_lock(self, lock: Path) -> None:
        """A `<p>.lock` without its socket: just an entry, removed under EX like any other."""
        gen = self.generation(lock)
        if gen is None:
            self.record(lock.name, "gone")
            return
        result = self.remove(lock, gen)
        if result == "removed":
            self.log(f"stale {lock}")
            self.stale.append({"path": str(lock), "pid": None})
        self.record(lock.name, result)

    def server_pid(self, path: Path, gen: Generation) -> int | None:
        """The PID serving this generation of the socket, or the one an earlier pass found for it: once
        known, only proven death removes the socket (a later failed query is no proof). A PID cached for
        another generation is retired, never reused for a replacement."""
        code, out = self.tmux(path, "display-message", "-p", "#{pid}")
        text = out.strip()
        found = int(text) if code == 0 and text.isdigit() and self.generation(path) == gen else None
        entry = self.known.get(path.name)
        if entry is not None and (entry[0] != gen or found not in (None, entry[1])):
            self.retire(path.name)
            entry = None
        if found is not None:
            entry = self.known[path.name] = (gen, found)
        return entry[1] if entry else None

    def kill_known(self, path: Path, gen: Generation, pid: int) -> None:
        self.log(f"kill-server {path} pid {pid}")
        self.tmux(path, "kill-server")
        if not self.wait(lambda: self.connect(path) in ("refused", "missing") and not self.pid_running(pid)):
            self.pending[path.name] = ("survived", pid)
            return
        self.known.pop(path.name, None)             # proven dead: the PID is no evidence for anything else
        self.killed.append({"path": str(path), "pid": pid})
        self.record(path.name, self.remove(path, gen, pid))   # held while any guarded process holds SH

    def wait(self, pred: Callable[[], bool], seconds: float = DEATH_WAIT) -> bool:
        """False after `seconds`; DeadlineReached if the global deadline comes first. Probes get only the
        time left (`budget`), no sleep runs past either end, and success after the deadline is no
        success."""
        end = self.now() + seconds
        self.limit = end
        try:
            while True:
                now = self.now()
                if now >= self.end:
                    raise DeadlineReached
                if now >= end:
                    return False
                try:
                    ok = pred()
                except WaitExpired:
                    return False
                now = self.now()
                if now >= self.end:
                    raise DeadlineReached
                if now >= end:                      # an answer at or after the wait's end is too late
                    return False
                if ok:
                    return True
                self.sleep(min(WAIT_GAP, end - now, self.end - now))
        finally:
            self.limit = None

    # --- the result ---

    def summary(self, closed: bool) -> dict[str, Any]:
        """`survived` and `unresolved` describe the final state: entries still present in `dead`. When
        `closed` is false, `reason` says why: the fault, an appeared entry, the held launch lock, or the
        deadline."""
        present = {p.name for p in self.dead.iterdir()} if self.dead.exists() else set()
        out: dict[str, Any] = {"killed": list(self.killed), "stale": list(self.stale),
                               "survived": [], "unresolved": []}
        for name in sorted(present):
            path = str(self.dead / name)
            if name in self.appeared:
                out["unresolved"].append(dict(self.appeared[name]))
                continue
            if name == LOCK_NAME:
                if self.note or self.fault:
                    out["unresolved"].append({"path": path, "pid": None, "reason": self.note or self.fault})
                continue
            if name.endswith(".lock") and name[:-5] in present:
                continue                            # reported with its socket
            outcome, pid = self.pending.get(name, ("unresolved", self.known_pid(name)))
            entry: dict[str, Any] = {"path": path, "pid": pid}
            reason = self.fault or (HELD if name in self.held else None)
            if outcome == "unresolved" and reason is not None:
                entry["reason"] = reason
            out[outcome].append(entry)
        for name in sorted(set(self.known) - present):
            gen, pid = self.known[name]
            out["unresolved"].append({"path": str(self.dead / name), "pid": pid, "generation": list(gen)})
        out["unresolved"].extend(self.orphans)
        out["closed"] = closed
        if not closed:                              # why closure failed, even with nothing left to list
            out["reason"] = self.fault or (APPEARED if self.appeared else None) or self.note or TIMED_OUT
        return out


def kind(mode: int) -> str:
    for test, name in ((stat.S_ISSOCK, "socket"), (stat.S_ISREG, "file"), (stat.S_ISDIR, "dir"),
                       (stat.S_ISLNK, "symlink")):
        if test(mode):
            return name
    return "other"


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
