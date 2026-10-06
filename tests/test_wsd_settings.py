import os
from pathlib import Path

import pytest

from heterodyne.config import ConfigError
from heterodyne.wsd.settings import resolve


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
