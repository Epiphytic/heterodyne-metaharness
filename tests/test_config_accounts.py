"""AU-2: accounts, profile accounts, failover and [usage] (ADR 0001 §4.1, §4.4 D1, D6, D7, D9)."""

import copy
import os
import re
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from heterodyne import cli
from heterodyne.config import ConfigError, accounts, capabilities, layers, load
from heterodyne.config.capabilities import Capabilities

ON = {"codex": Capabilities(login_binding=True, trusted_read=True),
      "claude-code": Capabilities(login_binding=True)}
MARKER = "MARKER-login"


def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def env(d: Path, **extra: str) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d / "home"), **extra}


def merged(**tables: Any) -> dict[str, Any]:
    """The defaults plus `tables` at the top level, as `load` would have merged them."""
    tree = copy.deepcopy(layers.read_defaults())
    tree.setdefault("profiles", {})
    tree.update(tables)
    return tree


def acct(adapter: str, login_dir: str) -> dict[str, str]:
    return {"adapter": adapter, "login_dir": login_dir}


def resolve(tree: Mapping[str, Any], home: Path, caps: Mapping[str, Capabilities] = ON
            ) -> dict[tuple[str, str], accounts.Account]:
    return accounts.check(tree, {"HOME": str(home)}, caps)


def rejects(tree: Mapping[str, Any], home: Path, pattern: str,
            caps: Mapping[str, Capabilities] = ON) -> ConfigError:
    with pytest.raises(ConfigError, match=pattern) as info:
        resolve(tree, home, caps)
    return info.value


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", '[profiles.a]\nadapter = "codex"\nmodel = "m1"\n[roles]\ncoder = "a"\n')
    write(tmp_path, "policy.toml", 'approvers = ["op"]\n')
    (tmp_path / "home").mkdir()
    return tmp_path


# Defaults and the empty capability table (D9).

def test_usage_defaults_load(cfg: Path) -> None:
    c = load(None, env(cfg))
    assert c.get("usage") == {"reserve_percent": 5, "stale_minutes": 30, "unknown_backoff_minutes": 30,
                              "untrusted_max_defer_minutes": 60, "min_recheck_seconds": 60,
                              "max_window_hours": 192}
    assert c.sources["usage.reserve_percent"] == "defaults"
    assert c.get("accounts") is None
    assert set(c.accounts) == {("claude-code", "default"), ("codex", "default")}
    home = cfg / "home"
    assert c.accounts[("codex", "default")].login_files == (home / ".codex" / "auth.json",)
    assert c.accounts[("claude-code", "default")].login_dir == home / ".claude"


def test_capability_table_is_empty() -> None:
    assert capabilities.CAPABILITIES == {}
    assert not capabilities.NONE.can_switch
    assert Capabilities(handoff_relaunch=True).can_switch
    assert Capabilities(cross_account_resume=True).can_switch


def test_accounts_refused_while_capabilities_empty(cfg: Path) -> None:
    base = (cfg / "config.toml").read_text()
    write(cfg, "config.toml", base + '[accounts.acct-a]\nadapter = "codex"\nlogin_dir = "/x/login-a"\n')
    with pytest.raises(ConfigError, match=r"accounts\.acct-a: accounts aren't supported on adapter codex"):
        load(None, env(cfg))
    write(cfg, "config.toml", base.replace('model = "m1"', 'model = "m1"\naccounts = ["acct-a"]')
          + '[accounts.acct-a]\nadapter = "codex"\nlogin_dir = "/x/login-a"\n')
    with pytest.raises(ConfigError, match=r"accounts\.acct-a: accounts aren't supported") as info:
        load(None, env(cfg))
    assert "profiles" not in str(info.value)


def test_failover_next_refused_without_trusted_read(tmp_path: Path) -> None:
    tree = merged(profiles={"p": {"adapter": "codex", "failover": "next"}})
    rejects(tree, tmp_path, r'profiles\.p: failover = "next" isn\'t supported on adapter codex', caps={})
    tree = merged(accounts={"acct-a": acct("claude-code", "/x/login-a")},
                  profiles={"p": {"adapter": "claude-code", "accounts": ["acct-a"], "failover": "next"}})
    rejects(tree, tmp_path, "trusted usage read")


