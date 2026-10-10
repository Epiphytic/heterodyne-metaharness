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
    PROBE_ARGV,
    PROBE_EXE,
    OpenShellSelfTest,
    classify,
    env_allowed,
    held_canaries,
    log_needles,
    other_accounts,
    probe_config,
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


def selftest() -> OpenShellSelfTest:
    return OpenShellSelfTest(wall=lambda: 1_800_000_000.0, sleep=lambda s: None)


def test_probes_file_is_stdlib_only_and_names_every_check() -> None:
    tree = ast.parse(PROBES.read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"errno", "hashlib", "ipaddress", "json", "os", "socket", "stat", "subprocess", "sys"}
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
    for pid, exe, argv, ppid in ((30, str(cli), ("codex",), 1), (32, PROBE_EXE, PROBE_ARGV, 30)):
        d = proc / str(pid)
        (d / "ns").mkdir(parents=True)
        (d / "exe").symlink_to(exe)
        (d / "ns" / "net").symlink_to("net:[4026531999]")
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
        (d / "environ").write_bytes(b"HOME=/s/home\0")
        (d / "status").write_text(f"Name:\tx\nPPid:\t{ppid}\nTracerPid:\t0\n")
    (proc / "7").mkdir()
    (proc / "7" / "ns").mkdir()
    (proc / "7" / "ns" / "net").symlink_to("net:[4026531999]")            # the workload's first process


@dataclass
class AgentBackend(StubBackend):
    def workload_pid(self, name: str) -> int:
        self.before()
        return 7


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


def agent_selftest(proc: Path) -> OpenShellSelfTest:
    """Real time, with every wait cut to a bounded 20 ms poll (the probe 'runs' on another thread)."""
    return OpenShellSelfTest(sleep=lambda s: time.sleep(min(s, 0.02)), proc=proc, peer_pid=lambda s: 32)


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


def test_agent_path_fails_without_the_prompt() -> None:
    with short_dir() as root:
        ctx = agent_context(root, StubTmux(screen="loading"))
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        clock = iter(range(1_800_000_000, 1_800_001_000))
        st = OpenShellSelfTest(wall=lambda: float(next(clock)), sleep=lambda s: None, proc=root / "proc",
                               peer_pid=lambda s: 32)
        with pytest.raises(SelfTestFailed, match="agent-prompt"):
            st.agent_path(ctx)


def test_agent_path_fails_when_the_agent_never_runs_the_probe() -> None:
    with short_dir() as root:
        ctx = agent_context(root, StubTmux())
        fake_probe_proc(root / "proc", root / "codex")
        ctx.layout.run(1).mkdir()
        clock = iter(range(1_800_000_000, 1_800_010_000))
        st = OpenShellSelfTest(wall=lambda: float(next(clock)), sleep=lambda s: None, proc=root / "proc",
                               peer_pid=lambda s: 32)
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
