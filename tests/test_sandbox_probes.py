"""The probes' own logic, offline (ADR 0001 r15 §11's negative controls).

Each test runs the real resources/probes.py against a scripted sandbox world: every file it opens is
mapped under a temporary root, and the broker's answers, the session sockets, the environment and curl
are fakes. A probe that passed a world which doesn't enforce a rule fails one of these tests.
"""
import ast
import ctypes
import errno
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Any

import pytest
from sandbox_env import wait_for

from heterodyne.agents.codex import Codex
from heterodyne.sandbox.openshell_selftest import AGENT_CHECKS, EXEC_CHECKS, OPENSHELL_ENV, env_allowed
from heterodyne.sandbox.spec import LAUNCHER_ENV

PROBES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources" / "probes.py"
CODE = compile(PROBES.read_text(), str(PROBES), "exec")
RUN = "/run/hz"
CGROUPFS = "/sys/fs/cgroup"
CHOSEN = "/sandbox/home/.codex/auth.json"
LOGIN = '{"fake": "not-a-token"}'
REAL_HOME_CANARY = "/outside/real-home/.heterodyne-canary"
OA_CANARY = "/outside/sessions/hz0123456789ab/oa-canary"
OTHER_LOGIN = "/outside/accounts/work/auth.json"
WSD_SOCKET = "/outside/state/wsd/ctl.sock"
ALLOWED, DENIED, MODEL = "probe.example.org", "denied.example.org", "chatgpt.com"
SYNTHETIC = "198.18.0.7"                       # OpenShell's policy DNS answers from 198.18.0.0/15
PROXY_CA = "*  issuer: O=OpenShell; CN=OpenShell Sandbox CA"
REFUSED = (7, "http=000\n", "* connect to 198.18.0.7 port 443 failed: Permission denied")
REACHED = (0, "http=200\n", PROXY_CA)        # curl -w's record ends the output
FORBIDDEN = '{"ok": false, "error": "forbidden"}'
STATUS = "Name:\tpython3\nSeccomp:\t2\nNoNewPrivs:\t1\nCapEff:\t0000000000000000\n"
NET_DEV = "Inter-|   Receive\n face |bytes    packets\n    lo: 0 0\n"
Results = tuple[int, dict[str, bool]]


LIBC_CALLS = ("ptrace", "process_vm_writev", "pidfd_getfd", "unshare")   # section 11's, all filtered


@dataclass
class World:
    """A sandbox as the probe sees it. The defaults enforce every rule."""
    root: Path
    path: str = "exec"
    env: dict[str, str] = field(default_factory=dict[str, str])
    readonly: tuple[str, ...] = (RUN, CHOSEN, CGROUPFS)
    denied: dict[str, int] = field(default_factory=dict[str, int])   # paths whose open gives this errno
    inet: int = errno.EACCES               # the broker's answer to an INET connect()
    udp_send: int = errno.EDESTADDRREQ     # ... to a non-DNS UDP sendto()
    raw: int = errno.EPROTONOSUPPORT       # ... to a raw or ICMP socket(); 0 creates it
    calls: dict[str, int] = field(               # each libc call's errno; 0 succeeds
        default_factory=lambda: dict.fromkeys(LIBC_CALLS, errno.EPERM))
    last_errno: int = 0
    cgroupfs: str = "ro"                   # how /sys/fs/cgroup is mounted: "ro", "rw", or an errno's name
    resolve: str = SYNTHETIC
    session_socket: bool = True
    replies: dict[str, str] = field(
        default_factory=lambda: {"approve": FORBIDDEN, "hook_event": '{"ok": true}'})
    curl: dict[str, tuple[int, str, str]] = field(default_factory=dict[str, tuple[int, str, str]])
    reported: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    runs: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])    # each curl Popen's kwargs
    spawned: list[list[str]] = field(default_factory=list[list[str]])          # every other Popen's argv
    ends: dict[int, "FakeEnd"] = field(default_factory=dict[int, "FakeEnd"])    # capture ends, by fd
    acks: list[int] = field(default_factory=list[int])      # len(reported) at each wait for the ACK

    def at(self, path: str | os.PathLike[str]) -> Path:
        return self.root / str(path).lstrip("/")

    def put(self, path: str, text: str) -> None:
        self.at(path).parent.mkdir(parents=True, exist_ok=True)
        self.at(path).write_text(text)

    def open(self, file: str, mode: str = "r") -> IO[Any]:
        if file in self.denied:
            raise OSError(self.denied[file], os.strerror(self.denied[file]), file)
        under = any(file == r or file.startswith(r + "/") for r in self.readonly)
        if under and any(c in mode for c in "wa+"):
            raise OSError(errno.EROFS, os.strerror(errno.EROFS), file)
        return self.at(file).open(mode)

    def answer(self, host: str) -> tuple[int, str, str]:
        if host in self.curl:
            return self.curl[host]
        if host == ALLOWED or (host == MODEL and self.path == "agent"):
            return REACHED
        return REFUSED

    def config(self) -> dict[str, Any]:
        """The probes' input, shaped as Task 8's probe_config builds it."""
        return {"path": self.path, "real_home_canary": REAL_HOME_CANARY, "oa_canary": OA_CANARY,
                "other_accounts": [{"account": "work", "class": "present", "paths": [OTHER_LOGIN]}],
                "chosen": [{"path": CHOSEN, "sha256": hashlib.sha256(LOGIN.encode()).hexdigest()}],
                "allowed": ALLOWED, "denied": DENIED, "model_host": MODEL, "wsd_socket": WSD_SOCKET,
                "env_allowed": sorted(env_allowed(Codex(), self.path)),
                "env_user": sorted(LAUNCHER_ENV | OPENSHELL_ENV)}


