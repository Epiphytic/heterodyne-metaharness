import ast
import dataclasses
import hashlib
import json
import os
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from sandbox_env import config_env, short_dir

from heterodyne.agents.base import Cli
from heterodyne.agents.codex import Codex
from heterodyne.sandbox import openshell_selftest
from heterodyne.sandbox.backend import Backend, BackendError, ExecResult
from heterodyne.sandbox.openshell_selftest import (
    AGENT_CHECKS,
    EXEC_CHECKS,
    NS_ARGV,
    PROBE_ARGV,
    PROBE_EXE,
    PROTECTION_CHECKS,
    OpenShellSelfTest,
    classify,
    env_allowed,
    held_canaries,
    log_needles,
    other_accounts,
    probe_config,
    read_ptrace_scope,
)
from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.sandbox.spec import Bind, SandboxSpec, SessionLayout
from heterodyne.session.server import TurnState
from heterodyne.tmux import Tmux

PROBES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources" / "probes.py"
PASSING = "".join(f"PASS {c} [evidence]\n" for c in EXEC_CHECKS) + "DONE 0\n"


@dataclass
class StubBackend:
    """Answers the self-test's backend calls; records what it was asked."""
    mode: str = "none"
    rc: int = 0
    out: bytes = PASSING.encode()
    lines: list[str] = field(default_factory=list[str])
    raises: bool = False
    calls: list[tuple[list[str], bytes | None]] = field(default_factory=list[tuple[list[str], bytes | None]])
    before: Callable[[], None] = lambda: None        # runs as the network mode is asked, before the probe
    during: Callable[[], None] = lambda: None        # runs while the probe "runs"

    def network_mode(self, name: str) -> str:
        self.before()
        return self.mode

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        self.calls.append((list(argv), input))
        self.during()
        if self.raises:
            raise BackendError("openshell sandbox timed out")
        return ExecResult(self.rc, self.out, b"")

    def logs(self, name: str, since: float) -> list[str]:
        return self.lines


def context(tmp_path: Path, backend: StubBackend, others: tuple[tuple[str, Path], ...] = ()) -> ProbeContext:
    login = tmp_path / "home" / ".codex" / "auth.json"
    login.parent.mkdir(parents=True, exist_ok=True)
    login.write_text('{"fake": "not-a-token"}')
    layout = SessionLayout(tmp_path / "sessions" / "hz0123456789ab")
    layout.root.mkdir(parents=True, exist_ok=True)
    spec = SandboxSpec(name="hz0123456789abg1", workdir=tmp_path, binds=(),
                       logins=(Bind(login.resolve(), layout.home / ".codex" / "auth.json", True),),
                       read_only=(), read_write=(), egress=(), env={}, uid=1000, gid=1000)
    settings = sandbox_settings(config_env(tmp_path / "cfg"))
    (tmp_path / "real-home").mkdir(exist_ok=True)
    return ProbeContext(backend=cast(Backend, backend), tmux=cast(Tmux, None),
                        tmux_session="wsd-hz0123456789ab",
                        spec=spec, layout=layout, generation=1, adapter=Codex(),
                        cli=Cli(tmp_path / "codex", tmp_path, "0.160.0"), token="fake-session-token",  # noqa: S106
                        others=others, real_home_canary=tmp_path / "real-home" / ".heterodyne-canary",
                        wsd_socket=tmp_path / "state" / "wsd" / "ctl.sock", settings=settings,
                        turns=TurnState)


def passing_logs(ctx: ProbeContext) -> list[str]:
    return [f"[1800000000.0] [ocsf] {needle} ..." for _, needle in log_needles(ctx, "exec")]


def selftest(scope: int = 2) -> OpenShellSelfTest:
    return OpenShellSelfTest(wall=lambda: 1_800_000_000.0, sleep=lambda s: None, ptrace_scope=lambda: scope)


def test_probes_file_is_stdlib_only_and_names_every_check() -> None:
    tree = ast.parse(PROBES.read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"ctypes", "errno", "hashlib", "ipaddress", "json", "os", "socket", "stat",
                        "subprocess", "sys"}
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for check in {*AGENT_CHECKS, *EXEC_CHECKS} - {"model-host-agent-path", "model-host-exec-path"}:
        assert check in literals
    assert "/run/hz/token" in PROBES.read_text()        # the token is read inside, never sent in the input


