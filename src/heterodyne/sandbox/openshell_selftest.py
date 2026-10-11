"""The §7 launch self-test under OpenShell (ADR 0001 §7 as amended by revision 15; spike S5).

Both paths run the same probes (resources/probes.py) inside the sandbox, with host-side preconditions
before and the OpenShell supervisor's own log after. The exec path runs them by a separate `sandbox
exec`; the agent path (Task 9) has the agent run them through its own tool and counts only results sent
by a peer the host verifies itself. Every failure raises SelfTestFailed naming the check; the runtime
then deletes the sandbox and the launch fails.
"""

import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heterodyne.agents.base import Adapter
from heterodyne.sandbox.backend import BackendError
from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed
from heterodyne.sandbox.spec import LAUNCHER_ENV, PROBES_INSIDE, Bind

PROBES_SOURCE = Path(__file__).resolve().with_name("resources") / "probes.py"
# The agent-path probe as the host requires to see it in /proc: the image's python, isolated mode (no
# PYTHON* variables, no user site), the read-only script.
PROBE_EXE = "/usr/bin/python3.12"
PROBE_ARGV = ("python3", "-I", str(PROBES_INSIDE), "--agent")
# What OpenShell 0.1.2 itself puts in a workload's environment (S5; none carries a secret).
OPENSHELL_ENV = frozenset({"OPENSHELL_SANDBOX", "OPENSHELL_USER_ENVIRONMENT", "SSL_CERT_FILE",
                           "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "GIT_SSL_CAINFO", "NODE_EXTRA_CA_CERTS",
                           "DENO_CERT",
                           "container", "HOSTNAME", "DEBIAN_FRONTEND", "SHELL"})
SHELL_ENV = frozenset({"PWD", "SHLVL", "_", "OLDPWD"})
COMMON_CHECKS = ("real-home-canary-unreadable", "non-allowlisted-host-blocked", "direct-network-blocked",
                 "control-op-rejected", "wsd-socket-absent", "allowlisted-host-reachable")
TAIL_CHECKS = ("hook-event-accepted", "host-env-not-inherited", "openshell-control-material-unreadable",
               "other-accounts")
EXEC_CHECKS = (*COMMON_CHECKS, "model-host-exec-path", *TAIL_CHECKS)
AGENT_CHECKS = (*COMMON_CHECKS, "model-host-agent-path", *TAIL_CHECKS)
PROBE_SECONDS = 180
LOG_SETTLE_SECONDS = 3
LIST_LIMIT = 4096                   # entries classify reads from a login's directory before "unknown"
CANARY_BYTES = 16
VERDICT = re.compile(r"(PASS|FAIL) (\S+) \[.*\]")
DIRECT_TARGET = "1.1.1.1:443"       # install-agnostic: allow=ip-port (the probes' literal-address target)


