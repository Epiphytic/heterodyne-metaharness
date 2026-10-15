"""wsd's agent runtime from the host config's `[platform] sandbox` (ADR 0001 §3.2, §7). Plan 4 builds
OpenShell only. A backend it doesn't build leaves wsd on NoRuntime, so a host whose setup recorded one
(every Linux host before plan 4 recorded `bubblewrap`) runs no agents until the operator opts in."""

import os
import shutil
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from heterodyne.agents.registry import ADAPTERS
from heterodyne.config import ConfigError, load, paths
from heterodyne.config.layers import table_at
from heterodyne.harness import paths as store_paths
from heterodyne.harness import selection
from heterodyne.harness import settings as harness_settings
from heterodyne.harness.store import open_store
from heterodyne.sandbox.hostread import HostUsageReader
from heterodyne.sandbox.openshell import OpenShellBackend, tool_env
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import DEFAULT_ACCOUNT, RuntimeConfig, SandboxRuntime
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.tmux import Tmux
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.usage import NoUsageReader, UsageReader
from heterodyne.wsd.workstream import utc_now

TMUX_SOCKET = "heterodyne-wsd"
TMUX_SCOPE = "heterodyne-wsd-tmux"      # prefix of the transient systemd scopes that hold the tmux server
CANARY = ".heterodyne-canary"           # in the real home: the self-test proves it unreadable inside
NOT_BUILT = frozenset({"bubblewrap", "seatbelt"})


def _stderr(text: str) -> None:
    print(text, file=sys.stderr)


def _launcher(service_manager: object) -> Callable[[], tuple[str, ...]] | None:
    """On systemd the wsd unit kills its whole cgroup on a stop, so the tmux server (and every agent)
    is started in a transient scope of its own, with a unique name per start."""
    if service_manager != "systemd":
        return None
    return lambda: (shutil.which("systemd-run") or "systemd-run", "--user", "--scope", "--collect",
                    "--quiet", f"--unit={TMUX_SCOPE}-{uuid.uuid4().hex[:12]}",
                    "--description=heterodyne wsd tmux server (agent sessions)")


def _config_dir(s: WsdSettings, env: Mapping[str, str]) -> Path:
    return s.config_dir if s.config_dir is not None else paths.config_dir(env)


def _store_problems(h: harness_settings.HarnessSettings, accounts: Accounts,
                    env: Mapping[str, str]) -> Callable[[], list[str]]:
    """The store's location problems, worked out again on every verification: what `config check` lists,
    with every adapter's default and configured login directories (plan 4c E22, `STORE_UNSAFE`)."""
    def problems() -> list[str]:
        logins = [accounts.login_paths(a, n)[0] for a in sorted(ADAPTERS)
                  for n in (DEFAULT_ACCOUNT, *accounts.configured(a))]
        return store_paths.location_problems(h, logins, env)
    return problems


def build_runtime(s: WsdSettings, env: Mapping[str, str], *,
                  notice: Callable[[str], None] = _stderr) -> AgentRuntime:
    settings = sandbox_settings(env)
    if settings.backend == "none":
        return NoRuntime()
    if settings.backend in NOT_BUILT:
        notice(f"wsd: [platform] sandbox = {settings.backend!r} is not built yet; no agents will run "
               "(set it to \"openshell\" to use the sandbox runtime)")
        return NoRuntime()
    if settings.backend != "openshell":
        raise ConfigError("config.toml: [platform] sandbox must be openshell, bubblewrap, seatbelt or none")
    if s.accounts is None:
        raise ConfigError("the sandbox runtime needs the host's accounts")
    home = env.get("HOME")
    if not home:
        raise ConfigError("the sandbox runtime needs HOME")
    real_home = Path(home)
    service_manager = table_at(load(env=env).values, "platform", "config").get("service_manager")
    harnesses = settings.harnesses
    config = RuntimeConfig(
        sessions=s.state_dir / "sessions", credlocks=s.credlock_dir,
        settings=settings, accounts=s.accounts, real_home=real_home,
        real_home_canary=real_home / CANARY, wsd_socket=s.socket, uid=os.getuid(), gid=os.getgid(),
        tmux=Tmux(TMUX_SOCKET, launcher=_launcher(service_manager)), clock=utc_now,
        store=open_store(harnesses), taint_dir=s.state_dir / "harness-taint", config_dir=_config_dir(s, env),
        store_problems=_store_problems(harnesses, s.accounts, env))
    backend = OpenShellBackend(settings.openshell, settings.podman, settings.image,
                               tool_env(env, settings.tool_env))
    return SandboxRuntime(config, backend, OpenShellSelfTest())


def build_usage_reader(s: WsdSettings, env: Mapping[str, str], runtime: AgentRuntime) -> UsageReader:
    """D9 (d)'s trusted read, when the sandbox runtime runs and Codex has a selected install. Each read
    verifies the selection's install again (`verified_cli`), so a reader built before a taint or a new
    selection never runs a CLI that doesn't verify now. The capability table decides whether wsd ever asks
    it (E15)."""
    settings = sandbox_settings(env)
    if not isinstance(runtime, SandboxRuntime) or s.accounts is None:
        return NoUsageReader()
    try:
        chosen = selection.load(_config_dir(s, env)).get("codex")
    except ConfigError:
        chosen = None           # `config check` says why; the reader is off until a restart reads it
    if chosen is None or chosen.current is None:
        return NoUsageReader()
    codex = ADAPTERS["codex"]
    base = {"PATH": env.get("PATH", os.defpath), "HOME": env.get("HOME", ""), "LANG": "C.UTF-8"}
    return HostUsageReader(s.accounts, lambda: runtime.verified_cli(codex), s.credlock_dir,
                           s.state_dir / "hostread", settings.lock_wait_seconds, env=base)