def test_classify(tmp_path: Path) -> None:
    present = tmp_path / "a" / "auth.json"
    present.parent.mkdir()
    present.write_text("{}")
    dangling = tmp_path / "a" / "dangling.json"
    dangling.symlink_to(tmp_path / "nowhere")
    assert classify(present) == "present"
    assert classify(tmp_path / "a" / "missing.json") == "absent"
    assert classify(dangling) == "unknown"
    assert classify(tmp_path / "no-dir" / "auth.json") == "unknown"


def test_other_accounts_skip_an_alias_of_the_chosen_login(tmp_path: Path) -> None:
    chosen = tmp_path / "default" / "auth.json"
    chosen.parent.mkdir()
    chosen.write_text("{}")
    alias = tmp_path / "alias"
    alias.symlink_to(chosen.parent)
    other = tmp_path / "work" / "auth.json"
    other.parent.mkdir()
    other.write_text("{}")
    found = other_accounts((("alias", alias / "auth.json"), ("work", other)),
                           (Bind(chosen.resolve(), Path("/inside/auth.json"), True),))
    assert found == [{"account": "work", "class": "present", "paths": [str(other)]}]


def test_env_allowed_adds_the_cli_tool_env_on_the_agent_path_only() -> None:
    assert "CODEX_THREAD_ID" in env_allowed(Codex(), "agent")
    assert "CODEX_THREAD_ID" not in env_allowed(Codex(), "exec")
    assert {"HOME", "OPENSHELL_SANDBOX", "PWD"} <= env_allowed(Codex(), "exec")


def test_probe_config_holds_no_token_and_pins_the_chosen_hash(tmp_path: Path) -> None:
    ctx = context(tmp_path, StubBackend())
    with held_canaries(ctx):
        cfg = probe_config(ctx, "exec")
        assert ctx.layout.oa_canary.read_text()                     # written fresh, readable outside
    assert "fake-session-token" not in json.dumps(cfg)
    [chosen] = cfg["chosen"]
    login = ctx.spec.logins[0]
    digest = hashlib.sha256(login.source.read_bytes()).hexdigest()
    assert chosen == {"path": str(login.target), "sha256": digest}
    assert cfg["model_host"] == "chatgpt.com" and cfg["wsd_socket"] == str(ctx.wsd_socket)


def test_exec_path_passes(tmp_path: Path) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    selftest().exec_path(ctx)
    [(argv, data)] = backend.calls
    assert argv == ["python3", "-I", "/run/hz/probes.py"]
    assert data is not None and json.loads(data)["path"] == "exec"
    assert ctx.real_home_canary.read_text()


@pytest.mark.parametrize("change, check", [
    ({"mode": "bridge"}, "outer-fence-network-none"),
    ({"rc": 1, "out": b"PASS a [x]\nFAIL direct-network-blocked [y]\n"}, "direct-network-blocked"),
    ({"raises": True}, "probes-timeout"),
])
def test_exec_path_failures(tmp_path: Path, change: dict[str, object], check: str) -> None:
    backend = StubBackend(**change)  # type: ignore[arg-type]
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    with pytest.raises(SelfTestFailed, match=check):
        selftest().exec_path(ctx)


def test_a_missing_log_line_fails(tmp_path: Path) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)[1:]
    with pytest.raises(SelfTestFailed, match="proxy-logged-refusal"):
        selftest().exec_path(ctx)


def test_an_unwritable_canary_fails_the_precondition(tmp_path: Path) -> None:
    ctx = context(tmp_path, StubBackend())
    (tmp_path / "real-home").rmdir()
    (tmp_path / "real-home").write_text("not a directory")
    with pytest.raises(SelfTestFailed, match="canary-precondition"):
        selftest().exec_path(ctx)


def test_an_unwritable_other_accounts_canary_fails_the_precondition(tmp_path: Path) -> None:
    ctx = context(tmp_path, StubBackend())
    ctx.layout.oa_canary.mkdir(parents=True)
    with pytest.raises(SelfTestFailed, match="canary-precondition"):
        selftest().exec_path(ctx)


