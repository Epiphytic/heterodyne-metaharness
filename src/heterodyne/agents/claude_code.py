"""The `claude-code` adapter's interactive launch shape (ADR 0001 §4.2; Claude Code 2.1.x flags)."""

import contextlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

from heterodyne.agents import exec_dirs
from heterodyne.agents.base import (
    CONTENT_MALFORMED,
    DEFAULTS_MALFORMED,
    HOOK_EVENTS,
    OWNS_KEY,
    RESERVED_MCP,
    STATUS_COMMAND,
    UNDECLARED_VERSION,
    UNKNOWN_KIND,
    AdapterError,
    Cli,
    Declared,
    InstallLayout,
    Presentation,
    UsageReport,
    checked_id,
    content_items,
    epoch_or_none,
    has_file,
    home_dir,
    hook_group,
    profile_args,
    read_json,
    read_json_at,
    write_at,
)
from heterodyne.agents.config_files import has_entry, merge, open_dir_at, open_under, read_bounded_at, without
from heterodyne.config.profile_args import FLAGS
from heterodyne.harness.resources import store_core
from heterodyne.sandbox.spec import RUN_INSIDE, AgentFacts, SessionLayout
from heterodyne.wsd.usage import Observation


def interactive_argv(binary: str, profile: Mapping[str, Any], *, session_id: str, resume: bool,
                     settings_file: Path, name: str, isolated: bool = False,
                     presented: Sequence[str] = ()) -> list[str]:
    """Launch (or resume) an interactive session with a fixed ID, hooks from `settings_file`, and
    permission prompts bypassed. `presented` (an install's content, E10) follows the launcher's flags, so
    the launcher's `--settings` stays the last settings source; then the profile's `args`, which pass only
    through the allowlist (Q4). `isolated` loads no user, project or local settings, so only
    `settings_file` (and managed policy) applies: a worktree's .claude/settings.json could otherwise
    disable every hook or add its own."""
    argv = [binary, "--resume" if resume else "--session-id", session_id]
    model = profile.get("model")
    if isinstance(model, str) and model:
        argv += ["--model", model]
    if isolated:
        # One token, never an empty argument: an empty one is lost wherever argv is joined into a command
        # line. Every 2.1.286 parser (its eager scan, commander) reads `--opt=` as the empty value.
        argv += ["--setting-sources="]
    argv += ["--permission-mode", "bypassPermissions", "--settings", str(settings_file), "--name", name]
    return argv + list(presented) + profile_args(profile, "claude-code")


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

# Claude Code 2.1.286 ends a turn that fails on an API error, a usage limit included, with StopFailure
# instead of Stop (S7 finding 1): it must reach the session socket, or the turn never ends to wsd.
CLAUDE_HOOK_EVENTS = (*HOOK_EVENTS, "StopFailure")
STATUS_SOURCE = "claude.statusline"
# The status line's windows (S7 (3)): its key, and the window's identity and label.
CLAUDE_WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))
DEFAULTS_FILE = "settings.json"
MCP_FILE = "mcp.json"
PLUGIN_DIR, PLUGIN_FILE, PLUGIN_MCP = ".claude-plugin", "plugin.json", ".mcp.json"
KINDS = frozenset({"plugins", "mcp"})
TOOL_ENV = frozenset({
    "AI_AGENT", "CLAUDECODE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_EFFORT", "CLAUDE_PID", "COREPACK_ENABLE_AUTO_PIN", "GIT_EDITOR",
    "NoDefaultCurrentDirectoryInExePath"})
# E9 (S9-6): the CLI checks DISABLE_UPDATES first. Both are set in the environment and the settings file.
UPDATER_ENV = {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}


def _object(text: str | None) -> dict[str, Any] | None:
    """`text` as a JSON object; None if missing. Raises ValueError for anything else."""
    if text is None:
        return None
    data: Any = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return cast(dict[str, Any], data)


def _servers(value: object) -> dict[str, Any]:
    """An `mcpServers` table: server names to tables, none of them the launcher's (E11)."""
    if not isinstance(value, dict):
        raise AdapterError(CONTENT_MALFORMED)
    if not all(isinstance(v, dict) for v in cast(dict[str, Any], value).values()):
        raise AdapterError(CONTENT_MALFORMED)
    if set(cast(dict[str, Any], value)) & RESERVED_MCP:
        raise AdapterError(OWNS_KEY)
    return cast(dict[str, Any], value)


