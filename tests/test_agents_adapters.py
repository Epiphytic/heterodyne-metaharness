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

from heterodyne.agents.base import HOOK_COMMAND, HOOK_EVENTS, AdapterError, write_at
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
    assert json.loads((conf / "settings.json").read_text()) == {"skipDangerousModePermissionPrompt": True}
    hooks = json.loads(ClaudeCode().run_files()[SETTINGS_FILE])["hooks"]
    assert set(hooks) == set(HOOK_EVENTS)
    assert {h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]} == {HOOK_COMMAND}


def test_codex_home_is_rewritten_and_stale_sockets_go(layout: SessionLayout, tmp_path: Path) -> None:
    Codex().prepare_home(layout, tmp_path / "wt")
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(layout.bridge / "app.sock"))
    stale.close()
    (layout.home / ".codex" / "config.toml").write_text("model = 'x'\n")
    Codex().prepare_home(layout, tmp_path / "wt")
    config = tomllib.loads((layout.home / ".codex" / "config.toml").read_text())
    assert config == {"approval_policy": "never", "sandbox_mode": "danger-full-access",
                      "projects": {str(tmp_path / "wt"): {"trust_level": "trusted"}},
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
    assert ClaudeCode().tui_argv(claude, {}, native_id=ID, resume=True, label="x")[1:3] == ["--resume", ID]
    assert Codex().tui_argv(codex, {}, native_id=None, resume=False, label="x") == [
        str(codex.binary), "--remote", APP_SOCKET, "--dangerously-bypass-approvals-and-sandbox"]
    assert Codex().tui_argv(codex, {}, native_id=ID, resume=True, label="x")[1:3] == ["resume", ID]
    with pytest.raises(AdapterError):
        Codex().tui_argv(codex, {}, native_id=None, resume=True, label="x")
    with pytest.raises(AdapterError):
        ClaudeCode().tui_argv(claude, {}, native_id=None, resume=False, label="x")


def trust_listing(layout: SessionLayout, codex: Path, worktree: Path) -> bytes:
    env = {"CODEX_HOME": str(layout.home / ".codex"), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, "-I", str(TRUST), str(codex), str(worktree)], env=env,
                          capture_output=True, timeout=60, check=True)
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


@pytest.mark.parametrize("adapter, name", [("claude-code", "settings.json"), ("claude-code", ".claude.json"),
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
