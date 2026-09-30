"""Host config and state locations (ADR 0001 §15). Environment overrides cover locations only."""

from collections.abc import Mapping
from pathlib import Path


def _home(env: Mapping[str, str]) -> Path:
    return Path(env["HOME"]) if env.get("HOME") else Path.home()


def config_dir(env: Mapping[str, str]) -> Path:
    if env.get("HETERODYNE_CONFIG_DIR"):
        return Path(env["HETERODYNE_CONFIG_DIR"]).expanduser()
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else _home(env) / ".config"
    return base / "heterodyne"


def state_dir(env: Mapping[str, str]) -> Path:
    if env.get("HETERODYNE_STATE_DIR"):
        return Path(env["HETERODYNE_STATE_DIR"]).expanduser()
    base = Path(env["XDG_STATE_HOME"]) if env.get("XDG_STATE_HOME") else _home(env) / ".local" / "state"
    return base / "heterodyne"
