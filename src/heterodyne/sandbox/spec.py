"""The platform-neutral sandbox spec (ADR 0001 §7): what one session's sandbox binds, may write, may reach
and gets in its environment, and the credential check every launch runs on it. Backends compile a spec
(openshell.py); adapters contribute `AgentFacts`; nothing here names an adapter or a backend.

Every host path is bound at the same path inside, except the generation's run directory, which is
`/run/hz` (read-only). Names follow D1 and paths follow D2 of plan 4.
"""

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from heterodyne.sandbox.settings import DENIED_HOSTS, HOST

RUN_INSIDE = Path("/run/hz")
BRIDGE_INSIDE = Path("/run/hz-bridge")
SOCKET_INSIDE = RUN_INSIDE / "s.sock"
PROBE_SOCKET_INSIDE = RUN_INSIDE / "p.sock"
TOKEN_INSIDE = RUN_INSIDE / "token"
SHIM_INSIDE = RUN_INSIDE / "shim.py"
SHIM_CONFIG_INSIDE = RUN_INSIDE / "shim.json"
PROBES_INSIDE = RUN_INSIDE / "probes.py"
AGENT_PROBE_INSIDE = RUN_INSIDE / "agent-probe.json"
REQUEST_INSIDE = RUN_INSIDE / "ws-request"
SYSTEM_READ_ONLY = ("/usr", "/lib", "/lib64", "/etc", "/proc", "/dev/urandom")
SYSTEM_READ_WRITE = ("/tmp", "/dev/null", "/dev/tty", "/dev/pts")  # noqa: S108 - the sandbox's own /tmp
READ_ONLY_ROLES = frozenset({"reviewer"})
CURL = "/usr/bin/curl"
MAX_GENERATION = 9999
MAX_SYMLINKS = 40               # the kernel's limit on links followed in one lookup
MAX_SOCKET_PATH = 100           # bytes; sun_path holds 108 with its NUL, and leave room
NAME = re.compile(r"hz[0-9a-f]{12}g[0-9]{1,4}")
BASE_PATH = ("/usr/local/bin", "/usr/bin", "/bin")
# Every variable a spec may set (D4). The adapter's config variable and fixed extras are among them.
LAUNCHER_ENV = frozenset({"HOME", "PATH", "LANG", "TERM", "USER", "HZ_SESSION_SOCKET", "CLAUDE_CONFIG_DIR",
                          "CODEX_HOME", "ENABLE_CLAUDEAI_MCP_SERVERS"})


class SpecRefused(Exception):
    """The session can't be given a sandbox as asked. Fixed wording, no paths: it becomes LaunchFailed."""


def short_id(key: str) -> str:
    return "hz" + hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()[:12]


def sandbox_name(key: str, generation: int) -> str:
    if not 1 <= generation <= MAX_GENERATION:
        raise SpecRefused(f"generation must be from 1 to {MAX_GENERATION}")
    return f"{short_id(key)}g{generation}"


@dataclass(frozen=True)
class SessionLayout:
    """One session's host directory (D2). Only `home`, `bridge`, `git` and `run(gen)` are ever bound in;
    the record, the event spools, the scratch and the canary stay outside every bind."""
    root: Path

    @classmethod
    def at(cls, sessions: Path, key: str) -> "SessionLayout":
        return cls(sessions / short_id(key))

    @property
    def record(self) -> Path:
        return self.root / "session.json"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def bridge(self) -> Path:
        return self.root / "bridge"

    @property
    def daemon(self) -> Path:
        return self.bridge / "daemon"

    @property
    def git(self) -> Path:
        return self.root / "git"            # the session's private git directory (Task 7A, D26)

    @property
    def oa_canary(self) -> Path:
        return self.root / "oa-canary"

    def run(self, generation: int) -> Path:
        return self.root / f"r{generation}"

    def socket(self, generation: int) -> Path:
        return self.run(generation) / "s.sock"

    def probe_socket(self, generation: int) -> Path:
        return self.run(generation) / "p.sock"

    def events(self, generation: int) -> Path:
        return self.root / f"r{generation}-events.jsonl"

    def scratch(self, generation: int) -> Path:
        return self.root / f"r{generation}-scratch"