def fake_socket(world: World) -> type:
    class FakeSocket:
        def __init__(self, family: int = socket.AF_INET, kind: int = socket.SOCK_STREAM,
                     proto: int = 0) -> None:
            special = kind == socket.SOCK_RAW or proto == socket.IPPROTO_ICMP
            if world.raw and family != socket.AF_UNIX and special:
                raise OSError(world.raw, os.strerror(world.raw))
            self.family, self.peer, self.sent = family, "", b""

        def settimeout(self, seconds: float) -> None:
            pass

        def connect(self, addr: object) -> None:
            if self.family != socket.AF_UNIX:
                raise OSError(world.inet, os.strerror(world.inet))
            if not world.session_socket or addr not in (f"{RUN}/s.sock", f"{RUN}/p.sock"):
                raise OSError(errno.ECONNREFUSED, os.strerror(errno.ECONNREFUSED))
            self.peer = str(addr)

        def sendto(self, data: bytes, addr: object) -> None:
            raise OSError(world.udp_send, os.strerror(world.udp_send))

        def sendall(self, data: bytes) -> None:
            self.sent += data
            if self.peer.endswith("p.sock"):
                world.reported += [json.loads(ln) for ln in data.splitlines()]

        def recv(self, n: int) -> bytes:
            if self.peer.endswith("p.sock"):
                world.acks.append(len(world.reported))     # the probe waits for the host's ACK
            return b"ack\n"

        def makefile(self) -> io.StringIO:
            return io.StringIO(world.replies[json.loads(self.sent)["type"]] + "\n")

        def close(self) -> None:
            pass

    return FakeSocket


class FakeEnd:
    """One end of the probe's capture socket pair: what the fake program writes, the probe reads."""

    def __init__(self, data: list[bytes], fd: int) -> None:
        self.data, self.fd = data, fd

    def fileno(self) -> int:
        return self.fd

    def __enter__(self) -> "FakeEnd":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def write(self, text: str) -> None:
        self.data.append(text.encode())

    def settimeout(self, seconds: float) -> None:
        assert seconds > 0

    def recv(self, n: int) -> bytes:
        return self.data.pop(0) if self.data else b""


def fake_socketpair(world: World) -> Callable[[], tuple[FakeEnd, FakeEnd]]:
    def socketpair() -> tuple[FakeEnd, FakeEnd]:
        data: list[bytes] = []
        n = 1000 + 2 * len(world.ends)
        ours, theirs = FakeEnd(data, n), FakeEnd(data, n + 1)
        world.ends[theirs.fd] = theirs
        return ours, theirs
    return socketpair


CHILD = 4242


