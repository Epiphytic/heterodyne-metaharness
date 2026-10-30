"""Typed reasoning seeds the private server home; remote resume argv stays closed."""
import tomllib
from pathlib import Path

import pytest

from heterodyne.agents.base import AdapterError, Cli
from heterodyne.agents.codex import APP_SOCKET, Codex
from heterodyne.config import ConfigError
from heterodyne.config.layers import check_profiles
from heterodyne.config.profile_args import FLAGS
from heterodyne.sandbox.spec import SessionLayout

THREAD = "0b1d8c5e-3f7a-4c2d-9e6b-7a8f9c0d1e2f"
PROFILE = {"adapter": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "medium"}


def test_medium_profile_seeds_server_home_without_remote_overrides(tmp_path: Path) -> None:
    check_profiles({"adapters": {"known": ["codex"]}, "profiles": {"build": PROFILE}})
    layout = SessionLayout(tmp_path / "session")
    layout.home.mkdir(parents=True)
    install = tmp_path / "install"
    (install / "defaults").mkdir(parents=True)
    (install / "defaults" / "config.toml").write_text('model_reasoning_effort = "high"\n')
    codex = Codex()
    codex.prepare_install_home(layout, tmp_path / "work", install, profile=PROFILE)
    home = tomllib.loads((layout.home / ".codex" / "config.toml").read_text())
    assert home["model_reasoning_effort"] == "medium"
    assert home["approval_policy"] == "never"
    assert home["sandbox_mode"] == "danger-full-access"
    cli = Cli(Path("/managed/codex"), Path("/managed"), "0.160.0")
    assert codex.tui_argv(cli, PROFILE, native_id=None, resume=False, label="build") == [
        "/managed/codex", "--remote", APP_SOCKET,
        "--dangerously-bypass-approvals-and-sandbox", "--model", "gpt-6.1-sol"]
    assert codex.tui_argv(cli, PROFILE, native_id=THREAD, resume=True, label="build") == [
        "/managed/codex", "resume", THREAD, "--remote", APP_SOCKET, "--model", "gpt-6.1-sol"]
    assert FLAGS["codex"] == {}


@pytest.mark.parametrize("value", [
    "", "xhigh", True, 7, [], {"value": "medium"}, 'medium"\napproval_policy="always'])
def test_reasoning_refuses_untyped_or_unsupported_values(tmp_path: Path, value: object) -> None:
    profile = dict(PROFILE, reasoning_effort=value)
    with pytest.raises(ConfigError, match="reasoning_effort"):
        check_profiles({"adapters": {"known": ["codex"]}, "profiles": {"build": profile}})
    layout = SessionLayout(tmp_path / "session")
    with pytest.raises(AdapterError, match="reasoning effort"):
        Codex().prepare_install_home(layout, tmp_path / "work", tmp_path, profile=profile)
    assert not layout.home.exists()


def test_reasoning_is_codex_only_and_generic_args_stay_refused() -> None:
    with pytest.raises(ConfigError, match="supported only by the codex adapter"):
        check_profiles({"adapters": {"known": ["claude-code"]}, "profiles": {
            "build": dict(PROFILE, adapter="claude-code")}})
    with pytest.raises(ConfigError, match="not a flag"):
        check_profiles({"adapters": {"known": ["codex"]}, "profiles": {
            "build": dict(PROFILE, args=["-c", 'model_reasoning_effort="medium"'])}})


@pytest.mark.parametrize("key", ["reasoning_efort", "adaptor", "models", "arg"])
def test_profile_unknown_keys_are_refused(key: str) -> None:
    with pytest.raises(ConfigError, match="unsupported profile keys"):
        check_profiles({"adapters": {"known": ["codex"]}, "profiles": {
            "build": dict(PROFILE, **{key: "medium"})}})


def test_profile_account_routing_keys_remain_supported() -> None:
    check_profiles({"adapters": {"known": ["codex"]}, "profiles": {
        "build": dict(PROFILE, accounts=["one", "two"], failover="next")}})
