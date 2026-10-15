import subprocess
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sandbox_env import config_env
from wsd_env import accounts_at, selected_config

import heterodyne
from heterodyne import platform
from heterodyne.admind import settings as admind_settings
from heterodyne.config import ConfigError, paths
from heterodyne.sandbox import build as build_module
from heterodyne.sandbox.build import CANARY, TMUX_SOCKET, build_harness, build_runtime, build_usage_reader
from heterodyne.sandbox.hostread import HostUsageReader
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.wsd import btq, cli
from heterodyne.wsd import harness as wh
from heterodyne.wsd.harness import HarnessOps
from heterodyne.wsd.journal import Journal
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
    builders: list[object] = []

    def run(_s: WsdSettings, _f: object, runtime: AgentRuntime, *,
            usage_reader: UsageReader | None = None, harness: object = None) -> int:
        seen.append(runtime)
        readers.append(usage_reader)
        builders.append(harness)
        return 0

    def refused(_s: WsdSettings, _env: Mapping[str, str]) -> AgentRuntime:
        raise ConfigError("config.toml: [platform] sandbox must be openshell, bubblewrap, seatbelt or none")

    monkeypatch.setattr(cli, "_settings", lambda: s)
    monkeypatch.setattr(cli, "_factory", lambda _s: None)
    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(cli, "build_runtime", lambda _s, _env: NoRuntime())
    monkeypatch.setattr(cli, "build_usage_reader", lambda _s, _env, _runtime: NoUsageReader())
    assert cli.wsd_main(["run"]) == 0 and isinstance(seen[0], NoRuntime)
    assert isinstance(readers[0], NoUsageReader) and callable(builders[0])
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


NO_QUEUES = cast(btq.QueueFactory, None)        # never called: nothing here installs
ADMIND = """
[platform]
service_manager = "systemd"
[admind]
{approve_bead}
[admind.marmot]
relays = ["wss://relay.example.org"]
"""


@pytest.mark.parametrize(("host", "refusal"), [
    ("", wh.NO_APPROVE_BEAD),                                                   # no [admind]
    (ADMIND.format(approve_bead='profile = "p-one"'), wh.NO_APPROVE_BEAD),      # approve_bead unset
    (ADMIND.format(approve_bead='approve_bead = "/usr/bin/true"'), wh.ADMIND_INVALID),   # no profile
    (ADMIND.format(approve_bead='profile = "p-one"\napprove_bead = "relative"'), wh.ADMIND_INVALID),
])
def test_an_install_refusal_says_why_no_decision_is_read(tmp_path: Path, host: str, refusal: str) -> None:
    """A missing `[admind] approve_bead` and an `[admind]` that doesn't validate each get their own refusal,
    which `install` gives before it stages or asks, and which wsd's start says once; the readout still
    fails, as a backstop."""
    env = config_env(tmp_path, host)
    journal = Journal(tmp_path / "journal.sqlite")
    notices: list[str] = []
    try:
        ops = build_harness(settings(tmp_path, env), env, NO_QUEUES, NoRuntime(), journal, lambda: (),
                            notice=notices.append)
        assert isinstance(ops, HarnessOps) and ops.readout_refusal == refusal
        assert notices == [f"wsctl harness install will refuse every install: {refusal}"]
        with pytest.raises(btq.ReadoutFailed):
            ops.readout("btq-1")
    finally:
        journal.close()


def test_a_valid_admind_with_approve_bead_reads_the_decisions(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    env = config_env(tmp_path, "")
    monkeypatch.setattr(admind_settings, "resolve", lambda _cfg, _env: SimpleNamespace(
        approve_bead=Path("/usr/bin/true")))
    monkeypatch.setattr(build_module, "load", lambda env: SimpleNamespace(values={"admind": {
        "approve_bead": "/usr/bin/true"}}))
    journal = Journal(tmp_path / "journal.sqlite")
    notices: list[str] = []
    try:
        ops = build_harness(settings(tmp_path, env), env, NO_QUEUES, NoRuntime(), journal, lambda: (),
                            notice=notices.append)
        assert isinstance(ops, HarnessOps) and ops.readout_refusal == "" and notices == []
        readout = cast("partial[Any]", ops.readout)
        assert readout.func is btq.approval_readout and readout.keywords == {"binary": Path("/usr/bin/true")}
    finally:
        journal.close()


def test_wsd_builds_the_harness_operations_against_admind(tmp_path: Path) -> None:
    """Plan 4c T10: the asks go to admind's ask socket and the alerts to its alerts directory. With admind
    not set up no decision can be read, so every readout fails."""
    env = config_env(tmp_path, "")
    s = settings(tmp_path, env)
    journal = Journal(tmp_path / "journal.sqlite")
    try:
        ops = build_harness(s, env, NO_QUEUES, NoRuntime(), journal, lambda: ())
        assert isinstance(ops, HarnessOps)
        state = paths.state_dir(env)
        assert ops.ask_socket == state / "admind" / "ask.sock" and ops.alerts_dir == state / "alerts"
        assert ops.taint_dir == s.state_dir / "harness-taint"
        # The commit ref names this checkout by path, as btq resolves it, and its HEAD.
        checkout = Path(heterodyne.__file__).resolve().parents[2]
        assert ops.repo == str(checkout)
        head = subprocess.run(["git", "-C", ops.repo, "rev-parse", "HEAD"], capture_output=True, check=True)
        assert ops.commit == head.stdout.decode().strip()
        with pytest.raises(btq.ReadoutFailed):
            ops.readout("btq-1")
        no_accounts = WsdSettings(s.state_dir, 60, 3600, 3, tmp_path / "btq", {}, (), None)
        assert build_harness(no_accounts, env, NO_QUEUES, NoRuntime(), journal, lambda: ()) is None
    finally:
        journal.close()
