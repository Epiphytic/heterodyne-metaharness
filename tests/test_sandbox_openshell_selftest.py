import ast
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from sandbox_env import config_env

from heterodyne.agents.base import Cli
from heterodyne.agents.codex import Codex
from heterodyne.sandbox.backend import Backend, BackendError, ExecResult
from heterodyne.sandbox.openshell_selftest import (
    AGENT_CHECKS,
    EXEC_CHECKS,
    OpenShellSelfTest,
    classify,
    env_allowed,
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


@dataclass
class StubBackend:
    """Answers the self-test's backend calls; records what it was asked."""
    mode: str = "none"
    rc: int = 0
    out: bytes = b""
    lines: list[str] = field(default_factory=list[str])
    raises: bool = False
    calls: list[tuple[list[str], bytes | None]] = field(default_factory=list[tuple[list[str], bytes | None]])

    def network_mode(self, name: str) -> str:
        return self.mode

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        self.calls.append((list(argv), input))
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
    cfg = probe_config(ctx, "exec")
    assert "fake-session-token" not in json.dumps(cfg)
    [chosen] = cfg["chosen"]
    login = ctx.spec.logins[0]
    digest = hashlib.sha256(login.source.read_bytes()).hexdigest()
    assert chosen == {"path": str(login.target), "sha256": digest}
    assert cfg["model_host"] == "chatgpt.com" and cfg["wsd_socket"] == str(ctx.wsd_socket)
    assert ctx.layout.oa_canary.read_text()                         # written fresh, readable outside


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