@dataclass(frozen=True)
class Bind:
    source: Path            # host
    target: Path            # inside
    read_only: bool


@dataclass(frozen=True)
class Egress:
    """One allowed network rule: these exact hosts on port 443, for connections whose executable (or an
    executable ancestor, S5 ADR impact 9) is one of `binaries`."""
    name: str
    hosts: tuple[str, ...]
    binaries: tuple[str, ...]


@dataclass(frozen=True)
class AgentFacts:
    """The session's adapter's part of its sandbox (Task 7 builds it)."""
    cli_root: Path                      # the CLI's install root, bound read-only at its host path
    cli_binary: Path                    # the CLI executable: the model rule's binary
    config_var: str                     # the variable naming the CLI's config directory
    config_dir: Path                    # inside the synthetic home
    login_names: tuple[str, ...]        # the adapter's login files, bound read-only into config_dir
    model_hosts: tuple[str, ...]
    extra_env: Mapping[str, str] = field(default_factory=dict[str, str])
    binds: tuple[Bind, ...] = ()
    read_write: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpecInput:
    key: str
    generation: int
    role: str
    layout: SessionLayout
    worktree: Path
    agent: AgentFacts
    login_files: tuple[Path, ...]       # the chosen account's login files, as configured
    extra_egress: tuple[str, ...]
    extra_ro_mounts: tuple[Path, ...]
    probe_allowed_host: str
    uid: int
    gid: int
    git_binds: tuple[Bind, ...] = ()    # Task 7A: the private git dir, the object store and `.git`, both RO


@dataclass(frozen=True)
class SandboxSpec:
    name: str
    workdir: Path
    binds: tuple[Bind, ...]             # everything but the login files
    logins: tuple[Bind, ...]            # the chosen account's login files, canonical sources
    read_only: tuple[str, ...]          # filesystem policy: paths inside
    read_write: tuple[str, ...]
    egress: tuple[Egress, ...]
    env: Mapping[str, str]
    uid: int
    gid: int


def build_spec(i: SpecInput) -> SandboxSpec:
    name = sandbox_name(i.key, i.generation)
    for sock in (i.layout.socket(i.generation), i.layout.probe_socket(i.generation)):
        if len(os.fsencode(sock)) > MAX_SOCKET_PATH:
            raise SpecRefused("the session socket path is too long; use a shorter state directory")
    hosts = (*i.agent.model_hosts, *i.extra_egress, i.probe_allowed_host)
    if any(h in DENIED_HOSTS or not HOST.fullmatch(h) for h in hosts):
        raise SpecRefused("the egress list names a denied host or an invalid host name")
    if set(i.agent.extra_env) - LAUNCHER_ENV or i.agent.config_var not in LAUNCHER_ENV:
        raise SpecRefused("the adapter sets a variable outside the launcher's allowlist")
    logins: list[Bind] = []
    for configured, login in zip(i.login_files, i.agent.login_names, strict=True):
        try:
            source = configured.resolve(strict=True)
        except (OSError, RuntimeError):
            raise SpecRefused("the chosen account's login file is missing or unresolvable") from None
        if not source.is_file():
            raise SpecRefused("the chosen account's login file is not a regular file")
        logins.append(Bind(source, i.agent.config_dir / login, True))
    worktree_ro = i.role in READ_ONLY_ROLES
    binds = (Bind(i.layout.home, i.layout.home, False),
             Bind(i.worktree, i.worktree, worktree_ro),
             Bind(i.layout.run(i.generation), RUN_INSIDE, True),
             Bind(i.agent.cli_root, i.agent.cli_root, True),
             *i.agent.binds,
             *i.git_binds,
             *(Bind(m, m, True) for m in i.extra_ro_mounts))
    read_only = (*SYSTEM_READ_ONLY, str(RUN_INSIDE), str(i.agent.cli_root),
                 *(str(m) for m in i.extra_ro_mounts), *(str(b.target) for b in logins),
                 *((str(i.worktree),) if worktree_ro else ()),
                 *(str(b.target) for b in i.git_binds if b.read_only))
    read_write = (*SYSTEM_READ_WRITE, str(i.layout.home), *(() if worktree_ro else (str(i.worktree),)),
                  *i.agent.read_write, *(str(b.target) for b in i.git_binds if not b.read_only))
    cli = (str(i.agent.cli_binary),)
    egress = [Egress("model", i.agent.model_hosts, cli)]
    if i.extra_egress:
        egress.append(Egress("extra", i.extra_egress, cli))
    egress.append(Egress("probe_control", (i.probe_allowed_host,), (CURL,)))
    env = {"HOME": str(i.layout.home), "PATH": ":".join((*BASE_PATH, str(i.agent.cli_binary.parent))),
           "LANG": "C.UTF-8", "TERM": "xterm-256color", "USER": "agent",
           "HZ_SESSION_SOCKET": str(SOCKET_INSIDE), i.agent.config_var: str(i.agent.config_dir),
           **i.agent.extra_env}
    return SandboxSpec(name, i.worktree, binds, tuple(logins), read_only, read_write, tuple(egress), env,
                       i.uid, i.gid)