def test_failover_none_accepted_everywhere(tmp_path: Path) -> None:
    for profile in ({"adapter": "codex", "failover": "none"}, {"adapter": "claude-code"}):
        assert resolve(merged(profiles={"p": profile}), tmp_path, {})


def test_failover_next_accepted_with_trusted_read(tmp_path: Path) -> None:
    tree = merged(accounts={"acct-a": acct("codex", "/x/login-a"), "acct-b": acct("codex", "/x/login-b")},
                  profiles={"p": {"adapter": "codex", "accounts": ["acct-a", "acct-b"], "failover": "next"}})
    found = resolve(tree, tmp_path)
    assert found[("codex", "acct-b")].login_files == (Path("/x/login-b/auth.json"),)


# Account names and shape (rules 1-8).

@pytest.mark.parametrize("name", ["a@b", "a/b", "a.b", "a b", "1abc", "-a", "x" * 65])
def test_account_name_rules_reject(tmp_path: Path, name: str) -> None:
    rejects(merged(accounts={name: acct("codex", "/x/login-a")}), tmp_path, "plain identifiers")


@pytest.mark.parametrize("name", ["default", "Default"])
def test_account_name_default_reserved(tmp_path: Path, name: str) -> None:
    rejects(merged(accounts={name: acct("codex", "/x/login-a")}), tmp_path, '"default" is reserved')


@pytest.mark.parametrize("name", ["acct-a", "acct_b", "A1", "x" * 64])
def test_account_name_rules_accept(tmp_path: Path, name: str) -> None:
    assert ("codex", name) in resolve(merged(accounts={name: acct("codex", "/x/login-a")}), tmp_path)


def test_secret_word_account_name_refused_by_scan(cfg: Path) -> None:
    base = (cfg / "config.toml").read_text()
    write(cfg, "config.toml", base + '[accounts.codex-auth]\nadapter = "codex"\nlogin_dir = "/x/login-a"\n')
    with pytest.raises(ConfigError, match="secrets must be a reference"):
        load(None, env(cfg), capabilities=ON)


@pytest.mark.parametrize(("tables", "pattern"), [
    ({"accounts": 1}, "accounts must be a table"),
    ({"accounts": {"acct-a": "x"}}, r"accounts\.acct-a must be a table"),
    ({"accounts": {"acct-a": {**acct("codex", "/x/l"), "extra": 1}}}, r"unknown keys \['extra'\]"),
    ({"accounts": {"acct-a": acct("mystery", "/x/l")}}, "adapter 'mystery' is not one of"),
    ({"accounts": {"acct-a": {"adapter": "codex", "login_dir": {"file": "/x/f"}}}}, "not a secret"),
    ({"accounts": {"acct-a": {"adapter": "codex", "login_dir": 3}}}, r"login_dir must be a string"),
    ({"accounts": {"acct-a": {"adapter": "codex"}}}, r"login_dir must be a string"),
    ({"accounts": {"acct-a": acct("codex", "")}}, r"login_dir must be a string"),
    ({"accounts": {"acct-a": acct("codex", "rel/login")}}, "must be an absolute path or start with ~/"),
    ({"accounts": {"acct-a": acct("codex", "~other/login")}}, "must be an absolute path or start with ~/"),
])
def test_account_shape_rules(tmp_path: Path, tables: dict[str, Any], pattern: str) -> None:
    rejects(merged(**tables), tmp_path, pattern)


# Profile accounts and failover (rules 10-17).

TWO = {"acct-a": acct("codex", "/x/login-a"), "acct-c": acct("claude-code", "/x/login-c")}


