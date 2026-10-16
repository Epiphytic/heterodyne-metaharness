from collections.abc import Mapping
from pathlib import Path

import pytest
from sandbox_env import config_env
from wsd_env import accounts_at, selected_config

from heterodyne import platform
from heterodyne.admind.settings import resolve as admind_settings
from heterodyne.config import ConfigError, load
from heterodyne.marmot.nip19 import hex_to_npub
from heterodyne.sandbox.build import CANARY, TMUX_SOCKET, build_runtime, build_usage_reader
from heterodyne.sandbox.hostread import HostUsageReader
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.wsd import cli
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.usage import NoUsageReader, UsageReader


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
    assert (c.taint_dir, c.config_dir) == (tmp_path / "state" / "wsd" / "harness-taint", tmp_path / "config")
    assert isinstance(c.store_problems(), list)          # worked out on each call, not at build time


def test_setup_records_no_runtime_until_the_operator_selects_openshell(tmp_path: Path) -> None:
    """Plan 3 P1 (btq-g08sd): crash-loop accounting must land before setup enables the runtime, so what
    setup records on Linux runs no agents; only an explicit `openshell` builds the sandbox runtime."""
    recorded = platform.backends("linux")
    host = "".join(f'{k} = "{v}"\n' for k, v in recorded.items())
    runtime, notices = build(tmp_path, f"[platform]\n{host}")
    assert isinstance(runtime, NoRuntime) and len(notices) == 1 and "openshell" in notices[0]
    edited = host.replace(f'sandbox = "{recorded["sandbox"]}"', 'sandbox = "openshell"')
    runtime, notices = build(tmp_path, f"[platform]\n{edited}")
    assert notices == [] and isinstance(runtime, SandboxRuntime)


def test_the_backend_runs_with_wsds_tool_environment_plus_its_overrides(tmp_path: Path) -> None:
    """Codex T12 r1 (copied from the plan): the clients need wsd's PATH, HOME and XDG_RUNTIME_DIR, and only
    those of wsd's variables; `tool_env` adds to them and wins."""
    env = config_env(tmp_path, '[platform]\nsandbox = "openshell"\n'
                               '[sandbox]\ntool_env = { PATH = "/opt/podman5/bin:/usr/bin", '
                               'CONTAINERS_CONF = "/opt/podman5/containers.conf" }\n')
    env |= {"PATH": "/usr/bin", "XDG_RUNTIME_DIR": "/run/user/1000", "WN_TOKEN": "fake-secret",
            "SSH_AUTH_SOCK": "/run/user/1000/agent.sock"}
    runtime = build_runtime(settings(tmp_path, env), env, notice=lambda _: None)
    assert isinstance(runtime, SandboxRuntime) and isinstance(runtime.backend, OpenShellBackend)
    assert runtime.backend.env == {"HOME": env["HOME"], "XDG_RUNTIME_DIR": "/run/user/1000",
                                   "PATH": "/opt/podman5/bin:/usr/bin",
                                   "CONTAINERS_CONF": "/opt/podman5/containers.conf"}


def test_openshell_without_systemd_starts_tmux_directly(tmp_path: Path) -> None:
    runtime, _ = build(tmp_path, '[platform]\nsandbox = "openshell"\nservice_manager = "launchd"\n'
                                 '[sandbox]\nimage = "localhost/heterodyne-agent:1"\n')
    assert isinstance(runtime, SandboxRuntime) and runtime.c.tmux.launcher is None


def test_wsd_run_uses_the_built_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = config_env(tmp_path, "")
    s = settings(tmp_path, env)
    seen: list[AgentRuntime] = []
    readers: list[UsageReader | None] = []

    def run(_s: WsdSettings, _f: object, runtime: AgentRuntime, *,
            usage_reader: UsageReader | None = None) -> int:
        seen.append(runtime)
        readers.append(usage_reader)
        return 0

    def refused(_s: WsdSettings, _env: Mapping[str, str]) -> AgentRuntime:
        raise ConfigError("config.toml: [platform] sandbox must be openshell, bubblewrap, seatbelt or none")

    monkeypatch.setattr(cli, "_settings", lambda: s)
    monkeypatch.setattr(cli, "_factory", lambda _s: None)
    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(cli, "build_runtime", lambda _s, _env: NoRuntime())
    monkeypatch.setattr(cli, "build_usage_reader", lambda _s, _env, _runtime: NoUsageReader())
    assert cli.wsd_main(["run"]) == 0 and isinstance(seen[0], NoRuntime)
    assert isinstance(readers[0], NoUsageReader)
    monkeypatch.setattr(cli, "build_runtime", refused)
    assert cli.wsd_main(["run"]) == cli.EX_CONFIG


def test_the_reader_needs_the_sandbox_runtime_and_a_selected_codex_install(tmp_path: Path) -> None:
    env = config_env(tmp_path, "")
    assert isinstance(build_usage_reader(settings(tmp_path, env), env, NoRuntime()), NoUsageReader)
    env = config_env(tmp_path, '[platform]\nsandbox = "openshell"\n')
    runtime = build_runtime(settings(tmp_path, env), env)
    assert isinstance(build_usage_reader(settings(tmp_path, env), env, runtime), NoUsageReader)
    selected_config(Path(env["HETERODYNE_CONFIG_DIR"]))
    assert isinstance(build_usage_reader(settings(tmp_path, env), env, NoRuntime()), NoUsageReader)
    reader = build_usage_reader(settings(tmp_path, env), env, runtime)
    assert isinstance(reader, HostUsageReader)
    assert reader.credlocks == settings(tmp_path, env).credlock_dir
    assert reader.scratch_root == settings(tmp_path, env).state_dir / "hostread"


@pytest.mark.parametrize("state_override", [False, True])
def test_runtime_create_paths_and_admind_share_the_host_state_directory(
        tmp_path: Path, state_override: bool) -> None:
    env = config_env(tmp_path, '[platform]\nsandbox = "openshell"\nservice_manager = "systemd"\n'
                               '[admind]\nprofile = "p-one"\n'
                               f'[admind.marmot]\nhome = "{tmp_path.parent / "m"}"\n'
                               'relays = ["wss://relay.example.org"]\n')
    (Path(env["HETERODYNE_CONFIG_DIR"]) / "policy.toml").write_text(
        'approvers = ["op"]\noperators = ["op"]\n[identities.op]\n'
        f'marmot_npub = "{hex_to_npub("c3" * 32)}"\n')
    if state_override:
        env["HETERODYNE_STATE_DIR"] = str(tmp_path / "different-state")
    runtime = build_runtime(settings(tmp_path, env), env)
    assert isinstance(runtime, SandboxRuntime)
    admind = admind_settings(load(env=env), env)
    ledger = runtime._ledger()  # pyright: ignore[reportPrivateUsage]
    assert runtime.c.alerts == ledger.alerts == admind.alerts_dir
    assert runtime.c.creates == ledger.directory == admind.state_dir.parent / "creates"
    assert not ledger.alerts.is_relative_to(runtime.c.sessions.parent)
