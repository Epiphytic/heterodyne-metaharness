"""The agent-path result channel (ADR 0001 §7 launch self-test, S5): run/p.sock, /run/hz/p.sock inside.

A result counts only from a peer the host verifies itself, from outside: its host PID by SO_PEERCRED,
and /proc showing the image's python running the read-only probes as PROBE_ARGV, untraced, in the
workload container's netns, inside the environment allowlist, with the CLI binary as an ancestor inside
that netns. Any other peer is rejected, and a rejected peer alone fails the gate.

The peer is pinned by its SO_PEERPIDFD pidfd, the connecting process itself: it must still be alive (the
pidfd not readable) before and after the /proc reads, at accept and again at `done`, so the reads saw that
process and not a successor with its PID. After the check at `done` the host sends ACK, which the probe
waits for before it exits. The transcript is exact: one result per check, one integer `done`, nothing
after it, the ACK delivered, then a clean end of stream. The run's completion, after that end of stream,
is stamped on a monotonic clock, for the deadline.
Reads, connections and the time per connection are bounded, and `close` finishes every worker before the
verdict is read.
It also checks, at both points, that nothing in the workload can tamper with the probe while it runs (§7
Probe protection, D23): every thread of every workload process but its first has no_new_privs and no
capability, and a scan it can't complete or resolve fails.
"""

import json
import os
import select
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from heterodyne.platform import peer_pidfd_checked
from heterodyne.sandbox.openshell_selftest import MIN_PTRACE_SCOPE, PROBE_ARGV, PROBE_EXE

MAX_LINE = 64 << 10
MAX_PEERS = 16              # connections served at once; any more is closed at once, and rejected
NO_CAPS = "0000000000000000"
SCAN_LIMIT = 1 << 17        # /proc entries (processes and threads) one workload scan may read
SCAN_SECONDS = 10.0         # and the time it may take; past either, the scan fails
GONE = (FileNotFoundError, ProcessLookupError)      # the process or thread exited while it was read
ACK = b"ack\n"              # sent after the check at `done`; the probe waits for it before exiting
ACCEPT_POLL = 0.05          # how often the accept loop looks for close()
JOIN_SECONDS = 2.0          # close() waits this long for each worker


class _ScanFailed(Exception):
    pass


class _Budget:
    """One scan's bound: SCAN_LIMIT entries and `seconds` on `clock`. Per scan, as workers verify at once."""
    def __init__(self, clock: Callable[[], float], seconds: float) -> None:
        self.clock = clock
        self.end = clock() + seconds
        self.entries = 0

    def tick(self) -> None:
        self.entries += 1
        if self.entries > SCAN_LIMIT or self.clock() > self.end:
            raise _ScanFailed("the workload scan did not finish")


def _stat(d: Path) -> tuple[int, int]:
    """(ppid, start time) from /proc/<pid>/stat: one read, so both are the same process's. The comm,
    in parentheses, may itself hold ") "."""
    fields = (d / "stat").read_text().rpartition(")")[2].split()
    return int(fields[1]), int(fields[19])


def start_time(proc: Path, pid: int) -> int:
    """A process's start time, in clock ticks since boot: with its pid, its identity."""
    return _stat(proc / str(pid))[1]


