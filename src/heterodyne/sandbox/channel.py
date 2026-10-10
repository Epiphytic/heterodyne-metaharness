"""The agent-path result channel (ADR 0001 §7 launch self-test, S5): run/p.sock, /run/hz/p.sock inside.

A result counts only from a peer the host verifies itself, from outside: its host PID by SO_PEERCRED,
and /proc showing the image's python running the read-only probes as PROBE_ARGV, untraced, in the
workload container's netns, inside the environment allowlist, with the CLI binary as an ancestor inside
that netns. Any other peer is rejected, and a rejected peer alone fails the gate.
"""

import json
import socket
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from heterodyne.platform import peer_pid_checked
from heterodyne.sandbox.openshell_selftest import PROBE_ARGV, PROBE_EXE

MAX_LINE = 64 << 10
MAX_PEERS = 16


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


@dataclass
class _Run:
    pid: int
    checks: dict[str, bool] = field(default_factory=dict[str, bool])
    done: int | None = None
    changed: bool = False
    malformed: bool = False


class ProbeChannel:
    def __init__(self, path: Path, verify: Callable[[int], str], *,
                 peer_pid: Callable[[socket.socket], int] = peer_pid_checked) -> None:
        self.path = path
        self.verify = verify
        self.peer_pid = peer_pid
        self.runs: list[_Run] = []
        self.rejected: list[str] = []          # reasons, kept for the audit
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self.path.unlink(missing_ok=True)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(self.path))
        # As the session socket: the run directory (0700) keeps other host users out, and the sandbox's
        # user may map to another uid inside (S5 used the same mode).
        self.path.chmod(0o777)
        sock.listen(MAX_PEERS)
        self._sock = sock
        threading.Thread(target=self._accept, args=(sock,), daemon=True).start()

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()
        self.path.unlink(missing_ok=True)

    def done(self) -> bool:
        with self._lock:
            return any(r.done is not None or r.changed or r.malformed for r in self.runs)

    def verdict(self, expected: Sequence[str]) -> str:
        with self._lock:
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
            failed = [c for c in expected if run.checks.get(c) is False]
            missing = [c for c in expected if c not in run.checks]
            if failed:
                return f"failed checks: {', '.join(failed)}"
            if missing:
                return f"missing checks: {', '.join(missing)}"
            if run.done != 0:
                return "the probe did not finish cleanly"
            return ""

    def _accept(self, sock: socket.socket) -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            try:
                pid = self.peer_pid(conn)
            except OSError:
                with self._lock:
                    self.rejected.append("peer credentials unreadable")
                return
            why = self.verify(pid)
            if why:
                with self._lock:
                    self.rejected.append(why)
                return
            run = _Run(pid)
            with self._lock:
                self.runs.append(run)
            with conn.makefile("rb") as stream:
                for line in stream:
                    if not self._record(run, pid, line):
                        return

    def _record(self, run: _Run, pid: int, line: bytes) -> bool:
        try:
            if len(line) > MAX_LINE:
                raise ValueError(line[:16])
            raw: object = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(type(raw))
            msg = cast(dict[str, object], raw)
        except ValueError:
            with self._lock:
                run.malformed = True
            return False
        if "done" in msg:
            again = self.verify(pid)          # the same verified process, still untraced, at completion
            with self._lock:
                run.changed = bool(again)
                done = msg["done"]
                run.done = done if isinstance(done, int) else -1
            return False
        check, ok = msg.get("check"), msg.get("ok")
        with self._lock:
            if isinstance(check, str) and isinstance(ok, bool):
                run.checks[check] = ok and run.checks.get(check, True)
            else:
                run.malformed = True
        return not run.malformed
