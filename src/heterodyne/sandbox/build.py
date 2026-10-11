"""wsd's agent runtime from the host config's `[platform] sandbox` (ADR 0001 §3.2, §7). Plan 4 builds
OpenShell only. A backend it doesn't build leaves wsd on NoRuntime, so a host whose setup recorded one
(every Linux host before plan 4 recorded `bubblewrap`) runs no agents until the operator opts in."""

import os
import shutil
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from heterodyne.config import ConfigError, load
from heterodyne.config.layers import table_at
from heterodyne.sandbox.openshell import OpenShellBackend, tool_env
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import RuntimeConfig, SandboxRuntime
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.tmux import Tmux
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings
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
    config = RuntimeConfig(
        sessions=s.state_dir / "sessions", settings=settings, accounts=s.accounts, real_home=real_home,
        real_home_canary=real_home / CANARY, wsd_socket=s.socket, uid=os.getuid(), gid=os.getgid(),
        tmux=Tmux(TMUX_SOCKET, launcher=_launcher(service_manager)), path=env.get("PATH", os.defpath),
        clock=utc_now)
    backend = OpenShellBackend(settings.openshell, settings.podman, settings.image,
                               tool_env(env, settings.tool_env))
    return SandboxRuntime(config, backend, OpenShellSelfTest())
