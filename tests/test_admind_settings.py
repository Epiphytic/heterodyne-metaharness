import functools
from pathlib import Path

import pytest

from heterodyne.admind import cli
from heterodyne.admind import settings as admind_settings
from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.config.capabilities import Capabilities
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
    assert s.operators[0].hex == OPERATOR_HEX
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


def test_an_operator_with_a_valid_npub_is_required(tmp_path: Path) -> None:
    env = write(tmp_path, operators="[]")
    with pytest.raises(ConfigError, match="at least one"):
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


def test_embedded_npub_is_redacted_everywhere(tmp_path: Path) -> None:
    for name in "abcdef":
        (tmp_path / name).mkdir()
    embedded = "operator" + NPUB
    upper = "operator" + NPUB.upper()
    configs = [
        write(tmp_path / "a", _with_units(embedded)),
        write(tmp_path / "b", BASE_CONFIG.replace('profile = "admin"', f'profile = "{embedded}"')),
        write(tmp_path / "c", operators=f'["{embedded}"]'),
        write(tmp_path / "d", _with_units(upper)),
        write(tmp_path / "e", BASE_CONFIG.replace('profile = "admin"', f'profile = "{upper}"')),
        write(tmp_path / "f", operators=f'["{upper}"]'),
    ]
    for env in configs:
        with pytest.raises(ConfigError) as exc:
            resolve(load(None, env), env)
        _assert_redacted(exc, "npub")


def test_embedded_hex_key_is_redacted(tmp_path: Path) -> None:
    env = write(tmp_path, _with_units("a" + OPERATOR_HEX))
    with pytest.raises(ConfigError) as exc:
        resolve(load(None, env), env)
    assert OPERATOR_HEX not in str(exc.value).lower()


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


def with_approve_bead(value: str) -> str:
    return BASE_CONFIG.replace('restart_units = [', f'approve_bead = "{value}"\nrestart_units = [', 1)


def test_approve_bead_is_optional_and_must_be_an_absolute_executable(tmp_path: Path) -> None:
    env = write(tmp_path)
    assert resolve(load(None, env), env).approve_bead is None
    tool = tmp_path / "approve-bead-tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    env = write(tmp_path, with_approve_bead(str(tool)))
    assert resolve(load(None, env), env).approve_bead == tool
    plain = tmp_path / "not-executable-tool"
    plain.write_text("#!/bin/sh\n")
    plain.chmod(0o644)
    for bad in ("relative/approve-bead-tool", str(plain), str(tmp_path / "missing-tool"), str(tmp_path), ""):
        env = write(tmp_path, with_approve_bead(bad))
        with pytest.raises(ConfigError) as err:
            resolve(load(None, env), env)
        assert "approve_bead must be an absolute path to an executable" in str(err.value)
        assert "tool" not in str(err.value) and (not bad or bad not in str(err.value))


# --- AU-11: each admind process on its profile's first account (ADR §4.4 D11) ---

ACCOUNTS_ON = {"claude-code": Capabilities(login_binding=True), "codex": Capabilities(login_binding=True)}
CLAUDE_OVERRIDES = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
WITH_ACCOUNTS = BASE_CONFIG.replace('model = "m1"\n', 'model = "m1"\naccounts = ["b", "c"]\n') + """
[profiles.sum]
adapter = "claude-code"
accounts = ["c"]
[accounts.b]
adapter = "claude-code"
login_dir = "~/.claude-b"
[accounts.c]
adapter = "claude-code"
login_dir = "~/.claude-c"
"""


def with_summarizer(config: str, profile: str) -> str:
    return config.replace('restart_units =', f'summarizer = "{profile}"\nrestart_units =', 1)


def linked_accounts(tmp_path: Path) -> None:
    """~/.claude-b is a symlink, so its configured, expanded and canonical forms all differ."""
    (tmp_path / "real-b").mkdir()
    (tmp_path / ".claude-b").symlink_to(tmp_path / "real-b")


def test_no_accounts_runs_on_the_default_login(tmp_path: Path) -> None:
    env = write(tmp_path)
    cfg = load(None, env, capabilities=ACCOUNTS_ON)
    s = resolve(cfg, env)
    assert s.agent_account.name == "default"
    assert s.agent_account.set == {} and s.agent_account.unset == ("CLAUDE_CONFIG_DIR",)
    assert s.agent_account.key == cfg.accounts[("claude-code", "default")].key
    assert s.summarizer_account is None and s.login_dirs == ()


