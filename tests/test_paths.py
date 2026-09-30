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
