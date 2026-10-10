"""The OpenShell backend (ADR 0001 §7 Runtime; spike S5): NVIDIA OpenShell 0.1.2 with the podman compute
driver. `policy`, `driver_config` and `create_argv` compile a SandboxSpec (pure); OpenShellBackend runs
the openshell and podman commands.

What OpenShell enforces, as S5 measured it: Landlock filesystem rules (hard requirement), a seccomp
broker that answers every INET socket operation itself, a podman `--network none` netns, and a
TLS-terminating egress proxy keyed by exact host and executable (or executable ancestor). The policy is
fixed at create time.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from heterodyne.sandbox.backend import BackendError, BackendUnavailable, ExecResult
from heterodyne.sandbox.spec import NAME, SandboxSpec


def policy(spec: SandboxSpec) -> dict[str, Any]:
    return {
        "version": 1,
        "filesystem_policy": {"include_workdir": False, "read_only": list(spec.read_only),
                              "read_write": list(spec.read_write)},
        "landlock": {"compatibility": "hard_requirement"},
        "process": {"run_as_user": str(spec.uid), "run_as_group": str(spec.gid)},
        "network_policies": {
            e.name: {"endpoints": [{"host": h, "port": 443} for h in e.hosts],
                     "binaries": [{"path": b} for b in e.binaries]}
            for e in spec.egress},
    }


def driver_config(spec: SandboxSpec) -> dict[str, Any]:
    return {"podman": {"mounts": [
        {"type": "bind", "source": str(b.source), "target": str(b.target), "read_only": b.read_only}
        for b in (*spec.binds, *spec.logins)]}}


def create_argv(binary: str, spec: SandboxSpec, image: str, policy_file: Path) -> list[str]:
    argv = [binary, "sandbox", "create", "--name", spec.name, "--detach", "--from", image,
            "--policy", str(policy_file), "--driver-config-json", json.dumps(driver_config(spec)),
            "--no-credential-warnings"]
    for name, value in spec.env.items():
        argv += ["--env", f"{name}={value}"]
    return [*argv, "--", "sleep", "infinity"]


Runner = Callable[[Sequence[str], bytes | None, float], subprocess.CompletedProcess[bytes]]
# The variables the openshell and podman clients need from wsd's own environment; tool_env adds to them.
TOOL_BASE = ("HOME", "PATH", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "LANG")
CREATE_SECONDS = 300
READY_SECONDS = 60
DELETE_SECONDS = 60
QUERY_SECONDS = 30


def tool_env(base: Mapping[str, str], overrides: Mapping[str, str]) -> dict[str, str]:
    return {**{k: base[k] for k in TOOL_BASE if k in base}, **overrides}


def _runner(env: Mapping[str, str]) -> Runner:
    def run(argv: Sequence[str], data: bytes | None, timeout: float) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(list(argv), input=data, stdin=None if data is not None else subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, env=dict(env), check=False)
    return run


class OpenShellBackend:
    def __init__(self, openshell: str, podman: str, image: str, env: Mapping[str, str], *,
                 runner: Runner | None = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.openshell = openshell
        self.podman = podman
        self.image = image
        self.env = dict(env)
        self.run = runner if runner is not None else _runner(self.env)
        self.clock = clock
        self.sleep = sleep

    def _call(self, argv: Sequence[str], timeout: float = QUERY_SECONDS,
              data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return self.run(argv, data, timeout)
        except subprocess.TimeoutExpired:
            raise BackendError(f"{Path(argv[0]).name} {argv[1]} timed out") from None
        except OSError:
            raise BackendUnavailable(f"{Path(argv[0]).name} can't be run") from None

    def available(self) -> bool:
        try:
            return self._call([self.openshell, "sandbox", "list"]).returncode == 0
        except (BackendError, BackendUnavailable):
            return False

    def names(self) -> set[str]:
        try:
            proc = self._call([self.openshell, "sandbox", "list"])
        except BackendError:
            raise BackendUnavailable("openshell sandbox list timed out") from None
        if proc.returncode != 0:
            raise BackendUnavailable("openshell sandbox list failed")
        return {t for t in proc.stdout.decode("utf-8", "replace").split() if NAME.fullmatch(t)}

    def create(self, spec: SandboxSpec, scratch: Path) -> None:
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        scratch.chmod(0o700)
        policy_file = scratch / "policy.yaml"            # JSON is valid YAML
        fd = os.open(policy_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(policy(spec), fh, indent=1)
        proc = self._call(create_argv(self.openshell, spec, self.image, policy_file), CREATE_SECONDS)
        if proc.returncode != 0:
            raise BackendError("openshell sandbox create failed")
        end = self.clock() + READY_SECONDS
        while self.exec(spec.name, spec.workdir, ["true"], timeout=QUERY_SECONDS).returncode != 0:
            if self.clock() > end:
                raise BackendError("the sandbox did not become ready")
            self.sleep(1)

    def _exec_argv(self, name: str, workdir: Path, tty: bool) -> list[str]:
        return [self.openshell, "sandbox", "exec", "-n", name, "--tty" if tty else "--no-tty",
                "--no-login-shell", "--workdir", str(workdir), "--"]

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        proc = self._call([*self._exec_argv(name, workdir, False), *argv], timeout, input)
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def tty_argv(self, name: str, workdir: Path, argv: Sequence[str]) -> list[str]:
        return ["env", "-i", *(f"{k}={v}" for k, v in self.env.items()),
                *self._exec_argv(name, workdir, True), *argv]

    def pane_env(self) -> Mapping[str, str]:
        return dict(self.env)

    def reaper_argv(self, name: str, deadline: int) -> list[str]:
        return ["env", "-i", *(f"{k}={v}" for k, v in self.env.items()), sys.executable, "-I", "-m",
                "heterodyne.sandbox.reaper", "--deadline", str(deadline), "--openshell", self.openshell,
                "--podman", self.podman, "--image", self.image, name]

    def delete(self, name: str) -> bool:
        try:
            self._call([self.openshell, "sandbox", "delete", name])
        except (BackendError, BackendUnavailable):
            pass                 # whether it went is decided only by the listing below
        end = self.clock() + DELETE_SECONDS
        while True:
            try:
                if name not in self.names():
                    return True
            except BackendUnavailable:
                pass
            if self.clock() > end:
                return False
            self.sleep(1)

    def logs(self, name: str, since: float) -> list[str]:
        proc = self._call([self.openshell, "logs", name, "--since", "10m", "--source", "sandbox",
                           "-n", "2000"])
        found: list[str] = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            stamp, sep, _ = line[1:].partition("]")
            try:
                if line.startswith("[") and sep and float(stamp) >= since - 2:
                    found.append(line)
            except ValueError:
                continue
        return found

    def _container(self, name: str) -> list[str]:
        proc = self._call([self.podman, "ps", "--filter", f"name=^openshell-default--{name}-",
                           "--format", "{{.ID}}"])
        return proc.stdout.decode("utf-8", "replace").split() if proc.returncode == 0 else []

    def kill(self, name: str) -> None:
        """Kill the sandbox's workload container with podman alone (D13's backstop): no OpenShell control
        service is needed, so a sandbox stops at its deadline while the gateway is down."""
        ids = self._container(name)
        if ids and self._call([self.podman, "kill", *ids]).returncode != 0:
            raise BackendError("podman kill failed")

    def network_mode(self, name: str) -> str:
        ids = self._container(name)
        if len(ids) != 1:
            return f"containers={len(ids)}"
        proc = self._call([self.podman, "inspect", "-f", "{{.HostConfig.NetworkMode}}", ids[0]])
        return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else "unknown"

    def workload_pid(self, name: str) -> int:
        ids = self._container(name)
        if len(ids) != 1:
            raise BackendError("the workload container is not exactly one container")
        proc = self._call([self.podman, "inspect", "-f", "{{.State.Pid}}", ids[0]])
        try:
            pid = int(proc.stdout.decode().strip())
        except ValueError:
            raise BackendError("the workload container has no PID") from None
        if proc.returncode != 0 or pid <= 1:
            raise BackendError("the workload container has no PID")
        return pid
