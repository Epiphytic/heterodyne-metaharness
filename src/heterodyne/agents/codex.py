"""The `codex` adapter in its managed shape (ADR 0001 §4.2; spikes S5 and S8; codex-cli 0.160.0): a
per-session `codex app-server` inside the sandbox, and the TUI attached to it with `--remote`. The
thread ID is assigned by Codex and reported by the first SessionStart hook (plan 4 D8)."""

import json
import os
import re
import stat
import tomllib
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
    find_binary,
    has_file,
    home_dir,
    hook_group,
    install_root,
    jwt_exp,
    profile_args,
    read_json,
    read_text_at,
    write_at,
)
from heterodyne.agents.config_files import merge, open_dir_at, open_under, read_bounded_at, toml_text, without
from heterodyne.config.profile_args import FLAGS
from heterodyne.harness.resources import store_core
from heterodyne.sandbox.spec import BRIDGE_INSIDE, RUN_INSIDE, AgentFacts, Bind, SessionLayout
from heterodyne.session.shim import MAX_DEPTH, MAX_LINE, json_depth
from heterodyne.wsd.usage import Observation

APP_SOCKET = f"unix://{BRIDGE_INSIDE}/app.sock"
HOOKS_FILE = "hooks.json"
TRUST_SCRIPT = RUN_INSIDE / "codex_trust.py"
TRUST_SOURCE = Path(__file__).resolve().parents[1] / "sandbox" / "resources" / "codex_trust.py"
USAGE_SCRIPT = RUN_INSIDE / "codex_usage.py"
USAGE_SOURCE = Path(__file__).resolve().parents[1] / "sandbox" / "resources" / "codex_usage.py"
IN_SESSION_SOURCE = "codex.app-server"
HOST_SOURCE = "codex.host-read"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
KEY_TEXT = re.compile(r'[^"\\\x00-\x1f\x7f]{1,400}')
HASH = re.compile(r"(sha256:)?[0-9a-f]{16,64}")
# The release's assets (plan 4c E5): the package archive for a target, and its checksums.
RELEASE_ASSET = "codex-package-{target}.tar.gz"
RELEASE_SUMS = "codex-package_SHA256SUMS"
DEFAULTS_FILE = "config.toml"
MCP_FILE = "mcp.toml"
SKILL_FILE = "SKILL.md"
TOOL_ENV = frozenset({"CODEX_CI", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_VERSION", "COLORTERM",
                      "GH_PAGER", "GIT_PAGER", "LC_ALL", "LC_CTYPE", "NO_COLOR", "PAGER"})
KINDS = frozenset({"skills", "mcp"})


def _duration(minutes: int) -> str:
    for unit, size in (("d", 1440), ("h", 60)):
        if minutes % size == 0:
            return f"{minutes // size}{unit}"
    return f"{minutes}m"


def parse_rate_limits(data: object, source: str) -> UsageReport | None:
    """A `RateLimitSnapshot` (codex-cli 0.160 `account/rateLimits/read`, S7 (3)): one observation per
    window with a positive integer duration, identified by its limit ID and duration, never its slot. A
    `rateLimitReachedType` other than null is the limit signal (plan 4b E6); its hint is the latest reset
    of a window at 100% or more. None if `data` is not an object."""
    if not isinstance(data, dict):
        return None
    snap = cast(dict[str, Any], data)
    limit: Any = snap.get("limitId")
    limit_id = limit if isinstance(limit, str) else "default"
    windows: list[Observation] = []
    for slot in ("primary", "secondary"):
        window: Any = snap.get(slot)
        if not isinstance(window, dict):
            continue
        w = cast(dict[str, Any], window)
        minutes: Any = w.get("windowDurationMins")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
            continue
        windows.append(Observation(f"codex:{limit_id}:{minutes}m", _duration(minutes), w.get("usedPercent"),
                                   epoch_or_none(w.get("resetsAt")), source))
    limited = snap.get("rateLimitReachedType") is not None
    full = [o.resets_at for o in windows if o.resets_at is not None and not isinstance(o.used_percent, bool)
            and isinstance(o.used_percent, int | float) and o.used_percent >= 100]
    return UsageReport(tuple(windows), limited, max(full) if limited and full else None)


