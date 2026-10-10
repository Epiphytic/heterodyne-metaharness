"""Sandbox settings (ADR 0001 §7, §15): the host's `[sandbox]` and `[platform]` tables, each adapter's
binary, the profiles, and each workstream's `[sandbox]` additions and effective tiers. Read once when wsd
starts; a change needs a restart. Errors name keys, never values: `tool_env` may carry paths."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heterodyne.config import Config, ConfigError, load, paths
from heterodyne.config.layers import as_table, string_list, table_at
from heterodyne.config.secret_scan import show

HOST = re.compile(r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
HOST_KEYS = frozenset({"egress_approved", "ro_mounts_approved", "image", "openshell", "podman",
                       "max_lifetime_minutes", "stop_margin_minutes", "probe_allowed_host",
                       "probe_denied_host", "agent_probe_seconds", "tool_env"})
# Variables the tools' own environment may not override: the sandbox launch sets them itself.
RESERVED_ENV = frozenset({"HOME", "USER", "LANG", "TERM"})
DENIED_HOSTS = frozenset({"mcp-proxy.anthropic.com"})   # §7 Connectors; spec.py enforces the same set
LOCAL_CLASSES = frozenset({"worktree_edit"})            # the shim's locally classifiable set (D14)
WHERE = "config.toml: [sandbox]"


@dataclass(frozen=True)
class WsSandbox:
    extra_egress: tuple[str, ...] = ()
    extra_ro_mounts: tuple[Path, ...] = ()
    local_classes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SandboxSettings:
    backend: str                             # `[platform] sandbox`, "none" when unset
    image: str
    openshell: str
    podman: str
    tool_env: Mapping[str, str]
    max_lifetime_seconds: int
    stop_margin_seconds: int
    probe_allowed_host: str
    probe_denied_host: str
    agent_probe_seconds: int
    hook_wait_seconds: float
    egress_approved: tuple[str, ...]
    binaries: Mapping[str, str]              # adapter -> `[adapters.<a>].binary`, as configured
    profiles: Mapping[str, Mapping[str, Any]]
    workstreams: Mapping[str, WsSandbox]


def sandbox_settings(env: Mapping[str, str]) -> SandboxSettings:
    from heterodyne.wsd.settings import workstream_names  # wsd.settings imports nothing from here

    host = load(env=env)
    streams = {name: load(name, env) for name in workstream_names(paths.config_dir(env))}
    return from_config(host, streams)


def from_config(host: Config, streams: Mapping[str, Config]) -> SandboxSettings:
    sb = table_at(host.values, "sandbox", "config")
    unknown = set(sb) - HOST_KEYS
    if unknown:
        raise ConfigError(f"{WHERE}: unknown keys {show(unknown)} (allowed: {sorted(HOST_KEYS)})")
    lifetime = _int(sb, "max_lifetime_minutes", 10, 1440) * 60
    margin = _int(sb, "stop_margin_minutes", 1, 120) * 60
    if margin >= lifetime:
        raise ConfigError(f"{WHERE} stop_margin_minutes must be less than max_lifetime_minutes")
    approved = tuple(string_list(sb.get("egress_approved", []), f"{WHERE} egress_approved"))
    for name in approved:
        if not HOST.fullmatch(name):
            raise ConfigError(f"{WHERE} egress_approved: {show(name)} is not a host name")
    backend = table_at(host.values, "platform", "config").get("sandbox", "none")
    if not isinstance(backend, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", backend):
        raise ConfigError("config.toml: [platform] sandbox must be a backend name")
    hook_wait = table_at(host.values, "timeouts", "config").get("hook_wait_seconds")
    if isinstance(hook_wait, bool) or not isinstance(hook_wait, int | float) or not 0 < hook_wait <= 60:
        raise ConfigError("[timeouts] hook_wait_seconds must be a number of seconds, more than 0 and at "
                          "most 60")
    adapters = table_at(host.values, "adapters", "config")
    binaries: dict[str, str] = {}
    for adapter in string_list(adapters.get("known", []), "adapters.known"):
        binary = table_at(adapters, adapter, "adapters").get("binary")
        if not isinstance(binary, str) or not binary:
            raise ConfigError(f"[adapters.{adapter}] binary must be a command name or path")
        binaries[adapter] = binary
    profiles = {name: dict(cast(Mapping[str, Any], value))
                for name, value in table_at(host.values, "profiles", "config.toml").items()}
    return SandboxSettings(
        backend=backend,
        image=_text(sb, "image"),
        openshell=_text(sb, "openshell"),
        podman=_text(sb, "podman"),
        tool_env=_tool_env(sb.get("tool_env", {})),
        max_lifetime_seconds=lifetime,
        stop_margin_seconds=margin,
        probe_allowed_host=_host(sb, "probe_allowed_host"),
        probe_denied_host=_host(sb, "probe_denied_host"),
        agent_probe_seconds=_int(sb, "agent_probe_seconds", 30, 900),
        hook_wait_seconds=float(hook_wait),
        egress_approved=approved,
        binaries=binaries,
        profiles=profiles,
        workstreams={name: _workstream(name, cfg) for name, cfg in streams.items()})


def _workstream(name: str, cfg: Config) -> WsSandbox:
    """The workstream's additions. `layers.check_workstream` has checked them against the host's
    approved lists when the layer was loaded."""
    where = f"workstreams/{name}.toml: [sandbox]"
    sb = table_at(cfg.values, "sandbox", "config")
    egress = tuple(string_list(sb.get("extra_egress", []), f"{where} extra_egress"))
    mounts = tuple(Path(p).resolve(strict=False)
                   for p in string_list(sb.get("extra_ro_mounts", []), f"{where} extra_ro_mounts"))
    local = frozenset(c for c in LOCAL_CLASSES if cfg.policy.tiers.get(c) == "auto_approve")
    return WsSandbox(egress, mounts, local)


def _int(table: Mapping[str, Any], key: str, low: int, high: int) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{WHERE} {key} must be an integer from {low} to {high}")
    return value


def _text(table: Mapping[str, Any], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value or "\0" in value:
        raise ConfigError(f"{WHERE} {key} must be a non-empty string")
    return value


def _host(table: Mapping[str, Any], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not HOST.fullmatch(value) or value in DENIED_HOSTS:
        raise ConfigError(f"{WHERE} {key} must be a host name (and not a denied host)")
    return value


def _tool_env(value: Any) -> dict[str, str]:
    table = as_table(value)
    if table is None:
        raise ConfigError(f"{WHERE} tool_env must be a table of NAME = \"value\" strings")
    found: dict[str, str] = {}
    for name, text in table.items():
        if not ENV_NAME.fullmatch(name) or name in RESERVED_ENV:
            raise ConfigError(f"{WHERE} tool_env: {show(name, False)} is not an allowed variable name")
        if not isinstance(text, str) or "\0" in text:
            raise ConfigError(f"{WHERE} tool_env.{name} must be a string")
        found[name] = text
    return found