def test_files_ship_the_probes() -> None:
    assert selftest().files() == {"probes.py": PROBES.read_text()}


# r1 review: the probes' output is validated in full, the canaries are verified, classify never blocks.


def _drop(check: str) -> str:
    return PASSING.replace(f"PASS {check} [evidence]\n", "")


@pytest.mark.parametrize("out", [
    "",
    _drop("other-accounts"),
    PASSING.replace("DONE 0\n", "PASS other-accounts [evidence]\nDONE 0\n"),
    PASSING.replace("DONE 0\n", "PASS made-up-check [evidence]\nDONE 0\n"),
    PASSING.replace("DONE 0\n", "something else\nDONE 0\n"),
    PASSING.replace("DONE 0\n", ""),
    PASSING.replace("DONE 0\n", "DONE 1\n"),
    PASSING + "PASS real-home-canary-unreadable [after the end]\n",
], ids=["empty", "partial", "duplicate", "unexpected", "malformed", "no-completion", "failed-completion",
        "after-completion"])
def test_an_exit_code_of_zero_without_every_result_fails(tmp_path: Path, out: str) -> None:
    backend = StubBackend(out=out.encode())
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    with pytest.raises(SelfTestFailed, match="probes-output"):
        selftest().exec_path(ctx)


def test_a_failed_check_fails_whatever_the_exit_code(tmp_path: Path) -> None:
    out = PASSING.replace("PASS wsd-socket-absent", "FAIL wsd-socket-absent")
    backend = StubBackend(out=out.encode())
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    with pytest.raises(SelfTestFailed, match="^wsd-socket-absent$"):
        selftest().exec_path(ctx)


def test_an_unknown_failed_name_is_never_echoed(tmp_path: Path) -> None:
    backend = StubBackend(rc=1, out=b"FAIL /some/host/path [x]\n")
    ctx = context(tmp_path, backend)
    with pytest.raises(SelfTestFailed, match="^probes$"):
        selftest().exec_path(ctx)


CANARIES = {"real-home": lambda ctx: ctx.real_home_canary, "other-accounts": lambda ctx: ctx.layout.oa_canary}


def _plant(kind: str, canary: Path, tmp_path: Path) -> None:
    if kind == "link-to-dev-null":
        canary.symlink_to(os.devnull)
    elif kind == "link-to-a-file":
        (tmp_path / "elsewhere").write_text("")
        canary.symlink_to(tmp_path / "elsewhere")
    elif kind == "hard-link":
        (tmp_path / "elsewhere").write_text("")
        os.link(tmp_path / "elsewhere", canary)
    elif kind == "fifo":
        os.mkfifo(canary)


@pytest.mark.parametrize("which", sorted(CANARIES))
@pytest.mark.parametrize("kind", ["link-to-dev-null", "link-to-a-file", "hard-link", "fifo"])
def test_a_canary_that_is_not_its_own_regular_file_fails_the_precondition(
        tmp_path: Path, which: str, kind: str) -> None:
    ctx = context(tmp_path, StubBackend())
    _plant(kind, CANARIES[which](ctx), tmp_path)
    with pytest.raises(SelfTestFailed, match="canary-precondition"):
        selftest().exec_path(ctx)


@pytest.mark.parametrize("which", sorted(CANARIES))
@pytest.mark.parametrize("readback", [b"", b"0" * 16, b"x" * 17])
def test_a_canary_read_back_other_than_written_fails_the_precondition(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, readback: bytes) -> None:
    ctx = context(tmp_path, StubBackend())
    canary = CANARIES[which](ctx)
    real = os.pread

    def pread(fd: int, n: int, offset: int) -> bytes:
        same = os.fstat(fd).st_ino == canary.stat().st_ino
        return readback if same else real(fd, n, offset)

    monkeypatch.setattr(openshell_selftest.os, "pread", pread)
    with pytest.raises(SelfTestFailed, match="canary-precondition"):
        selftest().exec_path(ctx)


