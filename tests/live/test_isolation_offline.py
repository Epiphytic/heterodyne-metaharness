"""Offline checks of the live harness's isolation: no network, no wn-agent, no dolt, so these run in the
normal suite. A parent environment full of production-looking values must not reach any child, and a
child environment that carries one must be refused before anything is spawned.

No production path is opened: the hostile values are only strings.
"""

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

import harness
import pytest
from harness import LiveError, Procs, check_child_env, child_env

from heterodyne.config import paths

PORT = 41234


def hostile(real: Path) -> dict[str, str]:
    """Values a production shell could carry: the real btq policy, config and endpoint, the real
    heterodyne dirs, the real HOME and XDG dirs, and bd/beads settings for the shared server."""
    return {
        "BTQ_POLICY": str(real / ".config/beads-task-queue/policy.json"),
        "BTQ_CONFIG_DIR": str(real / ".config/beads-task-queue"),
        "BTQ_REPO": str(real / "repos/beads-task-queue"),
        "BTQ_DOLT_PORT": "3307", "BTQ_DOLT_DATABASE": "tasks",
        "BTQ_CREDENTIALS": str(real / ".config/beads-task-queue/credentials.json"),
        "BTQ_TLS_CERT": str(real / ".config/beads-task-queue/server.crt"),
        "HETERODYNE_CONFIG_DIR": str(real / ".config/heterodyne"),
        "HETERODYNE_STATE_DIR": str(real / ".local/state/heterodyne"),
        "HOME": str(real), "XDG_CONFIG_HOME": str(real / ".config"),
        "XDG_STATE_HOME": str(real / ".local/state"),
        "XDG_DATA_HOME": str(real / ".local/share"),
        "BEADS_DOLT_SERVER_PORT": "3307", "BEADS_DOLT_SERVER_DATABASE": "tasks",
        "BEADS_DIR": str(real / ".hermes/.beads"), "BEADS_DOLT_PASSWORD": "not-a-real-password",
        "BEADS_DOLT_CREDENTIAL_COMMAND": "/bin/true", "TMUX": str(real / "tmux-sock,1,0"),
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "hzlive-test"
    r.mkdir()
    return r


@pytest.fixture
def parent(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values = hostile(harness.REAL_HOME)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_child_env_ignores_hostile_parent(root: Path, parent: dict[str, str]) -> None:
    """With production values in the parent environment, the built child environment holds none of them:
    every location is under the temp root, the endpoint is the private port and database, and no key
    outside the allowlist (BEADS_*, TMUX, BTQ_CREDENTIALS, ...) is inherited."""
    env = child_env(root, PORT)
    assert set(env) <= harness.CHILD_KEYS
    assert not set(env.values()) & set(parent.values())
    for key in harness.PATH_KEYS:
        if key in env:
            assert Path(env[key]).is_relative_to(root), key
    assert (env["BTQ_DOLT_PORT"], env["BTQ_DOLT_DATABASE"]) == (str(PORT), harness.DATABASE)
    assert not any(k.startswith("BEADS_") for k in env)
    assert paths.config_dir(env).is_relative_to(root) and paths.state_dir(env).is_relative_to(root)
    check_child_env(root, env)      # and the guard accepts it


# A password is not a location or an endpoint, so the guard cannot judge it; child_env never inherits
# it (test_child_env_ignores_hostile_parent).
@pytest.mark.parametrize("key", sorted(set(hostile(Path("/x"))) - {"BEADS_DOLT_PASSWORD"}))
def test_guard_refuses_each_hostile_value(root: Path, parent: dict[str, str], key: str) -> None:
    """A child environment that carries any one production value (merged over the built one) is refused
    before anything is spawned."""
    env = dict(child_env(root, PORT), **{key: parent[key]})
    with pytest.raises(LiveError, match="isolation guard"):
        check_child_env(root, env)


def test_procs_refuse_before_spawning(root: Path, parent: dict[str, str]) -> None:
    """`Procs.start` and `Procs.run` check the environment first: a hostile one starts nothing."""
    procs = Procs(root)
    env = dict(child_env(root, PORT), BTQ_DOLT_PORT="3307")
    marker = root / "spawned"
    argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]
    with pytest.raises(LiveError):
        procs.run(argv, env, timeout=10)
    with pytest.raises(LiveError):
        procs.start("x", argv, env, root / "log")
    assert not marker.exists() and procs.started == []


def test_forbidden_locations_ignore_home(parent: dict[str, str]) -> None:
    """The production locations the guard protects come from the password database, not $HOME, so a
    redirected HOME cannot move them; the guard's self-check refuses each of them."""
    assert harness.REAL_HOME == Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert harness.REAL_HOME / ".hermes" in harness.FORBIDDEN
    harness.self_check(Path("/tmp/hzlive-selfcheck"))  # noqa: S108 - a path only, never created


@pytest.mark.skipif(not harness.BTQ_SCRIPT.is_file(), reason="needs a beads-task-queue checkout (read only)")
def test_btq_resolves_private_locations(root: Path, parent: dict[str, str]) -> None:
    """btq's own `locations()` and policy path, evaluated in a child under the built environment while
    the parent holds production values, all resolve under the temp root and to the private endpoint.
    bin/btq is imported, never run; it opens nothing at import."""
    env = child_env(root, PORT)
    code = ("import importlib.machinery, importlib.util, json, os, sys\n"
            "loader = importlib.machinery.SourceFileLoader('btq', sys.argv[1])\n"
            "spec = importlib.util.spec_from_loader('btq', loader)\n"
            "mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)\n"
            "loc = mod.locations(); loc['policy'] = os.environ.get('BTQ_POLICY', '')\n"
            "print(json.dumps(loc))\n")
    out = subprocess.run([sys.executable, "-c", code, str(harness.BTQ_SCRIPT)], env=env, capture_output=True,
                         text=True, timeout=30, check=True)
    loc = json.loads(out.stdout)
    for name in ("config_dir", "repo", "credentials", "tls_cert", "policy"):
        assert Path(loc[name]).is_relative_to(root), name
    assert (loc["dolt_port"], loc["dolt_database"]) == (str(PORT), harness.DATABASE)