def fake_statvfs(world: World) -> Callable[[str], SimpleNamespace]:
    def statvfs(path: str) -> SimpleNamespace:
        if not world.at(path).exists():
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        if world.cgroupfs not in ("ro", "rw"):
            code = getattr(errno, world.cgroupfs)
            raise OSError(code, os.strerror(code), path)
        return SimpleNamespace(f_flag=os.ST_RDONLY if world.cgroupfs == "ro" else 0)
    return statvfs


class FakeChild:
    pid = CHILD
    returncode = None

    def __init__(self, argv: list[str]) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self) -> int:
        return -9


@dataclass
class FakeCall:
    world: World
    name: str
    restype: object = None
    argtypes: object = None

    def __call__(self, *args: object) -> int:
        self.world.last_errno = self.world.calls[self.name]
        return -1 if self.world.last_errno else 0


class FakeLibc:
    def __init__(self, world: World) -> None:
        self.world = world
        self.made: dict[str, FakeCall] = {}

    def __getattr__(self, name: str) -> FakeCall:
        if name not in LIBC_CALLS:
            raise AttributeError(name)
        return self.made.setdefault(name, FakeCall(self.world, name))


@pytest.fixture
def world(tmp_path: Path) -> World:
    w = World(tmp_path, env={"HOME": "/sandbox/home", "PWD": "/sandbox/work", "OPENSHELL_SANDBOX": "1",
                             "OPENSHELL_USER_ENVIRONMENT": json.dumps({"OPENSHELL_SANDBOX": "1"})})
    w.put(f"{RUN}/token", "fake-session-token\n")
    w.put(CHOSEN, LOGIN)
    w.put("/proc/net/dev", NET_DEV)
    w.put("/proc/net/route", "Iface\tDestination\tGateway\n")
    w.put("/proc/net/ipv6_route", "00000000000000000000000000000001 80 0 0 0 0 0 0 0 lo\n")
    w.put("/proc/self/status", STATUS)
    w.put(f"/proc/{CHILD}/mem", "")                     # Landlock: /proc is read-only
    w.denied[f"/proc/{CHILD}/mem"] = errno.EACCES
    w.put("/proc/self/comm", "python3\n")
    w.denied["/proc/self/comm"] = errno.EACCES
    w.put(f"{CGROUPFS}/cgroup.freeze", "0\n")              # the container's own, mounted read-only
    w.put(f"{CGROUPFS}/cgroup.procs", "1\n")
    return w


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Callable[[World], Results]:
    def go(world: World) -> Results:
        cfg = json.dumps(world.config())
        if world.path == "agent":
            world.put(f"{RUN}/agent-probe.json", cfg)
        listdir, lstat = os.listdir, os.lstat
        monkeypatch.setattr(sys, "argv", ["probes.py", "--agent"] if world.path == "agent" else ["probes.py"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(cfg))
        monkeypatch.setattr(os, "environ", dict(world.env))
        monkeypatch.setattr(os, "listdir", lambda p: listdir(world.at(p)))
        monkeypatch.setattr(os, "lstat", lambda p: lstat(world.at(p)))
        monkeypatch.setattr(os, "statvfs", fake_statvfs(world))
        monkeypatch.setattr(socket, "socket", fake_socket(world))
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (world.resolve, port))])

        def popen(argv: list[str], **kwargs: Any) -> Any:
            if argv[0] != "/usr/bin/curl":
                world.spawned.append(argv)
                return FakeChild(argv)
            world.runs.append({"argv": argv, **kwargs})
            rc, out, err = world.answer(argv[-1].removeprefix("https://").rstrip("/"))
            end = world.ends.get(kwargs.get("stdout", -1))
            if end is not None:
                end.write(f"{err}\n{out}")       # out carries its own terminator, or doesn't
            return SimpleNamespace(pid=CHILD + 1, returncode=rc, poll=lambda: rc,
                                   wait=lambda timeout=None: rc, kill=lambda: None)

        def run(argv: list[str], **kwargs: Any) -> None:
            raise AssertionError(f"the probe ran {argv} outside captured")

        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr(socket, "socketpair", fake_socketpair(world))
        monkeypatch.setattr(subprocess, "Popen", popen)
        monkeypatch.setattr(ctypes, "CDLL", lambda name, use_errno=False: FakeLibc(world))
        monkeypatch.setattr(ctypes, "get_errno", lambda: world.last_errno)
        try:
            exec(CODE, {"__name__": "__main__", "open": world.open})  # noqa: S102 - the probe script itself
            rc = -1                                    # the probe always ends with sys.exit
        except SystemExit as stop:
            rc = int(stop.code or 0)
        monkeypatch.undo()
        lines = capsys.readouterr().out.splitlines()
        assert lines[-1] == f"DONE {rc}"               # the probes' explicit completion, last
        verdicts = [ln.split()[:2] for ln in lines if ln.startswith(("PASS ", "FAIL "))]
        return rc, {name: verdict == "PASS" for verdict, name in verdicts}
    return go


