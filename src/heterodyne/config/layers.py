"""Read and merge config layers with provenance, enforcing the §15 layer rules."""

import re
import tomllib
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any, cast

from heterodyne.config.policy import POLICY_KEYS, ConfigError

WORKSTREAM_KEYS = frozenset({"roles", "repos", "sandbox", "cron", "render", "timeouts", "restrict"})
WORKSTREAM_SANDBOX_KEYS = frozenset({"extra_ro_mounts", "extra_egress"})
ENV_KEYS = {"HETERODYNE_CONFIG_DIR": ("paths", "config_dir"),
            "HETERODYNE_STATE_DIR": ("paths", "state_dir"),
            "HETERODYNE_LOG_LEVEL": ("debug", "log_level")}
SECRET_NAME = re.compile(r"password|passwd|token|secret|nsec|private_key|api_key", re.IGNORECASE)


def read_defaults() -> dict[str, Any]:
    text = resources.files("heterodyne").joinpath("defaults/defaults.toml").read_text()
    return tomllib.loads(text)


def read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text()) if path.exists() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc


def merge(base: dict[str, Any], overlay: Mapping[str, Any], label: str, sources: dict[str, str],
          prefix: str = "") -> None:
    for key, value in overlay.items():
        dotted = f"{prefix}{key}"
        table = as_table(value)
        if table is None:
            base[key] = value
            sources[dotted] = label
            continue
        child = base.get(key)
        if not isinstance(child, dict):
            child = base[key] = {}
        merge(cast(dict[str, Any], child), table, label, sources, dotted + ".")


def env_layer(env: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    layer: dict[str, Any] = {}
    labels: dict[str, str] = {}
    for var, value in env.items():
        if not var.startswith("HETERODYNE_"):
            continue
        if var not in ENV_KEYS:
            raise ConfigError(f"{var}: environment overrides are limited to {sorted(ENV_KEYS)}")
        table, key = ENV_KEYS[var]
        layer.setdefault(table, {})[key] = value
        labels[f"{table}.{key}"] = f"env:{var}"
    return layer, labels


def check_host(host: Mapping[str, Any]) -> None:
    misplaced = set(host) & POLICY_KEYS
    if misplaced:
        raise ConfigError(f"config.toml: {sorted(misplaced)} belong in policy.toml")


def check_workstream(name: str, ws: Mapping[str, Any], merged_host: Mapping[str, Any]) -> None:
    bad = set(ws) - WORKSTREAM_KEYS
    if bad:
        raise ConfigError(f"workstreams/{name}.toml: {sorted(bad)} not allowed in a workstream "
                          f"(allowed: {sorted(WORKSTREAM_KEYS)})")
    profiles = merged_host.get("profiles", {})
    for role, profile in ws.get("roles", {}).items():
        if profile not in profiles:
            raise ConfigError(f"workstreams/{name}.toml: role {role} names unknown profile {profile!r}")
    sandbox = ws.get("sandbox", {})
    bad_sandbox = set(sandbox) - WORKSTREAM_SANDBOX_KEYS
    if bad_sandbox:
        raise ConfigError(f"workstreams/{name}.toml: [sandbox] {sorted(bad_sandbox)} not allowed")
    approved = set(merged_host.get("sandbox", {}).get("egress_approved", []))
    extra = set(sandbox.get("extra_egress", [])) - approved
    if extra:
        raise ConfigError(f"workstreams/{name}.toml: {sorted(extra)} not in host sandbox.egress_approved")


def check_profiles(merged: Mapping[str, Any]) -> None:
    known = set(merged.get("adapters", {}).get("known", []))
    for name, profile in merged.get("profiles", {}).items():
        if profile.get("adapter") not in known:
            raise ConfigError(f"profiles.{name}: adapter {profile.get('adapter')!r} "
                              f"is not one of {sorted(known)}")


def _is_secret_ref(value: Any) -> bool:
    """A secret reference is a one-key table, `{ file = ... }` or `{ command = ... }`."""
    if not isinstance(value, Mapping):
        return False
    ref = cast(Mapping[str, Any], value)
    return len(ref) == 1 and next(iter(ref)) in ("file", "command")


def as_table(value: Any) -> Mapping[str, Any] | None:
    """`value` as a nested config table, or None for a leaf (scalar, array or secret reference)."""
    if isinstance(value, Mapping) and not _is_secret_ref(value):
        return cast(Mapping[str, Any], value)
    return None


def check_secrets(tree: Mapping[str, Any], prefix: str = "") -> None:
    for key, value in tree.items():
        dotted = f"{prefix}{key}"
        if SECRET_NAME.search(key) and not _is_secret_ref(value):
            raise ConfigError(f"{dotted}: secrets must be a reference, "
                              "{ file = ... } or { command = ... }")
        table = as_table(value)
        if table is not None:
            check_secrets(table, dotted + ".")
