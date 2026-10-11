import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sandbox_env import short_dir, wait_for

from heterodyne.sandbox import channel as channel_module
from heterodyne.sandbox.channel import ACK, MAX_LINE, MAX_PEERS, ProbeChannel, ProcVerifier
from heterodyne.sandbox.openshell_selftest import PROBE_ARGV, PROBE_EXE

NETNS = "net:[4026531999]"
CLI = "/opt/codex/bin/codex"
ALLOWED = frozenset({"HOME", "PATH"})


NO_CAPS = "0000000000000000"
HOST_NS: dict[str, Any] = {"userns": "user:[4026531837]", "mntns": "mnt:[4026531841]"}
WORKLOAD_CG = "/user.slice/user-1001.slice/user-session.slice/libpod-hz.scope"
SELF_CG = "/user.slice/user-1001.slice/user-session.slice/tmux-spawn-hz.scope"      # the scanner's
HOST_CG = "/init.scope"


def status_text(tgid: int, tid: int, ppid: int, *, tracer: int = 0, nnp: int = 1, caps: str = NO_CAPS) -> str:
    return (f"Name:\tpython3\nTgid:\t{tgid}\nPid:\t{tid}\nPPid:\t{ppid}\nTracerPid:\t{tracer}\n"
            f"Uid:\t1000\nNoNewPrivs:\t{nnp}\nCapPrm:\t{caps}\nCapEff:\t{caps}\n")


def stat_text(pid: int, ppid: int, start: int) -> str:
    """/proc/<pid>/stat: ppid is field 4, the start time field 22; the comm may hold ") "."""
    return f"{pid} (a) b) S {ppid} " + "0 " * 17 + f"{start} 0 0\n"


def cgroup_dir(root: Path, path: str) -> Path:
    """A cgroup v2 directory under the fake cgroupfs, thawed, which freezes at once when asked."""
    d = root / "cgroupfs" / path.lstrip("/")
    if not (d / "cgroup.threads").exists():
        d.mkdir(parents=True, exist_ok=True)
        (d / "cgroup.threads").write_text("")
        (d / "cgroup.freeze").write_text("0\n")
        (d / "cgroup.events").write_text("populated 1\nfrozen 1\n")
    return d


def join(root: Path, cgroup: str | None, tid: int) -> None:
    if cgroup is not None:
        with (cgroup_dir(root, cgroup) / "cgroup.threads").open("a") as f:
            f.write(f"{tid}\n")


def fake_thread(root: Path, pid: int, tid: int, *, ppid: int, nnp: int = 1, caps: str = NO_CAPS,
                cgroup: str | None = WORKLOAD_CG) -> None:
    """A thread of `pid` other than its leader: /proc/<tid> shows its own status."""
    (root / str(tid)).mkdir()
    (root / str(tid) / "status").write_text(status_text(pid, tid, ppid, nnp=nnp, caps=caps))
    join(root, cgroup, tid)