def test_an_enforcing_world_passes_every_check(world: World, run: Callable[[World], Results]) -> None:
    rc, results = run(world)
    assert rc == 0 and set(EXEC_CHECKS) <= results.keys() and all(results.values())


def test_the_agent_path_allows_the_cli_tool_env_and_reports_every_result(
        world: World, run: Callable[[World], Results]) -> None:
    world.path = "agent"
    world.env["CODEX_THREAD_ID"] = "fake-thread"
    rc, results = run(world)
    assert rc == 0 and set(AGENT_CHECKS) <= results.keys() and all(results.values())
    assert sorted(r["check"] for r in world.reported if "check" in r) == sorted(AGENT_CHECKS)
    assert results.keys() == set(AGENT_CHECKS)
    assert world.reported[-1] == {"done": 0}
    assert world.acks == [len(world.reported)]           # done, then one wait for the ACK, then the exit


def _env(**extra: str) -> Callable[[World], None]:
    return lambda w: w.env.update(extra)


def _replace(path: str, old: str, new: str) -> Callable[[World], None]:
    return lambda w: w.put(path, w.at(path).read_text().replace(old, new))


def _cgroupfs_rw(*, writable: bool) -> Callable[[World], None]:
    """A cgroupfs mounted read-write: its controls writable, or each refused with EACCES."""
    def go(w: World) -> None:
        w.cgroupfs = "rw"
        if writable:
            w.readonly = (RUN, CHOSEN)
        else:
            w.denied.update({f"{CGROUPFS}/{n}": errno.EACCES for n in ("cgroup.freeze", "cgroup.procs")})
    return go


def _call(name: str, code: int) -> Callable[[World], None]:
    return lambda w: w.calls.update({name: code})


def _deny(path: str, code: int | None) -> Callable[[World], None]:
    """`path`'s open answers `code`, or succeeds when None."""
    def go(w: World) -> None:
        if code is None:
            del w.denied[path]
        else:
            w.denied[path] = code
    return go


