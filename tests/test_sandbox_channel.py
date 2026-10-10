import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import short_dir, wait_for

from heterodyne.sandbox import channel as channel_module
from heterodyne.sandbox.channel import ACK, MAX_LINE, MAX_PEERS, ProbeChannel, ProcVerifier
from heterodyne.sandbox.openshell_selftest import PROBE_ARGV, PROBE_EXE

NETNS = "net:[4026531999]"
CLI = "/opt/codex/bin/codex"
ALLOWED = frozenset({"HOME", "PATH"})


def fake_proc(root: Path, pid: int, *, exe: str, argv: tuple[str, ...], ppid: int, netns: str = NETNS,
              env: tuple[str, ...] = ("HOME=/s/home",), tracer: int = 0) -> None:
    d = root / str(pid)
    (d / "ns").mkdir(parents=True)
    (d / "exe").symlink_to(exe)
    (d / "ns" / "net").symlink_to(netns)
    (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
    (d / "environ").write_bytes(b"".join(e.encode() + b"\0" for e in env))
    (d / "status").write_text(f"Name:\tpython3\nPPid:\t{ppid}\nTracerPid:\t{tracer}\nUid:\t1000\n")


def probe_tree(root: Path, **probe: object) -> None:
    """pid 30: the CLI (in the workload netns), 31: a shell, 32: the probe."""
    fake_proc(root, 30, exe=CLI, argv=("codex",), ppid=1)
    fake_proc(root, 31, exe="/usr/bin/bash", argv=("bash", "-c", "..."), ppid=30)
    fields = {"exe": PROBE_EXE, "argv": PROBE_ARGV, "ppid": 31, **probe}
    fake_proc(root, 32, **fields)  # type: ignore[arg-type]


def verifier(root: Path) -> ProcVerifier:
    return ProcVerifier(NETNS, CLI, ALLOWED, proc=root)


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
    fake_proc(tmp_path, 30, exe=CLI, argv=("codex",), ppid=1, netns="net:[1]")   # the CLI, but outside
    fake_proc(tmp_path, 31, exe="/usr/bin/bash", argv=("bash",), ppid=30)
    fake_proc(tmp_path, 32, exe=PROBE_EXE, argv=PROBE_ARGV, ppid=31)
    assert "no /opt/codex/bin/codex ancestor" in verifier(tmp_path)(32)


def test_a_vanished_pid_is_rejected(tmp_path: Path) -> None:
    assert "unreadable" in verifier(tmp_path)(99)


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    with short_dir() as d:
        yield d


def own_pidfd(pid: int) -> int:
    """A live pidfd for a fake PID: this test process's own."""
    return os.pidfd_open(os.getpid())


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
    ch = ProbeChannel(path, lambda pid: verdicts.get(pid, "not the probe"), peer_pid=lambda s: pids.pop(0),
                      pidfd_open=own_pidfd, **kw)  # type: ignore[arg-type]
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

    ch = ProbeChannel(sock_dir / "p.sock", verify, peer_pid=lambda s: 32, pidfd_open=own_pidfd)
    ch.start()
    assert send(sock_dir / "p.sock", *all_pass(EXPECTED)) == b""     # no ACK for a changed probe
    assert finish(ch) == "the probe changed before it finished"


def test_no_run_at_all_fails(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [], {})
    assert finish(ch) == "no verified probe run"


def test_a_peer_pid_failure_counts_as_rejected(sock_dir: Path) -> None:
    def broken(s: socket.socket) -> int:
        raise OSError("no peer credentials")

    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer_pid=broken, pidfd_open=own_pidfd)
    ch.start()
    send(sock_dir / "p.sock", *all_pass(EXPECTED))
    assert finish(ch) == "a peer that is not the probe connected"


def test_a_pidfd_failure_counts_as_rejected(sock_dir: Path) -> None:
    def gone(pid: int) -> int:
        raise ProcessLookupError(pid)

    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer_pid=lambda s: 32, pidfd_open=gone)
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
        ch = ProbeChannel(sock_dir / "p.sock", verify, peer_pid=lambda s: original.pid)
        ch.start()
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        expected = ("a peer that is not the probe connected" if when == "at-accept"
                    else "the probe changed before it finished")
        assert finish(ch) == expected
    finally:
        original.kill()
        original.wait()


def test_a_peer_already_gone_is_rejected(sock_dir: Path) -> None:
    gone = _sleeper()
    pidfd = os.pidfd_open(gone.pid)
    gone.kill()
    gone.wait()
    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer_pid=lambda s: gone.pid,
                      pidfd_open=lambda pid: os.dup(pidfd))
    ch.start()
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        assert finish(ch) == "a peer that is not the probe connected"
        assert ch.rejected == ["the peer exited"]
    finally:
        os.close(pidfd)


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
    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer_pid=lambda s: 32, pidfd_open=own_pidfd)
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
    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", pidfd_open=own_pidfd)
    with pytest.raises(OSError, match="injected"):
        ch.start()
    monkeypatch.undo()
    assert not (sock_dir / "p.sock").exists()
    ch.close()