def _socket(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(os.lstat(path).st_mode)
    except OSError:
        return False


class Codex:
    name = "codex"
    config_var = "CODEX_HOME"
    config_subdir = ".codex"
    login_names: tuple[str, ...] = ("auth.json",)
    model_hosts: tuple[str, ...] = ("chatgpt.com",)
    version_pin = "0.160.0"
    versions: Mapping[str, Declared] = {"0.160.0": Declared(TOOL_ENV, KINDS)}
    tool_env = versions[version_pin].tool_env
    install_layout = InstallLayout("github-release", "openai/codex",
                                   store_core.Layout("codex-package.json", "version", "target", "entrypoint",
                                                     None), "")
    content_kinds = KINDS
    # E9: written, or dropped, after the defaults. `features.apps` is the connectors; `profile` and
    # `profiles` are never written, so no config profile can turn a key back on. The rest of 0.160's
    # ConfigToml that changes what its commands see and how they start, where its traffic and logins
    # go, or which plugins and state it keeps outside the home is dropped too. So is every MCP server
    # (its command, env and http_headers_helper): MCP comes only from content items, which are checked.
    owned_keys = frozenset({
        "approval_policy", "sandbox_mode", "check_for_update_on_startup", "projects", "notify", "hooks",
        "features.apps", "features.daemon_auto_start", "features.plugins", "features.in_app_updates",
        "profile", "profiles", "mcp_servers",
        "shell_environment_policy", "allow_login_shell", "zsh_path", "sandbox_workspace_write",
        "default_permissions", "permissions", "js_repl_node_path", "js_repl_node_module_dirs",
        "browser_use", "computer_use", "agents", "orchestrator",
        "model_provider", "model_providers", "openai_base_url", "chatgpt_base_url", "oss_provider",
        "experimental_realtime_ws_base_url", "experimental_realtime_webrtc_call_base_url",
        "experimental_thread_store_endpoint", "experimental_thread_store", "otel",
        "forced_login_method", "forced_chatgpt_workspace_id", "cli_auth_credentials_store",
        "mcp_oauth_credentials_store", "mcp_oauth_callback_port", "mcp_oauth_callback_url",
        "mcp_enterprise_managed_auth",
        "plugins", "marketplaces", "allow_symlinked_codex_home", "sqlite_home", "log_dir"})
    # E25: the PATH aliases (tmp/arg0/) and the daemon copy (packages/), which the CLI runs.
    empty_read_only = (PurePosixPath(".codex/tmp"), PurePosixPath(".codex/packages"))
    profile_flags = FLAGS["codex"]
    prompt_marker = "›"
    assigns_id = True
    turn_end_events: tuple[str, ...] = ("Stop",)

    def locate(self, binary: str, path: str) -> Cli:
        real = find_binary(binary, path)
        root = install_root(real)
        version = root.name.split("-")[0]           # .../releases/<version>-<target>
        if not VERSION.fullmatch(version):
            raise AdapterError("the codex CLI version can't be read")
        return Cli(real, root, version)

    def facts(self, layout: SessionLayout, cli: Cli, uid: int) -> AgentFacts:
        # codex 0.160 binds the real app-server socket in /tmp/codex-daemon-<uid>/ and leaves only a
        # symlink at the --listen path, so that directory is bound from the session too (S5).
        return AgentFacts(cli_root=cli.root, cli_binary=cli.binary, config_var=self.config_var,
                          config_dir=layout.home / self.config_subdir, login_names=self.login_names,
                          model_hosts=self.model_hosts,
                          binds=(Bind(layout.bridge, BRIDGE_INSIDE, False),
                                 Bind(layout.daemon, Path(f"/tmp/codex-daemon-{uid}"), False)),  # noqa: S108
                          read_write=(str(BRIDGE_INSIDE),))

    def launcher_config(self, worktree: Path) -> dict[str, Any]:
        """The launcher's own config.toml keys, written last (E9). `check_for_update_on_startup` is the
        update switch; `daemon_auto_start` stops the CLI copying itself into packages/ and running the
        copy; `plugins` stops the curated plugin sync; `in_app_updates` keeps a later in-app updater off."""
        return {"approval_policy": "never", "sandbox_mode": "danger-full-access",
                "check_for_update_on_startup": False,
                "projects": {str(worktree): {"trust_level": "untrusted"}},
                "features": {"apps": False, "daemon_auto_start": False, "plugins": False,
                             "in_app_updates": False}}

    def prepare_home(self, layout: SessionLayout, worktree: Path) -> None:
        self._write_home(layout, toml_text(self.launcher_config(worktree)))

    def prepare_install_home(self, layout: SessionLayout, worktree: Path,
                             install: Path) -> tuple[exec_dirs.Cleared, ...]:
        tree = self._defaults(install)
        servers = self._content_servers(install)
        if servers:
            merge(tree, {"mcp_servers": servers})
        merge(tree, self.launcher_config(worktree))
        try:
            text = toml_text(tree)
        except ValueError:
            raise AdapterError(DEFAULTS_MALFORMED) from None
        cleared = exec_dirs.prepare(layout.home, self.empty_read_only)
        self._write_home(layout, text)
        return cleared

    def _write_home(self, layout: SessionLayout, config: str) -> None:
        for folder in (layout.bridge, layout.daemon):          # host-side session directories, not the home
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.chmod(0o700)                # codex refuses a socket directory that is not 0700
            for entry in folder.iterdir():
                if _socket(entry) or entry.is_symlink():
                    entry.unlink()
        hooks = {e: [hook_group(e)] for e in HOOK_EVENTS}
        conf = home_dir(layout.home, self.config_subdir)
        try:
            write_at(conf, "config.toml", config)
            write_at(conf, HOOKS_FILE, json.dumps({"hooks": hooks}))
        except OSError:
            raise AdapterError("the synthetic home can't be prepared") from None
        finally:
            os.close(conf)

    def _defaults(self, install: Path) -> dict[str, Any]:
        """`defaults/config.toml` without the owned keys; {} if the install has none."""
        try:
            fd = open_under(install, ("defaults",))
            if fd is None:
                return {}
            try:
                text = read_bounded_at(fd, DEFAULTS_FILE)
            finally:
                os.close(fd)
            return without(tomllib.loads(text), self.owned_keys) if text is not None else {}
        except (OSError, ValueError):            # tomllib's and UTF-8's errors are ValueErrors
            raise AdapterError(DEFAULTS_MALFORMED) from None

    def _content_servers(self, install: Path) -> dict[str, Any]:
        """Every `content/mcp/<name>/mcp.toml`'s servers, in name order; a name given twice refuses."""
        try:
            fd = open_under(install, ("content", "mcp"))
        except OSError:
            raise AdapterError(CONTENT_MALFORMED) from None
        if fd is None:
            return {}
        servers: dict[str, Any] = {}
        try:
            for name in sorted(os.listdir(fd)):  # noqa: PTH208 - by descriptor, never by path
                found = self._mcp_servers(fd, name)
                if set(found) & set(servers):
                    raise AdapterError(CONTENT_MALFORMED)
                servers.update(found)
        except OSError:
            raise AdapterError(CONTENT_MALFORMED) from None
        finally:
            os.close(fd)
        return servers

    def _mcp_servers(self, parent: int | None, item: str | Path) -> dict[str, Any]:
        """An MCP item's servers. Its file holds `[mcp_servers.<name>]` tables and nothing else (E11)."""
        try:
            fd = open_dir_at(parent, item)
        except OSError:
            raise AdapterError(CONTENT_MALFORMED) from None
        try:
            text = read_bounded_at(fd, MCP_FILE)
            data: dict[str, Any] = tomllib.loads(text) if text is not None else {}
        except (OSError, ValueError):
            raise AdapterError(CONTENT_MALFORMED) from None
        finally:
            os.close(fd)
        if set(data) - {"mcp_servers"}:
            raise AdapterError(OWNS_KEY)
        servers: Any = data.get("mcp_servers")
        if not isinstance(servers, dict) or not servers or not all(
                isinstance(v, dict) for v in cast(dict[str, Any], servers).values()):
            raise AdapterError(CONTENT_MALFORMED)
        if set(cast(dict[str, Any], servers)) & RESERVED_MCP:
            raise AdapterError(OWNS_KEY)
        return cast(dict[str, Any], servers)

    def check_content(self, kind: str, tree: Path) -> None:
        if kind not in self.content_kinds:
            raise AdapterError(UNKNOWN_KIND)
        if kind == "mcp":
            self._mcp_servers(None, tree)
            return
        try:
            fd = open_dir_at(None, tree)
        except OSError:
            raise AdapterError(CONTENT_MALFORMED) from None
        try:
            if read_bounded_at(fd, SKILL_FILE) is None:
                raise AdapterError(CONTENT_MALFORMED)
        except (OSError, ValueError):
            raise AdapterError(CONTENT_MALFORMED) from None
        finally:
            os.close(fd)

    def present(self, layout: SessionLayout, install: Path, manifest: store_core.Manifest) -> Presentation:
        """Each skill as a read-only bind at $CODEX_HOME/skills/<name> (S9-4: skills/ itself stays
        writable, for the CLI's .system skills). MCP servers go into config.toml instead."""
        declared = self.versions.get(manifest.version)
        if declared is None:
            raise AdapterError(UNDECLARED_VERSION)
        binds: list[Bind] = []
        for kind, name in content_items(manifest):
            if kind not in declared.presentation:
                raise AdapterError(UNKNOWN_KIND)
            source = install / "content" / kind / name
            self.check_content(kind, source)
            if kind == "skills":
                binds.append(Bind(source, layout.home / self.config_subdir / "skills" / name, True))
        return Presentation((), tuple(binds), {})

    def run_files(self, *, usage: bool = False, limits: bool = False) -> Mapping[str, str]:
        files = {TRUST_SCRIPT.name: TRUST_SOURCE.read_text()}
        if usage or limits:                # the one read gives both the windows and the limit
            files[USAGE_SCRIPT.name] = USAGE_SOURCE.read_text()
        return files

    def usage_from_event(self, payload: Mapping[str, Any]) -> UsageReport | None:
        return None                     # codex 0.160's hooks carry no usage (S7 (5))

    def usage_argv(self, cli: Cli) -> list[str] | None:
        return ["python3", "-I", str(USAGE_SCRIPT), str(cli.binary)]

    def usage_from_read(self, output: bytes) -> UsageReport | None:
        if len(output) > MAX_LINE or json_depth(output) > MAX_DEPTH:
            return None
        try:
            data: Any = json.loads(output)
        except ValueError:
            return None
        return parse_rate_limits(data, IN_SESSION_SOURCE)

    def has_state(self, home: Path, native_id: str | None) -> bool:
        found = checked_id(native_id)
        def rollout(name: str) -> bool:                # sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl
            return name.startswith("rollout-") and name.endswith(f"-{found}.jsonl")

        return found is not None and has_file(home, (self.config_subdir, "sessions"), rollout, 3)

    def access_expiry(self, login_files: Sequence[Path]) -> float:
        tokens: Any = read_json(login_files[0]).get("tokens")
        return jwt_exp(cast(dict[str, Any], tokens).get("access_token") if isinstance(tokens, dict) else None)

    def server_argv(self, cli: Cli) -> list[str] | None:
        return [str(cli.binary), "app-server", "--listen", APP_SOCKET]

    def server_ready(self, layout: SessionLayout) -> bool:
        if _socket(layout.bridge / "app.sock"):
            return True
        return layout.daemon.is_dir() and any(_socket(p) for p in layout.daemon.iterdir())

    def tui_argv(self, cli: Cli, profile: Mapping[str, Any], *, native_id: str | None, resume: bool,
                 label: str) -> list[str]:
        argv = [str(cli.binary)]
        if resume:
            thread = checked_id(native_id)
            if thread is None:
                raise AdapterError("a codex resume needs its thread ID")
            # 0.160.0 refuses permission overrides on a remote resume (T14 live); the thread keeps the
            # approval and sandbox policy it was started with, which the live gate checks on its rollout.
            argv += ["resume", thread, "--remote", APP_SOCKET]
        else:
            argv += ["--remote", APP_SOCKET, "--dangerously-bypass-approvals-and-sandbox"]
        model = profile.get("model")
        if isinstance(model, str) and model:
            argv += ["--model", model]
        return argv + profile_args(profile, self.name)

    def trust_argv(self, cli: Cli, worktree: Path) -> list[str] | None:
        return ["python3", "-I", str(TRUST_SCRIPT), str(cli.binary), str(worktree)]

    def apply_trust(self, layout: SessionLayout, output: bytes) -> int:
        conf = layout.home / self.config_subdir
        prefix = f"{conf / HOOKS_FILE}:"
        try:
            listed: Any = json.loads(output)
        except ValueError:
            raise AdapterError("the hook trust listing is not JSON") from None
        if not isinstance(listed, list):
            raise AdapterError("the hook trust listing is not a list")
        lines: list[str] = []
        for item in cast(list[Any], listed):
            entry = cast(dict[str, Any], item) if isinstance(item, dict) else {}
            key, digest = entry.get("key"), entry.get("hash")
            if not (isinstance(key, str) and isinstance(digest, str) and key.startswith(prefix)
                    and KEY_TEXT.fullmatch(key) and HASH.fullmatch(digest)):
                raise AdapterError("the hook trust listing has an invalid entry")
            lines.append(f'\n[hooks.state."{key}"]\ntrusted_hash = "{digest}"\n')
        if lines:
            fd = home_dir(layout.home, self.config_subdir)
            try:
                write_at(fd, "config.toml", read_text_at(fd, "config.toml") + "".join(lines))
            except OSError:
                raise AdapterError("the hook trust can't be recorded") from None
            finally:
                os.close(fd)
        return len(lines)