NEGATIVE_CONTROLS: list[tuple[str, Callable[[World], None], set[str]]] = [
    ("a mounted real-home canary", lambda w: w.put(REAL_HOME_CANARY, "x"), {"real-home-canary-unreadable"}),
    ("a mounted other-accounts canary", lambda w: w.put(OA_CANARY, "x"), {"other-accounts"}),
    ("another account's login reachable", lambda w: w.put(OTHER_LOGIN, LOGIN), {"other-accounts"}),
    ("a wrong login hash", lambda w: w.put(CHOSEN, '{"fake": "another"}'), {"other-accounts"}),
    ("a writable login", lambda w: setattr(w, "readonly", (RUN, CGROUPFS)), {"other-accounts"}),
    ("a leaked variable", _env(GITHUB_TOKEN="fake"), {"host-env-not-inherited"}),  # noqa: S106
    ("a leaked user-environment variable",
     _env(OPENSHELL_USER_ENVIRONMENT=json.dumps({"ANTHROPIC_API_KEY": "fake"})), {"host-env-not-inherited"}),
    ("a tool-only variable on the exec path", _env(CODEX_THREAD_ID="fake-thread"),
     {"host-env-not-inherited"}),
    ("a dead session socket", lambda w: setattr(w, "session_socket", False),
     {"control-op-rejected", "hook-event-accepted"}),
    ("a control operation accepted", lambda w: w.replies.update(approve='{"ok": true}'),
     {"control-op-rejected"}),
    ("a failed outer fence: an interface", _replace("/proc/net/dev", "lo: 0 0", "lo: 0 0\n  eth0: 0 0"),
     {"direct-network-blocked"}),
    ("a failed outer fence: a route", _replace("/proc/net/route", "Gateway\n", "Gateway\neth0\t0\t1\n"),
     {"direct-network-blocked"}),
    ("no seccomp broker", _replace("/proc/self/status", "Seccomp:\t2", "Seccomp:\t0"),
     {"direct-network-blocked"}),
    ("a capability", _replace("/proc/self/status", "CapEff:\t0000000000000000",
                              "CapEff:\t0000000000002000"),
     {"direct-network-blocked"}),
    ("the kernel fence's answer instead of the broker's", lambda w: setattr(w, "inet", errno.ENETUNREACH),
     {"direct-network-blocked", "non-allowlisted-host-blocked"}),
    ("a raw socket created", lambda w: setattr(w, "raw", 0), {"direct-network-blocked"}),
    ("a UDP send allowed", lambda w: setattr(w, "udp_send", errno.ENETUNREACH), {"direct-network-blocked"}),
    ("the denied host resolved for real", lambda w: setattr(w, "resolve", "192.0.2.7"),
     {"non-allowlisted-host-blocked"}),
    ("the denied host reached", lambda w: w.curl.update({DENIED: REACHED}), {"non-allowlisted-host-blocked"}),
    ("the control reached around the proxy",
     lambda w: w.curl.update({ALLOWED: (0, "http=200\n", "*  issuer: CN=R11")}),
     {"allowlisted-host-reachable"}),
    ("the control reached with no HTTP status", lambda w: w.curl.update({ALLOWED: (0, "", PROXY_CA)}),
     {"allowlisted-host-reachable"}),
    ("the model host reached from the exec path", lambda w: w.curl.update({MODEL: REACHED}),
     {"model-host-exec-path"}),
    ("wsd's socket reachable", lambda w: w.put(WSD_SOCKET, ""), {"wsd-socket-absent"}),
    ("OpenShell's key readable", lambda w: w.put("/.openshell/channel/sandbox/server.key", "fake"),
     {"openshell-control-material-unreadable"}),
    ("a readable secrets directory", lambda w: w.put("/run/secrets/actual.jwt", "fake"),
     {"openshell-control-material-unreadable"}),
    ("the probe's child attachable", _call("ptrace", 0), {"probe-tamper-denied"}),
    ("the attach refused only because the child is gone", _call("ptrace", errno.ESRCH),
     {"probe-tamper-denied"}),
    ("ptrace missing, not denied by OpenShell's filter", _call("ptrace", errno.ENOSYS),
     {"probe-tamper-denied"}),
    ("process_vm_writev unfiltered", _call("process_vm_writev", 0), {"probe-tamper-denied"}),
    ("pidfd_getfd unfiltered", _call("pidfd_getfd", errno.EBADF), {"probe-tamper-denied"}),
    ("unshare unfiltered", _call("unshare", 0), {"probe-tamper-denied"}),
    ("the child's memory writable", _deny(f"/proc/{CHILD}/mem", None), {"probe-tamper-denied"}),
    ("the child's memory refused by something but Landlock", _deny(f"/proc/{CHILD}/mem", errno.EPERM),
     {"probe-tamper-denied"}),
    ("/proc writable", _deny("/proc/self/comm", None), {"proc-readonly"}),
    ("/proc/self/comm refused by something but Landlock", _deny("/proc/self/comm", errno.EPERM),
     {"proc-readonly"}),
    ("the run directory writable", lambda w: setattr(w, "readonly", (CHOSEN, CGROUPFS)),
     {"probe-files-readonly"}),
    ("the cgroupfs mounted read-write", _cgroupfs_rw(writable=True),
     {"cgroupfs-readonly"}),
    ("the cgroupfs read-write but its controls denied", _cgroupfs_rw(writable=False),
     {"cgroupfs-readonly"}),
    ("a freeze control writable on a read-only mount", lambda w: setattr(w, "readonly", (RUN, CHOSEN)),
     {"cgroupfs-readonly"}),
    ("a control refused for another reason",
     lambda w: w.denied.update({f"{CGROUPFS}/cgroup.procs": errno.EPERM}),
     {"cgroupfs-readonly"}),
    ("the cgroupfs mount unreadable", lambda w: setattr(w, "cgroupfs", "EACCES"), {"cgroupfs-readonly"}),
]