def fake_proc(root: Path, pid: int, *, exe: str, argv: tuple[str, ...], ppid: int, netns: str = NETNS,
              env: tuple[str, ...] = ("HOME=/s/home",), tracer: int = 0, nnp: int = 1, caps: str = NO_CAPS,
              userns: str = "user:[4026531837]", mntns: str = "mnt:[4026532001]",
              start: int | None = None, cgroup: str | None = WORKLOAD_CG) -> None:
    """A process, its leader thread listed in `cgroup`; it started at `start` (its pid unless given)."""
    d = root / str(pid)
    (d / "ns").mkdir(parents=True)
    (d / "stat").write_text(stat_text(pid, ppid, pid if start is None else start))
    (d / "cgroup").write_text(f"0::{cgroup or HOST_CG}\n")
    (d / "exe").symlink_to(exe)
    (d / "ns" / "net").symlink_to(netns)
    (d / "ns" / "user").symlink_to(userns)
    (d / "ns" / "mnt").symlink_to(mntns)
    (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
    (d / "environ").write_bytes(b"".join(e.encode() + b"\0" for e in env))
    (d / "status").write_text(status_text(pid, pid, ppid, tracer=tracer, nnp=nnp, caps=caps))
    join(root, cgroup, pid)


def probe_tree(root: Path, **probe: object) -> None:
    """pid 1: the host's init, 7: the workload's first process (the supervisor: no no_new_privs,
    capable), 30: the CLI, 31: a shell, 32: the probe. The scanner runs in a cgroup of its own."""
    (root / "self").mkdir(exist_ok=True)
    (root / "self" / "cgroup").write_text(f"0::{SELF_CG}\n")
    fake_proc(root, 1, exe="/usr/lib/systemd/systemd", argv=("systemd",), ppid=0, nnp=0,
              caps="000001ffffffffff", cgroup=None, **HOST_NS)
    fake_proc(root, 7, exe="/opt/openshell/bin/supervisor", argv=("supervisor",), ppid=1, nnp=0,
              caps="000001ffffffffff")
    fake_proc(root, 30, exe=CLI, argv=("codex",), ppid=7)
    fake_proc(root, 31, exe="/usr/bin/bash", argv=("bash", "-c", "..."), ppid=30)
    fields = {"exe": PROBE_EXE, "argv": PROBE_ARGV, "ppid": 31, **probe}
    fake_proc(root, 32, **fields)  # type: ignore[arg-type]


WORKLOAD_NS = ("user:[4026531837]", "mnt:[4026532001]")      # fake_proc's defaults
AGENT_NS: dict[str, Any] = {"userns": "user:[4026533334]", "mntns": "mnt:[4026533333]"}


def verifier(root: Path, scope: int = 2, **kw: Any) -> ProcVerifier:
    cgroup_dir(root, WORKLOAD_CG)
    return ProcVerifier(NETNS, CLI, ALLOWED, namespaces=WORKLOAD_NS, root_pid=7, root_start=7,
                        cgroup=WORKLOAD_CG, ptrace_scope=lambda: scope, proc=root,
                        cgroupfs=root / "cgroupfs", **kw)


def freeze_state(root: Path) -> str:
    return (cgroup_dir(root, WORKLOAD_CG) / "cgroup.freeze").read_text().strip()


def test_the_real_probe_verifies(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    assert verifier(tmp_path)(32) == ""


@pytest.mark.parametrize("probe, reason", [
    ({"exe": "/usr/bin/python3"}, "not the probe"),
    ({"argv": ("python3", "/run/hz/probes.py", "--agent")}, "not the probe"),
    ({"tracer": 77}, "traced"),
    ({"netns": "net:[1]"}, "netns"),
    ({"env": ("HOME=/s/home", "LD_PRELOAD=/x.so")}, "environment beyond the allowlist: LD_PRELOAD"),
])
def test_a_wrong_peer_is_rejected(tmp_path: Path, probe: dict[str, object], reason: str) -> None:
    probe_tree(tmp_path, **probe)
    assert reason in verifier(tmp_path)(32)


def test_no_cli_ancestor_inside_the_netns_is_rejected(tmp_path: Path) -> None:
    fake_proc(tmp_path, 30, exe=CLI, argv=("codex",), ppid=7, netns="net:[1]")   # the CLI, but outside
    fake_proc(tmp_path, 31, exe="/usr/bin/bash", argv=("bash",), ppid=30)
    fake_proc(tmp_path, 32, exe=PROBE_EXE, argv=PROBE_ARGV, ppid=31)
    assert "no /opt/codex/bin/codex ancestor" in verifier(tmp_path)(32)


def test_a_vanished_pid_is_rejected(tmp_path: Path) -> None:
    assert "unreadable" in verifier(tmp_path)(99)


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    with short_dir() as d:
        yield d


def pinned(pid: int) -> tuple[int, int]:
    """A fake PID with a live pidfd (this test process's own), as peer_pidfd_checked returns them."""
    return pid, os.pidfd_open(os.getpid())


def send(path: Path, *lines: object) -> bytes:
    """Connect, send each message (a str as is, else JSON) on its own line, and return what the host
    sends back before it closes: ACK for a run it accepted."""
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(path))
        s.sendall(b"".join((ln if isinstance(ln, str) else json.dumps(ln)).encode() + b"\n" for ln in lines))
        s.shutdown(socket.SHUT_WR)               # all sent: as the probe exiting after the ACK
        s.settimeout(5)
        got = b""
        try:
            while chunk := s.recv(64):
                got += chunk
                if got.endswith(ACK):
                    break
        except ConnectionResetError:
            pass                                 # a rejected peer, closed with its lines unread
        return got


def all_pass(expected: tuple[str, ...]) -> list[object]:
    return [*({"check": c, "ok": True, "evidence": "x"} for c in expected), {"done": 0}]


EXPECTED = ("a", "b")


def channel(path: Path, pids: list[int], verdicts: dict[int, str], **kw: object) -> ProbeChannel:
    ch = ProbeChannel(path, lambda pid: verdicts.get(pid, "not the probe"),
                      peer=lambda s: pinned(pids.pop(0)), **kw)  # type: ignore[arg-type]
    ch.start()
    return ch


def finish(ch: ProbeChannel) -> str:
    ch.close()
    return ch.verdict(EXPECTED)


def test_one_verified_run_passes_and_is_acknowledged(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    try:
        assert send(sock_dir / "p.sock", *all_pass(EXPECTED)) == ACK
        wait_for(ch.done)
    finally:
        assert finish(ch) == ""
    assert not (sock_dir / "p.sock").exists()


def test_the_verdict_is_read_only_after_close(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [], {})
    try:
        with pytest.raises(RuntimeError):
            ch.verdict(EXPECTED)
    finally:
        ch.close()


def test_a_forger_after_the_probe_fails_the_gate(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32, 40], {32: ""})
    send(sock_dir / "p.sock", *all_pass(EXPECTED))
    wait_for(ch.done)
    with socket.socket(socket.AF_UNIX) as s:      # pid 40, not the probe, connected just before close
        s.connect(str(sock_dir / "p.sock"))
        assert finish(ch) == "a peer that is not the probe connected"


@pytest.mark.parametrize("lines, reason", [
    ([{"check": "a", "ok": True, "evidence": ""}, {"done": 0}], "missing checks: b"),
    ([{"check": "a", "ok": True, "evidence": ""}, {"check": "b", "ok": False, "evidence": ""}, {"done": 1}],
     "failed checks: b"),
    ([{"check": "a", "ok": True, "evidence": ""}, {"check": "b", "ok": True, "evidence": ""}, {"done": 1}],
     "the probe did not finish cleanly"),
    (["not json"], "malformed result"),
    # exactly one result per check, no unknown check, the exact schema, one integer `done`, nothing after it
    ([{"check": "a", "ok": True, "evidence": ""}, {"check": "a", "ok": True, "evidence": ""},
      {"check": "b", "ok": True, "evidence": ""}, {"done": 0}], "malformed result"),
    ([*all_pass(EXPECTED)[:2], {"check": "c", "ok": True, "evidence": ""}, {"done": 0}],
     "unexpected checks: c"),
    ([*all_pass(EXPECTED)[:2], {"done": False}], "malformed result"),
    ([*all_pass(EXPECTED)[:2], {"done": 0.0}], "malformed result"),
    ([*all_pass(EXPECTED)[:2], {"done": 0, "extra": 1}], "malformed result"),
    ([{"check": "a", "ok": 1, "evidence": ""}, *all_pass(EXPECTED)[1:]], "malformed result"),
    ([{"check": "a", "ok": True}, *all_pass(EXPECTED)[1:]], "malformed result"),
    ([{"check": "a", "ok": True, "evidence": "", "more": 1}, *all_pass(EXPECTED)[1:]], "malformed result"),
    ([*all_pass(EXPECTED), {"check": "a", "ok": False, "evidence": ""}], "malformed result"),
    ([*all_pass(EXPECTED), {"done": 1}], "malformed result"),
    ([*all_pass(EXPECTED), "junk"], "malformed result"),
    ([*all_pass(EXPECTED)[:2]], "the probe did not finish cleanly"),
])
def test_bad_results_fail(sock_dir: Path, lines: list[object], reason: str) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    send(sock_dir / "p.sock", *lines)
    wait_for(lambda: ch.done() or any(r.end for r in ch.runs))   # else close() may cut the wait for EOF
    assert finish(ch) == reason


def test_trailing_data_after_the_ack_fails(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(sock_dir / "p.sock"))
        s.sendall(b"".join(json.dumps(m).encode() + b"\n" for m in all_pass(EXPECTED)))
        s.settimeout(5)
        assert s.recv(64) == ACK
        s.sendall(b'{"done": 0}\n')
    wait_for(ch.done)
    assert finish(ch) == "malformed result"


def test_a_peer_that_changes_before_done_fails(sock_dir: Path) -> None:
    calls: list[int] = []

    def verify(pid: int) -> str:
        calls.append(pid)
        return "" if len(calls) == 1 else "traced"

    ch = ProbeChannel(sock_dir / "p.sock", verify, peer=lambda s: pinned(32))
    ch.start()
    assert send(sock_dir / "p.sock", *all_pass(EXPECTED)) == b""     # no ACK for a changed probe
    assert finish(ch) == "the probe changed before it finished"


def test_no_run_at_all_fails(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [], {})
    assert finish(ch) == "no verified probe run"


def test_a_peer_credential_failure_counts_as_rejected(sock_dir: Path) -> None:
    def broken(s: socket.socket) -> tuple[int, int]:
        raise OSError("no peer credentials")

    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer=broken)
    ch.start()
    send(sock_dir / "p.sock", *all_pass(EXPECTED))
    assert finish(ch) == "a peer that is not the probe connected"


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(["sleep", "30"])  # noqa: S607 - a stand-in process with a PID of its own