def classify(path: Path) -> str:
    """present: it opens as a regular file. absent: its directory lists, within LIST_LIMIT entries, and
    the name is not in it. unknown: anything else, such as an unlistable or oversized directory, a FIFO or
    device, or a listed name that doesn't open (a dangling symlink, EACCES). Never blocks."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        pass
    else:
        try:
            return "present" if stat.S_ISREG(os.fstat(fd).st_mode) else "unknown"
        finally:
            os.close(fd)
    try:
        with os.scandir(path.parent) as entries:
            for count, entry in enumerate(entries):
                if count >= LIST_LIMIT or entry.name == path.name:
                    return "unknown"
    except OSError:
        return "unknown"
    return "absent"


@dataclass(frozen=True)
class HeldCanary:
    """A canary the host wrote, held open (its directory and the file) until the probe has run."""
    parent: int
    fd: int
    name: str
    ident: tuple[int, int]          # (st_dev, st_ino)
    value: bytes

    def verify(self, check: str) -> None:
        """SelfTestFailed(check) unless the canary's name, looked up in its held directory without
        following a link, is still the file written: the same inode, a regular file with one link,
        holding exactly the value."""
        try:
            info = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
            same = ((info.st_dev, info.st_ino) == self.ident and stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1 and os.pread(self.fd, len(self.value) + 1, 0) == self.value)
        except OSError:
            same = False
        if not same:
            raise SelfTestFailed(check)

    def close(self) -> None:
        os.close(self.fd)
        os.close(self.parent)


def write_canary(path: Path) -> HeldCanary:
    """Write a fresh value to the canary through its directory, never following a link, and hold both
    open. Anything but a regular file with one link that reads back exactly the value fails
    canary-precondition, so the probe's "unreachable" inside is never vacuous."""
    value = secrets.token_hex(CANARY_BYTES // 2).encode()
    try:
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise SelfTestFailed("canary-precondition") from None
    fd = -1
    try:
        fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SelfTestFailed("canary-precondition")
        os.ftruncate(fd, 0)
        if os.pwrite(fd, value, 0) != len(value):
            raise SelfTestFailed("canary-precondition")
        held = HeldCanary(parent, fd, path.name, (info.st_dev, info.st_ino), value)
        held.verify("canary-precondition")
        return held
    except BaseException as exc:
        if fd >= 0:
            os.close(fd)
        os.close(parent)
        if isinstance(exc, OSError):
            raise SelfTestFailed("canary-precondition") from None
        raise


@contextmanager
def held_canaries(ctx: ProbeContext) -> Generator[tuple[HeldCanary, HeldCanary]]:
    """The real-home and Other accounts canaries, freshly written and held until the block ends."""
    home = write_canary(ctx.real_home_canary)
    try:
        oa = write_canary(ctx.layout.oa_canary)
        try:
            yield home, oa
        finally:
            oa.close()
    finally:
        home.close()


def other_accounts(others: Sequence[tuple[str, Path]], logins: Sequence[Bind]) -> list[dict[str, Any]]:
    """Every login file of every other account, by configured and canonical path. An account whose
    canonical login file is a chosen one (an alias of the chosen account) is not "other"."""
    chosen = {os.path.realpath(b.source) for b in logins}
    found: list[dict[str, Any]] = []
    for account, configured in others:
        canonical = os.path.realpath(configured)
        if canonical in chosen:
            continue
        found.append({"account": account, "class": classify(configured),
                      "paths": sorted({str(configured), canonical})})
    return found


def env_allowed(adapter: Adapter, path: str) -> frozenset[str]:
    base = LAUNCHER_ENV | OPENSHELL_ENV | SHELL_ENV
    return base | adapter.tool_env if path == "agent" else base


def probe_config(ctx: ProbeContext, path: str) -> dict[str, Any]:
    """The probes' input: each chosen login file's hash and the other accounts' classes. It holds no
    secret. The caller holds the canaries (`held_canaries`) around it and the probe run."""
    chosen = [{"path": str(b.target), "sha256": hashlib.sha256(b.source.read_bytes()).hexdigest()}
              for b in ctx.spec.logins]
    return {"path": path, "real_home_canary": str(ctx.real_home_canary),
            "oa_canary": str(ctx.layout.oa_canary),
            "other_accounts": other_accounts(ctx.others, ctx.spec.logins), "chosen": chosen,
            "allowed": ctx.settings.probe_allowed_host, "denied": ctx.settings.probe_denied_host,
            "model_host": ctx.adapter.model_hosts[0], "wsd_socket": str(ctx.wsd_socket),
            "env_allowed": sorted(env_allowed(ctx.adapter, path)),
            "env_user": sorted(LAUNCHER_ENV | OPENSHELL_ENV)}


def log_needles(ctx: ProbeContext, path: str) -> list[tuple[str, str]]:
    """The supervisor log lines this run must have produced. The model host's rule names only the CLI
    binary, and OpenShell also authorizes a connection whose executable ancestor is listed: ALLOWED
    proves the agent path ran inside the agent's tree, DENIED proves the exec path did not."""
    model, denied = ctx.adapter.model_hosts[0], ctx.settings.probe_denied_host
    refused = "[reason:transparent_tcp_policy_denied]"
    ancestry = (("agent-ancestry-allowed", f"ALLOWED /usr/bin/curl(0) -> {model}:443 [policy:model")
                if path == "agent" else
                ("exec-path-not-agent", f"DENIED /usr/bin/curl(0) -> {model}:443 {refused}"))
    return [("proxy-logged-refusal", f"DENIED /usr/bin/curl(0) -> {denied}:443 {refused}"),
            ("proxy-logged-allow",
             f"ALLOWED /usr/bin/curl(0) -> {ctx.settings.probe_allowed_host}:443 [policy:probe_control"),
            ("proxy-logged-direct-deny", f"-> {DIRECT_TARGET} {refused}"),
            ancestry]


def _results(returncode: int, stdout: bytes, checks: Sequence[str]) -> None:
    """Pass only on exactly one PASS line for each of `checks`, nothing else, then `DONE 0` last and a
    zero exit. A FAIL names its check (only names from `checks` are ever echoed); any other shortfall is
    probes-output."""
    lines = stdout.decode("utf-8", "replace").splitlines()
    found = [VERDICT.fullmatch(ln) for ln in lines]
    failed = [m[2] for m in found if m and m[1] == "FAIL" and m[2] in checks]
    if returncode != 0 or failed:
        raise SelfTestFailed(", ".join(dict.fromkeys(failed)) or "probes")
    names = [m[2] for m in found[:-1] if m]
    complete = lines[-1:] == ["DONE 0"] and len(names) == len(lines) - 1
    if not complete or sorted(names) != sorted(checks):
        raise SelfTestFailed("probes-output")


class OpenShellSelfTest:
    def __init__(self, *, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.wall = wall
        self.sleep = sleep

    def files(self) -> Mapping[str, str]:
        return {"probes.py": PROBES_SOURCE.read_text()}

    def _logs(self, ctx: ProbeContext, since: float, path: str) -> None:
        self.sleep(LOG_SETTLE_SECONDS)       # outside: the supervisor's log must show this run's lines
        lines = ctx.backend.logs(ctx.spec.name, since)
        missing = [check for check, needle in log_needles(ctx, path) if not any(needle in ln for ln in lines)]
        if missing:
            raise SelfTestFailed(", ".join(missing))

    def exec_path(self, ctx: ProbeContext) -> None:
        with held_canaries(ctx) as (home, oa):
            if ctx.backend.network_mode(ctx.spec.name) != "none":
                raise SelfTestFailed("outer-fence-network-none")
            data = json.dumps(probe_config(ctx, "exec")).encode()
            home.verify("canary-precondition")
            oa.verify("canary-precondition")
            since = self.wall()
            try:
                r = ctx.backend.exec(ctx.spec.name, ctx.spec.workdir, ["python3", "-I", str(PROBES_INSIDE)],
                                     input=data, timeout=PROBE_SECONDS)
            except BackendError:
                raise SelfTestFailed("probes-timeout") from None
            home.verify("real-home-canary-changed")
            oa.verify("other-accounts-canary-changed")
        _results(r.returncode, r.stdout, EXEC_CHECKS)
        self._logs(ctx, since, "exec")

    def agent_path(self, ctx: ProbeContext) -> None:
        raise SelfTestFailed("agent-path-not-built")      # Task 9 replaces this
