import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import harness_env as he
import pytest
from sandbox_env import fake_jwt, install_fake_cli, short_dir

from heterodyne.agents import codex as codex_mod
from heterodyne.agents import config_files
from heterodyne.agents.base import (
    DEFAULTS_MALFORMED,
    HOOK_COMMAND,
    HOOK_EVENTS,
    OWNS_KEY,
    PRE_TOOL_COMMAND,
    PRE_TOOL_TIMEOUT,
    AdapterError,
    find_binary,
    hook_group,
    install_root,
    profile_args,
    write_at,
)
from heterodyne.agents.claude_code import CLAUDE_HOOK_EVENTS, SETTINGS_FILE, ClaudeCode, interactive_argv
from heterodyne.agents.codex import APP_SOCKET, Codex
from heterodyne.agents.registry import ADAPTERS, login_names
from heterodyne.config import ConfigError
from heterodyne.config import profile_args as pa
from heterodyne.sandbox.spec import BRIDGE_INSIDE, LAUNCHER_ENV, RUN_INSIDE, SessionLayout

ID = "0b1d8c5e-3f7a-4c2d-9e6b-7a8f9c0d1e2f"
RESOURCES = Path(__file__).resolve().parents[1] / "src" / "heterodyne" / "sandbox" / "resources"
TRUST = RESOURCES / "codex_trust.py"


@pytest.fixture
def layout() -> Iterator[SessionLayout]:
    with short_dir() as root:                   # the bridge holds a socket: keep its path short
        layout = SessionLayout(root / "hz0123456789ab")
        layout.home.mkdir(parents=True)
        yield layout


def test_registry_names_both_adapters() -> None:
    assert set(ADAPTERS) == {"claude-code", "codex"}
    assert all(ADAPTERS[name].name == name for name in ADAPTERS)
    assert (ADAPTERS["claude-code"].assigns_id, ADAPTERS["codex"].assigns_id) == (False, True)


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_locate_reads_the_version_without_running_the_cli(tmp_path: Path, adapter: str) -> None:
    binary = install_fake_cli(tmp_path, adapter)
    cli = ADAPTERS[adapter].locate(binary.name, str(binary.parent))
    pin = ADAPTERS[adapter].version_pin
    assert (cli.binary, cli.root, cli.version) == (binary, binary.parent.parent, pin)


def test_locate_refuses_a_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="not found"):
        ClaudeCode().locate("claude", str(tmp_path))


def test_claude_home_merges_its_state_and_hooks_are_read_only(layout: SessionLayout, tmp_path: Path) -> None:
    conf = layout.home / ".claude"
    conf.mkdir()
    (conf / ".claude.json").write_text(json.dumps({"numStartups": 7, "projects": {"/elsewhere": {}}}))
    ClaudeCode().prepare_home(layout, tmp_path / "wt")
    state = json.loads((conf / ".claude.json").read_text())
    assert state["numStartups"] == 7 and state["hasCompletedOnboarding"] is True
    assert state["projects"][str(tmp_path / "wt")]["hasTrustDialogAccepted"] is True
    assert not (conf / "settings.json").exists()           # user settings are not loaded: see tui_argv
    flag = json.loads(ClaudeCode().run_files()[SETTINGS_FILE])
    assert flag["skipDangerousModePermissionPrompt"] is True
    assert set(flag["hooks"]) == set(CLAUDE_HOOK_EVENTS) == {*HOOK_EVENTS, "StopFailure"}
    assert {e: [h["command"] for g in gs for h in g["hooks"]] for e, gs in flag["hooks"].items()} == {
        e: [PRE_TOOL_COMMAND if e == "PreToolUse" else HOOK_COMMAND] for e in CLAUDE_HOOK_EVENTS}


def test_claude_drops_a_cached_remote_policy_from_the_home(layout: SessionLayout, tmp_path: Path) -> None:
    """Claude reads remote managed settings (policy, above our hooks) from a cache in its config dir,
    which the agent can write: each launch removes it, a link included, without following it."""
    conf = layout.home / ".claude"
    conf.mkdir()
    (conf / "remote-settings.json").write_text(json.dumps({"disableAllHooks": True}))
    ClaudeCode().prepare_home(layout, tmp_path / "wt")
    assert not (conf / "remote-settings.json").exists()
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (conf / "remote-settings.json").symlink_to(victim)
    ClaudeCode().prepare_home(layout, tmp_path / "wt")
    assert not (conf / "remote-settings.json").is_symlink() and victim.read_text() == "keep"


def test_codex_leaves_the_worktree_untrusted_so_its_project_config_is_not_loaded(
        layout: SessionLayout, tmp_path: Path) -> None:
    """codex-cli 0.160 loads a trusted project's .codex/config.toml, which can turn hooks off, override
    the approval policy or trust its own hooks; for an untrusted project it loads none of it, and our
    user-level approval_policy and sandbox_mode still apply (checked offline with app-server config/read)."""
    Codex().prepare_home(layout, tmp_path / "wt")
    config = tomllib.loads((layout.home / ".codex" / "config.toml").read_text())
    assert config["projects"][str(tmp_path / "wt")] == {"trust_level": "untrusted"}
    assert config["approval_policy"] == "never" and config["sandbox_mode"] == "danger-full-access"
    hooks = json.loads((layout.home / ".codex" / "hooks.json").read_text())["hooks"]
    assert {e: [h["command"] for g in gs for h in g["hooks"]] for e, gs in hooks.items()} == {
        e: [PRE_TOOL_COMMAND if e == "PreToolUse" else HOOK_COMMAND] for e in HOOK_EVENTS}


