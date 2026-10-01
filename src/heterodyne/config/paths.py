"""Host config and state locations (ADR 0001 §15). Environment overrides cover locations only."""

from collections.abc import Mapping
from pathlib import Path


def _home(env: Mapping[str, str]) -> Path:
    return Path(env["HOME"]) if env.get("HOME") else Path.home()


def expand(value: str, env: Mapping[str, str]) -> Path:
    """Expand a leading `~` against the supplied environment's HOME, not the process's."""
    if value == "~" or value.startswith("~/"):
        return _home(env) / value[2:]
    return Path(value)


def _xdg_base(env: Mapping[str, str], var: str, default: Path) -> Path:
    """XDG Base Directory spec: a relative value is invalid and is ignored."""
    value = env.get(var)
    if value and Path(value).is_absolute():
        return Path(value)
    return default


def config_dir(env: Mapping[str, str]) -> Path:
    if env.get("HETERODYNE_CONFIG_DIR"):
        return expand(env["HETERODYNE_CONFIG_DIR"], env)
    return _xdg_base(env, "XDG_CONFIG_HOME", _home(env) / ".config") / "heterodyne"


def state_dir(env: Mapping[str, str]) -> Path:
    if env.get("HETERODYNE_STATE_DIR"):
        return expand(env["HETERODYNE_STATE_DIR"], env)
    return _xdg_base(env, "XDG_STATE_HOME", _home(env) / ".local" / "state") / "heterodyne"
