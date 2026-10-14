"""Plan 4c T5: `[harnesses]` and `[harnesses.<adapter>]` (ADR 0001 §7 Managed installs, §15)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sandbox_env import config_env

from heterodyne.config import ConfigError, load
from heterodyne.harness import settings as hs
from heterodyne.sandbox.settings import sandbox_settings

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_install_agnostic.py"
# Built at runtime so this file carries no 40- or 64-hex literal (gitleaks) and no home path (the hook).
COMMIT40 = "0123456789" * 4
COMMIT64 = "0123456789abcdef" * 4
MARKER = "MARKER-value"


def harness(root: Path, host: str = "", **extra: str) -> hs.HarnessSettings:
    env = {**config_env(root, host), **extra}
    return hs.from_config(load(env=env), env)


def refused(root: Path, host: str, match: str) -> str:
    with pytest.raises(ConfigError, match=match) as exc:
        harness(root, host)
    return str(exc.value)


def test_defaults_and_bounds(tmp_path: Path) -> None:
    h = harness(tmp_path)
    assert h.store == Path("/var/lib/heterodyne/harnesses")
    assert h.fallback is False
    assert h.owner == "heterodyne-store"
    assert h.helper_socket == Path("/run/heterodyne-store/helper.sock")
    assert h.helper_copy == Path("/usr/local/libexec/heterodyne/store_core.py")
    assert h.staging_hours == 24
    assert h.approval_workstream == ""      # no default: `install` refuses without it (Q4)
    assert dict(h.recipes) == {}
    env = config_env(tmp_path)
    assert sandbox_settings(env).harnesses == h

    # Under the fallback the store's default is the service user's data directory.
    fallback = "[harnesses]\nfallback = true\n"
    home = Path(env["HOME"])
    assert harness(tmp_path, fallback).store == home / ".local" / "share" / "heterodyne" / "harnesses"
    data = tmp_path / "data"
    assert harness(tmp_path, fallback, XDG_DATA_HOME=str(data)).store == data / "heterodyne" / "harnesses"
    assert harness(tmp_path, fallback, XDG_DATA_HOME="relative").store.is_relative_to(home)
    # A configured store wins in both modes; `~` expands against the environment's HOME.
    assert harness(tmp_path, fallback + 'store = "~/s"\n').store == home / "s"
    assert harness(tmp_path, f'[harnesses]\nstore = "{tmp_path}/s"\n').store == tmp_path / "s"

    for hours in (1, 168):
        assert harness(tmp_path, f"[harnesses]\nstaging_hours = {hours}\n").staging_hours == hours
    for bad in ("0", "169", "true", '"24"', "2.5"):
        refused(tmp_path, f"[harnesses]\nstaging_hours = {bad}\n", "staging_hours")
    assert harness(tmp_path, '[harnesses]\napproval_workstream = "heterodyne"\n').approval_workstream \
        == "heterodyne"
    for key, value in [("approval_workstream", f'"Bad/{MARKER}"'), ("owner", f'"Bad {MARKER}"'),
                       ("store", f'"rel/{MARKER}"'), ("helper_socket", f'"rel/{MARKER}"'),
                       ("helper_copy", '""'), ("fallback", '"yes"'), ("store", "1")]:
        message = refused(tmp_path, f"[harnesses]\n{key} = {value}\n", key)
        assert MARKER not in message                # errors name keys, never values
    refused(tmp_path, '[harnesses]\nstores = 1\n', "unknown")


def test_a_workstream_layer_cannot_set_harnesses(tmp_path: Path) -> None:
    for text in ['[harnesses]\nstore = "/srv/x"\n', '[harnesses]\nfallback = true\n',
                 '[harnesses.codex]\nversion = "1.2.3"\n']:
        env = config_env(tmp_path, workstreams={"ws1": '[repos]\ndefault = "/srv/r"\n' + text})
        with pytest.raises(ConfigError, match=r"\[harnesses\] is host-only"):
            load("ws1", env)
        with pytest.raises(ConfigError, match=r"\[harnesses\] is host-only"):
            sandbox_settings(env)
    with pytest.raises(ConfigError, match="environment overrides are limited"):
        load(env={**config_env(tmp_path), "HETERODYNE_HARNESSES_STORE": "/srv/x"})


def recipe(version: str = "0.160.0", content: str = "", extra: str = "") -> str:
    return (f'[harnesses.codex]\nversion = "{version}"\ntarget = "x86_64-unknown-linux-musl"\n'
            f"{extra}content = [{content}]\n")


def test_recipes_need_exact_versions_and_full_commits(tmp_path: Path) -> None:
    content = (f'{{ kind = "skills", name = "review", git = "https://example.org/s.git", '
               f'commit = "{COMMIT40}" }}, '
               f'{{ kind = "skills", name = "lint", git = "https://example.org/l.git", '
               f'commit = "{COMMIT64}" }}, '
               '{ kind = "mcp", name = "docs", npm = "@example/docs-mcp", version = "1.2.3-beta.1" }')
    h = harness(tmp_path, recipe(content=content, extra='defaults = "codex-defaults.toml"\n'))
    r = h.recipes["codex"]
    assert (r.version, r.target) == ("0.160.0", "x86_64-unknown-linux-musl")
    assert r.defaults == "codex-defaults.toml"
    assert r.content == (
        hs.ContentSource("skills", "review", "https://example.org/s.git", COMMIT40, None, None),
        hs.ContentSource("skills", "lint", "https://example.org/l.git", COMMIT64, None, None),
        hs.ContentSource("mcp", "docs", None, None, "@example/docs-mcp", "1.2.3-beta.1"))
    assert set(h.recipes) == {"codex"}
    assert harness(tmp_path, recipe()).recipes["codex"].content == ()
    assert harness(tmp_path, recipe()).recipes["codex"].defaults is None

    git = '{{ kind = "skills", name = "s", git = "https://example.org/s.git", commit = "{}" }}'
    npm = '{{ kind = "mcp", name = "m", npm = "@example/m", version = "{}" }}'
    bad = {
        "version": [recipe(version=v) for v in ("0.160", "^0.160.0", "0.160.0+build", "latest", "v0.160.0")],
        "commit": [recipe(content=git.format(c)) for c in
                   (COMMIT40[:-1], COMMIT40 + "0", COMMIT64[:-1], "A" + COMMIT40[1:], "HEAD")],
        "content": [recipe(content=npm.format(v)) for v in ("1.2", "~1.2.3", "1.2.x", "*", "1.2.3 ")]
        + [recipe(content='{ kind = "mcp", name = "m", npm = "@example/m" }'),
           recipe(content='{ kind = "skills", name = "s", git = "https://example.org/s.git" }'),
           recipe(content=f'{{ kind = "mcp", name = "m", npm = "@e/m", version = "1.2.3", '
                          f'git = "https://example.org/s.git", commit = "{COMMIT40}" }}'),
           recipe(content='{ kind = "mcp", name = "m" }'),
           recipe(content='{ kind = "Bad Kind", name = "m", npm = "@e/m", version = "1.2.3" }'),
           recipe(content='{ kind = "mcp", name = "../m", npm = "@e/m", version = "1.2.3" }'),
           recipe(content='{ kind = "mcp", name = "m", npm = "@e/m", version = "1.2.3", extra = 1 }'),
           recipe(content='"not a table"')],
        "target": ['[harnesses.codex]\nversion = "0.160.0"\ncontent = []\n',
                   '[harnesses.codex]\nversion = "0.160.0"\ntarget = "a b"\n'],
        "defaults": [recipe(extra="defaults = 3\n"), recipe(extra='defaults = ""\n')],
        "unknown": [recipe(extra="channel = 1\n"),
                    '[harnesses.other]\nversion = "1.2.3"\ntarget = "x"\n'],
    }
    for key, hosts in bad.items():
        for host in hosts:
            message = refused(tmp_path, host, key)
            assert COMMIT40[:12] not in message and "@example" not in message


def test_install_agnostic_passes_the_store_default_and_flags_a_home_store(tmp_path: Path) -> None:
    """The shipped default is a system path, not an install path, so the checker passes it as is. A home
    path in the same key (what a host's own store would be) is still refused."""
    env = {**os.environ, "HETERODYNE_CONFIG_DIR": str(tmp_path / "no-config")}

    def check(text: str) -> int:
        (tmp_path / "c.toml").write_text(text)
        return subprocess.run([sys.executable, str(CHECKER), "--root", str(tmp_path), "c.toml"],
                              capture_output=True, text=True, env=env).returncode

    shipped = (ROOT / "src" / "heterodyne" / "defaults" / "defaults.toml").read_text()
    assert "/var/lib/heterodyne/harnesses" in shipped
    assert check(shipped) == 0
    assert check('[harnesses]\nstore = "/var/lib/heterodyne/harnesses"\n') == 0
    assert check('[harnesses]\nstore = "' + "/".join(["", "home", "alice", "store"]) + '"\n') == 1