def test_only_pre_tool_use_gets_the_blocking_wrapper_and_a_timeout() -> None:
    """A timed-out hook is cancelled and its tool runs (Claude 2.1.286, Codex 0.160): the timeout must
    sit well above the shim's own deadline, which settings caps at 60 seconds."""
    (pre,) = hook_group("PreToolUse")["hooks"]
    assert pre == {"type": "command", "command": PRE_TOOL_COMMAND, "timeout": PRE_TOOL_TIMEOUT}
    assert PRE_TOOL_TIMEOUT >= 2 * 60
    for event in ("SessionStart", "UserPromptSubmit", "Stop"):
        assert hook_group(event)["hooks"] == [{"type": "command", "command": HOOK_COMMAND}]


@pytest.mark.parametrize("stub, code", [
    (None, 2),                                          # no interpreter at all
    ("kill -9 $$", 2),                                  # killed
    ("exit 1", 2),                                      # crashed
    ("exit 75", 2),                                     # any other failure
    ("echo '{\"decision\": 1}'; exit 0", 0),           # an answer: passed through as it is
])
def test_the_pre_tool_use_wrapper_turns_every_failure_into_a_block(tmp_path: Path, stub: str | None,
                                                                   code: int) -> None:
    """Exit 2 is the only failure Claude and Codex treat as blocking for PreToolUse; any other nonzero
    exit lets the tool run under bypass mode."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sh").symlink_to("/bin/sh")
    if stub is not None:
        (bin_dir / "python3").write_text(f"#!/bin/sh\n{stub}\n")
        (bin_dir / "python3").chmod(0o755)
    proc = subprocess.run(["/bin/sh", "-c", PRE_TOOL_COMMAND], env={"PATH": str(bin_dir)}, input=b"{}",
                          capture_output=True, timeout=60, check=False)
    assert proc.returncode == code
    if code == 2:
        assert b"PreToolUse hook could not run" in proc.stderr
    else:
        assert json.loads(proc.stdout) == {"decision": 1}


def test_codex_home_is_rewritten_and_stale_sockets_go(layout: SessionLayout, tmp_path: Path) -> None:
    Codex().prepare_home(layout, tmp_path / "wt")
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(layout.bridge / "app.sock"))
    stale.close()
    (layout.home / ".codex" / "config.toml").write_text("model = 'x'\n")
    Codex().prepare_home(layout, tmp_path / "wt")
    config = tomllib.loads((layout.home / ".codex" / "config.toml").read_text())
    assert config == {"approval_policy": "never", "sandbox_mode": "danger-full-access",
                      "check_for_update_on_startup": False,
                      "projects": {str(tmp_path / "wt"): {"trust_level": "untrusted"}},
                      "features": {"apps": False, "daemon_auto_start": False, "plugins": False,
                                   "in_app_updates": False}}
    assert not (layout.bridge / "app.sock").exists()
    assert layout.daemon.stat().st_mode & 0o777 == 0o700
    hooks = json.loads((layout.home / ".codex" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == set(HOOK_EVENTS)


def test_codex_facts_bind_the_bridge_and_daemon(layout: SessionLayout, tmp_path: Path) -> None:
    binary = install_fake_cli(tmp_path, "codex")
    facts = Codex().facts(layout, Codex().locate("codex", str(binary.parent)), 1234)
    assert [(b.source, str(b.target), b.read_only) for b in facts.binds] == [
        (layout.bridge, str(BRIDGE_INSIDE), False),
        (layout.daemon, "/tmp/codex-daemon-1234", False)]  # noqa: S108
    assert facts.config_var == "CODEX_HOME" and facts.model_hosts == ("chatgpt.com",)


def test_state_is_found_where_each_cli_keeps_it(layout: SessionLayout) -> None:
    for adapter in (ClaudeCode(), Codex()):
        assert not adapter.has_state(layout.home, ID)
    (layout.home / ".claude" / "projects" / "-w").mkdir(parents=True)
    (layout.home / ".claude" / "projects" / "-w" / f"{ID}.jsonl").write_text("")
    rollouts = layout.home / ".codex" / "sessions" / "2026" / "10" / "09"
    rollouts.mkdir(parents=True)
    (rollouts / f"rollout-2026-10-09T00-00-00-{ID}.jsonl").write_text("")
    for adapter in (ClaudeCode(), Codex()):
        assert adapter.has_state(layout.home, ID)
        assert not adapter.has_state(layout.home, None)
        with pytest.raises(AdapterError, match="native ID"):
            adapter.has_state(layout.home, "*")


STATE = {"claude-code": (".claude/projects", "-w", f"{ID}.jsonl"),
         "codex": (".codex/sessions", "2026/10/09", f"rollout-2026-10-09T00-00-00-{ID}.jsonl")}


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_state_is_only_a_regular_file_reached_without_links(layout: SessionLayout, tmp_path: Path,
                                                             adapter: str) -> None:
    """The home is the agent's: a link below the config dir, or a directory with a state file's name,
    must not count as resumable state."""
    base, rel, name = STATE[adapter]
    outside = tmp_path / "outside" / rel
    outside.mkdir(parents=True)
    (outside / name).write_text("")
    parent = (layout.home / base / rel).parent
    parent.mkdir(parents=True)
    (layout.home / base / rel).symlink_to(outside)                       # a linked directory on the way
    assert not ADAPTERS[adapter].has_state(layout.home, ID)
    (layout.home / base / rel).unlink()
    (layout.home / base / rel).mkdir()
    (layout.home / base / rel / name).symlink_to(outside / name)         # a linked state file
    assert not ADAPTERS[adapter].has_state(layout.home, ID)
    (layout.home / base / rel / name).unlink()
    (layout.home / base / rel / name).mkdir()                            # a directory with its name
    assert not ADAPTERS[adapter].has_state(layout.home, ID)


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_a_linked_state_root_refuses_the_home(layout: SessionLayout, tmp_path: Path, adapter: str) -> None:
    base, rel, name = STATE[adapter]
    (tmp_path / "outside" / rel).mkdir(parents=True)
    (tmp_path / "outside" / rel / name).write_text("")
    (layout.home / base).parent.mkdir(parents=True)
    (layout.home / base).symlink_to(tmp_path / "outside")
    with pytest.raises(AdapterError, match="won't follow"):
        ADAPTERS[adapter].has_state(layout.home, ID)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_an_unreadable_state_directory_is_an_error_not_no_state(layout: SessionLayout, adapter: str) -> None:
    base, rel, name = STATE[adapter]
    blocked = layout.home / base / rel.split("/")[0]
    (layout.home / base / rel).mkdir(parents=True)
    (layout.home / base / rel / name).write_text("")
    blocked.chmod(0)
    try:
        with pytest.raises(OSError):
            ADAPTERS[adapter].has_state(layout.home, ID)
    finally:
        blocked.chmod(0o700)


def test_access_expiry(tmp_path: Path) -> None:
    claude, codex = tmp_path / ".credentials.json", tmp_path / "auth.json"
    claude.write_text(json.dumps({"claudeAiOauth": {"expiresAt": 1_900_000_000_000}}))
    codex.write_text(json.dumps({"tokens": {"access_token": fake_jwt(1_900_000_000)}}))
    assert ClaudeCode().access_expiry([claude]) == 1_900_000_000
    assert Codex().access_expiry([codex]) == 1_900_000_000
    for adapter, path in ((ClaudeCode(), claude), (Codex(), codex)):
        path.write_text("{}")
        with pytest.raises(AdapterError, match="expiry"):
            adapter.access_expiry([path])


def test_tui_argv(tmp_path: Path) -> None:
    claude = ClaudeCode().locate("claude", str(install_fake_cli(tmp_path, "claude-code").parent))
    codex = Codex().locate("codex", str(install_fake_cli(tmp_path, "codex").parent))
    first = ClaudeCode().tui_argv(claude, {"model": "m-1"}, native_id=ID, resume=False, label="bd-1 · coder")
    assert first[1:3] == ["--session-id", ID] and first[-2:] == ["--name", "bd-1 · coder"]
    assert str(RUN_INSIDE / SETTINGS_FILE) in first
    assert "--setting-sources=" in first and "" not in first     # no user, project or local settings
    assert ClaudeCode().tui_argv(claude, {}, native_id=ID, resume=True, label="x")[1:3] == ["--resume", ID]
    assert Codex().tui_argv(codex, {}, native_id=None, resume=False, label="x") == [
        str(codex.binary), "--remote", APP_SOCKET, "--dangerously-bypass-approvals-and-sandbox"]
    pinned = {"model": "m-2", "args": []}
    assert Codex().tui_argv(codex, pinned, native_id=None, resume=False, label="x") == [
        str(codex.binary), "--remote", APP_SOCKET, "--dangerously-bypass-approvals-and-sandbox",
        "--model", "m-2"]
    # A remote resume takes no permission override (0.160.0 refuses one): the thread keeps its own policy.
    assert Codex().tui_argv(codex, {"model": "m-2"}, native_id=ID, resume=True, label="x") == [
        str(codex.binary), "resume", ID, "--remote", APP_SOCKET, "--model", "m-2"]
    with pytest.raises(AdapterError):
        Codex().tui_argv(codex, {}, native_id=None, resume=True, label="x")
    with pytest.raises(AdapterError):
        ClaudeCode().tui_argv(claude, {}, native_id=None, resume=False, label="x")


def run_lister(layout: SessionLayout, codex: Path, worktree: Path,
               fault: str | None = None) -> subprocess.CompletedProcess[bytes]:
    env = {"CODEX_HOME": str(layout.home / ".codex"), "PATH": "/usr/bin:/bin"}
    if fault is not None:
        env["HZ_FAKE_HOOKS_FAULT"] = fault
    return subprocess.run([sys.executable, "-I", str(TRUST), str(codex), str(worktree)], env=env,
                          capture_output=True, timeout=60, check=False)


def trust_listing(layout: SessionLayout, codex: Path, worktree: Path) -> bytes:
    proc = run_lister(layout, codex, worktree)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_a_linked_config_dir_refuses_the_home(layout: SessionLayout, tmp_path: Path, adapter: str) -> None:
    """The home persists and is the agent's: a link it plants between generations must not redirect a
    host write on the next launch."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (layout.home / ADAPTERS[adapter].config_subdir).symlink_to(outside)
    with pytest.raises(AdapterError, match="won't follow"):
        ADAPTERS[adapter].prepare_home(layout, tmp_path / "wt")
    with pytest.raises(AdapterError, match="won't follow"):
        ADAPTERS[adapter].has_state(layout.home, ID)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("adapter, name", [("claude-code", ".claude.json"),
                                           ("codex", "config.toml"), ("codex", "hooks.json")])
