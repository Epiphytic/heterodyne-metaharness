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
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
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
DIRECT_TARGET = "1.1.1.1:443"       # install-agnostic: allow=ip-port (the probes' literal-address target)


def classify(path: Path) -> str:
    """present: it opens. absent: its directory lists and the name is not in it. unknown: anything else,
    such as an unlistable directory, or a listed name that doesn't open (a dangling symlink, EACCES)."""
    try:
        path.open("rb").close()
        return "present"
    except OSError:
        pass
    try:
        return "unknown" if path.name in {p.name for p in path.parent.iterdir()} else "absent"
    except OSError:
        return "unknown"


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
    """The probes' input, after the host-side preconditions: a fresh Other accounts canary readable
    outside, each chosen login file's hash, and the other accounts' classes. It holds no secret."""
    try:
        ctx.layout.oa_canary.write_text(secrets.token_hex(8))
        ctx.layout.oa_canary.read_bytes()
    except OSError:
        raise SelfTestFailed("canary-precondition") from None
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


class OpenShellSelfTest:
    def __init__(self, *, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.wall = wall
        self.sleep = sleep

    def files(self) -> Mapping[str, str]:
        return {"probes.py": PROBES_SOURCE.read_text()}

    def _canary(self, ctx: ProbeContext) -> None:
        try:
            ctx.real_home_canary.write_text(secrets.token_hex(8))
            ctx.real_home_canary.read_bytes()
        except OSError:
            raise SelfTestFailed("canary-precondition") from None

    def _logs(self, ctx: ProbeContext, since: float, path: str) -> None:
        self.sleep(LOG_SETTLE_SECONDS)       # outside: the supervisor's log must show this run's lines
        lines = ctx.backend.logs(ctx.spec.name, since)
        missing = [check for check, needle in log_needles(ctx, path) if not any(needle in ln for ln in lines)]
        if missing:
            raise SelfTestFailed(", ".join(missing))

    def exec_path(self, ctx: ProbeContext) -> None:
        self._canary(ctx)
        if ctx.backend.network_mode(ctx.spec.name) != "none":
            raise SelfTestFailed("outer-fence-network-none")
        data = json.dumps(probe_config(ctx, "exec")).encode()
        since = self.wall()
        try:
            r = ctx.backend.exec(ctx.spec.name, ctx.spec.workdir, ["python3", "-I", str(PROBES_INSIDE)],
                                 input=data, timeout=PROBE_SECONDS)
        except BackendError:
            raise SelfTestFailed("probes-timeout") from None
        if r.returncode != 0:
            failed = [ln.split()[1] for ln in r.stdout.decode("utf-8", "replace").splitlines()
                      if ln.startswith("FAIL ") and len(ln.split()) > 1]
            raise SelfTestFailed(", ".join(failed) or "probes")
        self._logs(ctx, since, "exec")

    def agent_path(self, ctx: ProbeContext) -> None:
        raise SelfTestFailed("agent-path-not-built")      # Task 9 replaces this
