import dataclasses
import json
from pathlib import Path

from heterodyne.sandbox.openshell import create_argv, driver_config, policy
from heterodyne.sandbox.spec import Bind, Egress, SandboxSpec


def spec() -> SandboxSpec:
    return SandboxSpec(
        name="hz0123456789abg1", workdir=Path("/w/repo"),
        binds=(Bind(Path("/s/home"), Path("/s/home"), False), Bind(Path("/s/r1"), Path("/run/hz"), True)),
        logins=(Bind(Path("/h/.codex/auth.json"), Path("/s/home/.codex/auth.json"), True),),
        read_only=("/usr", "/run/hz"), read_write=("/tmp", "/s/home"),  # noqa: S108 - a path inside
        egress=(Egress("model", ("chatgpt.com",), ("/opt/codex/bin/codex",)),
                Egress("probe_control", ("api.openai.com",), ("/usr/bin/curl",))),
        env={"HOME": "/s/home", "PATH": "/usr/bin"}, uid=1000, gid=1001)


def test_policy() -> None:
    assert policy(spec()) == {
        "version": 1,
        "filesystem_policy": {"include_workdir": False, "read_only": ["/usr", "/run/hz"],
                              "read_write": ["/tmp", "/s/home"]},  # noqa: S108 - a path inside
        "landlock": {"compatibility": "hard_requirement"},
        "process": {"run_as_user": "1000", "run_as_group": "1001"},
        "network_policies": {
            "model": {"endpoints": [{"host": "chatgpt.com", "port": 443}],
                      "binaries": [{"path": "/opt/codex/bin/codex"}]},
            "probe_control": {"endpoints": [{"host": "api.openai.com", "port": 443}],
                              "binaries": [{"path": "/usr/bin/curl"}]},
        },
    }


def test_driver_config_binds_everything_including_logins() -> None:
    mounts = driver_config(spec())["podman"]["mounts"]
    assert mounts == [
        {"type": "bind", "source": "/s/home", "target": "/s/home", "read_only": False},
        {"type": "bind", "source": "/s/r1", "target": "/run/hz", "read_only": True},
        {"type": "bind", "source": "/h/.codex/auth.json", "target": "/s/home/.codex/auth.json",
         "read_only": True},
    ]


def test_create_argv() -> None:
    argv = create_argv("openshell", spec(), "localhost/heterodyne-agent:1", Path("/s/r1-scratch/policy.yaml"))
    assert argv[:12] == ["openshell", "sandbox", "create", "--name", "hz0123456789abg1", "--detach", "--from",
                         "localhost/heterodyne-agent:1", "--policy", "/s/r1-scratch/policy.yaml",
                         "--driver-config-json", json.dumps(driver_config(spec()))]
    assert argv[12:] == ["--no-credential-warnings", "--env", "HOME=/s/home", "--env", "PATH=/usr/bin",
                         "--", "sleep", "infinity"]


def test_create_argv_never_requests_a_gpu() -> None:
    """OpenShell's GPU baseline makes /proc writable (boundary_server.rs:150-159; Task 9B)."""
    argv = create_argv("openshell", spec(), "img:1", Path("/s/r1-scratch/policy.yaml"))
    assert not any("gpu" in a.lower() for a in argv[:argv.index("--")])
    assert "gpu" not in json.dumps(policy(spec())).lower()


INSTALL = Path("/opt/codex")


def install_spec() -> SandboxSpec:
    """spec() with the CLI's install root bound read-only at its host path, as build_spec binds it."""
    base = spec()
    return dataclasses.replace(base, binds=(*base.binds, Bind(INSTALL, INSTALL, True)), install=INSTALL)


def test_the_install_bind_is_exactly_the_four_s9_8_fields() -> None:
    """Unsupported (today, and on every gateway outside NONRECURSIVE_VERSIONS): option A's object."""
    mounts = driver_config(install_spec(), nonrecursive=False)["podman"]["mounts"]
    assert mounts[2] == {"type": "bind", "source": "/opt/codex", "target": "/opt/codex", "read_only": True}
    assert json.dumps(driver_config(install_spec(), nonrecursive=False)) == json.dumps(
        driver_config(install_spec()))
    assert all(len(m) == 4 for m in mounts)


def test_a_supported_gateway_adds_recursive_false_to_the_install_bind_only() -> None:
    mounts = driver_config(install_spec(), nonrecursive=True)["podman"]["mounts"]
    assert mounts[2] == {"type": "bind", "source": "/opt/codex", "target": "/opt/codex", "read_only": True,
                         "recursive": False}
    plain = driver_config(install_spec())["podman"]["mounts"]
    assert mounts[:2] + mounts[3:] == plain[:2] + plain[3:]
    assert [len(m) for m in mounts] == [4, 4, 5, 4]


def test_create_argv_carries_the_bind_mode() -> None:
    policy_file = Path("/s/r1-scratch/policy.yaml")
    for nonrecursive in (True, False):
        argv = create_argv("openshell", install_spec(), "img:1", policy_file, nonrecursive=nonrecursive)
        config = argv[argv.index("--driver-config-json") + 1]
        assert config == json.dumps(driver_config(install_spec(), nonrecursive=nonrecursive))
    assert create_argv("openshell", install_spec(), "img:1", policy_file) == create_argv(
        "openshell", install_spec(), "img:1", policy_file, nonrecursive=False)