def test_linked_files_and_temp_names_are_replaced_not_followed(layout: SessionLayout, tmp_path: Path,
                                                               adapter: str, name: str) -> None:
    victim = tmp_path / "victim"
    victim.write_text('{"stolen": true}')
    conf = layout.home / ADAPTERS[adapter].config_subdir
    conf.mkdir()
    (conf / name).symlink_to(victim)
    (conf / f".{name}.tmp").symlink_to(victim)               # the old, predictable temporary name
    ADAPTERS[adapter].prepare_home(layout, tmp_path / "wt")
    assert victim.read_text() == '{"stolen": true}'
    assert not (conf / name).is_symlink() and "stolen" not in (conf / name).read_text()


def test_write_at_syncs_the_file_before_the_rename_and_the_directory_after(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        calls.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    def replace(src: str, dst: str, **dir_fds: int) -> None:
        calls.append("rename")
        real_replace(src, dst, **dir_fds)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        write_at(fd, "record.json", "{}")
    finally:
        os.close(fd)
    assert calls == ["file", "rename", "dir"] and (tmp_path / "record.json").read_text() == "{}"


def test_a_failed_sync_leaves_the_old_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "record.json").write_text("old")

    def broken(fd: int) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(os, "fsync", broken)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            write_at(fd, "record.json", "new")
    finally:
        os.close(fd)
    assert [q.name for q in tmp_path.iterdir()] == ["record.json"]
    assert (tmp_path / "record.json").read_text() == "old"