@dataclass(frozen=True)
class Protected:
    """The login material a session must not see (§7): the chosen account's login directory and files,
    and every other configured account's directory and files (the default included when it is not the
    chosen one), each as configured."""
    chosen_dir: Path
    chosen_files: tuple[Path, ...]
    other_dirs: tuple[Path, ...]
    other_files: tuple[Path, ...]


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _route(path: Path) -> tuple[Path, ...]:
    """Every path the kernel walks through to resolve `path`: each component joined to the prefix resolved
    so far, and where that is a symlink, the components of its target in turn. `..` steps back from the
    resolved prefix, as the kernel's does, not lexically. A component that doesn't exist is joined as is."""
    current = Path("/")
    pending = list(reversed((path if path.is_absolute() else Path.cwd() / path).parts[1:]))
    route = [current]
    links = 0
    while pending:
        part = pending.pop()
        current = current.parent if part == ".." else current / part
        route.append(current)
        if current.is_symlink():
            links += 1
            if links > MAX_SYMLINKS:
                raise SpecRefused("a bind source has too many symbolic links")
            try:
                target = current.readlink()
            except OSError:
                raise SpecRefused("a bind source can't be resolved") from None
            current = Path("/") if target.is_absolute() else current.parent
            pending.extend(reversed(target.parts[1:] if target.is_absolute() else target.parts))
    return tuple(route)


def check_credentials(spec: SandboxSpec, protected: Protected, real_home: Path) -> None:
    """D10. The login binds are exactly the chosen account's login files, read-only. No other bind's
    source, by its own path or its canonical one, equals or contains the real home, a login directory or
    a login file, or lies inside another account's login directory (r15 §7), by its own path or any the
    kernel walks through to resolve it (`_route`): a symlink out of that directory doesn't excuse a route
    into it. A source strictly inside the chosen account's own login directory that holds no login file
    is allowed: the Codex CLI installs under its default login directory, and plan 4 chooses only the
    default (D11)."""
    want = {_real(p) for p in protected.chosen_files}
    got = [_real(b.source) for b in spec.logins]
    if sorted(got) != sorted(want) or not all(b.read_only for b in spec.logins):
        raise SpecRefused("the login binds are not exactly the chosen account's login files, read-only")
    guarded = {q for p in (protected.chosen_dir, *protected.chosen_files, *protected.other_dirs,
                           *protected.other_files) for q in (p, _real(p))}
    other_dirs = {q for p in protected.other_dirs for q in (p, _real(p))}
    homes = {real_home, _real(real_home)}
    for b in spec.binds:
        for source in {b.source, _real(b.source)}:
            if any(h.is_relative_to(source) for h in homes):
                raise SpecRefused("a bind would expose the real home directory")
            if any(p.is_relative_to(source) for p in guarded):
                raise SpecRefused("a bind would expose a login directory or login file")
        if any(q.is_relative_to(d) for q in (b.source, *_route(b.source)) for d in other_dirs):
            raise SpecRefused("a bind would reach into another account's login directory")
