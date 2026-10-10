"""The `codex` adapter in its managed shape (ADR 0001 §4.2; spikes S5 and S8; codex-cli 0.160.0): a
per-session `codex app-server` inside the sandbox, and the TUI attached to it with `--remote`. The
thread ID is assigned by Codex and reported by the first SessionStart hook (plan 4 D8)."""

import json
import os
import re
import stat
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
    jwt_exp,
    profile_args,
    read_json,
    read_text_at,
    write_at,
)
from heterodyne.sandbox.spec import BRIDGE_INSIDE, RUN_INSIDE, AgentFacts, Bind, SessionLayout

APP_SOCKET = f"unix://{BRIDGE_INSIDE}/app.sock"
HOOKS_FILE = "hooks.json"
TRUST_SCRIPT = RUN_INSIDE / "codex_trust.py"
TRUST_SOURCE = Path(__file__).resolve().parents[1] / "sandbox" / "resources" / "codex_trust.py"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
KEY_TEXT = re.compile(r'[^"\\\x00-\x1f\x7f]{1,400}')
HASH = re.compile(r"(sha256:)?[0-9a-f]{16,64}")


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
    tool_env = frozenset({"CODEX_CI", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_VERSION", "COLORTERM",
                          "GH_PAGER", "GIT_PAGER", "LC_ALL", "LC_CTYPE", "NO_COLOR", "PAGER"})
    prompt_marker = "›"
    assigns_id = True

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

    def prepare_home(self, layout: SessionLayout, worktree: Path) -> None:
        for folder in (layout.bridge, layout.daemon):          # host-side session directories, not the home
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            folder.chmod(0o700)                # codex refuses a socket directory that is not 0700
            for entry in folder.iterdir():
                if _socket(entry) or entry.is_symlink():
                    entry.unlink()
        hooks = {e: [hook_group(e)] for e in HOOK_EVENTS}
        conf = home_dir(layout.home, self.config_subdir)
        try:
            # JSON string escapes are valid TOML basic-string escapes.
            write_at(conf, "config.toml",
                     'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n'
                     f"[projects.{json.dumps(str(worktree))}]\ntrust_level = \"untrusted\"\n"
                     "[features]\napps = false\n")
            write_at(conf, HOOKS_FILE, json.dumps({"hooks": hooks}))
        except OSError:
            raise AdapterError("the synthetic home can't be prepared") from None
        finally:
            os.close(conf)

    def run_files(self) -> Mapping[str, str]:
        return {TRUST_SCRIPT.name: TRUST_SOURCE.read_text()}

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
            argv += ["resume", thread]
        argv += ["--remote", APP_SOCKET, "--dangerously-bypass-approvals-and-sandbox"]
        model = profile.get("model")
        if isinstance(model, str) and model:
            argv += ["--model", model]
        return argv + profile_args(profile)

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