def test_trust_refuses_a_config_swapped_for_a_link(layout: SessionLayout, tmp_path: Path) -> None:
    codex = install_fake_cli(tmp_path, "codex")
    Codex().prepare_home(layout, tmp_path)
    listed = trust_listing(layout, codex, tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("keep\n")
    config = layout.home / ".codex" / "config.toml"
    config.unlink()
    config.symlink_to(victim)
    with pytest.raises(AdapterError, match="trust"):
        Codex().apply_trust(layout, listed)
    assert victim.read_text() == "keep\n"


def test_codex_run_files_ship_the_trust_lister() -> None:
    assert Codex().run_files()["codex_trust.py"] == TRUST.read_text()


def test_codex_trust_round_trip(layout: SessionLayout, tmp_path: Path) -> None:
    codex = install_fake_cli(tmp_path, "codex")
    Codex().prepare_home(layout, tmp_path)
    listed = trust_listing(layout, codex, tmp_path)
    assert len(json.loads(listed)) == len(HOOK_EVENTS)
    assert Codex().apply_trust(layout, listed) == len(HOOK_EVENTS)
    assert json.loads(trust_listing(layout, codex, tmp_path)) == []
    Codex().prepare_home(layout, tmp_path)            # a new launch rewrites config.toml: trust again
    assert len(json.loads(trust_listing(layout, codex, tmp_path))) == len(HOOK_EVENTS)


@pytest.mark.parametrize("entry", [
    {"key": "elsewhere/hooks.json:stop:0:0", "hash": "0123456789abcdef"},
    {"key": "HOOKS:stop:0:0\"]\nevil = 1", "hash": "0123456789abcdef"},
    {"key": "HOOKS:stop:0:0", "hash": "not-hex"},
    {"key": "HOOKS:stop:0:0"},
    "a string",
])
def test_apply_trust_refuses_anything_unexpected(layout: SessionLayout, tmp_path: Path,
                                                 entry: object) -> None:
    Codex().prepare_home(layout, tmp_path)
    hooks = str(layout.home / ".codex" / "hooks.json")
    text = json.dumps([entry]).replace("HOOKS", hooks.replace("\\", "\\\\"))
    before = (layout.home / ".codex" / "config.toml").read_text()
    with pytest.raises(AdapterError, match="trust"):
        Codex().apply_trust(layout, text.encode())
    assert (layout.home / ".codex" / "config.toml").read_text() == before


@pytest.mark.parametrize("fault", ["errors", "no-data", "empty-data", "other-cwd", "unknown-status",
                                   "missing-hook", "disabled", "other-command", "not-a-list"])
def test_the_trust_lister_refuses_a_listing_it_cannot_vouch_for(layout: SessionLayout, tmp_path: Path,
                                                                fault: str) -> None:
    """An empty answer is what the host reads as "every shim hook is trusted": a failed load, a listing
    for another directory, an unknown status, or a shim hook missing, disabled or changed must refuse
    the launch instead."""
    codex = install_fake_cli(tmp_path, "codex")
    Codex().prepare_home(layout, tmp_path)
    Codex().apply_trust(layout, trust_listing(layout, codex, tmp_path))   # all trusted: [] from here on
    assert json.loads(trust_listing(layout, codex, tmp_path)) == []
    proc = run_lister(layout, codex, tmp_path, fault)
    assert proc.returncode == 1 and proc.stdout == b""


def run_fake_tui(binary: Path, args: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run([str(binary), *args], input=b"hello\n", cwd=cwd, timeout=60, check=True,
                   capture_output=True, env={"PATH": "/usr/bin:/bin", **env})


def test_the_fake_codex_fires_only_hooks_it_trusts(tmp_path: Path) -> None:
    """As the real Codex does: an untrusted hook, or one whose command changed since it was trusted, does
    not run."""
    codex, home, out = install_fake_cli(tmp_path, "codex"), tmp_path / "codex-home", tmp_path / "out"
    home.mkdir()
    out.mkdir()
    commands = {name: f"cat > {out / name}" for name in ("trusted", "modified", "untrusted")}
    groups = [{"hooks": [{"type": "command", "command": c}]} for c in commands.values()]
    (home / "hooks.json").write_text(json.dumps({"hooks": {"UserPromptSubmit": groups}}))
    key = f"{home / 'hooks.json'}:user_prompt_submit:{{}}:0"

    def digest(command: str) -> str:
        return "sha256:" + hashlib.sha256(command.encode()).hexdigest()[:16]

    (home / "config.toml").write_text(
        f'[hooks.state."{key.format(0)}"]\ntrusted_hash = "{digest(commands["trusted"])}"\n'
        f'[hooks.state."{key.format(1)}"]\ntrusted_hash = "{digest("an older command")}"\n')
    run_fake_tui(codex, [], tmp_path, {"CODEX_HOME": str(home)})
    assert sorted(p.name for p in out.iterdir()) == ["trusted"]


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_the_fake_gives_each_hook_its_cwd(tmp_path: Path, adapter: str) -> None:
    """The shim resolves a relative file_path only against the hook's cwd, so the fake must send one."""
    binary, home, out = install_fake_cli(tmp_path, adapter), tmp_path / "home", tmp_path / "out.json"
    home.mkdir()
    work = tmp_path / "wt"
    work.mkdir()
    command = f"cat > {out}"
    hooks = {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": command}]}]}
    if adapter == "claude-code":
        (tmp_path / "settings.json").write_text(json.dumps({"hooks": hooks}))
        args, env = ["--session-id", ID, "--settings", str(tmp_path / "settings.json")], "CLAUDE_CONFIG_DIR"
    else:
        (home / "hooks.json").write_text(json.dumps({"hooks": hooks}))
        trusted = "sha256:" + hashlib.sha256(command.encode()).hexdigest()[:16]
        (home / "config.toml").write_text(
            f'[hooks.state."{home / "hooks.json"}:user_prompt_submit:0:0"]\ntrusted_hash = "{trusted}"\n')
        args, env = [], "CODEX_HOME"
    run_fake_tui(binary, args, work, {env: str(home)})
    assert json.loads(out.read_text())["cwd"] == str(work)


@pytest.mark.parametrize("planted", [
    {"disableAllHooks": True},
    {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "MARK"}]}]}},
])
def test_worktree_settings_neither_disable_nor_add_claude_hooks(layout: SessionLayout, tmp_path: Path,
                                                                planted: dict[str, object]) -> None:
    """The worktree is the agent's: its .claude/settings.json and settings.local.json, and the home's
    settings.json, must not turn our hooks off or add hooks of their own."""
    cli = ClaudeCode().locate("claude", str(install_fake_cli(tmp_path, "claude-code").parent))
    run, out, work = tmp_path / "run", tmp_path / "out", tmp_path / "wt"
    for folder in (run, out, work / ".claude", layout.home / ".claude"):
        folder.mkdir(parents=True)
    ours = json.loads(ClaudeCode().run_files()[SETTINGS_FILE])
    mine = {"type": "command", "command": f"touch {out / 'ours'}"}
    ours["hooks"] = {"UserPromptSubmit": [{"hooks": [mine]}]}
    (run / SETTINGS_FILE).write_text(json.dumps(ours))
    text = json.dumps(planted)
    for place in (work / ".claude" / "settings.json", work / ".claude" / "settings.local.json",
                  layout.home / ".claude" / "settings.json"):
        place.write_text(text.replace("MARK", f"touch {out / place.name}"))
    argv = ClaudeCode().tui_argv(cli, {}, native_id=ID, resume=False, label="x")
    run_fake_tui(Path(argv[0]), argv[1:], work, {"CLAUDE_CONFIG_DIR": str(layout.home / ".claude"),
                                                  "HZ_FAKE_PATHS": json.dumps({str(RUN_INSIDE): str(run)})})
    assert [p.name for p in out.iterdir()] == ["ours"]


