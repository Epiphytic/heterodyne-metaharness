"""The OpenShell backend (ADR 0001 §7 Runtime; spike S5): NVIDIA OpenShell 0.1.2 with the podman compute
driver. `policy`, `driver_config` and `create_argv` compile a SandboxSpec (pure); OpenShellBackend runs
the openshell and podman commands.

What OpenShell enforces, as S5 measured it: Landlock filesystem rules (hard requirement), a seccomp
broker that answers every INET socket operation itself, a podman `--network none` netns, and a
TLS-terminating egress proxy keyed by exact host and executable (or executable ancestor). The policy is
fixed at create time.
"""

import json
from pathlib import Path
from typing import Any

from heterodyne.sandbox.spec import SandboxSpec


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
