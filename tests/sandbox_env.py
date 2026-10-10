"""Sandbox test helpers: a scratch config directory, and a bounded wait on a condition."""

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from wsd_env import login_home

BASE_HOST = """
[profiles.p-one]
adapter = "claude-code"

[profiles.p-two]
adapter = "codex"
"""


def config_env(root: Path, host: str = "", workstreams: Mapping[str, str] | None = None) -> dict[str, str]:
    """A config directory at `root/config` holding a minimal host config.toml (two profiles) plus `host`,
    and one `workstreams/<name>.toml` per entry. HOME is `root/home`, with a fake default login per
    adapter. Returns the environment wsd would read it with."""
    config = root / "config"
    (config / "workstreams").mkdir(parents=True, exist_ok=True)
    (config / "config.toml").write_text(BASE_HOST + host)
    for name, text in (workstreams or {}).items():
        (config / "workstreams" / f"{name}.toml").write_text(text)
    home = login_home(root / "home")
    return {"HETERODYNE_CONFIG_DIR": str(config), "HOME": str(home)}


def wait_for(pred: Callable[[], object], timeout: float = 10.0) -> None:
    """Poll `pred` until it is true, failing after `timeout` seconds. A bounded wait on a condition,
    never a fixed delay."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)
