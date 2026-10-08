"""Configuration layering (ADR 0001 §15): defaults -> host -> workstream -> env, with host-only policy."""

import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from heterodyne.config import accounts as account_rules
from heterodyne.config import capabilities as capability_table
from heterodyne.config import layers, paths
from heterodyne.config.accounts import Account
from heterodyne.config.capabilities import Capabilities
from heterodyne.config.errors import ConfigError
from heterodyne.config.policy import Policy, build_policy

__all__ = ["Account", "Capabilities", "Config", "ConfigError", "Policy", "load"]


@dataclass(frozen=True)
class Config:
    values: dict[str, Any]
    sources: dict[str, str]
    policy: Policy
    accounts: dict[tuple[str, str], Account] = field(default_factory=dict[tuple[str, str], Account])

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.values
        for part in dotted.split("."):
            table = cast(dict[str, Any], node) if isinstance(node, dict) else None
            if table is None or part not in table:
                return default
            node = table[part]
        return node


def load(workstream: str | None = None, env: Mapping[str, str] = os.environ,
         capabilities: Mapping[str, Capabilities] | None = None) -> Config:
    """`capabilities` defaults to the in-code table (§4.4 D9), read at call time."""
    config_dir = paths.config_dir(env)
    defaults = layers.read_defaults()
    layers.check_secrets(defaults, "defaults")
    # Each layer's raw data is secret-scanned before any other validation of that layer, so no later
    # error can be the first to see (and quote) secret material.
    raw_env = {var: value for var, value in env.items() if var.startswith("HETERODYNE_")}
    layers.check_secrets(raw_env, "environment")
    host = layers.read_toml(config_dir / "config.toml")
    layers.check_secrets(host, "config.toml")
    layers.check_host(host)
    policy_raw = layers.read_toml(config_dir / "policy.toml")
    layers.check_secrets(policy_raw, "policy.toml")
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    layers.merge(values, copy.deepcopy(defaults), "defaults", sources)
    layers.merge(values, host, "host:config.toml", sources)
    restrict: dict[str, Any] = {}
    if workstream:
        ws = layers.read_toml(config_dir / "workstreams" / f"{workstream}.toml")
        layers.check_secrets(ws, f"workstreams/{workstream}.toml")
        layers.check_workstream(workstream, ws, values)
        restrict = ws.pop("restrict", {})
        layers.merge(values, ws, f"workstream:{workstream}.toml", sources)
    env_values, env_labels = layers.env_layer(env)
    for table, entries in env_values.items():
        values.setdefault(table, {}).update(entries)
    sources.update(env_labels)
    layers.check_profiles(values)
    caps = capability_table.CAPABILITIES if capabilities is None else capabilities
    found = account_rules.check(values, env, caps)
    policy = build_policy(policy_raw, defaults.get("tiers", {}), restrict)
    return Config(values=values, sources=sources, policy=policy, accounts=found)
