import json
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import short_dir, wait_for

from heterodyne.sandbox.channel import ProbeChannel, ProcVerifier
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


def send(path: Path, *lines: dict[str, object]) -> None:
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(path))
        s.sendall(b"".join((json.dumps(m) + "\n").encode() for m in lines))


def all_pass(expected: tuple[str, ...]) -> list[dict[str, object]]:
    return [*({"check": c, "ok": True, "evidence": "x"} for c in expected), {"done": 0}]


EXPECTED = ("a", "b")


def channel(path: Path, pids: list[int], verdicts: dict[int, str]) -> ProbeChannel:
    ch = ProbeChannel(path, lambda pid: verdicts.get(pid, "not the probe"), peer_pid=lambda s: pids.pop(0))
    ch.start()
    return ch


def test_one_verified_run_passes(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(ch.done)
        assert ch.verdict(EXPECTED) == ""
    finally:
        ch.close()
    assert not (sock_dir / "p.sock").exists()


def test_a_forger_after_the_probe_fails_the_gate(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [32, 40], {32: ""})
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(ch.done)
        send(sock_dir / "p.sock", *all_pass(EXPECTED))          # pid 40: not the probe
        wait_for(lambda: ch.verdict(EXPECTED) != "")
        assert ch.verdict(EXPECTED) == "a peer that is not the probe connected"
    finally:
        ch.close()


@pytest.mark.parametrize("lines, reason", [
    ([{"check": "a", "ok": True, "evidence": ""}, {"done": 0}], "missing checks: b"),
    ([{"check": "a", "ok": True, "evidence": ""}, {"check": "b", "ok": False, "evidence": ""}, {"done": 1}],
     "failed checks: b"),
    ([{"check": "a", "ok": True, "evidence": ""}, {"check": "b", "ok": True, "evidence": ""}, {"done": 1}],
     "the probe did not finish cleanly"),
    (["not json"], "malformed result"),
])
def test_bad_results_fail(sock_dir: Path, lines: list[object], reason: str) -> None:
    ch = channel(sock_dir / "p.sock", [32], {32: ""})
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(sock_dir / "p.sock"))
            body = b"".join((ln if isinstance(ln, str) else json.dumps(ln)).encode() + b"\n" for ln in lines)
            s.sendall(body)
        wait_for(lambda: ch.done() or ch.verdict(EXPECTED) == "malformed result")
        assert ch.verdict(EXPECTED) == reason
    finally:
        ch.close()


def test_a_peer_that_changes_before_done_fails(sock_dir: Path) -> None:
    calls: list[int] = []

    def verify(pid: int) -> str:
        calls.append(pid)
        return "" if len(calls) == 1 else "traced"

    ch = ProbeChannel(sock_dir / "p.sock", verify, peer_pid=lambda s: 32)
    ch.start()
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(ch.done)
        assert ch.verdict(EXPECTED) == "the probe changed before it finished"
    finally:
        ch.close()


def test_no_run_at_all_fails(sock_dir: Path) -> None:
    ch = channel(sock_dir / "p.sock", [], {})
    try:
        assert ch.verdict(EXPECTED) == "no verified probe run"
    finally:
        ch.close()


def test_a_peer_pid_failure_counts_as_rejected(sock_dir: Path) -> None:
    def broken(s: socket.socket) -> int:
        raise OSError("no peer credentials")

    ch = ProbeChannel(sock_dir / "p.sock", lambda pid: "", peer_pid=broken)
    ch.start()
    try:
        send(sock_dir / "p.sock", *all_pass(EXPECTED))
        wait_for(lambda: ch.verdict(EXPECTED) == "a peer that is not the probe connected")
    finally:
        ch.close()