def test_turn_end_events_per_adapter() -> None:
    assert ClaudeCode().turn_end_events == ("Stop", "StopFailure")
    assert Codex().turn_end_events == ("Stop",)
    assert "StopFailure" not in HOOK_EVENTS            # Codex's trusted hook set is unchanged


# --- plan 4c Task 6 (with Amendment 1 §4): declared versions, layouts, owned keys and updaters ---

def test_each_adapter_declares_its_s7_pin() -> None:
    assert set(Codex().versions) == {"0.160.0"} and Codex().version_pin == "0.160.0"
    assert set(ClaudeCode().versions) == {"2.1.286"} and ClaudeCode().version_pin == "2.1.286"


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_tool_env_is_per_declared_version(adapter: str) -> None:
    a = ADAPTERS[adapter]
    assert a.tool_env == a.versions[a.version_pin].tool_env
    assert all(d.presentation <= a.content_kinds for d in a.versions.values())


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_the_layouts_read_version_target_and_entry_point_from_fixture_releases(tmp_path: Path,
                                                                                adapter: str) -> None:
    a = ADAPTERS[adapter]
    if adapter == "codex":
        expected = ("github-release", "openai/codex", he.CODEX_LAYOUT, he.CODEX_STRIP)
        part, data = he.codex_part()
        version, target, entry = "0.160.0", "x86_64-unknown-linux-musl", "cli/bin/codex"
    else:
        expected = ("npm", "@anthropic-ai/claude-code-<target>", he.CLAUDE_LAYOUT, he.CLAUDE_STRIP)
        part, data = he.claude_part()
        version, target, entry = "2.1.286", he.CLAUDE_TARGET, "cli/claude"
    il = a.install_layout
    assert (il.artifact, il.locator, il.layout, il.strip) == expected
    staged = he.stage(tmp_path, il.layout, [(part, data)], adapter=adapter)
    m = he.staged_manifest(tmp_path, staged)
    assert (m.adapter, m.version, m.target, m.entry_point) == (adapter, version, target, entry)
    assert m.version in a.versions


def test_codex_names_its_release_assets() -> None:
    assert codex_mod.RELEASE_ASSET.format(target="x86_64-unknown-linux-musl") == \
        "codex-package-x86_64-unknown-linux-musl.tar.gz"
    assert codex_mod.RELEASE_SUMS == "codex-package_SHA256SUMS"


def test_registry_login_names_is_the_union_of_the_adapters() -> None:
    assert login_names() == frozenset({"auth.json", ".credentials.json"}) == he.LOGIN_NAMES
    assert login_names() == frozenset(n for a in ADAPTERS.values() for n in a.login_names)


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_the_old_lookup_still_works_and_agrees_with_the_declared_versions(tmp_path: Path,
                                                                           adapter: str) -> None:
    binary = install_fake_cli(tmp_path, adapter)
    cli = ADAPTERS[adapter].locate(binary.name, str(binary.parent))
    assert cli.version in ADAPTERS[adapter].versions
    assert (cli.install, cli.digest) == ("", "")
    assert install_root(find_binary(str(binary), "")) == cli.root


def install_with_defaults(tmp_path: Path, name: str, text: str) -> Path:
    install = tmp_path / "install"
    (install / "defaults").mkdir(parents=True)
    (install / "defaults" / name).write_text(text)
    return install


def owned_tree(owned: frozenset[str], value: object) -> dict[str, Any]:
    """A tree that sets every dotted key of `owned` to `value`."""
    tree: dict[str, Any] = {}
    for key in sorted(owned):
        *parents, leaf = key.split(".")
        here = tree
        for p in parents:
            here = here.setdefault(p, {})
        here[leaf] = value
    return tree