@pytest.mark.parametrize("when", ["at-accept", "at-done"])
def test_a_peer_replaced_during_the_proc_reads_is_rejected(sock_dir: Path, when: str) -> None:
    """The pidfd pins the process that connected. If it exits while /proc is read, the reads may have
    seen a successor with its PID (the stand-in 'verifies'), so the result can't count."""
    original = _sleeper()
    calls: list[int] = []

    def verify(pid: int) -> str:
        calls.append(pid)
        if (when == "at-accept") == (len(calls) == 1):
            original.kill()                  # the original exits mid-read ...
            original.wait()
        return ""                            # ... and what the reads saw still looks like the probe

    try:
        ch = ProbeChannel(sock_dir / "p.sock", verify,
                          peer=lambda s: (original.pid, os.pidfd_open(original.pid)))
        ch.start()
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        expected = ("a peer that is not the probe connected" if when == "at-accept"
                    else "the probe changed before it finished")
        assert finish(ch) == expected
    finally:
        original.kill()
        original.wait()


def test_a_peer_replaced_before_pinning_is_rejected(sock_dir: Path) -> None:
    """SO_PEERPIDFD gives the pidfd of the process that connected, even once it has exited and its PID
    names another. Here the original is gone, a live successor holds 'its' PID and verifies, and the
    pidfd still shows the original dead: the results can't count."""
    original, successor = _sleeper(), _sleeper()
    pidfd = os.pidfd_open(original.pid)
    original.kill()
    original.wait()
    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "" if pid == successor.pid else "not the probe",
                      peer=lambda s: (successor.pid, os.dup(pidfd)))
    ch.start()
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        assert finish(ch) == "a peer that is not the probe connected"
        assert ch.rejected == ["the peer exited"]
    finally:
        os.close(pidfd)
        successor.kill()
        successor.wait()