def test_a_canary_holds_its_fresh_value(tmp_path: Path) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    ctx.real_home_canary.write_text("stale")
    selftest().exec_path(ctx)
    first = ctx.real_home_canary.read_text()
    selftest().exec_path(ctx)
    assert first != "stale" and len(first) == 16 and ctx.real_home_canary.read_text() != first


def _within(seconds: float, fn: Callable[[], str]) -> str:
    result: list[str] = []
    t = threading.Thread(target=lambda: result.append(fn()), daemon=True)
    t.start()
    t.join(seconds)
    assert result, "classify blocked"
    return result[0]


def test_classify_never_blocks_on_a_fifo(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "auth.json")
    assert _within(5, lambda: classify(tmp_path / "auth.json")) == "unknown"


def test_classify_reads_a_bounded_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(5):
        (tmp_path / f"f{i}").write_text("")
    assert classify(tmp_path / "auth.json") == "absent"
    monkeypatch.setattr(openshell_selftest, "LIST_LIMIT", 3)
    assert classify(tmp_path / "auth.json") == "unknown"
    assert classify(tmp_path / "f4") == "present"


# r2 review: each canary's path still names the file written, before the probe runs and after it.


def _unlink(canary: Path) -> None:
    canary.unlink()


def _rename_away(canary: Path) -> None:
    canary.rename(canary.with_name("moved"))


def _replace(canary: Path) -> None:
    fresh = canary.with_name("fresh")
    fresh.write_text(canary.read_text())         # the same value, a different file
    fresh.replace(canary)


CHANGED = {"real-home": "real-home-canary-changed", "other-accounts": "other-accounts-canary-changed"}
CHANGES = {"unlink": _unlink, "rename-away": _rename_away, "replace": _replace}


@pytest.mark.parametrize("which", sorted(CANARIES))
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_canary_changed_while_the_probe_runs_fails_its_own_check(
        tmp_path: Path, which: str, change: str) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    backend.lines = passing_logs(ctx)
    backend.during = lambda: CHANGES[change](CANARIES[which](ctx))
    with pytest.raises(SelfTestFailed, match=f"^{CHANGED[which]}$"):
        selftest().exec_path(ctx)


@pytest.mark.parametrize("which", sorted(CANARIES))
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_canary_changed_before_the_probe_fails_the_precondition(
        tmp_path: Path, which: str, change: str) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    backend.before = lambda: CHANGES[change](CANARIES[which](ctx))
    with pytest.raises(SelfTestFailed, match="^canary-precondition$"):
        selftest().exec_path(ctx)
    assert backend.calls == []                   # the probe never ran


def test_the_canaries_are_released_whatever_happens(tmp_path: Path) -> None:
    before = len(os.listdir("/proc/self/fd"))  # noqa: PTH208
    for backend in (StubBackend(), StubBackend(raises=True), StubBackend(mode="bridge")):
        ctx = context(tmp_path, backend)
        backend.lines = passing_logs(ctx)
        try:
            selftest().exec_path(ctx)
        except SelfTestFailed:
            pass
    assert len(os.listdir("/proc/self/fd")) == before  # noqa: PTH208


@dataclass
class StubTmux:
    """The pane: shows the prompt marker, and on a paste runs `on_paste` (the 'agent')."""
    screen: str = "› "
    on_paste: list[Callable[[], None]] = field(default_factory=list[Callable[[], None]])
    pasted: list[str] = field(default_factory=list[str])

    def capture(self, name: str, lines: int) -> str:
        return self.screen

    def paste(self, name: str, text: str) -> None:
        self.pasted.append(text)
        for action in self.on_paste:
            threading.Thread(target=action, daemon=True).start()