def at(tree: Mapping[str, Any], key: str) -> object:
    here: Any = tree
    for p in key.split("."):
        if not isinstance(here, dict) or p not in here:
            return "<absent>"
        here = cast(dict[str, Any], here)[p]
    return here


def codex_generated(layout: SessionLayout, tmp_path: Path, defaults: str | None) -> dict[str, Any]:
    install = install_with_defaults(tmp_path / ("d" if defaults else "n"), "config.toml", defaults or "")
    Codex().prepare_install_home(layout, tmp_path / "wt", install)
    return tomllib.loads((layout.home / ".codex" / "config.toml").read_text())


def toml_value(value: object) -> str:
    return json.dumps(value) if isinstance(value, str) else str(value).lower()


def test_defaults_that_set_every_owned_key_lose_to_the_launcher_codex(layout: SessionLayout,
                                                                       tmp_path: Path) -> None:
    owned = Codex().owned_keys
    assert {"approval_policy", "sandbox_mode", "check_for_update_on_startup", "features.apps",
            "features.daemon_auto_start", "features.plugins", "features.in_app_updates", "hooks", "projects",
            "profile", "profiles", "notify"} <= owned
    plain = codex_generated(layout, tmp_path, None)
    lines = ['check_for_update_on_startup = true', 'profile = "x"', 'approval_policy = "on-request"',
             'sandbox_mode = "workspace-write"', 'notify = ["sh", "-c", "x"]', 'model = "kept"',
             '[features]', 'apps = true', 'daemon_auto_start = true', 'plugins = true',
             'in_app_updates = true', 'other_feature = true',
             '[profiles.x.features]', 'plugins = true',
             '[projects."/elsewhere"]', 'trust_level = "trusted"',
             '[hooks.state."k"]', 'trusted_hash = "h"']
    merged = codex_generated(layout, tmp_path, "\n".join(lines) + "\n")
    for key in owned:
        assert at(merged, key) == at(plain, key), key
    assert "profile" not in merged and "profiles" not in merged and "notify" not in merged
    assert merged["check_for_update_on_startup"] is False
    assert merged["features"] == {"apps": False, "daemon_auto_start": False, "plugins": False,
                                  "in_app_updates": False, "other_feature": True}
    assert merged["projects"] == {str(tmp_path / "wt"): {"trust_level": "untrusted"}}
    assert merged["model"] == "kept"                         # a key the launcher doesn't own is kept


def test_defaults_that_set_every_owned_key_lose_to_the_launcher_claude(tmp_path: Path) -> None:
    owned = ClaudeCode().owned_keys
    assert {"hooks", "statusLine", "skipDangerousModePermissionPrompt", "env.DISABLE_AUTOUPDATER",
            "env.DISABLE_UPDATES", "env.ENABLE_CLAUDEAI_MCP_SERVERS", "disableAllHooks",
            "permissions.defaultMode", "enabledPlugins", "extraKnownMarketplaces", "sandbox"} <= owned
    plain = json.loads(ClaudeCode().run_files(usage=True)[SETTINGS_FILE])
    defaults = owned_tree(owned, "")
    defaults["permissions"]["allow"] = ["Read"]
    install = install_with_defaults(tmp_path, "settings.json", json.dumps(defaults))
    merged = json.loads(ClaudeCode().run_files(usage=True, install=install)[SETTINGS_FILE])
    for key in owned:
        assert at(merged, key) == at(plain, key), key
    assert merged["env"] == {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}
    assert merged["permissions"] == {"allow": ["Read"]}


# One install per owned key, setting only that key the way an install would turn it back on.
CODEX_SET = {
    "approval_policy": "on-request", "sandbox_mode": "workspace-write",
    "check_for_update_on_startup": True, "projects": {"/elsewhere": {"trust_level": "trusted"}},
    "notify": ["sh", "-c", "x"], "hooks": {"state": {"k": {"trusted_hash": "h"}}},
    "features.apps": True, "features.daemon_auto_start": True, "features.plugins": True,
    "features.in_app_updates": True, "profile": "x", "profiles": {"x": {"features": {"plugins": True}}},
    "mcp_servers.heterodyne": {"command": "sh", "args": ["-c", "x"]},
    # what the commands it runs see, and how it starts and confines them
    "shell_environment_policy": {"inherit": "all", "set": {"PATH": "/planted"}},
    "allow_login_shell": True, "zsh_path": "/planted/zsh",
    "sandbox_workspace_write": {"writable_roots": ["/"], "network_access": True},
    "default_permissions": "x", "permissions": {"x": {"network": {"proxy_url": "http://planted"}}},
    "js_repl_node_path": "/planted/node", "js_repl_node_module_dirs": ["/planted"],
    "browser_use": {"full_cdp_access": True}, "computer_use": {"enabled": True},
    "agents": {"r": {"config_file": "/planted.toml"}}, "orchestrator": {"x": "y"},
    # where its traffic and its logins go
    "model_provider": "x", "model_providers": {"x": {"base_url": "http://planted", "env_key": "K"}},
    "openai_base_url": "http://planted", "chatgpt_base_url": "http://planted", "oss_provider": "ollama",
    "experimental_realtime_ws_base_url": "ws://planted",
    "experimental_realtime_webrtc_call_base_url": "http://planted",
    "experimental_thread_store_endpoint": "http://planted", "experimental_thread_store": {"type": "x"},
    "otel": {"exporter": "x"}, "forced_login_method": "api", "forced_chatgpt_workspace_id": "w",
    "cli_auth_credentials_store": "keyring", "mcp_oauth_credentials_store": "file",
    "mcp_oauth_callback_port": 1, "mcp_oauth_callback_url": "http://planted",
    "mcp_enterprise_managed_auth": True,
    # the plugins it fetches and the state it keeps outside the home
    "plugins": {"p": {"enabled": True}}, "marketplaces": {"m": {"source": "x"}},
    "allow_symlinked_codex_home": True, "sqlite_home": "/planted", "log_dir": "/planted",
}
CLAUDE_SET = {
    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]},
    "statusLine": {"type": "command", "command": "x"}, "skipDangerousModePermissionPrompt": False,
    "disableAllHooks": True, "permissions.defaultMode": "default", "enabledPlugins": {"p@m": True},
    "extraKnownMarketplaces": {"m": {"source": {"source": "github", "repo": "x/y"}}},
    "sandbox": {"enabled": False}, "env.DISABLE_AUTOUPDATER": "0", "env.DISABLE_UPDATES": "0",
    "env.ENABLE_CLAUDEAI_MCP_SERVERS": "1",
    # the settings the CLI runs as commands (its own list of them in 2.1.286)
    "apiKeyHelper": "x", "awsAuthRefresh": "x", "awsCredentialExport": "x", "gcpAuthRefresh": "x",
    "otelHeadersHelper": "x", "proxyAuthHelper": "x", "processWrapper": "x", "policyHelpers": ["x"],
    "fileSuggestion": {"type": "command", "command": "x"},
    "subagentStatusLine": {"type": "command", "command": "x"},
    # the worktree's own .mcp.json servers, started without asking
    "enableAllProjectMcpServers": True, "enabledMcpjsonServers": ["s"],
}


