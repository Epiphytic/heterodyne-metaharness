import os
from pathlib import Path

import pytest

from heterodyne.config import ConfigError
from heterodyne.wsd.settings import resolve, restart_fields, restart_view


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here may see the operator's btq, beads or bd settings."""
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", f"""
[profiles.a]
adapter = "codex"
model = "m1"
[profiles.b]
adapter = "claude-code"
model = "m2"
[integrations.beads]
btq = "{tmp_path}/btq"
dolt_port = 3307
credentials = {{ file = "{tmp_path}/btq-credentials.json" }}
""")
    write(tmp_path, "workstreams/alpha.toml", f"""
[roles]
coder = "b"
[repos]
default = "{tmp_path}/repos/proj"
docs = "~/docs"
""")
    return tmp_path


def env(d: Path) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d)}


def test_resolve_defaults_and_workstreams(cfg: Path) -> None:
    s = resolve(env(cfg))
    assert s.timer_owner == "internal"
    assert (s.backstop_seconds, s.reconcile_seconds, s.inbox_attempts_before_human) == (60.0, 300.0, 3)
    assert s.state_dir == cfg / ".local" / "state" / "heterodyne" / "wsd"
    assert s.btq_locations == {"dolt_port": "3307", "credentials": str(cfg / "btq-credentials.json")}
    [ws] = s.workstreams
    assert (ws.name, ws.coder_role, ws.coder_profile) == ("alpha", "coder", "b")
    assert ws.repos == {"default": cfg / "repos" / "proj", "docs": cfg / "docs"}
    assert (ws.limits.launch_failures_before_human, ws.limits.park_attempts_before_human) == (2, 3)


def test_wsd_table_is_host_only(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[wsd]\nbackstop_seconds = 1\n')
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        resolve(env(cfg))


def test_inline_credentials_are_refused(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace(
        f'credentials = {{ file = "{cfg}/btq-credentials.json" }}', 'credentials = "inline"')
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError):
        resolve(env(cfg))


def test_workstream_needs_default_repo(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\ncoder = "b"\n[repos]\nother = "/x"\n')
    with pytest.raises(ConfigError, match="default"):
        resolve(env(cfg))


def test_relative_repo_is_refused(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\ncoder = "b"\n[repos]\ndefault = "proj"\n')
    with pytest.raises(ConfigError, match="absolute"):
        resolve(env(cfg))


def test_workstream_file_name_must_be_a_slug(cfg: Path) -> None:
    write(cfg, "workstreams/Bad Name.toml", "")
    with pytest.raises(ConfigError, match="slug"):
        resolve(env(cfg))


def test_coder_profile_must_exist(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\nreview = "a"\n[repos]\ndefault = "/x"\n')
    with pytest.raises(ConfigError, match="roles.coder"):
        resolve(env(cfg))


def test_unknown_wsd_key_is_refused(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text() + "[wsd]\nbackstop = 5\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        resolve(env(cfg))


def test_unknown_beads_integration_key_is_refused(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace("dolt_port = 3307", 'dolt_port = 3307\nrelay = "x"')
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError, match=r"\[integrations.beads\]: unknown keys"):
        resolve(env(cfg))


def test_btq_checkout_is_required(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace(f'btq = "{cfg}/btq"\n', "")
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError, match="btq must be a path"):
        resolve(env(cfg))


def test_integrations_marmot_is_left_alone(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text()
          + '[integrations.marmot]\nsocket = "/run/x.sock"\n')
    assert resolve(env(cfg)).btq_checkout == cfg / "btq"


def test_restart_view_is_equal_for_two_resolves_of_the_same_files(cfg: Path) -> None:
    """AU-4 §5.2: `ConfiguredAccounts` has no value equality, so reload compares these plain values."""
    first, second = resolve(env(cfg)), resolve(env(cfg))
    assert first != second                         # the settings objects themselves never compare equal
    assert restart_view(first) == restart_view(second)
    assert restart_fields(restart_view(first), restart_view(second)) == []


def test_the_credential_locks_live_in_the_state_dir(cfg: Path) -> None:
    s = resolve(env(cfg))
    assert s.credlock_dir == s.state_dir / "credlocks"
    assert s.credlock_dir != s.lock_dir


@pytest.mark.parametrize("owner", ['"other"', '"Systemd"', 'true', '1', '[]'])
def test_timer_owner_is_explicit_and_closed(cfg: Path, owner: str) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text()
          + f"[wsd]\ntimer_owner = {owner}\n")
    with pytest.raises(ConfigError, match="timer_owner"):
        resolve(env(cfg))


def test_systemd_owner_preserves_effective_cadences_and_requires_restart(cfg: Path) -> None:
    before = resolve(env(cfg))
    write(cfg, "config.toml", (cfg / "config.toml").read_text()
          + '[wsd]\ntimer_owner = "systemd"\nbackstop_seconds = 20\n')
    after = resolve(env(cfg))
    assert after.timer_owner == "systemd"
    assert (after.backstop_seconds, after.reconcile_seconds) == (20.0, 300.0)
    assert set(restart_fields(restart_view(before), restart_view(after))) == {
        "timer_owner", "backstop_seconds"}


@pytest.mark.parametrize("key", ["backstop_seconds", "reconcile_seconds"])
@pytest.mark.parametrize("value", ["0.5", "20.5", "true", "0", "86401"])
def test_systemd_owner_refuses_nonintegral_or_out_of_range_cadence(
        cfg: Path, key: str, value: str) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text()
          + f'[wsd]\ntimer_owner = "systemd"\n{key} = {value}\n')
    with pytest.raises(ConfigError, match=key):
        resolve(env(cfg))


def test_internal_owner_retains_fractional_cadence(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text()
          + '[wsd]\ntimer_owner = "internal"\nbackstop_seconds = 0.5\n')
    assert resolve(env(cfg)).backstop_seconds == 0.5
