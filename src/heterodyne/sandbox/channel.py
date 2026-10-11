"""The agent-path result channel (ADR 0001 §7 launch self-test, S5): run/p.sock, /run/hz/p.sock inside.

A result counts only from a peer the host verifies itself, from outside: its host PID by SO_PEERCRED,
and /proc showing the image's python running the read-only probes as PROBE_ARGV, untraced, in the
workload container's netns, inside the environment allowlist, with the CLI binary as an ancestor inside
that netns. Any other peer is rejected, and a rejected peer alone fails the gate.

The peer is pinned by its SO_PEERPIDFD pidfd, the connecting process itself: it must still be alive (the
pidfd not readable) before and after the /proc reads, at accept and again at `done`, so the reads saw that
process and not a successor with its PID. After the check at `done` the host sends ACK, which the probe
waits for before it exits. The transcript is exact: one result per check, one integer `done`, nothing
after it, the ACK delivered, then a clean end of stream. `done` is stamped on a monotonic clock, for
the deadline.
Reads, connections and the time per connection are bounded, and `close` finishes every worker before the
verdict is read.
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
from heterodyne.sandbox.openshell_selftest import PROBE_ARGV, PROBE_EXE

MAX_LINE = 64 << 10
MAX_PEERS = 16              # connections served at once; any more is closed at once, and rejected
ACK = b"ack\n"              # sent after the check at `done`; the probe waits for it before exiting
ACCEPT_POLL = 0.05          # how often the accept loop looks for close()
JOIN_SECONDS = 2.0          # close() waits this long for each worker


class ProcVerifier:
    def __init__(self, netns: str, cli_binary: str, allowed_env: frozenset[str], *,
                 proc: Path = Path("/proc")) -> None:
        self.netns = netns
        self.cli = cli_binary
        self.allowed = allowed_env
        self.proc = proc

    def _ppid(self, pid: int) -> int:
        status = (self.proc / str(pid) / "status").read_text()
        return int(status.split("\nPPid:\t", 1)[1].split("\n", 1)[0])

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
        if "TracerPid:\t0\n" not in status:
            return "traced"
        if netns != self.netns:
            return "netns is not the workload container's"
        if env - self.allowed:
            return f"environment beyond the allowlist: {' '.join(sorted(env - self.allowed))}"
        q = pid
        while q > 1:
            try:
                q = self._ppid(q)
                if str((self.proc / str(q) / "ns" / "net").readlink()) != self.netns:
                    break
                if str((self.proc / str(q) / "exe").readlink()) == self.cli:
                    return ""
            except (OSError, IndexError, ValueError):
                break
        return f"no {self.cli} ancestor inside the workload netns"


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
    done_at: float | None = None    # the channel's clock when `done` arrived
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
        """"" on a pass, else the reason. `by`: the deadline on the channel's clock that `done` must meet."""
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
            if by is not None and (run.done_at is None or run.done_at > by):
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
                run.done_at = self.clock()
                return True
            check, ok, evidence = msg.get("check"), msg.get("ok"), msg.get("evidence")
            if (set(msg) == {"check", "ok", "evidence"} and isinstance(check, str) and type(ok) is bool
                    and isinstance(evidence, str) and check not in run.checks):
                run.checks[check] = ok
                return True
            run.malformed = True
            return False