def fake_probe_proc(proc: Path, cli: Path) -> None:
    """1: the host's init, 7: the workload's first process (the supervisor), 30: the CLI, 32: the probe."""
    for pid, exe, argv, ppid in ((1, "/usr/lib/systemd/systemd", ("systemd",), 0),
                                 (7, "/opt/openshell/bin/supervisor", ("supervisor",), 1),
                                 (30, str(cli), ("codex",), 7), (32, PROBE_EXE, PROBE_ARGV, 30)):
        d = proc / str(pid)
        (d / "task" / str(pid)).mkdir(parents=True)
        (d / "ns").mkdir()
        (d / "exe").symlink_to(exe)
        mnt = "mnt:[2]" if pid > 1 else "mnt:[3]"                           # init: the host's
        for ns, value in (("net", "net:[4026531999]"), ("user", "user:[1]"), ("mnt", mnt)):
            (d / "ns" / ns).symlink_to(value)
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
        (d / "environ").write_bytes(b"HOME=/s/home\0")
        (d / "stat").write_text(f"{pid} (x) S {ppid} " + "0 " * 17 + f"{pid} 0\n")   # started in pid order
        nnp = 0 if pid in (1, 7) else 1
        status = (f"Name:\tx\nTgid:\t{pid}\nPid:\t{pid}\nPPid:\t{ppid}\nTracerPid:\t0\nNoNewPrivs:\t{nnp}\n"
                  "CapPrm:\t0000000000000000\nCapEff:\t0000000000000000\n")
        (d / "status").write_text(status)
        (d / "task" / str(pid) / "status").write_text(status)
        (d / "cgroup").write_text("0::/init.scope\n" if pid == 1 else f"0::{WORKLOAD_CG}\n")
    (proc / "self").mkdir()
    (proc / "self" / "cgroup").write_text("0::/user.slice/scanner.scope\n")
    cg = proc.parent / "cgroupfs" / WORKLOAD_CG.lstrip("/")
    cg.mkdir(parents=True)
    (cg / "cgroup.threads").write_text("7\n30\n32\n")
    (cg / "cgroup.freeze").write_text("0\n")
    (cg / "cgroup.events").write_text("populated 1\nfrozen 1\n")


WORKLOAD_CG = "/user.slice/libpod-x.scope"


@dataclass
class AgentBackend(StubBackend):
    """Also answers the workload namespace pin, with `fake_probe_proc`'s values."""
    ns_out: bytes = b"user:[1]\nmnt:[2]\n"

    def workload_pid(self, name: str) -> int:
        self.before()
        return 7

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        if tuple(argv) == NS_ARGV:
            return ExecResult(0, self.ns_out, b"")
        return super().exec(name, workdir, argv, input=input, timeout=timeout)


def agent_context(root: Path, tmux: StubTmux) -> ProbeContext:
    backend = AgentBackend()
    ctx = context(root, backend)
    ctx = dataclasses.replace(ctx, tmux=cast(Tmux, tmux), cli=Cli(root / "codex", root, "0.160.0"))
    backend.lines = [f"[1800000000.0] [ocsf] {needle}" for _, needle in log_needles(ctx, "agent")]
    return ctx


def run_probe(ctx: ProbeContext, results: list[dict[str, object]]) -> None:
    with socket.socket(socket.AF_UNIX) as s:
        s.connect(str(ctx.layout.probe_socket(ctx.generation)))
        s.sendall(b"".join((json.dumps(m) + "\n").encode() for m in results))
        s.shutdown(socket.SHUT_WR)               # all sent: as the probe exiting after the ACK
        s.settimeout(5)
        try:
            while s.recv(64):
                pass
        except ConnectionResetError:
            pass


def fake_peer(s: socket.socket) -> tuple[int, int]:
    """The fake probe PID 32, with a live pidfd (this test process's own)."""
    return 32, os.pidfd_open(os.getpid())


def agent_selftest(proc: Path) -> OpenShellSelfTest:
    """Real time, with every wait cut to a bounded 20 ms poll (the probe 'runs' on another thread)."""
    return OpenShellSelfTest(sleep=lambda s: time.sleep(min(s, 0.02)), proc=proc, peer=fake_peer,
                             ptrace_scope=lambda: 2, cgroupfs=proc.parent / "cgroupfs")


def test_agent_path_passes_on_one_verified_run() -> None:
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        good = [*({"check": c, "ok": True, "evidence": ""} for c in AGENT_CHECKS), {"done": 0}]
        tmux.on_paste.append(lambda: run_probe(ctx, good))
        agent_selftest(root / "proc").agent_path(ctx)
        [prompt] = tmux.pasted
        assert " ".join(PROBE_ARGV) in prompt and "fake-session-token" not in prompt
        cfg = json.loads((ctx.layout.run(1) / "agent-probe.json").read_text())
        assert cfg["path"] == "agent" and "fake-session-token" not in json.dumps(cfg)
        assert not ctx.layout.probe_socket(1).exists()


