"""The adapter contract of the sandbox runtime (ADR 0001 §4.2, §7). An adapter says how its CLI is found
and pinned, what its synthetic home holds, where it keeps resumable state, how its login's expiry is read,
and how it is started in its managed shape. Nothing outside `heterodyne.agents` names a CLI."""

import base64
import contextlib
import errno
import json
import os
import secrets
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from heterodyne.sandbox.spec import AgentFacts, SessionLayout
from heterodyne.session.server import UUID

# One fixed hook command for every launch (plan 4 D5): Codex's trusted hash stays valid, and the
# session token is read from the file beside the socket, never from a command line.
HOOK_COMMAND = "python3 -I /run/hz/shim.py hook"
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "PreToolUse")


class AdapterError(Exception):
    """The adapter can't do this for the launch. Fixed wording, no paths or secrets: LaunchFailed."""


@dataclass(frozen=True)
class Cli:
    binary: Path        # canonical
    root: Path          # the install root, bound read-only
    version: str


def find_binary(binary: str, path: str) -> Path:
    found = binary if os.sep in binary else shutil.which(binary, path=path)
    if not found:
        raise AdapterError("the CLI binary was not found")
    real = Path(os.path.realpath(found))
    if not real.is_file() or not os.access(real, os.X_OK):
        raise AdapterError("the CLI binary was not found")
    return real


def install_root(binary: Path) -> Path:
    return binary.parent.parent if binary.parent.name == "bin" else binary.parent


def hook_group(matcher: str | None = None) -> dict[str, Any]:
    group: dict[str, Any] = {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    return group if matcher is None else {"matcher": matcher, **group}


def write_at(dirfd: int, name: str, text: str) -> None:
    """Write `name` in the directory `dirfd` atomically and durably, mode 0600. The temporary file is
    exclusive, has an unpredictable name and is never reached through a link, and the rename replaces
    whatever `name` is (a link there is replaced, not followed). The file is synced before the rename and
    the directory after it, so once this returns the new content survives a machine crash: the session
    record's transitions (`stopping` with its owed WIP, `unlanded`, `ended`) are ordered on disk before the
    step that follows them. Raises OSError."""
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
        os.fsync(dirfd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dirfd)
        raise


def write_private(path: Path, text: str) -> None:
    """write_at (atomic and durable) in a directory only the host writes (a run directory, a session
    directory). Never use it under a synthetic home: that is the agent's to change, and goes through
    home_dir."""
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        write_at(fd, path.name, text)
    finally:
        os.close(fd)


def home_dir(home: Path, *parts: str) -> int:
    """An open directory descriptor for <home>/<parts...>, each created 0700 if missing. Every component is
    opened relative to the one before without following a link, so a link the agent planted in its home
    between generations refuses the launch instead of redirecting a host write. The caller closes it."""
    try:
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise AdapterError("the synthetic home can't be opened") from None
    try:
        for part in parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o700, dir_fd=fd)
            inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = inner
    except OSError:
        os.close(fd)
        raise AdapterError("the synthetic home holds a path the host won't follow") from None
    return fd


def read_text_at(dirfd: int, name: str) -> str:
    """The text of the regular file `name` in `dirfd`, never through a link, never blocking on a FIFO.
    Raises OSError for anything else."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        return fh.read().decode("utf-8", "replace")


def read_json_at(dirfd: int, name: str) -> dict[str, Any]:
    """read_text_at as a JSON object; {} when missing, not a regular file, or not an object."""
    try:
        data: Any = json.loads(read_text_at(dirfd, name))
    except (OSError, ValueError):
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def no_link(home: Path, subdir: str) -> None:
    """has_state's guard: the config directory itself must not be a link (globs below it follow links,
    but a match there only names a session the CLI then fails to resume inside)."""
    if (home / subdir).is_symlink():
        raise AdapterError("the synthetic home holds a path the host won't follow")


def checked_id(native_id: str | None) -> str | None:
    if native_id is not None and not UUID.fullmatch(native_id):
        raise AdapterError("the native ID is not a canonical UUID")
    return native_id


def read_json(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def jwt_exp(token: object) -> float:
    if not isinstance(token, str) or token.count(".") != 2:
        raise AdapterError("the login's expiry can't be read")
    body = token.split(".")[1]
    try:
        claims: Any = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        raise AdapterError("the login's expiry can't be read") from None
    exp = cast(dict[str, Any], claims).get("exp") if isinstance(claims, dict) else None
    if not isinstance(exp, int | float) or isinstance(exp, bool):
        raise AdapterError("the login's expiry can't be read")
    return float(exp)


class Adapter(Protocol):
    name: str
    config_var: str
    config_subdir: str
    login_names: tuple[str, ...]
    model_hosts: tuple[str, ...]
    version_pin: str
    tool_env: frozenset[str]          # what the pinned CLI adds to its tools' environment (S5 item 11)
    prompt_marker: str                # its ready prompt, as tmux captures it
    assigns_id: bool                  # the CLI assigns the native ID itself (Codex's thread), else wsd does

    def locate(self, binary: str, path: str) -> Cli: ...

    def facts(self, layout: SessionLayout, cli: Cli, uid: int) -> AgentFacts: ...

    def prepare_home(self, layout: SessionLayout, worktree: Path) -> None: ...

    def run_files(self) -> Mapping[str, str]: ...

    def has_state(self, home: Path, native_id: str | None) -> bool:
        """The synthetic home holds resumable state for this native ID. Raises AdapterError for an ID
        that is not a canonical UUID, and OSError if the home can't be read."""
        ...

    def access_expiry(self, login_files: Sequence[Path]) -> float: ...

    def server_argv(self, cli: Cli) -> list[str] | None: ...

    def server_ready(self, layout: SessionLayout) -> bool: ...

    def tui_argv(self, cli: Cli, profile: Mapping[str, Any], *, native_id: str | None, resume: bool,
                 label: str) -> list[str]: ...

    def trust_argv(self, cli: Cli, worktree: Path) -> list[str] | None: ...

    def apply_trust(self, layout: SessionLayout, output: bytes) -> int:
        """Trust what `trust_argv`'s output lists; return how many entries it listed."""
        ...


def profile_args(profile: Mapping[str, Any]) -> list[str]:
    args = profile.get("args", [])
    return [str(a) for a in cast(list[Any], args)] if isinstance(args, list) else []
