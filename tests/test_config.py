import shutil
from pathlib import Path

import pytest

from heterodyne.config import ConfigError, load

ROOT = Path(__file__).resolve().parent.parent


def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", """
[profiles.a]
adapter = "codex"
model = "m1"
[profiles.b]
adapter = "claude-code"
model = "m2"
[roles]
coder = "a"
[sandbox]
egress_approved = ["pypi.org"]
[timeouts]
reminder_hours = 8
""")
    write(tmp_path, "policy.toml", 'approvers = ["op"]\n')
    return tmp_path


def env(d: Path, **extra: str) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d), **extra}


def test_precedence_and_sources(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[roles]\ncoder = "b"\n[timeouts]\nhook_wait_seconds = 9\n')
    c = load("w", env(cfg, HETERODYNE_LOG_LEVEL="debug"))
    assert c.get("timeouts.gatekeeper_seconds") == 60
    assert c.sources["timeouts.gatekeeper_seconds"] == "defaults"
    assert c.get("timeouts.reminder_hours") == 8
    assert c.sources["timeouts.reminder_hours"] == "host:config.toml"
    assert c.get("roles.coder") == "b" and c.sources["roles.coder"] == "workstream:w.toml"
    assert c.get("debug.log_level") == "debug" and c.sources["debug.log_level"] == "env:HETERODYNE_LOG_LEVEL"
    assert c.policy.approvers == ("op",)


def test_policy_keys_rejected_outside_policy_toml(cfg: Path) -> None:
    # Prepended: appended after a [table] header it would parse as a key of that table.
    write(cfg, "config.toml", 'approvers = ["x"]\n' + (cfg / "config.toml").read_text())
    with pytest.raises(ConfigError, match="policy.toml"):
        load(None, env(cfg))


def test_workstream_rejects_policy_and_unknown_keys(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[tiers]\npush_branch = "auto_approve"\n')
    with pytest.raises(ConfigError, match="not allowed"):
        load("w", env(cfg))
    write(cfg, "workstreams/w.toml", 'approvers = ["x"]\n')
    with pytest.raises(ConfigError, match="not allowed"):
        load("w", env(cfg))


def test_workstream_role_must_name_host_profile(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[roles]\ncoder = "nope"\n')
    with pytest.raises(ConfigError, match="profile"):
        load("w", env(cfg))


def test_profile_adapter_must_be_known(cfg: Path) -> None:
    write(cfg, "config.toml", '[profiles.a]\nadapter = "mystery"\n')
    with pytest.raises(ConfigError, match="adapter"):
        load(None, env(cfg))


def test_extra_egress_must_be_host_approved(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[sandbox]\nextra_egress = ["evil.example"]\n')
    with pytest.raises(ConfigError, match="egress_approved"):
        load("w", env(cfg))


def test_inline_secret_rejected_reference_accepted(cfg: Path) -> None:
    base = (cfg / "config.toml").read_text()
    write(cfg, "config.toml", base + '[integrations.marmot]\nauth_token = "abc123"\n')
    with pytest.raises(ConfigError, match="secret"):
        load(None, env(cfg))
    write(cfg, "config.toml", base + '[integrations.marmot]\nauth_token = { command = "pass show x" }\n')
    assert load(None, env(cfg)).get("integrations.marmot.auth_token") == {"command": "pass show x"}


def test_unknown_env_var_rejected(cfg: Path) -> None:
    with pytest.raises(ConfigError, match="HETERODYNE_ROLES"):
        load(None, env(cfg, HETERODYNE_ROLES="x"))


def test_restrict_applies_to_effective_policy(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[restrict]\nescalate = ["run_tests"]\n')
    assert load("w", env(cfg)).policy.tiers["run_tests"] == "escalate"
    assert load(None, env(cfg)).policy.tiers["run_tests"] == "auto_approve"


def test_missing_policy_fails_closed(cfg: Path) -> None:
    (cfg / "policy.toml").unlink()
    assert load(None, env(cfg)).policy.approvers == ()


def test_examples_load(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "examples", tmp_path, dirs_exist_ok=True)
    text = (tmp_path / "config.toml").read_text().replace("<linux-or-macos>", "linux")
    (tmp_path / "config.toml").write_text(text)
    assert load("example", env(tmp_path)).get("roles.coder") == "coder"


def with_ro_allowlist(cfg: Path, entries: list[str]) -> None:
    text = (cfg / "config.toml").read_text()
    listed = ", ".join(f'"{e}"' for e in entries)
    old = 'egress_approved = ["pypi.org"]'
    write(cfg, "config.toml", text.replace(old, f"{old}\nro_mounts_approved = [{listed}]"))


def ws_mounts(cfg: Path, mounts: list[str]) -> None:
    listed = ", ".join(f'"{m}"' for m in mounts)
    write(cfg, "workstreams/w.toml", f"[sandbox]\nextra_ro_mounts = [{listed}]\n")


def test_ro_mounts_require_host_allowlist(cfg: Path) -> None:
    ws_mounts(cfg, ["/srv/data"])
    with pytest.raises(ConfigError, match="ro_mounts_approved"):
        load("w", env(cfg))
    with_ro_allowlist(cfg, [])
    with pytest.raises(ConfigError, match="ro_mounts_approved"):
        load("w", env(cfg))


def test_ro_mounts_within_approved_tree(cfg: Path) -> None:
    with_ro_allowlist(cfg, ["/srv/data"])
    for ok in (["/srv/data"], ["/srv/data/sets/a"], ["/srv/data/./sets/"]):
        ws_mounts(cfg, ok)
        assert load("w", env(cfg)).get("sandbox.extra_ro_mounts") == ok
    for bad in (["/srv/database"], ["/srv/data/../etc"], ["/srv"], ["relative/path"]):
        ws_mounts(cfg, bad)
        with pytest.raises(ConfigError, match="ro_mounts_approved|absolute"):
            load("w", env(cfg))


def test_ro_mount_symlink_out_of_approved_tree_rejected(
        cfg: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    approved = tmp_path_factory.mktemp("approved")
    outside = tmp_path_factory.mktemp("outside")
    (approved / "escape").symlink_to(outside)
    with_ro_allowlist(cfg, [str(approved)])
    ws_mounts(cfg, [str(approved / "escape")])
    with pytest.raises(ConfigError, match="ro_mounts_approved"):
        load("w", env(cfg))


def test_policy_toml_inline_secret_rejected(cfg: Path) -> None:
    write(cfg, "policy.toml", 'approvers = ["op"]\n[identities.op]\napi_key = "hunter2"\n')
    with pytest.raises(ConfigError, match="secret"):
        load(None, env(cfg))


def test_workstream_inline_secret_rejected(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[repos]\ntoken = "hunter2"\n')
    with pytest.raises(ConfigError, match="secret"):
        load("w", env(cfg))


@pytest.mark.parametrize("ref", [
    '{ command = "" }',
    "{ command = 5 }",
    '{ file = "a", command = "b" }',
    '{ command = "x", extra = "y" }',
    "{}",
])
def test_malformed_secret_reference_rejected(cfg: Path, ref: str) -> None:
    base = (cfg / "config.toml").read_text()
    write(cfg, "config.toml", base + f"[integrations.marmot]\nauth_token = {ref}\n")
    with pytest.raises(ConfigError, match="secret"):
        load(None, env(cfg))