def _ended(sock_dir: Path, monkeypatch: pytest.MonkeyPatch, how: str) -> ProbeChannel:
    """A run that sends every check and `done`, then ends `how` instead of with a clean EOF."""
    real_send, real_recv = socket.socket.sendall, socket.socket.recv
    acked: list[bool] = []

    def sendall(self: socket.socket, data: bytes, *args: object) -> None:
        if data == ACK:
            if how == "ack-failed":
                raise BrokenPipeError(32, "injected")
            acked.append(True)
        real_send(self, data)

    def recv(self: socket.socket, n: int, *args: object) -> bytes:
        if how == "error" and acked and threading.current_thread() is not threading.main_thread():
            raise ConnectionResetError(104, "injected")
        return real_recv(self, n)

    monkeypatch.setattr(socket.socket, "sendall", sendall)
    monkeypatch.setattr(socket.socket, "recv", recv)
    ch = channel(sock_dir / "p.sock", [32], {32: ""}, seconds=0.5 if how == "timeout" else 30.0)
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_dir / "p.sock"))
    s.sendall(b"".join(json.dumps(m).encode() + b"\n" for m in all_pass(EXPECTED)))
    if how in ("timeout", "shutdown"):
        s.settimeout(5)
        assert s.recv(16) == ACK                 # then it neither ends its stream nor goes away
        wait_for(lambda: how == "shutdown" or ch.runs[0].end != "")
    else:
        wait_for(lambda: ch.runs and ch.runs[0].end != "")
    ch.close()
    s.close()
    return ch


@pytest.mark.parametrize("how", ["timeout", "shutdown", "error", "ack-failed"])
def test_a_run_that_does_not_end_with_a_clean_eof_fails(
        sock_dir: Path, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    ch = _ended(sock_dir, monkeypatch, how)
    assert ch.runs[0].end == how and not ch.runs[0].finished
    assert ch.verdict(EXPECTED) == "the probe did not finish cleanly"


def test_a_clean_run_ends_with_eof(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    send(sock_dir / "p.sock", *all_pass(EXPECTED))
    wait_for(ch.done)
    assert finish(ch) == "" and ch.runs[0].end == "eof"


def test_the_deadline_applies_to_completion(sock_dir: Path) -> None:
    now = [100.0]
    ch = channel(sock_dir / "p.sock", [32], {32: ""}, clock=lambda: now[0])
    send(sock_dir / "p.sock", *all_pass(EXPECTED))
    wait_for(ch.done)
    ch.close()
    assert ch.runs[0].finished_at == 100.0
    assert ch.verdict(EXPECTED, by=100.0) == ""                       # at the deadline: in time
    assert ch.verdict(EXPECTED, by=99.5) == "the probe did not finish in time"


def test_done_in_time_with_a_late_handshake_fails(sock_dir: Path) -> None:
    """`done` arrives before the deadline, but the check at `done`, the ACK and the clean EOF finish
    after it: the run is stamped when it completes, so it is late."""
    now = [100.0]
    calls: list[int] = []

    def verify(pid: int) -> str:
        calls.append(pid)
        if len(calls) == 2:
            now[0] = 200.0                       # the check at `done` overruns the deadline
        return ""

    ch = ProbeChannel(sock_dir / "p.sock", verify, peer=lambda s: pinned(32), clock=lambda: now[0])
    ch.start()
    assert send(sock_dir / "p.sock", *all_pass(EXPECTED)) == ACK
    wait_for(ch.done)
    ch.close()
    assert ch.runs[0].finished_at == 200.0
    assert ch.verdict(EXPECTED, by=150.0) == "the probe did not finish in time"


CLIENT = """
import json, socket, sys
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
for c in ("a", "b"):
    s.sendall((json.dumps({"check": c, "ok": True, "evidence": "x"}) + "\\n").encode())
s.sendall(b'{"done": 0}\\n')
if sys.argv[2] == "wait":            # as the probe: stay alive until the host acknowledges
    s.settimeout(10)
    sys.stdout.write(s.recv(16).decode())
"""


@pytest.mark.parametrize("client, reason", [("wait", ""), ("exit", "the probe changed before it finished")])
def test_a_real_probe_process_is_checked_alive_at_done(sock_dir: Path, client: str, reason: str) -> None:
    """A real subprocess, peer credentials and pidfd: the probe that waits for the ACK is still alive at
    the check at `done`; one that exits at once is not (the check is slowed so it surely has)."""
    child: list[subprocess.Popen[str]] = []
    calls: list[int] = []

    def verify(pid: int) -> str:
        calls.append(pid)
        if len(calls) == 2:
            time.sleep(0.5)
        return "" if pid == child[0].pid else "not the probe"

    ch = ProbeChannel(sock_dir / "p.sock", verify)
    ch.start()
    try:
        child.append(subprocess.Popen([sys.executable, "-I", "-c", CLIENT, str(sock_dir / "p.sock"), client],
                                      stdout=subprocess.PIPE, text=True))
        out, _ = child[0].communicate(timeout=30)
        wait_for(ch.done)
    finally:
        got = finish(ch)
    assert got == reason
    assert out == (ACK.decode() if client == "wait" else "")


def test_an_oversized_unterminated_line_is_malformed_without_buffering_it(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(sock_dir / "p.sock"))
        s.settimeout(5)
        try:
            for _ in range(4):               # the host stops reading after MAX_LINE, and closes
                s.sendall(b"x" * MAX_LINE)
        except OSError:
            pass
        wait_for(ch.done)
    assert finish(ch) == "malformed result"


def test_a_stalled_peer_hits_its_deadline(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""}, seconds=0.3)
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(sock_dir / "p.sock"))
        s.sendall(json.dumps({"check": "a", "ok": True, "evidence": ""}).encode() + b"\n")
        s.settimeout(5)
        assert s.recv(16) == b""             # the host gives up on it, and closes
    assert finish(ch) == "the probe did not finish cleanly"