@pytest.mark.parametrize("cgroup", ["0::/user.slice/scanner.scope\n", "0::/\n", "1:cpu:/x\n", None])
def test_agent_path_fails_without_a_cgroup_of_the_workloads_own(cgroup: str | None) -> None:
    """The scanner's own cgroup, the root, cgroup v1, or none readable: nothing to freeze and enumerate."""
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        if cgroup is None:
            (root / "proc" / "7" / "cgroup").unlink()
        else:
            (root / "proc" / "7" / "cgroup").write_text(cgroup)
        ctx.layout.run(1).mkdir()
        with pytest.raises(SelfTestFailed, match="^workload-cgroup$"):
            agent_selftest(root / "proc").agent_path(ctx)
        assert tmux.pasted == []


def test_agent_path_fails_without_the_prompt() -> None:
    with short_dir() as root:
        ctx = agent_context(root, StubTmux(screen="loading"))
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        clock = iter(range(1_800_000_000, 1_800_001_000))
        st = OpenShellSelfTest(clock=lambda: float(next(clock)), sleep=lambda s: None, proc=root / "proc",
                               peer=fake_peer, ptrace_scope=lambda: 2,
                               cgroupfs=root / "cgroupfs")
        with pytest.raises(SelfTestFailed, match="agent-prompt"):
            st.agent_path(ctx)


def test_agent_path_fails_when_the_agent_never_runs_the_probe() -> None:
    with short_dir() as root:
        ctx = agent_context(root, StubTmux())
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        clock = iter(range(1_800_000_000, 1_800_010_000))
        st = OpenShellSelfTest(clock=lambda: float(next(clock)), sleep=lambda s: None, proc=root / "proc",
                               peer=fake_peer, ptrace_scope=lambda: 2,
                               cgroupfs=root / "cgroupfs")
        with pytest.raises(SelfTestFailed, match="agent-path-channel: no verified probe run"):
            st.agent_path(ctx)


@pytest.mark.parametrize("which", sorted(CANARIES))
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_agent_path_a_canary_changed_while_the_probe_runs_fails_its_own_check(
        which: str, change: str) -> None:
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        good = [*({"check": c, "ok": True, "evidence": ""} for c in AGENT_CHECKS), {"done": 0}]

        def agent() -> None:
            CHANGES[change](CANARIES[which](ctx))
            run_probe(ctx, good)

        tmux.on_paste.append(agent)
        with pytest.raises(SelfTestFailed, match=f"^{CHANGED[which]}$"):
            agent_selftest(root / "proc").agent_path(ctx)
        assert not ctx.layout.probe_socket(1).exists()


@pytest.mark.parametrize("which", sorted(CANARIES))
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_agent_path_a_canary_changed_before_the_prompt_fails_the_precondition(
        which: str, change: str) -> None:
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        cast(AgentBackend, ctx.backend).before = lambda: CHANGES[change](CANARIES[which](ctx))
        with pytest.raises(SelfTestFailed, match="^canary-precondition$"):
            agent_selftest(root / "proc").agent_path(ctx)
        assert tmux.pasted == []                 # the agent was never asked to run the probe


def test_agent_path_a_probe_finishing_during_the_last_poll_fails() -> None:
    """The verified probe sends `done` while the self-test sleeps its last poll, after the deadline: the
    wait then sees it done, but the channel stamped `done` past the deadline, so it fails."""
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        good = [*({"check": c, "ok": True, "evidence": ""} for c in AGENT_CHECKS), {"done": 0}]
        now = [1000.0]

        def sleep(seconds: float) -> None:
            if tmux.pasted and now[0] == 1000.0:
                now[0] += ctx.settings.agent_probe_seconds + 1      # the poll oversleeps the deadline ...
                run_probe(ctx, good)                                # ... and the probe finishes meanwhile

        st = OpenShellSelfTest(clock=lambda: now[0], sleep=sleep, proc=root / "proc", peer=fake_peer,
                               ptrace_scope=lambda: 2, cgroupfs=root / "cgroupfs")
        with pytest.raises(SelfTestFailed, match="^agent-path-channel: the probe did not finish in time$"):
            st.agent_path(ctx)