def test_each_owned_key_has_an_install_case() -> None:
    assert set(CODEX_SET) == Codex().owned_keys
    assert set(CLAUDE_SET) == ClaudeCode().owned_keys


@pytest.mark.parametrize("key", sorted(CODEX_SET))
def test_an_install_that_sets_one_owned_key_is_overridden_codex(layout: SessionLayout, tmp_path: Path,
                                                                 key: str) -> None:
    plain = codex_generated(layout, tmp_path, None)
    merged = codex_generated(layout, tmp_path,
                             config_files.toml_text(owned_tree(frozenset({key}), CODEX_SET[key])))
    assert at(merged, key) == at(plain, key)
    assert at(merged, key) != CODEX_SET[key]


@pytest.mark.parametrize("key", sorted(CLAUDE_SET))
def test_an_install_that_sets_one_owned_key_is_overridden_claude(tmp_path: Path, key: str) -> None:
    plain = json.loads(ClaudeCode().run_files(usage=True)[SETTINGS_FILE])
    install = install_with_defaults(tmp_path, "settings.json",
                                    json.dumps(owned_tree(frozenset({key}), CLAUDE_SET[key])))
    merged = json.loads(ClaudeCode().run_files(usage=True, install=install)[SETTINGS_FILE])
    assert at(merged, key) == at(plain, key)
    assert at(merged, key) != CLAUDE_SET[key]


def test_the_launcher_hooks_run_python3_from_path() -> None:
    """Why PATH in an install's env matters: the hook command names python3, not a path to it."""
    hooks = json.loads(ClaudeCode().run_files()[SETTINGS_FILE])["hooks"]
    assert hooks["Stop"][0]["hooks"][0]["command"].startswith("python3 ")


@pytest.mark.parametrize("env", [
    "PATH",                             # a planted python3 runs instead of the launcher's hook
    "CLAUDE_CODE_SHELL_PREFIX",         # wraps every hook and tool command
    "CLAUDE_CODE_PLUGIN_DIRS",          # loads plugins that check_content never saw
    "HZ_NOT_A_CLAUDE_VARIABLE",         # and anything else: the allowlist is empty
])
def test_an_install_env_entry_is_refused(tmp_path: Path, env: str) -> None:
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "python3").write_text("#!/bin/sh\nexit 0\n")
    (planted / "python3").chmod(0o755)
    value = f"{planted}:/usr/bin:/bin" if env == "PATH" else str(planted)
    install = install_with_defaults(tmp_path, "settings.json", json.dumps({"env": {env: value}}))
    with pytest.raises(AdapterError) as refused:
        ClaudeCode().run_files(install=install)
    assert str(refused.value) == OWNS_KEY


def test_an_install_env_that_is_not_a_table_is_refused(tmp_path: Path) -> None:
    install = install_with_defaults(tmp_path, "settings.json", json.dumps({"env": ["PATH=/planted"]}))
    with pytest.raises(AdapterError) as refused:
        ClaudeCode().run_files(install=install)
    assert str(refused.value) == DEFAULTS_MALFORMED


def test_an_install_env_of_only_launcher_keys_is_overridden_not_refused(tmp_path: Path) -> None:
    install = install_with_defaults(tmp_path, "settings.json",
                                    json.dumps({"env": {"DISABLE_UPDATES": "0"}, "model": "kept"}))
    merged = json.loads(ClaudeCode().run_files(install=install)[SETTINGS_FILE])
    assert merged["env"] == {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}
    assert merged["model"] == "kept"


@pytest.mark.parametrize(("adapter", "name", "text"), [
    ("codex", "config.toml", "x = [\n"), ("codex", "config.toml", "when = 1979-05-27T07:32:00Z\n"),
    ("claude-code", "settings.json", "[1]"), ("claude-code", "settings.json", "{"),
])
def test_defaults_the_adapter_cant_read_are_refused(layout: SessionLayout, tmp_path: Path, adapter: str,
                                                    name: str, text: str) -> None:
    install = install_with_defaults(tmp_path, name, text)
    with pytest.raises(AdapterError) as refused:
        if adapter == "codex":
            Codex().prepare_install_home(layout, tmp_path / "wt", install)
        else:
            ClaudeCode().run_files(install=install)
    assert str(refused.value) == DEFAULTS_MALFORMED


def test_a_linked_defaults_file_is_refused_not_followed(layout: SessionLayout, tmp_path: Path) -> None:
    outside = tmp_path / "outside.toml"
    outside.write_text('model = "planted"\n')
    install = tmp_path / "install"
    (install / "defaults").mkdir(parents=True)
    (install / "defaults" / "config.toml").symlink_to(outside)
    with pytest.raises(AdapterError) as refused:
        Codex().prepare_install_home(layout, tmp_path / "wt", install)
    assert str(refused.value) == DEFAULTS_MALFORMED


