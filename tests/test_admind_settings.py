from pathlib import Path

import pytest

from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.marmot.nip19 import hex_to_npub

OPERATOR_HEX = "c3" * 32

BASE_CONFIG = """
[platform]
os = "linux"
service_manager = "systemd"
sandbox = "bubblewrap"
[profiles.admin]
adapter = "claude-code"
model = "m1"
[admind]
profile = "admin"
restart_units = ["wsd.service", "runner@x.service"]  # install-agnostic: allow=email (template unit)
[admind.marmot]
relays = ["wss://relay.example.org"]
"""


def write(d: Path, config: str = BASE_CONFIG, operators: str = '["op"]') -> dict[str, str]:
    (d / "config.toml").write_text(config)
    (d / "policy.toml").write_text(
        f'approvers = ["op"]\noperators = {operators}\n'
        f'[identities.op]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n')
    return {"HETERODYNE_CONFIG_DIR": str(d), "HETERODYNE_STATE_DIR": str(d / "state"), "HOME": str(d)}


def test_resolves_defaults_and_operator(tmp_path: Path) -> None:
    env = write(tmp_path)
    s = resolve(load(None, env), env)
    assert s.operator_hex == OPERATOR_HEX
    assert s.adapter_binary == "claude"
    assert s.profile["model"] == "m1"
    assert s.workdir == tmp_path
    units = ("wsd.service", "runner@x.service")  # install-agnostic: allow=email (template unit)
    assert s.restart_units == units
    assert s.chunk_chars == 4000 and s.alert_poll_seconds == 5
    assert s.state_dir == tmp_path / "state" / "admind"
    assert s.alerts_dir == tmp_path / "state" / "alerts"
    assert s.marmot_home == tmp_path / "state" / "admind" / "marmot"
    assert s.relays == ("wss://relay.example.org",)
    assert s.service_manager == "systemd"


@pytest.mark.parametrize(("patch", "message"), [
    ('[admind]\nprofile = "nope"\n', "not defined"),
    ('[profiles.cx]\nadapter = "codex"\n[admind]\nprofile = "cx"\n', "supports"),
    ('[admind]\nprofile = "admin"\nrestart_units = ["../etc/passwd"]\n', "not a unit name"),
    ('[admind]\nprofile = "admin"\nrestart_units = ["-evil.service"]\n', "not a unit name"),
    ('[admind]\nprofile = "admin"\nchunk_chars = 10\n', "chunk_chars"),
    ('[admind]\nprofile = "admin"\nalert_poll_seconds = true\n', "alert_poll_seconds"),
    ('[admind]\nprofile = "admin"\nsurprise = 1\n', "unknown keys"),
    ('[admind]\nprofile = "admin"\n[admind.marmot]\nrelays = []\n', "relays"),
    ('[admind]\nprofile = "admin"\n[admind.marmot]\nrelays = ["https://x"]\n', "relays"),
    ('[admind]\nprofile = "admin"\n[admind.marmot]\nrelays = ["wss://r"]\nhome = "rel/path"\n', "absolute"),
])
def test_invalid_admind_config_is_rejected(tmp_path: Path, patch: str, message: str) -> None:
    config = BASE_CONFIG.split("[admind]")[0] + patch
    if "[admind.marmot]" not in patch:
        config += '[admind.marmot]\nrelays = ["wss://relay.example.org"]\n'
    env = write(tmp_path, config)
    with pytest.raises(ConfigError, match=message):
        resolve(load(None, env), env)


def test_exactly_one_operator_with_a_valid_npub(tmp_path: Path) -> None:
    env = write(tmp_path, operators="[]")
    with pytest.raises(ConfigError, match="exactly one"):
        resolve(load(None, env), env)
    env = write(tmp_path)
    (tmp_path / "policy.toml").write_text(
        'approvers = ["op"]\noperators = ["op"]\n[identities.op]\nmarmot_npub = "npub1bad"\n')
    with pytest.raises(ConfigError, match="not a valid npub") as exc:
        resolve(load(None, env), env)
    assert "npub1bad" not in str(exc.value)


def test_socket_path_length_is_checked(tmp_path: Path) -> None:
    deep = tmp_path / ("d" * 120)
    config = BASE_CONFIG.replace('relays = ["wss://relay.example.org"]',
                                 f'relays = ["wss://relay.example.org"]\nhome = "{deep}"')
    env = write(tmp_path, config)
    with pytest.raises(ConfigError, match="too long"):
        resolve(load(None, env), env)


def test_admind_is_not_settable_from_a_workstream(tmp_path: Path) -> None:
    env = write(tmp_path)
    (tmp_path / "workstreams").mkdir()
    (tmp_path / "workstreams" / "w.toml").write_text('[admind]\nprofile = "admin"\n')
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        load("w", env)