@pytest.mark.parametrize("break_it, failing", [(f, c) for _, f, c in NEGATIVE_CONTROLS],
                         ids=[name for name, _, _ in NEGATIVE_CONTROLS])
def test_each_broken_rule_fails_its_own_check_only(
        world: World, run: Callable[[World], Results], break_it: Callable[[World], None],
        failing: set[str]) -> None:
    break_it(world)
    rc, results = run(world)
    assert rc == 1
    assert {name for name, ok in results.items() if not ok} == failing


def test_an_absent_cgroupfs_passes(world: World, run: Callable[[World], Results]) -> None:
    for name in ("cgroup.freeze", "cgroup.procs"):
        world.at(f"{CGROUPFS}/{name}").unlink()
    world.at(CGROUPFS).rmdir()
    rc, results = run(world)
    assert rc == 0 and results["cgroupfs-readonly"]
    assert not world.at(f"{CGROUPFS}/cgroup.freeze").exists()     # r+: nothing is created


def test_every_program_the_probe_runs_reports_on_a_socket(world: World,
                                                          run: Callable[[World], Results]) -> None:
    """D23 (Amendment 1): curl, by its path and with no curlrc, its output on the capture socket pair and no
    input."""
    rc, _ = run(world)
    assert rc == 0 and len(world.runs) == 3
    assert world.spawned == [[sys.executable, "-I", "-c", "import time; time.sleep(60)"]]   # section 11's
    for r in world.runs:
        assert r["argv"][:2] == ["/usr/bin/curl", "-q"], r          # -q first: no curlrc is read
        assert r["stdout"] in world.ends and r["stderr"] == r["stdout"], r
        assert r["stdin"] == subprocess.DEVNULL and "capture_output" not in r, r


BAD_STATUSES = {
    "missing": "",
    "malformed": "http=2x0\n",
    "zero": "http=000\n",
    "unterminated": "http=200",
    "partial": "http=2\n",
    "a short zero": "http=0\n",
    "too long": "http=2000\n",
    "not ASCII digits": "http=\uff12\uff10\uff10\n",
    "a zero after it": "http=200\nhttp=000\n",
    "a zero before it": "http=000\nhttp=200\n",
    "duplicated": "http=200\nhttp=200\n",
    "not the last line": "http=200\n* trailing\n",
}


@pytest.mark.parametrize("status", BAD_STATUSES.values(), ids=BAD_STATUSES.keys())
@pytest.mark.parametrize("target", ["allowlisted", "model host"])
def test_a_reach_needs_one_complete_nonzero_http_status_last(
        world: World, run: Callable[[World], Results], status: str, target: str) -> None:
    """Task 9C: reached through the proxy, but curl's write-out isn't exactly one terminal http=DDD record
    with a nonzero code. The model host's case is the agent path's (the exec path must be refused)."""
    world.path = "agent"
    world.env["CODEX_THREAD_ID"] = "fake-thread"
    world.curl[ALLOWED if target == "allowlisted" else MODEL] = (0, status, PROXY_CA)
    rc, results = run(world)
    assert rc == 1
    assert {name for name, ok in results.items() if not ok} == {
        "allowlisted-host-reachable" if target == "allowlisted" else "model-host-agent-path"}


@pytest.mark.parametrize("status", ["http=200\n", "http=404\n", "http=301\n"])
def test_any_complete_nonzero_status_counts_as_reached(world: World, run: Callable[[World], Results],
                                                       status: str) -> None:
    world.curl[ALLOWED] = (0, status, PROXY_CA)
    rc, results = run(world)
    assert rc == 0 and all(results.values())


