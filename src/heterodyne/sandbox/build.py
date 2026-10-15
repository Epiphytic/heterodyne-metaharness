"""wsd's agent runtime from the host config's `[platform] sandbox` (ADR 0001 §3.2, §7). Plan 4 builds
OpenShell only. A backend it doesn't build leaves wsd on NoRuntime, so a host whose setup recorded one
(every Linux host before plan 4 recorded `bubblewrap`) runs no agents until the operator opts in."""

import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from functools import partial
from pathlib import Path
from typing import Any

import heterodyne
from heterodyne.admind import asks
from heterodyne.admind import settings as admind_settings
from heterodyne.agents.registry import ADAPTERS
from heterodyne.config import ConfigError, load, paths
from heterodyne.config.layers import table_at
from heterodyne.harness import paths as store_paths
from heterodyne.harness import selection
from heterodyne.harness import settings as harness_settings
from heterodyne.harness.fetch import Fetcher, HttpsOpener
from heterodyne.harness.store import open_store
from heterodyne.sandbox.hostread import HostUsageReader
from heterodyne.sandbox.openshell import OpenShellBackend, tool_env
from heterodyne.sandbox.openshell_selftest import OpenShellSelfTest
from heterodyne.sandbox.runtime import DEFAULT_ACCOUNT, RuntimeConfig, SandboxRuntime
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.tmux import Tmux
from heterodyne.wsd import btq
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.harness import HarnessOps
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.usage import NoUsageReader, UsageReader
from heterodyne.wsd.workstream import utc_now

TMUX_SOCKET = "heterodyne-wsd"
TMUX_SCOPE = "heterodyne-wsd-tmux"      # prefix of the transient systemd scopes that hold the tmux server
CANARY = ".heterodyne-canary"           # in the real home: the self-test proves it unreadable inside
NOT_BUILT = frozenset({"bubblewrap", "seatbelt"})
FETCH_SECONDS = 600.0                   # one install's downloads, all told
GIT_SECONDS = 10.0


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
        return store_paths.location_problems(h, _logins(accounts), env)
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


def _logins(accounts: Accounts) -> list[Path]:
    return [accounts.login_paths(a, n)[0] for a in sorted(ADAPTERS)
            for n in (DEFAULT_ACCOUNT, *accounts.configured(a))]


def _commit() -> str:
    """The harness checkout's HEAD, which an install approval names; "" when this is not a git checkout (the
    install then refuses `NO_COMMIT`)."""
    root = Path(heterodyne.__file__).resolve().parents[2]
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD^{commit}"],
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=GIT_SECONDS, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.decode(errors="replace").strip()


def _no_readout(bead: str) -> btq.Readout | btq.Busy:
    raise btq.ReadoutFailed("[admind] approve_bead is not set")


def _approve_bead(env: Mapping[str, str]) -> Path | None:
    try:
        return admind_settings.resolve(load(env=env), env).approve_bead
    except ConfigError:
        return None             # admind isn't set up here: no decision can be read, so none is applied


def build_harness(s: WsdSettings, env: Mapping[str, str], factory: btq.QueueFactory, runtime: AgentRuntime,
                  journal: Journal,
                  guard_locks: Callable[[], Sequence[AbstractContextManager[Any]]]) -> HarnessOps | None:
    """`wsctl harness`'s operations (plan 4c T10), or None without the host's accounts (the store's location
    check needs them). The approvals go to admind's ask socket and its alerts directory."""
    if s.accounts is None:
        return None
    harnesses = sandbox_settings(env).harnesses
    state = paths.state_dir(env)
    binary = _approve_bead(env)
    readout = _no_readout if binary is None else partial(btq.approval_readout, binary=binary)
    fetcher = Fetcher(HttpsOpener(), s.state_dir / "harness-cache", time.monotonic, FETCH_SECONDS,
                      login_dirs=_logins(s.accounts))
    return HarnessOps(
        settings=harnesses, store=open_store(harnesses), fetcher=fetcher, queues=factory, journal=journal,
        readout=readout, ask_socket=state / "admind" / asks.ASK_SOCKET, config_dir=_config_dir(s, env),
        taint_dir=s.state_dir / "harness-taint", alerts_dir=state / "alerts", runtime=runtime,
        clock=utc_now, commit=_commit(), guard_locks=guard_locks,
        store_problems=_store_problems(harnesses, s.accounts, env))
