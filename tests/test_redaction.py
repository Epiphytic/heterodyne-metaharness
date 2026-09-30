"""Validation errors must never echo secret-shaped values (review round 4)."""

from pathlib import Path

import pytest

from heterodyne.config import ConfigError, layers, load
from heterodyne.config.policy import effective_tiers
from heterodyne.config.secret_scan import show

# Assembled at runtime so no literal token sits in the repo.
TOKEN = "gh" + "p_" + "Z9" * 18
AWS = "AK" + "IA" + "QRSTUVWXYZ234567"


def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", '[profiles.a]\nadapter = "codex"\nmodel = "m"\n[sandbox]\n'
                                   'egress_approved = ["pypi.org"]\nro_mounts_approved = ["/srv"]\n')
    write(tmp_path, "policy.toml", 'approvers = ["op"]\n')
    return tmp_path


def env(d: Path, **extra: str) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d), **extra}


def error_text(fn: object, *args: object) -> str:
    with pytest.raises(ConfigError) as exc:
        fn(*args)  # type: ignore[operator]
    return str(exc.value)


def test_show_redacts_secret_values_only() -> None:
    assert TOKEN not in show(TOKEN)
    assert "redacted" in show(TOKEN)
    assert TOKEN not in show(["ok", [f"/srv/{TOKEN}"], {TOKEN: "x"}])
    assert show("pypi.org") == "'pypi.org'"
    assert show(["a", "b"]) == "['a', 'b']"
    assert show("run_tests", quote=False) == "run_tests"
    assert TOKEN not in show(TOKEN, quote=False)


@pytest.mark.parametrize(("workstream", "tokens"), [
    (f'[roles]\ncoder = "{TOKEN}"\n', [TOKEN]),
    (f'[roles]\n"{TOKEN}" = "a"\n', [TOKEN]),
    (f'[sandbox]\nextra_egress = ["{TOKEN}.example"]\n', [TOKEN]),
    (f'[sandbox]\nextra_ro_mounts = ["/etc/{TOKEN}"]\n', [TOKEN]),
    (f'[restrict]\nescalate = ["{TOKEN}"]\n', [TOKEN]),
    (f'[restrict]\n"{AWS}" = ["run_tests"]\n', [AWS]),
    (f'"{TOKEN}" = 1\n', [TOKEN]),
])
def test_workstream_errors_do_not_echo_tokens(cfg: Path, workstream: str, tokens: list[str]) -> None:
    write(cfg, "workstreams/w.toml", workstream)
    text = error_text(load, "w", env(cfg))
    assert all(t not in text for t in tokens), text


@pytest.mark.parametrize("policy", [
    f'[tiers]\n"{TOKEN}" = "escalate"\n',
    f'[tiers]\npush_branch = "{TOKEN}"\n',
    f'"{TOKEN}" = 1\n',
    f'[identities."{TOKEN}"]\ngithub = ["x"]\n',
])
def test_policy_errors_do_not_echo_tokens(cfg: Path, policy: str) -> None:
    write(cfg, "policy.toml", policy)
    assert TOKEN not in error_text(load, None, env(cfg))


def test_host_errors_do_not_echo_tokens(cfg: Path) -> None:
    write(cfg, "config.toml", f'[profiles.a]\nadapter = "{TOKEN}"\n')
    assert TOKEN not in error_text(load, None, env(cfg))
    write(cfg, "config.toml", f'[sandbox]\nro_mounts_approved = ["{TOKEN}"]\n')
    assert TOKEN not in error_text(load, None, env(cfg))


def test_env_errors_do_not_echo_tokens(cfg: Path) -> None:
    assert TOKEN not in error_text(load, None, env(cfg, **{f"HETERODYNE_{TOKEN}": "x"}))
    assert TOKEN not in error_text(load, None, env(cfg, HETERODYNE_LOG_LEVEL=TOKEN))


# The validators themselves redact, even when called without the scan in front of them.

def test_validators_redact_without_prior_scan() -> None:
    host = {"profiles": {"a": {"adapter": "codex"}},
            "sandbox": {"egress_approved": [], "ro_mounts_approved": ["/srv"]}}
    for ws in ({"roles": {"coder": TOKEN}}, {"roles": {TOKEN: "zz"}},
               {"sandbox": {"extra_egress": [TOKEN]}}, {"sandbox": {"extra_ro_mounts": [f"/etc/{TOKEN}"]}},
               {"sandbox": {"extra_ro_mounts": [TOKEN]}}, {"sandbox": {TOKEN: []}}, {TOKEN: {}}):
        assert TOKEN not in error_text(layers.check_workstream, "w", ws, host)
    defaults = {"auto_approve": ["run_tests"], "escalate": [], "hard_deny": [], "locked": []}
    assert TOKEN not in error_text(effective_tiers, defaults, {TOKEN: "escalate"}, {})
    assert TOKEN not in error_text(effective_tiers, defaults, {"run_tests": TOKEN}, {})
    assert TOKEN not in error_text(effective_tiers, defaults, {}, {"escalate": [TOKEN]})
    assert TOKEN not in error_text(effective_tiers, defaults, {}, {TOKEN: []})
    merged = {"adapters": {"known": ["codex"]}, "profiles": {TOKEN: {"adapter": TOKEN}}}
    assert TOKEN not in error_text(layers.check_profiles, merged)
    assert TOKEN not in error_text(layers.env_layer, {f"HETERODYNE_{TOKEN}": "x"})
    assert TOKEN not in error_text(layers.check_host, {TOKEN: 1, "approvers": []})


def test_token_after_underscore_or_dash_detected() -> None:
    from heterodyne.config.secret_scan import secret_value

    assert secret_value(f"HETERODYNE_{TOKEN}")
    assert secret_value(f"prefix-{AWS}")
    assert secret_value("risk-management-dashboard-v2") is None