def claude_windows(limits: Mapping[str, Any]) -> tuple[Observation, ...]:
    """The status line's `rate_limits`: one observation per window it has, its values unchecked."""
    found: list[Observation] = []
    for key, kind in CLAUDE_WINDOWS:
        window: Any = limits.get(key)
        if isinstance(window, dict):
            w = cast(dict[str, Any], window)
            found.append(Observation(f"claude:{kind}", kind, w.get("used_percentage"),
                                     epoch_or_none(w.get("resets_at")), STATUS_SOURCE))
    return tuple(found)


class ClaudeCode:
    name = "claude-code"
    config_var = "CLAUDE_CONFIG_DIR"
    config_subdir = ".claude"
    login_names: tuple[str, ...] = (".credentials.json",)
    model_hosts: tuple[str, ...] = ("api.anthropic.com",)
    versions: Mapping[str, Declared] = {"2.1.286": Declared(TOOL_ENV, KINDS)}
    install_layout = InstallLayout("npm", "@anthropic-ai/claude-code-<target>",
                                   store_core.Layout("package.json", "version", None, None, "claude"),
                                   "package/")
    content_kinds = KINDS
    # E9: dropped from the defaults and, where the launcher has a value, written over them. The
    # connectors' switch is ENABLE_CLAUDEAI_MCP_SERVERS; the plugin and sandbox keys are the launcher's.
    # The CLI runs apiKeyHelper .. subagentStatusLine as commands (its own list of them, 2.1.286), and
    # the two .mcp.json switches start the worktree's servers without asking. MCP comes only from content
    # items, so a settings-file mcpServers is dropped too.
    owned_keys = frozenset({
        "hooks", "statusLine", "skipDangerousModePermissionPrompt", "disableAllHooks",
        "permissions.defaultMode", "enabledPlugins", "extraKnownMarketplaces", "sandbox",
        "env.DISABLE_AUTOUPDATER", "env.DISABLE_UPDATES", "env.ENABLE_CLAUDEAI_MCP_SERVERS",
        "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh", "otelHeadersHelper",
        "proxyAuthHelper", "processWrapper", "policyHelpers", "fileSuggestion", "subagentStatusLine",
        "enableAllProjectMcpServers", "enabledMcpjsonServers", "mcpServers"})
    # Any other env entry is refused: PATH picks the hooks' python3, CLAUDE_CODE_SHELL_PREFIX wraps
    # them and CLAUDE_CODE_PLUGIN_DIRS loads plugins, and the CLI reads many more. None is needed yet.
    env_allowed: frozenset[str] = frozenset()
    empty_read_only: tuple[PurePosixPath, ...] = ()
    profile_flags = FLAGS["claude-code"]
    prompt_marker = "❯"
    assigns_id = False
    turn_end_events: tuple[str, ...] = ("Stop", "StopFailure")

    def facts(self, layout: SessionLayout, cli: Cli, uid: int) -> AgentFacts:
        return AgentFacts(cli_root=cli.root, cli_binary=cli.binary, config_var=self.config_var,
                          config_dir=layout.home / self.config_subdir, login_names=self.login_names,
                          model_hosts=self.model_hosts,
                          extra_env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false", **UPDATER_ENV})

    def _prepare_home(self, layout: SessionLayout, worktree: Path) -> None:
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

    def prepare_install_home(self, layout: SessionLayout, worktree: Path,
                             install: Path) -> tuple[exec_dirs.Cleared, ...]:
        """The defaults go into the run directory's settings file (run_files), not the home."""
        self._prepare_home(layout, worktree)
        return exec_dirs.prepare(layout.home, self.empty_read_only)

    def run_files(self, *, usage: bool = False, limits: bool = False,
                  install: Path | None = None) -> Mapping[str, str]:
        """The limit signal is the StopFailure hook, always installed, so `limits` adds nothing. With an
        `install`, its `defaults/settings.json` is merged under the launcher's keys."""
        hooks = {e: [hook_group(e, "*" if e == "PreToolUse" else None)] for e in CLAUDE_HOOK_EVENTS}
        settings: dict[str, Any] = {"skipDangerousModePermissionPrompt": True, "hooks": hooks,
                                    "env": dict(UPDATER_ENV)}
        if usage:
            settings["statusLine"] = {"type": "command", "command": STATUS_COMMAND}
        if install is not None:
            settings = merge(self._defaults(install), settings)
        return {SETTINGS_FILE: json.dumps(settings)}

    def _defaults(self, install: Path) -> dict[str, Any]:
        try:
            fd = open_under(install, ("defaults",))
            if fd is None:
                return {}
            try:
                found = _object(read_bounded_at(fd, DEFAULTS_FILE))
            finally:
                os.close(fd)
        except (OSError, ValueError):
            raise AdapterError(DEFAULTS_MALFORMED) from None
        if found is None:
            return {}
        defaults = without(found, self.owned_keys)
        env: Any = defaults.get("env", {})
        if not isinstance(env, dict):
            raise AdapterError(DEFAULTS_MALFORMED)
        if set(cast(dict[str, Any], env)) - self.env_allowed:
            raise AdapterError(OWNS_KEY)
        return defaults

    def check_content(self, kind: str, tree: Path) -> None:
        if kind not in self.content_kinds:
            raise AdapterError(UNKNOWN_KIND)
        try:
            fd = open_dir_at(None, tree)
        except OSError:
            raise AdapterError(CONTENT_MALFORMED) from None
        try:
            if kind == "mcp":
                found = _object(read_bounded_at(fd, MCP_FILE))
                if found is None:
                    raise AdapterError(CONTENT_MALFORMED)
                if set(found) - {"mcpServers"}:
                    raise AdapterError(OWNS_KEY)
                _servers(found.get("mcpServers"))
            else:
                self._check_plugin(fd)
        except (OSError, ValueError):
            raise AdapterError(CONTENT_MALFORMED) from None
        finally:
            os.close(fd)

    def _check_plugin(self, fd: int) -> None:
        """A plugin is marked by .claude-plugin/plugin.json (S9-2). It may bring no hooks and no settings,
        and no MCP server named like the launcher's (E11)."""
        try:
            meta = open_dir_at(fd, PLUGIN_DIR)
        except OSError:
            raise AdapterError(CONTENT_MALFORMED) from None
        try:
            plugin = _object(read_bounded_at(meta, PLUGIN_FILE))
        finally:
            os.close(meta)
        if plugin is None:
            raise AdapterError(CONTENT_MALFORMED)
        if "hooks" in plugin or has_entry(fd, "hooks") or has_entry(fd, "settings.json"):
            raise AdapterError(OWNS_KEY)
        if "mcpServers" in plugin:
            _servers(plugin["mcpServers"])
        bundled = _object(read_bounded_at(fd, PLUGIN_MCP))
        if bundled is not None:
            _servers(bundled.get("mcpServers", bundled))

    def present(self, layout: SessionLayout, install: Path, manifest: store_core.Manifest) -> Presentation:
        """Each plugin as a `--plugin-dir` (S9-2), each MCP item's file as a `--mcp-config`, and always
        `--strict-mcp-config`, which keeps a worktree's .mcp.json out (S9-3)."""
        declared = self.versions.get(manifest.version)
        if declared is None:
            raise AdapterError(UNDECLARED_VERSION)
        plugins: list[str] = []
        mcp: list[str] = []
        for kind, name in content_items(manifest):
            if kind not in declared.presentation:
                raise AdapterError(UNKNOWN_KIND)
            source = install / "content" / kind / name
            self.check_content(kind, source)
            if kind == "plugins":
                plugins += ["--plugin-dir", str(source)]
            else:
                mcp += ["--mcp-config", str(source / MCP_FILE)]
        return Presentation((*plugins, *mcp, "--strict-mcp-config"), (), {})

    def usage_from_event(self, payload: Mapping[str, Any]) -> UsageReport | None:
        """S7 (5): a StopFailure with `error == "rate_limit"` is the limit signal; its reset is only in
        message text, which is never parsed. S7 (3): the status line's `rate_limits`."""
        event = payload.get("hook_event_name")
        if event == "StopFailure":
            return UsageReport(limited=True) if payload.get("error") == "rate_limit" else None
        limits: Any = payload.get("rate_limits")
        if event != "StatusLine" or not isinstance(limits, dict):
            return None
        windows = claude_windows(cast(dict[str, Any], limits))
        return UsageReport(windows) if windows else None

    def usage_argv(self, cli: Cli) -> list[str] | None:
        return None

    def usage_from_read(self, output: bytes) -> UsageReport | None:
        return None

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