def test_a_connection_flood_is_capped_and_rejected(sock_dir: Path) -> None:
    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer=lambda s: pinned(32))
    ch.start()
    socks = [socket.socket(socket.AF_UNIX) for _ in range(MAX_PEERS + 4)]
    try:
        for s in socks:
            s.connect(str(sock_dir / "p.sock"))
        wait_for(lambda: "too many peers" in ch.rejected)
        assert finish(ch) == "a peer that is not the probe connected"
    finally:
        for s in socks:
            s.close()


def test_close_ends_stalled_peers_and_their_workers(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(sock_dir / "p.sock"))
        wait_for(lambda: len(ch.runs) == 1)
        before = threading.active_count()
        t0 = time.monotonic()
        assert finish(ch) == "the probe did not finish cleanly"
        assert time.monotonic() - t0 < 2
        assert threading.active_count() <= before - 2      # the worker and the accept loop are gone
        s.settimeout(5)
        assert s.recv(16) == b""


@pytest.mark.parametrize("step", ["chmod", "listen", "thread"])
def test_a_failed_start_leaves_nothing_behind(sock_dir: Path, monkeypatch: pytest.MonkeyPatch,
                                              step: str) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected")

    targets: dict[str, tuple[object, str]] = {"chmod": (Path, "chmod"), "listen": (socket.socket, "listen"),
                                              "thread": (channel_module.threading.Thread, "start")}
    owner, name = targets[step]
    monkeypatch.setattr(owner, name, fail)
    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer=lambda s: pinned(32))
    with pytest.raises(OSError, match="injected"):
        ch.start()
    monkeypatch.undo()
    assert not (sock_dir / "p.sock").exists()
    ch.close()


def test_the_modules_import_without_linux_pidfds() -> None:
    """macOS has neither os.pidfd_open nor SO_PEERPIDFD: importing the channel and the self-test must
    still work (the runtime chooses NoRuntime after the import); only a peer check fails, closed."""
    code = """
import os, socket, sys
for name in ("pidfd_open",):
    if hasattr(os, name):
        delattr(os, name)
if hasattr(socket, "SO_PEERPIDFD"):
    delattr(socket, "SO_PEERPIDFD")
import heterodyne.sandbox.channel, heterodyne.sandbox.openshell_selftest
from heterodyne import platform
sys.platform = "darwin"
a, b = socket.socketpair(socket.AF_UNIX)
try:
    platform.peer_pidfd_checked(a)
except OSError:
    print("fails closed")
"""
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60,
                       check=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "fails closed\n"


@pytest.mark.parametrize("probe, reason", [
    ({"nnp": 0}, "the probe can gain privileges"),
    ({"mntns": "mnt:[4026533333]"}, "outside the workload's user and mount namespaces"),    # a new mount ns
    ({"userns": "user:[4026533334]"}, "outside the workload's user and mount namespaces"),
])
def test_a_probe_that_could_be_tampered_with_is_rejected(tmp_path: Path, probe: dict[str, object],
                                                          reason: str) -> None:
    probe_tree(tmp_path, **probe)
    assert reason in verifier(tmp_path)(32)


@pytest.mark.parametrize("scope", [-1, 0, 1])
def test_a_ptrace_scope_below_2_is_rejected(tmp_path: Path, scope: int) -> None:
    probe_tree(tmp_path)
    assert f"kernel.yama.ptrace_scope is {scope}" in verifier(tmp_path, scope)(32)