def probe_function(name: str) -> Callable[..., Any]:
    """One function from the probe script, defined on its own: the script runs its checks when loaded."""
    tree = ast.parse(PROBES.read_text())
    [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    scope: dict[str, Any] = {"socket": socket, "subprocess": subprocess, "time": time, "CAPTURE_SECONDS": 30,
                             "CAPTURE_BYTES": 1 << 20, "TIMED_OUT": "timeout"}
    exec(compile(ast.Module([fn], []), str(PROBES), "exec"), scope)  # noqa: S102 - the probe's own code
    return scope[name]


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs Linux's /proc")
def test_another_process_cannot_reopen_a_captured_output(tmp_path: Path) -> None:
    """The reopen through /proc/<pid>/fd that a pipe allows is refused for the capture socket (ENXIO). The
    same reopen of a pipe succeeds, so this test can tell the two apart."""
    captured = probe_function("captured")
    pid, go = tmp_path / "pid", tmp_path / "go"
    os.mkfifo(go)
    child = [sys.executable, "-I", "-c", f"import os; open({str(pid)!r}, 'w').write(str(os.getpid())); "
             f"print('from-the-child', flush=True); open({str(go)!r}).read()"]
    result: list[tuple[int, str]] = []
    t = threading.Thread(target=lambda: result.append(captured(child)))
    t.start()
    try:
        wait_for(lambda: pid.exists() and pid.read_text() != "")
        with pytest.raises(OSError) as refused:
            os.close(os.open(f"/proc/{pid.read_text()}/fd/1", os.O_WRONLY | os.O_NONBLOCK))
    finally:
        with go.open("w"):
            pass
        t.join()
    assert refused.value.errno == errno.ENXIO
    assert result == [(0, "from-the-child\n")]
    with subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.stdin.read()"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as p:
        os.close(os.open(f"/proc/{p.pid}/fd/1", os.O_WRONLY | os.O_NONBLOCK))   # a pipe: reopened
        p.communicate(b"")


def py(code: str) -> list[str]:
    return [sys.executable, "-I", "-c", code]


def test_a_capture_larger_than_the_socket_buffer_completes_with_its_tail() -> None:
    """Codex 9C r1: the output is drained while the program runs, so one past the socket's buffer can't
    block it. What's kept is bounded, and it's the tail, where curl's write-out record is."""
    captured = probe_function("captured")
    rc, out = captured(py("import sys; sys.stdout.write('x' * (4 << 20)); sys.stderr.write('end\\n'); "
                          "sys.exit(3)"), seconds=20)
    assert rc == 3
    assert len(out) == 1 << 20 and out.endswith("xend\n")


def test_a_capture_keeps_the_exit_status_and_both_streams() -> None:
    captured = probe_function("captured")
    assert captured(py("import sys; print('out', flush=True); print('err', file=sys.stderr); sys.exit(7)")) \
        == (7, "out\nerr\n")
    assert captured(py("pass")) == (0, "")


def test_a_capture_past_its_deadline_is_killed_reaped_and_fails(tmp_path: Path) -> None:
    captured = probe_function("captured")
    pid = tmp_path / "pid"
    started = time.monotonic()
    rc, out = captured(py(f"import os, time; open({str(pid)!r}, 'w').write(str(os.getpid())); "
                          "print('partial', flush=True); time.sleep(60)"), seconds=1)
    assert rc == "timeout" and out == "partial\n"
    assert time.monotonic() - started < 10
    with pytest.raises(ChildProcessError):              # already reaped: no zombie is left
        os.waitpid(int(pid.read_text()), os.WNOHANG)


def test_a_capture_held_open_after_the_program_exits_ends_at_the_deadline(tmp_path: Path) -> None:
    """The program exits 0, but something it started keeps the socket open: the read is bounded by the
    same deadline, and the result fails, whatever the program's own exit status."""
    captured = probe_function("captured")
    grandchild = tmp_path / "grandchild"
    try:
        rc, _ = captured(py("import subprocess, sys; p = subprocess.Popen([sys.executable, '-I', '-c', "
                            "'import time; time.sleep(60)']); "
                            f"open({str(grandchild)!r}, 'w').write(str(p.pid))"), seconds=1)
        assert rc == "timeout"
    finally:
        if grandchild.exists() and grandchild.read_text():
            os.kill(int(grandchild.read_text()), 9)
