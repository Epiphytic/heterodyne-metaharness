"""Sandbox test helpers: a scratch config directory, and a bounded wait on a condition."""

import base64
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from fakes.fake_backend import FakeBackend
from fakes.scripted_selftest import ScriptedSelfTest
from wsd_env import ADAPTERS, Clock, accounts_at, git_repo, login_home

from heterodyne.config import paths
from heterodyne.config.accounts import Account, canonical_login, credential_key
from heterodyne.sandbox.backend import BackendUnavailable
from heterodyne.sandbox.runtime import RuntimeConfig, SandboxRuntime, read_record
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.tmux import Tmux
from heterodyne.wsd import ids
from heterodyne.wsd.accounts import ConfiguredAccounts
from heterodyne.wsd.runtime import LaunchSpec

BASE_HOST = """
[profiles.p-one]
adapter = "claude-code"

[profiles.p-two]
adapter = "codex"
"""


def config_env(root: Path, host: str = "", workstreams: Mapping[str, str] | None = None) -> dict[str, str]:
    """A config directory at `root/config` holding a minimal host config.toml (two profiles) plus `host`,
    and one `workstreams/<name>.toml` per entry. HOME is `root/home`, with a fake default login per
    adapter. Returns the environment wsd would read it with."""
    config = root / "config"
    (config / "workstreams").mkdir(parents=True, exist_ok=True)
    (config / "config.toml").write_text(BASE_HOST + host)
    for name, text in (workstreams or {}).items():
        (config / "workstreams" / f"{name}.toml").write_text(text)
    home = login_home(root / "home")
    return {"HETERODYNE_CONFIG_DIR": str(config), "HOME": str(home)}


def wait_for(pred: Callable[[], object], timeout: float = 10.0) -> None:
    """Poll `pred` until it is true, failing after `timeout` seconds. A bounded wait on a condition,
    never a fixed delay."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


@contextlib.contextmanager
def short_dir() -> Iterator[Path]:
    """A scratch directory with a short path. Unix socket paths must fit sun_path (108 bytes), and
    pytest's tmp_path is often too deep for a session layout's sockets."""
    root = Path(tempfile.mkdtemp(prefix="hz", dir=tempfile.gettempdir()))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


FAKE_CLI = Path(__file__).resolve().parent / "fakes" / "fake_agent_cli.py"
PINS = {"claude-code": "2.1.286", "codex": "0.160.0"}


def install_fake_cli(root: Path, adapter: str) -> Path:
    """A fake CLI install laid out like the real one, at its pinned version. Returns the binary."""
    if adapter == "claude-code":
        top = root / "cli" / "claude"
        binary = top / "bin" / "claude"
        binary.parent.mkdir(parents=True, exist_ok=True)
        (top / "package.json").write_text(json.dumps({"version": PINS[adapter]}))
    else:
        binary = root / "cli" / "codex" / "releases" / f"{PINS[adapter]}-x86_64-fake" / "bin" / "codex"
        binary.parent.mkdir(parents=True, exist_ok=True)
    name = "claude" if adapter == "claude-code" else "codex"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" --as={name} "$@"\n')
    binary.chmod(0o755)
    return binary


def fake_jwt(exp: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"x.{body}.x"


def write_login(folder: Path, adapter: str, expires_at: int) -> None:
    """A fake login for `adapter` in `folder` that expires at `expires_at` (epoch seconds). No credential."""
    folder.mkdir(parents=True, exist_ok=True)
    if adapter == "claude-code":
        (folder / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"expiresAt": expires_at * 1000, "accessToken": "fake-not-a-token"}}))
    else:
        (folder / "auth.json").write_text(json.dumps({"tokens": {"access_token": fake_jwt(expires_at)}}))


def fresh_logins(home: Path, expires_at: int) -> None:
    """Fake default logins that expire at `expires_at` (epoch seconds). Obvious fakes, no credential."""
    write_login(home / ".claude", "claude-code", expires_at)
    write_login(home / ".codex", "codex", expires_at)


def with_named(rig: "RuntimeRig", adapter: str, name: str, expires_at: int) -> Path:
    """Configure the named account `name` for `adapter` at `~/.hz-<name>`, its fake login expiring at
    `expires_at`, as AU-2 would resolve it, and give the runtime the accounts. Returns the directory."""
    configured = f"~/.hz-{name}"
    env = {"HOME": str(rig.home)}
    write_login(paths.expand(configured, env), adapter, expires_at)
    login_dir, files = canonical_login(name, adapter, configured, env)
    current = rig.runtime.c.accounts
    assert isinstance(current, ConfiguredAccounts)
    account = Account(name, adapter, configured, login_dir, files, credential_key(adapter, files))
    named = {**current.named, (adapter, name): account}
    rig.runtime.c = replace(rig.runtime.c, accounts=ConfiguredAccounts(ADAPTERS, env, named))
    return paths.expand(configured, env)


@dataclass
class RuntimeRig:
    root: Path
    runtime: SandboxRuntime
    backend: FakeBackend
    selftest: ScriptedSelfTest
    tmux: Tmux
    clock: Clock
    worktree: Path
    home: Path
    repo: Path


def runtime_rig(root: Path, tmux: Tmux, clock: Clock, host: str = "") -> RuntimeRig:
    """A SandboxRuntime on the fake backend, the scripted self-test and a guarded tmux, with both fake
    CLIs at their pins and default logins valid for a day. `root` must be short (short_dir)."""
    env = config_env(root / "c", host)
    home = Path(env["HOME"])
    fresh_logins(home, clock() + 86_400)
    claude, codex = install_fake_cli(root / "i", "claude-code"), install_fake_cli(root / "i", "codex")
    backend, selftest = FakeBackend(), ScriptedSelfTest()
    config = RuntimeConfig(
        sessions=root / "s", credlocks=root / "k", settings=sandbox_settings(env), accounts=accounts_at(home),
        real_home=home, real_home_canary=root / "canary", wsd_socket=root / "ctl.sock", uid=os.getuid(),
        gid=os.getgid(), tmux=tmux, path=os.pathsep.join((str(claude.parent), str(codex.parent))),
        clock=clock)
    runtime = SandboxRuntime(config, backend, selftest)
    repo, worktree = git_repo(root / "r"), root / "w"          # a linked worktree, as btq makes them
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "btq/btq-1", str(worktree)],
                   check=True, capture_output=True)
    return RuntimeRig(root, runtime, backend, selftest, tmux, clock, worktree, home, repo)


def launch_spec(rig: RuntimeRig, profile: str, bead: str = "btq-1", *, account: str = "default",
                resume: bool = False) -> LaunchSpec:
    """A first launch, as plan 3's guard builds it: Claude's native ID is the session key, Codex's is
    unknown until its thread starts. The credential key is the account's, pinned now (AU-3)."""
    key = ids.role_session(bead, "coder", profile)
    native = key if profile == "p-one" else None
    return LaunchSpec("alpha", bead, "coder", profile, key, f"{bead} · coder", rig.worktree, resume=resume,
                      native_id=native, account=account, repo=rig.repo,
                      credential_key=rig.runtime.c.accounts.current_key(ADAPTERS[profile], account))


