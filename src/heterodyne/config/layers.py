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
    sandbox = table_at(host, "sandbox", "config.toml")
    _strings(sandbox, "egress_approved", "config.toml: sandbox")
    for root in _strings(sandbox, "ro_mounts_approved", "config.toml: sandbox"):
        _abs_path(root, "config.toml: sandbox.ro_mounts_approved")


def check_workstream(name: str, ws: Mapping[str, Any], merged_host: Mapping[str, Any]) -> None:
    bad = set(ws) - WORKSTREAM_KEYS
    if bad:
        raise ConfigError(f"workstreams/{name}.toml: {sorted(bad)} not allowed in a workstream "
                          f"(allowed: {sorted(WORKSTREAM_KEYS)})")
    where = f"workstreams/{name}.toml"
    profiles = table_at(merged_host, "profiles", "config.toml")
    for role, profile in table_at(ws, "roles", where).items():
        if not isinstance(profile, str):
            raise ConfigError(f"{where}: roles.{role} must be a profile name (string)")
        if profile not in profiles:
            raise ConfigError(f"workstreams/{name}.toml: role {role} names unknown profile {profile!r}")
    sandbox = table_at(ws, "sandbox", where)
    string_list(table_at(ws, "restrict", where).get("escalate", []), f"{where}: restrict.escalate")
    string_list(table_at(ws, "restrict", where).get("hard_deny", []), f"{where}: restrict.hard_deny")
    bad_sandbox = set(sandbox) - WORKSTREAM_SANDBOX_KEYS
    if bad_sandbox:
        raise ConfigError(f"workstreams/{name}.toml: [sandbox] {sorted(bad_sandbox)} not allowed")
    host_sandbox = table_at(merged_host, "sandbox", "config.toml")
    approved = set(_strings(host_sandbox, "egress_approved", "config.toml: sandbox"))
    extra = set(_strings(sandbox, "extra_egress", f"workstreams/{name}.toml: sandbox")) - approved
    if extra:
        raise ConfigError(f"workstreams/{name}.toml: {sorted(extra)} not in host sandbox.egress_approved")
    roots = [_abs_path(p, "config.toml: sandbox.ro_mounts_approved")
             for p in _strings(host_sandbox, "ro_mounts_approved", "config.toml: sandbox")]
    for mount in _strings(sandbox, "extra_ro_mounts", f"workstreams/{name}.toml: sandbox"):
        resolved = _abs_path(mount, f"workstreams/{name}.toml: sandbox.extra_ro_mounts")
        if not any(resolved.is_relative_to(root) for root in roots):
            raise ConfigError(f"workstreams/{name}.toml: read-only mount {mount!r} is not within "
                              "host sandbox.ro_mounts_approved")


def _strings(table: Mapping[str, Any], key: str, where: str) -> list[str]:
    return string_list(table.get(key, []), f"{where}.{key}")


def string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in cast(list[Any], value)):
        raise ConfigError(f"{where} must be a list of strings")
    return cast(list[str], value)


def table_at(tree: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    """`tree[key]` as a table (empty if absent); anything else is a ConfigError."""
    value = tree.get(key, {})
    table = as_table(value)
    if table is None:
        raise ConfigError(f"{where}.{key} must be a table")
    return table


def _abs_path(value: str, where: str) -> Path:
    """Absolute path with `..` and symlinks resolved, so containment is judged on the real target.

    Containment uses `Path.is_relative_to`, which compares whole components: `/data` covers
    `/data/x` but not `/database`.
    """
    if not Path(value).is_absolute():
        raise ConfigError(f"{where}: {value!r} must be an absolute path")
    return Path(value).resolve(strict=False)


def check_profiles(merged: Mapping[str, Any]) -> None:
    known = set(string_list(table_at(merged, "adapters", "config").get("known", []), "adapters.known"))
    for name, value in table_at(merged, "profiles", "config.toml").items():
        profile = as_table(value)
        if profile is None:
            raise ConfigError(f"profiles.{name} must be a table")
        adapter = profile.get("adapter")
        if not isinstance(adapter, str) or adapter not in known:
            raise ConfigError(f"profiles.{name}: adapter {adapter!r} "
                              f"is not one of {sorted(known)}")


def _is_secret_ref(value: Any) -> bool:
    """A secret reference is a one-key table, `{ file = "..." }` or `{ command = "..." }`,
    whose value is a non-empty string."""
    if not isinstance(value, Mapping):
        return False
    ref = cast(Mapping[str, Any], value)
    if len(ref) != 1:
        return False
    key, target = next(iter(ref.items()))
    return key in ("file", "command") and isinstance(target, str) and target.strip() != ""


def as_table(value: Any) -> Mapping[str, Any] | None:
    """`value` as a nested config table, or None for a leaf (scalar, array or secret reference)."""
    if isinstance(value, Mapping) and not _is_secret_ref(value):
        return cast(Mapping[str, Any], value)
    return None


def check_secrets(tree: Mapping[str, Any], layer: str) -> None:
    """Reject inline secrets in one layer: a secret-named key must hold a valid reference.

    Walks every value, including arrays (of tables, and nested arrays), so nothing is skipped.
    Error paths carry indices, e.g. `identities.op.credentials[0].api_key`.
    """
    _walk_secrets(tree, layer, "")


def _walk_secrets(value: Any, layer: str, path: str) -> None:
    if isinstance(value, list):
        for i, item in enumerate(cast(list[Any], value)):
            _walk_secrets(item, layer, f"{path}[{i}]")
        return
    table = as_table(value)
    if table is None:
        return
    for key, child in table.items():
        dotted = f"{path}.{key}" if path else key
        if SECRET_NAME.search(key) and not _is_secret_ref(child):
            raise ConfigError(f"{layer}: {dotted}: secrets must be a reference, "
                              '{ file = "<path>" } or { command = "<command>" } (exactly one, non-empty)')
        _walk_secrets(child, layer, dotted)
