import os
from pathlib import Path

import pytest

from heterodyne.admind import asks
from heterodyne.config import ConfigError, paths
from heterodyne.wsd.settings import RelaySettings, resolve, restart_fields, restart_view


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


# --- plan 5i part 2: [wsd.relay], admind's interim relay ---

def with_relay(cfg: Path, text: str) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text() + f"[wsd.relay]\n{text}\n")


def relay(cfg: Path) -> RelaySettings:
    r = resolve(env(cfg)).relay
    assert r is not None
    return r


def test_relay_settings_defaults_and_bounds(cfg: Path) -> None:
    r = relay(cfg)
    assert r == RelaySettings(enabled=True, ask_socket=paths.state_dir(env(cfg)) / "admind" / asks.ASK_SOCKET,
                              question_rounds_before_human=3, owed_hours_before_human=24,
                              notice_backoff_seconds=60.0)
    assert r.ask_socket == cfg / ".local" / "state" / "heterodyne" / "admind" / "ask.sock"   # admind's own
    with_relay(cfg, f'enabled = false\nask_socket = "{cfg}/run/ask.sock"\nquestion_rounds_before_human = 1\n'
               'owed_hours_before_human = 720\nnotice_backoff_seconds = 1800')
    assert resolve(env(cfg)).relay == RelaySettings(False, cfg / "run" / "ask.sock", 1, 720, 1800.0)


def test_relay_ask_socket_expands_home(cfg: Path) -> None:
    with_relay(cfg, 'ask_socket = "~/s/ask.sock"')
    assert relay(cfg).ask_socket == cfg / "s" / "ask.sock"


@pytest.mark.parametrize(("text", "match"), [
    ('enabled = "yes"', "enabled must be true or false"),
    ("enabled = 1", "enabled must be true or false"),
    ('ask_socket = "relative.sock"', "ask_socket must be an absolute path"),
    ('ask_socket = ""', "ask_socket must be a path"),
    ("question_rounds_before_human = 0", "question_rounds_before_human must be an integer from 1 to 100"),
    ("question_rounds_before_human = 101", "question_rounds_before_human must be an integer from 1 to 100"),
    ("question_rounds_before_human = true", "question_rounds_before_human must be an integer"),
    ("owed_hours_before_human = 0", "owed_hours_before_human must be an integer from 1 to 720"),
    ("owed_hours_before_human = 721", "owed_hours_before_human must be an integer from 1 to 720"),
    ("owed_hours_before_human = 2.5", "owed_hours_before_human must be an integer from 1 to 720"),
    ("notice_backoff_seconds = 0", "notice_backoff_seconds must be a number of seconds"),
    ("notice_backoff_seconds = 1801", "notice_backoff_seconds must be a number of seconds"),
    ("notice_backoff_seconds = false", "notice_backoff_seconds must be a number of seconds"),
    ("backoff = 5", "unknown keys"),
])
def test_bad_relay_settings_are_refused(cfg: Path, text: str, match: str) -> None:
    with_relay(cfg, text)
    with pytest.raises(ConfigError, match=rf"\[wsd.relay\] .*{match}|\[wsd.relay\]: {match}"):
        resolve(env(cfg))


def test_relay_settings_are_host_only(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", (cfg / "workstreams/alpha.toml").read_text()
          + "[wsd.relay]\nenabled = false\n")
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        resolve(env(cfg))


def test_a_relay_setting_change_needs_a_restart(cfg: Path) -> None:
    running = restart_view(resolve(env(cfg)))
    with_relay(cfg, "question_rounds_before_human = 4")
    assert restart_fields(running, restart_view(resolve(env(cfg)))) == ["relay"]
