"""The `claude-code` adapter's interactive launch shape (ADR 0001 §4.2; Claude Code 2.1.x flags)."""

import contextlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from heterodyne.agents.base import (
    HOOK_EVENTS,
    AdapterError,
    Cli,
    checked_id,
    find_binary,
    has_file,
    home_dir,
    hook_group,
    install_root,
    read_json,
    read_json_at,
    write_at,
)
from heterodyne.sandbox.spec import RUN_INSIDE, AgentFacts, SessionLayout


def interactive_argv(binary: str, profile: Mapping[str, Any], *, session_id: str, resume: bool,
                     settings_file: Path, name: str, isolated: bool = False) -> list[str]:
    """Launch (or resume) an interactive session with a fixed ID, hooks from `settings_file`, and
    permission prompts bypassed. The profile's `args` are appended verbatim. `isolated` loads no user,
    project or local settings, so only `settings_file` (and managed policy) applies: a worktree's
    .claude/settings.json could otherwise disable every hook or add its own."""
    argv = [binary, "--resume" if resume else "--session-id", session_id]
    model = profile.get("model")
    if isinstance(model, str) and model:
        argv += ["--model", model]
    if isolated:
        argv += ["--setting-sources", ""]
    argv += ["--permission-mode", "bypassPermissions", "--settings", str(settings_file), "--name", name]
    args = profile.get("args", [])
    if isinstance(args, list):
        argv += [str(a) for a in cast(list[Any], args)]
    return argv


def headless_argv(binary: str, profile: Mapping[str, Any]) -> list[str]:
    """A one-shot run that reads its prompt on stdin and prints text: no tools, no hooks, no MCP
    servers, no user or local settings, no saved session. The profile's `args` are not appended: they
    could re-enable tools (plan 2b B8)."""
    argv = [binary, "-p"]
    model = profile.get("model")
    if isinstance(model, str) and model:
        argv += ["--model", model]
    return argv + ["--tools", "", "--setting-sources", "project", "--settings", '{"disableAllHooks": true}',
                   "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"]


SETTINGS_FILE = "claude-settings.json"          # in the run directory: read-only inside
REMOTE_POLICY = "remote-settings.json"          # Claude's cache of remote managed settings


class ClaudeCode:
    name = "claude-code"
    config_var = "CLAUDE_CONFIG_DIR"
    config_subdir = ".claude"
    login_names: tuple[str, ...] = (".credentials.json",)
    model_hosts: tuple[str, ...] = ("api.anthropic.com",)
    version_pin = "2.1.286"
    tool_env = frozenset({
        "AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_EFFORT", "CLAUDE_PID", "COREPACK_ENABLE_AUTO_PIN", "GIT_EDITOR",
        "NoDefaultCurrentDirectoryInExePath"})
    prompt_marker = "❯"
    assigns_id = False

    def locate(self, binary: str, path: str) -> Cli:
        real = find_binary(binary, path)
        root = install_root(real)
        version = read_json(root / "package.json").get("version")
        if not isinstance(version, str) or not version:
            raise AdapterError("the claude-code CLI version can't be read")
        return Cli(real, root, version)

    def facts(self, layout: SessionLayout, cli: Cli, uid: int) -> AgentFacts:
        return AgentFacts(cli_root=cli.root, cli_binary=cli.binary, config_var=self.config_var,
                          config_dir=layout.home / self.config_subdir, login_names=self.login_names,
                          model_hosts=self.model_hosts, extra_env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"})

    def prepare_home(self, layout: SessionLayout, worktree: Path) -> None:
        conf = home_dir(layout.home, self.config_subdir)
        try:
            # Remote managed settings outrank our hooks and the cache is the agent's to write.
            with contextlib.suppress(FileNotFoundError):
                os.unlink(REMOTE_POLICY, dir_fd=conf)
            state = read_json_at(conf, ".claude.json")
            projects: Any = state.get("projects")
            projects = cast(dict[str, Any], projects) if isinstance(projects, dict) else {}
            project: Any = projects.get(str(worktree))
            project = cast(dict[str, Any], project) if isinstance(project, dict) else {}
            projects[str(worktree)] = {**project, "hasTrustDialogAccepted": True,
                                       "hasCompletedProjectOnboarding": True}
            state.update(hasCompletedOnboarding=True, bypassPermissionsModeAccepted=True, projects=projects)
            state.setdefault("theme", "dark")
            write_at(conf, ".claude.json", json.dumps(state))
        except OSError:
            raise AdapterError("the synthetic home can't be prepared") from None
        finally:
            os.close(conf)

    def run_files(self) -> Mapping[str, str]:
        hooks = {e: [hook_group(e, "*" if e == "PreToolUse" else None)] for e in HOOK_EVENTS}
        return {SETTINGS_FILE: json.dumps({"skipDangerousModePermissionPrompt": True, "hooks": hooks})}

    def has_state(self, home: Path, native_id: str | None) -> bool:
        found = checked_id(native_id)
        name = f"{found}.jsonl"
        return found is not None and has_file(home, (self.config_subdir, "projects"), name.__eq__, 1)

    def access_expiry(self, login_files: Sequence[Path]) -> float:
        oauth: Any = read_json(login_files[0]).get("claudeAiOauth")
        expires: Any = cast(dict[str, Any], oauth).get("expiresAt") if isinstance(oauth, dict) else None
        if not isinstance(expires, int | float) or isinstance(expires, bool):
            raise AdapterError("the login's expiry can't be read")
        return expires / 1000

    def server_argv(self, cli: Cli) -> list[str] | None:
        return None

    def server_ready(self, layout: SessionLayout) -> bool:
        return True

    def tui_argv(self, cli: Cli, profile: Mapping[str, Any], *, native_id: str | None, resume: bool,
                 label: str) -> list[str]:
        session = checked_id(native_id)
        if session is None:
            raise AdapterError("a claude-code launch needs its native ID")
        return interactive_argv(str(cli.binary), profile, session_id=session, resume=resume,
                                settings_file=RUN_INSIDE / SETTINGS_FILE, name=label, isolated=True)

    def trust_argv(self, cli: Cli, worktree: Path) -> list[str] | None:
        return None

    def apply_trust(self, layout: SessionLayout, output: bytes) -> int:
        return 0
