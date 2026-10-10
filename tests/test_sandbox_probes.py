"""The probes' own logic, offline (ADR 0001 r15 §11's negative controls).

Each test runs the real resources/probes.py against a scripted sandbox world: every file it opens is
mapped under a temporary root, and the broker's answers, the session sockets, the environment and curl
are fakes. A probe that passed a world which doesn't enforce a rule fails one of these tests.
"""
import errno
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import pytest

from heterodyne.agents.codex import Codex
from heterodyne.sandbox.openshell_selftest import AGENT_CHECKS, EXEC_CHECKS, OPENSHELL_ENV, env_allowed
from heterodyne.sandbox.spec import LAUNCHER_ENV

PROBES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources" / "probes.py"
CODE = compile(PROBES.read_text(), str(PROBES), "exec")
RUN = "/run/hz"
CHOSEN = "/sandbox/home/.codex/auth.json"
LOGIN = '{"fake": "not-a-token"}'
REAL_HOME_CANARY = "/outside/real-home/.heterodyne-canary"
OA_CANARY = "/outside/sessions/hz0123456789ab/oa-canary"
OTHER_LOGIN = "/outside/accounts/work/auth.json"
WSD_SOCKET = "/outside/state/wsd/ctl.sock"
ALLOWED, DENIED, MODEL = "probe.example.org", "denied.example.org", "chatgpt.com"
SYNTHETIC = "198.18.0.7"                       # OpenShell's policy DNS answers from 198.18.0.0/15
PROXY_CA = "*  issuer: O=OpenShell; CN=OpenShell Sandbox CA"
REFUSED = (7, "http=000", "* connect to 198.18.0.7 port 443 failed: Permission denied")
REACHED = (0, "http=200", PROXY_CA)
FORBIDDEN = '{"ok": false, "error": "forbidden"}'
STATUS = "Name:\tpython3\nSeccomp:\t2\nNoNewPrivs:\t1\nCapEff:\t0000000000000000\n"
NET_DEV = "Inter-|   Receive\n face |bytes    packets\n    lo: 0 0\n"
Results = tuple[int, dict[str, bool]]


@dataclass
class World:
    """A sandbox as the probe sees it. The defaults enforce every rule."""
    root: Path
    path: str = "exec"
    env: dict[str, str] = field(default_factory=dict[str, str])
    readonly: tuple[str, ...] = (RUN, CHOSEN)
    denied: dict[str, int] = field(default_factory=dict[str, int])   # paths whose open gives this errno
    inet: int = errno.EACCES               # the broker's answer to an INET connect()
    udp_send: int = errno.EDESTADDRREQ     # ... to a non-DNS UDP sendto()
    raw: int = errno.EPROTONOSUPPORT       # ... to a raw or ICMP socket(); 0 creates it
    resolve: str = SYNTHETIC
    session_socket: bool = True
    replies: dict[str, str] = field(
        default_factory=lambda: {"approve": FORBIDDEN, "hook_event": '{"ok": true}'})
    curl: dict[str, tuple[int, str, str]] = field(default_factory=dict[str, tuple[int, str, str]])
    reported: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

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

        def makefile(self) -> io.StringIO:
            return io.StringIO(world.replies[json.loads(self.sent)["type"]] + "\n")

        def close(self) -> None:
            pass

    return FakeSocket


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
        monkeypatch.setattr(socket, "socket", fake_socket(world))
        monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (world.resolve, port))])

        def curl(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            rc, out, err = world.answer(argv[-1].removeprefix("https://").rstrip("/"))
            return subprocess.CompletedProcess(argv, rc, out, err)

        monkeypatch.setattr(subprocess, "run", curl)
        try:
            exec(CODE, {"__name__": "__main__", "open": world.open})  # noqa: S102 - the probe script itself
            rc = -1                                    # the probe always ends with sys.exit
        except SystemExit as stop:
            rc = int(stop.code or 0)
        monkeypatch.undo()
        lines = capsys.readouterr().out.splitlines()
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
    assert {r["check"] for r in world.reported if "check" in r} == results.keys()
    assert world.reported[-1] == {"done": 0}


def _env(**extra: str) -> Callable[[World], None]:
    return lambda w: w.env.update(extra)


def _replace(path: str, old: str, new: str) -> Callable[[World], None]:
    return lambda w: w.put(path, w.at(path).read_text().replace(old, new))


NEGATIVE_CONTROLS: list[tuple[str, Callable[[World], None], set[str]]] = [
    ("a mounted real-home canary", lambda w: w.put(REAL_HOME_CANARY, "x"), {"real-home-canary-unreadable"}),
    ("a mounted other-accounts canary", lambda w: w.put(OA_CANARY, "x"), {"other-accounts"}),
    ("another account's login reachable", lambda w: w.put(OTHER_LOGIN, LOGIN), {"other-accounts"}),
    ("a wrong login hash", lambda w: w.put(CHOSEN, '{"fake": "another"}'), {"other-accounts"}),
    ("a writable login", lambda w: setattr(w, "readonly", (RUN,)), {"other-accounts"}),
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
     lambda w: w.curl.update({ALLOWED: (0, "http=200", "*  issuer: CN=R11")}),
     {"allowlisted-host-reachable"}),
    ("the model host reached from the exec path", lambda w: w.curl.update({MODEL: REACHED}),
     {"model-host-exec-path"}),
    ("wsd's socket reachable", lambda w: w.put(WSD_SOCKET, ""), {"wsd-socket-absent"}),
    ("OpenShell's key readable", lambda w: w.put("/.openshell/channel/sandbox/server.key", "fake"),
     {"openshell-control-material-unreadable"}),
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
