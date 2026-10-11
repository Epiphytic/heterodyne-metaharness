from collections.abc import Mapping
from pathlib import Path

import pytest
from sandbox_env import config_env
from wsd_env import accounts_at

from heterodyne.config import ConfigError
from heterodyne.sandbox.build import CANARY, TMUX_SOCKET, build_runtime
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.wsd import cli
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings


def settings(tmp_path: Path, env: dict[str, str]) -> WsdSettings:
    return WsdSettings(tmp_path / "state" / "wsd", 60, 3600, 3, tmp_path / "btq", {}, (),
                       accounts_at(Path(env["HOME"])))


def build(tmp_path: Path, host: str) -> tuple[AgentRuntime, list[str]]:
    env = config_env(tmp_path, host)
    notices: list[str] = []
    return build_runtime(settings(tmp_path, env), env, notice=notices.append), notices


@pytest.mark.parametrize("host", ["", '[platform]\nsandbox = "none"\n'])
def test_no_backend_is_no_runtime(tmp_path: Path, host: str) -> None:
    runtime, notices = build(tmp_path, host)
    assert isinstance(runtime, NoRuntime) and notices == []


@pytest.mark.parametrize("backend", ["bubblewrap", "seatbelt"])
def test_a_backend_plan_4_does_not_build_is_no_runtime_with_a_notice(tmp_path: Path, backend: str) -> None:
    runtime, notices = build(tmp_path, f'[platform]\nsandbox = "{backend}"\n')
    assert isinstance(runtime, NoRuntime)
    assert len(notices) == 1 and backend in notices[0] and "no agents" in notices[0]


def test_an_unknown_backend_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="sandbox"):
        build(tmp_path, '[platform]\nsandbox = "firecracker"\n')


def test_openshell_builds_the_sandbox_runtime(tmp_path: Path) -> None:
    runtime, notices = build(tmp_path, '[platform]\nsandbox = "openshell"\nservice_manager = "systemd"\n'
                                       '[sandbox]\nimage = "localhost/heterodyne-agent:1"\n')
    assert notices == [] and isinstance(runtime, SandboxRuntime)
    assert isinstance(runtime.backend, OpenShellBackend) and isinstance(runtime.selftest, OpenShellSelfTest)
    assert runtime.backend.image == "localhost/heterodyne-agent:1"
    c = runtime.c
    assert c.sessions == tmp_path / "state" / "wsd" / "sessions"
    assert c.wsd_socket == settings(tmp_path, {"HOME": str(c.real_home)}).socket
    assert c.real_home_canary == c.real_home / CANARY
    assert c.tmux.socket_name == TMUX_SOCKET and c.tmux.launcher is not None
    assert c.tmux.launcher()[1:4] == ("--user", "--scope", "--collect")


def test_openshell_without_systemd_starts_tmux_directly(tmp_path: Path) -> None:
    runtime, _ = build(tmp_path, '[platform]\nsandbox = "openshell"\nservice_manager = "launchd"\n'
                                 '[sandbox]\nimage = "localhost/heterodyne-agent:1"\n')
    assert isinstance(runtime, SandboxRuntime) and runtime.c.tmux.launcher is None


def test_wsd_run_uses_the_built_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = config_env(tmp_path, "")
    s = settings(tmp_path, env)
    seen: list[AgentRuntime] = []

    def run(_s: WsdSettings, _f: object, runtime: AgentRuntime) -> int:
        seen.append(runtime)
        return 0

    def refused(_s: WsdSettings, _env: Mapping[str, str]) -> AgentRuntime:
        raise ConfigError("config.toml: [platform] sandbox must be openshell, bubblewrap, seatbelt or none")

    monkeypatch.setattr(cli, "_settings", lambda: s)
    monkeypatch.setattr(cli, "_factory", lambda _s: None)
    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(cli, "build_runtime", lambda _s, _env: NoRuntime())
    assert cli.wsd_main(["run"]) == 0 and isinstance(seen[0], NoRuntime)
    monkeypatch.setattr(cli, "build_runtime", refused)
    assert cli.wsd_main(["run"]) == cli.EX_CONFIG