@pytest.mark.parametrize(("profile", "pattern"), [
    ({"accounts": "acct-a"}, r"profiles\.p\.accounts must be a list of account names"),
    ({"accounts": [1]}, r"profiles\.p\.accounts must be a list of account names"),
    ({"accounts": []}, r"profiles\.p\.accounts is empty; omit it"),
    ({"accounts": ["acct-a", "acct-a"]}, "lists acct-a more than once"),
    ({"accounts": ["default"]}, "\"default\" can't be listed"),
    ({"accounts": ["nope"]}, r"profiles\.p: unknown account nope"),
    ({"accounts": ["acct-c"]}, "account acct-c is for adapter claude-code, not codex"),
    ({"failover": "always"}, r"profiles\.p\.failover must be \"none\" or \"next\", not 'always'"),
    ({"failover": True}, r"profiles\.p\.failover must be"),
])
def test_profile_accounts_rules(tmp_path: Path, profile: dict[str, Any], pattern: str) -> None:
    rejects(merged(accounts=TWO, profiles={"p": {"adapter": "codex", **profile}}), tmp_path, pattern)


# Credential identities and aliases (rule 18).

def alias_pair(tmp_path: Path, a: str, b: str, adapter_b: str = "codex") -> dict[str, Any]:
    return merged(accounts={"acct-a": acct("codex", a), "acct-b": acct(adapter_b, b)})


def test_alias_equal_and_nested_dirs(tmp_path: Path) -> None:
    x = tmp_path / "x"
    rejects(alias_pair(tmp_path, f"{x}/l", f"{x}/l"), tmp_path, "directories nest or are equal")
    rejects(alias_pair(tmp_path, f"{x}/l", f"{x}/l/inner"), tmp_path, "nest or are equal")
    rejects(alias_pair(tmp_path, f"{x}/l/inner", f"{x}/l"), tmp_path, "nest or are equal")
    rejects(alias_pair(tmp_path, f"{x}/l", f"{x}/l", "claude-code"), tmp_path, r"acct-b \(claude-code\)")
    rejects(merged(accounts={"team": acct("codex", "~/.codex/team")}), tmp_path,
            r"accounts default \(codex\) and team \(codex\) share a login")
    assert resolve(alias_pair(tmp_path, f"{x}/login", f"{x}/login-other"), tmp_path)


def test_alias_symlinked_dir_and_file(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    rejects(alias_pair(tmp_path, str(real), str(tmp_path / "link")), tmp_path, "nest or are equal")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "auth.json").write_text("{}")
    (b / "auth.json").symlink_to(a / "auth.json")
    rejects(alias_pair(tmp_path, str(a), str(b)), tmp_path, "a login file resolves to the same file")


def test_alias_hard_link(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "auth.json").write_text("{}")
    os.link(a / "auth.json", b / "auth.json")
    rejects(alias_pair(tmp_path, str(a), str(b)), tmp_path, "a login file is hard-linked")
    (b / "auth.json").unlink()
    assert resolve(alias_pair(tmp_path, str(a), str(b)), tmp_path)   # a missing file never aliases


