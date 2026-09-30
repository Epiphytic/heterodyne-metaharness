from pathlib import Path

from heterodyne.config import paths


def test_explicit_env_wins() -> None:
    env = {"HETERODYNE_CONFIG_DIR": "/x/cfg", "XDG_CONFIG_HOME": "/y", "HOME": "/h"}
    assert paths.config_dir(env) == Path("/x/cfg")


def test_xdg_then_home_default() -> None:
    assert paths.config_dir({"XDG_CONFIG_HOME": "/y", "HOME": "/h"}) == Path("/y/heterodyne")
    assert paths.config_dir({"HOME": "/h"}) == Path("/h/.config/heterodyne")
    assert paths.state_dir({"HOME": "/h"}) == Path("/h/.local/state/heterodyne")
    assert paths.state_dir({"XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path("/s/heterodyne")


def test_explicit_state_env_wins() -> None:
    env = {"HETERODYNE_STATE_DIR": "/x/st", "XDG_STATE_HOME": "/s", "HOME": "/h"}
    assert paths.state_dir(env) == Path("/x/st")


def test_tilde_expands_against_supplied_home() -> None:
    assert paths.config_dir({"HETERODYNE_CONFIG_DIR": "~/cfg", "HOME": "/h"}) == Path("/h/cfg")
    assert paths.state_dir({"HETERODYNE_STATE_DIR": "~", "HOME": "/h"}) == Path("/h")


def test_relative_xdg_values_are_ignored() -> None:
    assert paths.config_dir({"XDG_CONFIG_HOME": "rel", "HOME": "/h"}) == Path("/h/.config/heterodyne")
    assert paths.state_dir({"XDG_STATE_HOME": "rel", "HOME": "/h"}) == Path("/h/.local/state/heterodyne")


def test_empty_values_fall_through() -> None:
    env = {"HETERODYNE_CONFIG_DIR": "", "XDG_CONFIG_HOME": "", "HOME": "/h"}
    assert paths.config_dir(env) == Path("/h/.config/heterodyne")
