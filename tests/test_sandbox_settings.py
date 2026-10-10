from pathlib import Path

import pytest
from sandbox_env import config_env

from heterodyne.config import ConfigError
from heterodyne.sandbox.settings import WsSandbox, sandbox_settings


def test_defaults(tmp_path: Path) -> None:
    s = sandbox_settings(config_env(tmp_path))
    assert s.backend == "none"
    assert s.image == "localhost/heterodyne-agent:1"
    assert (s.openshell, s.podman) == ("openshell", "podman")
    assert (s.max_lifetime_seconds, s.stop_margin_seconds) == (7200, 900)
    assert (s.probe_allowed_host, s.probe_denied_host) == ("api.openai.com", "example.org")
    assert s.agent_probe_seconds == 240
    assert s.hook_wait_seconds == 5.0
    assert s.binaries == {"claude-code": "claude", "codex": "codex"}
    assert s.profiles["p-two"]["adapter"] == "codex"
    assert dict(s.tool_env) == {}
    assert s.egress_approved == ()
    assert s.workstreams == {}


def test_backend_and_runtime_keys(tmp_path: Path) -> None:
    env = config_env(tmp_path, """
[platform]
sandbox = "openshell"

[sandbox]
max_lifetime_minutes = 60
stop_margin_minutes = 10
tool_env = { PATH = "/opt/podman5/bin:/usr/bin", CONTAINERS_CONF = "/opt/podman5/containers.conf" }
""")
    s = sandbox_settings(env)
    assert s.backend == "openshell"
    assert (s.max_lifetime_seconds, s.stop_margin_seconds) == (3600, 600)
    assert s.tool_env["PATH"] == "/opt/podman5/bin:/usr/bin"


@pytest.mark.parametrize("table, needle", [
    ("bogus = 1", "unknown keys"),
    ("max_lifetime_minutes = 5", "max_lifetime_minutes"),
    ("max_lifetime_minutes = 30\nstop_margin_minutes = 30", "stop_margin_minutes"),
    ("agent_probe_seconds = 10", "agent_probe_seconds"),
    ('probe_denied_host = "not a host"', "probe_denied_host"),
    ('probe_allowed_host = "mcp-proxy.anthropic.com"', "probe_allowed_host"),
    ('image = ""', "image"),
    ('tool_env = { "lower-case" = "x" }', "tool_env"),
    ('tool_env = { HOME = "/elsewhere" }', "tool_env"),
    ('egress_approved = ["Bad_Host"]', "egress_approved"),
])
def test_bad_values_are_refused_by_key(tmp_path: Path, table: str, needle: str) -> None:
    with pytest.raises(ConfigError, match=needle):
        sandbox_settings(config_env(tmp_path, f"\n[sandbox]\n{table}\n"))


def test_tool_env_values_never_appear_in_errors(tmp_path: Path) -> None:
    env = config_env(tmp_path, '\n[sandbox]\ntool_env = { GOOD = "/opt/fake-value/bin", "bad name" = "x" }\n')
    with pytest.raises(ConfigError) as caught:
        sandbox_settings(env)
    assert "fake-value" not in str(caught.value)


def test_workstream_additions_and_local_classes(tmp_path: Path) -> None:
    mounts = tmp_path / "shared"
    mounts.mkdir()
    env = config_env(tmp_path, f"""
[sandbox]
egress_approved = ["pypi.org", "github.com"]
ro_mounts_approved = ["{mounts}"]
""", {
        "alpha": f'[sandbox]\nextra_egress = ["github.com"]\nextra_ro_mounts = ["{mounts}"]\n',
        "beta": '[restrict]\nescalate = ["worktree_edit"]\n',
    })
    s = sandbox_settings(env)
    assert s.workstreams["alpha"] == WsSandbox(("github.com",), (mounts.resolve(),),
                                               frozenset({"worktree_edit"}))
    assert s.workstreams["beta"] == WsSandbox((), (), frozenset())