def test_agent_path_a_channel_that_fails_to_start_leaves_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected")

    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        monkeypatch.setattr(socket.socket, "listen", fail)
        with pytest.raises(OSError, match="injected"):
            agent_selftest(root / "proc").agent_path(ctx)
        monkeypatch.undo()
        assert not ctx.layout.probe_socket(1).exists() and tmux.pasted == []


def test_agent_path_a_handshake_finishing_after_the_deadline_fails() -> None:
    """The probe sends `done` in time, but the self-test's last poll oversleeps the deadline before the
    probe takes the ACK and closes: the run completes late, so it fails although `done` was in time."""
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        good = [*({"check": c, "ok": True, "evidence": ""} for c in AGENT_CHECKS), {"done": 0}]
        now = [1000.0]
        sent = threading.Event()
        late = threading.Event()

        def probe() -> None:
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(str(ctx.layout.probe_socket(ctx.generation)))
                s.sendall(b"".join((json.dumps(m) + "\n").encode() for m in good))
                assert s.recv(64)                   # the ACK
                sent.set()
                late.wait(5)                        # the clean EOF only after the deadline has passed

        def sleep(seconds: float) -> None:
            if sent.is_set() and not late.is_set():
                now[0] += ctx.settings.agent_probe_seconds + 1
                late.set()
            time.sleep(min(seconds, 0.02))

        tmux.on_paste.append(probe)
        st = OpenShellSelfTest(clock=lambda: now[0], sleep=sleep, proc=root / "proc", peer=fake_peer,
                               ptrace_scope=lambda: 2, cgroupfs=root / "cgroupfs")
        with pytest.raises(SelfTestFailed, match="^agent-path-channel: the probe did not finish in time$"):
            st.agent_path(ctx)


def test_both_paths_run_the_protection_checks() -> None:
    assert set(PROTECTION_CHECKS) <= set(EXEC_CHECKS) and set(PROTECTION_CHECKS) <= set(AGENT_CHECKS)


@pytest.mark.parametrize("scope", [-1, 0, 1])
def test_the_exec_path_needs_ptrace_scope_2(tmp_path: Path, scope: int) -> None:
    backend = StubBackend()
    ctx = context(tmp_path, backend)
    with pytest.raises(SelfTestFailed, match=f"^ptrace-scope: {scope}, probe protection needs 2 or more$"):
        selftest(scope).exec_path(ctx)
    assert backend.calls == []


def test_the_agent_path_needs_ptrace_scope_2() -> None:
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        st = OpenShellSelfTest(sleep=lambda s: None, proc=root / "proc", peer=fake_peer,
                               ptrace_scope=lambda: 1)
        with pytest.raises(SelfTestFailed, match="^ptrace-scope: 1, probe protection needs 2 or more$"):
            st.agent_path(ctx)
        assert tmux.pasted == []


@pytest.mark.parametrize("out", [b"", b"user:[1]\n", b"user:[1]\nmnt:[2]\nmnt:[3]\n", b"mnt:[2]\nuser:[1]\n"])
def test_the_agent_path_needs_the_workload_namespaces_pinned(out: bytes) -> None:
    with short_dir() as root:
        tmux = StubTmux()
        ctx = agent_context(root, tmux)
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        cast(AgentBackend, ctx.backend).ns_out = out
        with pytest.raises(SelfTestFailed, match="^workload-namespaces$"):
            agent_selftest(root / "proc").agent_path(ctx)
        assert tmux.pasted == []


def test_read_ptrace_scope(tmp_path: Path) -> None:
    (tmp_path / "scope").write_text("2\n")
    (tmp_path / "junk").write_text("x")
    assert (read_ptrace_scope(tmp_path / "scope"), read_ptrace_scope(tmp_path / "junk"),
            read_ptrace_scope(tmp_path / "missing")) == (2, -1, -1)