class ProcVerifier:
    def __init__(self, netns: str, cli_binary: str, allowed_env: frozenset[str], *,
                 namespaces: tuple[str, str], root_pid: int, root_start: int,
                 ptrace_scope: Callable[[], int], proc: Path = Path("/proc"),
                 clock: Callable[[], float] = time.monotonic, budget: float = SCAN_SECONDS) -> None:
        self.netns = netns
        self.cli = cli_binary
        self.allowed = allowed_env
        self.namespaces = namespaces         # the workload's (user, mnt), pinned by the host
        self.root = root_pid                 # the workload container's first process, the one task
        self.root_start = root_start         # exempt, pinned by its start time against pid reuse
        self.clock = clock
        self.budget = budget
        self.ptrace_scope = ptrace_scope
        self.proc = proc

    @staticmethod
    def _field(status: str, name: str) -> str:
        return status.split(f"\n{name}:\t", 1)[1].split("\n", 1)[0]

    def _ppid(self, pid: int) -> int:
        return int(self._field((self.proc / str(pid) / "status").read_text(), "PPid"))

    def _cli_ancestor(self, pid: int) -> int | None:
        q = pid
        while q > 1:
            try:
                q = self._ppid(q)
                if str((self.proc / str(q) / "ns" / "net").readlink()) != self.netns:
                    return None
                if str((self.proc / str(q) / "exe").readlink()) == self.cli:
                    return q
            except (OSError, IndexError, ValueError):
                return None
        return None

    def _scan(self) -> str:
        """"" when no workload task but the first process can gain or holds a capability, else why not.

        Workload members: the descendants of the first process, and every process in the workload's
        mount namespace (one reparented away from the tree, say). Each process's parent and start time
        come from one read of its stat, so a parent must exist and have started first; else its pid was
        reused, or it exited mid-scan, and membership is unresolved. Every member's every thread must
        have no_new_privs and no permitted or effective capability: capabilities are per thread. A
        process created after the listing inherits no_new_privs from its parent, and under it can gain
        no capability on exec. Any evidence missing, malformed, or changed between reads fails the
        scan, as does running past SCAN_LIMIT entries or the budget."""
        try:
            budget = _Budget(self.clock, self.budget)
            procs, mounted = self._list(budget)
            if procs.get(self.root, (0, -1))[1] != self.root_start:
                return "the workload's first process changed"
            members = mounted | {pid for pid in procs if self._descends(pid, procs)}
            members.discard(self.root)
            for pid in sorted(members):
                why = self._tasks(pid, procs[pid], budget)
                if why:
                    return why
        except _ScanFailed as exc:
            return str(exc)
        return ""

    def _list(self, budget: _Budget) -> tuple[dict[int, tuple[int, int]], set[int]]:
        """Every listed process's (ppid, start time), and those in the workload's mount namespace."""
        procs: dict[int, tuple[int, int]] = {}
        mounted: set[int] = set()
        for d in self.proc.iterdir():
            if not d.name.isdigit():
                continue
            budget.tick()
            try:
                procs[int(d.name)] = _stat(d)
            except GONE:
                continue                     # exited: its children, if any, then name a missing parent
            except (OSError, IndexError, ValueError):
                raise _ScanFailed("workload process evidence unreadable") from None
            try:
                if str((d / "ns" / "mnt").readlink()) == self.namespaces[1]:
                    mounted.add(int(d.name))
            except OSError:
                pass                         # another user's, or gone: its membership rests on ancestry
        return procs, mounted

    def _descends(self, pid: int, procs: dict[int, tuple[int, int]]) -> bool:
        q, steps = pid, 0
        while q != self.root:
            ppid, start = procs[q]
            if ppid == 0:
                return False
            if ppid not in procs or procs[ppid][1] > start or steps > len(procs):
                raise _ScanFailed("the workload process list is unresolved")
            q, steps = ppid, steps + 1
        return q != pid

    def _tasks(self, pid: int, seen: tuple[int, int], budget: _Budget) -> str:
        d = self.proc / str(pid)
        try:
            tids = sorted(int(t.name) for t in (d / "task").iterdir() if t.name.isdigit())
            for tid in tids:
                budget.tick()
                try:
                    status = (d / "task" / str(tid) / "status").read_text()
                except GONE:
                    continue                 # the thread exited
                nnp = self._field(status, "NoNewPrivs")
                caps = {self._field(status, "CapPrm"), self._field(status, "CapEff")}
                who = f"workload process {pid}" + ("" if tid == pid else f" (thread {tid})")
                if nnp != "1":
                    return f"{who} can gain privileges"
                if caps != {NO_CAPS}:
                    return f"{who} holds capabilities"
            if _stat(d) != seen:
                raise _ScanFailed("the workload process list is unresolved")   # reused or reparented
        except GONE:
            return ""                        # exited before or while its threads were read
        except (OSError, IndexError, ValueError):
            raise _ScanFailed("workload process evidence unreadable") from None
        return ""

    def __call__(self, pid: int) -> str:
        p = self.proc / str(pid)
        try:
            exe = str((p / "exe").readlink())
            argv = tuple(a.decode("utf-8", "replace") for a in (p / "cmdline").read_bytes().split(b"\0")[:-1])
            status = (p / "status").read_text()
            netns = str((p / "ns" / "net").readlink())
            env = {e.split(b"=", 1)[0].decode("utf-8", "replace")
                   for e in (p / "environ").read_bytes().split(b"\0") if e}
        except OSError as exc:
            return f"/proc/{pid} unreadable ({exc.strerror})"
        if exe != PROBE_EXE or argv != PROBE_ARGV:
            return "not the probe"
        if "\nTracerPid:\t0\n" not in status:
            return "traced"
        if "\nNoNewPrivs:\t1\n" not in status:
            return "the probe can gain privileges"
        if netns != self.netns:
            return "netns is not the workload container's"
        if env - self.allowed:
            return f"environment beyond the allowlist: {' '.join(sorted(env - self.allowed))}"
        cli = self._cli_ancestor(pid)
        if cli is None:
            return f"no {self.cli} ancestor inside the workload netns"
        try:
            seen = {tuple(str((self.proc / str(q) / "ns" / ns).readlink()) for ns in ("user", "mnt"))
                    for q in (pid, cli)}
        except OSError:
            seen = set[tuple[str, ...]]()
        if seen != {self.namespaces}:
            return "the probe or its CLI is outside the workload's user and mount namespaces"
        scope = self.ptrace_scope()
        if scope < MIN_PTRACE_SCOPE:
            return f"kernel.yama.ptrace_scope is {scope}; probe protection needs {MIN_PTRACE_SCOPE} or more"
        return self._scan()


