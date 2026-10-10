"""Sandbox test helpers: a scratch config directory, and a bounded wait on a condition."""

import base64
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from fakes.fake_backend import FakeBackend
from fakes.scripted_selftest import ScriptedSelfTest
from wsd_env import Clock, accounts_at, git_repo, login_home

from heterodyne.sandbox.runtime import RuntimeConfig, SandboxRuntime
from heterodyne.sandbox.settings import sandbox_settings
from heterodyne.tmux import Tmux
from heterodyne.wsd import ids
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


def fresh_logins(home: Path, expires_at: int) -> None:
    """Fake default logins that expire at `expires_at` (epoch seconds). Obvious fakes, no credential."""
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".codex").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"expiresAt": expires_at * 1000, "accessToken": "fake-not-a-token"}}))
    (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": fake_jwt(expires_at)}}))


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
        sessions=root / "s", settings=sandbox_settings(env), accounts=accounts_at(home), real_home=home,
        real_home_canary=root / "canary", wsd_socket=root / "ctl.sock", uid=os.getuid(), gid=os.getgid(),
        tmux=tmux, path=os.pathsep.join((str(claude.parent), str(codex.parent))), clock=clock)
    runtime = SandboxRuntime(config, backend, selftest)
    repo, worktree = git_repo(root / "r"), root / "w"          # a linked worktree, as btq makes them
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "btq/btq-1", str(worktree)],
                   check=True, capture_output=True)
    return RuntimeRig(root, runtime, backend, selftest, tmux, clock, worktree, home, repo)


def launch_spec(rig: RuntimeRig, profile: str, bead: str = "btq-1", *, account: str = "default",
                resume: bool = False) -> LaunchSpec:
    """A first launch, as plan 3's guard builds it: Claude's native ID is the session key, Codex's is
    unknown until its thread starts."""
    key = ids.role_session(bead, "coder", profile)
    native = key if profile == "p-one" else None
    return LaunchSpec("alpha", bead, "coder", profile, key, f"{bead} · coder", rig.worktree, resume=resume,
                      native_id=native, account=account, repo=rig.repo)