def test_a_capable_workload_process_is_rejected(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    fake_proc(tmp_path, 40, exe="/usr/bin/python3.12", argv=("python3",), ppid=30, caps="0000000000080000")
    assert verifier(tmp_path)(32) == "workload process 40 holds capabilities"


def test_capable_processes_outside_the_workload_are_ignored(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    fake_proc(tmp_path, 50, exe="/usr/sbin/sshd", argv=("sshd",), ppid=1, caps="000001ffffffffff", nnp=0,
              cgroup=None, **HOST_NS)
    assert verifier(tmp_path)(32) == ""


def test_a_cli_and_probe_in_namespaces_the_agent_made_are_rejected(sock_dir: Path, tmp_path: Path) -> None:
    """An overlay over /run/hz from a new user and mount namespace, with the CLI started in there as the
    forged probe's parent: the two agree with each other, but not with the namespaces the host pinned."""
    probe_tree(tmp_path, **AGENT_NS)
    shutil.rmtree(tmp_path / "30")
    fake_proc(tmp_path, 30, exe=CLI, argv=("codex",), ppid=7, userns=AGENT_NS["userns"],
              mntns=AGENT_NS["mntns"])
    reason = "the probe or its CLI is outside the workload's user and mount namespaces"
    assert verifier(tmp_path)(32) == reason
    ch = ProbeChannel(sock_dir / "p.sock", verifier(tmp_path), peer=lambda s: pinned(32))
    ch.start()
    send(sock_dir / "p.sock", *all_pass(EXPECTED))
    assert finish(ch) == "a peer that is not the probe connected"
    assert ch.rejected == [reason]


def test_a_probe_traced_after_it_connected_fails_at_completion(sock_dir: Path, tmp_path: Path) -> None:
    probe_tree(tmp_path)
    ch = ProbeChannel(sock_dir / "p.sock", verifier(tmp_path), peer=lambda s: pinned(32))
    ch.start()
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(sock_dir / "p.sock"))
        s.sendall(b"".join((json.dumps(m) + "\n").encode() for m in all_pass(EXPECTED)[:-1]))
        wait_for(lambda: bool(ch.runs) and len(ch.runs[0].checks) == len(EXPECTED))
        status = tmp_path / "32" / "status"
        status.write_text(status.read_text().replace("TracerPid:\t0", "TracerPid:\t31"))
        s.sendall(b'{"done": 0}\n')
        wait_for(ch.done)
    assert finish(ch) == "the probe changed before it finished"


SYS_PTRACE = "0000000000080000"


def extra(root: Path, pid: int, ppid: int, **fields: object) -> None:
    """Another workload task: a python, by default under no_new_privs with no capability."""
    fake_proc(root, pid, exe="/usr/bin/python3.12", argv=("python3",), ppid=ppid, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("pid, ppid, fields", [
    (30, 7, {"nnp": 0, "caps": SYS_PTRACE}),        # the CLI itself, launched capable
    (40, 31, {"nnp": 0, "caps": SYS_PTRACE}),       # a tool the agent ran, capable
    (40, 31, {"nnp": 0}),                           # under no no_new_privs, it can still gain them
])
def test_only_the_workloads_first_process_may_go_without_no_new_privs(
        tmp_path: Path, pid: int, ppid: int, fields: dict[str, Any]) -> None:
    probe_tree(tmp_path)
    if pid == 30:
        (tmp_path / "30" / "status").write_text(status_text(30, 30, 7, **fields))
    else:
        extra(tmp_path, pid, ppid, **fields)
    assert verifier(tmp_path)(32) == f"workload process {pid} can gain privileges"
    assert freeze_state(tmp_path) == "0"


def test_a_capable_thread_behind_a_harmless_leader_is_rejected(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    extra(tmp_path, 40, 31)
    fake_thread(tmp_path, 40, 41, ppid=31, caps=SYS_PTRACE)
    assert verifier(tmp_path)(32) == "workload process 40 (thread 41) holds capabilities"


def test_the_first_processs_threads_are_exempt(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    fake_thread(tmp_path, 7, 8, ppid=1, nnp=0, caps="000001ffffffffff")
    assert verifier(tmp_path)(32) == ""


@pytest.mark.parametrize("ns", [True, False])
def test_a_reparented_capable_process_is_a_member_by_its_cgroup(tmp_path: Path, ns: bool) -> None:
    """Reparented to the host's init, in namespaces it made or with its namespaces unreadable (the r2
    control): its cgroup still lists it."""
    probe_tree(tmp_path)
    extra(tmp_path, 60, 1, caps=SYS_PTRACE, **AGENT_NS)
    if not ns:
        shutil.rmtree(tmp_path / "60" / "ns")
    assert verifier(tmp_path)(32) == "workload process 60 holds capabilities"


def test_a_capable_process_in_a_child_cgroup_is_a_member(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    extra(tmp_path, 40, 31, caps=SYS_PTRACE, cgroup=WORKLOAD_CG + "/agent-made")
    assert verifier(tmp_path)(32) == "workload process 40 holds capabilities"


def test_a_probe_outside_the_workloads_cgroup_is_rejected(tmp_path: Path) -> None:
    probe_tree(tmp_path, cgroup="/user.slice/elsewhere.scope")
    assert verifier(tmp_path)(32) == "the probe is outside the workload's cgroup"


def test_a_thread_that_ends_while_frozen_fails_closed(tmp_path: Path) -> None:
    """Fork and exit: the listed parent is gone though the cgroup was frozen (a SIGKILL from outside, or a
    freeze that didn't hold), so the scan can't vouch for what it did before it ended."""
    probe_tree(tmp_path)
    extra(tmp_path, 40, 31)
    extra(tmp_path, 41, 40, caps=SYS_PTRACE)
    (tmp_path / "40" / "status").unlink()
    assert verifier(tmp_path)(32) == "a workload thread ended while the workload was frozen"


def test_every_workload_read_happens_while_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A capable thread can't replace a checked one, nor a child appear unlisted: no workload read is made
    before the freeze holds or after the thaw."""
    probe_tree(tmp_path)
    extra(tmp_path, 40, 31)
    fake_thread(tmp_path, 40, 41, ppid=31)
    v = verifier(tmp_path)
    freeze = cgroup_dir(tmp_path, WORKLOAD_CG) / "cgroup.freeze"
    thawed_reads: list[str] = []
    real_text, real_bytes, real_link = Path.read_text, Path.read_bytes, Path.readlink

    def watch(path: Path) -> None:
        if path != freeze and path.name != "cgroup.events" and real_text(freeze).strip() != "1":
            thawed_reads.append(path.name)

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        watch(self)
        return real_text(self, *args, **kwargs)

    def read_bytes(self: Path) -> bytes:
        watch(self)
        return real_bytes(self)

    def readlink(self: Path) -> Path:
        watch(self)
        return real_link(self)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "readlink", readlink)
    assert v(32) == ""
    monkeypatch.undo()
    assert thawed_reads == [] and freeze_state(tmp_path) == "0"


def unreadable(path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads a file whatever its mode")
    path.chmod(0)


@pytest.mark.parametrize("what", ["missing", "unwritable"])
def test_a_workload_that_cant_be_frozen_fails_closed(tmp_path: Path, what: str) -> None:
    probe_tree(tmp_path)
    v = verifier(tmp_path)
    freeze = cgroup_dir(tmp_path, WORKLOAD_CG) / "cgroup.freeze"
    if what == "missing":
        freeze.unlink()
    else:
        unreadable(freeze)
    assert v(32) == "the workload could not be frozen"
    if what == "missing":
        assert not freeze.exists()                  # the write never creates a control file


def test_a_freeze_that_never_holds_times_out_and_thaws(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    (cgroup_dir(tmp_path, WORKLOAD_CG) / "cgroup.events").write_text("populated 1\nfrozen 0\n")
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    v = verifier(tmp_path, clock=lambda: now[0], sleep=sleep, budget=1.0)
    assert v(32) == "the workload could not be frozen"
    assert 1.0 < now[0] < 1.1 and freeze_state(tmp_path) == "0"


def test_the_workload_is_thawed_when_the_check_raises(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    probe_tree(tmp_path)
    v = verifier(tmp_path)

    def fail(self: ProcVerifier, pid: int, budget: object) -> str:
        assert freeze_state(tmp_path) == "1"
        raise RuntimeError("injected")

    monkeypatch.setattr(ProcVerifier, "_threads", fail)
    with pytest.raises(RuntimeError, match="injected"):
        v(32)
    assert freeze_state(tmp_path) == "0"


def test_a_thaw_that_fails_fails_the_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe_tree(tmp_path)
    v = verifier(tmp_path)
    monkeypatch.setattr(ProcVerifier, "_thaw", lambda self: False)
    assert v(32) == "the workload could not be thawed"


def test_a_low_ptrace_scope_never_freezes(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    v = verifier(tmp_path, scope=1)
    (cgroup_dir(tmp_path, WORKLOAD_CG) / "cgroup.freeze").unlink()
    assert v(32).startswith("kernel.yama.ptrace_scope is 1")


@pytest.mark.parametrize("hide", ["threads", "status", "subdir"])
def test_unreadable_evidence_fails_closed(tmp_path: Path, hide: str) -> None:
    probe_tree(tmp_path)
    extra(tmp_path, 40, 31, cgroup=WORKLOAD_CG + "/child")
    target = {"threads": cgroup_dir(tmp_path, WORKLOAD_CG) / "cgroup.threads",
              "status": tmp_path / "40" / "status",
              "subdir": cgroup_dir(tmp_path, WORKLOAD_CG + "/child")}[hide]
    unreadable(target)
    try:
        assert verifier(tmp_path)(32) == "workload process evidence unreadable"
    finally:
        target.chmod(0o755)
    assert freeze_state(tmp_path) == "0"


@pytest.mark.parametrize("status", [
    "Name:\tx\nTgid:\t40\nPid:\t40\n",                                       # no NoNewPrivs or caps
    status_text(40, 41, 31),                                                 # another thread's status
])
def test_malformed_evidence_fails_closed(tmp_path: Path, status: str) -> None:
    probe_tree(tmp_path)
    extra(tmp_path, 40, 31)
    (tmp_path / "40" / "status").write_text(status)
    assert verifier(tmp_path)(32) == "workload process evidence unreadable"


def test_a_malformed_cgroup_thread_list_fails_closed(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    (cgroup_dir(tmp_path, WORKLOAD_CG) / "cgroup.threads").write_text("7\nx\n")
    assert verifier(tmp_path)(32) == "workload process evidence unreadable"


@pytest.mark.parametrize("start", [None, 8])
def test_a_replaced_first_process_fails_closed(tmp_path: Path, start: int | None) -> None:
    probe_tree(tmp_path)
    if start is None:
        (tmp_path / "7" / "stat").unlink()
    else:
        (tmp_path / "7" / "stat").write_text(stat_text(7, 1, start))
    assert verifier(tmp_path)(32) == "the workload's first process changed"


@pytest.mark.parametrize("phase", ["freeze", "subdirs", "threads"])
def test_a_scan_past_its_budget_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             phase: str) -> None:
    """The clock runs out in each phase in turn: waiting for the freeze, walking the cgroup tree, or
    reading the threads (every clock read is a second). A first, unbounded run finds the phases."""
    probe_tree(tmp_path)
    for i in range(20):
        extra(tmp_path, 100 + i, 31, cgroup=f"{WORKLOAD_CG}/sub{i}")
    if phase == "freeze":
        (cgroup_dir(tmp_path, WORKLOAD_CG) / "cgroup.events").write_text("frozen 0\n")
    now = [0.0]

    def clock() -> float:
        now[0] += 1.0
        return now[0]

    marks: list[tuple[str, float]] = []
    real = ProcVerifier._members

    def members(self: ProcVerifier, budget: Any) -> Any:
        marks.append(("walk", now[0]))
        try:
            return real(self, budget)
        finally:
            marks.append(("walked", now[0]))

    monkeypatch.setattr(ProcVerifier, "_members", members)
    if phase == "freeze":
        budget = 5.0
    else:
        assert verifier(tmp_path, clock=clock, sleep=lambda s: None, budget=1e9)(32) == ""
        (_, start), (_, walked) = marks
        assert walked - start > 20 and now[0] - walked > 20          # both phases tick every entry
        budget = (start + walked) / 2 if phase == "subdirs" else (walked + now[0]) / 2
        marks.clear()
        now[0] = 0.0
    v = verifier(tmp_path, clock=clock, sleep=lambda s: None, budget=budget)
    if phase == "freeze":
        assert v(32) == "the workload could not be frozen" and marks == []
    else:
        assert v(32) == "the workload scan did not finish"
        assert len(marks) == 2 and (marks[1][1] < now[0] - 1) == (phase == "threads")
    assert freeze_state(tmp_path) == "0"


def test_a_scan_past_its_entry_limit_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe_tree(tmp_path)
    monkeypatch.setattr(channel_module, "SCAN_LIMIT", 4)
    assert verifier(tmp_path)(32) == "the workload scan did not finish"


def test_verifications_never_overlap_their_freezes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe_tree(tmp_path)
    v = verifier(tmp_path)
    inside, most = [0], [0]
    real = ProcVerifier._freeze

    def freeze(self: ProcVerifier, budget: Any) -> None:
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        real(self, budget)

    def thaw(self: ProcVerifier) -> bool:
        time.sleep(0.01)                            # widen the window another worker could overlap in
        inside[0] -= 1
        return real_thaw(self)

    real_thaw = ProcVerifier._thaw
    monkeypatch.setattr(ProcVerifier, "_freeze", freeze)
    monkeypatch.setattr(ProcVerifier, "_thaw", thaw)
    workers = [threading.Thread(target=v, args=(32,)) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert most[0] == 1 and freeze_state(tmp_path) == "0"


def test_start_time_reads_the_stat_field(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    assert channel_module.start_time(tmp_path, 31) == 31
    assert channel_module.start_time(Path("/proc"), os.getpid()) > 0


@pytest.mark.parametrize("cgroup, ok", [
    (f"0::{WORKLOAD_CG}\n", True),
    (f"0::{WORKLOAD_CG}\n1:name=systemd:/x\n", False),     # a cgroup v1 hierarchy too
    ("1:name=systemd:/x\n", False),
    (f"0::{SELF_CG}\n", False),                             # the scanner's own
    ("0::/user.slice\n", False),                            # an ancestor of it
    ("0::/\n", False),
    ("0::/user.slice/../x\n", False),
])
def test_the_workload_cgroup_is_pinned_only_when_safe(tmp_path: Path, cgroup: str, ok: bool) -> None:
    (tmp_path / "7").mkdir()
    (tmp_path / "7" / "cgroup").write_text(cgroup)
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "cgroup").write_text(f"0::{SELF_CG}\n")
    if ok:
        assert channel_module.workload_cgroup(tmp_path, 7) == WORKLOAD_CG
    else:
        with pytest.raises(ValueError, match="cgroup"):
            channel_module.workload_cgroup(tmp_path, 7)