def alive(pidfd: int) -> bool:
    """A pidfd turns readable when its process exits."""
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    return not poller.poll(0)


@dataclass
class _Run:
    pid: int
    checks: dict[str, bool] = field(default_factory=dict[str, bool])
    done: int | None = None
    finished_at: float | None = None    # the channel's clock when the run finished (the stamp for `by`)
    finished: bool = False      # `done`, the check at `done`, the ACK sent, then the peer's clean EOF
    end: str = ""               # how the connection ended: eof, timeout, error, shutdown, ack-failed
    changed: bool = False
    malformed: bool = False


class ProbeChannel:
    def __init__(self, path: Path, verify: Callable[[int], str], *,
                 peer: Callable[[socket.socket], tuple[int, int]] = peer_pidfd_checked,
                 clock: Callable[[], float] = time.monotonic, seconds: float = 900.0) -> None:
        self.path = path
        self.verify = verify
        self.peer = peer                       # (pid, pidfd); the channel owns and closes the pidfd
        self.clock = clock
        self.seconds = seconds                 # each connection's deadline, from its accept
        self.runs: list[_Run] = []
        self.rejected: list[str] = []          # reasons, kept for the audit
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._accepter: threading.Thread | None = None
        self._conns: set[socket.socket] = set()
        self._workers: list[threading.Thread] = []
        self._closed = False
        self._unfinished = False

    def start(self) -> None:
        """Listen on `path`. On any failure nothing is left behind: no socket, no pathname."""
        self.path.unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX)
        bound = False
        try:
            sock.bind(str(self.path))
            bound = True
            # As the session socket: the run directory (0700) keeps other host users out, and the
            # sandbox's user may map to another uid inside (S5 used the same mode).
            self.path.chmod(0o777)
            sock.listen(MAX_PEERS)
            sock.settimeout(ACCEPT_POLL)
            accepter = threading.Thread(target=self._accept, args=(sock,), daemon=True)
            accepter.start()
        except BaseException:
            sock.close()
            if bound:
                self.path.unlink(missing_ok=True)
            raise
        self._sock, self._accepter = sock, accepter

    def close(self) -> None:
        """Accept what is already queued, end every connection, wait for every worker, then remove the
        socket. The verdict is read only after this."""
        self._stop.set()
        if self._accepter is not None:
            self._accepter.join(JOIN_SECONDS)
        with self._lock:
            conns, workers = list(self._conns), list(self._workers)
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in workers:
            worker.join(JOIN_SECONDS)
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()
            self.path.unlink(missing_ok=True)
        accepter = [self._accepter] if self._accepter is not None else []
        with self._lock:
            self._unfinished = any(w.is_alive() for w in [*accepter, *workers])
            self._closed = True

    def done(self) -> bool:
        with self._lock:
            return bool(self.rejected) or any(r.finished or r.changed or r.malformed for r in self.runs)

    def verdict(self, expected: Sequence[str], *, by: float | None = None) -> str:
        """"" on a pass, else the reason. `by`: the deadline on the channel's clock that the run's
        completion (after the check at `done`, the ACK and the clean end of stream) must meet."""
        with self._lock:
            if not self._closed:
                raise RuntimeError("the verdict is read only after close()")
            if self._unfinished:
                return "the channel did not finish"
            if self.rejected:
                return "a peer that is not the probe connected"
            if not self.runs:
                return "no verified probe run"
            if len(self.runs) > 1:
                return "more than one probe run"
            run = self.runs[0]
            if run.malformed:
                return "malformed result"
            if run.changed:
                return "the probe changed before it finished"
            if not run.finished:
                return "the probe did not finish cleanly"
            if by is not None and (run.finished_at is None or run.finished_at > by):
                return "the probe did not finish in time"
            unexpected = sorted(set(run.checks) - set(expected))
            failed = [c for c in expected if run.checks.get(c) is False]
            missing = [c for c in expected if c not in run.checks]
            if unexpected:
                return f"unexpected checks: {', '.join(unexpected)}"
            if failed:
                return f"failed checks: {', '.join(failed)}"
            if missing:
                return f"missing checks: {', '.join(missing)}"
            if run.done != 0:
                return "the probe did not finish cleanly"
            return ""

    def _accept(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self._admit(conn)
        sock.setblocking(False)              # close(): take what connected before it, then stop
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            self._admit(conn)

    def _admit(self, conn: socket.socket) -> None:
        with self._lock:
            if len(self._conns) >= MAX_PEERS:
                self.rejected.append("too many peers")
                conn.close()
                return
            self._conns.add(conn)
            worker = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            self._workers.append(worker)
        worker.start()

    def _pinned(self, pidfd: int, pid: int) -> str:
        """The /proc checks, bracketed by the pidfd: the process was alive throughout, so they saw it."""
        if not alive(pidfd):
            return "the peer exited"
        why = self.verify(pid)
        if not why and not alive(pidfd):
            return "the peer exited"
        return why

    def _serve(self, conn: socket.socket) -> None:
        try:
            with conn:
                try:
                    pid, pidfd = self.peer(conn)
                except OSError:
                    with self._lock:
                        self.rejected.append("peer credentials unreadable")
                    return
                try:
                    self._converse(conn, pid, pidfd)
                finally:
                    os.close(pidfd)
        finally:
            with self._lock:
                self._conns.discard(conn)

    def _converse(self, conn: socket.socket, pid: int, pidfd: int) -> None:
        why = self._pinned(pidfd, pid)
        if why:
            with self._lock:
                self.rejected.append(why)
            return
        run = _Run(pid)
        with self._lock:
            self.runs.append(run)
        deadline = self.clock() + self.seconds
        buf = b""
        while True:
            line, sep, rest = buf.partition(b"\n")
            if sep:
                buf = rest
                if not self._record(run, line):
                    return
                if run.done is not None:
                    break
                continue
            if len(buf) > MAX_LINE:
                with self._lock:
                    run.malformed = True
                return
            chunk, end = self._recv(conn, deadline, MAX_LINE + 1 - len(buf))
            if end:
                with self._lock:
                    run.end = end              # no `done`: the run is unfinished, however it ended
                return
            buf += chunk
        why = self._pinned(pidfd, pid)         # the same process, still alive and untraced, at completion
        if why:
            with self._lock:
                run.changed = True
            return
        try:
            conn.sendall(ACK)
        except OSError:
            with self._lock:
                run.end = "ack-failed"
            return
        trailing, end = (buf, "") if buf else self._recv(conn, deadline, 1)
        with self._lock:
            if trailing:
                run.malformed = True           # nothing may follow `done`
            run.end = end
            run.finished = not trailing and end == "eof"
            if run.finished:
                run.finished_at = self.clock()

    def _recv(self, conn: socket.socket, deadline: float, n: int) -> tuple[bytes, str]:
        """(data, "") or (b"", how the stream ended): eof, timeout, error or shutdown (close())."""
        left = deadline - self.clock()
        if left <= 0:
            return b"", "timeout"
        conn.settimeout(left)
        try:
            data = conn.recv(n)
        except TimeoutError:
            return b"", "timeout"
        except OSError:
            return b"", "shutdown" if self._stop.is_set() else "error"
        if data:
            return data, ""
        return b"", "shutdown" if self._stop.is_set() else "eof"

    def _record(self, run: _Run, line: bytes) -> bool:
        """One message, to the exact schema; False on anything else (the run is then malformed)."""
        try:
            raw: object = json.loads(line)
        except ValueError:
            raw = None
        msg = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
        with self._lock:
            if set(msg) == {"done"} and type(msg["done"]) is int:
                run.done = msg["done"]
                return True
            check, ok, evidence = msg.get("check"), msg.get("ok"), msg.get("evidence")
            if (set(msg) == {"check", "ok", "evidence"} and isinstance(check, str) and type(ok) is bool
                    and isinstance(evidence, str) and check not in run.checks):
                run.checks[check] = ok
                return True
            run.malformed = True
            return False
