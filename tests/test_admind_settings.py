from pathlib import Path

import pytest

from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.config.secret_scan import show
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


NPUB = hex_to_npub(OPERATOR_HEX)


def _with_units(unit: str) -> str:
    old = 'restart_units = ["wsd.service", "runner'
    return BASE_CONFIG.replace(old, f'restart_units = ["{unit}"]\n# ["runner')


def _assert_redacted(exc: pytest.ExceptionInfo[ConfigError], kind: str) -> None:
    text = str(exc.value)
    assert NPUB.lower() not in text.lower() and OPERATOR_HEX not in text.lower()
    assert f"<redacted {kind}>" in text


def test_npub_in_restart_units_is_redacted(tmp_path: Path) -> None:
    env = write(tmp_path, _with_units(NPUB))
    with pytest.raises(ConfigError) as exc:
        resolve(load(None, env), env)
    _assert_redacted(exc, "npub")


def test_hex_key_in_restart_units_is_redacted(tmp_path: Path) -> None:
    env = write(tmp_path, _with_units(OPERATOR_HEX))
    with pytest.raises(ConfigError) as exc:
        resolve(load(None, env), env)
    _assert_redacted(exc, "hex key")


def test_npub_as_profile_name_is_redacted(tmp_path: Path) -> None:
    env = write(tmp_path, BASE_CONFIG.replace('profile = "admin"', f'profile = "{NPUB}"'))
    with pytest.raises(ConfigError) as exc:
        resolve(load(None, env), env)
    _assert_redacted(exc, "npub")


def test_npub_in_policy_operators_is_redacted(tmp_path: Path) -> None:
    env = write(tmp_path, operators=f'["{NPUB}"]')
    with pytest.raises(ConfigError) as exc:
        resolve(load(None, env), env)
    _assert_redacted(exc, "npub")


def test_uppercase_npub_is_redacted_everywhere(tmp_path: Path) -> None:
    upper = NPUB.upper()
    for name in "abc":
        (tmp_path / name).mkdir()
    configs = [
        write(tmp_path / "a", _with_units(upper)),
        write(tmp_path / "b", BASE_CONFIG.replace('profile = "admin"', f'profile = "{upper}"')),
        write(tmp_path / "c", operators=f'["{upper}"]'),
    ]
    for env in configs:
        with pytest.raises(ConfigError) as exc:
            resolve(load(None, env), env)
        _assert_redacted(exc, "npub")


def test_show_leaves_ordinary_values_alone() -> None:
    assert show("ordinary") == "'ordinary'"
    assert show("ordinary", quote=False) == "ordinary"


@pytest.mark.parametrize("relay", ["wss://", "ws://h\\n", "wss://h\\u0000", "wss://a b", "ws://",
                                   "wss://h\\u0080", "wss://h:invalid", "wss://h:99999"])
def test_malformed_relay_urls_are_rejected(tmp_path: Path, relay: str) -> None:
    env = write(tmp_path, BASE_CONFIG.replace("wss://relay.example.org", relay))
    with pytest.raises(ConfigError, match="relays"):
        resolve(load(None, env), env)


def test_normal_relay_is_accepted(tmp_path: Path) -> None:
    env = write(tmp_path, BASE_CONFIG.replace("wss://relay.example.org", "wss://relay.example:443"))
    assert resolve(load(None, env), env).relays == ("wss://relay.example:443",)
