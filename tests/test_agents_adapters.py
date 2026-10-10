import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from sandbox_env import fake_jwt, install_fake_cli, short_dir

from heterodyne.agents.base import (
    HOOK_COMMAND,
    HOOK_EVENTS,
    PRE_TOOL_COMMAND,
    PRE_TOOL_TIMEOUT,
    AdapterError,
    hook_group,
    write_at,
)
from heterodyne.agents.claude_code import SETTINGS_FILE, ClaudeCode
from heterodyne.agents.codex import APP_SOCKET, Codex
from heterodyne.agents.registry import ADAPTERS
from heterodyne.sandbox.spec import BRIDGE_INSIDE, RUN_INSIDE, SessionLayout

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
    assert set(flag["hooks"]) == set(HOOK_EVENTS)
    assert {e: [h["command"] for g in gs for h in g["hooks"]] for e, gs in flag["hooks"].items()} == {
        e: [PRE_TOOL_COMMAND if e == "PreToolUse" else HOOK_COMMAND] for e in HOOK_EVENTS}


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
                      "projects": {str(tmp_path / "wt"): {"trust_level": "untrusted"}},
                      "features": {"apps": False}}
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
    assert Codex().tui_argv(codex, {}, native_id=ID, resume=True, label="x")[1:3] == ["resume", ID]
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