def test_an_install_without_defaults_is_the_launcher_alone(layout: SessionLayout, tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    Codex().prepare_install_home(layout, tmp_path / "wt", install)
    Codex().prepare_home(layout, tmp_path / "wt2")
    first = tomllib.loads((layout.home / ".codex" / "config.toml").read_text())
    assert first["features"]["plugins"] is False
    assert json.loads(ClaudeCode().run_files(install=install)[SETTINGS_FILE]) == json.loads(
        ClaudeCode().run_files()[SETTINGS_FILE])


@pytest.mark.parametrize("value", ["tab\there", "nul\x00", "del\x7f", "quote\"back\\slash", "é ✓",
                                   "\U0001f600", "line\nbreak"])
def test_the_generated_toml_round_trips_any_default_string(layout: SessionLayout, tmp_path: Path,
                                                           value: str) -> None:
    quoted = json.dumps(value, ensure_ascii=False)          # TOML has no surrogate-pair escapes
    default = f"model = {quoted}\n[tables.\"a b\"]\nlist = [1, 2.5, true, {{k = {quoted}}}]\n"
    # json.dumps escapes are TOML basic-string escapes, except \x7f, which TOML wants escaped too
    install = install_with_defaults(tmp_path, "config.toml", default.replace("\x7f", "\\u007f"))
    Codex().prepare_install_home(layout, tmp_path / "wt", install)
    merged = tomllib.loads((layout.home / ".codex" / "config.toml").read_text())
    assert merged["model"] == value
    assert merged["tables"]["a b"]["list"] == [1, 2.5, True, {"k": value}]


def test_the_updater_is_off_in_what_the_adapter_generates(layout: SessionLayout, tmp_path: Path) -> None:
    for prepare in (lambda: Codex().prepare_home(layout, tmp_path / "wt"),
                    lambda: Codex().prepare_install_home(layout, tmp_path / "wt", tmp_path)):
        prepare()
        text = (layout.home / ".codex" / "config.toml").read_text()
        top = text.split("\n[", 1)[0]
        assert "check_for_update_on_startup = false" in top.splitlines()
        features = tomllib.loads(text)["features"]
        assert {k: features[k] for k in ("daemon_auto_start", "plugins", "in_app_updates", "apps")} == {
            "daemon_auto_start": False, "plugins": False, "in_app_updates": False, "apps": False}
    binary = install_fake_cli(tmp_path, "claude-code")
    facts = ClaudeCode().facts(layout, ClaudeCode().locate("claude", str(binary.parent)), 1234)
    assert facts.extra_env == {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "DISABLE_AUTOUPDATER": "1",
                               "DISABLE_UPDATES": "1"}
    assert set(facts.extra_env) <= LAUNCHER_ENV
    for usage in (False, True):
        settings = json.loads(ClaudeCode().run_files(usage=usage)[SETTINGS_FILE])
        assert settings["env"] == {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}


OVERRIDES = ("-c", "--config", "--enable", "--disable")


def test_codex_argv_carries_no_config_override(tmp_path: Path) -> None:
    codex = Codex().locate("codex", str(install_fake_cli(tmp_path, "codex").parent))
    argvs = [Codex().server_argv(codex) or [],
             Codex().tui_argv(codex, {"model": "m"}, native_id=None, resume=False, label="x"),
             Codex().tui_argv(codex, {"model": "m"}, native_id=ID, resume=True, label="x")]
    for argv in argvs:
        assert not [a for a in argv if a in OVERRIDES or a.startswith(("-c", "--config=", "--enable=",
                                                                       "--disable="))], argv


@pytest.mark.parametrize(("adapter", "args"), [
    ("codex", ["-c", "features.plugins=true"]), ("codex", ["--enable=plugins"]), ("codex", "-c k=v"),
    ("claude-code", ["--settings", "{}"]), ("claude-code", ["--verbose"]), ("claude-code", [3]),
])
def test_profile_args_at_launch_refuses_what_config_refuses(tmp_path: Path, adapter: str,
                                                            args: object) -> None:
    profile = {"adapter": adapter, "args": args}
    with pytest.raises(ConfigError):
        pa.check(adapter, args, "profiles.p.args")
    with pytest.raises(AdapterError) as refused:
        profile_args(profile, adapter)
    assert str(refused.value) == OWNS_KEY
    binary = install_fake_cli(tmp_path, adapter)
    cli = ADAPTERS[adapter].locate(binary.name, str(binary.parent))
    native = None if adapter == "codex" else ID
    with pytest.raises(AdapterError):
        ADAPTERS[adapter].tui_argv(cli, profile, native_id=native, resume=False, label="x")
    if adapter == "claude-code":
        with pytest.raises(AdapterError):
            interactive_argv("claude", profile, session_id=ID, resume=False, settings_file=Path("/s"),
                             name="n")


def test_profile_args_pass_with_no_args() -> None:
    assert profile_args({}, "codex") == [] and profile_args({"args": []}, "claude-code") == []


@pytest.mark.parametrize("adapter", ["claude-code", "codex"])
def test_the_adapters_allowlists_match_the_config_table(adapter: str) -> None:
    assert ADAPTERS[adapter].profile_flags == pa.FLAGS[adapter]
    assert set(pa.FLAGS) == set(ADAPTERS)


def test_claude_presentation_argv_follows_the_launchers_flags() -> None:
    argv = interactive_argv("claude", {}, session_id=ID, resume=False, settings_file=Path("/s"), name="n",
                            isolated=True, presented=("--plugin-dir", "/i/content/plugins/p",
                                                      "--strict-mcp-config"))
    assert argv[argv.index("--settings"):] == ["--settings", "/s", "--name", "n", "--plugin-dir",
                                               "/i/content/plugins/p", "--strict-mcp-config"]