def test_implicit_defaults_alias(cfg: Path) -> None:
    home = cfg / "home"
    (home / ".claude").mkdir()
    (home / ".codex").symlink_to(home / ".claude")
    with pytest.raises(ConfigError, match=r"accounts default \(claude-code\) and default \(codex\)"):
        load(None, env(cfg))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_alias_stat_error_fails_closed(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    (locked / "a").mkdir(parents=True)
    locked.chmod(0)
    try:
        rejects(alias_pair(tmp_path, str(locked / "a"), str(tmp_path / "b")), tmp_path,
                r"account acct-a: login file can't be checked for aliases \(EACCES\)")
    finally:
        locked.chmod(0o755)


def test_credential_key(tmp_path: Path) -> None:
    key = accounts.credential_key("codex", (Path("/x/a/auth.json"),))
    assert re.fullmatch(r"ck1-[0-9a-f]{32}", key)
    assert key == accounts.credential_key("codex", (Path("/x/a/auth.json"),))
    assert key != accounts.credential_key("claude-code", (Path("/x/a/auth.json"),))
    assert key != accounts.credential_key("codex", (Path("/x/b/auth.json"),))
    real, other = tmp_path / "real", tmp_path / "other"
    real.mkdir()
    other.mkdir()
    (tmp_path / "link").symlink_to(real)
    keys = {d: resolve(merged(accounts={"acct-a": acct("codex", str(tmp_path / d))}), tmp_path)
            [("codex", "acct-a")].key for d in ("real", "link")}
    assert keys["real"] == keys["link"]
    (tmp_path / "link").unlink()
    (tmp_path / "link").symlink_to(other)
    repointed = resolve(merged(accounts={"acct-a": acct("codex", str(tmp_path / "link"))}), tmp_path)
    assert repointed[("codex", "acct-a")].key != keys["real"]


def test_configured_dir_round_trips(cfg: Path) -> None:
    base = (cfg / "config.toml").read_text()
    write(cfg, "config.toml", base + '[accounts.acct-a]\nadapter = "codex"\nlogin_dir = "~/logins/../a"\n')
    c = load(None, env(cfg), capabilities=ON)
    a = c.accounts[("codex", "acct-a")]
    assert a.configured_dir == "~/logins/../a"
    assert a.login_dir == cfg / "home" / "a"
    assert c.accounts[("codex", "default")].configured_dir == "~/.codex"
    assert c.accounts[("claude-code", "default")].configured_dir == "~/.claude"


def test_canonical_login_matches_load(cfg: Path) -> None:
    c = load(None, env(cfg))
    d = c.accounts[("codex", "default")]
    assert accounts.canonical_login("default", "codex", d.configured_dir, env(cfg)) == (d.login_dir,
                                                                                         d.login_files)


def no_marker(exc: ConfigError) -> None:
    assert MARKER not in str(exc)
    assert MARKER not in "".join(traceback.format_exception(exc))


@pytest.mark.parametrize(("login_dir", "pattern"), [
    ({"file": f"/x/{MARKER}"}, "not a secret"),
    (f"rel/{MARKER}", "absolute path"),
])
def test_shape_errors_never_show_login_paths(tmp_path: Path, login_dir: Any, pattern: str) -> None:
    no_marker(rejects(merged(accounts={"acct-a": {"adapter": "codex", "login_dir": login_dir}}),
                      tmp_path, pattern))


def test_refusal_and_alias_errors_never_show_login_paths(tmp_path: Path) -> None:
    no_marker(rejects(merged(accounts={"acct-a": acct("codex", f"/x/{MARKER}")}), tmp_path,
                      "aren't supported", caps={}))
    no_marker(rejects(alias_pair(tmp_path, f"/x/{MARKER}", f"/x/{MARKER}/in"), tmp_path, "share a login"))


def resolution_error(tree: Mapping[str, Any], home: Path, reason: str) -> None:
    exc = rejects(tree, home, rf"account acct-a \(codex\): login directory can't be resolved \({reason}\)")
    no_marker(exc)
    assert exc.__cause__ is None and exc.__context__ is None


def test_symlink_loop_errors_are_path_free(tmp_path: Path) -> None:
    loop = tmp_path / MARKER
    loop.symlink_to(tmp_path / f"{MARKER}-2")
    (tmp_path / f"{MARKER}-2").symlink_to(loop)
    resolution_error(merged(accounts={"acct-a": acct("codex", str(loop))}), tmp_path, "RuntimeError")
    d = tmp_path / f"{MARKER}-dir"
    d.mkdir()
    (d / "auth.json").symlink_to(d / "auth.json")
    resolution_error(merged(accounts={"acct-a": acct("codex", str(d))}), tmp_path, "RuntimeError")


def test_nul_in_login_dir_is_path_free(tmp_path: Path) -> None:
    resolution_error(merged(accounts={"acct-a": acct("codex", f"/x/{MARKER}\0")}), tmp_path, "ValueError")


def test_resolve_oserror_is_path_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = Path.resolve

    def failing(self: Path, strict: bool = False) -> Path:
        if MARKER in str(self):
            raise OSError(36, "File name too long", str(self))
        return real(self, strict)

    monkeypatch.setattr(Path, "resolve", failing)
    resolution_error(merged(accounts={"acct-a": acct("codex", f"/x/{MARKER}")}), tmp_path, "ENAMETOOLONG")


def test_adapter_without_login_file_set(tmp_path: Path) -> None:
    tree = merged()
    tree["adapters"]["known"] = ["claude-code", "codex", "mystery"]
    rejects(tree, tmp_path, "adapter mystery has no login file set")


# [usage] (rules 18a-23).

@pytest.mark.parametrize(("usage", "pattern"), [
    ({"extra": 1}, r"usage: unknown keys \['extra'\]"),
    ({"reserve_percent": -1}, "usage.reserve_percent must be an integer from 0 to 50, not -1"),
    ({"reserve_percent": 51}, "from 0 to 50, not 51"),
    ({"reserve_percent": 5.0}, "from 0 to 50, not 5.0"),
    ({"reserve_percent": True}, "from 0 to 50, not True"),
    *[({key: bad}, rf"usage\.{key} must be a positive integer, not {re.escape(repr(bad))}")
      for key in ("stale_minutes", "unknown_backoff_minutes", "untrusted_max_defer_minutes",
                  "min_recheck_seconds", "max_window_hours")
      for bad in (0, -1, "30", True)],
    ({"min_recheck_seconds": 1801}, r"min_recheck_seconds \(1801\) must be at most 60 × the .*\(1800\)"),
    ({"stale_minutes": 10, "min_recheck_seconds": 601}, r"\(601\) .*\(600\)"),
    ({"max_window_hours": 1, "untrusted_max_defer_minutes": 61, "unknown_backoff_minutes": 30},
     r"usage\.untrusted_max_defer_minutes \(61\) must be at most 60 × max_window_hours \(60\)"),
    ({"max_window_hours": 1, "untrusted_max_defer_minutes": 60, "unknown_backoff_minutes": 61},
     r"usage\.unknown_backoff_minutes \(61\) must be at most 60 × max_window_hours \(60\)"),
])
def test_usage_rules(tmp_path: Path, usage: dict[str, Any], pattern: str) -> None:
    tree = merged()
    tree["usage"].update(usage)
    rejects(tree, tmp_path, pattern)


@pytest.mark.parametrize("usage", [
    {"reserve_percent": 0}, {"reserve_percent": 50}, {"min_recheck_seconds": 1800},
    {"stale_minutes": 10, "min_recheck_seconds": 600},
    {"max_window_hours": 1, "untrusted_max_defer_minutes": 60, "unknown_backoff_minutes": 60},
])
def test_usage_boundaries_accepted(tmp_path: Path, usage: dict[str, Any]) -> None:
    tree = merged()
    tree["usage"].update(usage)
    assert resolve(tree, tmp_path)


def test_usage_host_override_merged_before_checking(cfg: Path) -> None:
    base = (cfg / "config.toml").read_text()
    write(cfg, "config.toml", base + "[usage]\nstale_minutes = 1\n")
    c = load(None, env(cfg))   # min_recheck_seconds 60 is exactly 60 × 1
    assert c.get("usage.stale_minutes") == 1 and c.sources["usage.stale_minutes"] == "host:config.toml"
    write(cfg, "config.toml", base + "[usage]\nstale_minutes = 1\nmin_recheck_seconds = 61\n")
    with pytest.raises(ConfigError, match=r"min_recheck_seconds \(61\)"):
        load(None, env(cfg))


def test_usage_not_a_table(cfg: Path) -> None:
    write(cfg, "config.toml", "usage = 1\n" + (cfg / "config.toml").read_text())
    with pytest.raises(ConfigError, match="usage must be a table"):
        load(None, env(cfg))


# Layers (D1, §15).

@pytest.mark.parametrize("text", ['[accounts.acct-a]\nadapter = "codex"\n', "[usage]\nstale_minutes = 5\n"])
def test_workstream_rejects_accounts_and_usage(cfg: Path, text: str) -> None:
    write(cfg, "workstreams/w.toml", text)
    with pytest.raises(ConfigError, match=r"\[accounts\] and \[usage\] are host-only"):
        load("w", env(cfg))


def test_workstream_profiles_keep_generic_message(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[profiles.a]\naccounts = ["acct-a"]\n')
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        load("w", env(cfg))


def test_policy_rejects_accounts(cfg: Path) -> None:
    write(cfg, "policy.toml", 'approvers = ["op"]\n[accounts.acct-a]\nadapter = "codex"\n')
    with pytest.raises(ConfigError, match="policy.toml: unknown keys"):
        load(None, env(cfg))


def test_env_cannot_select_account(cfg: Path) -> None:
    with pytest.raises(ConfigError, match="HETERODYNE_ACCOUNT"):
        load(None, env(cfg, HETERODYNE_ACCOUNT="acct-a"))
    c = load(None, env(cfg, CLAUDE_CONFIG_DIR="/x/elsewhere", CODEX_HOME="/x/elsewhere-codex"))
    assert c.accounts[("claude-code", "default")].login_dir == cfg / "home" / ".claude"
    assert c.accounts[("codex", "default")].login_dir == cfg / "home" / ".codex"


# config check (cli).

def run_check(cfg: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
              ) -> tuple[int, str, str]:
    monkeypatch.setattr(capabilities, "CAPABILITIES", ON)
    for var in [v for v in os.environ if v.startswith("HETERODYNE_")]:
        monkeypatch.delenv(var)
    monkeypatch.setenv("HETERODYNE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HOME", str(cfg / "home"))
    rc = cli.main(["config", "check"])
    out, err = capsys.readouterr()
    return rc, out, err


def with_accounts(cfg: Path, profile_extra: str, login_a: str = f"/x/{MARKER}-a") -> None:
    write(cfg, "config.toml", f"""
[profiles.a]
adapter = "codex"
model = "m1"
{profile_extra}
[roles]
coder = "a"
[accounts.acct-a]
adapter = "codex"
login_dir = "{login_a}"
[accounts.acct-b]
adapter = "codex"
login_dir = "/x/{MARKER}-b"
""")


def test_config_check_hides_login_dir(cfg: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    with_accounts(cfg, 'accounts = ["acct-a"]')
    rc, out, err = run_check(cfg, monkeypatch, capsys)
    assert rc == 0
    assert "accounts.acct-a.login_dir = <hidden>    (host:config.toml)" in out
    assert "accounts.acct-a.adapter = 'codex'    (host:config.toml)" in out
    assert "usage.reserve_percent = 5    (defaults)" in out
    assert MARKER not in out + err
    assert "warning:" not in out + err


def test_config_check_symlink_loop_is_path_free(cfg: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    loop = cfg / f"{MARKER}-loop"
    loop.symlink_to(loop)
    with_accounts(cfg, "", login_a=str(loop))
    rc, out, err = run_check(cfg, monkeypatch, capsys)
    assert rc == 1
    assert err.startswith("config error: account acct-a (codex): login directory can't be resolved")
    assert MARKER not in out + err and "Traceback" not in err


def test_config_check_warnings(cfg: Path, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    with_accounts(cfg, 'accounts = ["acct-a", "acct-b"]')
    rc, out, _ = run_check(cfg, monkeypatch, capsys)
    assert rc == 0
    assert ('warning: profiles.a lists 2 accounts with failover = "none"; only the first is used '
            "(ADR §4.4 D6)") in out
    with_accounts(cfg, 'failover = "next"')
    rc, out, _ = run_check(cfg, monkeypatch, capsys)
    assert rc == 0
    assert ('warning: profiles.a has failover = "next" but no accounts; it only ever uses the default '
            "login") in out
    with_accounts(cfg, 'accounts = ["acct-a", "acct-b"]\nfailover = "next"')
    rc, out, _ = run_check(cfg, monkeypatch, capsys)
    assert rc == 0 and "warning:" not in out