def test_the_agent_runs_on_its_profiles_first_account(tmp_path: Path) -> None:
    linked_accounts(tmp_path)
    env = write(tmp_path, with_summarizer(WITH_ACCOUNTS, "sum"))
    cfg = load(None, env, capabilities=ACCOUNTS_ON)
    s = resolve(cfg, env)
    b, c = cfg.accounts[("claude-code", "b")], cfg.accounts[("claude-code", "c")]
    assert s.agent_account.name == "b" and s.agent_account.key == b.key
    assert s.agent_account.set == {"CLAUDE_CONFIG_DIR": str(tmp_path / "real-b")}    # canonical
    assert s.agent_account.unset == CLAUDE_OVERRIDES
    assert s.summarizer_account is not None and s.summarizer_account.name == "c"
    assert s.summarizer_account.set == {"CLAUDE_CONFIG_DIR": str(c.login_dir)}
    assert s.summarizer_account.key == c.key != b.key
    # every named account's three literal forms, longest first; never a default directory
    assert set(s.login_dirs) == {"~/.claude-b", str(tmp_path / ".claude-b"), str(tmp_path / "real-b"),
                                 "~/.claude-c", str(tmp_path / ".claude-c")}
    assert [len(f) for f in s.login_dirs] == sorted((len(f) for f in s.login_dirs), reverse=True)
    assert not any(f.endswith("/.claude") for f in s.login_dirs)


def test_a_default_summarizer_has_its_own_default_login(tmp_path: Path) -> None:
    env = write(tmp_path, with_summarizer(WITH_ACCOUNTS, "admin").replace(
        'summarizer = "admin"', 'summarizer = "plain"') + '[profiles.plain]\nadapter = "claude-code"\n')
    s = resolve(load(None, env, capabilities=ACCOUNTS_ON), env)
    assert s.agent_account.name == "b"
    assert s.summarizer_account is not None and s.summarizer_account.name == "default"
    assert s.summarizer_account.unset == ("CLAUDE_CONFIG_DIR",)


def test_a_codex_profile_maps_to_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cross-adapter mapping, at the settings level only (acceptance amendment, design §4)."""
    monkeypatch.setattr(admind_settings, "ADMIN_ADAPTERS", ("claude-code", "codex"))
    config = with_summarizer(WITH_ACCOUNTS, "cx") + """
[profiles.cx]
adapter = "codex"
accounts = ["x"]
[accounts.x]
adapter = "codex"
login_dir = "~/.codex-x"
"""
    env = write(tmp_path, config)
    cfg = load(None, env, capabilities=ACCOUNTS_ON)
    s = resolve(cfg, env)
    assert s.summarizer_account is not None
    assert s.summarizer_account.set == {"CODEX_HOME": str(cfg.accounts[("codex", "x")].login_dir)}
    assert s.summarizer_account.unset == ("OPENAI_API_KEY", "CODEX_API_KEY")
    assert s.agent_account.set == {"CLAUDE_CONFIG_DIR": str(cfg.accounts[("claude-code", "b")].login_dir)}


def test_a_login_selector_in_adminds_environment_is_refused(tmp_path: Path) -> None:
    env = write(tmp_path)
    secret_dir = str(tmp_path / "elsewhere-MARKER")
    with pytest.raises(ConfigError) as exc:
        resolve(load(None, env, capabilities=ACCOUNTS_ON), {**env, "CLAUDE_CONFIG_DIR": secret_dir})
    assert str(exc.value) == ("admind's environment sets CLAUDE_CONFIG_DIR; logins are chosen by [accounts] "
                              "in config.toml (ADR §4.4 D1). Unset it, or configure an account")
    assert "MARKER" not in str(exc.value) and str(tmp_path) not in str(exc.value)
    # Codex's selector is no concern of a Claude-only admind
    resolve(load(None, env, capabilities=ACCOUNTS_ON), {**env, "CODEX_HOME": secret_dir})


def test_a_selector_is_refused_whatever_account_either_process_is_on(tmp_path: Path) -> None:
    """§3.2: named accounts set the selector themselves, but an inherited one is still refused."""
    selected = {"CLAUDE_CONFIG_DIR": str(tmp_path / "x-MARKER")}
    plain = '[profiles.plain]\nadapter = "claude-code"\n'
    for summarizer in ("sum", "plain"):     # both on named accounts; the summarizer on default
        env = write(tmp_path, with_summarizer(WITH_ACCOUNTS, summarizer) + plain)
        with pytest.raises(ConfigError, match="sets CLAUDE_CONFIG_DIR") as exc:
            resolve(load(None, env, capabilities=ACCOUNTS_ON), {**env, **selected})
        assert "MARKER" not in str(exc.value) and str(tmp_path) not in str(exc.value)


def test_admind_exits_78_on_an_inherited_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    env = write(tmp_path, with_summarizer(WITH_ACCOUNTS, "sum"))
    for k, v in {**env, "CLAUDE_CONFIG_DIR": str(tmp_path / "x-MARKER")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cli.hconfig, "load", functools.partial(load, capabilities=ACCOUNTS_ON))
    assert cli.main(["run"]) == cli.EX_CONFIG
    err = capsys.readouterr().err
    assert "sets CLAUDE_CONFIG_DIR" in err
    assert "MARKER" not in err and str(tmp_path) not in err