def bead_worktree(rig: RuntimeRig, bead: str) -> Path:
    """A linked worktree of the rig's repository pinned on `btq/<bead>`, for a second independent session:
    a bead's branch holds one session at a time (btq-jjnqm)."""
    worktree = rig.root / f"w-{bead}"
    subprocess.run(["git", "-C", str(rig.repo), "worktree", "add", "-q", "-b", f"btq/{bead}", str(worktree)],
                   check=True, capture_output=True)
    return worktree


def sandbox_teardown(runtime: SandboxRuntime, keys: Iterable[str], listed: Callable[[], set[str]],
                     owners: Mapping[str, SandboxRuntime] | None = None) -> list[str]:
    """The live module's teardown: stop every session it launched, each tried whatever the others do,
    then confirm with the sandbox list (`openshell sandbox list`) that none of their sandboxes is left.
    A session launched through another runtime (`owners`) is stopped through that one: only the runtime
    that launched it holds its session server.
    A create that may still land (started, never returned, its cancellation not recorded) is uncertain
    in any phase, `ended` included: its watcher is kept for a sandbox that may yet appear. So is any
    generation's watcher still running once its session is stopped, which an earlier generation's late
    create leaves behind a later record. Returns what is uncertain; the caller keeps the scratch, records
    and tmux server unless it is empty."""
    uncertain: list[str] = []
    sandboxes: set[str] = set()
    for key in dict.fromkeys(keys):
        owner = (owners or {}).get(key, runtime)
        try:
            rec = read_record(owner.layout(key))
            sandboxes |= {rec.sandbox} if rec is not None else set()
            owner.stop(key)
            rec = read_record(owner.layout(key))
            if rec is not None:
                sandboxes.add(rec.sandbox)
                if rec.create_started and not rec.created:
                    uncertain.append(f"session {key} generation {rec.generation}: its create is unresolved")
                kept = [g for g in range(1, rec.generation + 1)
                        if owner.c.tmux.has_session(owner.reaper_name(key, g))]
                if kept:
                    uncertain.append(f"session {key}: the watchers of generations {kept} are still running")
        except Exception as exc:  # noqa: BLE001 - every session is tried; each failure is reported
            uncertain.append(f"session {key}: {type(exc).__name__}: {exc}")
    try:
        left = sorted(sandboxes & listed())
    except BackendUnavailable as exc:
        uncertain.append(f"the sandbox list: {exc}")
    else:
        if left:
            uncertain.append(f"sandboxes still listed: {', '.join(left)}")
    return uncertain


def gateway_listener(ss: str, port: int, proc: Path = Path("/proc"), uid: int | None = None) -> int:
    """The PID of the one process listening on the gateway's endpoint, from `ss -ltnpeH sport = :PORT`.
    Every listener on the port counts, on any address: exactly one is allowed, on 127.0.0.1:PORT, naming
    exactly one PID, run by this user (`uid`, else ours) and holding that socket's inode. Anything else
    raises ValueError."""
    rows = [line.split() for line in ss.splitlines() if line.strip()]
    if len(rows) != 1:
        raise ValueError(f"{len(rows)} listeners on port {port}")
    row = rows[0]
    if len(row) < 4 or row[3] != f"127.0.0.1:{port}":
        raise ValueError(f"the listener on port {port} is not on 127.0.0.1")
    users = next((field for field in row if field.startswith("users:")), "")
    pids = set(re.findall(r"pid=(\d+)", users))
    if len(pids) != 1:
        raise ValueError(f"the listener on port {port} names {len(pids)} processes")
    inode = next((field.removeprefix("ino:") for field in row if field.startswith("ino:")), "")
    if not inode.isdigit():
        raise ValueError(f"the listener on port {port} shows no socket inode")
    pid = int(pids.pop())
    ids_line = next(line for line in (proc / str(pid) / "status").read_text().splitlines()
                    if line.startswith("Uid:"))
    if set(ids_line.split()[1:]) != {str(os.getuid() if uid is None else uid)}:
        raise ValueError(f"the listener on port {port} is another user's process")
    if f"socket:[{inode}]" not in {str(fd.readlink()) for fd in (proc / str(pid) / "fd").iterdir()}:
        raise ValueError(f"process {pid} does not hold the listener on port {port}")
    return pid
